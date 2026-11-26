"""Gráficos de la app (Plotly), con una paleta accesible (RNF05).

Colores: la paleta de la interfaz (crema, petróleo, coral y carbón), con los dos tonos de datos ajustados
para el fondo crema y comprobados con el validador de la guía de visualización usada en el proyecto:
petróleo #008e8c y coral #e4583b tienen contraste de 3:1 o más con el fondo, croma suficiente y se
distinguen entre sí con daltonismo (además, cambian de forma: círculo lleno y rombo hueco). Los colores
de estado van siempre con un símbolo y una etiqueta. Toda cifra de un gráfico también está en una tabla.

- figura_criterios: barras por criterio de la rúbrica sobre una pista con su máximo.
- figura_tags: impacto y relevancia de cada tag (puntos con forma y color distintos).
- figura_linea_de_tiempo: periodos de uso de cada tag y el mes de referencia.
- figura_antes_despues: cada criterio antes y después de las mejoras.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import plotly.graph_objects as go

from analizador_cv.utilidades import numero_es

PETROLEO, PETROLEO_CLARO = "#008e8c", "#8fcac6"  # el claro solo marca «antes», con leyenda y tabla
CORAL = "#e4583b"
PISTA = "#ece1d1"  # pista arena del máximo de cada criterio
TINTA, TINTA_2, TENUE = "#24212b", "#5c5664", "#847d8b"
REJILLA, BASE, SUPERFICIE = "#ece1d1", "#cfc3b3", "#fff7ec"  # SUPERFICIE = el fondo crema de la app
FUENTE = "'Instrument Sans', system-ui, -apple-system, 'Segoe UI', sans-serif"
FUENTE_TITULO = "'Instrument Serif', Georgia, serif"
CONFIGURACION = {"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"]}
_PERIODO = re.compile(r"^(\d{4})-(\d{2}) a (\d{4})-(\d{2})$")


def _estilo(figura: go.Figure, titulo: str, altura: int, *, leyenda: bool = False) -> go.Figure:
    figura.update_layout(
        title={"text": titulo, "x": 0, "xanchor": "left", "font": {"family": FUENTE_TITULO, "size": 20, "color": TINTA}},
        height=altura, margin={"l": 10, "r": 24, "t": 68 if leyenda else 52, "b": 36},
        paper_bgcolor=SUPERFICIE, plot_bgcolor=SUPERFICIE, separators=", ",
        font={"family": FUENTE, "color": TINTA_2, "size": 13}, showlegend=leyenda,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "xanchor": "left", "x": 0, "title": None},
        hoverlabel={"bgcolor": "#fffcf7", "bordercolor": BASE, "font": {"color": TINTA, "family": FUENTE}},
    )
    figura.update_xaxes(gridcolor=REJILLA, linecolor=BASE, zeroline=False, tickfont={"color": TENUE})
    figura.update_yaxes(showgrid=False, linecolor=BASE, tickfont={"color": TINTA_2}, automargin=True)
    return figura


def _alto(filas: int, *, por_fila: int = 34, minimo: int = 220) -> int:
    return max(minimo, 110 + por_fila * filas)


def figura_criterios(resultado: dict[str, Any]) -> go.Figure:
    """Puntos de cada criterio (petróleo) sobre una pista con su máximo; la etiqueta dice «x de máximo»."""
    criterios = list(reversed(resultado["puntuacion"]["criterios"]))  # Plotly dibuja la primera categoría abajo
    nombres = [c["nombre"] for c in criterios]
    figura = go.Figure()
    figura.add_bar(y=nombres, x=[c["maximo"] for c in criterios], orientation="h", width=0.5, hoverinfo="skip",
                   marker={"color": PISTA, "cornerradius": 4}, name="Máximo")
    figura.add_bar(y=nombres, x=[c["puntos"] for c in criterios], orientation="h", width=0.5, name="Puntos",
                   marker={"color": PETROLEO, "cornerradius": 4}, customdata=[c["maximo"] for c in criterios],
                   hovertemplate="%{y}: %{x:.1f} de %{customdata} puntos<extra></extra>")
    for criterio in criterios:
        figura.add_annotation(x=criterio["maximo"], y=criterio["nombre"], xanchor="left", xshift=8, showarrow=False,
                              text=f"{numero_es(criterio['puntos'], 1)} de {criterio['maximo']}",
                              font={"color": TINTA, "size": 13})
    figura.update_layout(barmode="overlay")
    figura.update_xaxes(range=[0, max(c["maximo"] for c in criterios) * 1.18], title=None, dtick=5)
    return _estilo(figura, "Puntos por criterio de la rúbrica", _alto(len(criterios), por_fila=46))


def tags_ordenados(tags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Primero los más relevantes y, entre ellos, los de mayor impacto."""
    return sorted(tags, key=lambda t: (-t["relevancia"], -t["impacto"], t["nombre"].lower()))


