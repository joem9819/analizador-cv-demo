"""Métricas de la evaluación técnica (definición del proyecto, §9).

Coincidencias con la verdad de referencia (corpus.py):
- Habilidades. Una habilidad extraída coincide con una de la referencia si, sin mayúsculas ni tildes:
  1. es igual a su nombre o a una de sus formas (sin el paréntesis final: «Python (básico)» = «Python»);
  2. o el diccionario de la app las une (la misma entrada o sinónimos: «Office 365» = «Microsoft 365»);
  3. o una contiene a la otra como palabras seguidas con hasta 2 palabras de diferencia («Python básico» =
     «Python»), salvo que el diccionario diga que son habilidades distintas («SQL» y «SQL Server»).
  Cada habilidad de la referencia se empareja con una sola extraída (emparejamiento máximo). Las
  extraídas que repiten una ya emparejada («Windows 10» y «Windows 11») no cuentan como error; las que
  solo coinciden con una habilidad «opcional» no cuentan ni a favor ni en contra; el resto son falsos
  positivos. Las «nucleo» sin pareja son falsos negativos.
- Experiencias: cargo y empresa parecidos (iguales sin paréntesis, uno dentro del otro con al menos el
  60 % del largo, o difflib ≥ 0,85). Con fechas: además, inicio y fin a ±1 mes.
- Formación: título e institución parecidos (sin institución en la referencia, basta el título).

Pruebas estadísticas sin dependencias externas y reproducibles (semilla fija):
- Spearman con rangos promedio para los empates y valor p unilateral por permutaciones;
- Wilcoxon de rangos con signo, unilateral, con la distribución EXACTA condicionada a los empates
  (se descartan las diferencias cero, método de Wilcoxon);
- intervalo de confianza del 95 % por bootstrap de percentiles.
"""

from __future__ import annotations

import difflib
import html
import math
import random
import re
from dataclasses import dataclass, field
from statistics import mean, median, stdev
from typing import Any, Iterable, Sequence

from analizador_cv.analisis.cotejo import normalizar
from analizador_cv.analisis.habilidades import Diccionario, limpiar_nombre

SEMILLA = 20261020
REMUESTREOS = 10_000
PERMUTACIONES = 10_000
_PARENTESIS = re.compile(r"\s*\([^)]*\)")


# =============================================================================
# Precisión, recall y F1
# =============================================================================
@dataclass
class Conteo:
    vp: int = 0  # verdaderos positivos
    fp: int = 0
    fn: int = 0

    def __add__(self, otro: "Conteo") -> "Conteo":
        return Conteo(self.vp + otro.vp, self.fp + otro.fp, self.fn + otro.fn)

    @property
    def precision(self) -> float | None:
        return self.vp / (self.vp + self.fp) if self.vp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.vp / (self.vp + self.fn) if self.vp + self.fn else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None:
            return None
        return 0.0 if p + r == 0 else 2 * p * r / (p + r)

    def a_dict(self) -> dict[str, Any]:
        return {"vp": self.vp, "fp": self.fp, "fn": self.fn, "precision": _r(self.precision),
                "recall": _r(self.recall), "f1": _r(self.f1)}


def _r(valor: float | None, decimales: int = 4) -> float | None:
    return None if valor is None else round(valor, decimales)


def emparejar(aristas: dict[int, list[int]]) -> dict[int, int]:
    """Emparejamiento máximo (caminos de aumento): {predicción: referencia}. Las listas van en orden de preferencia."""
    pareja_de_ref: dict[int, int] = {}

    def aumentar(p: int, vistos: set[int]) -> bool:
        for r in aristas.get(p, []):
            if r in vistos:
                continue
            vistos.add(r)
            if r not in pareja_de_ref or aumentar(pareja_de_ref[r], vistos):
                pareja_de_ref[r] = p
                return True
        return False

    for p in sorted(aristas):
        aumentar(p, set())
    return {p: r for r, p in pareja_de_ref.items()}


# =============================================================================
# Habilidades
# =============================================================================
def _palabras(texto: str) -> list[str]:
    return [p for p in re.split(r"[\s,;:]+", texto) if p]


def _contiene(corta: list[str], larga: list[str]) -> bool:
    n = len(corta)
    return any(larga[i:i + n] == corta for i in range(len(larga) - n + 1))


@dataclass
class _Nombre:
    formas: set[str]
    canonicos: set[str]


def _nombre_prediccion(nombre: str, diccionario: Diccionario) -> _Nombre:
    formas = {f for f in (normalizar(nombre).strip(" .,;:"), limpiar_nombre(nombre)) if f}
    encontrada = diccionario.buscar(nombre)
    return _Nombre(formas, {normalizar(encontrada.canonico)} if encontrada else set())


