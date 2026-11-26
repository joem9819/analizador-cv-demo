"""Comando «prueba-conexion»: comprueba la clave y el modelo, negocia los parámetros que acepta la API
y los guarda en datos/congelados/<perfil>.json.

Usa una hoja de vida FICTICIA (no corresponde a ninguna persona) y un esquema con las mismas
construcciones que los esquemas de la app: objetos anidados, listas, enum, null, mínimo y máximo.
Las sondas no se guardan en el historial; el reporte queda en resultados/<perfil>/prueba_conexion/.
"""

from __future__ import annotations

import shutil
import time
from typing import Any, Callable

from analizador_cv import __version__
from analizador_cv.configuracion import Configuracion, leer_clave
from analizador_cv.llm.cliente_api import ClienteCompatibleOpenAI
from analizador_cv.llm.negociacion import (
    NOTA_ACEPTADOS,
    huella_congelado,
    huella_perfil,
    modo_de_cuerpo,
    negociar,
    ruta_congelado,
)
from analizador_cv.llm.salida import interpretar
from analizador_cv.utilidades import ahora_iso, escribir_json, formatear_duracion, json_legible, leer_json, marca_archivo

Salida = Callable[[str], None]

NOMBRE_ESQUEMA_PRUEBA = "prueba_hoja_de_vida"

ESQUEMA_PRUEBA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cargo_actual": {"type": "string"},
        "habilidades": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "experiencias": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cargo": {"type": "string"},
                    "empresa": {"type": "string"},
                    "inicio": {"type": "string"},
                    "fin": {"type": ["string", "null"]},
                },
                "required": ["cargo", "empresa", "inicio", "fin"],
                "additionalProperties": False,
            },
        },
        "anios_experiencia": {"type": "integer", "minimum": 0, "maximum": 60},
        "nivel": {"type": "string", "enum": ["junior", "semisenior", "senior"]},
    },
    "required": ["cargo_actual", "habilidades", "experiencias", "anios_experiencia", "nivel"],
    "additionalProperties": False,
}

HOJA_FICTICIA = """HOJA DE VIDA FICTICIA (prueba de conexión; no corresponde a ninguna persona real)
Perfil: desarrolladora de software júnior.
Experiencia:
- Desarrolladora web, Empresa Ficticia S.A.S., de 2024-02 a la fecha. Construyó una API REST con Python y FastAPI.
- Practicante de soporte TI, Compañía Inventada Ltda., de 2023-01 a 2023-12. Atendió solicitudes de mesa de ayuda.
Habilidades: Python, FastAPI, SQL, Git, soporte técnico."""

MENSAJES_PRUEBA = [
    {"role": "system", "content": (
        "Extraes información de hojas de vida. Usa solo lo que dice el texto; no inventes datos. "
        "Las fechas van en formato AAAA-MM y «fin» es null si el cargo sigue vigente. "
        "Responde únicamente con un objeto JSON que cumpla este esquema:\n" + json_legible(ESQUEMA_PRUEBA))},
    {"role": "user", "content": HOJA_FICTICIA},
]


def _guardar(cfg: Configuracion, nombre_perfil: str, nuevo: dict[str, Any], salida: Salida) -> tuple[str, str | None]:
    """Escribe el archivo de parámetros negociados. Devuelve (estado, ruta del respaldo del anterior)."""
    ruta = ruta_congelado(cfg, nombre_perfil)
    if not ruta.exists():
        escribir_json(ruta, nuevo)
        return "guardado", None
    previo = leer_json(ruta)
    if previo.get("huella") == nuevo["huella"]:
        salida(f"   Los parámetros aceptados coinciden con los de la prueba del {previo.get('fecha')}: el archivo no cambia.")
        return "sin_cambios", None
    respaldo = ruta.parent / "respaldos" / f"{ruta.stem}_{marca_archivo()}.json"
    respaldo.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ruta, respaldo)
    escribir_json(ruta, nuevo)
    return "actualizado", str(respaldo)


def _mostrar_respuesta(respuesta: Any, estado: str, errores: list[str], salida: Salida) -> None:
    salida(f"   Modelo devuelto: «{respuesta.modelo_respuesta or '-'}» | motivo de fin: {respuesta.motivo_fin} | "
           f"latencia: {formatear_duracion(respuesta.latencia_s)} | tokens entrada/salida/razonamiento: "
           f"{respuesta.tokens_entrada}/{respuesta.tokens_salida}/{respuesta.tokens_razonamiento}")
    if estado == "valida":
        salida("   JSON válido según el esquema de prueba.")
    else:
        salida(f"   ADVERTENCIA: la salida de la prueba no es válida ({estado}: {'; '.join(errores)[:300]}). "
               "Los parámetros se guardan igual; los reintentos de la app cubren fallos ocasionales.")


