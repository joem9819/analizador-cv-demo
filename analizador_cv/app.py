"""Interfaz web del Analizador CV (Streamlit). Se abre con «python -m analizador_cv app».

1. Hoja de vida: un archivo propio (con aviso de privacidad y autorización) o un ejemplo ficticio, y
   el enmascaramiento opcional de los datos personales.
2. Tipo de análisis: general, o frente a una oferta por URL o con su texto pegado (si la URL falla o es
   de un portal que prohíbe el acceso automatizado, la app pide el texto).
3. Resultados en pestañas: resumen, tags y temporalidad, requisitos, información extraída, mejoras y
   antes/después (con exportación) y detalle técnico. Abajo, el historial local.

El modelo es Gemini 3.5 Flash-Lite por su API (el único perfil de configuracion.json). Sin un modelo listo, la
app funciona en modo demostración con la línea base (RF14), marcada como tal.
Los cálculos los hace el motor (analisis/) y las operaciones, interfaz/flujo.py: aquí solo se dibuja.
La configuración y la base de datos se pueden cambiar con ANALIZADOR_CV_CONFIG y ANALIZADOR_CV_BD.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from analizador_cv import __version__, bd  # noqa: E402
from analizador_cv.analisis.comparacion import aplicar_mejoras  # noqa: E402
from analizador_cv.analisis.fechas import Mes  # noqa: E402
from analizador_cv.analisis.motor import ETAPAS_POR_MODO, ErrorAnalisis  # noqa: E402
from analizador_cv.analisis.plantillas import ErrorPlantilla  # noqa: E402
from analizador_cv.configuracion import Configuracion, ErrorConfiguracion, cargar_archivo_env, cargar_configuracion  # noqa: E402
from analizador_cv.datos_personales import restaurar  # noqa: E402
from analizador_cv.documentos import ErrorDocumento  # noqa: E402
from analizador_cv.interfaz import exportacion, flujo, graficos, tablas  # noqa: E402
from analizador_cv.interfaz.flujo import DEMOSTRACION, EstadoPerfil, Ejecucion  # noqa: E402
from analizador_cv.llm.backends import ErrorBackend  # noqa: E402
from analizador_cv.llm.negociacion import NegociacionFallida  # noqa: E402
from analizador_cv.llm.prueba_conexion import prueba_conexion  # noqa: E402
from analizador_cv.utilidades import cantidad, formatear_duracion, json_legible, numero_es  # noqa: E402

ERRORES = (ErrorBackend, ErrorConfiguracion, NegociacionFallida, ErrorPlantilla, bd.ErrorBaseDatos, ErrorDocumento)
MODOS = ("General", "Frente a una oferta")
ORIGENES = ("Mi archivo", "Un ejemplo ficticio")
ETIQUETA_DEMO = ("MODO DEMOSTRACIÓN · sin modelo de lenguaje: la línea base por palabras clave extrae y evalúa con "
                 "reglas. Sirve para probar la app; con un modelo los resultados son más completos.")
ICONOS_ESTADO = {"valida": "✔", "invalida": "✖", "truncada": "✖", "error": "✖"}

#: Estilo editorial: crema #fff7ec de fondo, carbón #24212b para el texto, verde petróleo #0e7c7b en encabezados y
#: detalles, coral #ff6b4a en los botones principales y los elementos destacados, y lavanda #d7c5ff en acentos discretos.
#: Las secciones se separan con filetes y espacio, no con cajas ni sombras. El coral lleva texto carbón (5,6:1): con
#: blanco no llega a 3:1. El texto pequeño en petróleo usa el tono hondo #0b6463 (6,5:1 sobre el crema).
#: El tema de Streamlit (colores, fuentes y radios) está en .streamlit/config.toml.
CSS = """
<style>
:root {--acv-crema: #fff7ec; --acv-papel: #fffcf7; --acv-arena: #f6ecde; --acv-linea: #e6d9c6; --acv-carbon: #24212b;
       --acv-gris: #5c5664; --acv-petroleo: #0e7c7b; --acv-petroleo-hondo: #0b6463; --acv-coral: #ff6b4a;
       --acv-lavanda: #d7c5ff; --acv-lavanda-tinte: #f3edff; --acv-serif: "Instrument Serif", Georgia, serif;}
::selection {background: var(--acv-lavanda); color: var(--acv-carbon);}
[data-testid="stHeader"] {background: transparent;}
[data-testid="stMainBlockContainer"] {padding-top: 3.25rem; padding-bottom: 5rem; max-width: 84rem;}
section[data-testid="stSidebar"] h2 {padding-bottom: .45rem; margin-bottom: .35rem; border-bottom: 1px solid var(--acv-linea);}
[data-testid="stWidgetLabel"] p {font-weight: 500; letter-spacing: .005em;}
hr {border-bottom-color: var(--acv-linea) !important;}

/* Encabezado: antetítulo con filete coral, título en serif y datos del modelo como una línea de créditos */
.acv-hero {padding: 0 0 30px; margin: 0 0 34px; border-bottom: 1px solid var(--acv-carbon);}
.acv-hero-antetitulo {display: flex; align-items: center; gap: 14px; font-size: .72rem; font-weight: 600;
                      letter-spacing: .22em; text-transform: uppercase; color: var(--acv-petroleo-hondo);}
.acv-hero-antetitulo::before {content: ""; width: 34px; height: 1px; background: var(--acv-coral);}
.acv-hero-titulo {font-family: var(--acv-serif); font-weight: 400; font-size: clamp(2.7rem, 5.4vw, 4.4rem);
                  line-height: 1; letter-spacing: -.012em; color: var(--acv-carbon); margin-top: 18px;}
.acv-hero-titulo em {font-style: italic; color: var(--acv-petroleo);}
.acv-hero-texto {margin-top: 16px; max-width: 44rem; font-size: 1.06rem; line-height: 1.65; color: var(--acv-gris);}
.acv-chips {margin-top: 24px; display: flex; flex-wrap: wrap; gap: 10px 28px;}
.acv-chip {display: inline-flex; align-items: center; gap: 9px; font-size: .82rem; letter-spacing: .02em;
           color: var(--acv-carbon);}
.acv-chip::before {content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--acv-lavanda);}
.acv-chip.acv-petroleo::before {background: var(--acv-petroleo);}
.acv-chip.acv-coral::before {background: var(--acv-coral);}

/* Pasos: secciones abiertas bajo un filete, con el número en serif cursiva */
.st-key-paso_1, .st-key-paso_2, .st-key-paso_3 {border: 0 !important; border-top: 1px solid var(--acv-linea) !important;
           border-radius: 0 !important; background: transparent; padding: 30px 0 18px !important; margin-top: 10px;}
.acv-paso {display: flex; align-items: baseline; gap: 18px; margin: 0 0 18px; font-family: var(--acv-serif);
           font-size: 2.1rem; font-weight: 400; line-height: 1.1; color: var(--acv-carbon);}
.acv-num {font-family: var(--acv-serif); font-style: italic; font-size: 1.2rem; letter-spacing: .06em;
          color: var(--acv-petroleo); min-width: 1.7em;}

/* Botones: coral con texto carbón para el principal; los demás, de línea fina */
button[data-testid="stBaseButton-primary"], button[data-testid="stBaseButton-secondary"] {
           font-weight: 600; letter-spacing: .02em; padding-left: 1.25rem; padding-right: 1.25rem; box-shadow: none;
           transition: background-color .18s ease, border-color .18s ease, color .18s ease;}
