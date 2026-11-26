"""Figuras PNG de la evaluación técnica para el informe (matplotlib).

Paleta: los tres primeros colores categóricos de la paleta de referencia de la guía de visualización
(azul, naranja y aguamarina), que se distinguen entre sí también con daltonismo; cada método conserva
su color en todas las figuras y además tiene su propia forma de marcador, para la impresión en gris.
Las figuras no llevan título (el número y el título van en el informe, según APA 7) y usan coma decimal.
Si hay corridas reales con modelo, la corrida simulada no se dibuja.
En la extracción y la temporalidad, el modelo aparece dos veces: con el complemento del diccionario (lo
que muestra la app; naranja, como en las demás figuras) y solo el modelo (aguamarina).
"""

from __future__ import annotations

import random
from pathlib import Path
from statistics import median
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")  # azul, naranja, aguamarina, amarillo
MARCADORES = ("o", "s", "D")
TINTA, TINTA_2, TENUE = "#0b0b0b", "#52514e", "#898781"
REJILLA, BASE, SUPERFICIE = "#e1e0d9", "#c3c2b7", "#ffffff"
ANCHO = 6.3  # pulgadas (16 cm: el ancho útil de una página carta con márgenes de 2,5 cm)
NIVELES = ("bajo", "medio", "alto")


def _coma(decimales: int = 1):
    return FuncFormatter(lambda valor, _: f"{valor:.{decimales}f}".replace(".", ","))


def _estilo() -> None:
    plt.rcParams.update({
        "font.family": "Arial", "font.size": 9, "text.color": TINTA, "axes.labelcolor": TINTA_2,
        "axes.edgecolor": BASE, "axes.linewidth": 0.8, "xtick.color": TENUE, "ytick.color": TENUE,
        "xtick.labelcolor": TINTA_2, "ytick.labelcolor": TINTA_2, "axes.grid": True, "grid.color": REJILLA,
        "grid.linewidth": 0.6, "grid.linestyle": "-", "axes.axisbelow": True, "legend.frameon": False,
        "figure.facecolor": SUPERFICIE, "axes.facecolor": SUPERFICIE, "savefig.facecolor": SUPERFICIE,
        "axes.spines.top": False, "axes.spines.right": False,
    })


def _corridas_dibujables(evaluacion) -> list:
    reales = [c for c in evaluacion.corridas if c.metodo not in ("linea_base", "simulado")]
    elegidas = [c for c in evaluacion.corridas if c.metodo == "linea_base"]
    elegidas += reales or [c for c in evaluacion.corridas if c.metodo == "simulado"]
    return elegidas[:3]


#: Color y marcador de cada variante: la del modelo que usa la app conserva el color del modelo en las demás figuras.
_POSICION_VARIANTE = {"linea_base": 0, "modelo_y_diccionario": 1, "modelo": 2}


def _series_por_variante(corridas, disponibles: set) -> list[tuple[int, Any, str]]:
    """(posición de color, corrida, variante) de la línea base, del modelo + diccionario y del modelo solo."""
    series = [(_POSICION_VARIANTE[v], c, v) for c in corridas for v in c.variantes() if (c.nombre, v) in disponibles]
    return sorted(series, key=lambda s: s[0])[:3]


def _etiqueta_variante(corrida, variante: str) -> str:
    if variante == "modelo":
        return f"{corrida.etiqueta}: solo el modelo (sin el complemento del diccionario)"
    if variante == "modelo_y_diccionario":
        return f"{corrida.etiqueta}: modelo + diccionario (lo que muestra la app)"
    return corrida.etiqueta


def _guardar(figura, ruta: Path) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    figura.savefig(ruta, dpi=200, bbox_inches="tight")
    plt.close(figura)
    return ruta


def figura_extraccion(evaluacion, corridas, ruta: Path) -> Path | None:
    """F1 (micro) por campo y método, en TXT: puntos con su forma y color por método."""
    campos = (("habilidades", "Habilidades"), ("experiencias", "Experiencias\n(cargo y empresa)"),
              ("experiencias_fechas", "Experiencias\ncon fechas (±1 mes)"), ("formacion", "Formación"))
    resumen = {(f["corrida"], f["variante"]): f for f in evaluacion.tablas["extraccion_resumen"] if f["formato"] == "txt"}
    series = _series_por_variante(corridas, set(resumen))
    if not series:
        return None
    figura, eje = plt.subplots(figsize=(ANCHO, 3.2))
    desplazamientos = [(k - (len(series) - 1) / 2) * 0.2 for k in range(len(series))]
    for k, (i, corrida, variante) in enumerate(series):
        fila = resumen[(corrida.nombre, variante)]
        valores = [fila[f"{campo}_f1"] for campo, _ in campos]
        ys = [len(campos) - 1 - j + desplazamientos[k] for j in range(len(campos))]
        eje.scatter(valores, ys, s=46, color=SERIES[i], marker=MARCADORES[i], edgecolors=SUPERFICIE, linewidths=1.5,
                    label=_etiqueta_variante(corrida, variante), zorder=3)
    eje.set_yticks(range(len(campos)), [n for _, n in reversed(campos)])
    eje.set_xlim(0, 1.02)
    eje.xaxis.set_major_formatter(_coma(1))
    eje.set_xlabel("F1 (0 a 1; más es mejor)")
    eje.grid(axis="y", visible=False)
    eje.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=1, fontsize=8, handletextpad=0.4)
    return _guardar(figura, ruta)


