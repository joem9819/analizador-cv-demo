"""Cliente HTTP para API compatibles con OpenAI (Groq y Gemini), escrito solo con la librería estándar (urllib).

Endpoints usados: GET {url}/models y POST {url}/chat/completions. Cada solicitud es una
conversación nueva sin streaming. Los parámetros que se envían los arma negociacion.opciones_para
con lo que la API aceptó en «prueba-conexion» (datos/congelados/<perfil>.json).

La clave se lee de una variable de entorno y solo viaja en el encabezado Authorization: nunca se
guarda en la base, en los resultados ni en los mensajes de error.

Límites de uso (HTTP 429):
- límite por minuto (RPM/TPM): se espera lo que indique la API (Retry-After, «retryDelay» de
  Gemini o «try again in …» de Groq) y se reintenta; la espera no consume intentos;
- cupo diario (RPD/TPD, «PerDay») o espera mayor que «espera_maxima_429_s»: se detiene con un
  mensaje que indica cuándo se puede volver a intentar.
"""

from __future__ import annotations

import email.utils
import json
import math
import re
import socket
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from analizador_cv import __version__
from analizador_cv.llm.backends import ErrorBackend, InformacionModelo, RespuestaBackend
from analizador_cv.utilidades import formatear_duracion

#: Encabezados de cupo que se registran (nunca se registra Authorization).
PREFIJO_CABECERAS_LIMITE = "x-ratelimit-"
#: Esperas ante errores 5xx (servicio sobrecargado o caído) antes de detener la ejecución.
ESPERAS_SERVICIO_S = (10.0, 30.0, 60.0)
#: Base del retroceso exponencial cuando un 429 no indica cuánto esperar.
ESPERA_429_BASE_S = 15.0
#: Máximo de esperas por 429 en una misma solicitud antes de detener la ejecución.
MAX_ESPERAS_429 = 20
#: Margen añadido a la espera pedida por la API.
MARGEN_ESPERA_S = 0.5

#: Indicios de que la API rechazó la SALIDA del modelo (no los parámetros): consume un intento.
MARCAS_SALIDA_INVALIDA = (
    "json_validate_failed",
    "does not match the expected schema",
    "failed_generation",
    "failed to generate json",
)
#: Indicios de cupo diario agotado (Groq: «(RPD)», «(TPD)», «per day»; Gemini: «…PerDay…»).
MARCAS_CUPO_DIARIO = ("per day", "perday", "(rpd)", "(tpd)", "daily")


# =============================================================================
# Utilidades
# =============================================================================
def duracion_a_segundos(texto: Any) -> float | None:
    """Convierte «17», «7.66s», «2m59.56s», «1h2m3s» o «120ms» en segundos."""
    if texto is None:
        return None
    texto = str(texto).strip().lower()
    if not texto:
        return None
    try:
        return float(texto)
    except ValueError:
        pass
    partes = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", texto)
    if not partes:
        return None
    factores = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    return sum(float(valor) * factores[unidad] for valor, unidad in partes)


def segundos_retry_after(valor: str | None) -> float | None:
    """Retry-After en segundos (admite número de segundos o fecha HTTP)."""
    if valor is None:
        return None
    try:
        return max(0.0, float(str(valor).strip()))
    except ValueError:
        pass
    try:
        fecha = email.utils.parsedate_to_datetime(str(valor))
    except (TypeError, ValueError):
        return None
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=timezone.utc)
    return max(0.0, (fecha - datetime.now(timezone.utc)).total_seconds())


def extraer_mensaje_error(texto: str | None) -> str:
    """Mensaje legible de un cuerpo de error (formato OpenAI, Groq o Gemini)."""
    if not texto:
        return "(sin cuerpo)"
    try:
        datos = json.loads(texto)
    except (json.JSONDecodeError, TypeError):
        return str(texto).strip()[:500]
    if isinstance(datos, list) and datos:
        datos = datos[0]
    if isinstance(datos, dict):
        error = datos.get("error", datos)
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:500]
        if isinstance(error, str):
            return error[:500]
    return str(texto).strip()[:500]


