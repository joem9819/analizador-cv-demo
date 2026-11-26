"""Fortalezas, debilidades y sugerencias que genera el programa (RF08 y RF09), sin modelo.

- Fortaleza: criterio con 80 % o más de su máximo y tags con impacto de 0,7 o más.
- Debilidad: criterio con 50 % o menos de su máximo, conceptos en cero y requisitos obligatorios ausentes.
- Sugerencias del programa: palabras clave de requisitos ausentes («agrégala solo si realmente la
  tienes»; nunca se insertan), requisitos solo mencionados (mostrar dónde se aplicaron), secciones
  esenciales que faltan y experiencias con fechas incompletas. Las de la línea base incluyen además
  la recomendación de cuantificar logros.
También arma los textos que reciben los prompts: el diagnóstico (mejoras) y la experiencia calculada
(evaluación específica).
"""

from __future__ import annotations

from typing import Any

from analizador_cv.analisis.compatibilidad import Cobertura
from analizador_cv.analisis.cotejo import normalizar
from analizador_cv.analisis.extraccion import Extraccion
from analizador_cv.analisis.fechas import meses_acumulados
from analizador_cv.analisis.habilidades import Diccionario
from analizador_cv.analisis.puntuacion import Puntuacion, Redaccion
from analizador_cv.analisis.temporalidad import Tag
from analizador_cv.utilidades import numero_es

UMBRAL_FORTALEZA = 0.8
UMBRAL_DEBILIDAD = 0.5
IMPACTO_DESTACADO = 0.7


def _puntos(valor: float) -> str:
    return numero_es(valor, 1)


def fortalezas_y_debilidades(puntuacion: Puntuacion, tags: list[Tag],
                             coberturas: list[Cobertura] | None) -> tuple[list[str], list[str]]:
    fortalezas, debilidades = [], []
    for criterio in puntuacion.criterios:
        texto = f"{criterio.nombre}: {_puntos(criterio.puntos)} de {criterio.maximo}"
        if criterio.proporcion >= UMBRAL_FORTALEZA:
            fortalezas.append(texto)
        elif criterio.proporcion <= UMBRAL_DEBILIDAD:
            debilidades.append(texto)
        for concepto in criterio.conceptos:
            if concepto.puntos == 0:
                debilidades.append(f"{concepto.nombre}: {concepto.detalle}")
    for tag in [t for t in tags if t.impacto >= IMPACTO_DESTACADO][:3]:
        usos = f"usado en {len(tag.usos)} {'bloque' if len(tag.usos) == 1 else 'bloques'}"
        ultimo = f", último uso {tag.ultimo_uso}" if tag.ultimo_uso else ""
        fortalezas.append(f"{tag.nombre}: impacto {numero_es(tag.impacto, 2)} ({usos}, {tag.meses} meses{ultimo})")
    for cobertura in coberturas or []:
        if cobertura.estado == "ausente" and cobertura.requisito.tipo == "obligatorio":
            debilidades.append(f"Requisito obligatorio ausente: {cobertura.requisito.texto}")
    return fortalezas, debilidades