def prueba_conexion(cfg: Configuracion, nombre_perfil: str | None = None, *, salida: Salida = print,
                    dormir: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Comprueba la conexión del perfil y guarda los parámetros que acepta la API."""
    perfil = cfg.perfil(nombre_perfil)
    nombre = perfil["nombre"]
    semilla = int(cfg.generacion["semilla"])
    salida(f"Perfil: {nombre} | proveedor: {perfil.get('proveedor')} | modelo: {perfil['modelo']} | URL: {perfil['url']}")
    if perfil.get("aviso_privacidad"):
        salida(f"AVISO: {perfil['aviso_privacidad']}")

    clave = leer_clave(perfil)
    salida(f"Clave: la variable {perfil['variable_clave']} está definida (no se muestra ni se guarda).")
    cliente = ClienteCompatibleOpenAI(perfil, clave, avisar=salida, dormir=dormir)
    info = cliente.informacion()
    salida(f"1. Autenticación correcta: {perfil.get('proveedor')} ofrece el modelo «{perfil['modelo']}».")
    salida("2. Sondas con la hoja de vida ficticia:")
    negociacion = negociar(cliente, perfil, MENSAJES_PRUEBA, ESQUEMA_PRUEBA, nombre=NOMBRE_ESQUEMA_PRUEBA,
                           semilla=semilla, salida=salida)
    respuesta = negociacion.respuesta
    if respuesta is not None:
        estado, _, errores = interpretar(respuesta, ESQUEMA_PRUEBA)
        salida("3. Respuesta:")
        _mostrar_respuesta(respuesta, estado, errores, salida)
        if respuesta.cabeceras_limite:
            salida("   Cupo informado por la API: " + ", ".join(f"{k} = {v}" for k, v in sorted(respuesta.cabeceras_limite.items())))
    else:
        estado, errores = "invalida", [negociacion.salida_rechazada or "sin respuesta"]
        salida("3. ADVERTENCIA: la API rechazó la salida del modelo en la prueba; los reintentos de la app cubren fallos ocasionales.")

    nuevo: dict[str, Any] = {
        "perfil": nombre,
        "proveedor": perfil.get("proveedor"),
        "url": perfil["url"].rstrip("/"),
        "modelo_solicitado": perfil["modelo"],
        "modelo_devuelto": respuesta.modelo_respuesta if respuesta is not None else None,
        "fecha": ahora_iso(),
        "version_software": __version__,
        "ajustes": negociacion.ajustes,
        "enviar_semilla": negociacion.enviar_semilla,
        "modo_salida": modo_de_cuerpo(negociacion.cuerpo),
        "parametros_descartados": negociacion.descartados,
        "parametros_enviados": sorted(negociacion.cuerpo) + (["seed"] if negociacion.enviar_semilla else []),
        "modelo_api": info.detalles.get("modelo_api"),
        "huella_perfil": huella_perfil(perfil),
        "nota": NOTA_ACEPTADOS,
    }
    nuevo["huella"] = huella_congelado(nuevo)
    reporte = {
        **{k: nuevo[k] for k in ("perfil", "proveedor", "url", "modelo_solicitado", "modelo_devuelto", "fecha",
                                 "ajustes", "enviar_semilla", "modo_salida", "parametros_descartados")},
        "sondas": negociacion.sondas,
        "salida_valida": estado == "valida",
        "errores_validacion": errores,
        "contenido": respuesta.contenido if respuesta is not None else None,
        "motivo_fin": respuesta.motivo_fin if respuesta is not None else None,
        "latencia_s": respuesta.latencia_s if respuesta is not None else None,
        "tokens": {"entrada": respuesta.tokens_entrada, "salida": respuesta.tokens_salida,
                   "razonamiento": respuesta.tokens_razonamiento, "total": respuesta.tokens_total}
        if respuesta is not None else None,
        "esperas_429": respuesta.esperas_429 if respuesta is not None else 0,
        "cabeceras_limite": respuesta.cabeceras_limite if respuesta is not None else None,
        "nota": NOTA_ACEPTADOS,
    }
    ruta_reporte = cfg.ruta_resultados / nombre / "prueba_conexion" / f"prueba_conexion_{marca_archivo()}.json"
    escribir_json(ruta_reporte, reporte)
    estado_archivo, respaldo = _guardar(cfg, nombre, nuevo, salida)
    if estado_archivo != "sin_cambios":
        salida(f"4. Parámetros aceptados ({estado_archivo}) en: {ruta_congelado(cfg, nombre)}")
        if respaldo:
            salida(f"   Respaldo del archivo anterior: {respaldo}")
    salida("   Enviados: " + ", ".join(nuevo["parametros_enviados"])
           + (" | descartados: " + ", ".join(d["parametro"] for d in negociacion.descartados) if negociacion.descartados else ""))
    salida(f"Reporte de la prueba: {ruta_reporte}")
    return {**reporte, "estado": estado_archivo, "ruta_congelado": str(ruta_congelado(cfg, nombre)),
            "ruta_reporte": str(ruta_reporte)}
