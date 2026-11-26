"""Detección determinista de las secciones de una hoja de vida (criterio «Estructura y completitud»).

Un renglón es un encabezado si, sin adornos («#», «-», «:»…), es uno de los nombres de sección
conocidos, o si empieza con uno de ellos y está en MAYÚSCULAS o termina en «:». También se reconoce
un encabezado con contenido en el mismo renglón («Habilidades: Python, SQL»).
El contacto se detecta por su contenido: correo, teléfono, perfil web o las marcas del enmascaramiento.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from analizador_cv.analisis.cotejo import normalizar
from analizador_cv.datos_personales import PATRONES

NOMBRES_SECCION: dict[str, tuple[str, ...]] = {
    "perfil": ("perfil", "perfil profesional", "perfil laboral", "resumen", "resumen profesional", "acerca de mi",
               "sobre mi", "objetivo", "objetivo profesional", "objetivo laboral", "presentacion", "extracto"),
    "experiencia": ("experiencia", "experiencia laboral", "experiencia profesional", "experiencia relevante",
                    "historial laboral", "trayectoria", "trayectoria profesional", "trayectoria laboral"),
    "formacion": ("formacion", "formacion academica", "formacion profesional", "educacion", "educacion formal",
                  "estudios", "estudios realizados", "informacion academica"),
    "habilidades": ("habilidades", "habilidades tecnicas", "habilidades blandas", "competencias",
                    "competencias tecnicas", "conocimientos", "conocimientos tecnicos", "aptitudes", "tecnologias",
                    "herramientas", "stack tecnologico", "skills"),
    "proyectos": ("proyectos", "proyectos personales", "proyectos academicos", "proyectos destacados", "portafolio"),
    "certificaciones": ("certificaciones", "certificados", "cursos", "cursos y certificaciones", "cursos complementarios",
                        "formacion complementaria", "educacion complementaria", "educacion continua"),
    "idiomas": ("idiomas", "idioma", "lenguas"),
    "referencias": ("referencias", "referencias personales", "referencias laborales"),
    "contacto": ("contacto", "datos de contacto", "informacion de contacto", "datos personales", "informacion personal"),
}
#: Secciones que exige la rúbrica (§6.1).
ESENCIALES = ("contacto", "perfil", "experiencia", "formacion", "habilidades")
ETIQUETAS = {"contacto": "contacto", "perfil": "perfil", "experiencia": "experiencia", "formacion": "formación",
             "habilidades": "habilidades", "proyectos": "proyectos", "certificaciones": "certificaciones",
             "idiomas": "idiomas", "referencias": "referencias"}

_ADORNOS = " \t#*=_|>-:.•·▪●"
_MARCAS_CONTACTO = re.compile(r"\[(?:CORREO|TELEFONO|PERFIL_WEB)_\d+\]")
_PERFIL_PROFESIONAL = re.compile(r"(?:linkedin\.com/in/|github\.com/|gitlab\.com/)", re.IGNORECASE)
_VARIANTES = sorted(((v, s) for s, vs in NOMBRES_SECCION.items() for v in vs), key=lambda par: len(par[0]), reverse=True)


@dataclass
class Encabezado:
    seccion: str
    renglon: int
    inicio: int  # posición del renglón en el texto
    en_linea: bool  # el contenido sigue en el mismo renglón


@dataclass
class Secciones:
    encabezados: list[Encabezado] = field(default_factory=list)
    contacto: bool = False
    bloques: dict[str, str] = field(default_factory=dict)

    @property
    def presentes(self) -> set[str]:
        return {e.seccion for e in self.encabezados} | ({"contacto"} if self.contacto else set())

    def inicios(self) -> list[int]:
        """Posiciones de los encabezados que abren una sección (no los que llevan el contenido en el renglón)."""
        return sorted(e.inicio for e in self.encabezados if not e.en_linea)


def _encabezado(renglon: str) -> tuple[str, bool] | None:
    limpio = normalizar(renglon).strip(_ADORNOS)
    if not limpio or len(limpio) > 60:
        return None
    for variante, seccion in _VARIANTES:
        if limpio == variante:
            return seccion, False
    for variante, seccion in _VARIANTES:
        if re.match(rf"{re.escape(variante)}\s*:\s*\S", limpio):
            return seccion, True
    letras = [c for c in renglon if c.isalpha()]
    mayusculas = bool(letras) and all(c.isupper() for c in letras)
    if mayusculas or renglon.rstrip().endswith(":"):
        for variante, seccion in _VARIANTES:
            if re.match(rf"{re.escape(variante)}(?![a-z])", limpio) and len(limpio.split()) <= 6:
                return seccion, False
    return None


def tiene_contacto(texto: str) -> bool:
    if _MARCAS_CONTACTO.search(texto) or _PERFIL_PROFESIONAL.search(texto):
        return True
    return any(p.search(texto) for categoria, p, _, _ in PATRONES if categoria in ("correo", "telefono"))


def detectar_secciones(texto: str) -> Secciones:
    """Encabezados, presencia de contacto y texto de cada sección.

    Un encabezado con el contenido en el mismo renglón («Tecnologías: Python, Django») cuenta como
    presencia de la sección, pero no corta la sección en la que está (puede estar dentro de una experiencia).
    """
    resultado = Secciones(contacto=tiene_contacto(texto))
    renglones = texto.split("\n")
    posicion = 0
    for numero, renglon in enumerate(renglones):
        encontrado = _encabezado(renglon)
        if encontrado:
            resultado.encabezados.append(Encabezado(encontrado[0], numero, posicion, encontrado[1]))
        posicion += len(renglon) + 1

    def agregar(seccion: str, contenido: str) -> None:
        contenido = contenido.strip()
        if contenido:
            anterior = resultado.bloques.get(seccion)
            resultado.bloques[seccion] = f"{anterior}\n{contenido}" if anterior else contenido

    abren = [e for e in resultado.encabezados if not e.en_linea]
    for i, encabezado in enumerate(abren):
        fin = abren[i + 1].renglon if i + 1 < len(abren) else len(renglones)
        agregar(encabezado.seccion, "\n".join(renglones[encabezado.renglon + 1:fin]))
    for encabezado in resultado.encabezados:
        if encabezado.en_linea:
            agregar(encabezado.seccion, renglones[encabezado.renglon].split(":", 1)[1])
    return resultado
