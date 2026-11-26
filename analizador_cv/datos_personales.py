"""Enmascaramiento opcional de datos personales antes de enviar una hoja de vida a una API (RNF01).

Reemplaza cada dato por una marca numerada ([CORREO_1], [TELEFONO_1]…) y guarda la equivalencia
para restaurar los datos en la versión mejorada, solo en este computador. No toca las fechas de la
experiencia ni las duraciones, que hacen falta para el análisis.

Categorías (se aplican en este orden):
- nombre: los nombres que indique el usuario (sin distinguir mayúsculas ni tildes);
- correo: direcciones de correo electrónico;
- perfil_web: perfiles de LinkedIn, Facebook, Instagram, X/Twitter y TikTok (GitHub y portafolios no,
  porque son evidencia de las habilidades);
- datos_sensibles: lo que sigue a «Fecha de nacimiento», «Lugar de nacimiento», «Edad», «Estado civil»,
  «Nacionalidad», «Género» o «Sexo»;
- documento: números con etiqueta de documento (C.C., cédula, T.I., pasaporte, NIT…);
- direccion: lo que sigue a «Dirección:» y la nomenclatura colombiana (Calle 10 # 43-21);
- telefono: números con etiqueta (Tel., Cel., Móvil, WhatsApp), celulares de Colombia (3xx), fijos
  (60x) y números con prefijo internacional.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CATEGORIAS = ("nombre", "correo", "perfil_web", "datos_sensibles", "documento", "direccion", "telefono")
MARCAS = {"nombre": "NOMBRE", "correo": "CORREO", "perfil_web": "PERFIL_WEB", "datos_sensibles": "DATO_SENSIBLE",
          "documento": "DOCUMENTO", "direccion": "DIRECCION", "telefono": "TELEFONO"}

_I = re.IGNORECASE
_IM = re.IGNORECASE | re.MULTILINE

#: (categoría, patrón, grupo que se reemplaza, mínimo de dígitos del valor)
PATRONES: list[tuple[str, re.Pattern[str], int, int]] = [
    ("correo", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), 0, 0),
    ("perfil_web", re.compile(r"(?<![\w.-])(?:https?://)?(?:[\w-]+\.)?(?:linkedin\.com/(?:in|pub)/|facebook\.com/|"
                              r"instagram\.com/|(?:twitter|x)\.com/|tiktok\.com/@)[^\s,;)]+", _I), 0, 0),
    ("datos_sensibles", re.compile(r"\b(?:fecha|lugar)\s+de\s+nacimiento\s*[:\-]\s*([^\n|]+)", _IM), 1, 0),
    ("datos_sensibles", re.compile(r"\b(?:edad|estado\s+civil|nacionalidad|g[ée]nero|sexo)\s*[:\-]\s*([^\n|]+)", _IM), 1, 0),
    ("documento", re.compile(r"(?<!\w)(?:c\.?\s?c\.?|c[ée]dula(?:\s+de\s+ciudadan[íi]a)?|documento(?:\s+de\s+identidad)?|"
                             r"t\.\s?i\.|tarjeta\s+de\s+identidad|c\.\s?e\.|c[ée]dula\s+de\s+extranjer[íi]a|pasaporte|"
                             r"nit|dni|rut)\s*(?:n[°ºo]\.?|no\.?|n[úu]mero)?\s*[:.]?\s*"
                             r"(\d[\d.\s-]{4,14}\d|[A-Z]{1,3}\d{5,10})(?!\d)", _I), 1, 6),
    ("direccion", re.compile(r"\b(?:direcci[óo]n(?:\s+de\s+residencia)?|domicilio|residencia)\s*[:\-]\s*([^\n|]+)", _IM), 1, 0),
    ("direccion", re.compile(r"\b(?:calle|cl\.?|carrera|cra\.?|kr\.?|cr\.?|avenida|av\.?|diagonal|dg\.?|transversal|"
                             r"tv\.?|circular|cq\.?)\s*\d+\s*[a-z]?\s*(?:bis\s*)?(?:sur\s*)?(?:#|n[°ºo]\.?|no\.?|n[úu]mero)"
                             r"\s*\d+\s*[a-z]?\s*-\s*\d+(?:\s*(?:sur|este))?", _I), 0, 0),
    # Con etiqueta exige «:» (o «Tel.»/«Cel.») para no confundir «desarrollo móvil 2019 - 2021» con un teléfono.
    ("telefono", re.compile(r"\b(?:(?:tel(?:[ée]fono)?|cel(?:ular)?|m[óo]vil|whats?app)(?:\s+(?:fijo|m[óo]vil))?\s*:|"
                            r"(?:tel|cel)\.)\s*(\+?[\d(][\d\s().-]{5,18}\d)(?!\d)", _I), 1, 7),
    ("telefono", re.compile(r"(?<![\w+])(?:\+?57[\s.-]?)?\(?3\d{2}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"), 0, 10),
    ("telefono", re.compile(r"(?<![\w+])(?:\+?57[\s.-]?)?\(?60\d\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"), 0, 10),
    ("telefono", re.compile(r"(?<!\w)\+\d{1,3}[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,4}){2,4}(?!\d)"), 0, 8),
]

_VOCALES = {"a": "aáàäâ", "e": "eéèëê", "i": "iíìïî", "o": "oóòöô", "u": "uúùüû", "n": "nñ"}


@dataclass
class TextoEnmascarado:
    texto: str
    #: marca → valor original (no sale de este computador)
    equivalencias: dict[str, str] = field(default_factory=dict)

    @property
    def conteo(self) -> dict[str, int]:
        """Datos distintos enmascarados por categoría."""
        conteo = {c: 0 for c in CATEGORIAS}
        for marca in self.equivalencias:
            prefijo = marca[1:].rsplit("_", 1)[0]
            categoria = next(c for c, m in MARCAS.items() if m == prefijo)
            conteo[categoria] += 1
        return conteo

    @property
    def total(self) -> int:
        return len(self.equivalencias)


def _patron_nombre(nombre: str) -> re.Pattern[str]:
    """Patrón que reconoce el nombre sin distinguir mayúsculas ni tildes y con cualquier espacio intermedio."""
    partes = []
    for caracter in nombre.strip().lower():
        base = next((b for b, variantes in _VOCALES.items() if caracter in variantes), None)
        if caracter.isspace():
            partes.append(r"\s+")
        elif base:
            partes.append(f"[{_VOCALES[base]}]")
        else:
            partes.append(re.escape(caracter))
    return re.compile(r"(?<!\w)" + re.sub(r"(\\s\+)+", r"\\s+", "".join(partes)) + r"(?!\w)", _I)


def enmascarar(texto: str, *, categorias: tuple[str, ...] | list[str] = CATEGORIAS,
               nombres: tuple[str, ...] | list[str] = ()) -> TextoEnmascarado:
    """Reemplaza los datos personales de las categorías indicadas por marcas numeradas."""
    desconocidas = set(categorias) - set(CATEGORIAS)
    if desconocidas:
        raise ValueError(f"Categorías desconocidas: {', '.join(sorted(desconocidas))}")
    resultado = TextoEnmascarado(texto)
    por_valor: dict[tuple[str, str], str] = {}
    contadores = {c: 0 for c in CATEGORIAS}

    def marca_para(categoria: str, valor: str) -> str:
        # Clave exacta: dos grafías del mismo dato reciben marcas distintas para que restaurar() las devuelva tal cual.
        clave = (categoria, valor)
        if clave not in por_valor:
            contadores[categoria] += 1
            marca = f"[{MARCAS[categoria]}_{contadores[categoria]}]"
            por_valor[clave] = marca
            resultado.equivalencias[marca] = valor
        return por_valor[clave]

    def aplicar(categoria: str, patron: re.Pattern[str], grupo: int, minimo_digitos: int) -> None:
        def reemplazar(coincidencia: re.Match[str]) -> str:
            completo = coincidencia.group(0)
            valor = coincidencia.group(grupo)
            limpio = valor.rstrip(" ,;.\t")
            if not limpio or sum(c.isdigit() for c in limpio) < minimo_digitos:
                return completo
            inicio = coincidencia.start(grupo) - coincidencia.start()
            return completo[:inicio] + marca_para(categoria, limpio) + completo[inicio + len(limpio):]

        resultado.texto = patron.sub(reemplazar, resultado.texto)

    if "nombre" in categorias:
        for nombre in sorted({n.strip() for n in nombres if len(n.strip()) >= 3}, key=len, reverse=True):
            aplicar("nombre", _patron_nombre(nombre), 0, 0)
    for categoria, patron, grupo, minimo in PATRONES:
        if categoria in categorias:
            aplicar(categoria, patron, grupo, minimo)
    return resultado


def restaurar(texto: str, equivalencias: dict[str, str]) -> str:
    """Devuelve los datos originales a un texto con marcas (por ejemplo, la hoja de vida mejorada)."""
    for marca, valor in equivalencias.items():
        texto = texto.replace(marca, valor)
    return texto