def _nombre_referencia(entrada: dict[str, Any], diccionario: Diccionario) -> _Nombre:
    formas = {normalizar(entrada["nombre"]), limpiar_nombre(entrada["nombre"])} | set(entrada["formas"])
    canonicos = {normalizar(h.canonico) for h in (diccionario.buscar(f) for f in formas) if h}
    return _Nombre({f for f in formas if f}, canonicos)


def fuerza_coincidencia(prediccion: _Nombre, referencia: _Nombre) -> int:
    """3 = igual, 2 = el diccionario las une, 1 = una contiene a la otra, 0 = no coinciden."""
    if prediccion.formas & referencia.formas:
        return 3
    if prediccion.canonicos & referencia.canonicos:
        return 2
    if prediccion.canonicos and referencia.canonicos:
        return 0  # el diccionario conoce las dos y son habilidades distintas
    for a in prediccion.formas:
        for b in referencia.formas:
            pa, pb = _palabras(a), _palabras(b)
            corta, larga = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
            if (corta and len(larga) - len(corta) <= 2 and any(len(p) >= 3 for p in corta)
                    and _contiene(corta, larga)):
                return 1
    return 0


@dataclass
class ResultadoHabilidades:
    conteo: Conteo
    pares: list[tuple[dict[str, Any], dict[str, Any]]]  # (predicción, referencia) emparejadas
    falsos_positivos: list[str]
    falsos_negativos: list[str]
    repetidas: list[str] = field(default_factory=list)
    opcionales: list[str] = field(default_factory=list)

    def recall_por_clase(self, referencia: list[dict[str, Any]]) -> dict[str, Conteo]:
        """Verdaderos positivos y falsos negativos por clase de habilidad (tecnología o práctica)."""
        emparejadas = {r["nombre"] for _, r in self.pares}
        conteos: dict[str, Conteo] = {}
        for entrada in referencia:
            if entrada["nivel"] != "nucleo":
                continue
            conteo = conteos.setdefault(entrada["clase"], Conteo())
            if entrada["nombre"] in emparejadas:
                conteo.vp += 1
            else:
                conteo.fn += 1
        return conteos


def evaluar_habilidades(predichas: list[dict[str, Any]], referencia: list[dict[str, Any]],
                        diccionario: Diccionario) -> ResultadoHabilidades:
    """«predichas»: los tags del resultado (nombre y meses). «referencia»: las habilidades de la verdad."""
    nombres_p = [_nombre_prediccion(p["nombre"], diccionario) for p in predichas]
    nombres_r = [_nombre_referencia(r, diccionario) for r in referencia]
    nucleo = [i for i, r in enumerate(referencia) if r["nivel"] == "nucleo"]
    aristas: dict[int, list[int]] = {}
    for i, nombre in enumerate(nombres_p):
        fuerzas = [(fuerza_coincidencia(nombre, nombres_r[j]), j) for j in nucleo]
        aristas[i] = [j for f, j in sorted(fuerzas, key=lambda x: -x[0]) if f > 0]
    pares = emparejar(aristas)
    emparejadas_r = set(pares.values())
    fp, repetidas, opcionales = [], [], []
    for i, prediccion in enumerate(predichas):
        if i in pares:
            continue
        if any(j in emparejadas_r for j in aristas[i]):
            repetidas.append(prediccion["nombre"])
        elif any(fuerza_coincidencia(nombres_p[i], nombres_r[j]) for j, r in enumerate(referencia)
                 if r["nivel"] == "opcional"):
            opcionales.append(prediccion["nombre"])
        else:
            fp.append(prediccion["nombre"])
    fn = [referencia[j]["nombre"] for j in nucleo if j not in emparejadas_r]
    return ResultadoHabilidades(Conteo(len(pares), len(fp), len(fn)),
                                [(predichas[i], referencia[j]) for i, j in sorted(pares.items())], fp, fn,
                                repetidas, opcionales)


# =============================================================================
# Experiencias y formación
# =============================================================================
def _limpio(texto: str | None) -> str:
    return normalizar(_PARENTESIS.sub("", texto or "")).strip(" .,;:-")


def parecidos(a: str | None, b: str | None) -> bool:
    x, y = _limpio(a), _limpio(b)
    if not x or not y:
        return False
    if x == y:
        return True
    corto, largo = (x, y) if len(x) <= len(y) else (y, x)
    if corto in largo and len(corto) / len(largo) >= 0.6:
        return True
    return difflib.SequenceMatcher(None, x, y).ratio() >= 0.85


def _mes_indice(texto: str | None) -> int | None:
    if not texto:
        return None
    anio, mes = texto.split("-")
    return int(anio) * 12 + int(mes) - 1


