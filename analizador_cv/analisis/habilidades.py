"""Diccionario de habilidades (datos/habilidades.json): nombre canónico, tipo, categoría y sinónimos.

Se usa para:
1. unificar los tags que extrae el modelo («JS» y «JavaScript» son el mismo tag);
2. la línea base por palabras clave (detectar habilidades sin modelo);
3. la guardia contra la información inventada: una habilidad que aparece en una mejora pero no en el
   original es información nueva.
Las habilidades que no están en el diccionario se comparan por su nombre normalizado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from analizador_cv.analisis.cotejo import contar, normalizar, patron_termino
from analizador_cv.configuracion import RAIZ_PROYECTO
from analizador_cv.utilidades import hash_objeto, leer_json

RUTA_DICCIONARIO = RAIZ_PROYECTO / "datos" / "habilidades.json"
TIPOS = ("tecnica", "blanda")

_PARENTESIS = re.compile(r"\s*\([^)]*\)")
_VERSION_FINAL = re.compile(r"\s+v?\d+(?:\.\d+)*$")


@dataclass(frozen=True)
class Habilidad:
    canonico: str
    tipo: str
    categoria: str
    #: formas normalizadas (canónico y sinónimos), de la más larga a la más corta
    terminos: tuple[str, ...]


def limpiar_nombre(nombre: str | None) -> str:
    """Quita paréntesis y números de versión finales: «Python 3.11 (scripts)» → «python»."""
    texto = _PARENTESIS.sub("", normalizar(nombre))
    return _VERSION_FINAL.sub("", texto).strip(" .,;:-")


class Diccionario:
    def __init__(self, datos: dict[str, Any]):
        self.version = str(datos["version"])
        self.huella = hash_objeto(datos)
        self.habilidades: list[Habilidad] = []
        self._por_termino: dict[str, Habilidad] = {}
        for entrada in datos["habilidades"]:
            if entrada["tipo"] not in TIPOS:
                raise ValueError(f"Tipo desconocido en el diccionario: {entrada['tipo']!r} ({entrada['canonico']})")
            terminos = list(dict.fromkeys(t for t in (normalizar(x) for x in [entrada["canonico"], *entrada["sinonimos"]]) if t))
            habilidad = Habilidad(entrada["canonico"], entrada["tipo"], entrada["categoria"],
                                  tuple(sorted(terminos, key=len, reverse=True)))
            for termino in terminos:
                if termino in self._por_termino:
                    raise ValueError(f"Término repetido en el diccionario de habilidades: «{termino}»")
                self._por_termino[termino] = habilidad
            self.habilidades.append(habilidad)
        self._patrones = {h.canonico: re.compile("|".join(patron_termino(t).pattern for t in h.terminos))
                          for h in self.habilidades}

    def buscar(self, nombre: str | None) -> Habilidad | None:
        """Habilidad del diccionario cuyo nombre o sinónimo coincide con «nombre» completo."""
        normalizado = normalizar(nombre)
        return self._por_termino.get(normalizado) or self._por_termino.get(limpiar_nombre(nombre))

    def clave(self, nombre: str) -> str:
        """Identidad del tag: el canónico normalizado si está en el diccionario; si no, el nombre limpio."""
        habilidad = self.buscar(nombre)
        return normalizar(habilidad.canonico) if habilidad else limpiar_nombre(nombre)

    def nombre_canonico(self, nombre: str) -> str:
        habilidad = self.buscar(nombre)
        return habilidad.canonico if habilidad else nombre.strip()

    def contar(self, nombre: str, texto_normalizado: str) -> int:
        """Apariciones de la habilidad (por cualquiera de sus formas) en un texto normalizado."""
        habilidad = self.buscar(nombre)
        if habilidad:
            return len(self._patrones[habilidad.canonico].findall(texto_normalizado))
        return contar(texto_normalizado, limpiar_nombre(nombre) or nombre)

    def en_texto(self, nombre: str, texto_normalizado: str) -> bool:
        return self.contar(nombre, texto_normalizado) > 0

    def detectar(self, texto_normalizado: str) -> list[tuple[Habilidad, int]]:
        """Habilidades del diccionario presentes en el texto, con su número de apariciones."""
        encontradas = []
        for habilidad in self.habilidades:
            cantidad = len(self._patrones[habilidad.canonico].findall(texto_normalizado))
            if cantidad:
                encontradas.append((habilidad, cantidad))
        return encontradas


@lru_cache(maxsize=4)
def _cargar(ruta: str) -> Diccionario:
    return Diccionario(leer_json(Path(ruta)))


def cargar_diccionario(ruta: Path | str | None = None) -> Diccionario:
    return _cargar(str(Path(ruta) if ruta else RUTA_DICCIONARIO))
