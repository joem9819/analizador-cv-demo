"""Operaciones de la interfaz que no dependen de Streamlit.

La app (app.py) solo dibuja: lee y enmascara la hoja de vida, obtiene la oferta, analiza, reanaliza
y consulta el historial con estas funciones, que usan el mismo motor que la línea de comandos.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from analizador_cv import bd
from analizador_cv.analisis.comparacion import comparar
from analizador_cv.analisis.fechas import Mes
from analizador_cv.analisis.motor import Analizador, RequisitosOferta, analizar_y_guardar
from analizador_cv.configuracion import RAIZ_PROYECTO, Configuracion, clave_definida
from analizador_cv.datos_personales import enmascarar
from analizador_cv.documentos import Documento, leer_documento
from analizador_cv.llm.modelo import ModeloLLM
from analizador_cv.llm.negociacion import ErrorCongelado, cargar_congelado
from analizador_cv.llm.salida import Intento
from analizador_cv.ofertas import Oferta, desde_texto, obtener_oferta
from analizador_cv.utilidades import leer_json

#: Opción «sin modelo»: la línea base por palabras clave (modo demostración, RF14).
DEMOSTRACION = "demostracion"
RUTA_EJEMPLOS = RAIZ_PROYECTO / "datos" / "ejemplos"
FORMATOS_EJEMPLO = ("pdf", "docx", "txt")
Avance = Callable[[str, Intento], None]


# =============================================================================
# Perfiles de modelo
# =============================================================================
@dataclass
class EstadoPerfil:
    nombre: str
    etiqueta: str
    tipo: str
    listo: bool
    problema: str | None = None
    aviso_privacidad: str | None = None
    #: tiene la clave, pero falta la prueba de conexión o ya no corresponde a la configuración
    falta_prueba: bool = False

    @property
    def envia_texto(self) -> bool:
        """True si el texto de la hoja de vida sale de este computador (API de un proveedor)."""
        return self.tipo == "openai_compatible"


def estados_perfiles(cfg: Configuracion) -> list[EstadoPerfil]:
    """La demostración y el modelo de configuracion.json (Gemini por su API), con lo que les falta para usarse."""
    estados = [EstadoPerfil(DEMOSTRACION, "Demostración (sin modelo)", DEMOSTRACION,
                            True, aviso_privacidad="El texto no sale de este computador: no se usa ningún modelo.")]
    for nombre in cfg.nombres_perfiles:
        perfil = cfg.perfil(nombre)
        etiqueta = f"{perfil['proveedor']} · {perfil['modelo']}"
        aviso = perfil.get("aviso_privacidad")
        if not clave_definida(perfil):
            estados.append(EstadoPerfil(nombre, etiqueta, perfil["tipo"], False,
                                        f"Falta la clave {perfil['variable_clave']} en el archivo .env de la carpeta "
                                        "del prototipo (README, sección 5).", aviso))
            continue
        try:
            cargar_congelado(cfg, perfil)
        except ErrorCongelado as error:
            estados.append(EstadoPerfil(nombre, etiqueta, perfil["tipo"], False, str(error), aviso, falta_prueba=True))
            continue
        estados.append(EstadoPerfil(nombre, etiqueta, perfil["tipo"], True, aviso_privacidad=aviso))
    return estados


def perfil_inicial(cfg: Configuracion, estados: list[EstadoPerfil]) -> str:
    """El perfil predeterminado si está listo; si no, la demostración."""
    predeterminado = cfg.datos["perfil_predeterminado"]
    return predeterminado if any(e.nombre == predeterminado and e.listo for e in estados) else DEMOSTRACION


# =============================================================================
# Ejemplos ficticios
# =============================================================================
@dataclass
class Ejemplo:
    id: str
    titulo: str
    descripcion: str
    archivos: dict[str, Path]
    oferta: Path | None


def cargar_ejemplos(carpeta: Path = RUTA_EJEMPLOS) -> list[Ejemplo]:
    """Hojas de vida ficticias de datos/ejemplos/ (ejemplos.json) con sus archivos en cada formato."""
    indice = carpeta / "ejemplos.json"
    if not indice.is_file():
        return []
    ejemplos = []
    for datos in leer_json(indice)["hojas_de_vida"]:
        fuente = carpeta / datos["fuente"]
        archivos = {f: fuente.with_suffix(f".{f}") for f in FORMATOS_EJEMPLO if fuente.with_suffix(f".{f}").is_file()}
        oferta = carpeta / datos["oferta"] if datos.get("oferta") else None
        ejemplos.append(Ejemplo(datos["id"], datos["titulo"], datos["descripcion"], archivos, oferta))
    return ejemplos


# =============================================================================
# Hoja de vida
# =============================================================================
@dataclass
class HojaPreparada:
    """Hoja de vida lista para analizar: el texto del documento ya está enmascarado si se pidió."""

    documento: Documento
    enmascarado: bool = False
    equivalencias: dict[str, str] = field(default_factory=dict)
    conteo: dict[str, int] = field(default_factory=dict)


def separar_nombres(texto: str) -> list[str]:
    """«María Ficticia, Otro Nombre» → ["María Ficticia", "Otro Nombre"]."""
    return [n.strip() for n in re.split(r"[,;\n]", texto or "") if n.strip()]


def preparar_hoja(cfg: Configuracion, nombre: str, contenido: bytes, *, enmascarar_datos: bool,
                  nombres: list[str] | None = None) -> HojaPreparada:
    """Lee la hoja de vida (ErrorDocumento si no se puede) y, si se pide, enmascara sus datos personales."""
    documento = leer_documento(nombre, contenido, **cfg.documentos)
    if not enmascarar_datos:
        return HojaPreparada(documento)
    resultado = enmascarar(documento.texto, nombres=nombres or [])
    documento.texto = resultado.texto
    return HojaPreparada(documento, True, resultado.equivalencias, resultado.conteo)


# =============================================================================
# Oferta
# =============================================================================
def leer_oferta_url(cfg: Configuracion, url: str) -> Oferta:
    """Una sola petición, iniciada por el usuario (nunca lanza excepciones: el estado dice qué pasó)."""
    return obtener_oferta(url, cfg.ofertas)


def oferta_para_analizar(texto: str, leida: Oferta | None) -> Oferta:
    """La oferta leída por URL si su texto no cambió; si no, el texto del campo como oferta pegada."""
    if leida is not None and leida.exito and (texto or "").strip() == (leida.texto or "").strip():
        return leida
    return desde_texto(texto)


# =============================================================================
# Análisis
# =============================================================================
@dataclass
class Ejecucion:
    """Un análisis hecho en la app, con lo necesario para mejorarlo, reanalizarlo y mostrar su detalle."""

    resultado: dict[str, Any]
    texto: str
    nombre_archivo: str
    perfil: str = DEMOSTRACION
    oferta: Oferta | None = None
    enmascarado: bool = False
    #: una por intento: etapa, mensajes enviados, respuesta, tokens y latencia (detalle técnico, RF13)
    llamadas: list[dict[str, Any]] = field(default_factory=list)
    analisis_id: int | None = None
    documento_id: int | None = None


def crear_analizador(cfg: Configuracion, perfil: str, *, referencia: Mes | None = None,
                     salida: Callable[[str], None] = print) -> Analizador:
    """«salida» recibe los avisos del cliente (esperas por el límite de uso de la API)."""
    modelo = None if perfil == DEMOSTRACION else ModeloLLM(cfg, perfil, salida=salida)
    return Analizador(cfg, modelo, referencia=referencia)


def _llamada(etapa: str, intento: Intento, mensajes: list[dict[str, str]]) -> dict[str, Any]:
    respuesta = intento.respuesta
    return {"etapa": etapa, "intento": intento.numero, "estado": intento.estado, "latencia_s": intento.latencia_s,
            "tokens_entrada": respuesta.tokens_entrada if respuesta else None,
            "tokens_salida": respuesta.tokens_salida if respuesta else None,
            "tokens_total": respuesta.tokens_total if respuesta else None,
            "mensajes": mensajes, "respuesta": respuesta.contenido if respuesta else None,
            "errores": list(intento.errores)}


def analizar(cfg: Configuracion, analizador: Analizador, documento: Documento, *, perfil: str,
             enmascarado: bool = False, oferta: Oferta | None = None, requisitos: RequisitosOferta | None = None,
             guardar: bool = True, con_mejoras: bool = True, analisis_previo_id: int | None = None,
             origen_id: int | None = None, al_avanzar: Avance | None = None) -> Ejecucion:
    """Analiza con o sin guardar en el historial. «al_avanzar» recibe cada intento de llamada al modelo.

    Lanza ErrorAnalisis si una etapa no obtiene una respuesta válida (el análisis no tiene puntaje).
    """
    llamadas: list[dict[str, Any]] = []

    def registrar(etapa: str, intento: Intento, mensajes: list[dict[str, str]], opciones: dict[str, Any]) -> None:
        llamadas.append(_llamada(etapa, intento, mensajes))
        if al_avanzar is not None:
            al_avanzar(etapa, intento)

    ejecucion = Ejecucion({}, documento.texto, documento.nombre, perfil, oferta, enmascarado, llamadas)
    if guardar:
        bd.inicializar(cfg.ruta_bd, cfg.ruta_respaldos)
        with bd.abrir(cfg.ruta_bd) as conexion:
            ejecucion.analisis_id, ejecucion.documento_id, ejecucion.resultado = analizar_y_guardar(
                analizador, conexion, documento, enmascarado=enmascarado, oferta=oferta, requisitos=requisitos,
                analisis_previo_id=analisis_previo_id, origen_id=origen_id, con_mejoras=con_mejoras, registrar=registrar)
    else:
        ejecucion.resultado = analizador.analizar(
            documento.texto, oferta=oferta.texto if oferta is not None else None, requisitos=requisitos,
            con_mejoras=con_mejoras, advertencias=documento.advertencias,
            registrar=registrar if analizador.modelo is not None else None)
    return ejecucion


def reanalizar(cfg: Configuracion, anterior: Ejecucion, texto: str, *, editado_a_mano: bool, guardar: bool = True,
               salida: Callable[[str], None] = print,
               al_avanzar: Avance | None = None) -> tuple[Ejecucion, dict[str, Any]]:
    """Reanaliza la versión mejorada con el mismo modelo, la misma fecha de referencia y los mismos
    requisitos de la oferta, sin pedir mejoras (RF11). Devuelve (ejecución, comparación)."""
    analizador = crear_analizador(cfg, anterior.perfil, referencia=Mes.desde_texto(anterior.resultado["fecha_referencia"]),
                                  salida=salida)
    compatibilidad = anterior.resultado["compatibilidad"]
    requisitos = RequisitosOferta.desde_dict(compatibilidad["oferta"]) if compatibilidad else None
    base = Path(anterior.nombre_archivo).stem
    documento = Documento(f"{base}_mejorada.txt", "editado", texto,
                          "editado_en_la_app" if editado_a_mano else "mejoras_aplicadas")
    despues = analizar(cfg, analizador, documento, perfil=anterior.perfil, enmascarado=anterior.enmascarado,
                       oferta=anterior.oferta, requisitos=requisitos, guardar=guardar, con_mejoras=False,
                       analisis_previo_id=anterior.analisis_id, origen_id=anterior.documento_id, al_avanzar=al_avanzar)
    return despues, comparar(anterior.resultado, despues.resultado)


# =============================================================================
# Historial
# =============================================================================
def historial(cfg: Configuracion, limite: int = 20) -> list[dict[str, Any]]:
    """Los últimos análisis del historial local, del más reciente al más antiguo."""
    if not cfg.ruta_bd.exists():
        return []
    with bd.abrir(cfg.ruta_bd, solo_lectura=True) as conexion:
        filas = conexion.execute(
            """SELECT a.analisis_id, a.fecha_inicio, d.nombre_archivo, a.modo, a.metodo, a.perfil, a.modelo,
                      a.puntuacion, a.compatibilidad, a.estado, a.analisis_previo_id, d.enmascarado, a.error
               FROM analisis a JOIN documentos d ON d.documento_id = a.documento_id
               ORDER BY a.analisis_id DESC LIMIT ?""", (limite,)).fetchall()
    return [dict(f) for f in filas]


def abrir_analisis(cfg: Configuracion, analisis_id: int) -> Ejecucion:
    """Reconstruye un análisis completo del historial (con su texto, su oferta y sus llamadas)."""
    with bd.abrir(cfg.ruta_bd, solo_lectura=True) as conexion:
        analisis = conexion.execute("SELECT * FROM analisis WHERE analisis_id = ?", (analisis_id,)).fetchone()
        if analisis is None or analisis["estado"] != "completo":
            raise ValueError(f"El análisis {analisis_id} no existe o no se completó (no tiene puntaje).")
        documento = conexion.execute("SELECT * FROM documentos WHERE documento_id = ?",
                                     (analisis["documento_id"],)).fetchone()
        fila_oferta = (conexion.execute("SELECT * FROM ofertas WHERE oferta_id = ?", (analisis["oferta_id"],)).fetchone()
                       if analisis["oferta_id"] else None)
        filas_llamadas = conexion.execute(
            """SELECT etapa, intento, estado, latencia_s, tokens_entrada, tokens_salida, tokens_total, mensajes_json,
                      contenido, errores_json FROM llamadas_llm WHERE analisis_id = ? ORDER BY llamada_id""",
            (analisis_id,)).fetchall()
    oferta = None
    if fila_oferta is not None:
        oferta = Oferta(fila_oferta["estado"], fila_oferta["origen"], fila_oferta["mensaje"], metodo=fila_oferta["metodo"],
                        url=fila_oferta["url"], url_final=fila_oferta["url_final"], dominio=fila_oferta["dominio"],
                        portal=fila_oferta["portal"], titulo=fila_oferta["titulo"], empresa=fila_oferta["empresa"],
                        ubicacion=fila_oferta["ubicacion"], texto=fila_oferta["texto"], fecha=fila_oferta["fecha"])
    llamadas = [{"etapa": f["etapa"], "intento": f["intento"], "estado": f["estado"], "latencia_s": f["latencia_s"],
                 "tokens_entrada": f["tokens_entrada"], "tokens_salida": f["tokens_salida"],
                 "tokens_total": f["tokens_total"], "mensajes": json.loads(f["mensajes_json"]),
                 "respuesta": f["contenido"], "errores": json.loads(f["errores_json"]) if f["errores_json"] else []}
                for f in filas_llamadas]
    return Ejecucion(json.loads(analisis["resultado_json"]), documento["texto"], documento["nombre_archivo"],
                     analisis["perfil"] or DEMOSTRACION, oferta, bool(documento["enmascarado"]), llamadas,
                     analisis["analisis_id"], analisis["documento_id"])
