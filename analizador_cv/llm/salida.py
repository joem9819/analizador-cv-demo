"""Respuestas JSON validadas con reintentos (requisito RNF03: un error nunca se convierte en puntaje).

pedir_json envía la solicitud, interpreta el contenido, lo valida contra el esquema completo y, si
hace falta, contra un validador propio de la etapa. Los textos del JSON se devuelven con las entidades
HTML decodificadas («Dise&ntilde;o» → «Diseño»): Gemini las escribió en la evaluación (Etapa 8). El
contenido original queda igual en el registro de cada intento. Cada intento queda en un registro (estado,
errores, tokens y latencia) que el llamador puede guardar en la base (tabla llamadas_llm).

Estados de un intento:
- «valida»: JSON que cumple el esquema y el validador;
- «invalida»: JSON malformado o que no cumple; consume un intento;
- «truncada»: el modelo se quedó sin tokens (motivo de fin = length); consume un intento;
- «error_http» / «error_tiempo»: error del backend que consume un intento;
- «error_conexion», «error_cupo», «error_autenticacion», etc.: detienen sin reintentar.
"""

from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from analizador_cv.llm.backends import Backend, ErrorBackend, RespuestaBackend
from analizador_cv.llm.esquemas import validar
from analizador_cv.utilidades import ahora_iso

Validador = Callable[[Any], list[str]]

_CERCO = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
_ENTIDAD = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]*|#[0-9]+|#[xX][0-9a-fA-F]+);?")


@dataclass
class Intento:
    numero: int
    estado: str
    fecha_inicio: str
    fecha_fin: str
    latencia_s: float | None
    respuesta: RespuestaBackend | None = None
    datos: Any = None
    errores: list[str] = field(default_factory=list)
    tipo_error: str | None = None

    @property
    def valido(self) -> bool:
        return self.estado == "valida"


@dataclass
class ResultadoJSON:
    datos: Any
    intentos: list[Intento]

    @property
    def ultimo(self) -> Intento:
        return self.intentos[-1]


class ErrorSalidaLLM(Exception):
    """No se obtuvo una respuesta válida: se agotaron los intentos o el backend detuvo la solicitud."""

    def __init__(self, mensaje: str, intentos: list[Intento], tipo: str):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.intentos = intentos
        self.tipo = tipo


def cargar_json(texto: str | None) -> tuple[Any, str | None]:
    """Interpreta el contenido del modelo como JSON. Devuelve (datos, error).

    Acepta el JSON envuelto en un bloque ```json … ``` o con texto alrededor, porque algunos modelos lo
    hacen en el modo json_object.
    """
    if texto is None or not texto.strip():
        return None, "respuesta sin contenido"
    texto = texto.strip()
    cerco = _CERCO.match(texto)
    if cerco:
        texto = cerco.group(1)
    try:
        return json.loads(texto), None
    except json.JSONDecodeError as error:
        inicio, fin = texto.find("{"), texto.rfind("}")
        if 0 <= inicio < fin:
            try:
                return json.loads(texto[inicio:fin + 1]), None
            except json.JSONDecodeError:
                pass
        return None, f"JSON malformado ({error.msg}, posición {error.pos})"


def decodificar_entidades(datos: Any) -> Any:
    """Los mismos datos con las entidades HTML de cada texto decodificadas (claves incluidas)."""
    if isinstance(datos, str):
        return html.unescape(datos) if _ENTIDAD.search(datos) else datos
    if isinstance(datos, list):
        return [decodificar_entidades(v) for v in datos]
    if isinstance(datos, dict):
        return {decodificar_entidades(k): decodificar_entidades(v) for k, v in datos.items()}
    return datos


def interpretar(respuesta: RespuestaBackend, esquema: dict[str, Any],
                validador: Validador | None = None) -> tuple[str, Any, list[str]]:
    """Clasifica una respuesta del backend. Devuelve (estado, datos, errores)."""
    if respuesta.error_formato:
        return "invalida", None, [respuesta.error_formato]
    datos, error = cargar_json(respuesta.contenido)
    if respuesta.motivo_fin == "length":
        return "truncada", datos, ["salida truncada por el límite de tokens (motivo de fin = length)"] + ([error] if error else [])
    if error:
        return "invalida", None, [error]
    datos = decodificar_entidades(datos)
    errores = validar(datos, esquema)
    if not errores and validador is not None:
        errores = list(validador(datos))
    return ("invalida" if errores else "valida"), datos, errores


def _estado_de_error(error: ErrorBackend) -> str:
    return {
        "cupo_agotado": "error_cupo",
        "conexion": "error_conexion",
        "tiempo": "error_tiempo",
        "autenticacion": "error_autenticacion",
        "modelo_no_encontrado": "error_modelo",
        "servicio_no_disponible": "error_servicio",
        "solicitud_rechazada": "error_solicitud",
    }.get(error.tipo, "error_http")


def pedir_json(backend: Backend, mensajes: list[dict[str, str]], opciones: dict[str, Any], esquema: dict[str, Any], *,
               validador: Validador | None = None, max_intentos: int = 3,
               registrar: Callable[[Intento], None] | None = None) -> ResultadoJSON:
    """Pide una respuesta JSON válida con hasta «max_intentos» intentos.

    Lanza ErrorSalidaLLM si el backend detiene la solicitud (conexión, clave, cupo…) o si ningún intento
    es válido. «registrar» recibe cada intento (válido o no) para guardarlo.
    """
    intentos: list[Intento] = []
    for numero in range(1, max_intentos + 1):
        inicio = ahora_iso()
        t0 = time.perf_counter()
        try:
            respuesta = backend.chat(mensajes, opciones)
        except ErrorBackend as error:
            intento = Intento(numero, _estado_de_error(error), inicio, ahora_iso(),
                              error.latencia_s if error.latencia_s is not None else time.perf_counter() - t0,
                              errores=[f"{error.tipo}: {error.mensaje}"], tipo_error=error.tipo)
            intentos.append(intento)
            if registrar:
                registrar(intento)
            if error.detiene:
                raise ErrorSalidaLLM(error.mensaje, intentos, error.tipo) from error
            continue
        estado, datos, errores = interpretar(respuesta, esquema, validador)
        intento = Intento(numero, estado, inicio, ahora_iso(), respuesta.latencia_s, respuesta,
                          datos if estado == "valida" else None, errores)
        intentos.append(intento)
        if registrar:
            registrar(intento)
        if intento.valido:
            return ResultadoJSON(datos, intentos)
    resumen = "; ".join(f"intento {i.numero}: {i.estado} ({'; '.join(i.errores)[:200]})" for i in intentos)
    raise ErrorSalidaLLM(f"El modelo no devolvió una respuesta válida en {max_intentos} intentos. {resumen}",
                         intentos, "salida_invalida")
