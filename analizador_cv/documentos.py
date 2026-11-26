"""Lectura de hojas de vida en PDF, DOCX y TXT (requisito RF01).

- PDF: pdfplumber, con detección de dos columnas por página (riesgo R4 de la definición del
  proyecto): si un hueco vertical separa dos bloques de texto, se lee primero la columna izquierda y
  después la derecha, en lugar de mezclar sus renglones. Si pdfplumber falla, se intenta con pypdf.
- DOCX: el XML del documento en orden de lectura: párrafos, tablas, cuadros de texto, encabezados
  y pies de página.
- TXT: UTF-8 (con o sin BOM), UTF-16 y, como alternativa, Windows-1252.

No hace OCR: si un PDF no tiene texto seleccionable (escaneado), lo informa y pide otro formato.
"""

from __future__ import annotations

import codecs
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from analizador_cv.utilidades import hash_texto, numero_es

FORMATOS = {".pdf": "pdf", ".docx": "docx", ".txt": "txt"}
SUGERENCIAS_FORMATO = {
    ".doc": "es un documento de Word 97-2003: ábralo en Word y guárdelo como .docx",
    ".odt": "es un documento de OpenDocument: guárdelo como .docx o expórtelo a PDF",
    ".rtf": "es un archivo RTF: guárdelo como .docx o como texto (.txt)",
    ".pages": "es un documento de Pages: expórtelo a PDF o a .docx",
    ".jpg": "es una imagen: la app no reconoce texto en imágenes (OCR); use el PDF o el DOCX original",
    ".jpeg": "es una imagen: la app no reconoce texto en imágenes (OCR); use el PDF o el DOCX original",
    ".png": "es una imagen: la app no reconoce texto en imágenes (OCR); use el PDF o el DOCX original",
}
#: Tamaño máximo del contenido descomprimido de un DOCX (protege contra archivos ZIP manipulados).
MAXIMO_DESCOMPRIMIDO_DOCX = 50 * 1024 * 1024
LIGADURAS = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}

# Detección de columnas en PDF (medidas en puntos tipográficos; 72 pt = 1 pulgada)
TOLERANCIA_RENGLON_PT = 2.5
PASO_BARRIDO_PT = 2.0
HUECO_MINIMO_PT = 10.0
MINIMO_RENGLONES_COLUMNAS = 8
MINIMO_RENGLONES_POR_LADO = 4
#: Renglones de arriba de la página que pueden cruzar el hueco sin contar en la tolerancia: el encabezado a
#: todo el ancho (nombre, título y contacto). Sin esto, una hoja de vida corta a dos columnas se leía
#: mezclando las columnas (Etapa 8: 15 de los 18 PDF a dos columnas del corpus).
MAXIMO_RENGLONES_ENCABEZADO = 4
#: Si más de esta fracción de los renglones de la derecha comparte renglón con la izquierda, es una
#: tabla (p. ej., «cargo … fechas»), no dos columnas.
MAXIMO_RENGLONES_ALINEADOS = 0.75

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"


class ErrorDocumento(Exception):
    """El archivo no se puede leer.

    tipo: «formato_no_soportado», «archivo_vacio», «demasiado_grande», «archivo_danado», «protegido»
    o «sin_texto». El mensaje está escrito para la persona usuaria.
    """

    def __init__(self, tipo: str, mensaje: str):
        super().__init__(mensaje)
        self.tipo = tipo
        self.mensaje = mensaje


@dataclass
class Documento:
    nombre: str
    formato: str
    texto: str
    metodo: str
    paginas: int | None = None
    advertencias: list[str] = field(default_factory=list)

    @property
    def caracteres(self) -> int:
        return len(self.texto)

    @property
    def palabras(self) -> int:
        return len(self.texto.split())

    @property
    def hash_texto(self) -> str:
        return hash_texto(self.texto)


@dataclass
class _Lectura:
    texto: str
    metodo: str
    paginas: int | None = None
    advertencias: list[str] = field(default_factory=list)
    tiene_imagenes: bool = False


# =============================================================================
# Texto
# =============================================================================
def normalizar_texto(texto: str) -> str:
    """Normaliza Unicode, saltos de línea y espacios, y quita caracteres de control e invisibles."""
    texto = unicodedata.normalize("NFC", texto)
    for ligadura, letras in LIGADURAS.items():
        texto = texto.replace(ligadura, letras)
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    for separador in ("\x0b", "\x0c", " ", " "):
        texto = texto.replace(separador, "\n")
    texto = texto.replace("­", "")  # guion blando
    texto = re.sub(r"[   ]", " ", texto)
    texto = "".join(c for c in texto if c in "\n\t" or not unicodedata.category(c).startswith("C"))
    lineas = [re.sub(r"[ \t]+", " ", linea).strip() for linea in texto.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lineas)).strip()


