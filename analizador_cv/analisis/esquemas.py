"""Esquema JSON y validador de cada etapa que usa el modelo (requisito RNF03).

Los esquemas usan solo construcciones que la prueba de conexión verificó con cada API: objetos,
listas, enum, null, mínimo y máximo. Todos los campos son obligatorios y sin campos adicionales,
como exige el modo estricto; un dato que falta se representa con null. No se usan minLength ni
maxLength.

maxItems documenta el máximo de cada lista, pero NO se envía a la API: Gemini rechaza con un error
genérico («invalid argument») los esquemas con maxItems en listas anidadas (sondas del 20/10/2026).
El esquema completo va en las instrucciones del prompt; a la API va sin_limites(esquema) y el código
recorta las listas con recortar() en lugar de pedir otro intento.

Los validadores revisan solo lo indispensable para calcular que el esquema no puede expresar: que la
oferta tenga requisitos y que la evaluación cubra cada requisito una sola vez. Si fallan, pedir_json
vuelve a intentar (cada intento cuesta una llamada). Los demás defectos (evidencia vacía, fragmento
que no está en el original) no se reintentan: el código los degrada o los descarta y lo registra.
"""

from __future__ import annotations

from typing import Any

CADENA: dict[str, Any] = {"type": "string"}
CADENA_O_NULO: dict[str, Any] = {"type": ["string", "null"]}

TIPOS_ACTIVIDAD = ("logro", "funcion")
TIPOS_FORMACION = ("formal", "curso", "certificacion")
ESTADOS_FORMACION = ("completo", "en_curso", "sin_dato")
TIPOS_HABILIDAD = ("tecnica", "blanda")
TIPOS_REQUISITO = ("obligatorio", "deseable")
CATEGORIAS_REQUISITO = ("habilidad_tecnica", "habilidad_blanda", "experiencia", "formacion", "idioma", "otro")
ESTADOS_COBERTURA = ("cubierto", "mencionado", "ausente")
TIPOS_SUGERENCIA_MODELO = ("redaccion", "estructura")


