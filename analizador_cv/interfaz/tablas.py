"""Filas de las tablas de la app y de los CSV exportados (las mismas en los dos lugares), y los
renglones que cambian entre el texto original y la versión mejorada."""

from __future__ import annotations

import difflib
from typing import Any

ESTADOS_REQUISITO = {"cubierto": "✔ Cubierto", "mencionado": "◐ Mencionado", "ausente": "✖ Ausente"}
ESTADOS_SUGERENCIA = {"aprobada": "Aprobada", "descartada": "Descartada por la guardia",
                      "requiere_confirmacion": "Solo si la tienes"}
TIPOS_SUGERENCIA = {"redaccion": "Redacción", "estructura": "Estructura", "palabra_clave": "Palabra clave"}
CLASES_BLOQUE = {"experiencia": "Experiencia", "proyecto": "Proyecto", "formacion": "Formación"}
PRECISIONES = {"mes": "mes", "año": "año", "duracion": "solo duración", "incompleta": "incompleta",
               "sin_fecha": "sin fechas"}
ETAPAS = {"extraccion_cv": "Extracción", "requisitos_oferta": "Requisitos de la oferta",
          "evaluacion_general": "Evaluación", "evaluacion_especifica": "Evaluación frente a la oferta",
          "mejoras": "Mejoras"}


def filas_criterios(resultado: dict[str, Any]) -> list[dict[str, Any]]:
    """Un renglón por concepto de la rúbrica, con su criterio."""
    return [{"Criterio": c["nombre"], "Concepto": k["nombre"], "Puntos": k["puntos"], "Máximo": k["maximo"],
             "Detalle": k["detalle"]}
            for c in resultado["puntuacion"]["criterios"] for k in c["conceptos"]]


def filas_tags(resultado: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"Tag": t["nombre"], "Tipo": "blanda" if t["tipo"] == "blanda" else "técnica",
             "Relevancia": t["relevancia"], "Impacto": t["impacto"], "Evidencia": t["evidencia"],
             "Recencia": t["recencia"], "Duración": t["duracion"], "Meses": t["meses"], "Primer uso": t["primer_uso"],
             "Último uso": t["ultimo_uso"], "Apariciones": t["apariciones"],
             "Usos": "; ".join(f"{u['etiqueta']} ({u['periodo']})" for u in t["usos"]),
             "Requisitos": ", ".join(t["requisitos"])}
            for t in resultado["tags"]]


def filas_requisitos(resultado: dict[str, Any]) -> list[dict[str, Any]]:
    compatibilidad = resultado.get("compatibilidad")
    if not compatibilidad:
        return []
    return [{"Id": r["id"], "Requisito": r["texto"], "Tipo": r["tipo"], "Peso": r["peso"], "Estado": r["estado"],
             "Cobertura": r["cobertura"], "Evidencia": r["evidencia"] or "", "Ajuste del programa": r["ajuste"] or ""}
            for r in compatibilidad["requisitos"]]


def filas_bloques(resultado: dict[str, Any]) -> list[dict[str, Any]]:
    """Experiencias, proyectos y formación extraídos y verificados, con su periodo calculado."""
    extraccion = resultado["extraccion"]
    filas = []
    for bloque in extraccion["experiencias"] + extraccion["proyectos"] + extraccion["formacion"]:
        periodo = bloque["periodo"]
        filas.append({"Tipo": CLASES_BLOQUE[bloque["clase"]], "Título": bloque["titulo"],
                      "Entidad": bloque["entidad"] or "", "Inicio": periodo["inicio"] or "", "Fin": periodo["fin"] or "",
                      "Meses": periodo["meses"], "Precisión": PRECISIONES.get(periodo["precision"], periodo["precision"]),
                      "Actividades": len(bloque["actividades"]),
                      "Logros": sum(1 for a in bloque["actividades"] if a["es_logro"]),
                      "Habilidades": ", ".join(bloque["habilidades"]),
                      "Observaciones": "; ".join(periodo["problemas"])})
    return filas