def _lista_paginas(numeros: list[int]) -> str:
    textos = [str(n) for n in numeros]
    return textos[0] if len(textos) == 1 else ", ".join(textos[:-1]) + " y " + textos[-1]


# =============================================================================
# PDF
# =============================================================================
@dataclass
class _Renglon:
    top: float
    bottom: float
    intervalos: list[tuple[float, float]]

    def cubre(self, x: float) -> bool:
        return any(a <= x <= b for a, b in self.intervalos)

    def abarca(self, x: float) -> bool:
        """x está entre la primera y la última palabra del renglón (aunque caiga en un espacio)."""
        return min(a for a, _ in self.intervalos) <= x <= max(b for _, b in self.intervalos)

    def cruza(self, hueco: tuple[float, float]) -> bool:
        """True si el renglón va a todo el ancho: una palabra pasa por el centro del hueco o el espacio entre
        las palabras de los dos lados es menor que HUECO_MINIMO_PT. Una palabra que solo toca el borde del
        hueco (el final de un renglón largo de la columna izquierda) no lo cruza."""
        corte = (hueco[0] + hueco[1]) / 2
        if any(a <= corte <= b for a, b in self.intervalos):
            return True
        izquierda = [b for a, b in self.intervalos if b < corte]
        derecha = [a for a, b in self.intervalos if a > corte]
        return bool(izquierda and derecha) and min(derecha) - max(izquierda) < HUECO_MINIMO_PT


def _agrupar_renglones(palabras: list[dict[str, Any]]) -> list[_Renglon]:
    renglones: list[_Renglon] = []
    for p in sorted(palabras, key=lambda p: (p["top"], p["x0"])):
        if renglones and abs(p["top"] - renglones[-1].top) <= TOLERANCIA_RENGLON_PT:
            renglones[-1].intervalos.append((p["x0"], p["x1"]))
            renglones[-1].bottom = max(renglones[-1].bottom, p["bottom"])
        else:
            renglones.append(_Renglon(p["top"], p["bottom"], [(p["x0"], p["x1"])]))
    return renglones