def fechas_cercanas(prediccion: dict[str, Any], referencia: dict[str, Any], tolerancia: int = 1) -> bool:
    """Inicio y fin a ±tolerancia meses («periodo» del resultado frente a la verdad de referencia)."""
    for clave in ("inicio", "fin"):
        p, r = _mes_indice(prediccion.get(clave)), _mes_indice(referencia.get(clave))
        if p is None or r is None or abs(p - r) > tolerancia:
            return False
    return True


@dataclass
class ResultadoBloques:
    conteo: Conteo
    conteo_con_fechas: Conteo | None
    pares: list[tuple[dict[str, Any], dict[str, Any]]]


def evaluar_experiencias(predichas: list[dict[str, Any]], referencia: list[dict[str, Any]]) -> ResultadoBloques:
    """«predichas»: resultado["extraccion"]["experiencias"] (titulo, entidad, periodo)."""
    aristas = {i: [j for j, r in enumerate(referencia)
                   if parecidos(p["titulo"], r["cargo"]) and (r["empresa"] is None or parecidos(p["entidad"], r["empresa"]))]
               for i, p in enumerate(predichas)}
    pares = emparejar(aristas)
    conteo = Conteo(len(pares), len(predichas) - len(pares), len(referencia) - len(pares))
    con_fechas = sum(fechas_cercanas(predichas[i]["periodo"], referencia[j]) for i, j in pares.items())
    return ResultadoBloques(conteo, Conteo(con_fechas, len(predichas) - con_fechas, len(referencia) - con_fechas),
                            [(predichas[i], referencia[j]) for i, j in sorted(pares.items())])


def evaluar_formacion(predichas: list[dict[str, Any]], referencia: list[dict[str, Any]]) -> ResultadoBloques:
    aristas = {i: [j for j, r in enumerate(referencia)
                   if parecidos(p["titulo"], r["titulo"])
                   and (r["institucion"] is None or parecidos(p["entidad"], r["institucion"]))]
               for i, p in enumerate(predichas)}
    pares = emparejar(aristas)
    conteo = Conteo(len(pares), len(predichas) - len(pares), len(referencia) - len(pares))
    return ResultadoBloques(conteo, None, [(predichas[i], referencia[j]) for i, j in sorted(pares.items())])


# =============================================================================
# Requisitos de la oferta
# =============================================================================
def evaluar_requisitos_oferta(predichos: list[dict[str, Any]], referencia: list[dict[str, Any]],
                              texto_oferta: str) -> dict[str, Any]:
    """Cada requisito de la referencia tiene un ancla (frase de la oferta). Está identificado si algún
    requisito extraído contiene esa ancla o queda dentro de ella. Un extraído que cubre 2 o más anclas
    está «fusionado»; uno que no cubre ninguna es un requisito de más.

    Aparte se cuentan los requisitos que el modelo escribió con entidades HTML («Dise&ntilde;o», pasó con
    Gemini en las ofertas de QA) y las anclas identificadas si se decodifican; la medida principal es la estricta."""
    anclas = [normalizar(r["ancla"]) for r in referencia]

    def cubiertas(texto: str) -> list[int]:
        texto = normalizar(texto)
        return [j for j, ancla in enumerate(anclas) if ancla in texto or (len(texto) >= 12 and texto in ancla)]

    cubre = [cubiertas(requisito["texto"]) for requisito in predichos]
    decodificados = {j for requisito in predichos for j in cubiertas(html.unescape(requisito["texto"]))}
    identificados = {j for lista in cubre for j in lista}
    unicos = {j: [i for i, lista in enumerate(cubre) if j in lista] for j in identificados}
    tipo_correcto = [predichos[unicos[j][0]]["tipo"] == referencia[j]["tipo"]
                     for j in identificados if len(unicos[j]) == 1 and len(cubre[unicos[j][0]]) == 1]
    return {
        "referencia": len(referencia), "extraidos": len(predichos), "identificados": len(identificados),
        "recall": _r(len(identificados) / len(referencia)) if referencia else None,
        "precision": _r(sum(bool(c) for c in cubre) / len(predichos)) if predichos else None,
        "fusionados": sum(len(c) >= 2 for c in cubre), "de_mas": sum(not c for c in cubre),
        "tipo_correcto": _r(float(mean(tipo_correcto))) if tipo_correcto else None,
        "no_identificados": [referencia[j]["id"] for j in range(len(referencia)) if j not in identificados],
        "con_entidades_html": sum(html.unescape(r["texto"]) != r["texto"] for r in predichos),
        "identificados_decodificando": len(decodificados),
    }


# =============================================================================
# Estadística
# =============================================================================
def rangos(valores: Sequence[float]) -> list[float]:
    """Rangos (desde 1) con el promedio para los empates."""
    orden = sorted(range(len(valores)), key=lambda i: valores[i])
    resultado = [0.0] * len(valores)
    i = 0
    while i < len(orden):
        j = i
        while j + 1 < len(orden) and valores[orden[j + 1]] == valores[orden[i]]:
            j += 1
        promedio = (i + j) / 2 + 1
        for k in range(i, j + 1):
            resultado[orden[k]] = promedio
        i = j + 1
    return resultado


