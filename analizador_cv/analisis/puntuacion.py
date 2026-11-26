"""Puntuación general de 100 puntos con desglose (definición del proyecto, §6.1; RF04).

El modelo nunca entrega el puntaje: aporta niveles anclados (redacción) y datos extraídos que el
código ya verificó; los puntos salen de estas reglas fijas (versión VERSION_RUBRICA).

- Estructura y completitud (20): 3 por sección esencial (contacto, perfil, experiencia, formación y
  habilidades); extensión 3 (200 a 900 palabras; 1,5 entre 120 y 1300); texto extraíble 2.
- Redacción e impacto (25): verbos de acción 5 × nivel/3; claridad 5 × nivel/3; ortografía
  5 × nivel/3 (nivel 3 sin errores, 2 con 1 o 2, 1 con 3 o 4, 0 con 5 o más); logros cuantificados
  10 × mín(1, actividades con cifras / 3).
- Habilidades y tags con evidencia (30): 25 × impacto medio de los tags ponderado por su relevancia
  general; 5 × mín(1, habilidades técnicas / 8).
- Experiencia y temporalidad (15): fechas completas 6 (promedio por experiencia: mes y año = 1; solo
  año o solo duración = 0,5; falta el fin = 0,25; sin fechas = 0); coherencia 4 (proporción sin fechas
  imposibles); vigencia 3 (último fin hace 12 meses o menos = 3; hasta 36 = 1,5); duración
  2 × mín(1, meses acumulados / 24).
- Formación (10): título formal 4; institución 2 y fecha o estado 2 (proporción de los títulos
  formales); cursos o certificaciones 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from analizador_cv.analisis.extraccion import Extraccion
from analizador_cv.analisis.fechas import Mes, meses_acumulados, meses_entre
from analizador_cv.analisis.secciones import ESENCIALES, ETIQUETAS, Secciones
from analizador_cv.analisis.temporalidad import Tag
from analizador_cv.utilidades import cantidad, numero_es

VERSION_RUBRICA = "1"
CRITERIOS = {"estructura": ("Estructura y completitud", 20), "redaccion": ("Redacción e impacto", 25),
             "habilidades": ("Habilidades y tags con evidencia", 30), "experiencia": ("Experiencia y temporalidad", 15),
             "formacion": ("Formación", 10)}
PARAMETROS: dict[str, Any] = {
    "puntos_seccion": 3, "extension_ideal": (200, 900), "extension_aceptable": (120, 1300), "puntos_extension": 3,
    "puntos_texto_extraible": 2,
    "puntos_nivel": 5, "ortografia": ((0, 3), (2, 2), (4, 1)), "puntos_cifras": 10, "meta_cifras": 3,
    "puntos_impacto": 25, "puntos_cantidad": 5, "meta_tecnicas": 8,
    "puntos_fechas": 6, "valor_precision": {"mes": 1.0, "año": 0.5, "duracion": 0.5, "incompleta": 0.25, "sin_fecha": 0.0},
    "puntos_coherencia": 4, "puntos_vigencia": 3, "vigencia": ((12, 1.0), (36, 0.5)), "puntos_duracion": 2,
    "meta_meses": 24,
    "puntos_titulo": 4, "puntos_institucion": 2, "puntos_fecha_formacion": 2, "puntos_cursos": 2,
}


@dataclass
class Redaccion:
    """Insumos del criterio de redacción. Los da el modelo (niveles anclados) o la línea base (heurística)."""

    verbos_accion: int
    claridad: int
    errores: list[dict[str, str]] = field(default_factory=list)  # solo los verificados en el texto
    evidencia: dict[str, list[str]] = field(default_factory=dict)
    fuente: str = "modelo"
    ortografia_evaluada: bool = True
    errores_descartados: int = 0

    @property
    def nivel_ortografia(self) -> int:
        if not self.ortografia_evaluada:
            return 2
        for limite, nivel in PARAMETROS["ortografia"]:
            if len(self.errores) <= limite:
                return nivel
        return 0

    def a_dict(self) -> dict[str, Any]:
        return {"verbos_accion": self.verbos_accion, "claridad": self.claridad, "nivel_ortografia": self.nivel_ortografia,
                "ortografia_evaluada": self.ortografia_evaluada, "errores": [dict(e) for e in self.errores],
                "errores_descartados": self.errores_descartados, "evidencia": {k: list(v) for k, v in self.evidencia.items()},
                "fuente": self.fuente}


@dataclass
class Concepto:
    nombre: str
    puntos: float
    maximo: float
    detalle: str

    def a_dict(self) -> dict[str, Any]:
        return {"nombre": self.nombre, "puntos": round(self.puntos, 2), "maximo": self.maximo, "detalle": self.detalle}


@dataclass
class Criterio:
    id: str
    conceptos: list[Concepto]

    @property
    def nombre(self) -> str:
        return CRITERIOS[self.id][0]

    @property
    def maximo(self) -> int:
        return CRITERIOS[self.id][1]

    @property
    def puntos(self) -> float:
        return round(sum(c.puntos for c in self.conceptos), 2)

    @property
    def proporcion(self) -> float:
        return self.puntos / self.maximo

    def a_dict(self) -> dict[str, Any]:
        return {"id": self.id, "nombre": self.nombre, "puntos": self.puntos, "maximo": self.maximo,
                "conceptos": [c.a_dict() for c in self.conceptos]}


@dataclass
class Puntuacion:
    criterios: list[Criterio]

    @property
    def total(self) -> float:
        return round(sum(c.puntos for c in self.criterios), 1)

    def criterio(self, identificador: str) -> Criterio:
        return next(c for c in self.criterios if c.id == identificador)

    def a_dict(self) -> dict[str, Any]:
        return {"total": self.total, "maximo": 100, "version_rubrica": VERSION_RUBRICA,
                "criterios": [c.a_dict() for c in self.criterios]}


def _n(valor: float, decimales: int = 1) -> str:
    return numero_es(valor, decimales)



def criterio_estructura(secciones: Secciones, extraccion: Extraccion, palabras: int, *,
                        problema_extraccion: bool) -> Criterio:
    p = PARAMETROS
    por_contenido = {"perfil": bool(extraccion.perfil), "experiencia": bool(extraccion.experiencias),
                     "formacion": bool(extraccion.formacion), "habilidades": bool(extraccion.habilidades), "contacto": False}
    conceptos = []
    for seccion in ESENCIALES:
        if seccion in secciones.presentes:
            detalle = "presente" if seccion == "contacto" else "presente (encabezado)"
            puntos = p["puntos_seccion"]
        elif por_contenido[seccion]:
            detalle, puntos = "presente (contenido sin encabezado)", p["puntos_seccion"]
        else:
            detalle, puntos = "no se encontró", 0
        conceptos.append(Concepto(f"Sección {ETIQUETAS[seccion]}", puntos, p["puntos_seccion"], detalle))
    ideal, aceptable = p["extension_ideal"], p["extension_aceptable"]
    if ideal[0] <= palabras <= ideal[1]:
        puntos = p["puntos_extension"]
    elif aceptable[0] <= palabras <= aceptable[1]:
        puntos = p["puntos_extension"] / 2
    else:
        puntos = 0
    conceptos.append(Concepto("Extensión", puntos, p["puntos_extension"],
                              f"{palabras} palabras (recomendado: {ideal[0]} a {ideal[1]})"))
    conceptos.append(Concepto("Texto extraíble", 0 if problema_extraccion else p["puntos_texto_extraible"],
                              p["puntos_texto_extraible"],
                              "hay páginas sin texto seleccionable" if problema_extraccion else "todo el texto se pudo leer"))
    return Criterio("estructura", conceptos)


def criterio_redaccion(redaccion: Redaccion, extraccion: Extraccion) -> Criterio:
    p = PARAMETROS
    actividades = [a for b in extraccion.experiencias + extraccion.proyectos for a in b.actividades]
    con_cifras = sum(a.cuantificada for a in actividades)
    ortografia = (cantidad(len(redaccion.errores), "error verificado", "errores verificados") if redaccion.ortografia_evaluada
                  else "no evaluada por la línea base: nivel neutro 2")
    return Criterio("redaccion", [
        Concepto("Verbos de acción", p["puntos_nivel"] * redaccion.verbos_accion / 3, p["puntos_nivel"],
                 f"nivel {redaccion.verbos_accion} de 3"),
        Concepto("Claridad", p["puntos_nivel"] * redaccion.claridad / 3, p["puntos_nivel"],
                 f"nivel {redaccion.claridad} de 3"),
        Concepto("Ortografía", p["puntos_nivel"] * redaccion.nivel_ortografia / 3, p["puntos_nivel"],
                 f"nivel {redaccion.nivel_ortografia} de 3 ({ortografia})"),
        Concepto("Logros cuantificados", p["puntos_cifras"] * min(1.0, con_cifras / p["meta_cifras"]), p["puntos_cifras"],
                 f"{con_cifras} de {cantidad(len(actividades), 'actividad', 'actividades')} con cifras (meta: {p['meta_cifras']})"),
    ])


def impacto_medio(tags: list[Tag]) -> float:
    """Impacto medio ponderado por la relevancia general de cada tag."""
    pesos = sum(t.relevancia_general for t in tags)
    return sum(t.relevancia_general * t.impacto for t in tags) / pesos if pesos else 0.0


def criterio_habilidades(tags: list[Tag]) -> Criterio:
    p = PARAMETROS
    medio = impacto_medio(tags)
    tecnicas = sum(t.tipo == "tecnica" for t in tags)
    con_evidencia = sum(t.evidencia > 0.3 for t in tags)
    return Criterio("habilidades", [
        Concepto("Impacto de los tags", p["puntos_impacto"] * medio, p["puntos_impacto"],
                 f"impacto medio {_n(medio, 2)} en {len(tags)} tags; {con_evidencia} con uso en experiencias o proyectos"),
        Concepto("Cantidad de habilidades técnicas", p["puntos_cantidad"] * min(1.0, tecnicas / p["meta_tecnicas"]),
                 p["puntos_cantidad"], f"{cantidad(tecnicas, 'técnica', 'técnicas')} (meta: {p['meta_tecnicas']})"),
    ])


def criterio_experiencia(extraccion: Extraccion, referencia: Mes) -> Criterio:
    p = PARAMETROS
    experiencias = extraccion.experiencias
    if not experiencias:
        sin = "no hay experiencias"
        return Criterio("experiencia", [Concepto("Fechas completas", 0, p["puntos_fechas"], sin),
                                        Concepto("Coherencia cronológica", 0, p["puntos_coherencia"], sin),
                                        Concepto("Vigencia", 0, p["puntos_vigencia"], sin),
                                        Concepto("Duración", 0, p["puntos_duracion"], sin)])
    valores = [p["valor_precision"][e.periodo.precision] for e in experiencias]
    completas = sum(v == 1.0 for v in valores)
    incoherentes = [e for e in experiencias if any("posterior" in x or "anterior" in x for x in e.periodo.problemas)]
    ubicadas = [e for e in experiencias if e.periodo.ubicado]
    if ubicadas:
        ultimo = max(e.periodo.fin for e in ubicadas)
        transcurridos = meses_entre(ultimo, referencia)
        vigencia = next((v for limite, v in p["vigencia"] if transcurridos <= limite), 0.0)
        detalle_vigencia = f"último fin: {ultimo} (hace {transcurridos} meses)"
    else:
        vigencia, detalle_vigencia = 0.0, "ninguna experiencia tiene fechas"
    meses = meses_acumulados(e.periodo for e in experiencias)
    return Criterio("experiencia", [
        Concepto("Fechas completas", p["puntos_fechas"] * sum(valores) / len(valores), p["puntos_fechas"],
                 f"{completas} de {cantidad(len(experiencias), 'experiencia', 'experiencias')} con mes y año de inicio y fin"),
        Concepto("Coherencia cronológica", p["puntos_coherencia"] * (1 - len(incoherentes) / len(experiencias)),
                 p["puntos_coherencia"],
                 "sin fechas imposibles" if not incoherentes else
                 f"{len(incoherentes)} con fechas imposibles: " + "; ".join(e.etiqueta for e in incoherentes)),
        Concepto("Vigencia", p["puntos_vigencia"] * vigencia, p["puntos_vigencia"], detalle_vigencia),
        Concepto("Duración", p["puntos_duracion"] * min(1.0, meses / p["meta_meses"]), p["puntos_duracion"],
                 f"{meses} meses acumulados sin solapamientos (meta: {p['meta_meses']})"),
    ])


def criterio_formacion(extraccion: Extraccion) -> Criterio:
    p = PARAMETROS
    formales = [b for b in extraccion.formacion if b.tipo == "formal"]
    cursos = [b for b in extraccion.formacion if b.tipo in ("curso", "certificacion")]
    con_institucion = sum(bool(b.entidad) for b in formales)
    con_fecha = sum(b.periodo.precision != "sin_fecha" or b.estado in ("completo", "en_curso") for b in formales)
    total = len(formales) or 1
    return Criterio("formacion", [
        Concepto("Título formal", p["puntos_titulo"] if formales else 0, p["puntos_titulo"],
                 cantidad(len(formales), "título formal", "títulos formales") if formales else "no se encontró un título formal"),
        Concepto("Institución", p["puntos_institucion"] * con_institucion / total if formales else 0,
                 p["puntos_institucion"], f"{con_institucion} de {len(formales)} con institución"),
        Concepto("Fecha o estado", p["puntos_fecha_formacion"] * con_fecha / total if formales else 0,
                 p["puntos_fecha_formacion"], f"{con_fecha} de {len(formales)} con fecha o estado (en curso o completo)"),
        Concepto("Cursos o certificaciones", p["puntos_cursos"] if cursos else 0, p["puntos_cursos"],
                 cantidad(len(cursos), "curso o certificación", "cursos o certificaciones")),
    ])


def calcular_puntuacion(*, secciones: Secciones, extraccion: Extraccion, redaccion: Redaccion, tags: list[Tag],
                        palabras: int, referencia: Mes, problema_extraccion: bool = False) -> Puntuacion:
    return Puntuacion([
        criterio_estructura(secciones, extraccion, palabras, problema_extraccion=problema_extraccion),
        criterio_redaccion(redaccion, extraccion),
        criterio_habilidades(tags),
        criterio_experiencia(extraccion, referencia),
        criterio_formacion(extraccion),
    ])
