"""Obtención de la oferta laboral para el modo específico (RF06 y RNF08; definición, sección 5.3).

Tres orígenes:
- URL (opcional): si el dominio es de un portal cuyos términos prohíben el acceso automatizado
  (lista en configuracion.json, con la fecha de verificación), NO se descarga la página: se explica
  el motivo y se pide pegar la descripción. En los demás sitios se respeta robots.txt y se hace una
  sola petición a la página (más la de robots.txt), sin iniciar sesión ni eludir bloqueos. Cada
  redirección se revisa igual que la dirección original. Del HTML se extrae, en orden: datos
  estructurados JSON-LD de tipo JobPosting, texto principal con trafilatura y, como último
  recurso, el texto visible de la página.
- HTML guardado: el mismo análisis sin red (para pruebas y páginas de sitios permitidos). Si la
  página declara como dirección (canonical u og:url) la de un portal de la lista, se rechaza igual.
- Texto pegado: solo se normaliza.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterator
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup, Comment, UnicodeDammit

from analizador_cv import __version__
from analizador_cv.documentos import normalizar_texto
from analizador_cv.utilidades import ahora_iso, hash_texto

#: Sin tildes: las cabeceras HTTP solo admiten ASCII.
AGENTE_USUARIO = f"analizador-cv/{__version__} (prototipo academico, ITM; una peticion por accion del usuario)"
TIPOS_HTML = ("text/html", "application/xhtml+xml")
ESTADOS_BLOQUEO = (401, 403, 429, 999)
MAXIMO_BYTES_ROBOTS = 512 * 1024
MAX_REDIRECCIONES_ROBOTS = 5
MINIMO_TEXTO_PEGADO = 100
#: Indicios de una página de inicio de sesión o de verificación (solo se usan si no se encontró la oferta).
MARCAS_SESION = ("captcha", "iniciar sesión", "inicia sesión", "inicie sesión", "sign in", "log in", "login",
                 "authwall", "regístrate", "registrate")
ETIQUETAS_BLOQUE = ("p", "div", "li", "ul", "ol", "tr", "table", "section", "article", "h1", "h2", "h3", "h4",
                    "h5", "h6", "dt", "dd", "header", "footer")
ETIQUETAS_RUIDO = ("script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "template",
                   "iframe", "button")

MENSAJE_PEGAR = "Copie la descripción de la oferta desde su navegador y péguela en el campo de texto."
MENSAJE_ROBOTS = ("El archivo robots.txt del sitio no permite que programas automáticos lean esta página, y la app "
                  "lo respeta. " + MENSAJE_PEGAR)


@dataclass
class Oferta:
    """Resultado de obtener una oferta. Si «estado» no es «extraida», «mensaje» explica qué hacer."""

    estado: str
    origen: str
    mensaje: str
    metodo: str | None = None
    url: str | None = None
    url_final: str | None = None
    dominio: str | None = None
    portal: str | None = None
    titulo: str | None = None
    empresa: str | None = None
    ubicacion: str | None = None
    texto: str | None = None
    http_estado: int | None = None
    redirecciones: int = 0
    fecha: str = field(default_factory=ahora_iso)

    @property
    def exito(self) -> bool:
        return self.estado == "extraida"

    @property
    def caracteres(self) -> int:
        return len(self.texto or "")

    @property
    def hash_texto(self) -> str | None:
        return hash_texto(self.texto) if self.texto else None


class _Detencion(Exception):
    def __init__(self, estado: str, mensaje: str, *, url: str | None = None, http_estado: int | None = None,
                 portal: str | None = None, redirecciones: int = 0):
        super().__init__(mensaje)
        self.estado, self.mensaje, self.url = estado, mensaje, url
        self.http_estado, self.portal, self.redirecciones = http_estado, portal, redirecciones


# =============================================================================
# Direcciones y portales
# =============================================================================
def dominio_de(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.rstrip(".").lower() if host else None


def portal_prohibido(url: str | None, portales: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Portal de la lista al que pertenece la dirección (por dominio o subdominio), o None."""
    dominio = dominio_de(url)
    if not dominio:
        return None
    for portal in portales:
        for d in portal["dominios"]:
            if dominio == d or dominio.endswith("." + d):
                return portal
    return None