def filas_sugerencias(resultado: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"Id": s["id"], "Tipo": TIPOS_SUGERENCIA.get(s["tipo"], s["tipo"]),
             "Estado": ESTADOS_SUGERENCIA.get(s["estado"], s["estado"]),
             "Origen": "modelo" if s["origen"] == "modelo" else "programa",
             "Fragmento original": s["fragmento_original"] or "", "Propuesta": s["propuesta"],
             "Justificación": s["justificacion"] or "", "Motivos": "; ".join(s["motivos"]),
             "Información nueva": ", ".join(s["entidades_nuevas"]),
             "Palabras nuevas": ", ".join(s.get("palabras_nuevas") or []), "Aplicable": s["aplicable"]}
            for s in resultado["sugerencias"]]


def filas_llamadas(resultado: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"Etapa": ETAPAS.get(l["etapa"], l["etapa"]), "Intentos": l["intentos"], "Estado": l["estado"],
             "Tokens de entrada": l["tokens_entrada"], "Tokens de salida": l["tokens_salida"],
             "Tokens en total": l["tokens_total"], "Latencia (s)": l["latencia_s"]}
            for l in resultado["llamadas"]]


def filas_comparacion(comparacion: dict[str, Any]) -> list[dict[str, Any]]:
    """Puntuación total, compatibilidad y cada criterio: antes, después y diferencia."""
    filas = [{"Medida": "Puntuación general", "Antes": comparacion["puntuacion"]["antes"],
              "Después": comparacion["puntuacion"]["despues"], "Diferencia": comparacion["puntuacion"]["delta"]}]
    if comparacion["compatibilidad"]:
        c = comparacion["compatibilidad"]
        filas.append({"Medida": "Compatibilidad (%)", "Antes": c["antes"], "Después": c["despues"], "Diferencia": c["delta"]})
    filas += [{"Medida": c["nombre"], "Antes": c["antes"], "Después": c["despues"], "Diferencia": c["delta"]}
              for c in comparacion["criterios"]]
    return filas


PRESENCIA_TAG = {"igual": "en las dos", "nuevo": "solo en la mejorada", "quitado": "solo en la original"}


def filas_comparacion_tags(comparacion: dict[str, Any], *, solo_cambios: bool = False) -> list[dict[str, Any]]:
    filas = [{"Tag": t["nombre"], "Presencia": PRESENCIA_TAG.get(t["cambio"], t["cambio"]),
              "Impacto antes": t["impacto_antes"], "Impacto después": t["impacto_despues"],
              "Diferencia": t["delta_impacto"], "Relevancia antes": t["relevancia_antes"],
              "Relevancia después": t["relevancia_despues"]}
             for t in comparacion["tags"]]
    if solo_cambios:
        filas = [f for f in filas if f["Presencia"] != PRESENCIA_TAG["igual"] or f["Diferencia"] or
                 f["Relevancia antes"] != f["Relevancia después"]]
    return filas


def filas_comparacion_requisitos(comparacion: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"Id": r["id"], "Requisito": r["texto"], "Tipo": r["tipo"], "Antes": r["antes"], "Después": r["despues"]}
            for r in comparacion["requisitos"] or []]


def cambios(antes: str, despues: str) -> list[tuple[str, str]]:
    """Renglones quitados («-») y agregados («+») de la versión mejorada, en orden."""
    salida = []
    for operacion, i1, i2, j1, j2 in difflib.SequenceMatcher(None, antes.split("\n"), despues.split("\n"),
                                                             autojunk=False).get_opcodes():
        if operacion == "equal":
            continue
        salida += [("-", r) for r in antes.split("\n")[i1:i2] if r.strip()]
        salida += [("+", r) for r in despues.split("\n")[j1:j2] if r.strip()]
    return salida