def figura_tags(tags: list[dict[str, Any]], *, limite: int = 20, frente_a_oferta: bool = False) -> go.Figure:
    seleccion = list(reversed(tags_ordenados(tags)[:limite]))
    nombres = [t["nombre"] for t in seleccion]
    detalle = [[numero_es(t["evidencia"]), numero_es(t["recencia"]), numero_es(t["duracion"]), t["meses"]]
               for t in seleccion]
    figura = go.Figure()
    # Una línea tenue une los dos valores de cada tag: muestra la distancia entre relevancia e impacto.
    xs, ys = [], []
    for tag in seleccion:
        xs += [tag["impacto"], tag["relevancia"], None]
        ys += [tag["nombre"], tag["nombre"], None]
    figura.add_scatter(x=xs, y=ys, mode="lines", line={"color": REJILLA, "width": 2}, hoverinfo="skip", showlegend=False)
    # El impacto es un círculo lleno y la relevancia, un rombo hueco más grande: si coinciden, se ven los dos.
    figura.add_scatter(x=[t["impacto"] for t in seleccion], y=nombres, mode="markers", name="Impacto", customdata=detalle,
                       marker={"color": PETROLEO, "size": 10, "symbol": "circle", "line": {"color": SUPERFICIE, "width": 2}},
                       hovertemplate=("%{y}: impacto %{x:.2f}<br>evidencia %{customdata[0]} · recencia %{customdata[1]}"
                                      " · duración %{customdata[2]} (%{customdata[3]} meses)<extra></extra>"))
    figura.add_scatter(x=[t["relevancia"] for t in seleccion], y=nombres, mode="markers", name="Relevancia" + (
        " frente a la oferta" if frente_a_oferta else ""), marker={"color": CORAL, "size": 15, "symbol": "diamond-open",
                                                                    "line": {"color": CORAL, "width": 2.5}},
                       hovertemplate="%{y}: relevancia %{x:.2f}<extra></extra>")
    figura.update_xaxes(range=[-0.03, 1.05], dtick=0.2, tickformat=".1f", title=None)
    titulo = "Relevancia e impacto de cada tag (0 a 1)" + (f" · los {limite} más relevantes" if len(tags) > limite else "")
    return _estilo(figura, titulo, _alto(len(seleccion), por_fila=28), leyenda=True)


def _mes(anio: str, mes: str) -> date:
    return date(int(anio), int(mes), 1)


