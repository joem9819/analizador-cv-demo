"""Plantillas versionadas de los mensajes al modelo (datos/plantillas/<etapa>.txt; requisito RNF04).

Formato de cada archivo:

    # version: 1
    # etapa: extraccion_cv
    # descripcion: …
    === SISTEMA ===
    instrucciones con {{MARCADORES}}
    === USUARIO ===
    contenido con {{MARCADORES}}

Los marcadores se reemplazan una sola vez (el texto de la hoja de vida puede contener llaves sin
problema). Si a un marcador le falta valor o sobra un valor, es un error de programación. La huella
(SHA-256 del archivo) y la versión quedan en cada análisis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from analizador_cv.utilidades import hash_texto

ETAPAS = ("extraccion_cv", "requisitos_oferta", "evaluacion_general", "evaluacion_especifica", "mejoras")
_MARCADOR = re.compile(r"\{\{([A-Z_]+)\}\}")
_SISTEMA = "=== SISTEMA ==="
_USUARIO = "=== USUARIO ==="


class ErrorPlantilla(Exception):
    """Plantilla que falta, mal formada o usada con valores que no corresponden."""


@dataclass(frozen=True)
class Plantilla:
    etapa: str
    version: str
    sistema: str
    usuario: str
    huella: str

    @property
    def marcadores(self) -> set[str]:
        return set(_MARCADOR.findall(self.sistema)) | set(_MARCADOR.findall(self.usuario))

    def mensajes(self, **valores: str) -> list[dict[str, str]]:
        """Mensajes de sistema y de usuario con los marcadores reemplazados."""
        faltan = self.marcadores - set(valores)
        sobran = set(valores) - self.marcadores
        if faltan or sobran:
            raise ErrorPlantilla(f"Plantilla «{self.etapa}»: faltan valores para {sorted(faltan)} o sobran {sorted(sobran)}.")

        def sustituir(texto: str) -> str:
            return _MARCADOR.sub(lambda m: str(valores[m.group(1)]), texto)

        return [{"role": "system", "content": sustituir(self.sistema)}, {"role": "user", "content": sustituir(self.usuario)}]


def leer_plantilla(ruta: Path) -> Plantilla:
    if not ruta.exists():
        raise ErrorPlantilla(f"No se encontró la plantilla {ruta}.")
    contenido = ruta.read_text(encoding="utf-8")
    metadatos: dict[str, str] = {}
    for renglon in contenido.splitlines():
        coincidencia = re.match(r"#\s*(\w+)\s*:\s*(.+)", renglon)
        if not coincidencia:
            break
        metadatos[coincidencia.group(1)] = coincidencia.group(2).strip()
    if _SISTEMA not in contenido or _USUARIO not in contenido or contenido.index(_SISTEMA) > contenido.index(_USUARIO):
        raise ErrorPlantilla(f"La plantilla {ruta.name} debe tener «{_SISTEMA}» y después «{_USUARIO}».")
    if "version" not in metadatos:
        raise ErrorPlantilla(f"La plantilla {ruta.name} no declara «# version: …».")
    sistema = contenido.split(_SISTEMA, 1)[1].split(_USUARIO, 1)[0].strip()
    usuario = contenido.split(_USUARIO, 1)[1].strip()
    return Plantilla(metadatos.get("etapa", ruta.stem), metadatos["version"], sistema, usuario, hash_texto(contenido))


def cargar_plantillas(carpeta: Path | str) -> dict[str, Plantilla]:
    """Las plantillas de todas las etapas, leídas de nuevo en cada análisis. Falla si falta alguna."""
    return {etapa: leer_plantilla(Path(carpeta) / f"{etapa}.txt") for etapa in ETAPAS}