def sugerencias_del_programa(*, extraccion: Extraccion, puntuacion: Puntuacion, coberturas: list[Cobertura] | None,
                             texto: str, diccionario: Diccionario, incluir_generales: bool) -> list[dict[str, Any]]:
    """Sugerencias deterministas, en el formato de las del modelo más el tipo «palabra_clave»."""
    sugerencias: list[dict[str, Any]] = []
    texto_norm = normalizar(texto)
    for cobertura in coberturas or []:
        requisito = cobertura.requisito
        if cobertura.estado == "ausente" and requisito.categoria in ("habilidad_tecnica", "habilidad_blanda", "idioma"):
            nombre = ", ".join(requisito.terminos) if requisito.terminos else requisito.texto
            sugerencias.append({"tipo": "palabra_clave", "fragmento_original": None, "propuesta": nombre,
                                "justificacion": f"La oferta lo pide como {requisito.tipo} ({requisito.id}) y no aparece "
                                                 "en tu hoja de vida. Agrégalo solo si realmente lo tienes."})
        elif cobertura.estado == "mencionado" and requisito.categoria in ("habilidad_tecnica", "habilidad_blanda"):
            presentes = [t for t in requisito.terminos if diccionario.en_texto(t, texto_norm)]
            if not presentes:
                continue
            nombre = ", ".join(presentes)  # solo lo que ya está en la hoja de vida
            sugerencias.append({"tipo": "estructura", "fragmento_original": cobertura.evidencia,
                                "propuesta": f"Muestra en qué experiencia, proyecto o estudio aplicaste «{nombre}».",
                                "justificacion": f"La oferta lo pide ({requisito.id}) y tu hoja de vida solo lo nombra: "
                                                 "con un ejemplo de uso cuenta como cubierto."})
    estructura = puntuacion.criterio("estructura")
    for concepto in estructura.conceptos:
        if concepto.nombre.startswith("Sección") and concepto.puntos == 0:
            seccion = concepto.nombre.removeprefix("Sección ")
            sugerencias.append({"tipo": "estructura", "fragmento_original": None,
                                "propuesta": f"Agrega la sección «{seccion}» con un encabezado propio.",
                                "justificacion": "Es una de las secciones esenciales y los filtros automáticos la buscan "
                                                 "por su encabezado."})
    for experiencia in extraccion.experiencias:
        if experiencia.periodo.precision != "mes":
            sugerencias.append({"tipo": "estructura", "fragmento_original": experiencia.titulo,
                                "propuesta": f"Escribe el mes y el año de inicio y de fin de «{experiencia.etiqueta}».",
                                "justificacion": "Con fechas completas se puede calcular cuánto tiempo y qué tan reciente "
                                                 "es tu experiencia."})
    if incluir_generales:
        cifras = puntuacion.criterio("redaccion").conceptos[-1]
        if cifras.puntos < cifras.maximo:
            sugerencias.append({"tipo": "estructura", "fragmento_original": None,
                                "propuesta": "Si los conoces, agrega resultados con cifras a tus actividades "
                                             "(cuánto, cuántos, en qué porcentaje).",
                                "justificacion": "Los logros cuantificados muestran el impacto de tu trabajo."})
    return sugerencias


def texto_diagnostico(puntuacion: Puntuacion, redaccion: Redaccion, coberturas: list[Cobertura] | None) -> str:
    """Resumen para el prompt de mejoras: puntos por criterio, conceptos débiles y requisitos sin cubrir."""
    renglones = [f"Puntuación: {_puntos(puntuacion.total)} de 100."]
    for criterio in puntuacion.criterios:
        debiles = [f"{c.nombre} ({c.detalle})" for c in criterio.conceptos if c.puntos < c.maximo * 0.75]
        renglones.append(f"- {criterio.nombre}: {_puntos(criterio.puntos)} de {criterio.maximo}"
                         + (f". A mejorar: {'; '.join(debiles)}" if debiles else ""))
    if redaccion.errores:
        renglones.append("Errores de ortografía detectados: "
                         + "; ".join(f"«{e['fragmento']}» → «{e['correccion']}»" for e in redaccion.errores))
    faltan = [c for c in coberturas or [] if c.estado != "cubierto"]
    if faltan:
        renglones.append("Requisitos de la oferta sin cubrir con evidencia (no inventes experiencia para cubrirlos): "
                         + "; ".join(f"{c.requisito.id} {c.requisito.texto} ({c.estado})" for c in faltan))
    return "\n".join(renglones)


def texto_experiencia(extraccion: Extraccion) -> str:
    """Meses de cada experiencia y total, para el prompt de evaluación específica."""
    if not extraccion.experiencias:
        return "La hoja de vida no tiene experiencias con cargo identificable."
    renglones = [f"- {e.etiqueta}: {e.periodo.texto()}" + (f", {e.periodo.meses} meses" if e.periodo.meses else "")
                 for e in extraccion.experiencias]
    total = meses_acumulados(e.periodo for e in extraccion.experiencias)
    renglones.append(f"Total sin contar dos veces los periodos que se solapan: {total} meses.")
    return "\n".join(renglones)