def _detectar_hueco(renglones: list[_Renglon], x0: float, x1: float) -> tuple[float, float] | None:
    """Busca un hueco vertical entre el 20 % y el 80 % del ancho que separe dos columnas de texto.

    Se toleran el encabezado de arriba (hasta MAXIMO_RENGLONES_ENCABEZADO renglones seguidos) y unos pocos
    renglones más que lo crucen (el 10 %). Primero se busca un hueco que ningún otro renglón cruce; la
    tolerancia solo se usa si no lo hay (si no, un renglón largo de la columna izquierda podía quedar
    partido por el corte).
    """
    ancho = x1 - x0
    if len(renglones) < MINIMO_RENGLONES_COLUMNAS or ancho <= 0:
        return None
    tolerancia = max(1, len(renglones) // 10)

    def cubren(x: float) -> int:
        encabezado = 0
        while (encabezado < min(MAXIMO_RENGLONES_ENCABEZADO, len(renglones))
               and renglones[encabezado].abarca(x)):
            encabezado += 1
        return sum(1 for r in renglones[encabezado:] if r.cubre(x))

    posiciones = []
    x = x0 + 0.2 * ancho
    while x <= x0 + 0.8 * ancho:
        posiciones.append((x, cubren(x)))
        x += PASO_BARRIDO_PT

    def mas_ancho(maximo: int) -> tuple[float, float] | None:
        mejor: tuple[float, float] | None = None
        inicio = ultimo = None
        for x, cantidad in posiciones + [(None, maximo + 1)]:
            if cantidad <= maximo:
                inicio = x if inicio is None else inicio
                ultimo = x
            elif inicio is not None:
                if mejor is None or ultimo - inicio > mejor[1] - mejor[0]:
                    mejor = (inicio, ultimo)
                inicio = None
        return mejor if mejor is not None and mejor[1] - mejor[0] >= HUECO_MINIMO_PT else None

    mejor = mas_ancho(0) or mas_ancho(tolerancia)
    if mejor is None:
        return None
    corte = (mejor[0] + mejor[1]) / 2
    resto = [r for r in renglones if not r.cruza(mejor)]
    izquierda = [r for r in resto if any(a < corte for a, _ in r.intervalos)]
    derecha = [r for r in resto if any(b > corte for _, b in r.intervalos)]
    if len(izquierda) < MINIMO_RENGLONES_POR_LADO or len(derecha) < MINIMO_RENGLONES_POR_LADO:
        return None
    alineados = sum(1 for r in derecha if any(a < corte for a, _ in r.intervalos))
    if alineados / len(derecha) > MAXIMO_RENGLONES_ALINEADOS:
        return None
    return mejor


def _texto_pagina(pagina: Any) -> tuple[str, bool]:
    """Texto de una página de pdfplumber y si se leyó a dos columnas."""
    x0, arriba, x1, abajo = pagina.bbox
    renglones = _agrupar_renglones(pagina.extract_words())
    hueco = _detectar_hueco(renglones, x0, x1)
    if hueco is None:
        return pagina.extract_text() or "", False
    corte = (hueco[0] + hueco[1]) / 2
    # Tramos verticales: los renglones que cruzan el hueco (a todo el ancho) separan bloques a dos columnas.
    tramos: list[tuple[bool, list[_Renglon]]] = []
    for renglon in renglones:
        cruza = renglon.cruza(hueco)
        if tramos and tramos[-1][0] == cruza:
            tramos[-1][1].append(renglon)
        else:
            tramos.append((cruza, [renglon]))
    partes = []
    for i, (cruza, grupo) in enumerate(tramos):
        sup = arriba if i == 0 else (max(r.bottom for r in tramos[i - 1][1]) + min(r.top for r in grupo)) / 2
        inf = abajo if i == len(tramos) - 1 else (max(r.bottom for r in grupo) + min(r.top for r in tramos[i + 1][1])) / 2
        sup, inf = max(arriba, min(sup, abajo)), max(arriba, min(inf, abajo))
        if inf <= sup:
            continue
        cajas = [(x0, sup, x1, inf)] if cruza else [(x0, sup, corte, inf), (corte, sup, x1, inf)]
        for caja in cajas:
            texto = pagina.crop(caja).extract_text() or ""
            if texto.strip():
                partes.append(texto)
    return "\n\n".join(partes), True


def _es_protegido(error: BaseException | None) -> bool:
    vistos = 0
    while error is not None and vistos < 5:
        nombres = " ".join(type(a).__name__ for a in (error, *getattr(error, "args", ())))
        if "Password" in nombres or "Encrypt" in nombres:
            return True
        error = error.__cause__ or error.__context__
        vistos += 1
    return False


def _leer_pdf_pdfplumber(contenido: bytes) -> _Lectura:
    import pdfplumber

    textos, columnas, sin_texto, imagenes = [], [], [], False
    with pdfplumber.open(io.BytesIO(contenido), password="") as pdf:
        for numero, pagina in enumerate(pdf.pages, start=1):
            texto, dos_columnas = _texto_pagina(pagina)
            imagenes = imagenes or bool(pagina.images)
            if not texto.strip():
                sin_texto.append(numero)
            if dos_columnas:
                columnas.append(numero)
            textos.append(texto)
        paginas = len(pdf.pages)
    metodo = "pdfplumber" + (f" (dos columnas en pág. {_lista_paginas(columnas)})" if columnas else "")
    advertencias = []
    if sin_texto and len(sin_texto) < paginas:
        advertencias.append(f"Pág. {_lista_paginas(sin_texto)} sin texto seleccionable (posiblemente imágenes): "
                            "su contenido no se analiza.")
    return _Lectura("\n\n".join(textos), metodo, paginas, advertencias, imagenes)


def _leer_pdf_pypdf(contenido: bytes) -> _Lectura:
    from pypdf import PdfReader

    lector = PdfReader(io.BytesIO(contenido))
    if lector.is_encrypted and not lector.decrypt(""):
        raise ErrorDocumento("protegido", "El PDF está protegido con contraseña. Quite la protección y vuelva a cargarlo.")
    textos = [pagina.extract_text() or "" for pagina in lector.pages]
    imagenes = any(len(pagina.images) for pagina in lector.pages)
    return _Lectura("\n\n".join(textos), "pypdf", len(lector.pages), [], imagenes)


def _leer_pdf(contenido: bytes) -> _Lectura:
    if b"%PDF-" not in contenido[:1024]:
        raise ErrorDocumento("archivo_danado", "La extensión es .pdf, pero el contenido no es un PDF.")
    try:
        return _leer_pdf_pdfplumber(contenido)
    except ErrorDocumento:
        raise
    except Exception as error:  # pdfminer lanza excepciones de muchos tipos con PDF dañados o protegidos
        if _es_protegido(error):
            raise ErrorDocumento("protegido", "El PDF está protegido con contraseña. Quite la protección y vuelva "
                                              "a cargarlo.") from error
        try:
            lectura = _leer_pdf_pypdf(contenido)
        except ErrorDocumento:
            raise
        except Exception:
            raise ErrorDocumento("archivo_danado", f"No se pudo leer el PDF ({type(error).__name__}). Puede estar "
                                                   "dañado: expórtelo de nuevo y vuelva a cargarlo.") from error
        lectura.advertencias.append(f"pdfplumber no pudo leer el PDF ({type(error).__name__}); se usó pypdf.")
        return lectura


# =============================================================================
# DOCX
# =============================================================================
def _texto_parrafo(parrafo: Any) -> str:
    """Texto propio de un párrafo, sin sus cuadros de texto (se leen como párrafos aparte)."""
    partes: list[str] = []

    def recorrer(nodo: Any) -> None:
        for hijo in nodo:
            etiqueta = hijo.tag
            if etiqueta in (W + "txbxContent", MC_FALLBACK, W + "pPr", W + "rPr"):
                continue
            if etiqueta == W + "t":
                partes.append(hijo.text or "")
            elif etiqueta == W + "tab":
                partes.append("\t")
            elif etiqueta in (W + "br", W + "cr"):
                partes.append("\n")
            elif etiqueta == W + "noBreakHyphen":
                partes.append("-")
            else:
                recorrer(hijo)

    recorrer(parrafo)
    return "".join(partes)


def _lineas_docx(elemento: Any, salida: list[str]) -> None:
    """Agrega a «salida» los renglones de un elemento en orden de lectura.

    En las tablas, una fila con una sola línea por celda se lee como «celda | celda» (tabla de datos);
    si alguna celda tiene varios párrafos, es una tabla de diseño y las celdas se leen una tras otra.
    El contenido alternativo (mc:Fallback) se omite porque repite el principal.
    """
    for hijo in elemento:
        etiqueta = hijo.tag
        if etiqueta == MC_FALLBACK:
            continue
        if etiqueta == W + "p":
            salida.append(_texto_parrafo(hijo))
            _lineas_docx(hijo, salida)  # cuadros de texto anclados en el párrafo
        elif etiqueta == W + "tr":
            celdas = []
            for celda in hijo.findall(W + "tc"):
                lineas: list[str] = []
                _lineas_docx(celda, lineas)
                celdas.append([linea for linea in lineas if linea.strip()])
            if all(len(c) <= 1 for c in celdas):
                salida.append(" | ".join(c[0] for c in celdas if c))
            else:
                for c in celdas:
                    salida.extend(c + [""])
        else:
            _lineas_docx(hijo, salida)


def _leer_docx(contenido: bytes) -> _Lectura:
    if contenido[:4] == b"\xd0\xcf\x11\xe0":
        raise ErrorDocumento("protegido", "El archivo es un documento de Word 97-2003 con extensión .docx o un .docx "
                                          "protegido con contraseña. Guárdelo como .docx sin contraseña.")
    if not zipfile.is_zipfile(io.BytesIO(contenido)):
        raise ErrorDocumento("archivo_danado", "La extensión es .docx, pero el contenido no es un documento de Word.")
    with zipfile.ZipFile(io.BytesIO(contenido)) as comprimido:
        if "word/document.xml" not in comprimido.namelist():
            raise ErrorDocumento("archivo_danado", "El archivo .docx no contiene un documento de Word.")
        if sum(info.file_size for info in comprimido.infolist()) > MAXIMO_DESCOMPRIMIDO_DOCX:
            raise ErrorDocumento("demasiado_grande", "El contenido del .docx es demasiado grande para una hoja de vida.")
    import docx

    try:
        documento = docx.Document(io.BytesIO(contenido))
    except Exception as error:
        raise ErrorDocumento("archivo_danado", f"No se pudo abrir el documento de Word ({type(error).__name__}).") from error

    def bloques(atributos: tuple[str, ...]) -> Iterator[str]:
        vistos: set[str] = set()
        for seccion in documento.sections:
            for atributo in atributos:
                parte = getattr(seccion, atributo)
                if parte.is_linked_to_previous:
                    continue
                lineas: list[str] = []
                _lineas_docx(parte._element, lineas)
                texto = "\n".join(lineas).strip()
                if texto and texto not in vistos:
                    vistos.add(texto)
                    yield texto

    cuerpo: list[str] = []
    _lineas_docx(documento.element.body, cuerpo)
    encabezados = list(bloques(("first_page_header", "header", "even_page_header")))
    pies = list(bloques(("first_page_footer", "footer", "even_page_footer")))
    texto = "\n\n".join(encabezados + ["\n".join(cuerpo)] + pies)
    imagenes = any(rel.reltype.endswith("/image") for rel in documento.part.rels.values())
    metodo = "python-docx" + (" (con encabezados o pies de página)" if encabezados or pies else "")
    return _Lectura(texto, metodo, None, [], imagenes)


# =============================================================================
# TXT
# =============================================================================
def _leer_txt(contenido: bytes) -> _Lectura:
    advertencias: list[str] = []
    if contenido.startswith(codecs.BOM_UTF8):
        texto, codificacion = contenido.decode("utf-8-sig", errors="replace"), "UTF-8"
    elif contenido.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        texto, codificacion = contenido.decode("utf-16", errors="replace"), "UTF-16"
    elif len(contenido) % 2 == 0 and contenido[1::2].count(0) > 0.4 * len(contenido) / 2:
        texto, codificacion = contenido.decode("utf-16-le", errors="replace"), "UTF-16"
    else:
        try:
            texto, codificacion = contenido.decode("utf-8"), "UTF-8"
        except UnicodeDecodeError:
            texto, codificacion = contenido.decode("cp1252", errors="replace"), "Windows-1252"
            advertencias.append("El archivo no está en UTF-8: se leyó con la codificación Windows-1252 (ANSI).")
    if "\x00" in texto:
        raise ErrorDocumento("formato_no_soportado", "El archivo .txt no parece ser texto: contiene datos binarios.")
    return _Lectura(texto, f"texto plano ({codificacion})", None, advertencias)


# =============================================================================
# Entrada
# =============================================================================
def leer_documento(nombre: str, contenido: bytes, *, tamano_maximo_mb: float = 5, minimo_caracteres: int = 200,
                   caracteres_aviso_extension: int = 20000) -> Documento:
    """Lee una hoja de vida a partir de su nombre de archivo y su contenido (bytes)."""
    extension = Path(nombre).suffix.lower()
    if extension not in FORMATOS:
        detalle = SUGERENCIAS_FORMATO.get(extension, "no es PDF, DOCX ni TXT")
        raise ErrorDocumento("formato_no_soportado", f"El archivo «{nombre}» {detalle}. Formatos admitidos: PDF, DOCX y TXT.")
    if not contenido:
        raise ErrorDocumento("archivo_vacio", f"El archivo «{nombre}» está vacío.")
    if len(contenido) > tamano_maximo_mb * 1024 * 1024:
        raise ErrorDocumento("demasiado_grande", f"El archivo pesa {numero_es(len(contenido) / 1024 / 1024, 1)} MB; "
                                                 f"el máximo es {f'{tamano_maximo_mb:g}'.replace('.', ',')} MB.")
    formato = FORMATOS[extension]
    lectura = {"pdf": _leer_pdf, "docx": _leer_docx, "txt": _leer_txt}[formato](contenido)
    texto = normalizar_texto(lectura.texto)
    utiles = len(re.sub(r"\s", "", texto))
    if utiles < minimo_caracteres:
        if formato == "pdf" and lectura.tiene_imagenes:
            raise ErrorDocumento("sin_texto", "El PDF no tiene texto seleccionable: parece escaneado o exportado como "
                                              "imagen. La app no reconoce texto en imágenes (OCR). Expórtelo de nuevo "
                                              "desde Word o Google Docs, o cargue la hoja de vida en DOCX o TXT.")
        raise ErrorDocumento("sin_texto", f"El archivo casi no tiene texto ({utiles} caracteres; el mínimo es "
                                          f"{minimo_caracteres}). Revise que sea la hoja de vida completa.")
    advertencias = list(lectura.advertencias)
    if len(texto) > caracteres_aviso_extension:
        advertencias.append(f"La hoja de vida es muy extensa ({len(texto)} caracteres): el análisis puede tardar más.")
    return Documento(Path(nombre).name, formato, texto, lectura.metodo, lectura.paginas, advertencias)


def leer_archivo(ruta: Path | str, **opciones: Any) -> Documento:
    """Atajo para leer una hoja de vida desde el disco (las opciones son las de leer_documento)."""
    ruta = Path(ruta)
    return leer_documento(ruta.name, ruta.read_bytes(), **opciones)
