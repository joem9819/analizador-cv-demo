"""Parámetros que acepta cada API y opciones de cada solicitud.

«prueba-conexion» envía una solicitud de prueba con todos los parámetros del perfil y un esquema
con las mismas construcciones que usan los esquemas de la app (objetos anidados, listas, enum,
null, mínimos y máximos). Si la API la rechaza (HTTP 400), se identifica el parámetro en el mensaje
de error y se aplica un único ajuste documentado (quitar un parámetro opcional, usar max_tokens,
quitar una palabra del esquema, desactivar «strict» o usar json_object), hasta MAX_SONDAS veces.
Los ajustes aceptados se guardan en datos/congelados/<perfil>.json.

Después, opciones_para arma el cuerpo de cada solicitud aplicando esos ajustes al esquema de la
etapa (extracción, evaluación o mejoras): el esquema va en «response_format».
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from analizador_cv.configuracion import Configuracion, ErrorConfiguracion
from analizador_cv.llm.backends import ErrorBackend, RespuestaBackend
from analizador_cv.llm.cliente_api import extraer_mensaje_error
from analizador_cv.utilidades import hash_objeto, leer_json

MODOS_SALIDA = ("json_schema_estricto", "json_schema", "json_object")
PALABRAS_ESQUEMA = ("minimum", "maximum", "additionalProperties", "minItems", "maxItems", "minLength", "maxLength")
MAX_SONDAS = 6

NOTA_ACEPTADOS = (
    "«Aceptado» significa que la API no rechazó el parámetro. Una API puede ignorar en silencio un parámetro "
    "que no usa (por ejemplo, «seed»); por eso no se asume que la semilla haga reproducibles las respuestas. "
    "La clave de API nunca se guarda en este archivo."
)

Salida = Callable[[str], None]


class ErrorCongelado(ErrorConfiguracion):
    """Faltan los parámetros negociados de un perfil o ya no corresponden a su configuración."""


# =============================================================================
# Cuerpo de las solicitudes
# =============================================================================
def formato_respuesta(modo: str, esquema: dict[str, Any], nombre: str) -> dict[str, Any]:
    """Valor de «response_format» para cada modo de salida estructurada."""
    if modo == "json_schema_estricto":
        return {"type": "json_schema", "json_schema": {"name": nombre, "strict": True, "schema": esquema}}
    if modo == "json_schema":
        return {"type": "json_schema", "json_schema": {"name": nombre, "strict": False, "schema": esquema}}
    if modo == "json_object":
        return {"type": "json_object"}
    raise ErrorConfiguracion(f"Modo de salida estructurada desconocido: {modo!r} (use uno de {', '.join(MODOS_SALIDA)}).")


def modo_de_cuerpo(cuerpo: dict[str, Any]) -> str:
    formato = cuerpo.get("response_format") or {}
    if formato.get("type") == "json_object":
        return "json_object"
    if formato.get("type") == "json_schema":
        return "json_schema_estricto" if formato.get("json_schema", {}).get("strict") else "json_schema"
    return "sin_formato"


def cuerpo_configurado(perfil: dict[str, Any], esquema: dict[str, Any], nombre: str) -> dict[str, Any]:
    """Cuerpo según configuracion.json, antes de los ajustes negociados (solo perfiles de API)."""
    cuerpo = copy.deepcopy(perfil.get("parametros") or {})
    if perfil.get("temperature") is not None:  # null en el perfil = no se envía
        cuerpo = {"temperature": perfil["temperature"], **cuerpo}
    cuerpo["response_format"] = formato_respuesta(perfil["salida_estructurada"], copy.deepcopy(esquema), nombre)
    return cuerpo


def huella_perfil(perfil: dict[str, Any]) -> str:
    """Identidad de lo que el perfil pide enviar: si cambia, hay que negociar de nuevo."""
    claves = ("tipo", "url", "modelo", "parametros", "temperature", "salida_estructurada", "enviar_semilla",
              "parametros_opcionales")
    return hash_objeto({k: perfil.get(k) for k in claves})


# =============================================================================
# Diagnóstico de rechazos
# =============================================================================
def _menciona(texto: str, palabra: str) -> bool:
    return re.search(rf"(?<![a-z0-9_]){re.escape(palabra.lower())}(?![a-z0-9_])", texto) is not None


def _contiene_palabra(esquema: Any, palabra: str) -> bool:
    if isinstance(esquema, dict):
        return palabra in esquema or any(_contiene_palabra(v, palabra) for v in esquema.values())
    if isinstance(esquema, list):
        return any(_contiene_palabra(v, palabra) for v in esquema)
    return False


def _quitar_palabra(esquema: Any, palabra: str) -> Any:
    if isinstance(esquema, dict):
        return {k: _quitar_palabra(v, palabra) for k, v in esquema.items() if k != palabra}
    if isinstance(esquema, list):
        return [_quitar_palabra(v, palabra) for v in esquema]
    return esquema


def diagnosticar_rechazo(texto_error: str, cuerpo: dict[str, Any], enviar_semilla: bool,
                         opcionales: list[str]) -> tuple[str, str] | None:
    """Identifica qué ajustar a partir del mensaje de error de la API. Devuelve (ajuste, parámetro) o None."""
    texto = (texto_error or "").lower()
    if enviar_semilla and "seed" in opcionales and _menciona(texto, "seed"):
        return ("quitar_semilla", "seed")
    for parametro in opcionales:
        if parametro != "seed" and parametro in cuerpo and _menciona(texto, parametro):
            return ("quitar_parametro", parametro)
    if "max_completion_tokens" in cuerpo and _menciona(texto, "max_completion_tokens"):
        return ("usar_max_tokens", "max_completion_tokens")
    formato = cuerpo.get("response_format") or {}
    if formato.get("type") == "json_schema":
        esquema = formato.get("json_schema", {}).get("schema")
        for palabra in PALABRAS_ESQUEMA:
            if _menciona(texto, palabra) and _contiene_palabra(esquema, palabra):
                return ("quitar_palabra_esquema", palabra)
        if formato.get("json_schema", {}).get("strict") and _menciona(texto, "strict"):
            return ("desactivar_strict", "strict")
        if _menciona(texto, "json_schema") or _menciona(texto, "response_format"):
            return ("usar_json_object", "response_format")
    return None


def aplicar_ajuste(ajuste: tuple[str, str] | list[str], cuerpo: dict[str, Any],
                   enviar_semilla: bool) -> tuple[dict[str, Any], bool, str]:
    """Aplica un ajuste y devuelve (cuerpo nuevo, enviar_semilla, descripción)."""
    tipo, parametro = ajuste
    cuerpo = copy.deepcopy(cuerpo)
    if tipo == "quitar_semilla":
        return cuerpo, False, "no se envía «seed»"
    if tipo == "quitar_parametro":
        cuerpo.pop(parametro, None)
        return cuerpo, enviar_semilla, f"no se envía «{parametro}»"
    if tipo == "usar_max_tokens":
        if "max_completion_tokens" in cuerpo:
            cuerpo["max_tokens"] = cuerpo.pop("max_completion_tokens")
        return cuerpo, enviar_semilla, "se usa «max_tokens» en lugar de «max_completion_tokens»"
    formato = cuerpo["response_format"]
    if tipo == "quitar_palabra_esquema":
        if formato.get("type") == "json_schema":
            formato["json_schema"]["schema"] = _quitar_palabra(formato["json_schema"]["schema"], parametro)
        return cuerpo, enviar_semilla, (f"se quita «{parametro}» del esquema enviado (el programa sigue validando "
                                        "con el esquema completo)")
    if tipo == "desactivar_strict":
        if formato.get("type") == "json_schema":
            formato["json_schema"]["strict"] = False
        return cuerpo, enviar_semilla, "salida estructurada sin «strict» (json_schema en modo de mejor esfuerzo)"
    if tipo == "usar_json_object":
        cuerpo["response_format"] = {"type": "json_object"}
        return cuerpo, enviar_semilla, "salida en modo «json_object» (el esquema solo va en las instrucciones)"
    raise ValueError(f"Ajuste desconocido: {tipo}")


# =============================================================================
# Parámetros negociados (datos/congelados/<perfil>.json)
# =============================================================================
def ruta_congelado(cfg: Configuracion, nombre_perfil: str) -> Path:
    return cfg.ruta_congelados / f"{nombre_perfil}.json"


def huella_congelado(datos: dict[str, Any]) -> str:
    return hash_objeto({k: datos.get(k) for k in ("huella_perfil", "ajustes", "enviar_semilla")})


def cargar_congelado(cfg: Configuracion, perfil: dict[str, Any]) -> dict[str, Any]:
    """Lee los parámetros negociados y comprueba que sigan correspondiendo al perfil configurado."""
    nombre = perfil["nombre"]
    ruta = ruta_congelado(cfg, nombre)
    comando = f"python -m analizador_cv prueba-conexion --perfil {nombre}"
    if not ruta.exists():
        raise ErrorCongelado(f"Falta la prueba de conexión del perfil «{nombre}» ({ruta}). Ejecute primero: {comando}")
    datos = leer_json(ruta)
    if datos.get("huella") != huella_congelado(datos):
        raise ErrorCongelado(f"El archivo {ruta} fue modificado a mano. Ejecute de nuevo: {comando}")
    if datos.get("huella_perfil") != huella_perfil(perfil):
        raise ErrorCongelado(
            f"La configuración del perfil «{nombre}» cambió desde la prueba de conexión del {datos.get('fecha')}. "
            f"Ejecute de nuevo: {comando}"
        )
    return datos


def opciones_para(perfil: dict[str, Any], esquema: dict[str, Any], congelado: dict[str, Any] | None = None, *,
                  nombre: str = "respuesta", semilla: int | None = None,
                  max_tokens: int | None = None) -> dict[str, Any]:
    """Opciones de una solicitud (todo lo que va en el cuerpo además de model y messages).

    En modo json_object el esquema no viaja en la solicitud: las instrucciones deben incluirlo.
    """
    if congelado is None:
        raise ErrorCongelado(f"Falta la prueba de conexión del perfil «{perfil['nombre']}». "
                             f"Ejecute primero: python -m analizador_cv prueba-conexion --perfil {perfil['nombre']}")
    cuerpo = cuerpo_configurado(perfil, esquema, nombre)
    enviar_semilla = bool(perfil.get("enviar_semilla", True))
    for ajuste in congelado.get("ajustes") or []:
        cuerpo, enviar_semilla, _ = aplicar_ajuste(ajuste, cuerpo, enviar_semilla)
    if max_tokens:
        clave = "max_tokens" if "max_tokens" in cuerpo else "max_completion_tokens"
        cuerpo[clave] = int(max_tokens)
    if enviar_semilla and semilla is not None:
        cuerpo["seed"] = int(semilla)
    return cuerpo


# =============================================================================
# Sondas
# =============================================================================
@dataclass
class Negociacion:
    respuesta: RespuestaBackend | None
    cuerpo: dict[str, Any]
    enviar_semilla: bool
    ajustes: list[list[str]] = field(default_factory=list)
    descartados: list[dict[str, Any]] = field(default_factory=list)
    sondas: list[dict[str, Any]] = field(default_factory=list)
    salida_rechazada: str | None = None


class NegociacionFallida(Exception):
    """La API rechazó la solicitud y no se pudo identificar qué ajustar."""


def negociar(cliente: Any, perfil: dict[str, Any], mensajes: list[dict[str, str]], esquema: dict[str, Any], *,
             nombre: str, semilla: int, salida: Salida = print) -> Negociacion:
    """Envía la solicitud de prueba y ajusta los parámetros rechazados, uno por sonda."""
    cuerpo = cuerpo_configurado(perfil, esquema, nombre)
    enviar_semilla = bool(perfil.get("enviar_semilla", True))
    opcionales = list(perfil.get("parametros_opcionales") or [])
    resultado = Negociacion(None, cuerpo, enviar_semilla)
    vistos: set[tuple[str, str]] = set()
    for numero in range(1, MAX_SONDAS + 1):
        opciones = copy.deepcopy(resultado.cuerpo)
        if resultado.enviar_semilla:
            opciones["seed"] = int(semilla)
        try:
            resultado.respuesta = cliente.chat(mensajes, opciones)
            resultado.sondas.append({"sonda": numero, "resultado": "aceptada", "parametros": sorted(opciones)})
            salida(f"   Sonda {numero}: solicitud aceptada.")
            return resultado
        except ErrorBackend as error:
            if error.tipo == "http" and "esquema" in error.mensaje:
                resultado.salida_rechazada = error.mensaje
                resultado.sondas.append({"sonda": numero, "resultado": "aceptada; salida rechazada por el esquema",
                                         "mensaje_api": extraer_mensaje_error(error.cuerpo)})
                salida(f"   Sonda {numero}: la API aceptó los parámetros, pero rechazó la salida del modelo: {error.mensaje}")
                return resultado
            if error.tipo != "solicitud_rechazada":
                raise
            texto = error.cuerpo or error.mensaje
            mensaje_api = extraer_mensaje_error(texto)
            ajuste = diagnosticar_rechazo(texto, resultado.cuerpo, resultado.enviar_semilla, opcionales)
            resultado.sondas.append({"sonda": numero, "resultado": "rechazada", "mensaje_api": mensaje_api,
                                     "ajuste": list(ajuste) if ajuste else None})
            if ajuste is None or ajuste in vistos:
                raise NegociacionFallida(
                    f"La API rechazó la solicitud y no se pudo identificar qué parámetro ajustar. Mensaje de la API: "
                    f"{mensaje_api}"
                ) from error
            vistos.add(ajuste)
            resultado.cuerpo, resultado.enviar_semilla, descripcion = aplicar_ajuste(
                ajuste, resultado.cuerpo, resultado.enviar_semilla)
            resultado.ajustes.append(list(ajuste))
            resultado.descartados.append({"parametro": ajuste[1], "ajuste": descripcion, "motivo": mensaje_api})
            salida(f"   Sonda {numero}: la API rechazó «{ajuste[1]}» ({mensaje_api[:160]}). Ajuste: {descripcion}.")
    raise NegociacionFallida(f"Se hicieron {MAX_SONDAS} sondas sin que la API aceptara la solicitud.")