def normalizar_url(texto: str | None) -> str | None:
    """Dirección http(s) completa, o None si no es válida. Sin esquema, se asume https://."""
    texto = (texto or "").strip()
    if not texto or any(c.isspace() for c in texto):
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", texto, re.IGNORECASE):
        if re.match(r"^[a-z][a-z0-9+.-]*:(?!\d)", texto, re.IGNORECASE):  # mailto:, javascript:, etc.
            return None
        texto = "https://" + texto
    try:
        partes = urlsplit(texto)
        valido = partes.scheme.lower() in ("http", "https") and bool(partes.hostname) and partes.port != 0
    except ValueError:
        return None
    return texto if valido else None


def _fecha_es(iso: str) -> str:
    anio, mes, dia = iso.split("-")
    return f"{dia}/{mes}/{anio}"


def _mensaje_portal(portal: dict[str, Any]) -> str:
    return (f"{portal['nombre']} prohíbe en sus términos de uso el acceso automatizado a sus páginas "
            f"({portal['documento']}; verificado el {_fecha_es(portal['fecha_verificacion'])}). Por eso la app no "
            f"descarga esta oferta. {MENSAJE_PEGAR}")


# =============================================================================
# Red
# =============================================================================
def crear_cliente(tiempo_espera_s: float = 15, *, transporte: httpx.BaseTransport | None = None) -> httpx.Client:
    """Cliente HTTP sin redirecciones automáticas (cada salto se revisa). «transporte» sirve para pruebas sin red."""
    return httpx.Client(
        transport=transporte,
        timeout=httpx.Timeout(tiempo_espera_s),
        follow_redirects=False,
        headers={"User-Agent": AGENTE_USUARIO, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                 "Accept-Language": "es-CO,es;q=0.9,en;q=0.5"},
    )


def _descargar(cliente: httpx.Client, url: str, maximo_bytes: int) -> tuple[httpx.Response, bytes]:
    with cliente.stream("GET", url) as respuesta:
        datos = bytearray()
        for bloque in respuesta.iter_bytes():
            datos.extend(bloque)
            if len(datos) > maximo_bytes:
                raise _Detencion("demasiado_grande", f"La página supera el tamaño máximo ({maximo_bytes // 1024} KB). "
                                 + MENSAJE_PEGAR, url=url)
    return respuesta, bytes(datos)


class _Robots:
    """Lee y guarda robots.txt por origen (RFC 9309).

    2xx: se aplican sus reglas; 401 o 403: no se permite (criterio conservador); otros 4xx: no hay
    restricciones. Si robots.txt no se puede leer (error de red, 5xx, demasiado grande o redirección a
    un portal de la lista), se asume que no se permite y se detiene con un mensaje que dice por qué.
    """

    def __init__(self, cliente: httpx.Client, portales: list[dict[str, Any]]):
        self._cliente = cliente
        self._portales = portales
        self._cache: dict[str, RobotFileParser | bool] = {}

    def permite(self, url: str) -> bool:
        partes = urlsplit(url)
        origen = f"{partes.scheme}://{partes.netloc}"
        if origen not in self._cache:
            self._cache[origen] = self._leer(origen + "/robots.txt")
        reglas = self._cache[origen]
        if isinstance(reglas, bool):
            return reglas
        return reglas.can_fetch(AGENTE_USUARIO, url)

    def _leer(self, url: str) -> RobotFileParser | bool:
        precaucion = "por precaución no se lee la página. " + MENSAJE_PEGAR
        for _ in range(MAX_REDIRECCIONES_ROBOTS + 1):
            if portal_prohibido(url, self._portales):
                return False
            try:
                respuesta, datos = _descargar(self._cliente, url, MAXIMO_BYTES_ROBOTS)
            except _Detencion as error:
                raise _Detencion("robots", "El archivo robots.txt del sitio es demasiado grande; " + precaucion,
                                 url=url) from error
            except httpx.TimeoutException as error:
                raise _Detencion("error_red", "El sitio no respondió a tiempo. " + MENSAJE_PEGAR, url=url) from error
            except httpx.HTTPError as error:
                raise _Detencion("error_red", f"No se pudo conectar con el sitio ({type(error).__name__}). "
                                 + MENSAJE_PEGAR, url=url) from error
            if respuesta.is_redirect:
                url = urljoin(url, respuesta.headers.get("location", ""))
                continue
            codigo = respuesta.status_code
            if 200 <= codigo < 300:
                reglas = RobotFileParser()
                reglas.parse(datos.decode("utf-8", errors="replace").splitlines())
                return reglas
            if codigo >= 500:
                raise _Detencion("error_red", f"El sitio respondió con un error (HTTP {codigo}) al consultar su archivo "
                                 "robots.txt; " + precaucion, url=url, http_estado=codigo)
            return codigo not in (401, 403)
        return False