def _mes_siguiente(dia: date) -> date:
    return date(dia.year + dia.month // 12, dia.month % 12 + 1, 1)


def tiene_periodo(tag: dict[str, Any]) -> bool:
    """True si algún uso del tag tiene un periodo con fechas («AAAA-MM a AAAA-MM»)."""
    return any(_PERIODO.match(u["periodo"]) for u in tag["usos"])


def figura_linea_de_tiempo(tags: list[dict[str, Any]], referencia: str, *, limite: int = 15) -> go.Figure | None:
    """Barras con los periodos de uso de cada tag (los que tienen fechas). None si ninguno tiene fechas."""
    con_fechas = [t for t in tags if tiene_periodo(t)]
    if not con_fechas:
        return None
    seleccion = sorted(sorted(con_fechas, key=lambda t: -t["meses"])[:limite],
                       key=lambda t: (t["primer_uso"] or "", t["nombre"].lower()), reverse=True)
    bases, duraciones, nombres, textos = [], [], [], []
    for tag in seleccion:
        for uso in tag["usos"]:
            coincidencia = _PERIODO.match(uso["periodo"])
            if not coincidencia:
                continue
            inicio = _mes(*coincidencia.groups()[:2])
            fin = _mes_siguiente(_mes(*coincidencia.groups()[2:]))  # el mes final se cuenta completo
            bases.append(inicio.isoformat())
            duraciones.append((fin - inicio).days * 86_400_000)
            nombres.append(tag["nombre"])
            textos.append(f"{uso['etiqueta']}<br>{uso['periodo']}")
    figura = go.Figure()
    figura.add_bar(base=bases, x=duraciones, y=nombres, orientation="h", width=0.55, customdata=textos,
                   marker={"color": PETROLEO, "cornerradius": 3}, hovertemplate="%{y}<br>%{customdata}<extra></extra>")
    fin_referencia = _mes_siguiente(_mes(*referencia.split("-")))
    figura.add_vline(x=fin_referencia.isoformat(), line={"color": TINTA_2, "width": 1})
    figura.add_annotation(x=fin_referencia.isoformat(), y=1, yref="paper", yanchor="bottom", showarrow=False,
                          text=f"referencia: {referencia}", font={"color": TINTA_2, "size": 12})
    figura.update_xaxes(type="date", tickformat="%Y", dtick="M12", title=None)
    titulo = "Línea de tiempo de las competencias" + (f" · las {limite} de más meses" if len(con_fechas) > limite else "")
    return _estilo(figura, titulo, _alto(len(seleccion), por_fila=30))


def figura_antes_despues(comparacion: dict[str, Any]) -> go.Figure:
    """Cada criterio antes (petróleo claro) y después (petróleo) de las mejoras, con la diferencia a la derecha."""
    criterios = list(reversed(comparacion["criterios"]))
    nombres = [c["nombre"] for c in criterios]
    xs, ys = [], []
    for criterio in criterios:
        xs += [criterio["antes"], criterio["despues"], None]
        ys += [criterio["nombre"], criterio["nombre"], None]
    figura = go.Figure()
    figura.add_scatter(x=xs, y=ys, mode="lines", line={"color": BASE, "width": 2}, hoverinfo="skip", showlegend=False)
    for clave, nombre, color in (("antes", "Antes", PETROLEO_CLARO), ("despues", "Después", PETROLEO)):
        figura.add_scatter(x=[c[clave] for c in criterios], y=nombres, mode="markers", name=nombre,
                           marker={"color": color, "size": 12, "line": {"color": SUPERFICIE, "width": 2}},
                           customdata=[c["maximo"] for c in criterios],
                           hovertemplate=f"%{{y}} ({nombre.lower()}): %{{x:.1f}} de %{{customdata}}<extra></extra>")
    maximo = max(c["maximo"] for c in criterios)
    for criterio in criterios:
        delta = criterio["delta"] or 0
        texto = "sin cambio" if not delta else f"{'+' if delta > 0 else '−'}{numero_es(abs(delta), 1)}"
        figura.add_annotation(x=maximo * 1.02, y=criterio["nombre"], xanchor="left", showarrow=False, text=texto,
                              font={"color": TINTA if delta else TENUE, "size": 13})
    figura.update_xaxes(range=[0, maximo * 1.22], dtick=5, title=None)
    return _estilo(figura, "Puntos por criterio antes y después de las mejoras", _alto(len(criterios), por_fila=46),
                   leyenda=True)