def figura_compatibilidad(evaluacion, corridas, ruta: Path) -> Path | None:
    """H2: compatibilidad por nivel previsto (oferta A), un panel por método, con la mediana por nivel."""
    filas = [f for f in evaluacion.tablas["compatibilidad"] if f["grupo"] == "principal"]
    corridas = [c for c in corridas if any(f["corrida"] == c.nombre for f in filas)]
    if not corridas:
        return None
    h2 = {(f["corrida"], f["version_oferta"], f["vacante"]): f for f in evaluacion.tablas["h2_validez_orden"]}
    figura, ejes = plt.subplots(1, len(corridas), figsize=(ANCHO, 2.9), sharey=True, squeeze=False)
    generador = random.Random(7)
    for i, (eje, corrida) in enumerate(zip(ejes[0], corridas)):
        propias = [f for f in filas if f["corrida"] == corrida.nombre]
        for x, nivel in enumerate(NIVELES):
            valores = [f["compatibilidad"] for f in propias if f["nivel"] == nivel]
            xs = [x + generador.uniform(-0.16, 0.16) for _ in valores]
            eje.scatter(xs, valores, s=26, color=SERIES[i], marker=MARCADORES[i], edgecolors=SUPERFICIE,
                        linewidths=1.2, zorder=3)
            if valores:  # la mediana va detrás de los puntos para no taparlos
                eje.plot([x - 0.28, x + 0.28], [median(valores)] * 2, color=TINTA, linewidth=2, solid_capstyle="round",
                         zorder=2)
        fila = h2.get((corrida.nombre, "A", "todas"))
        texto = corrida.etiqueta
        if fila and fila["rho_compatibilidad"] is not None:
            p = fila["p_unilateral_compatibilidad"]
            texto += f"\nρ = {fila['rho_compatibilidad']:.2f}".replace(".", ",") + (
                " (p < 0,001)" if p < 0.001 else f" (p = {p:.3f})".replace(".", ","))
        eje.set_title(texto, fontsize=8.5, color=TINTA, loc="left")
        eje.set_xticks(range(3), ["Bajo", "Medio", "Alto"])
        eje.set_xlim(-0.5, 2.5)
        eje.set_ylim(0, 102)
        eje.grid(axis="x", visible=False)
        eje.set_xlabel("Nivel de adecuación previsto")
    ejes[0][0].set_ylabel("Compatibilidad (%)")
    ejes[0][0].yaxis.set_major_formatter(_coma(0))
    figura.tight_layout()
    return _guardar(figura, ruta)


def figura_mejoras(evaluacion, corridas, ruta: Path) -> Path | None:
    """H3: cambio de la puntuación general y de la compatibilidad tras aplicar las mejoras (dos paneles)."""
    filas = evaluacion.tablas["mejoras"]
    corridas = [c for c in corridas if c.con_modelo and any(f["corrida"] == c.nombre for f in filas)]
    if not corridas:
        return None
    figura, ejes = plt.subplots(1, 2, figsize=(ANCHO, 0.9 + 0.55 * len(corridas)), sharey=True)
    generador = random.Random(11)
    medidas = (("delta_puntuacion", "Cambio de la puntuación general (puntos)"),
               ("delta_compatibilidad", "Cambio de la compatibilidad (puntos %)"))
    for eje, (medida, titulo) in zip(ejes, medidas):
        todos = [f[medida] for f in filas if f["corrida"] in {c.nombre for c in corridas}]
        bajo, alto = min(todos + [0]), max(todos + [0])
        margen = max(0.5, 0.1 * (alto - bajo))
        for k, corrida in enumerate(corridas):
            i = [c.nombre for c in _corridas_dibujables(evaluacion)].index(corrida.nombre)
            valores = [f[medida] for f in filas if f["corrida"] == corrida.nombre]
            ys = [k + generador.uniform(-0.14, 0.14) for _ in valores]
            eje.scatter(valores, ys, s=22, color=SERIES[i], marker=MARCADORES[i], edgecolors=SUPERFICIE,
                        linewidths=1.0, zorder=3)
            eje.plot([median(valores)] * 2, [k - 0.3, k + 0.3], color=TINTA, linewidth=2, zorder=2)
        eje.set_xlim(min(bajo - margen, -1), max(alto + margen, 1))
        eje.axvline(0, color=BASE, linewidth=0.8, zorder=1)
        eje.set_xlabel(titulo)
        eje.xaxis.set_major_formatter(_coma(1))
        eje.grid(axis="y", visible=False)
    ejes[0].set_yticks(range(len(corridas)), [c.etiqueta for c in corridas])
    ejes[0].set_ylim(-0.6, len(corridas) - 0.4)
    figura.tight_layout()
    return _guardar(figura, ruta)