def _decodificar(datos: bytes, tipo_contenido: str = "") -> str:
    juego = re.search(r"charset=([\w.-]+)", tipo_contenido or "", re.IGNORECASE)
    detector = UnicodeDammit(datos, known_definite_encodings=[juego.group(1)] if juego else [], is_html=True)
    return detector.unicode_markup or datos.decode("utf-8", errors="replace")


# =============================================================================
# HTML
# =============================================================================
def _cargar_json_ld(texto: str) -> Any:
    texto = (texto or "").strip()
    texto = re.sub(r"^<!--|-->$", "", texto).strip()
    texto = re.sub(r"^(?://|/\*)?\s*<!\[CDATA\[\s*(?:\*/)?", "", texto)
    texto = re.sub(r"(?://|/\*)?\s*\]\]>\s*(?:\*/)?$", "", texto).strip()
    for candidato in (texto, re.sub(r",\s*([}\]])", r"\1", texto)):
        try:
            return json.loads(candidato, strict=False)
        except json.JSONDecodeError:
            continue
    return None


def _recorrer(datos: Any) -> Iterator[dict[str, Any]]:
    if isinstance(datos, dict):
        yield datos
        for valor in datos.values():
            yield from _recorrer(valor)
    elif isinstance(datos, list):
        for valor in datos:
            yield from _recorrer(valor)


def _es_tipo(objeto: dict[str, Any], tipo: str) -> bool:
    tipos = objeto.get("@type")
    tipos = tipos if isinstance(tipos, list) else [tipos]
    return any(re.split(r"[/:#]", str(t))[-1].lower() == tipo for t in tipos if t)


def _buscar_jobposting(sopa: BeautifulSoup) -> dict[str, Any] | None:
    for script in sopa.find_all("script", attrs={"type": re.compile(r"ld\+json", re.IGNORECASE)}):
        for objeto in _recorrer(_cargar_json_ld(script.string or script.get_text())):
            if _es_tipo(objeto, "jobposting"):
                return objeto
    return None


def _texto_de(valor: Any) -> str:
    """Texto de un valor de schema.org (texto, número, lista u objeto con nombre o descripción)."""
    if valor is None or isinstance(valor, bool):
        return ""
    if isinstance(valor, (str, int, float)):
        return str(valor).strip()
    if isinstance(valor, list):
        return "; ".join(t for t in (_texto_de(v) for v in valor) if t)
    if isinstance(valor, dict):
        for clave in ("name", "@value", "description", "credentialCategory", "value"):
            if valor.get(clave):
                return _texto_de(valor[clave])
        if valor.get("monthsOfExperience"):
            return f"{valor['monthsOfExperience']} meses de experiencia"
    return ""


def _html_a_texto(fragmento: Any) -> str:
    """Convierte HTML en texto conservando los saltos de los bloques (párrafos, listas, títulos)."""
    if not isinstance(fragmento, BeautifulSoup):
        texto = _texto_de(fragmento)
        if "&lt;" in texto:
            texto = html_lib.unescape(texto)
        if "<" not in texto:
            return normalizar_texto(html_lib.unescape(texto))
        fragmento = BeautifulSoup(texto, "lxml")
    for salto in fragmento.find_all("br"):
        salto.replace_with("\n")
    for elemento in fragmento.find_all("li"):
        elemento.insert(0, "- ")
    for elemento in fragmento.find_all(ETIQUETAS_BLOQUE):
        elemento.insert_after("\n")
    return normalizar_texto(fragmento.get_text(""))


