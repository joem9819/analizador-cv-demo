"""Funciones auxiliares compartidas: hashes, fechas, JSON y formato de tiempos."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def hash_texto(texto: str) -> str:
    """Devuelve el SHA-256 (hexadecimal) de un texto codificado en UTF-8."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def hash_objeto(objeto: Any) -> str:
    """SHA-256 de la serialización JSON canónica (claves ordenadas) de un objeto."""
    return hash_texto(json_canonico(objeto))


def json_canonico(objeto: Any) -> str:
    """Serializa a JSON de forma estable para calcular hashes."""
    return json.dumps(objeto, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_legible(objeto: Any) -> str:
    """Serializa a JSON con sangría y caracteres en español sin escapar."""
    return json.dumps(objeto, ensure_ascii=False, indent=2)


def leer_json(ruta: Path) -> Any:
    with Path(ruta).open("r", encoding="utf-8") as archivo:
        return json.load(archivo)


def escribir_json(ruta: Path, objeto: Any) -> None:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json_legible(objeto) + "\n", encoding="utf-8")


def ahora_iso() -> str:
    """Fecha y hora local con zona horaria, en formato ISO 8601 (precisión de segundos)."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def marca_archivo() -> str:
    """Marca de tiempo apta para nombres de archivo (AAAAMMDD_HHMMSS)."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def formatear_duracion(segundos: float | None) -> str:
    """Convierte segundos a un texto legible: «2 h 05 min», «4 min 10 s» o «12,3 s»."""
    if segundos is None:
        return "sin estimación"
    segundos = max(0.0, float(segundos))
    if segundos < 60:
        return f"{segundos:.1f} s".replace(".", ",")
    minutos, seg = divmod(int(round(segundos)), 60)
    if minutos < 60:
        return f"{minutos} min {seg:02d} s"
    horas, minutos = divmod(minutos, 60)
    return f"{horas} h {minutos:02d} min"


def numero_es(valor: float | None, decimales: int = 2) -> str:
    """Formatea un número con coma decimal (convención en español)."""
    if valor is None:
        return "—"
    try:
        if valor != valor:  # NaN
            return "—"
    except TypeError:
        return str(valor)
    return f"{valor:.{decimales}f}".replace(".", ",")


def cantidad(n: int, singular: str, plural: str) -> str:
    """«1 sugerencia», «3 sugerencias»."""
    return f"{n} {singular if n == 1 else plural}"


def preparar_consola() -> None:
    """Evita fallos de codificación en consolas de Windows (cp1252) o salidas redirigidas."""
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
