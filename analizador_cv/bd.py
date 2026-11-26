"""Historial local en SQLite (RF13 y RNF04): documentos, ofertas, análisis y llamadas al modelo.

Todo queda en este computador. Cada análisis registra la huella de la configuración del modelo y
la versión del programa; cada llamada al modelo, sus mensajes, opciones, respuesta, tokens y
latencia. Si se abre una base creada con otro esquema, se respalda en datos/respaldos/ y se crea
una nueva.
"""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from analizador_cv import __version__
from analizador_cv.documentos import Documento
from analizador_cv.llm.salida import Intento
from analizador_cv.ofertas import Oferta
from analizador_cv.utilidades import ahora_iso, hash_objeto, json_canonico, marca_archivo

VERSION_ESQUEMA = "1"

ESTADOS_LLAMADA = ("valida", "invalida", "truncada", "error_http", "error_tiempo", "error_conexion", "error_cupo",
                   "error_autenticacion", "error_modelo", "error_servicio", "error_solicitud")

ESQUEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS metadatos (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documentos (
    documento_id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_archivo TEXT NOT NULL,
    formato TEXT NOT NULL CHECK (formato IN ('pdf', 'docx', 'txt', 'editado')),
    origen_id INTEGER REFERENCES documentos(documento_id),
    texto TEXT NOT NULL,
    hash_texto TEXT NOT NULL,
    paginas INTEGER,
    caracteres INTEGER NOT NULL,
    metodo_extraccion TEXT NOT NULL,
    advertencias_json TEXT,
    enmascarado INTEGER NOT NULL DEFAULT 0 CHECK (enmascarado IN (0, 1)),
    fecha TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ofertas (
    oferta_id INTEGER PRIMARY KEY AUTOINCREMENT,
    origen TEXT NOT NULL CHECK (origen IN ('url', 'html_guardado', 'texto_pegado')),
    estado TEXT NOT NULL,
    metodo TEXT,
    url TEXT,
    url_final TEXT,
    dominio TEXT,
    portal TEXT,
    titulo TEXT,
    empresa TEXT,
    ubicacion TEXT,
    texto TEXT,
    texto_guardado INTEGER NOT NULL CHECK (texto_guardado IN (0, 1)),
    hash_texto TEXT,
    caracteres INTEGER NOT NULL,
    http_estado INTEGER,
    redirecciones INTEGER NOT NULL DEFAULT 0,
    mensaje TEXT NOT NULL,
    fecha TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analisis (
    analisis_id INTEGER PRIMARY KEY AUTOINCREMENT,
    documento_id INTEGER NOT NULL REFERENCES documentos(documento_id),
    oferta_id INTEGER REFERENCES ofertas(oferta_id),
    analisis_previo_id INTEGER REFERENCES analisis(analisis_id),
    modo TEXT NOT NULL CHECK (modo IN ('general', 'especifico')),
    metodo TEXT NOT NULL CHECK (metodo IN ('llm', 'linea_base')),
    perfil TEXT,
    modelo TEXT,
    huella_configuracion TEXT NOT NULL,
    version_software TEXT NOT NULL,
    estado TEXT NOT NULL CHECK (estado IN ('en_curso', 'completo', 'fallido')),
    puntuacion REAL CHECK (puntuacion IS NULL OR (puntuacion >= 0 AND puntuacion <= 100)),
    compatibilidad REAL CHECK (compatibilidad IS NULL OR (compatibilidad >= 0 AND compatibilidad <= 100)),
    resultado_json TEXT,
    error TEXT,
    fecha_inicio TEXT NOT NULL,
    fecha_fin TEXT,
    CHECK ((modo = 'especifico') = (oferta_id IS NOT NULL)),
    CHECK ((metodo = 'llm') = (perfil IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS llamadas_llm (
    llamada_id INTEGER PRIMARY KEY AUTOINCREMENT,
    analisis_id INTEGER REFERENCES analisis(analisis_id),
    etapa TEXT NOT NULL,
    perfil TEXT NOT NULL,
    proveedor TEXT,
    modelo TEXT NOT NULL,
    intento INTEGER NOT NULL CHECK (intento >= 1),
    estado TEXT NOT NULL CHECK (estado IN ({", ".join(f"'{e}'" for e in ESTADOS_LLAMADA)})),
    hash_entrada TEXT NOT NULL,
    mensajes_json TEXT NOT NULL,
    opciones_json TEXT NOT NULL,
    respuesta_cruda TEXT,
    contenido TEXT,
    errores_json TEXT,
    motivo_fin TEXT,
    modelo_respuesta TEXT,
    tokens_entrada INTEGER,
    tokens_salida INTEGER,
    tokens_razonamiento INTEGER,
    tokens_total INTEGER,
    latencia_s REAL,
    esperas_429 INTEGER NOT NULL DEFAULT 0,
    segundos_espera_429 REAL NOT NULL DEFAULT 0,
    fecha_inicio TEXT NOT NULL,
    fecha_fin TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analisis_documento ON analisis(documento_id);
CREATE INDEX IF NOT EXISTS idx_llamadas_analisis ON llamadas_llm(analisis_id);
"""


class ErrorBaseDatos(Exception):
    """Error al abrir o preparar la base de datos."""


def conectar(ruta: Path, solo_lectura: bool = False) -> sqlite3.Connection:
    """Abre la base SQLite con claves foráneas activadas y filas accesibles por nombre."""
    ruta = Path(ruta)
    if solo_lectura:
        if not ruta.exists():
            raise ErrorBaseDatos(f"No existe la base de datos: {ruta}.")
        conexion = sqlite3.connect(f"{ruta.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    else:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        conexion = sqlite3.connect(str(ruta), timeout=30)
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys = ON")
    conexion.execute("PRAGMA busy_timeout = 30000")
    return conexion


@contextmanager
def abrir(ruta: Path, solo_lectura: bool = False) -> Iterator[sqlite3.Connection]:
    """Abre la base, confirma los cambios al salir y SIEMPRE cierra la conexión.

    Cerrar es importante en Windows: una conexión abierta bloquea el archivo.
    """
    conexion = conectar(ruta, solo_lectura=solo_lectura)
    try:
        yield conexion
        if not solo_lectura:
            conexion.commit()
    except BaseException:
        if not solo_lectura:
            conexion.rollback()
        raise
    finally:
        conexion.close()


def leer_metadato(conexion: sqlite3.Connection, clave: str) -> str | None:
    try:
        fila = conexion.execute("SELECT valor FROM metadatos WHERE clave = ?", (clave,)).fetchone()
    except sqlite3.OperationalError:  # base sin la tabla de metadatos
        return None
    return fila["valor"] if fila else None


def respaldar(ruta: Path, carpeta: Path, motivo: str = "respaldo") -> Path:
    """Copia la base con la API de respaldo de SQLite (segura aunque haya otra conexión abierta)."""
    destino = Path(carpeta) / f"{Path(ruta).stem}_{motivo}_{marca_archivo()}.sqlite"
    destino.parent.mkdir(parents=True, exist_ok=True)
    origen = sqlite3.connect(str(ruta))
    copia = sqlite3.connect(str(destino))
    try:
        origen.backup(copia)
    except sqlite3.DatabaseError:  # el archivo no es una base SQLite válida: se copia tal cual
        copia.close()
        shutil.copy2(ruta, destino)
    finally:
        copia.close()
        origen.close()
    return destino


def inicializar(ruta: Path, carpeta_respaldos: Path) -> Path | None:
    """Crea la base si no existe. Si existe con otro esquema, la respalda y la reemplaza.

    Devuelve la ruta del respaldo, si se hizo.
    """
    ruta = Path(ruta)
    respaldo = None
    if ruta.exists():
        try:
            with abrir(ruta, solo_lectura=True) as conexion:
                version = leer_metadato(conexion, "version_esquema")
        except sqlite3.DatabaseError:
            version = None
        if version == VERSION_ESQUEMA:
            return None
        respaldo = respaldar(ruta, carpeta_respaldos, f"esquema_{version or 'desconocido'}")
        ruta.unlink()
    with abrir(ruta) as conexion:
        conexion.executescript(ESQUEMA_SQL)
        conexion.executemany("INSERT INTO metadatos (clave, valor) VALUES (?, ?)",
                             [("version_esquema", VERSION_ESQUEMA), ("version_software", __version__),
                              ("fecha_creacion", ahora_iso())])
    return respaldo


def _insertar(conexion: sqlite3.Connection, tabla: str, fila: dict[str, Any]) -> int:
    columnas = ", ".join(fila)
    marcadores = ", ".join("?" for _ in fila)
    cursor = conexion.execute(f"INSERT INTO {tabla} ({columnas}) VALUES ({marcadores})", tuple(fila.values()))
    return int(cursor.lastrowid)


# =============================================================================
# Registros
# =============================================================================
def guardar_documento(conexion: sqlite3.Connection, documento: Documento, *, enmascarado: bool = False,
                      origen_id: int | None = None) -> int:
    """Guarda el texto que se analiza (si se enmascaró, el texto con marcas)."""
    return _insertar(conexion, "documentos", {
        "nombre_archivo": documento.nombre, "formato": documento.formato, "origen_id": origen_id,
        "texto": documento.texto, "hash_texto": documento.hash_texto, "paginas": documento.paginas,
        "caracteres": documento.caracteres, "metodo_extraccion": documento.metodo,
        "advertencias_json": json_canonico(documento.advertencias) if documento.advertencias else None,
        "enmascarado": 1 if enmascarado else 0, "fecha": ahora_iso(),
    })


def guardar_oferta(conexion: sqlite3.Connection, oferta: Oferta, *, guardar_texto: bool = True) -> int:
    """Guarda el resultado de obtener una oferta. En la evaluación de URL no se guarda el texto de terceros."""
    return _insertar(conexion, "ofertas", {
        "origen": oferta.origen, "estado": oferta.estado, "metodo": oferta.metodo, "url": oferta.url,
        "url_final": oferta.url_final, "dominio": oferta.dominio, "portal": oferta.portal, "titulo": oferta.titulo,
        "empresa": oferta.empresa, "ubicacion": oferta.ubicacion, "texto": oferta.texto if guardar_texto else None,
        "texto_guardado": 1 if guardar_texto and oferta.texto else 0, "hash_texto": oferta.hash_texto,
        "caracteres": oferta.caracteres, "http_estado": oferta.http_estado, "redirecciones": oferta.redirecciones,
        "mensaje": oferta.mensaje, "fecha": oferta.fecha,
    })


def crear_analisis(conexion: sqlite3.Connection, *, documento_id: int, modo: str, metodo: str,
                   huella_configuracion: str, oferta_id: int | None = None, perfil: str | None = None,
                   modelo: str | None = None, analisis_previo_id: int | None = None) -> int:
    return _insertar(conexion, "analisis", {
        "documento_id": documento_id, "oferta_id": oferta_id, "analisis_previo_id": analisis_previo_id, "modo": modo,
        "metodo": metodo, "perfil": perfil, "modelo": modelo, "huella_configuracion": huella_configuracion,
        "version_software": __version__, "estado": "en_curso", "fecha_inicio": ahora_iso(),
    })


def finalizar_analisis(conexion: sqlite3.Connection, analisis_id: int, *, resultado: dict[str, Any] | None = None,
                       puntuacion: float | None = None, compatibilidad: float | None = None,
                       error: str | None = None) -> None:
    """Cierra el análisis como «completo» o, si hay error, como «fallido» (nunca con puntaje)."""
    if error is not None:
        puntuacion = compatibilidad = None
    conexion.execute(
        "UPDATE analisis SET estado = ?, puntuacion = ?, compatibilidad = ?, resultado_json = ?, error = ?, fecha_fin = ? "
        "WHERE analisis_id = ?",
        ("fallido" if error is not None else "completo", puntuacion, compatibilidad,
         json_canonico(resultado) if resultado is not None else None, error, ahora_iso(), analisis_id))


def registrar_llamada(conexion: sqlite3.Connection, intento: Intento, mensajes: list[dict[str, str]],
                      opciones: dict[str, Any], *, etapa: str, perfil: dict[str, Any],
                      analisis_id: int | None = None) -> int:
    """Guarda un intento de llamada al modelo (válido o no). Se usa como «registrar» de ModeloLLM.pedir."""
    respuesta = intento.respuesta
    return _insertar(conexion, "llamadas_llm", {
        "analisis_id": analisis_id, "etapa": etapa, "perfil": perfil["nombre"], "proveedor": perfil.get("proveedor"),
        "modelo": perfil["modelo"], "intento": intento.numero, "estado": intento.estado,
        "hash_entrada": hash_objeto({"mensajes": mensajes, "opciones": opciones, "modelo": perfil["modelo"]}),
        "mensajes_json": json_canonico(mensajes), "opciones_json": json_canonico(opciones),
        "respuesta_cruda": respuesta.cuerpo_crudo if respuesta else None,
        "contenido": respuesta.contenido if respuesta else None,
        "errores_json": json_canonico(intento.errores) if intento.errores else None,
        "motivo_fin": respuesta.motivo_fin if respuesta else None,
        "modelo_respuesta": respuesta.modelo_respuesta if respuesta else None,
        "tokens_entrada": respuesta.tokens_entrada if respuesta else None,
        "tokens_salida": respuesta.tokens_salida if respuesta else None,
        "tokens_razonamiento": respuesta.tokens_razonamiento if respuesta else None,
        "tokens_total": respuesta.tokens_total if respuesta else None,
        "latencia_s": intento.latencia_s,
        "esperas_429": respuesta.esperas_429 if respuesta else 0,
        "segundos_espera_429": respuesta.segundos_espera_429 if respuesta else 0.0,
        "fecha_inicio": intento.fecha_inicio, "fecha_fin": intento.fecha_fin,
    })
