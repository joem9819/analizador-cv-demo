"""Resumen en Markdown de la evaluación técnica (resultados/evaluacion/informe/resumen.md).

Reúne las cifras principales de cada pregunta e hipótesis a partir de las tablas que calculó informe.py.
Los números del informe de investigación se toman de los CSV; este resumen sirve para revisarlos.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from analizador_cv.utilidades import ahora_iso

_VARIANTES = {"linea_base": "línea base", "modelo": "modelo (sin diccionario)", "modelo_y_diccionario": "modelo + diccionario"}
_MEDIDAS = {"habilidades_f1": "F1 de habilidades", "experiencias_fechas_f1": "F1 de experiencias con fechas",
            "formacion_f1": "F1 de formación", "puntuacion": "puntuación general", "compatibilidad": "compatibilidad (%)"}


def _n(valor: Any, decimales: int = 2) -> str:
    """Número con coma decimal; «—» si falta."""
    if valor is None or valor == "":
        return "—"
    if isinstance(valor, bool):
        return "sí" if valor else "no"
    if isinstance(valor, int):
        return f"{valor:,}".replace(",", " ")
    return f"{valor:.{decimales}f}".replace(".", ",")


def _p(valor: Any) -> str:
    if valor is None:
        return "—"
    return "< 0,001" if valor < 0.001 else _n(valor, 3)


def _tabla(encabezados: list[str], filas: list[list[str]]) -> list[str]:
    if not filas:
        return ["_Sin datos todavía._", ""]
    return (["| " + " | ".join(encabezados) + " |", "|" + "---|" * len(encabezados)]
            + ["| " + " | ".join(f) + " |" for f in filas] + [""])


def escribir_resumen(evaluacion, ruta: Path) -> Path:
    t = evaluacion.tablas
    etiqueta = {c.nombre: c.etiqueta for c in evaluacion.corridas}
    lineas = ["# Evaluación técnica: resumen de resultados", "",
              f"Generado el {ahora_iso()[:16].replace('T', ' ')} con `python -m analizador_cv evaluar`. "
              "Las cifras salen de los CSV de esta carpeta; el corpus y la verdad de referencia están en "
              "`datos/corpus/` (definición del proyecto, §4 y §9).", ""]
    if any(c.metodo == "simulado" for c in evaluacion.corridas):
        lineas += ["> **La corrida «simulado» NO es un resultado de investigación.** Usa reglas en lugar de un modelo "
                   "para verificar que la evaluación funciona de punta a punta y para estimar la cuota de la corrida real. "
                   "Sus tokens son estimados y su latencia es 0.", ""]

    lineas += ["## Corridas", ""]
    lineas += _tabla(["Corrida", "Método", "Diseño", "Estado", "Unidades hechas"],
                     [[etiqueta[c.nombre], c.metodo, c.info.get("diseno", "—"), c.info.get("estado", "—"),
                       f"{c.info.get('unidades_hechas', '—')} de {c.info.get('unidades', '—')}"] for c in evaluacion.corridas])

    lineas += ["## P1 y H1. Exactitud de la extracción (TXT)", "",
               "F1 micro (todos los documentos juntos); «F1 medio» es el promedio por documento con su IC 95 % (bootstrap). "
               "EAM = error absoluto medio de los meses por habilidad con uso fechado en la referencia.", ""]
    filas = [f for f in t["extraccion_resumen"] if f["formato"] == "txt"]
    lineas += _tabla(["Corrida", "Variante", "Docs", "Habilidades F1", "F1 medio [IC 95 %]", "Recall tecnologías",
                      "Experiencias F1", "Con fechas F1", "Formación F1", "EAM meses", "Exactos"],
                     [[etiqueta[f["corrida"]], _VARIANTES[f["variante"]], _n(f["documentos"]), _n(f["habilidades_f1"], 3),
                       f"{_n(f['habilidades_f1_medio_por_documento'], 3)} [{_n(f['habilidades_f1_ic95_bajo'], 3)}; "
                       f"{_n(f['habilidades_f1_ic95_alto'], 3)}]", _n(f["habilidades_recall_tecnologia_medio"], 3),
                       _n(f["experiencias_f1"], 3), _n(f["experiencias_fechas_f1"], 3), _n(f["formacion_f1"], 3),
                       _n(f["temporalidad_eam_fechados"]), _n(f["temporalidad_exactos_fechados"], 3)] for f in filas])
    if t["h1_comparacion"]:
        lineas += ["**H1 (modelo frente a línea base, emparejado por documento):**", ""]
        lineas += _tabla(["Corrida", "Variante", "Medida", "n", "Línea base", "Modelo", "Diferencia [IC 95 %]", "p (Wilcoxon, unilateral)"],
                         [[etiqueta[f["corrida"]], _VARIANTES[f["variante"]], _MEDIDAS[f["medida"]], _n(f["n"]), _n(f["media_a"], 3),
                           _n(f["media_b"], 3), f"{_n(f['diferencia_media'], 3)} [{_n(f['ic95_bajo'], 3)}; {_n(f['ic95_alto'], 3)}]",
                           _p(f["wilcoxon_p_unilateral"])] for f in t["h1_comparacion"]])

    lineas += ["## P1. Invarianza al formato", ""]
    otros = [f for f in t["extraccion_resumen"] if f["formato"] != "txt"]
    lineas += _tabla(["Corrida", "Variante", "Formato", "Docs", "Habilidades F1", "Experiencias con fechas F1", "Formación F1"],
                     [[etiqueta[f["corrida"]], _VARIANTES[f["variante"]], f["formato"].upper(), _n(f["documentos"]),
                       _n(f["habilidades_f1"], 3), _n(f["experiencias_fechas_f1"], 3), _n(f["formacion_f1"], 3)] for f in otros])
    for corrida in evaluacion.corridas:
        propias = [f for f in t["formatos"] if f["corrida"] == corrida.nombre]
        if propias:
            iguales = sum(f["diferencia_max_puntuacion"] == 0 for f in propias)
            mayor = max(f["diferencia_max_puntuacion"] for f in propias)
            lineas.append(f"- {etiqueta[corrida.nombre]}: {iguales} de {len(propias)} hojas de vida con la misma puntuación "
                          f"en TXT, DOCX y PDF; la mayor diferencia fue de {_n(mayor)} puntos.")
    lineas.append("")

    lineas += ["## P2 y H2. Validez del orden (ρ de Spearman con el nivel previsto)", ""]
    lineas += _tabla(["Corrida", "Oferta", "Vacante", "n", "ρ compatibilidad", "p", "ρ puntuación", "p",
                      "Compat. alto", "Compat. medio", "Compat. bajo"],
                     [[etiqueta[f["corrida"]], f["version_oferta"], f["vacante"], _n(f["n"]), _n(f["rho_compatibilidad"], 3),
                       _p(f["p_unilateral_compatibilidad"]), _n(f["rho_puntuacion"], 3), _p(f["p_unilateral_puntuacion"]),
                       _n(f["compatibilidad_media_alto"], 1), _n(f["compatibilidad_media_medio"], 1),
                       _n(f["compatibilidad_media_bajo"], 1)] for f in t["h2_validez_orden"]])
    if t["estabilidad"]:
        lineas += ["**Estabilidad entre repeticiones:**", ""]
        lineas += _tabla(["Corrida", "Documento", "Nivel", "Puntuaciones", "DE", "Compatibilidades", "DE", "Requisitos iguales"],
                         [[etiqueta[f["corrida"]], f["documento"], f["nivel"], " / ".join(_n(v, 1) for v in f["puntuaciones"]),
                           _n(f["de_puntuacion"]), " / ".join(_n(v, 1) for v in f["compatibilidades"]), _n(f["de_compatibilidad"]),
                           f"{f['requisitos_concordantes']} de {f['requisitos']}"] for f in t["estabilidad"]])
    if t["sensibilidad"]:
        lineas += ["**Sensibilidad (variantes sin una competencia requerida):**", ""]
        lineas += _tabla(["Corrida", "Documento", "Competencia retirada", "Compat. original", "Sin la competencia", "Diferencia"],
                         [[etiqueta[f["corrida"]], f["documento"], f["competencia_retirada"], _n(f["compatibilidad_original"], 1),
                           _n(f["compatibilidad_sin_competencia"], 1), _n(f["diferencia"], 1)] for f in t["sensibilidad"]])

    lineas += ["## P3 y H3. Cambio tras aplicar las mejoras", ""]
    lineas += _tabla(["Corrida", "Medida", "n", "Con mejoras aplicadas", "Antes", "Después", "Diferencia media [IC 95 %]",
                      "Mediana", "p (Wilcoxon, unilateral)"],
                     [[etiqueta[f["corrida"]], _MEDIDAS[f["medida"]], _n(f["n"]), _n(f["con_mejoras_aplicadas"]), _n(f["media_a"]),
                       _n(f["media_b"]), f"{_n(f['diferencia_media'])} [{_n(f['ic95_bajo'])}; {_n(f['ic95_alto'])}]",
                       _n(f["diferencia_mediana"]), _p(f["wilcoxon_p_unilateral"])] for f in t["h3_mejora"]])

    lineas += ["## P3 y H4. Guardia contra la información inventada", ""]
    lineas += _tabla(["Corrida", "Sugerencias del modelo", "Con información nueva", "Con cambios de contenido", "Aprobadas",
                      "Tasa antes", "Tasa después", "Revisión manual"],
                     [[etiqueta[f["corrida"]], _n(f["sugerencias_del_modelo"]), _n(f["con_informacion_nueva"]),
                       _n(f["con_cambios_de_contenido"]), _n(f["aprobadas"]), _n(f["tasa_invencion_antes"], 3),
                       _n(f["tasa_invencion_despues"], 3),
                       (f"{f['revision_manual_respondidas']} de {f['revision_manual_muestra']} revisadas"
                        + (f"; residual {_n(f['tasa_invencion_residual_manual'], 3)}" if f.get("tasa_invencion_residual_manual") is not None else ""))
                       if "revision_manual_muestra" in f else "—"] for f in t["guardia"]])

    lineas += ["## Requisitos de las ofertas", ""]
    lineas += _tabla(["Corrida", "Oferta", "Identificados", "Precisión", "Fusionados", "De más", "Tipo correcto", "No identificados"],
                     [[etiqueta[f["corrida"]], f["oferta"], f"{f['identificados']} de {f['referencia']}", _n(f["precision"], 3),
                       _n(f["fusionados"]), _n(f["de_mas"]), _n(f["tipo_correcto"], 3), ", ".join(f["no_identificados"]) or "—"]
                      for f in t["requisitos_oferta"]])

    lineas += ["## P4. Extracción de ofertas por URL", ""]
    if t["urls_resumen"]:
        lineas += _tabla(["Medida", "Enlaces", "%"], [[f["medida"], _n(f["valor"]), _n(f["porcentaje"], 1)]
                                                      for f in t["urls_resumen"]])
    else:
        lineas += ["_Todavía no se midió: `python -m analizador_cv evaluar --urls lista.txt` (una URL por renglón, solo "
                   "sitios que no prohíben el acceso automatizado)._", ""]

    lineas += ["## Calidad de la salida y rendimiento", ""]
    lineas += _tabla(["Corrida", "Llamadas", "Válidas al primer intento", "Unidades fallidas", "Tokens (mediana, análisis con mejoras)",
                      "Latencia del modelo (mediana / p95, s)", "Reloj con esperas (mediana / p95, s)"],
                     [[etiqueta[f["corrida"]], _n(f["llamadas"]), _n(f["porcentaje_validas_al_primer_intento"], 3),
                       _n(f["unidades_fallidas"]), _n(f["principal_tokens_mediana"], 0),
                       f"{_n(f['principal_latencia_mediana_s'], 1)} / {_n(f['principal_latencia_p95_s'], 1)}",
                       f"{_n(f['principal_reloj_mediana_s'], 1)} / {_n(f['principal_reloj_p95_s'], 1)}"] for f in t["llamadas"]])
    if t["estimacion_cuota"]:
        lineas += ["## Estimación de la cuota para la corrida real (de la corrida simulada)", ""]
        lineas += _tabla(["Alcance", "Unidades", "Llamadas", "Tokens Groq", "Días de cuota gratuita de Groq", "Tokens Gemini"],
                         [[f["alcance"], _n(f["unidades"]), _n(f["llamadas"]), _n(f["tokens_groq"]),
                           _n(f["dias_cuota_gratuita_groq"], 1), _n(f["tokens_gemini"])] for f in t["estimacion_cuota"]])
        lineas += ["Calibrada con la corrida real de la Etapa 6 (Groq gasta muchos más tokens de salida por el razonamiento). "
                   "No incluye reintentos. La cuota gratuita de Groq es de 200 000 tokens por día (configuracion.json).", ""]

    lineas += ["## Archivos", "", "- Tablas: los `.csv` de esta carpeta (UTF-8, separados por comas).",
               "- Figuras: `figuras/` (PNG, 200 ppp).",
               "- Revisión manual de la fidelidad (H4): `revision_manual_<corrida>.csv`. Complete las columnas "
               "`agrega_informacion` y `exagera` con «sí» o «no» y vuelva a ejecutar el comando con `--solo-informe`.", ""]
    ruta.write_text("\n".join(lineas), encoding="utf-8")
    return ruta
