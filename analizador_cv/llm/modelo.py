"""ModeloLLM: un perfil de configuracion.json listo para pedir respuestas JSON validadas."""

from __future__ import annotations

from typing import Any, Callable

from analizador_cv.configuracion import Configuracion, leer_clave
from analizador_cv.llm.backends import Backend
from analizador_cv.llm.cliente_api import ClienteCompatibleOpenAI
from analizador_cv.llm.negociacion import cargar_congelado, huella_perfil, opciones_para
from analizador_cv.llm.salida import Intento, ResultadoJSON, Validador, pedir_json
from analizador_cv.utilidades import hash_objeto

Salida = Callable[[str], None]


def crear_backend(perfil: dict[str, Any], *, salida: Salida = print) -> Backend:
    """Crea el cliente del perfil. Los perfiles de API exigen la clave."""
    if perfil["tipo"] == "openai_compatible":
        return ClienteCompatibleOpenAI(perfil, leer_clave(perfil), avisar=salida)
    raise ValueError(f"Tipo de perfil desconocido: {perfil['tipo']}")


class ModeloLLM:
    """Reúne el perfil, su backend y los parámetros negociados en la prueba de conexión.

    Se puede inyectar el backend (pruebas) y el congelado; si no, se crean desde la configuración.
    """

    def __init__(self, cfg: Configuracion, nombre_perfil: str | None = None, *, backend: Backend | None = None,
                 congelado: dict[str, Any] | None = None, salida: Salida = print):
        self.perfil = cfg.perfil(nombre_perfil)
        if congelado is None:
            congelado = cargar_congelado(cfg, self.perfil)
        self.congelado = congelado
        self.backend = backend if backend is not None else crear_backend(self.perfil, salida=salida)
        self.max_intentos = cfg.max_intentos
        self.semilla = int(cfg.generacion["semilla"])

    @property
    def nombre(self) -> str:
        return self.perfil["nombre"]

    @property
    def huella(self) -> str:
        """Identidad de la configuración del modelo (perfil + ajustes negociados), para registrar en cada análisis."""
        ajustes = (self.congelado or {}).get("ajustes")
        return hash_objeto({"perfil": huella_perfil(self.perfil), "ajustes": ajustes, "semilla": self.semilla})

    def opciones(self, esquema: dict[str, Any], *, nombre: str, max_tokens: int | None = None) -> dict[str, Any]:
        return opciones_para(self.perfil, esquema, self.congelado, nombre=nombre, semilla=self.semilla,
                             max_tokens=max_tokens)

    def pedir(self, mensajes: list[dict[str, str]], esquema: dict[str, Any], *, nombre: str,
              validador: Validador | None = None, max_tokens: int | None = None,
              registrar: Callable[[Intento, list[dict[str, str]], dict[str, Any]], None] | None = None) -> ResultadoJSON:
        """Pide una respuesta JSON que cumpla «esquema». «registrar» recibe (intento, mensajes, opciones)."""
        opciones = self.opciones(esquema, nombre=nombre, max_tokens=max_tokens)
        return pedir_json(self.backend, mensajes, opciones, esquema, validador=validador, max_intentos=self.max_intentos,
                          registrar=(lambda intento: registrar(intento, mensajes, opciones)) if registrar else None)