def _ubicacion(puesto: dict[str, Any]) -> str | None:
    partes: list[str] = []
    lugares = puesto.get("jobLocation")
    for lugar in lugares if isinstance(lugares, list) else [lugares]:
        direccion = lugar.get("address") if isinstance(lugar, dict) else lugar
        if isinstance(direccion, dict):
            textos = [_texto_de(direccion.get(c)) for c in ("addressLocality", "addressRegion", "addressCountry")]
        else:
            textos = [_texto_de(direccion)]
        partes += [t for t in textos if t and t not in partes]
    if str(puesto.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        partes.append("Remoto")
    requisito = _texto_de(puesto.get("applicantLocationRequirements"))
    if requisito and requisito not in partes:
        partes.append(requisito)
    return ", ".join(partes) or None


def _texto_jobposting(puesto: dict[str, Any]) -> tuple[str, str, str | None, str | None]:
    """Devuelve (texto completo, descripción, título, empresa) de un JobPosting."""
    titulo = _texto_de(puesto.get("title") or puesto.get("name")) or None
    empresa = _texto_de(puesto.get("hiringOrganization")) or None
    descripcion = _html_a_texto(puesto.get("description"))
    lineas = [titulo or ""]
    for etiqueta, valor in (("Empresa", empresa), ("Ubicación", _ubicacion(puesto)),
                            ("Tipo de empleo", _texto_de(puesto.get("employmentType"))),
                            ("Publicada", _texto_de(puesto.get("datePosted"))),
                            ("Vigente hasta", _texto_de(puesto.get("validThrough")))):
        if valor:
            lineas.append(f"{etiqueta}: {valor}")
    lineas += ["", descripcion]
    comparable = descripcion.lower()
    for etiqueta, clave in (("Requisitos", "qualifications"), ("Responsabilidades", "responsibilities"),
                            ("Habilidades", "skills"), ("Experiencia", "experienceRequirements"),
                            ("Formación", "educationRequirements")):
        valor = _html_a_texto(puesto.get(clave))
        if valor and valor.lower() not in comparable:
            lineas += ["", f"{etiqueta}:", valor]
    return normalizar_texto("\n".join(lineas)), descripcion, titulo, empresa


def _titulo_pagina(sopa: BeautifulSoup) -> str | None:
    meta = sopa.find("meta", attrs={"property": "og:title"})
    if meta and meta.get("content"):
        return normalizar_texto(meta["content"])
    for etiqueta in ("title", "h1"):
        elemento = sopa.find(etiqueta)
        if elemento and elemento.get_text(strip=True):
            return normalizar_texto(elemento.get_text(" "))
    return None


def _url_declarada(sopa: BeautifulSoup) -> str | None:
    """Dirección que declara una página guardada: canonical, og:url o el comentario «saved from url»."""
    candidatos = [enlace["href"] for enlace in sopa.find_all("link", href=True)
                  if "canonical" in [r.lower() for r in (enlace.get("rel") or [])]]
    meta = sopa.find("meta", attrs={"property": "og:url"})
    if meta and meta.get("content"):
        candidatos.append(meta["content"])
    for comentario in sopa.find_all(string=lambda t: isinstance(t, Comment)):
        guardado = re.search(r"saved from url=\(\d+\)(\S+)", comentario)
        if guardado:
            candidatos.append(guardado.group(1))
    return next((normalizar_url(c) for c in candidatos if normalizar_url(c)), None)


def _texto_principal(html: str, url: str | None) -> str:
    import trafilatura

    texto = trafilatura.extract(html, url=url, include_comments=False, include_tables=True, include_links=False,
                                include_images=False, favor_recall=True, deduplicate=False)
    return normalizar_texto(texto or "")


def _texto_visible(sopa: BeautifulSoup) -> str:
    for elemento in sopa.find_all(ETIQUETAS_RUIDO):
        elemento.decompose()
    contenedor = sopa.find("main") or sopa.find("article") or sopa.body or sopa
    return _html_a_texto(BeautifulSoup(str(contenedor), "lxml"))


def analizar_html(html: str, url: str | None, opciones: dict[str, Any], *, origen: str) -> Oferta:
    """Extrae la oferta de una página HTML ya obtenida (por URL o guardada)."""
    portales = opciones.get("portales_prohibidos", [])
    minimo = int(opciones.get("minimo_caracteres", 300))
    sopa = BeautifulSoup(html, "lxml")
    if origen == "html_guardado":
        declarada = _url_declarada(sopa)
        portal = portal_prohibido(declarada, portales)
        if portal:
            return Oferta("portal_prohibido", origen, f"La página guardada es de {portal['nombre']}. " + _mensaje_portal(portal),
                          url=declarada, dominio=dominio_de(declarada), portal=portal["nombre"])
        url = url or declarada
    base = {"url": url, "url_final": url, "dominio": dominio_de(url)}
    titulo, empresa, ubicacion = _titulo_pagina(sopa), None, None
    puesto = _buscar_jobposting(sopa)
    if puesto is not None:
        texto, descripcion, titulo_puesto, empresa = _texto_jobposting(puesto)
        titulo, ubicacion = titulo_puesto or titulo, _ubicacion(puesto)
        if len(descripcion) >= minimo:
            return Oferta("extraida", origen, "Oferta extraída de los datos estructurados de la página (JobPosting).",
                          metodo="json_ld", titulo=titulo, empresa=empresa, ubicacion=ubicacion, texto=texto, **base)
    texto, metodo = _texto_principal(html, url), "texto_principal"
    if len(texto) < minimo:
        visible = _texto_visible(sopa)
        if len(visible) > len(texto):
            texto, metodo = visible, "texto_visible"
    if len(texto) >= minimo:
        if titulo and titulo.lower() not in texto[:500].lower():
            texto = f"{titulo}\n\n{texto}"
        return Oferta("extraida", origen, "Oferta extraída del texto de la página. Revise que corresponda a la oferta: "
                                          "puede incluir texto ajeno a ella.",
                      metodo=metodo, titulo=titulo, empresa=empresa, ubicacion=ubicacion, texto=texto, **base)
    if any(marca in html.lower() for marca in MARCAS_SESION):
        return Oferta("requiere_sesion", origen, "La página pide iniciar sesión o resolver una verificación, y la app "
                                                 "no elude esos controles. " + MENSAJE_PEGAR, titulo=titulo, **base)
    return Oferta("sin_contenido", origen, "No se encontró el texto de la oferta en la página (puede que se cargue con "
                                           "JavaScript). " + MENSAJE_PEGAR, titulo=titulo, **base)


# =============================================================================
# Entradas
# =============================================================================
def _obtener(cliente: httpx.Client, url: str, opciones: dict[str, Any]) -> Oferta:
    portales = opciones.get("portales_prohibidos", [])
    maximo = int(float(opciones.get("tamano_maximo_mb", 3)) * 1024 * 1024)
    robots = _Robots(cliente, portales)
    actual = url
    for saltos in range(int(opciones.get("max_redirecciones", 5)) + 1):
        portal = portal_prohibido(actual, portales)
        if portal:
            raise _Detencion("portal_prohibido", f"La dirección redirige a {portal['nombre']}. " + _mensaje_portal(portal),
                             url=actual, portal=portal["nombre"], redirecciones=saltos)
        if not robots.permite(actual):
            raise _Detencion("robots", MENSAJE_ROBOTS, url=actual, redirecciones=saltos)
        try:
            respuesta, datos = _descargar(cliente, actual, maximo)
        except httpx.TimeoutException as error:
            raise _Detencion("error_red", "El sitio no respondió a tiempo. " + MENSAJE_PEGAR, url=actual,
                             redirecciones=saltos) from error
        except httpx.HTTPError as error:
            raise _Detencion("error_red", f"No se pudo conectar con el sitio ({type(error).__name__}). " + MENSAJE_PEGAR,
                             url=actual, redirecciones=saltos) from error
        if respuesta.is_redirect:
            actual = urljoin(actual, respuesta.headers.get("location", ""))
            if not normalizar_url(actual):
                raise _Detencion("url_invalida", "La página redirige a una dirección no válida. " + MENSAJE_PEGAR,
                                 url=actual, redirecciones=saltos + 1)
            continue
        codigo = respuesta.status_code
        if codigo in ESTADOS_BLOQUEO:
            raise _Detencion("bloqueada", f"El sitio rechazó la solicitud (HTTP {codigo}). La app no intenta eludir "
                             "bloqueos ni iniciar sesión. " + MENSAJE_PEGAR, url=actual, http_estado=codigo,
                             redirecciones=saltos)
        if codigo in (404, 410):
            raise _Detencion("no_encontrada", f"La oferta no existe o ya no está publicada (HTTP {codigo}).", url=actual,
                             http_estado=codigo, redirecciones=saltos)
        if codigo >= 400:
            raise _Detencion("error_red", f"El sitio respondió con un error (HTTP {codigo}). " + MENSAJE_PEGAR, url=actual,
                             http_estado=codigo, redirecciones=saltos)
        tipo = respuesta.headers.get("content-type", "").split(";")[0].strip().lower()
        if tipo and tipo not in TIPOS_HTML:
            raise _Detencion("no_html", f"La dirección no lleva a una página web (tipo «{tipo}»). " + MENSAJE_PEGAR,
                             url=actual, http_estado=codigo, redirecciones=saltos)
        oferta = analizar_html(_decodificar(datos, respuesta.headers.get("content-type", "")), actual, opciones,
                               origen="url")
        oferta.url, oferta.url_final, oferta.http_estado, oferta.redirecciones = url, actual, codigo, saltos
        return oferta
    raise _Detencion("demasiadas_redirecciones", "La página redirige demasiadas veces. " + MENSAJE_PEGAR, url=actual)


def obtener_oferta(url: str, opciones: dict[str, Any], *, cliente: httpx.Client | None = None) -> Oferta:
    """Intenta obtener la oferta desde su URL. Nunca lanza excepciones: el estado dice qué pasó.

    «opciones» es la sección «ofertas» de configuracion.json. «cliente» permite inyectar un cliente
    httpx (las pruebas usan un transporte falso, sin red).
    """
    direccion = normalizar_url(url)
    if direccion is None:
        return Oferta("url_invalida", "url", "La dirección no es válida: use una URL completa, por ejemplo "
                                             "https://empresa.com/empleos/123. " + MENSAJE_PEGAR, url=(url or "").strip())
    portal = portal_prohibido(direccion, opciones.get("portales_prohibidos", []))
    if portal:
        return Oferta("portal_prohibido", "url", _mensaje_portal(portal), url=direccion, dominio=dominio_de(direccion),
                      portal=portal["nombre"])
    propio = cliente is None
    cliente = cliente or crear_cliente(float(opciones.get("tiempo_espera_s", 15)))
    try:
        return _obtener(cliente, direccion, opciones)
    except _Detencion as detencion:
        return Oferta(detencion.estado, "url", detencion.mensaje, url=direccion, url_final=detencion.url,
                      dominio=dominio_de(detencion.url or direccion), portal=detencion.portal,
                      http_estado=detencion.http_estado, redirecciones=detencion.redirecciones)
    finally:
        if propio:
            cliente.close()


def desde_html(contenido: bytes | str, opciones: dict[str, Any], *, url: str | None = None) -> Oferta:
    """Extrae la oferta de una página HTML guardada (sin red)."""
    html = _decodificar(contenido) if isinstance(contenido, bytes) else contenido
    return analizar_html(html, normalizar_url(url) if url else None, opciones, origen="html_guardado")


def desde_texto(texto: str) -> Oferta:
    """Oferta pegada por el usuario."""
    limpio = normalizar_texto(texto or "")
    if len(limpio) < MINIMO_TEXTO_PEGADO:
        return Oferta("sin_contenido", "texto_pegado", f"El texto pegado es muy corto ({len(limpio)} caracteres) para "
                                                       "identificar los requisitos de la oferta.", texto=limpio or None)
    return Oferta("extraida", "texto_pegado", "Oferta tomada del texto pegado.", metodo="texto_pegado", texto=limpio)
