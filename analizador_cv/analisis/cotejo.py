"""Normalización y cotejo de textos: busca en el texto original lo que devuelve el modelo.

Las comparaciones se hacen sobre una forma normalizada: minúsculas, sin tildes (la ñ se conserva),
comillas y guiones unificados y espacios colapsados. ubicar_fragmento devuelve la posición en el
texto ORIGINAL, para poder reemplazar ese fragmento al aplicar una mejora. Si el fragmento no está
literal, busca la ventana de palabras más parecida con difflib.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

_EQUIVALENCIAS = {"“": '"', "”": '"', "«": '"', "»": '"', "‘": "'", "’": "'", "´": "'", "–": "-", "—": "-",
                  "‐": "-", "‑": "-", "−": "-", "•": "-", "●": "-", "▪": "-", "·": "-", "…": "..."}

#: Límites de un término: no puede estar pegado a letras o dígitos, ni a un punto por la izquierda («js» no
#: está en «node.js»), ni a «+» o «#» por la derecha (C++, C#).
_IZQUIERDA = r"(?<![a-z0-9ñ.])"
_DERECHA = r"(?![a-z0-9ñ+#])"

UMBRAL_FRAGMENTO = 0.85


def _normalizar_caracter(caracter: str) -> str:
    caracter = _EQUIVALENCIAS.get(caracter, caracter)
    if caracter in "ñÑ":
        return "ñ"
    descompuesto = unicodedata.normalize("NFD", caracter)
    return "".join(c for c in descompuesto if not unicodedata.combining(c)).lower()


def normalizar_con_mapa(texto: str) -> tuple[str, list[int]]:
    """Forma normalizada del texto y, para cada carácter normalizado, su posición en el original."""
    salida: list[str] = []
    mapa: list[int] = []
    for posicion, caracter in enumerate(texto):
        if caracter.isspace():
            if salida and salida[-1] != " ":
                salida.append(" ")
                mapa.append(posicion)
            continue
        for normalizado in _normalizar_caracter(caracter):
            salida.append(normalizado)
            mapa.append(posicion)
    while salida and salida[-1] == " ":
        salida.pop()
        mapa.pop()
    return "".join(salida), mapa


def normalizar(texto: str | None) -> str:
    """Minúsculas, sin tildes, comillas y guiones unificados y un solo espacio entre palabras."""
    return normalizar_con_mapa(texto or "")[0]


@lru_cache(maxsize=8192)
def patron_termino(termino_normalizado: str) -> re.Pattern[str]:
    """Patrón de un término ya normalizado, como palabra completa; entre palabras admite espacio o guion."""
    partes = [re.escape(p) for p in termino_normalizado.split(" ") if p]
    return re.compile(_IZQUIERDA + "[ -]".join(partes) + _DERECHA)


def contiene(texto_normalizado: str, termino: str) -> bool:
    termino = normalizar(termino)
    return bool(termino) and patron_termino(termino).search(texto_normalizado) is not None


def contar(texto_normalizado: str, termino: str) -> int:
    termino = normalizar(termino)
    return len(patron_termino(termino).findall(texto_normalizado)) if termino else 0


@dataclass(frozen=True)
class Ubicacion:
    """Posición de un fragmento en el texto original (inicio inclusivo, fin exclusivo)."""

    inicio: int
    fin: int
    similitud: float


def ubicar_fragmento(fragmento: str | None, texto: str, *, umbral: float = UMBRAL_FRAGMENTO,
                     normalizado: tuple[str, list[int]] | None = None) -> Ubicacion | None:
    """Busca el fragmento en el texto. Devuelve su ubicación o None si no hay una ventana suficientemente parecida.

    «normalizado» permite reutilizar normalizar_con_mapa(texto) cuando se ubican muchos fragmentos.
    """
    buscado = normalizar(fragmento).strip(" .;,:-")
    if not buscado:
        return None
    norma, mapa = normalizado or normalizar_con_mapa(texto)
    posicion = norma.find(buscado)
    if posicion >= 0:
        return Ubicacion(mapa[posicion], mapa[posicion + len(buscado) - 1] + 1, 1.0)
    palabras = [(m.start(), m.end()) for m in re.finditer(r"\S+", norma)]
    cantidad = len(buscado.split(" "))
    comparador = difflib.SequenceMatcher(autojunk=False)
    comparador.set_seq2(buscado)  # difflib guarda en caché la segunda secuencia
    mejor: tuple[float, int, int] | None = None
    for tamano in sorted({max(1, cantidad + d) for d in (-2, -1, 0, 1, 2)}):
        for i in range(0, len(palabras) - tamano + 1):
            inicio, fin = palabras[i][0], palabras[i + tamano - 1][1]
            comparador.set_seq1(norma[inicio:fin])
            if comparador.real_quick_ratio() < umbral or comparador.quick_ratio() < umbral:
                continue
            similitud = comparador.ratio()
            if similitud >= umbral and (mejor is None or similitud > mejor[0]):
                mejor = (similitud, inicio, fin)
    if mejor is None:
        return None
    return Ubicacion(mapa[mejor[1]], mapa[mejor[2] - 1] + 1, round(mejor[0], 3))


def similitud(a: str | None, b: str | None) -> float:
    """Parecido entre dos textos normalizados (0–1)."""
    return difflib.SequenceMatcher(None, normalizar(a), normalizar(b), autojunk=False).ratio()