def analizar_429(cuerpo: str | None, cabeceras: dict[str, str]) -> tuple[float | None, bool, str]:
    """Devuelve (segundos de espera indicados o None, ¿es cupo diario?, mensaje de la API)."""
    texto = cuerpo or ""
    bajo = texto.lower()
    diario = any(marca in bajo for marca in MARCAS_CUPO_DIARIO)
    espera = segundos_retry_after(cabeceras.get("retry-after"))
    if espera is None:
        coincidencia = re.search(r'"retrydelay"\s*:\s*"(\d+(?:\.\d+)?)s"', bajo)
        if coincidencia:
            espera = float(coincidencia.group(1))
    if espera is None:
        coincidencia = re.search(r"try again in ((?:\d+(?:\.\d+)?(?:ms|h|m|s))+)", bajo)
        if coincidencia:
            espera = duracion_a_segundos(coincidencia.group(1))
    return espera, diario, extraer_mensaje_error(texto)


def estimar_cupo(limites: dict[str, Any] | None, tokens_por_solicitud: float | None,
                 solicitudes: tuple[int, ...] = (400,)) -> dict[str, Any]:
    """Estima cuántas solicitudes caben por día y cuántos días tomarían (sin reintentos).

    El valor predeterminado (400) es el consumo estimado de la evaluación técnica por modelo
    (definición del proyecto, sección 9).
    """
    limites = limites or {}
    rpd, tpd = limites.get("rpd"), limites.get("tpd")
    candidatos = []
    if rpd:
        candidatos.append(("solicitudes por día (RPD)", int(rpd)))
    if tpd and tokens_por_solicitud:
        candidatos.append(("tokens por día (TPD)", int(tpd // max(1.0, float(tokens_por_solicitud)))))
    if not candidatos:
        return {"disponible": False,
                "motivo": "Límites diarios no configurados en configuracion.json (perfil → limites)."}
    limitante, por_dia = min(candidatos, key=lambda c: c[1])
    por_dia = max(1, por_dia)
    return {
        "disponible": True,
        "tokens_por_solicitud": tokens_por_solicitud,
        "solicitudes_por_dia": por_dia,
        "limite_que_manda": limitante,
        "dias": {str(n): math.ceil(n / por_dia) for n in solicitudes},
        "nota": "Estimación sin reintentos ni esperas; se basa en el uso de tokens medido y en los límites configurados.",
    }


# =============================================================================
# Espaciado entre solicitudes
# =============================================================================
class Limitador:
    """Espacia las solicitudes según el perfil: mínimo de segundos, RPM y TPM (ventanas de 60 s).

    Usa el uso real de tokens de las respuestas anteriores. Si la API informa los tokens restantes
    del minuto (x-ratelimit-remaining-tokens) y no alcanzan para otra solicitud, espera hasta su
    reinicio (x-ratelimit-reset-tokens).
    """

    def __init__(self, espaciado_minimo_s: float = 0.0, rpm: int | None = None, tpm: int | None = None, *,
                 dormir: Callable[[float], None] = time.sleep, reloj: Callable[[], float] = time.monotonic):
        self.espaciado = float(espaciado_minimo_s or 0.0)
        self.rpm = int(rpm) if rpm else None
        self.tpm = int(tpm) if tpm else None
        self._dormir = dormir
        self._reloj = reloj
        self._envios: deque[list[Any]] = deque()  # [instante, tokens]
        self._ultimo: float | None = None
        self._tokens_previstos: int | None = None
        self._pista: tuple[float, int, float] | None = None  # (instante, tokens restantes, segundos hasta reinicio)

    def calcular_espera(self) -> float:
        ahora = self._reloj()
        while self._envios and self._envios[0][0] <= ahora - 60.0:
            self._envios.popleft()
        espera = 0.0
        if self._ultimo is not None:
            espera = max(espera, self._ultimo + self.espaciado - ahora)
        if self.rpm and len(self._envios) >= self.rpm:
            espera = max(espera, self._envios[len(self._envios) - self.rpm][0] + 60.0 - ahora)
        previstos = self._tokens_previstos
        if self.tpm and previstos:
            usados = sum(t or 0 for _, t in self._envios)
            if usados + previstos > self.tpm:
                for instante, tokens in self._envios:
                    usados -= tokens or 0
                    if usados + previstos <= self.tpm:
                        espera = max(espera, instante + 60.0 - ahora)
                        break
        if self._pista and previstos:
            instante, restantes, reinicio = self._pista
            if restantes < previstos:
                espera = max(espera, instante + reinicio - ahora)
        return max(0.0, espera)

    def esperar(self) -> float:
        espera = self.calcular_espera()
        if espera > 0:
            self._dormir(espera)
        return espera

    def registrar_envio(self) -> None:
        ahora = self._reloj()
        self._ultimo = ahora
        self._envios.append([ahora, None])

    def registrar_uso(self, tokens: int | None, cabeceras: dict[str, str] | None = None) -> None:
        if self._envios and self._envios[-1][1] is None:
            self._envios[-1][1] = int(tokens or self._tokens_previstos or 0)
        if tokens:
            self._tokens_previstos = int(tokens)
        cabeceras = cabeceras or {}
        restantes = cabeceras.get("x-ratelimit-remaining-tokens")
        reinicio = duracion_a_segundos(cabeceras.get("x-ratelimit-reset-tokens"))
        try:
            self._pista = (self._reloj(), int(float(restantes)), reinicio) if restantes is not None and reinicio is not None else None
        except ValueError:
            self._pista = None


# =============================================================================
# Cliente
# =============================================================================
class _RespuestaError(Exception):
    """Respuesta HTTP con código de error (uso interno)."""

    def __init__(self, codigo: int, cuerpo: str, cabeceras: dict[str, str], latencia: float):
        super().__init__(f"HTTP {codigo}")
        self.codigo = codigo
        self.cuerpo = cuerpo
        self.cabeceras = cabeceras
        self.latencia = latencia


class ClienteCompatibleOpenAI:
    """Cliente mínimo de chat/completions. Respeta el proxy del sistema (la API está en internet)."""

    nombre = "openai_compatible"

    def __init__(self, perfil: dict[str, Any], clave: str, *,
                 dormir: Callable[[float], None] = time.sleep, reloj: Callable[[], float] = time.monotonic,
                 avisar: Callable[[str], None] | None = None,
                 esperas_servicio: tuple[float, ...] = ESPERAS_SERVICIO_S):
        self.perfil = perfil["nombre"]
        self.proveedor = perfil.get("proveedor") or "La API"
        self.url = str(perfil["url"]).rstrip("/")
        self.modelo = perfil["modelo"]
        self.variable_clave = perfil.get("variable_clave")
        self.tiempo_espera_s = float(perfil.get("tiempo_espera_s", 120))
        self.espera_maxima_429_s = float(perfil.get("espera_maxima_429_s", 300))
        limites = perfil.get("limites") or {}
        self.limitador = Limitador(perfil.get("espaciado_minimo_s", 0.0), limites.get("rpm"), limites.get("tpm"),
                                   dormir=dormir, reloj=reloj)
        self._clave = clave
        self._dormir = dormir
        self._avisar = avisar or (lambda _texto: None)
        self._esperas_servicio = tuple(esperas_servicio)
        self._abridor = urllib.request.build_opener()

    def __repr__(self) -> str:  # nunca incluye la clave
        return f"ClienteCompatibleOpenAI(perfil={self.perfil!r}, url={self.url!r}, modelo={self.modelo!r})"

    # ------------------------------------------------------------ HTTP básico
    def _redactar(self, texto: str) -> str:
        """Omite identificadores de organización y, por precaución, la clave si apareciera."""
        texto = re.sub(r"org_[A-Za-z0-9]+", "org_[omitido]", texto or "")
        if self._clave and self._clave in texto:
            texto = texto.replace(self._clave, "[clave omitida]")
        return texto

    def _solicitar(self, metodo: str, ruta: str, cuerpo: dict[str, Any] | None = None) -> tuple[str, float, dict[str, str]]:
        datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8") if cuerpo is not None else None
        peticion = urllib.request.Request(
            self.url + ruta,
            data=datos,
            method=metodo,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "Authorization": f"Bearer {self._clave}",
                "User-Agent": f"analizador-cv/{__version__}",
            },
        )
        inicio = time.perf_counter()
        try:
            with self._abridor.open(peticion, timeout=self.tiempo_espera_s) as respuesta:
                texto = respuesta.read().decode("utf-8", errors="replace")
                cabeceras = {k.lower(): v for k, v in respuesta.headers.items()}
            return self._redactar(texto), time.perf_counter() - inicio, cabeceras
        except urllib.error.HTTPError as error:
            latencia = time.perf_counter() - inicio
            cuerpo_error = error.read().decode("utf-8", errors="replace") if error.fp else ""
            cabeceras = {k.lower(): v for k, v in (error.headers.items() if error.headers else [])}
            raise _RespuestaError(error.code, self._redactar(cuerpo_error), cabeceras, latencia) from None
        except (socket.timeout, TimeoutError):
            raise ErrorBackend("tiempo", f"Se agotó el tiempo de espera ({self.tiempo_espera_s:.0f} s) en {ruta}.",
                               None, time.perf_counter() - inicio) from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, (socket.timeout, TimeoutError)):
                raise ErrorBackend("tiempo", f"Se agotó el tiempo de espera en {ruta}.", None,
                                   time.perf_counter() - inicio) from None
            raise ErrorBackend(
                "conexion",
                f"No se pudo conectar con {self.proveedor} en {self.url} ({error.reason}). "
                "Revise la conexión a internet (o la configuración de proxy de la red).",
                None,
                time.perf_counter() - inicio,
            ) from None
        except (ConnectionError, OSError) as error:
            raise ErrorBackend("conexion", f"Error de conexión con {self.url}: {error}", None,
                               time.perf_counter() - inicio) from None

    def _error_de_http(self, error: _RespuestaError) -> ErrorBackend:
        mensaje = extraer_mensaje_error(error.cuerpo)
        bajo = (error.cuerpo or "").lower()
        # Gemini responde a una clave inválida con HTTP 400 «Please pass a valid API key» (verificado el 19/10/2026).
        clave_invalida = error.codigo == 400 and any(m in bajo for m in ("api key", "api_key_invalid", "valid api key"))
        if error.codigo in (401, 403) or clave_invalida:
            pista = (" La respuesta parece venir del cortafuegos del proveedor («error code 1010»)."
                     if "1010" in bajo else "")
            return ErrorBackend(
                "autenticacion",
                f"{self.proveedor} rechazó la solicitud (HTTP {error.codigo}): {mensaje}.{pista} "
                f"Revise que la variable {self.variable_clave} tenga una clave válida y vigente.",
                error.cuerpo, error.latencia)
        if error.codigo in (400, 422) and any(marca in bajo for marca in MARCAS_SALIDA_INVALIDA):
            return ErrorBackend("http", f"La API rechazó la salida del modelo por no cumplir el esquema "
                                        f"(HTTP {error.codigo}): {mensaje}", error.cuerpo, error.latencia)
        if error.codigo == 404:
            return ErrorBackend("modelo_no_encontrado",
                                f"{self.proveedor} respondió 404: {mensaje}. Revise el modelo «{self.modelo}» y la URL {self.url}.",
                                error.cuerpo, error.latencia)
        if error.codigo in (400, 413, 422):
            return ErrorBackend(
                "solicitud_rechazada",
                f"{self.proveedor} rechazó la solicitud (HTTP {error.codigo}): {mensaje}. "
                f"Ejecute «python -m analizador_cv prueba-conexion --perfil {self.perfil}» para revisar los parámetros.",
                error.cuerpo, error.latencia)
        return ErrorBackend("http", f"HTTP {error.codigo}: {mensaje}", error.cuerpo, error.latencia)

    def _error_cupo(self, error: _RespuestaError, espera: float | None, diario: bool, mensaje: str,
                    esperas: int, segundos: float) -> ErrorBackend:
        if diario:
            motivo = "se agotó el cupo diario"
        elif espera is not None:
            motivo = (f"la API pide esperar {formatear_duracion(espera)}, más que el máximo configurado "
                      f"({formatear_duracion(self.espera_maxima_429_s)})")
        else:
            motivo = f"el límite persiste después de {esperas} esperas"
        reanudar = ""
        if espera is not None:
            hora = (datetime.now() + timedelta(seconds=espera)).strftime("%d/%m/%Y %H:%M")
            reanudar = f" Según la API, podrá continuar hacia las {hora} (hora de este computador)."
        elif self.proveedor == "Google" or "googleapis" in self.url:
            reanudar = (" El cupo diario de Gemini se reinicia a medianoche, hora del Pacífico "
                        "(2:00 o 3:00 a. m. en Colombia, según el horario de verano de EE. UU.).")
        return ErrorBackend(
            "cupo_agotado",
            f"{self.proveedor}: {motivo} (HTTP 429: {mensaje}).{reanudar} "
            "Vuelva a intentarlo más tarde o use otro perfil de modelo.",
            error.cuerpo, error.latencia, esperas, segundos)

    def _enviar(self, metodo: str, ruta: str, cuerpo: dict[str, Any] | None = None,
                limitar: bool = False) -> tuple[str, float, dict[str, str], int, float]:
        """Envía la solicitud manejando 429 y 5xx. Devuelve (texto, latencia, cabeceras, esperas 429, segundos)."""
        esperas = 0
        segundos = 0.0
        fallos_servicio = 0
        while True:
            if limitar:
                self.limitador.esperar()
                self.limitador.registrar_envio()
            try:
                texto, latencia, cabeceras = self._solicitar(metodo, ruta, cuerpo)
                return texto, latencia, cabeceras, esperas, segundos
            except _RespuestaError as error:
                if error.codigo == 429:
                    espera, diario, mensaje = analizar_429(error.cuerpo, error.cabeceras)
                    if diario or esperas >= MAX_ESPERAS_429 or (espera is not None and espera > self.espera_maxima_429_s):
                        raise self._error_cupo(error, espera, diario, mensaje, esperas, segundos) from None
                    if espera is None:
                        espera = min(ESPERA_429_BASE_S * (2 ** esperas), self.espera_maxima_429_s)
                    espera += MARGEN_ESPERA_S
                    self._avisar(f"   Límite por minuto de {self.proveedor} (HTTP 429): se espera "
                                 f"{formatear_duracion(espera)} y se reintenta (no consume intentos).")
                    self._dormir(espera)
                    esperas += 1
                    segundos += espera
                    continue
                if error.codigo >= 500:
                    mensaje = extraer_mensaje_error(error.cuerpo)
                    if fallos_servicio < len(self._esperas_servicio):
                        espera = self._esperas_servicio[fallos_servicio]
                        fallos_servicio += 1
                        self._avisar(f"   {self.proveedor} no está disponible (HTTP {error.codigo}); se espera "
                                     f"{formatear_duracion(espera)} y se reintenta ({fallos_servicio}/{len(self._esperas_servicio)}).")
                        self._dormir(espera)
                        continue
                    raise ErrorBackend(
                        "servicio_no_disponible",
                        f"{self.proveedor} sigue sin estar disponible (HTTP {error.codigo}) después de "
                        f"{len(self._esperas_servicio)} esperas: {mensaje}. Vuelva a intentarlo más tarde.",
                        error.cuerpo, error.latencia, esperas, segundos) from None
                fallo = self._error_de_http(error)
                fallo.esperas_429, fallo.segundos_espera_429 = esperas, segundos
                raise fallo from None

    # ------------------------------------------------------------ endpoints
    def listar_modelos(self) -> list[dict[str, Any]]:
        texto, _, _, _, _ = self._enviar("GET", "/models")
        try:
            datos = json.loads(texto)
        except json.JSONDecodeError as error:
            raise ErrorBackend("http", f"Respuesta no JSON en /models: {texto[:200]}", texto) from error
        modelos = datos.get("data") if isinstance(datos, dict) else None
        if modelos is None and isinstance(datos, dict):
            modelos = datos.get("models")
        return [m for m in (modelos or []) if isinstance(m, dict)]

    def informacion(self) -> InformacionModelo:
        """Comprueba la clave y que el proveedor ofrezca el modelo (GET /models)."""
        modelos = self.listar_modelos()
        elegido = None
        for m in modelos:
            identificador = str(m.get("id") or m.get("name") or "")
            if identificador in (self.modelo, f"models/{self.modelo}"):
                elegido = m
                break
        if elegido is None:
            disponibles = sorted(str(m.get("id") or m.get("name")) for m in modelos)
            muestra = ", ".join(disponibles[:30]) + (" …" if len(disponibles) > 30 else "")
            raise ErrorBackend(
                "modelo_no_encontrado",
                f"{self.proveedor} no ofrece el modelo «{self.modelo}» con esta clave. "
                f"Modelos disponibles: {muestra or 'ninguno'}",
            )
        return InformacionModelo(
            backend="openai_compatible",
            modelo=self.modelo,
            url=self.url,
            perfil=self.perfil,
            proveedor=self.proveedor,
            detalles={"modelo_api": elegido},
        )

    def chat(self, mensajes: list[dict[str, str]], opciones: dict[str, Any]) -> RespuestaBackend:
        """Envía una conversación nueva a /chat/completions.

        «opciones» trae response_format, los parámetros aceptados por la API y, si corresponde, la semilla.
        """
        cuerpo = {"model": self.modelo, "messages": mensajes, **opciones}
        texto, latencia, cabeceras, esperas, segundos = self._enviar("POST", "/chat/completions", cuerpo, limitar=True)
        limites = {k: v for k, v in cabeceras.items() if k.startswith(PREFIJO_CABECERAS_LIMITE)} or None
        try:
            datos = json.loads(texto)
        except json.JSONDecodeError:
            self.limitador.registrar_uso(None, cabeceras)
            return RespuestaBackend(cuerpo_crudo=texto, contenido=None, latencia_s=latencia,
                                    error_formato="El cuerpo de la respuesta HTTP no es JSON.",
                                    esperas_429=esperas, segundos_espera_429=segundos, cabeceras_limite=limites)
        if isinstance(datos, list) and datos:
            datos = datos[0]
        if not isinstance(datos, dict):
            datos = {}
        eleccion = (datos.get("choices") or [{}])[0] or {}
        mensaje = eleccion.get("message") or {}
        contenido = mensaje.get("content")
        if isinstance(contenido, list):  # algunas API devuelven partes de texto
            contenido = "".join(p.get("text", "") for p in contenido if isinstance(p, dict))
        uso = datos.get("usage") if isinstance(datos.get("usage"), dict) else {}
        detalles = uso.get("completion_tokens_details") if isinstance(uso.get("completion_tokens_details"), dict) else {}
        total = uso.get("total_tokens")
        if total is None and (uso.get("prompt_tokens") is not None or uso.get("completion_tokens") is not None):
            total = (uso.get("prompt_tokens") or 0) + (uso.get("completion_tokens") or 0)
        self.limitador.registrar_uso(total, cabeceras)
        return RespuestaBackend(
            cuerpo_crudo=texto,
            contenido=contenido,
            motivo_fin=eleccion.get("finish_reason"),
            tokens_entrada=uso.get("prompt_tokens"),
            tokens_salida=uso.get("completion_tokens"),
            latencia_s=latencia,
            modelo_respuesta=datos.get("model"),
            id_respuesta=datos.get("id"),
            huella_sistema=datos.get("system_fingerprint"),
            tokens_razonamiento=detalles.get("reasoning_tokens"),
            tokens_total=total,
            uso=uso or None,
            esperas_429=esperas,
            segundos_espera_429=segundos,
            cabeceras_limite=limites,
        )