def _pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    mx, my = mean(x), mean(y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return None if sx == 0 or sy == 0 else sxy / (sx * sy)


def spearman(x: Sequence[float], y: Sequence[float], *, permutaciones: int = PERMUTACIONES,
             semilla: int = SEMILLA) -> dict[str, Any]:
    """ρ de Spearman y valor p unilateral (ρ > 0) por permutaciones de y."""
    if len(x) != len(y) or len(x) < 3:
        return {"n": len(x), "rho": None, "p_unilateral": None}
    rx, ry = rangos(x), rangos(y)
    rho = _pearson(rx, ry)
    if rho is None:
        return {"n": len(x), "rho": None, "p_unilateral": None}
    generador = random.Random(semilla)
    permutado = list(ry)
    al_menos = 0
    for _ in range(permutaciones):
        generador.shuffle(permutado)
        valor = _pearson(rx, permutado)
        if valor is not None and valor >= rho - 1e-12:
            al_menos += 1
    return {"n": len(x), "rho": round(rho, 4), "p_unilateral": round((al_menos + 1) / (permutaciones + 1), 5)}


def wilcoxon(diferencias: Sequence[float]) -> dict[str, Any]:
    """Wilcoxon de rangos con signo, unilateral (diferencias > 0), con la distribución exacta.

    Se descartan las diferencias cero. Con empates, los rangos promedio se duplican para que sean
    enteros y la distribución de W+ se calcula por conteo de todas las asignaciones de signo.
    """
    distintas = [d for d in diferencias if abs(d) > 1e-12]
    n = len(distintas)
    resultado: dict[str, Any] = {"n_total": len(diferencias), "n_sin_cero": n, "ceros": len(diferencias) - n}
    if n == 0:
        return {**resultado, "w_mas": None, "p_unilateral": None}
    r = rangos([abs(d) for d in distintas])
    dobles = [int(round(2 * v)) for v in r]
    w_mas_doble = sum(v for v, d in zip(dobles, distintas) if d > 0)
    total = sum(dobles)
    conteos = [0] * (total + 1)
    conteos[0] = 1
    for v in dobles:
        for s in range(total, v - 1, -1):
            conteos[s] += conteos[s - v]
    casos = 2 ** n
    p = sum(conteos[w_mas_doble:]) / casos
    return {**resultado, "w_mas": w_mas_doble / 2, "p_unilateral": round(p, 6)}


def intervalo_bootstrap(valores: Sequence[float], *, remuestreos: int = REMUESTREOS,
                        semilla: int = SEMILLA) -> tuple[float | None, float | None]:
    """IC 95 % de la media por bootstrap de percentiles."""
    if len(valores) < 2:
        return None, None
    generador = random.Random(semilla)
    n = len(valores)
    medias = sorted(sum(valores[generador.randrange(n)] for _ in range(n)) / n for _ in range(remuestreos))
    return round(medias[int(0.025 * remuestreos)], 4), round(medias[int(0.975 * remuestreos) - 1], 4)


def resumen_numerico(valores: Iterable[float | None]) -> dict[str, Any]:
    """n, media, desviación estándar, mediana, p95 (rango más cercano), mínimo y máximo."""
    datos = sorted(v for v in valores if v is not None)
    if not datos:
        return {"n": 0, "media": None, "de": None, "mediana": None, "p95": None, "minimo": None, "maximo": None}
    p95 = datos[max(0, math.ceil(0.95 * len(datos)) - 1)]
    return {"n": len(datos), "media": round(mean(datos), 4), "de": round(stdev(datos), 4) if len(datos) > 1 else None,
            "mediana": round(median(datos), 4), "p95": round(p95, 4), "minimo": round(datos[0], 4),
            "maximo": round(datos[-1], 4)}


def comparacion_emparejada(a: Sequence[float], b: Sequence[float]) -> dict[str, Any]:
    """Diferencia b − a emparejada: media con IC 95 % (bootstrap), mediana y Wilcoxon unilateral (b > a)."""
    diferencias = [y - x for x, y in zip(a, b)]
    bajo, alto = intervalo_bootstrap(diferencias)
    return {"n": len(diferencias), "media_a": _r(mean(a)) if a else None, "media_b": _r(mean(b)) if b else None,
            "diferencia_media": _r(mean(diferencias)) if diferencias else None, "ic95_bajo": bajo, "ic95_alto": alto,
            "diferencia_mediana": _r(median(diferencias)) if diferencias else None, "wilcoxon": wilcoxon(diferencias)}
