"""Fechas, duraciones y periodos de la hoja de vida (temporalidad; definición del proyecto, §6.4).

El modelo copia las fechas tal como aparecen («feb. 2023», «02/2023», «2021», «a la fecha»); este
módulo las interpreta, así que los meses los calcula siempre el código. Reglas:
- Los meses de un periodo se cuentan de forma inclusiva: de enero a diciembre de 2023 son 12.
- Una fecha con solo el año vale enero si es de inicio y diciembre si es de fin (precisión «año»);
  un año solo, sin fin («Proyecto (2023)»), se toma como ese año completo.
- «actual», «presente», «a la fecha»… valen la fecha de referencia del análisis.
- Si falta el fin, el periodo se reduce al mes de inicio (estimación conservadora) y se marca.
- Una fecha posterior a la fecha de referencia se recorta a ella y se marca como problema.
- Los periodos que se solapan se fusionan antes de sumar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from analizador_cv.analisis.cotejo import normalizar

MESES = {
    "enero": 1, "ene": 1, "jan": 1, "january": 1,
    "febrero": 2, "feb": 2, "february": 2,
    "marzo": 3, "mar": 3, "march": 3,
    "abril": 4, "abr": 4, "apr": 4, "april": 4,
    "mayo": 5, "may": 5,
    "junio": 6, "jun": 6, "june": 6,
    "julio": 7, "jul": 7, "july": 7,
    "agosto": 8, "ago": 8, "aug": 8, "august": 8,
    "septiembre": 9, "setiembre": 9, "sep": 9, "sept": 9, "set": 9, "september": 9,
    "octubre": 10, "oct": 10, "october": 10,
    "noviembre": 11, "nov": 11, "november": 11,
    "diciembre": 12, "dic": 12, "dec": 12, "december": 12,
}
PALABRAS_ACTUAL = ("actual", "actualidad", "actualmente", "presente", "la fecha", "hoy", "vigente", "en curso",
                   "hasta ahora", "present", "current", "now")
_ACTUAL = re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(p) for p in PALABRAS_ACTUAL) + r")(?![a-z])")
_ANIO = r"((?:19|20)\d{2})"
_ANIO_MES = re.compile(rf"(?<!\d){_ANIO}[-/.](\d{{1,2}})(?!\d)")
_MES_ANIO = re.compile(rf"(?<!\d)(\d{{1,2}})[-/.]{_ANIO}(?!\d)")
_NOMBRE_MES = re.compile(rf"(?<![a-z])([a-z]{{3,10}})\.?\s*(?:de\s+|del\s+|-\s*|/\s*)?{_ANIO}(?!\d)")
_SOLO_ANIO = re.compile(rf"(?<!\d){_ANIO}(?!\d)")
_SEPARADOR_RANGO = re.compile(r"\s+(?:-|a|al|hasta)\s+|\s*-\s*(?=[a-z]|(?:19|20)\d{2})")

#: La normalización conserva la ñ: «año» sigue siendo «año» (y se acepta «ano» por si falta la tilde).
_RE_ANIOS = re.compile(r"(\d+)\s*a[ñn]os?\b")
_RE_MESES = re.compile(r"(\d+)\s*mes(?:es)?\b")
_RE_ANIO_Y_MEDIO = re.compile(r"(\d+)\s*a[ñn]os?\s+y\s+medio\b")


@dataclass(frozen=True, order=True)
class Mes:
    anio: int
    mes: int

    @property
    def indice(self) -> int:
        return self.anio * 12 + self.mes - 1

    @classmethod
    def desde_indice(cls, indice: int) -> "Mes":
        return cls(indice // 12, indice % 12 + 1)

    @classmethod
    def hoy(cls) -> "Mes":
        actual = date.today()
        return cls(actual.year, actual.month)

    @classmethod
    def desde_texto(cls, texto: str) -> "Mes":
        """«AAAA-MM» (fecha de referencia en la configuración o en la línea de comandos)."""
        coincidencia = re.fullmatch(r"\s*(\d{4})-(\d{1,2})\s*", texto or "")
        if not coincidencia or not 1 <= int(coincidencia.group(2)) <= 12:
            raise ValueError(f"Fecha no válida: {texto!r} (use AAAA-MM, por ejemplo 2026-10).")
        return cls(int(coincidencia.group(1)), int(coincidencia.group(2)))

    def __str__(self) -> str:
        return f"{self.anio:04d}-{self.mes:02d}"


def meses_entre(desde: Mes, hasta: Mes) -> int:
    """Meses transcurridos de «desde» a «hasta» (0 si es el mismo mes)."""
    return hasta.indice - desde.indice


@dataclass(frozen=True)
class FechaLeida:
    mes: Mes
    precision: str  # «mes», «año» o «actual»


def menciona_actual(texto: str | None) -> bool:
    """True si el texto dice que algo sigue vigente («actual», «a la fecha», «en curso»…)."""
    return _ACTUAL.search(normalizar(texto)) is not None


def leer_fecha(texto: str | None, *, referencia: Mes, es_fin: bool = False) -> FechaLeida | None:
    """Interpreta una fecha escrita en la hoja de vida. Devuelve None si no la reconoce."""
    t = normalizar(texto)
    if not t:
        return None
    if _ACTUAL.search(t):
        return FechaLeida(referencia, "actual")
    for patron, orden in ((_ANIO_MES, (1, 2)), (_MES_ANIO, (2, 1))):
        coincidencia = patron.search(t)
        if coincidencia:
            anio, mes = int(coincidencia.group(orden[0])), int(coincidencia.group(orden[1]))
            if 1 <= mes <= 12:
                return FechaLeida(Mes(anio, mes), "mes")
    for coincidencia in _NOMBRE_MES.finditer(t):
        if coincidencia.group(1) in MESES:
            return FechaLeida(Mes(int(coincidencia.group(2)), MESES[coincidencia.group(1)]), "mes")
    coincidencia = _SOLO_ANIO.search(t)
    if coincidencia:
        return FechaLeida(Mes(int(coincidencia.group(1)), 12 if es_fin else 1), "año")
    return None


def duracion_en_meses(texto: str | None) -> int | None:
    """Convierte «1 año y 6 meses» en 18 y «2 años y medio» en 30. None si no reconoce el formato.

    Rescatada del prototipo anterior (verificacion.py) y ampliada con «y medio».
    """
    t = normalizar(texto)
    medio = _RE_ANIO_Y_MEDIO.search(t)
    if medio:
        return int(medio.group(1)) * 12 + 6
    anios = _RE_ANIOS.search(t)
    meses = _RE_MESES.search(t)
    if not anios and not meses:
        return None
    return (int(anios.group(1)) * 12 if anios else 0) + (int(meses.group(1)) if meses else 0)


@dataclass
class Periodo:
    """Periodo de una experiencia, proyecto o estudio.

    precision: «mes» (inicio y fin con mes), «año» (alguna fecha solo con el año), «duracion» (sin
    fechas, solo una duración escrita), «incompleta» (falta el fin) o «sin_fecha».
    """

    inicio: Mes | None
    fin: Mes | None
    meses: int | None
    precision: str
    problemas: list[str] = field(default_factory=list)

    @property
    def ubicado(self) -> bool:
        return self.inicio is not None and self.fin is not None

    def a_dict(self) -> dict:
        return {"inicio": str(self.inicio) if self.inicio else None, "fin": str(self.fin) if self.fin else None,
                "meses": self.meses, "precision": self.precision, "problemas": list(self.problemas)}

    def texto(self) -> str:
        if self.ubicado:
            return f"{self.inicio} a {self.fin}"
        if self.meses:
            return f"{self.meses} meses (sin fechas)"
        return "sin fechas"


def _partir_rango(texto: str) -> tuple[str, str] | None:
    """«feb. 2023 - actual» → («feb. 2023», «actual»), cuando el modelo copió el rango en un solo campo."""
    t = normalizar(texto)
    partes = _SEPARADOR_RANGO.split(t, maxsplit=1)
    return (partes[0], partes[1]) if len(partes) == 2 and partes[0] and partes[1] else None


def leer_periodo(inicio: str | None, fin: str | None, duracion: str | None, *, referencia: Mes) -> Periodo:
    """Arma el periodo con las fechas escritas. Si no hay fechas, usa la duración escrita."""
    if inicio and not fin:
        rango = _partir_rango(inicio)
        if rango:
            inicio, fin = rango
    fecha_inicio = leer_fecha(inicio, referencia=referencia)
    fecha_fin = leer_fecha(fin, referencia=referencia, es_fin=True)
    if fecha_inicio is not None and fecha_inicio.precision == "actual":
        fecha_inicio = None  # «actual» no es una fecha de inicio
    problemas: list[str] = []
    if fecha_inicio is None and fecha_fin is None:
        meses = duracion_en_meses(duracion)
        if meses:
            return Periodo(None, None, meses, "duracion")
        return Periodo(None, None, None, "sin_fecha", ["no tiene fechas ni duración"])
    if fecha_inicio is None:
        return Periodo(None, None, duracion_en_meses(duracion), "incompleta", ["falta la fecha de inicio"])
    if fecha_fin is None and fecha_inicio.precision == "año":
        # «Proyecto (2023)»: un año solo, sin fin, se toma como ese año completo.
        fecha_fin = FechaLeida(Mes(fecha_inicio.mes.anio, 12), "año")
    desde = fecha_inicio.mes
    if desde > referencia:
        problemas.append(f"la fecha de inicio ({desde}) es posterior a la fecha de referencia ({referencia})")
        desde = referencia
    if fecha_fin is None:
        return Periodo(desde, desde, 1, "incompleta", problemas + ["falta la fecha de fin"])
    hasta = fecha_fin.mes
    if hasta > referencia:
        if fecha_fin.precision == "año" and hasta.anio == referencia.anio:
            hasta = referencia  # «2026» como fin en octubre de 2026: hasta octubre, sin problema
        else:
            problemas.append(f"la fecha de fin ({hasta}) es posterior a la fecha de referencia ({referencia})")
            hasta = referencia
    if hasta < desde:
        return Periodo(None, None, None, "sin_fecha", problemas + [f"el fin ({hasta}) es anterior al inicio ({desde})"])
    precision = "año" if "año" in (fecha_inicio.precision, fecha_fin.precision) else "mes"
    return Periodo(desde, hasta, meses_entre(desde, hasta) + 1, precision, problemas)


def fusionar(intervalos: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Une intervalos de meses (índices inclusivos) que se solapan o se tocan."""
    resultado: list[tuple[int, int]] = []
    for inicio, fin in sorted(intervalos):
        if resultado and inicio <= resultado[-1][1] + 1:
            resultado[-1] = (resultado[-1][0], max(resultado[-1][1], fin))
        else:
            resultado.append((inicio, fin))
    return resultado


def meses_acumulados(periodos: Iterable[Periodo]) -> int:
    """Meses de todos los periodos, sin contar dos veces los que se solapan.

    Los periodos con duración pero sin fechas se suman aparte (no se puede saber si se solapan).
    """
    periodos = list(periodos)
    ubicados = fusionar((p.inicio.indice, p.fin.indice) for p in periodos if p.ubicado)
    return sum(fin - inicio + 1 for inicio, fin in ubicados) + sum(p.meses or 0 for p in periodos if not p.ubicado)
