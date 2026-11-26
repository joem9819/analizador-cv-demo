"""Carga de la configuración versionada (configuracion.json), perfiles de modelo, claves y rutas."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analizador_cv.utilidades import leer_json

#: Carpeta raíz del prototipo (la que contiene configuracion.json y .env).
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
RUTA_CONFIG_PREDETERMINADA = RAIZ_PROYECTO / "configuracion.json"
RUTA_ENV = RAIZ_PROYECTO / ".env"

#: La app usa modelos por API compatibles con OpenAI (Gemini); no hay modelos locales.
TIPOS_PERFIL = ("openai_compatible",)


class ErrorConfiguracion(Exception):
    """Error en el archivo de configuración o en una opción de la línea de comandos."""


class ErrorClave(ErrorConfiguracion):
    """Falta la clave de API de un perfil (variable de entorno no definida)."""


@dataclass
class Configuracion:
    """Configuración del prototipo con rutas resueltas respecto a la raíz del proyecto."""

    datos: dict[str, Any]
    raiz: Path
    ruta_archivo: Path

    # ------------------------------------------------------------------ rutas
    def _ruta(self, clave: str) -> Path:
        valor = Path(self.datos["rutas"][clave])
        return valor if valor.is_absolute() else (self.raiz / valor)

    @property
    def ruta_bd(self) -> Path:
        return self._ruta("base_datos")

    @property
    def ruta_resultados(self) -> Path:
        return self._ruta("resultados")

    @property
    def ruta_plantillas(self) -> Path:
        return self._ruta("plantillas")

    @property
    def ruta_respaldos(self) -> Path:
        return self._ruta("respaldos")

    @property
    def ruta_congelados(self) -> Path:
        """Parámetros negociados por prueba-conexion: junto a la base de datos que los usa."""
        return self.ruta_bd.parent / "congelados"

    # ------------------------------------------------------------- secciones
    @property
    def generacion(self) -> dict[str, Any]:
        return self.datos["generacion"]

    @property
    def documentos(self) -> dict[str, Any]:
        return self.datos["documentos"]

    @property
    def ofertas(self) -> dict[str, Any]:
        return self.datos["ofertas"]

    @property
    def max_intentos(self) -> int:
        """Intentos por solicitud al modelo: 1 inicial + reintentos."""
        return 1 + int(self.datos["reintentos"]["max_reintentos"])

    # --------------------------------------------------------------- perfiles
    @property
    def nombres_perfiles(self) -> list[str]:
        return list(self.datos["perfiles"])

    def perfil(self, nombre: str | None = None) -> dict[str, Any]:
        """Definición del perfil (copia) con su nombre. Sin nombre, el perfil predeterminado."""
        nombre = nombre or self.datos["perfil_predeterminado"]
        if nombre not in self.datos["perfiles"]:
            raise ErrorConfiguracion(
                f"El perfil «{nombre}» no existe en {self.ruta_archivo.name}. "
                f"Perfiles disponibles: {', '.join(self.nombres_perfiles)}"
            )
        definicion = copy.deepcopy(self.datos["perfiles"][nombre])
        definicion["nombre"] = nombre
        definicion.setdefault("temperature", self.generacion["temperature"])
        return definicion


def cargar_archivo_env(ruta: Path | None = None) -> list[str]:
    """Lee un archivo .env (CLAVE=valor) y define las variables que aún no existan en el entorno.

    Sin ruta, usa el de la variable ANALIZADOR_CV_ENV o, si no está, el .env de la carpeta del prototipo
    (las pruebas que abren la app en otro proceso la apuntan a un archivo vacío). Las variables ya
    definidas (por ejemplo, con $env: en PowerShell) tienen prioridad. Devuelve solo los nombres
    cargados; los valores nunca se imprimen ni se guardan en otro lugar.
    """
    ruta = Path(ruta or os.environ.get("ANALIZADOR_CV_ENV") or RUTA_ENV)
    if not ruta.is_file():
        return []
    cargadas = []
    for linea in ruta.read_text(encoding="utf-8-sig").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        nombre, valor = linea.split("=", 1)
        nombre = nombre.strip().removeprefix("export ").strip()
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        if nombre and valor and not os.environ.get(nombre, "").strip():
            os.environ[nombre] = valor
            cargadas.append(nombre)
    return cargadas


def clave_definida(perfil: dict[str, Any]) -> bool:
    """True si el perfil no necesita clave o si su variable de entorno tiene un valor."""
    variable = perfil.get("variable_clave")
    return not variable or bool(os.environ.get(variable, "").strip())


def leer_clave(perfil: dict[str, Any]) -> str:
    """Lee la clave de API del perfil desde su variable de entorno. Nunca la imprime ni la guarda."""
    variable = perfil.get("variable_clave")
    if not variable:
        raise ErrorConfiguracion(f"El perfil «{perfil['nombre']}» no define «variable_clave».")
    clave = os.environ.get(variable, "").strip()
    if not clave:
        donde = f"\nCree la clave en {perfil['url_claves']}" if perfil.get("url_claves") else ""
        raise ErrorClave(
            f"Falta la clave de API: la variable {variable} no está definida.\n"
            f"Agréguela al archivo .env de la carpeta del prototipo (una línea {variable}=su-clave) "
            f"o defínala en PowerShell solo para esta ventana:\n"
            f'    $env:{variable} = "pegue-aquí-su-clave"{donde}'
        )
    return clave


def _validar(datos: dict[str, Any]) -> None:
    obligatorias = ["perfil_predeterminado", "generacion", "perfiles", "reintentos", "documentos", "ofertas", "rutas"]
    faltantes = [c for c in obligatorias if c not in datos]
    if faltantes:
        raise ErrorConfiguracion(f"Faltan secciones en la configuración: {', '.join(faltantes)}")
    if datos["perfil_predeterminado"] not in datos["perfiles"]:
        raise ErrorConfiguracion(f"El perfil predeterminado «{datos['perfil_predeterminado']}» no está en «perfiles».")
    if not 0 <= float(datos["generacion"]["temperature"]) <= 2:
        raise ErrorConfiguracion("La temperatura debe estar entre 0 y 2.")
    if int(datos["reintentos"]["max_reintentos"]) < 0:
        raise ErrorConfiguracion("max_reintentos no puede ser negativo.")
    for nombre, perfil in datos["perfiles"].items():
        tipo = perfil.get("tipo")
        if tipo not in TIPOS_PERFIL:
            raise ErrorConfiguracion(f"El perfil «{nombre}» tiene un tipo desconocido: {tipo!r}.")
        # null = no se envía (p. ej., Gemini 3.5 Flash-Lite ignora la temperatura).
        if perfil.get("temperature") is not None and not 0 <= float(perfil["temperature"]) <= 2:
            raise ErrorConfiguracion(f"La temperatura del perfil «{nombre}» debe estar entre 0 y 2.")
        requeridas = ("proveedor", "url", "modelo", "variable_clave", "parametros", "salida_estructurada")
        faltan = [c for c in requeridas if c not in perfil]
        if faltan:
            raise ErrorConfiguracion(f"Al perfil «{nombre}» le faltan las claves: {', '.join(faltan)}")
    for portal in datos["ofertas"].get("portales_prohibidos", []):
        faltan = [c for c in ("nombre", "dominios", "documento", "url_terminos", "fecha_verificacion") if not portal.get(c)]
        if faltan:
            raise ErrorConfiguracion(f"Al portal «{portal.get('nombre', '?')}» le faltan las claves: {', '.join(faltan)}")


def cargar_configuracion(ruta: Path | str | None = None, *, bd: Path | str | None = None) -> Configuracion:
    """Lee configuracion.json y aplica la ruta de la base indicada en la línea de comandos."""
    ruta_archivo = Path(ruta) if ruta else RUTA_CONFIG_PREDETERMINADA
    if not ruta_archivo.exists():
        raise ErrorConfiguracion(f"No se encontró el archivo de configuración: {ruta_archivo}")
    datos = copy.deepcopy(leer_json(ruta_archivo))
    _validar(datos)
    if bd:
        datos["rutas"]["base_datos"] = str(Path(bd).resolve())
    return Configuracion(datos=datos, raiz=ruta_archivo.resolve().parent, ruta_archivo=ruta_archivo)