button[data-testid="stBaseButton-primary"]:not(:disabled) {background: var(--acv-coral); border: 1px solid var(--acv-coral);
           color: var(--acv-carbon);}
button[data-testid="stBaseButton-primary"]:not(:disabled):hover {background: var(--acv-carbon);
           border-color: var(--acv-carbon); color: var(--acv-crema);}
button[data-testid="stBaseButton-primary"]:disabled {background: transparent; border: 1px solid var(--acv-linea);
           color: #9b94a0;}
button[data-testid="stBaseButton-secondary"] {background: transparent; border: 1px solid #cdbfac; color: var(--acv-carbon);
           font-weight: 500;}
button[data-testid="stBaseButton-secondary"]:not(:disabled):hover {background: var(--acv-papel);
           border-color: var(--acv-carbon); color: var(--acv-carbon);}
button[data-testid^="stBaseButton"] p {color: inherit;}
button[data-testid^="stBaseButton"]:focus-visible {outline: none; box-shadow: 0 0 0 3px var(--acv-lavanda) !important;}

/* Pestañas: texto sobrio, la elegida en carbón con un filete coral */
[data-testid="stTabs"] [role="tablist"] {gap: 22px;}
[data-testid="stTab"] {color: var(--acv-gris);}
[data-testid="stTab"] p {letter-spacing: .01em;}
[data-testid="stTab"][data-hovered], [data-testid="stTab"][data-selected] {color: var(--acv-carbon);}
[data-testid="stTab"][data-selected] .react-aria-SelectionIndicator {background-color: var(--acv-coral);}

/* Desplegables, carga de archivos, avisos y métricas */
[data-testid="stExpander"] details {background: transparent; border-color: var(--acv-linea); border-radius: 4px;}
[data-testid="stExpander"] summary:hover, [data-testid="stExpander"] summary:hover svg {color: var(--acv-petroleo-hondo);}
[data-testid="stFileUploaderDropzone"] {background: var(--acv-papel); border: 1px dashed #d4c5af; border-radius: 4px;}
[data-testid="stAlertContainer"] {border-radius: 3px;}
[data-testid="stMetric"] {background: transparent; border-color: var(--acv-linea) !important; border-radius: 4px;
           padding: 18px 22px !important;}
[data-testid="stMetricLabel"] p {font-size: .72rem; font-weight: 600; letter-spacing: .16em; text-transform: uppercase;
           color: var(--acv-gris);}
[data-testid="stMetricValue"] {font-family: var(--acv-serif); letter-spacing: -.01em;}

/* Cifras principales: abiertas, bajo un filete carbón, con una línea fina de medida */
.acv-tarjeta {--acv-tono: var(--acv-coral); border-top: 1px solid var(--acv-carbon); padding: 18px 2px 6px;}
.acv-tarjeta.acv-petroleo {--acv-tono: var(--acv-petroleo);}
.acv-etiqueta {color: var(--acv-gris); font-size: .72rem; font-weight: 600; letter-spacing: .16em; text-transform: uppercase;}
.acv-valor {font-family: var(--acv-serif); color: var(--acv-carbon); font-size: 4.4rem; font-weight: 400; line-height: 1;
            letter-spacing: -.015em; margin-top: 14px; font-variant-numeric: lining-nums;}
.acv-valor.acv-mediano {font-size: 3.2rem;}
.acv-valor small {font-family: "Instrument Sans", sans-serif; font-size: .95rem; color: var(--acv-gris); font-weight: 400;
                  letter-spacing: .02em; margin-left: 10px;}
.acv-medidor {height: 2px; background: #e8dccb; margin: 20px 0 12px;}
.acv-medidor > div {height: 100%; background: var(--acv-tono);}
.acv-nota {color: var(--acv-gris); font-size: .86rem; line-height: 1.55;}

/* Franja del modo demostración, textos citados y cambios frente al original */
.acv-demo {background: var(--acv-lavanda-tinte); border-left: 2px solid #9f88e8; padding: 12px 18px; border-radius: 0 3px 3px 0;
           color: var(--acv-carbon); font-size: .9rem; font-weight: 500; line-height: 1.55; margin: 0 0 26px;}
.acv-texto {white-space: pre-wrap; background: var(--acv-papel); border: 1px solid var(--acv-linea);
            border-left: 2px solid var(--acv-lavanda); border-radius: 2px; padding: 10px 14px; color: var(--acv-carbon);
            line-height: 1.55; margin-bottom: .5rem;}
.acv-quitado, .acv-agregado {white-space: pre-wrap; border-radius: 2px; padding: 2px 10px; margin: 2px 0;
            color: var(--acv-carbon);}
.acv-quitado {background: #ffe7df; border-left: 2px solid #e4583b;}
.acv-agregado {background: #e3f0ec; border-left: 2px solid var(--acv-petroleo);}

@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] {padding-top: 2.25rem;}
  .acv-hero {padding-bottom: 24px; margin-bottom: 26px;}
  .acv-paso {font-size: 1.7rem; gap: 12px;}
  .acv-valor {font-size: 3.4rem;}
  .acv-valor.acv-mediano {font-size: 2.6rem;}
  [data-testid="stTabs"] [role="tablist"] {gap: 18px;}
}
</style>
"""


# =============================================================================
# Utilidades de la interfaz
# =============================================================================
def _n(valor: float | None, decimales: int = 1) -> str:
    return numero_es(valor, decimales)


def _literal(texto: str | None) -> str:
    """Texto de la hoja de vida o del modelo para st.markdown, sin que se interprete como Markdown. Los
    paréntesis no se escapan: «\\(» abre una fórmula en Streamlit (y sin corchetes no hay enlaces)."""
    return re.sub(r"([\\`*_{}\[\]#+\-.!|<>~$:])", r"\\\1", texto or "")


def _html(contenido: str) -> None:
    st.markdown(contenido, unsafe_allow_html=True)


def _tarjeta(etiqueta: str, valor: str, unidad: str, fraccion: float | None, nota: str, *, grande: bool = True,
             tono: str = "") -> None:
    """tono: "" (coral) o "acv-petroleo"."""
    medidor = ("" if fraccion is None else
               f'<div class="acv-medidor"><div style="width:{max(0.0, min(1.0, fraccion)) * 100:.1f}%"></div></div>')
    _html(f'<div class="acv-tarjeta {tono}"><div class="acv-etiqueta">{html.escape(etiqueta)}</div>'
          f'<div class="acv-valor{"" if grande else " acv-mediano"}">{html.escape(valor)}<small>{html.escape(unidad)}'
          f'</small></div>{medidor}<div class="acv-nota">{html.escape(nota)}</div></div>')


def _paso(numero: int, titulo: str) -> None:
    _html(f'<div class="acv-paso" data-paso="{numero}" role="heading" aria-level="2"><span class="acv-num">{numero:02d}</span>'
          f'{html.escape(titulo)}</div>')


def _encabezado(estado: EstadoPerfil) -> None:
    modelo = "Modo demostración (sin modelo)" if estado.tipo == DEMOSTRACION else f"Modelo: {estado.etiqueta}"
    chips = "".join(f'<span class="acv-chip {tono}">{html.escape(c)}</span>'
                    for c, tono in ((modelo, "acv-petroleo"), ("PDF · DOCX · TXT", ""),
                                    ("Guardia contra la información inventada", "acv-coral")))
    _html('<div class="acv-hero"><div class="acv-hero-antetitulo">Análisis de hojas de vida con IA</div>'
          '<div class="acv-hero-titulo">Analizador <em>CV</em></div>'
          '<div class="acv-hero-texto">Analiza una hoja de vida, calcula una puntuación con desglose y sugiere mejoras '
          'sin inventar información. El modelo extrae y cita evidencia; el programa la verifica y calcula.</div>'
          f'<div class="acv-chips">{chips}</div></div>')


def _franja_demo() -> None:
    _html(f'<div class="acv-demo" role="status">⚠ {html.escape(ETIQUETA_DEMO)}</div>')


def _tabla(filas: list[dict[str, Any]], *, progreso: dict[str, str] | None = None, alto: int | None = None) -> None:
    """Tabla con los números en el formato del navegador; «progreso» = {columna: color} para valores de 0 a 1."""
    if not filas:
        return
    configuracion: dict[str, Any] = {}
    for columna, valor in filas[0].items():
        if progreso and columna in progreso:
            configuracion[columna] = st.column_config.ProgressColumn(columna, min_value=0.0, max_value=1.0,
                                                                     format="localized", color=progreso[columna])
        elif isinstance(valor, float):
            configuracion[columna] = st.column_config.NumberColumn(columna, format="localized")
    st.dataframe(filas, hide_index=True, column_config=configuracion, width="stretch",
                 height=alto if alto else "auto")


def _figura(figura, descripcion: str) -> None:
    st.plotly_chart(figura, theme=None, config=graficos.CONFIGURACION, alt=descripcion)


def _configuracion() -> Configuracion:
    cargar_archivo_env()
    return cargar_configuracion(os.environ.get("ANALIZADOR_CV_CONFIG") or None,
                                bd=os.environ.get("ANALIZADOR_CV_BD") or None)


def _ejecucion() -> Ejecucion | None:
    return st.session_state.get("ejecucion")


def _numero() -> int:
    """Número del análisis en pantalla: las claves de sus controles cambian con cada análisis nuevo."""
    return st.session_state.get("numero", 0)


def _mostrar(ejecucion: Ejecucion, equivalencias: dict[str, str]) -> None:
    st.session_state.ejecucion = ejecucion
    st.session_state.equivalencias = equivalencias
    st.session_state.numero = _numero() + 1


# =============================================================================
# Barra lateral
# =============================================================================
def _probar_conexion(cfg: Configuracion, perfil: str) -> None:
    with st.status(f"Prueba de conexión de {perfil}…", expanded=True) as estado:
        try:
            resultado = prueba_conexion(cfg, perfil, salida=st.write)
        except ERRORES as error:
            estado.update(label="La prueba de conexión falló", state="error")
            st.error(str(error))
            return
        if not resultado.get("salida_valida"):
            estado.update(label="La API respondió, pero la salida no fue válida", state="error")
            return
    st.session_state.conexion_lista = f"Conexión lista: ya puede analizar con {perfil}."
    st.rerun()


def _barra_lateral(cfg: Configuracion) -> tuple[EstadoPerfil, bool, str]:
    with st.sidebar:
        st.header("Modelo de lenguaje")
        estados = flujo.estados_perfiles(cfg)
        por_nombre = {e.nombre: e for e in estados}
        if st.session_state.get("perfil") not in por_nombre:
            st.session_state.perfil = flujo.perfil_inicial(cfg, estados)
        opciones = [n for n in por_nombre if n != DEMOSTRACION] + [DEMOSTRACION]
        nombre = st.radio("Modelo", opciones, key="perfil", label_visibility="collapsed",
                          format_func=lambda n: por_nombre[n].etiqueta + ("" if por_nombre[n].listo
                                                                          else " (no disponible)"))
        estado = por_nombre[nombre]
        if st.session_state.get("conexion_lista"):
            st.success(st.session_state.pop("conexion_lista"))
        if estado.problema:
            st.warning(estado.problema)
            if estado.falta_prueba and st.button("Probar la conexión", help="Envía una hoja de vida ficticia y guarda "
                                                 "los parámetros que acepta la API (1 a 3 solicitudes)."):
                _probar_conexion(cfg, nombre)
        if estado.aviso_privacidad:
            st.caption(f"**Privacidad.** {estado.aviso_privacidad}")
        st.header("Opciones")
        guardar = st.toggle("Guardar en el historial local", value=True, key="guardar",
                            help=f"Guarda el texto analizado, la oferta, el resultado y cada llamada al modelo en "
                                 f"{cfg.ruta_bd.name}, solo en este computador.")
        referencia = st.text_input("Mes de referencia (AAAA-MM)", key="referencia", placeholder="vacío = el mes actual",
                                   help="Con él se calculan la recencia y los periodos «a la fecha». Fíjelo para "
                                        "repetir un análisis con los mismos resultados.")
        st.caption(f"Analizador CV {__version__} · prototipo académico (Seminario de Investigación, ITM). "
                   "Las fórmulas y la rúbrica están en la definición del proyecto, §6.")
    return estado, guardar, referencia


# =============================================================================
# 1. Hoja de vida
# =============================================================================
def _aviso_privacidad(cfg: Configuracion, estado: EstadoPerfil) -> str:
    if estado.tipo == DEMOSTRACION:
        envio = "no se envía a ningún servicio: el modo demostración usa la línea base por palabras clave, sin modelo."
    else:
        envio = (f"el texto de la hoja de vida (enmascarado, si lo activa) se envía a la API de "
                 f"{cfg.perfil(estado.nombre)['proveedor']} para el análisis. {estado.aviso_privacidad or ''}")
    return f"""**Aviso de privacidad del prototipo** (definición del proyecto, §10; Ley 1581 de 2012 y Decreto 1377 de 2013)

- **Finalidad:** analizar su hoja de vida, calcular una puntuación con desglose y sugerir mejoras. La app la usa la
  persona dueña de la hoja de vida; no selecciona ni filtra candidatos.
- **Envío:** {_literal(envio)}
- **Almacenamiento:** solo en este computador, en `{cfg.ruta_bd.name}`, si la opción «Guardar en el historial
  local» está activa. Puede borrar ese archivo cuando quiera.
- **Enmascaramiento:** si lo activa, el correo, el teléfono, el documento, la dirección, los perfiles web y los
  nombres que escriba se reemplazan por marcas como \\[CORREO\\_1\\] antes del análisis y se restauran al descargar
  la versión mejorada.
- **Datos sensibles:** si su hoja de vida tiene datos sensibles (por ejemplo, de salud), no está obligado a autorizar
  su tratamiento: puede quitarlos antes de cargarla.
- **Autorización:** se pide antes de cargar el archivo, con la casilla de abajo. No marcarla no cuenta como
  autorización y el archivo no se carga."""


def _entrada_hoja(
    cfg: Configuracion, estado: EstadoPerfil, *, publica: bool = False
) -> tuple[tuple[str, bytes] | None, flujo.Ejemplo | None]:
    """(nombre y contenido del archivo elegido, ejemplo elegido)."""
    _paso(1, "Hoja de vida")
    origen = ORIGENES[1] if publica else st.radio(
        "¿Qué hoja de vida quiere analizar?", ORIGENES, horizontal=True, key="origen")
    contenido, ejemplo = None, None
    if origen == ORIGENES[0]:
        with st.expander("Aviso de privacidad (léalo antes de cargar su hoja de vida)",
                         expanded=not st.session_state.get("autorizo")):
            st.markdown(_aviso_privacidad(cfg, estado))
        autorizo = st.checkbox("Leí el aviso de privacidad y autorizo el tratamiento de mi hoja de vida para este "
                               "análisis.", key="autorizo")
        archivo = st.file_uploader("Hoja de vida en PDF, DOCX o TXT (hasta 5 MB)", type=["pdf", "docx", "txt"],
                                   key="archivo", disabled=not autorizo)
        if archivo is not None and autorizo:
            contenido = (archivo.name, archivo.getvalue())
    else:
        ejemplos = {e.id: e for e in flujo.cargar_ejemplos()}
        if not ejemplos:
            st.error("No se encontraron los ejemplos de datos/ejemplos/.")
            return None, None
        columnas = st.columns([3, 1])
        elegido = columnas[0].selectbox("Ejemplo", list(ejemplos), key="ejemplo",
                                        format_func=lambda i: f"{ejemplos[i].titulo} · {ejemplos[i].descripcion}")
        ejemplo = ejemplos[elegido]
        formato = columnas[1].selectbox("Formato", list(ejemplo.archivos), key="formato_ejemplo", format_func=str.upper)
        ruta = ejemplo.archivos.get(formato) or next(iter(ejemplo.archivos.values()))
        contenido = (ruta.name, ruta.read_bytes())
        st.caption("Datos ficticios: no corresponden a personas ni empresas reales.")
    if publica:
        return contenido, ejemplo
    columnas = st.columns([1, 2], vertical_alignment="bottom")
    enmascarar = columnas[0].toggle("Enmascarar datos personales", value=True, key="enmascarar",
                                    help="Reemplaza el correo, el teléfono, el documento, la dirección, los perfiles "
                                         "web y los nombres que escriba por marcas como [CORREO_1] antes del análisis.")
    columnas[1].text_input("Nombres que también se enmascaran (separados por comas)", key="nombres",
                           disabled=not enmascarar, placeholder="Ej.: María Ficticia Ejemplo")
    return contenido, ejemplo


# =============================================================================
# 2. Tipo de análisis
# =============================================================================
def _leer_oferta(cfg: Configuracion) -> None:
    oferta = flujo.leer_oferta_url(cfg, st.session_state.get("url", ""))
    st.session_state.oferta_leida = oferta
    if oferta.exito:
        st.session_state.oferta_texto = oferta.texto
        detalle = " · ".join(v for v in (oferta.titulo, oferta.empresa, oferta.ubicacion) if v)
        st.session_state.mensaje_oferta = ("exito", f"{oferta.mensaje} {detalle}".strip())
    else:
        st.session_state.mensaje_oferta = ("aviso", oferta.mensaje)


def _usar_oferta_ejemplo(ruta: Path) -> None:
    st.session_state.oferta_texto = ruta.read_text(encoding="utf-8")
    st.session_state.oferta_leida = None
    st.session_state.mensaje_oferta = ("exito", "Oferta ficticia del ejemplo en el campo de texto.")


def _entrada_oferta(cfg: Configuracion, ejemplo: flujo.Ejemplo | None, *, publica: bool = False) -> str:
    _paso(2, "Tipo de análisis")
    modo = st.radio("Tipo de análisis", MODOS, horizontal=True, key="modo", label_visibility="collapsed",
                    captions=("Puntuación con la rúbrica de 100 puntos", "Además, compatibilidad (%) con una oferta"))
    if modo == MODOS[0]:
        return modo
    if publica:
        st.session_state.oferta_leida = None
        oferta = ejemplo.oferta.read_text(encoding="utf-8") if ejemplo and ejemplo.oferta else ""
        st.session_state.oferta_texto = oferta
        if oferta:
            with st.expander("Ver oferta ficticia"):
                st.text(oferta)
        return modo
    columnas = st.columns([4, 1], vertical_alignment="bottom")
    url = columnas[0].text_input("Dirección de la oferta (URL, opcional)", key="url", persist_state="page",
                                 placeholder="https://empresa.com/empleos/123")
    columnas[1].button("Leer la oferta", on_click=_leer_oferta, args=(cfg,), disabled=not url.strip(), width="stretch",
                       help="Una sola petición a esa página. Los portales que prohíben el acceso automatizado "
                            "(como LinkedIn, Computrabajo y Magneto) no se descargan.")
    mensaje = st.session_state.get("mensaje_oferta")
    if mensaje:
        (st.success if mensaje[0] == "exito" else st.warning)(mensaje[1])
    if ejemplo is not None and ejemplo.oferta is not None:
        st.button("Usar la oferta ficticia de este ejemplo", on_click=_usar_oferta_ejemplo, args=(ejemplo.oferta,))
    st.text_area("Descripción de la oferta", key="oferta_texto", height=220, persist_state="page",
                 placeholder="Pegue aquí la descripción completa, con los requisitos, si no tiene la URL o si la app "
                             "no pudo leerla.")
    return modo


# =============================================================================
# Análisis
# =============================================================================
def _al_avanzar(etapa: str, intento) -> None:
    latencia = formatear_duracion(intento.latencia_s) if intento.latencia_s is not None else ""
    icono = ICONOS_ESTADO.get(intento.estado, "↻")
    estado = "lista" if intento.estado == "valida" else f"respuesta {intento.estado}, se reintenta"
    st.write(f"{icono} {tablas.ETAPAS.get(etapa, etapa)}: {estado} ({latencia}, intento {intento.numero})")


def _analizar(cfg: Configuracion, estado: EstadoPerfil, contenido: tuple[str, bytes], modo: str, guardar: bool,
              referencia_texto: str) -> None:
    try:
        referencia = Mes.desde_texto(referencia_texto.strip()) if referencia_texto.strip() else None
    except ValueError as error:
        st.error(f"Mes de referencia: {error}")
        return
    enmascarar = st.session_state.get("enmascarar", True)
    try:
        hoja = flujo.preparar_hoja(cfg, *contenido, enmascarar_datos=enmascarar,
                                   nombres=flujo.separar_nombres(st.session_state.get("nombres", "")))
    except ErrorDocumento as error:
        st.error(str(error))
        return
    oferta = None
    if modo == MODOS[1]:
        oferta = flujo.oferta_para_analizar(st.session_state.get("oferta_texto", ""), st.session_state.get("oferta_leida"))
        if not oferta.exito:
            st.error(oferta.mensaje)
            return
    etapas = ETAPAS_POR_MODO["especifico" if oferta else "general"]
    inicio = time.perf_counter()
    with st.status("Analizando…", expanded=True) as estado_ui:
        st.write(f"Hoja de vida: {hoja.documento.nombre} ({hoja.documento.formato.upper()}, "
                 f"{hoja.documento.palabras} palabras, lectura con {hoja.documento.metodo}).")
        if hoja.enmascarado:
            st.write(f"Datos personales enmascarados antes del análisis: {sum(hoja.conteo.values())}.")
        if estado.tipo == DEMOSTRACION:
            st.write("Línea base por palabras clave: sin llamadas a un modelo.")
        else:
            st.write(f"{cantidad(len(etapas), 'llamada', 'llamadas')} a {estado.etiqueta}: "
                     + ", ".join(tablas.ETAPAS[e].lower() for e in etapas) + ".")
        try:
            analizador = flujo.crear_analizador(cfg, estado.nombre, referencia=referencia, salida=st.write)
            ejecucion = flujo.analizar(cfg, analizador, hoja.documento, perfil=estado.nombre,
                                       enmascarado=hoja.enmascarado, oferta=oferta, guardar=guardar,
                                       al_avanzar=_al_avanzar)
        except ErrorAnalisis as error:
            estado_ui.update(label="El análisis no se completó y no tiene puntaje", state="error")
            st.error(f"El análisis no se completó y no tiene puntaje. {error}. Puede intentarlo de nuevo; si se "
                     "repite, pruebe con la demostración.")
            return
        except ERRORES as error:
            estado_ui.update(label="El análisis no se pudo hacer", state="error")
            st.error(str(error))
            return
        estado_ui.update(label=f"Análisis completo en {formatear_duracion(time.perf_counter() - inicio)}",
                         state="complete", expanded=False)
    _mostrar(ejecucion, hoja.equivalencias)


# =============================================================================
# 3. Resultados
# =============================================================================
def _descripcion_metodo(resultado: dict[str, Any]) -> str:
    modelo = resultado["modelo"]
    metodo = f"{modelo['proveedor']} · {modelo['modelo']}" if modelo else "línea base por palabras clave"
    modo = "frente a una oferta" if resultado["modo"] == "especifico" else "general"
    return f"Análisis {modo} con {metodo} · mes de referencia {resultado['fecha_referencia']}"


def _resultados(cfg: Configuracion, ejecucion: Ejecucion, guardar: bool, *, publica: bool = False) -> None:
    resultado = ejecucion.resultado
    _paso(3, "Resultados")
    st.caption(f"{_descripcion_metodo(resultado)} · {ejecucion.nombre_archivo}"
               + (f" · análisis {ejecucion.analisis_id} del historial" if ejecucion.analisis_id else ""))
    if resultado["metodo"] == "linea_base":
        _franja_demo()
    nombres = ["Resumen", "Tags y temporalidad"] + (["Requisitos de la oferta"] if resultado["compatibilidad"] else [])
    mejoras = "Sugerencias" if publica else "Mejoras y antes/después"
    nombres += ["Información extraída", mejoras, "Detalle técnico"]
    # Con estado: al pulsar un botón dentro de una pestaña, la app se vuelve a ejecutar y sigue en esa pestaña.
    pestanas = dict(zip(nombres, st.tabs(nombres, key="pestana", on_change="rerun")))
    with pestanas["Resumen"]:
        _pestana_resumen(resultado)
    with pestanas["Tags y temporalidad"]:
        _pestana_tags(resultado)
    if resultado["compatibilidad"]:
        with pestanas["Requisitos de la oferta"]:
            _pestana_requisitos(resultado)
    with pestanas["Información extraída"]:
        _pestana_extraccion(resultado)
    with pestanas[mejoras]:
        _pestana_mejoras(cfg, ejecucion, guardar, publica=publica)
    with pestanas["Detalle técnico"]:
        _pestana_detalle(ejecucion)


def _conteo_estados(resultado: dict[str, Any]) -> str:
    """«3 cubiertos, 1 mencionado y 2 ausentes»."""
    conteo = {"cubierto": 0, "mencionado": 0, "ausente": 0}
    for requisito in resultado["compatibilidad"]["requisitos"]:
        conteo[requisito["estado"]] += 1
    partes = [cantidad(conteo[e], e, e + "s") for e in conteo]
    return f"{partes[0]}, {partes[1]} y {partes[2]}"


def _pestana_resumen(resultado: dict[str, Any]) -> None:
    puntuacion = resultado["puntuacion"]
    columnas = st.columns(2)
    with columnas[0]:
        _tarjeta("Puntuación general", _n(puntuacion["total"]), "de 100", puntuacion["total"] / 100,
                 f"Rúbrica v{puntuacion['version_rubrica']}: estructura, redacción, habilidades, experiencia y formación.")
    with columnas[1]:
        if resultado["compatibilidad"]:
            _tarjeta("Compatibilidad con la oferta", f"{_n(resultado['compatibilidad']['porcentaje'])} %", "",
                     resultado["compatibilidad"]["porcentaje"] / 100,
                     f"Requisitos: {_conteo_estados(resultado)} (los obligatorios pesan el doble).", grande=False,
                     tono="acv-petroleo")
        else:
            _tarjeta("Compatibilidad con la oferta", "—", "", None,
                     "Análisis general: elija «Frente a una oferta» para calcularla.", grande=False, tono="acv-petroleo")
    _figura(graficos.figura_criterios(resultado), "Barras con los puntos de cada criterio sobre su máximo.")
    with st.expander("Desglose por concepto"):
        _tabla(tablas.filas_criterios(resultado))
    columnas = st.columns(2)
    with columnas[0]:
        st.markdown("**Fortalezas**")
        st.markdown("  \n".join(f":green[✔] {_literal(f)}" for f in resultado["fortalezas"])
                    or "Ninguna con las reglas de la rúbrica.")
    with columnas[1]:
        st.markdown("**Debilidades**")
        st.markdown("  \n".join(f":red[✖] {_literal(d)}" for d in resultado["debilidades"])
                    or "Ninguna con las reglas de la rúbrica.")
    for advertencia in resultado["advertencias"]:
        st.warning(advertencia)
    descartados = resultado["extraccion"]["no_verificados"]
    if descartados:
        st.info(f"La verificación descartó {cantidad(len(descartados), 'dato', 'datos')} que el modelo extrajo pero no "
                "están en el texto (detalle en «Información extraída»).")


def _pestana_tags(resultado: dict[str, Any]) -> None:
    tags = resultado["tags"]
    if not tags:
        st.info("No se identificaron habilidades en la hoja de vida.")
        return
    especifico = resultado["modo"] == "especifico"
    st.caption("**Impacto** = 0,5 × evidencia + 0,3 × recencia + 0,2 × duración. **Relevancia** "
               + ("frente a la oferta: 1 si es un requisito obligatorio, 0,6 si es deseable y 0,2 si la oferta no lo pide."
                  if especifico else "general: qué tanto aparece y si se usó en experiencias o proyectos recientes."))
    _figura(graficos.figura_tags(tags, frente_a_oferta=especifico),
            "Puntos con la relevancia y el impacto de cada tag, de 0 a 1.")
    figura = graficos.figura_linea_de_tiempo(tags, resultado["fecha_referencia"])
    if figura is not None:
        _figura(figura, "Barras con los periodos en que se usó cada tag.")
        sin_fechas = [t["nombre"] for t in tags if not graficos.tiene_periodo(t)]
        if sin_fechas:
            st.caption(f"Sin periodos con fechas (solo listados o sin fechas): {', '.join(sin_fechas)}.")
    else:
        st.info("Ningún tag tiene periodos con fechas: no hay línea de tiempo.")
    _tabla(tablas.filas_tags(resultado), progreso={"Relevancia": graficos.CORAL, "Impacto": graficos.PETROLEO})


def _pestana_requisitos(resultado: dict[str, Any]) -> None:
    compatibilidad = resultado["compatibilidad"]
    oferta = compatibilidad["oferta"]
    encabezado = " · ".join(v for v in (oferta.get("cargo"), oferta.get("empresa")) if v)
    st.markdown(f"**{_n(compatibilidad['porcentaje'])} % de compatibilidad**"
                + (f" con {_literal(encabezado)}" if encabezado else "") + f": {_conteo_estados(resultado)}.")
    filas = [{**f, "Estado": tablas.ESTADOS_REQUISITO[f["Estado"]]} for f in tablas.filas_requisitos(resultado)]
    _tabla(filas)
    st.caption("Compatibilidad = 100 × Σ(peso × cobertura) ÷ Σ peso. Obligatorio pesa 2 y deseable 1; cubierto vale 1 "
               "(con evidencia en una experiencia, proyecto o estudio), mencionado 0,5 y ausente 0. El tiempo de "
               "experiencia lo decide el programa con los meses calculados.")
    if oferta.get("descartados"):
        with st.expander(f"Requisitos que la verificación descartó ({len(oferta['descartados'])})"):
            _tabla([{"Requisito": d.get("texto") or d.get("valor", ""), "Motivo": d.get("motivo", "")}
                    for d in oferta["descartados"]])


def _pestana_extraccion(resultado: dict[str, Any]) -> None:
    extraccion = resultado["extraccion"]
    if extraccion["perfil"]:
        st.markdown("**Perfil**")
        _html(f'<div class="acv-texto">{html.escape(extraccion["perfil"])}</div>')
    st.markdown("**Experiencia, proyectos y formación** (periodos calculados por el programa)")
    _tabla(tablas.filas_bloques(resultado))
    with st.expander("Actividades de cada experiencia y proyecto"):
        for bloque in extraccion["experiencias"] + extraccion["proyectos"]:
            st.markdown(f"**{_literal(bloque['titulo'])}**" + (f" · {_literal(bloque['entidad'])}" if bloque["entidad"] else ""))
            marcas = lambda a: (" · logro" if a["es_logro"] else "") + (" · con cifras" if a["cuantificada"] else "")  # noqa: E731
            st.markdown("\n".join(f"- {_literal(a['texto'])}{marcas(a)}" for a in bloque["actividades"])
                        or "Sin actividades descritas.")
    columnas = st.columns(2)
    with columnas[0]:
        st.markdown("**Habilidades**")
        st.markdown(" ".join(f":{'violet' if h['tipo'] == 'blanda' else 'blue'}-badge[{_literal(h['nombre'])}]"
                             for h in extraccion["habilidades"]) or "Ninguna.")
        st.caption("Azul: técnicas · gris: blandas.")
    with columnas[1]:
        st.markdown("**Idiomas**")
        st.markdown("\n".join(f"- {_literal(i['idioma'])}" + (f" ({_literal(i['nivel'])})" if i.get("nivel") else "")
                              for i in extraccion["idiomas"]) or "Ninguno.")
    redaccion = resultado["redaccion"]
    st.markdown("**Redacción** (niveles de 0 a 3)")
    st.markdown(f"Verbos de acción: {redaccion['verbos_accion']} · claridad: {redaccion['claridad']} · ortografía: "
                f"{redaccion['nivel_ortografia']}" + ("" if redaccion["ortografia_evaluada"] else " (no evaluada: nivel neutro)"))
    if redaccion["errores"]:
        _tabla([{"Escrito": e["fragmento"], "Corrección": e["correccion"]} for e in redaccion["errores"]])
    st.markdown("**Verificación contra el texto original**")
    st.markdown(f"Tasa de verificación: {_n(100 * extraccion['tasa_verificacion'])} % de "
                f"{extraccion['elementos_extraidos']} datos extraídos.")
    if extraccion["no_verificados"]:
        st.caption("Datos descartados porque no están en el texto:")
        _tabla([{"Campo": d["campo"], "Valor": d["valor"], "Motivo": d["motivo"]} for d in extraccion["no_verificados"]])
    if extraccion["complementadas"]:
        st.caption("Habilidades escritas en el texto que el modelo omitió y agregó el diccionario:")
        _tabla([{k.capitalize(): v for k, v in c.items()} for c in extraccion["complementadas"]])


# -----------------------------------------------------------------------------
# Mejoras y antes/después
# -----------------------------------------------------------------------------
def _aplicar(ejecucion: Ejecucion, aceptadas: list[str], numero: int) -> None:
    texto, aplicadas, no_aplicadas = aplicar_mejoras(ejecucion.texto, ejecucion.resultado, aceptadas)
    st.session_state[f"mejorado_{numero}"] = texto
    st.session_state[f"aplicado_{numero}"] = texto
    st.session_state[f"aplicacion_{numero}"] = (aplicadas, no_aplicadas)


def _editar_original(ejecucion: Ejecucion, numero: int) -> None:
    st.session_state[f"mejorado_{numero}"] = ejecucion.texto
    st.session_state[f"aplicado_{numero}"] = None
    st.session_state[f"aplicacion_{numero}"] = ([], [])


def _sugerencia(sugerencia: dict[str, Any], numero: int | None = None) -> None:
    with st.container(border=True):
        if numero is not None:
            st.checkbox(f"Aplicar {sugerencia['id']}", value=True, key=f"aceptar_{numero}_{sugerencia['id']}")
        else:
            st.markdown(f"**{sugerencia['id']}** · {tablas.TIPOS_SUGERENCIA.get(sugerencia['tipo'], sugerencia['tipo'])}")
        if sugerencia["fragmento_original"]:
            columnas = st.columns(2)
            columnas[0].caption("Texto original")
            columnas[0].markdown(f'<div class="acv-texto">{html.escape(sugerencia["fragmento_original"])}</div>',
                                 unsafe_allow_html=True)
            columnas[1].caption("Propuesta")
            columnas[1].markdown(f'<div class="acv-texto">{html.escape(sugerencia["propuesta"])}</div>',
                                 unsafe_allow_html=True)
        else:
            st.markdown(_literal(sugerencia["propuesta"]))
        if sugerencia["justificacion"]:
            st.caption(_literal(sugerencia["justificacion"]))
        if sugerencia["motivos"]:
            st.caption("Motivo: " + _literal("; ".join(sugerencia["motivos"])))


def _pestana_mejoras(cfg: Configuracion, ejecucion: Ejecucion, guardar: bool, *, publica: bool = False) -> None:
    resultado, numero = ejecucion.resultado, _numero()
    sugerencias = resultado["sugerencias"]
    guardia = resultado["guardia"]
    if guardia:
        texto = (f"{cantidad(guardia['aprobadas'], 'sugerencia aprobada', 'sugerencias aprobadas')} · "
                 f"{guardia['descartadas']} descartadas por la guardia · "
                 f"{cantidad(guardia['requieren_confirmacion'], 'palabra clave', 'palabras clave')} para confirmar")
        st.markdown(f"**{texto}**")
        if guardia["del_modelo"]:
            cambios = guardia.get("con_cambios_de_contenido") or 0
            st.caption(f"Del modelo: {guardia['con_informacion_nueva']} de {guardia['del_modelo']} sugerencias traían "
                       "información que no está en la hoja de vida (cifras, habilidades, nombres, cargos o "
                       "responsabilidades)" + (f" y {cambios} más cambiaban lo que dice la frase (palabras que no "
                                               "estaban en el original)" if cambios else "")
                       + ". La guardia descartó esas sugerencias.")
    redaccion = [s for s in sugerencias if s["aplicable"]]
    consejos = [s for s in sugerencias if s["estado"] == "aprobada" and not s["aplicable"]]
    palabras = [s for s in sugerencias if s["estado"] == "requiere_confirmacion"]
    descartadas = [s for s in sugerencias if s["estado"] == "descartada"]
    if redaccion:
        st.markdown("#### Sugerencias de redacción")
        st.caption("Marcadas: las que aprobó la guardia. Desmarque las que no quiera aplicar.")
        for sugerencia in redaccion:
            _sugerencia(sugerencia, numero)
    elif resultado["metodo"] == "linea_base":
        aviso = "La demostración identifica sugerencias y palabras clave con reglas."
        if not publica:
            aviso = ("La línea base no reescribe frases: las sugerencias de redacción las propone el modelo. "
                     "Puede editar el texto usted mismo con «Editar el texto original» y reanalizarlo.")
        st.info(aviso)
    if consejos:
        st.markdown("#### Consejos de estructura")
        for sugerencia in consejos:
            _sugerencia(sugerencia)
    if palabras:
        st.markdown("#### Palabras clave que pide la oferta")
        st.warning("Agréguelas solo si realmente las tiene y puede demostrarlas: la app nunca las inserta.")
        for sugerencia in palabras:
            _sugerencia(sugerencia)
    if descartadas:
        with st.expander(f"Descartadas por la guardia ({len(descartadas)})"):
            for sugerencia in descartadas:
                _sugerencia(sugerencia)

    if publica:
        return
    st.markdown("#### Versión mejorada")
    aceptadas = [s["id"] for s in redaccion if st.session_state.get(f"aceptar_{numero}_{s['id']}", True)]
    columnas = st.columns(2)
    columnas[0].button(f"Aplicar {cantidad(len(aceptadas), 'sugerencia marcada', 'sugerencias marcadas')}",
                       type="primary", on_click=_aplicar, args=(ejecucion, aceptadas, numero), disabled=not aceptadas,
                       width="stretch")
    columnas[1].button("Editar el texto original", on_click=_editar_original, args=(ejecucion, numero), width="stretch")
    clave = f"mejorado_{numero}"
    if st.session_state.get(clave) is None:
        return
    aplicadas, no_aplicadas = st.session_state.get(f"aplicacion_{numero}", ([], []))
    if aplicadas:
        st.success(f"Se aplicaron {cantidad(len(aplicadas), 'sugerencia', 'sugerencias')}: {', '.join(aplicadas)}.")
    for item in no_aplicadas:
        st.warning(f"No se aplicó {item['id']}: {item['motivo']}.")
    columnas = st.columns(2)
    with columnas[0]:
        st.caption("Texto analizado (original)")
        _html(f'<div class="acv-texto" style="height:400px;overflow-y:auto">{html.escape(ejecucion.texto)}</div>')
    texto = columnas[1].text_area("Versión mejorada (puede editarla)", key=clave, height=420)
    st.caption("Si la edita, agregue solo información verdadera: la guardia revisa las sugerencias del modelo, no lo que "
               "usted escriba." + (" Los datos personales aparecen enmascarados y se restauran al descargar."
                                   if ejecucion.enmascarado else ""))
    cambios = tablas.cambios(ejecucion.texto, texto)
    with st.expander(f"Cambios frente al original ({cantidad(len(cambios), 'renglón', 'renglones')})"):
        for signo, renglon in cambios:
            clase = "acv-quitado" if signo == "-" else "acv-agregado"
            _html(f'<div class="{clase}">{"−" if signo == "-" else "+"} {html.escape(renglon)}</div>')
    equivalencias = st.session_state.get("equivalencias") or {}
    restaurado = restaurar(texto, equivalencias)
    base = Path(ejecucion.nombre_archivo).stem
    columnas = st.columns(3)
    columnas[0].download_button("Descargar en TXT", exportacion.hoja_txt(restaurado), f"{base}_mejorada.txt",
                                "text/plain", on_click="ignore", width="stretch")
    columnas[1].download_button("Descargar en DOCX", exportacion.hoja_docx(restaurado), f"{base}_mejorada.docx",
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                on_click="ignore", width="stretch")
    if columnas[2].button("Reanalizar la versión mejorada", type="primary", disabled=not cambios, width="stretch"):
        _reanalizar(cfg, ejecucion, texto, guardar, numero)
    if ejecucion.enmascarado and not equivalencias:
        st.caption("Este análisis se abrió del historial: los datos enmascarados no se pueden restaurar.")
    _comparacion(ejecucion, texto, numero)


def _reanalizar(cfg: Configuracion, ejecucion: Ejecucion, texto: str, guardar: bool, numero: int) -> None:
    inicio = time.perf_counter()
    with st.status("Reanalizando la versión mejorada…", expanded=True) as estado_ui:
        try:
            despues, comparacion = flujo.reanalizar(
                cfg, ejecucion, texto, editado_a_mano=texto != st.session_state.get(f"aplicado_{numero}"),
                guardar=guardar, salida=st.write, al_avanzar=_al_avanzar)
        except ErrorAnalisis as error:
            estado_ui.update(label="El reanálisis no se completó y no tiene puntaje", state="error")
            st.error(str(error))
            return
        except ERRORES as error:
            estado_ui.update(label="El reanálisis no se pudo hacer", state="error")
            st.error(str(error))
            return
        estado_ui.update(label=f"Reanálisis completo en {formatear_duracion(time.perf_counter() - inicio)}",
                         state="complete", expanded=False)
    st.session_state[f"comparacion_{numero}"] = (despues, comparacion)


def _comparacion(ejecucion: Ejecucion, texto: str, numero: int) -> None:
    guardado = st.session_state.get(f"comparacion_{numero}")
    if not guardado:
        return
    despues, comparacion = guardado
    st.markdown("#### Antes y después")
    if despues.texto != texto:
        st.caption("El texto cambió desde el último reanálisis: vuelva a reanalizar para actualizar la comparación.")
    if not comparacion["mismo_metodo"]:
        st.warning("El reanálisis no usó la misma configuración que el análisis original: las cifras no son comparables.")
    columnas = st.columns(2)
    total = comparacion["puntuacion"]
    columnas[0].metric("Puntuación general", f"{_n(total['despues'])} de 100", _signo(total["delta"]),
                       delta_color="normal" if total["delta"] else "off", border=True,
                       help=f"Antes: {_n(total['antes'])} de 100")
    if comparacion["compatibilidad"]:
        c = comparacion["compatibilidad"]
        columnas[1].metric("Compatibilidad con la oferta", f"{_n(c['despues'])} %", _signo(c["delta"], " puntos"),
                           delta_color="normal" if c["delta"] else "off", border=True, help=f"Antes: {_n(c['antes'])} %")
        if not c["delta"]:
            st.caption("La compatibilidad solo cambia si la nueva versión cubre más requisitos con evidencia; las "
                       "mejoras de redacción no agregan evidencia nueva.")
    _figura(graficos.figura_antes_despues(comparacion), "Puntos de cada criterio antes y después de las mejoras.")
    _tabla(tablas.filas_comparacion(comparacion))
    tags = tablas.filas_comparacion_tags(comparacion, solo_cambios=True)
    if tags:
        st.markdown("**Tags que cambiaron**")
        _tabla(tags)
    requisitos = [r for r in tablas.filas_comparacion_requisitos(comparacion) if r["Antes"] != r["Después"]]
    if requisitos:
        st.markdown("**Requisitos que cambiaron**")
        _tabla(requisitos)


def _signo(delta: float | None, unidad: str = "") -> str | None:
    """Diferencia para st.metric: el signo debe ser «+» o «-» (ASCII) para que se coloree bien."""
    if delta is None:
        return None
    return f"{'+' if delta >= 0 else '-'}{_n(abs(delta))}{unidad}"


# -----------------------------------------------------------------------------
# Detalle técnico
# -----------------------------------------------------------------------------
def _pestana_detalle(ejecucion: Ejecucion) -> None:
    resultado = ejecucion.resultado
    modelo = resultado["modelo"]
    datos = {"Método": "modelo de lenguaje" if modelo else "línea base por palabras clave (demostración)",
             "Modelo": f"{modelo['proveedor']} · {modelo['modelo']} (perfil {modelo['perfil']})" if modelo else "—",
             "Salida estructurada": (modelo or {}).get("modo_salida") or "—",
             "Versión del motor": resultado["version_motor"],
             "Rúbrica": resultado["puntuacion"]["version_rubrica"],
             "Fecha del análisis": resultado["fecha"][:16].replace("T", " "),
             "Mes de referencia": resultado["fecha_referencia"],
             "Huella de la configuración": resultado["huella_configuracion"],
             "Texto analizado": f"{resultado['texto']['palabras']} palabras · hash {resultado['texto']['hash'][:16]}…"}
    _tabla([{"Dato": k, "Valor": str(v)} for k, v in datos.items()])
    llamadas = resultado["llamadas"]
    if llamadas:
        latencia = sum(l["latencia_s"] or 0 for l in llamadas)
        tokens = sum(l["tokens_total"] or 0 for l in llamadas)
        st.markdown(f"**Llamadas al modelo:** {len(llamadas)} · {f'{tokens:,}'.replace(',', ' ')} tokens · "
                    f"{formatear_duracion(latencia)} en total (meta del prototipo: 60 s o menos, RNF09).")
        _tabla(tablas.filas_llamadas(resultado))
        for llamada in ejecucion.llamadas:
            titulo = (f"{tablas.ETAPAS.get(llamada['etapa'], llamada['etapa'])} · intento {llamada['intento']} · "
                      f"{llamada['estado']}")
            with st.expander(titulo):
                for mensaje in llamada["mensajes"]:
                    st.caption(f"Mensaje «{mensaje['role']}»")
                    st.code(mensaje["content"], language=None, wrap_lines=True)
                st.caption("Respuesta del modelo")
                st.code(_json_bonito(llamada["respuesta"]), language="json", wrap_lines=True)
                for error in llamada["errores"]:
                    st.error(error)
    else:
        st.info("Modo demostración: no hubo llamadas a un modelo. Las salidas de abajo las produjeron las reglas de la "
                "línea base y pasaron por la misma verificación y las mismas fórmulas.")
    with st.expander("Salidas de cada etapa (JSON)"):
        st.json(resultado["salidas"], expanded=1)


def _json_bonito(contenido: str | None) -> str:
    if not contenido:
        return "(sin contenido)"
    try:
        return json_legible(json.loads(contenido))
    except ValueError:
        return contenido


# =============================================================================
# Historial
# =============================================================================
def _abrir(cfg: Configuracion, analisis_id: int) -> None:
    try:
        _mostrar(flujo.abrir_analisis(cfg, analisis_id), {})
        st.session_state.error_historial = None
    except (ValueError, bd.ErrorBaseDatos) as error:
        st.session_state.error_historial = str(error)


def _historial(cfg: Configuracion) -> None:
    st.divider()
    with st.expander("Historial local (últimos 20 análisis)"):
        try:
            filas = flujo.historial(cfg)
        except bd.ErrorBaseDatos as error:
            st.error(str(error))
            return
        if not filas:
            st.caption(f"Aún no hay análisis guardados en {cfg.ruta_bd.name}.")
            return
        _tabla([{"Id": f["analisis_id"], "Fecha": f["fecha_inicio"][:16].replace("T", " "), "Archivo": f["nombre_archivo"],
                 "Modo": f["modo"], "Método": f["modelo"] or "línea base", "Puntuación": f["puntuacion"],
                 "Compatibilidad": f["compatibilidad"], "Estado": f["estado"],
                 "Versión de": f["analisis_previo_id"] or "", "Enmascarado": "sí" if f["enmascarado"] else "no"}
                for f in filas])
        completos = [f["analisis_id"] for f in filas if f["estado"] == "completo"]
        if completos:
            columnas = st.columns([3, 1], vertical_alignment="bottom")
            elegido = columnas[0].selectbox("Análisis", completos, key="historial_id")
            columnas[1].button("Abrir", on_click=_abrir, args=(cfg, elegido), width="stretch")
        if st.session_state.get("error_historial"):
            st.error(st.session_state.error_historial)
        st.caption(f"Archivo: {cfg.ruta_bd}")


# =============================================================================
# Página
# =============================================================================
def main(*, publica: bool = False) -> None:
    st.set_page_config(page_title="Analizador CV", page_icon="📄", layout="wide")
    _html(CSS)
    try:
        cfg = cargar_configuracion() if publica else _configuracion()
    except ErrorConfiguracion as error:
        st.error(f"No se pudo cargar la configuración: {error}")
        st.stop()
    if publica:
        estado = EstadoPerfil(DEMOSTRACION, "Demostración (sin modelo)", DEMOSTRACION, True)
        guardar, referencia = False, "2026-10"
    else:
        estado, guardar, referencia = _barra_lateral(cfg)
    _encabezado(estado)
    if estado.tipo == DEMOSTRACION:
        _franja_demo()
    with st.container(border=True, key="paso_1"):
        contenido, ejemplo = _entrada_hoja(cfg, estado, publica=publica)
    with st.container(border=True, key="paso_2"):
        modo = _entrada_oferta(cfg, ejemplo, publica=publica)

    faltan = []
    if contenido is None:
        faltan.append("cargue la hoja de vida (y autorice su tratamiento)" if st.session_state.get("origen") == ORIGENES[0]
                      else "elija un ejemplo")
    if not estado.listo:
        faltan.append("pruebe la conexión con el modelo o elija la demostración")
    if modo == MODOS[1] and not st.session_state.get("oferta_texto", "").strip():
        faltan.append("elija un ejemplo con una oferta" if publica else "pegue la descripción de la oferta o léala desde su URL")
    if st.button("Analizar", type="primary", disabled=bool(faltan)):
        _analizar(cfg, estado, contenido, modo, guardar, referencia)
    if faltan:
        st.caption("Para analizar: " + "; ".join(faltan) + ".")
    ejecucion = _ejecucion()
    if ejecucion is not None:
        with st.container(border=True, key="paso_3"):
            _resultados(cfg, ejecucion, guardar, publica=publica)
            st.download_button("Descargar los resultados (CSV y JSON, .zip)", _zip(ejecucion), _nombre_zip(ejecucion),
                               "application/zip", on_click="ignore")
    if not publica:
        _historial(cfg)


def _zip(ejecucion: Ejecucion) -> bytes:
    guardado = st.session_state.get(f"comparacion_{_numero()}")
    despues, comparacion = guardado if guardado else (None, None)
    return exportacion.resultados_zip(ejecucion.resultado, comparacion, despues.resultado if despues else None)


def _nombre_zip(ejecucion: Ejecucion) -> str:
    return f"resultados_{Path(ejecucion.nombre_archivo).stem}.zip"


if __name__ == "__main__":
    main()