def objeto(propiedades: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": propiedades, "required": list(propiedades), "additionalProperties": False}


def lista(elementos: dict[str, Any], maximo: int | None = None) -> dict[str, Any]:
    esquema: dict[str, Any] = {"type": "array", "items": elementos}
    if maximo is not None:
        esquema["maxItems"] = maximo
    return esquema


def enum(valores: tuple[str, ...] | list[str]) -> dict[str, Any]:
    return {"type": "string", "enum": list(valores)}


def nivel() -> dict[str, Any]:
    return {"type": "integer", "minimum": 0, "maximum": 3}


_ACTIVIDADES = lista(objeto({"texto": CADENA, "tipo": enum(TIPOS_ACTIVIDAD)}), 12)

ESQUEMA_EXTRACCION: dict[str, Any] = objeto({
    "perfil": CADENA_O_NULO,
    "experiencias": lista(objeto({
        "cargo": CADENA,
        "empresa": CADENA_O_NULO,
        "inicio": CADENA_O_NULO,
        "fin": CADENA_O_NULO,
        "duracion": CADENA_O_NULO,
        "actividades": _ACTIVIDADES,
        "habilidades": lista(CADENA, 25),
    }), 15),
    "proyectos": lista(objeto({
        "nombre": CADENA,
        "inicio": CADENA_O_NULO,
        "fin": CADENA_O_NULO,
        "actividades": _ACTIVIDADES,
        "habilidades": lista(CADENA, 25),
    }), 10),
    "formacion": lista(objeto({
        "titulo": CADENA,
        "institucion": CADENA_O_NULO,
        "inicio": CADENA_O_NULO,
        "fin": CADENA_O_NULO,
        "estado": enum(ESTADOS_FORMACION),
        "tipo": enum(TIPOS_FORMACION),
        "habilidades": lista(CADENA, 25),
    }), 15),
    "habilidades": lista(objeto({"nombre": CADENA, "tipo": enum(TIPOS_HABILIDAD)}), 60),
    "idiomas": lista(objeto({"idioma": CADENA, "nivel": CADENA_O_NULO}), 8),
})

ESQUEMA_REQUISITOS: dict[str, Any] = objeto({
    "cargo": CADENA_O_NULO,
    "empresa": CADENA_O_NULO,
    "requisitos": lista(objeto({
        "texto": CADENA,
        "tipo": enum(TIPOS_REQUISITO),
        "categoria": enum(CATEGORIAS_REQUISITO),
        "terminos": lista(CADENA, 10),
    }), 30),
})

_REDACCION = {
    "verbos_accion": objeto({"nivel": nivel(), "evidencia": lista(CADENA, 3)}),
    "claridad": objeto({"nivel": nivel(), "evidencia": lista(CADENA, 3)}),
    "errores_ortografia": lista(objeto({"fragmento": CADENA, "correccion": CADENA}), 15),
}

ESQUEMA_EVALUACION_GENERAL: dict[str, Any] = objeto(dict(_REDACCION))


def esquema_evaluacion_especifica(ids_requisitos: list[str]) -> dict[str, Any]:
    """Redacción más la cobertura de cada requisito de la oferta (sus identificadores van en un enum)."""
    return objeto({
        **_REDACCION,
        "requisitos": lista(objeto({
            "id": enum(ids_requisitos),
            "estado": enum(ESTADOS_COBERTURA),
            "evidencia": CADENA_O_NULO,
        }), len(ids_requisitos)),
    })


ESQUEMA_MEJORAS: dict[str, Any] = objeto({
    "sugerencias": lista(objeto({
        "tipo": enum(TIPOS_SUGERENCIA_MODELO),
        "fragmento_original": CADENA_O_NULO,
        "propuesta": CADENA,
        "justificacion": CADENA,
    }), 12),
})


def sin_limites(esquema: Any) -> Any:
    """El esquema sin maxItems: la versión que se envía a la API y con la que se valida la respuesta."""
    if isinstance(esquema, dict):
        return {k: sin_limites(v) for k, v in esquema.items() if k != "maxItems"}
    if isinstance(esquema, list):
        return [sin_limites(v) for v in esquema]
    return esquema


def recortar(datos: Any, esquema: dict[str, Any]) -> Any:
    """Recorta cada lista de la respuesta al maxItems del esquema completo (conserva los primeros)."""
    if isinstance(datos, list) and esquema.get("type") == "array":
        maximo = esquema.get("maxItems")
        elementos = datos[:maximo] if maximo is not None else datos
        return [recortar(e, esquema.get("items") or {}) for e in elementos]
    if isinstance(datos, dict) and esquema.get("type") == "object":
        propiedades = esquema.get("properties") or {}
        return {k: recortar(v, propiedades.get(k) or {}) for k, v in datos.items()}
    return datos


# =============================================================================
# Validadores propios de cada etapa
# =============================================================================
def validar_requisitos(datos: dict[str, Any]) -> list[str]:
    if not any(r["texto"].strip() for r in datos["requisitos"]):
        return ["requisitos: la oferta debe tener al menos un requisito"]
    return []


def validador_evaluacion(ids_requisitos: list[str] | None):
    """Validador de la evaluación: en modo específico, cada requisito exactamente una vez."""

    def validar(datos: dict[str, Any]) -> list[str]:
        if not ids_requisitos:
            return []
        vistos = [r["id"] for r in datos["requisitos"]]
        errores = [f"requisitos: falta el requisito {i}" for i in ids_requisitos if i not in vistos]
        errores += [f"requisitos: el requisito {i} aparece {vistos.count(i)} veces"
                    for i in sorted(set(vistos)) if vistos.count(i) > 1]
        return errores

    return validar
