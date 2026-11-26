"""Antes y después (RF11): aplica las mejoras aceptadas y compara dos análisis.

aplicar_mejoras reemplaza en el texto analizado el fragmento original de cada sugerencia de redacción
aprobada por la guardia (o de las que la persona aceptó). Las de estructura son consejos y las palabras
clave nunca se insertan. comparar da los cambios de la puntuación, de cada criterio, de la
compatibilidad, de cada tag y de cada requisito.
"""

from __future__ import annotations

import re
from typing import Any

from analizador_cv.utilidades import hash_texto

_VINETA_INICIAL = re.compile(r"^\s*[-•*·▪●]\s+")
_PUNTUACION_FINAL = (".", ";", ":")


def aplicar_mejoras(texto: str, resultado: dict[str, Any],
                    aceptadas: list[str] | None = None) -> tuple[str, list[str], list[dict[str, str]]]:
    """Devuelve (texto mejorado, ids aplicados, no aplicadas con su motivo).

    «aceptadas» son los ids que eligió la persona; sin ella, se aplican todas las aplicables.
    El texto debe ser el mismo que se analizó (se compara su hash).
    """
    if hash_texto(texto) != resultado["texto"]["hash"]:
        raise ValueError("El texto no es el que se analizó: las posiciones de las sugerencias no corresponden.")
    sugerencias = resultado["sugerencias"]
    pedidas = set(aceptadas) if aceptadas is not None else {s["id"] for s in sugerencias if s["aplicable"]}
    no_aplicadas = [{"id": s["id"], "motivo": "no es una sugerencia de redacción aprobada por la guardia"}
                    for s in sugerencias if s["id"] in pedidas and not s["aplicable"]]
    no_aplicadas += [{"id": i, "motivo": "no existe"} for i in sorted(pedidas - {s["id"] for s in sugerencias})]
    candidatas = sorted((s for s in sugerencias if s["id"] in pedidas and s["aplicable"]), key=lambda s: s["ubicacion"][0])
    partes, cursor, aplicadas = [], 0, []
    for sugerencia in candidatas:
        inicio, fin = sugerencia["ubicacion"]
        if inicio < cursor:
            no_aplicadas.append({"id": sugerencia["id"], "motivo": "se solapa con otra sugerencia aplicada"})
            continue
        propuesta = sugerencia["propuesta"].strip()
        if not _VINETA_INICIAL.match(texto[inicio:fin]):
            # La viñeta del original queda fuera del tramo ubicado y se conserva (aunque el fragmento citado la traiga).
            propuesta = _VINETA_INICIAL.sub("", propuesta)
        if propuesta[-1:] in _PUNTUACION_FINAL and texto[fin:fin + 1] in _PUNTUACION_FINAL:
            fin += 1  # la ubicación no incluye el punto final del original: queda solo el de la propuesta
        partes += [texto[cursor:inicio], propuesta]
        cursor = fin
        aplicadas.append(sugerencia["id"])
    partes.append(texto[cursor:])
    return "".join(partes), aplicadas, no_aplicadas


def _delta(antes: float | None, despues: float | None) -> float | None:
    return None if antes is None or despues is None else round(despues - antes, 2)


def comparar(antes: dict[str, Any], despues: dict[str, Any]) -> dict[str, Any]:
    """Diferencias entre el análisis original y el de la versión mejorada."""
    criterios_despues = {c["id"]: c for c in despues["puntuacion"]["criterios"]}
    criterios = [{"id": c["id"], "nombre": c["nombre"], "maximo": c["maximo"], "antes": c["puntos"],
                  "despues": criterios_despues[c["id"]]["puntos"],
                  "delta": _delta(c["puntos"], criterios_despues[c["id"]]["puntos"])}
                 for c in antes["puntuacion"]["criterios"]]

    tags_antes = {t["clave"]: t for t in antes["tags"]}
    tags_despues = {t["clave"]: t for t in despues["tags"]}
    tags = []
    for clave in list(tags_antes) + [c for c in tags_despues if c not in tags_antes]:
        a, d = tags_antes.get(clave), tags_despues.get(clave)
        tags.append({"nombre": (a or d)["nombre"], "clave": clave,
                     "cambio": "nuevo" if a is None else "quitado" if d is None else "igual",
                     "impacto_antes": a["impacto"] if a else None, "impacto_despues": d["impacto"] if d else None,
                     "delta_impacto": _delta(a["impacto"] if a else None, d["impacto"] if d else None),
                     "relevancia_antes": a["relevancia"] if a else None,
                     "relevancia_despues": d["relevancia"] if d else None})

    compatibilidad = requisitos = None
    if antes.get("compatibilidad") and despues.get("compatibilidad"):
        pa, pd = antes["compatibilidad"]["porcentaje"], despues["compatibilidad"]["porcentaje"]
        compatibilidad = {"antes": pa, "despues": pd, "delta": _delta(pa, pd)}
        estados_despues = {r["id"]: r for r in despues["compatibilidad"]["requisitos"]}
        requisitos = [{"id": r["id"], "texto": r["texto"], "tipo": r["tipo"], "antes": r["estado"],
                       "despues": estados_despues.get(r["id"], {}).get("estado")}
                      for r in antes["compatibilidad"]["requisitos"]]

    total_a, total_d = antes["puntuacion"]["total"], despues["puntuacion"]["total"]
    return {"puntuacion": {"antes": total_a, "despues": total_d, "delta": _delta(total_a, total_d)},
            "criterios": criterios, "compatibilidad": compatibilidad, "requisitos": requisitos, "tags": tags,
            "mismo_metodo": antes["metodo"] == despues["metodo"] and antes["huella_configuracion"] == despues["huella_configuracion"]}
