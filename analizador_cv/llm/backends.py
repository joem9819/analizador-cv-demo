"""Tipos comunes a los backends de modelos de lenguaje (API compatible con OpenAI y backends de prueba)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class RespuestaBackend:
    """Respuesta de un modelo (chat/completions o /api/chat)."""

    cuerpo_crudo: str
    contenido: str | None
    motivo_fin: str | None = None
    tokens_entrada: int | None = None
    tokens_salida: int | None = None
    duracion_total_ns: int | None = None
    duracion_carga_ns: int | None = None
    duracion_generacion_ns: int | None = None
    latencia_s: float = 0.0
    error_formato: str | None = None
    # Campos de las API compatibles con OpenAI
    modelo_respuesta: str | None = None
    id_respuesta: str | None = None
    huella_sistema: str | None = None
    tokens_razonamiento: int | None = None
    tokens_total: int | None = None
    uso: dict[str, Any] | None = None
    esperas_429: int = 0
    segundos_espera_429: float = 0.0
    cabeceras_limite: dict[str, str] | None = None


class ErrorBackend(Exception):
    """Fallo al comunicarse con el backend.

    tipo:
    - «conexion»: no se pudo conectar (servidor apagado, sin internet, URL incorrecta). Detiene.
    - «modelo_no_encontrado»: el modelo no existe o no está descargado. Detiene.
    - «autenticacion»: la API rechazó la clave (401/403). Detiene.
    - «cupo_agotado»: se agotó el cupo diario o la espera pedida es demasiado larga (429). Detiene.
    - «servicio_no_disponible»: errores 5xx persistentes tras esperar. Detiene.
    - «solicitud_rechazada»: la API rechazó los parámetros (400/404/413/422). Detiene.
    - «http»: error HTTP atribuible a la salida del modelo u otro error. Consume un intento.
    - «tiempo»: se agotó el tiempo de espera. Consume un intento.
    """

    def __init__(self, tipo: str, mensaje: str, cuerpo: str | None = None, latencia_s: float | None = None,
                 esperas_429: int = 0, segundos_espera_429: float = 0.0):
        super().__init__(mensaje)
        self.tipo = tipo
        self.mensaje = mensaje
        self.cuerpo = cuerpo
        self.latencia_s = latencia_s
        self.esperas_429 = esperas_429
        self.segundos_espera_429 = segundos_espera_429

    @property
    def consume_intento(self) -> bool:
        return self.tipo in ("http", "tiempo")

    @property
    def detiene(self) -> bool:
        """True si el error es de infraestructura: se detiene sin gastar reintentos."""
        return not self.consume_intento


@dataclass
class InformacionModelo:
    backend: str
    modelo: str
    url: str | None = None
    perfil: str | None = None
    proveedor: str | None = None
    detalles: dict[str, Any] = field(default_factory=dict)


class Backend(Protocol):
    """Interfaz de los clientes: «opciones» es todo lo que va en el cuerpo además de model y messages.

    Las opciones las arma negociacion.opciones_para: response_format y los parámetros negociados.
    """

    nombre: str

    def informacion(self) -> InformacionModelo: ...

    def chat(self, mensajes: list[dict[str, str]], opciones: dict[str, Any]) -> RespuestaBackend: ...
