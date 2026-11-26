"""Validador mínimo de esquemas JSON (el subconjunto que usan los esquemas de la app).

Admite: type (texto o lista, p. ej. ["string", "null"]), properties, required,
additionalProperties (booleano), items, enum, minimum, maximum, minItems, maxItems, minLength y
maxLength. Se valida siempre con el esquema completo, aunque a la API se le haya enviado una versión
recortada (por ejemplo, sin «minimum» si la API no lo aceptaba).
"""

from __future__ import annotations

from typing import Any

_TIPOS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _ruta(base: str, clave: str | int) -> str:
    if isinstance(clave, int):
        return f"{base}[{clave}]"
    return f"{base}.{clave}" if base else clave


def validar(datos: Any, esquema: dict[str, Any], ruta: str = "") -> list[str]:
    """Devuelve la lista de errores (vacía si los datos cumplen el esquema)."""
    donde = ruta or "(raíz)"
    tipos = esquema.get("type")
    if tipos is not None:
        tipos = [tipos] if isinstance(tipos, str) else list(tipos)
        if not any(_TIPOS[t](datos) for t in tipos):
            return [f"{donde}: se esperaba {' o '.join(tipos)} y llegó {type(datos).__name__}"]
    errores: list[str] = []
    if "enum" in esquema and datos not in esquema["enum"]:
        errores.append(f"{donde}: valor {datos!r} fuera de {esquema['enum']}")
    if isinstance(datos, (int, float)) and not isinstance(datos, bool):
        if "minimum" in esquema and datos < esquema["minimum"]:
            errores.append(f"{donde}: {datos} es menor que el mínimo {esquema['minimum']}")
        if "maximum" in esquema and datos > esquema["maximum"]:
            errores.append(f"{donde}: {datos} es mayor que el máximo {esquema['maximum']}")
    if isinstance(datos, str):
        if "minLength" in esquema and len(datos) < esquema["minLength"]:
            errores.append(f"{donde}: texto más corto que {esquema['minLength']} caracteres")
        if "maxLength" in esquema and len(datos) > esquema["maxLength"]:
            errores.append(f"{donde}: texto más largo que {esquema['maxLength']} caracteres")
    if isinstance(datos, list):
        if "minItems" in esquema and len(datos) < esquema["minItems"]:
            errores.append(f"{donde}: menos de {esquema['minItems']} elementos")
        if "maxItems" in esquema and len(datos) > esquema["maxItems"]:
            errores.append(f"{donde}: más de {esquema['maxItems']} elementos")
        if isinstance(esquema.get("items"), dict):
            for i, elemento in enumerate(datos):
                errores += validar(elemento, esquema["items"], _ruta(ruta, i))
    if isinstance(datos, dict):
        propiedades = esquema.get("properties") or {}
        for clave in esquema.get("required") or []:
            if clave not in datos:
                errores.append(f"{_ruta(ruta, clave)}: falta el campo obligatorio")
        if esquema.get("additionalProperties") is False:
            for clave in datos:
                if clave not in propiedades:
                    errores.append(f"{_ruta(ruta, clave)}: campo no permitido por el esquema")
        for clave, subesquema in propiedades.items():
            if clave in datos:
                errores += validar(datos[clave], subesquema, _ruta(ruta, clave))
    return errores