def figura_temporalidad(evaluacion, corridas, ruta: Path) -> Path | None:
    """Distribución del error absoluto de los meses por habilidad (habilidades con uso fechado, TXT)."""
    tramos = (("0", 0, 0), ("1–2", 1, 2), ("3–6", 3, 6), ("7–12", 7, 12), ("> 12", 13, 10_000))
    pares = [p for p in evaluacion.tablas["temporalidad"] if p["formato"] == "txt" and p["meses_referencia"] > 0]
    series = []
    for i, corrida, variante in _series_por_variante(corridas, {(p["corrida"], p["variante"]) for p in pares}):
        propios = [p for p in pares if p["corrida"] == corrida.nombre and p["variante"] == variante]
        series.append((i, _etiqueta_variante(corrida, variante),
                       [100 * sum(a <= p["error_abs"] <= b for p in propios) / len(propios) for _, a, b in tramos]))
    if not series:
        return None
    figura, eje = plt.subplots(figsize=(ANCHO, 2.9))
    ancho = 0.8 / len(series)
    for k, (i, etiqueta, valores) in enumerate(series):
        xs = [x - 0.4 + ancho * (k + 0.5) for x in range(len(tramos))]
        eje.bar(xs, valores, width=ancho * 0.92, color=SERIES[i], label=etiqueta, zorder=3)
    eje.set_xticks(range(len(tramos)), [t for t, _, _ in tramos])
    eje.set_xlabel("Error absoluto de los meses acumulados por habilidad")
    eje.set_ylabel("Habilidades (%)")
    eje.set_ylim(0, 100)
    eje.grid(axis="x", visible=False)
    eje.yaxis.set_major_formatter(_coma(0))
    eje.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=1, fontsize=8)
    return _guardar(figura, ruta)


def figura_guardia(evaluacion, corridas, ruta: Path) -> Path | None:
    """H4: sugerencias del modelo según lo que hizo la guardia (barras apiladas en porcentaje)."""
    filas = {f["corrida"]: f for f in evaluacion.tablas["guardia"]}
    corridas = [c for c in corridas if c.nombre in filas and filas[c.nombre]["sugerencias_del_modelo"]]
    if not corridas:
        return None
    categorias = (("aprobadas", "Aprobadas"), ("con_informacion_nueva", "Descartadas: información nueva"),
                  ("con_cambios_de_contenido", "Descartadas: cambios de contenido"),
                  ("descartadas_por_otros_motivos", "Descartadas: otros motivos"))
    figura, eje = plt.subplots(figsize=(ANCHO, 0.9 + 0.5 * len(corridas)))
    for k, corrida in enumerate(corridas):
        fila = filas[corrida.nombre]
        izquierda = 0.0
        for j, (clave, nombre) in enumerate(categorias):
            valor = 100 * fila[clave] / fila["sugerencias_del_modelo"]
            eje.barh(k, valor, left=izquierda, height=0.42, color=SERIES[j], edgecolor=SUPERFICIE, linewidth=1.5,
                     label=nombre if k == 0 else None, zorder=3)
            izquierda += valor
    eje.set_yticks(range(len(corridas)), [f"{c.etiqueta}\n(n = {filas[c.nombre]['sugerencias_del_modelo']})"
                                          for c in corridas])
    eje.set_xlim(0, 100)
    eje.set_ylim(-0.6, len(corridas) - 0.4)
    eje.xaxis.set_major_formatter(_coma(0))
    eje.set_xlabel("Sugerencias del modelo (%)")
    eje.grid(axis="y", visible=False)
    eje.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, fontsize=8)
    return _guardar(figura, ruta)


def dibujar(evaluacion, carpeta: Path) -> list[Path]:
    _estilo()
    corridas = _corridas_dibujables(evaluacion)
    rutas = [
        figura_extraccion(evaluacion, corridas, carpeta / "figura_extraccion_f1.png"),
        figura_compatibilidad(evaluacion, corridas, carpeta / "figura_compatibilidad_por_nivel.png"),
        figura_mejoras(evaluacion, corridas, carpeta / "figura_antes_despues.png"),
        figura_temporalidad(evaluacion, corridas, carpeta / "figura_error_temporalidad.png"),
        figura_guardia(evaluacion, corridas, carpeta / "figura_guardia.png"),
    ]
    return [r for r in rutas if r is not None]
