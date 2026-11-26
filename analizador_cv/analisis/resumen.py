"""Resumen en texto de un análisis y de una comparación antes/después (para la línea de comandos)."""

from __future__ import annotations

from typing import Any

from analizador_cv.utilidades import cantidad, formatear_duracion, numero_es

ESTADOS_SUGERENCIA = {"aprobada": "aprobada", "descartada": "DESCARTADA", "requiere_confirmacion": "solo si la tienes"}
TIPOS_SUGERENCIA = {"redaccion": "redacción", "estructura": "estructura", "palabra_clave": "palabra clave"}


def _n(valor: float | None, decimales: int = 1) -> str:
    return numero_es(valor, decimales)


def _recortar(texto: str | None, largo: int = 110) -> str:
    texto = " ".join((texto or "").split())
    return texto if len(texto) <= largo else texto[: largo - 1] + "…"


def texto_resultado(resultado: dict[str, Any], *, tags: int = 12) -> str:
    r = []
    modelo = resultado["modelo"]
    metodo = (f"modelo {modelo['perfil']} ({modelo['modelo']})" if modelo
              else "línea base por palabras clave (DEMOSTRACIÓN: sin modelo de lenguaje)")
    modo = "específico (frente a una oferta)" if resultado["modo"] == "especifico" else "general"
    r.append(f"Método: {metodo} | modo: {modo} | fecha de referencia: {resultado['fecha_referencia']}")
    r.append("")
    puntuacion = resultado["puntuacion"]
    r.append(f"PUNTUACIÓN GENERAL: {_n(puntuacion['total'])} / 100 (rúbrica v{puntuacion['version_rubrica']})")
    for criterio in puntuacion["criterios"]:
        r.append(f"  {criterio['nombre']:<34} {_n(criterio['puntos']):>5} / {criterio['maximo']}")
        for concepto in criterio["conceptos"]:
            r.append(f"      · {concepto['nombre']}: {_n(concepto['puntos'])}/{_n(concepto['maximo'], 0)} — {concepto['detalle']}")
    compatibilidad = resultado["compatibilidad"]
    if compatibilidad:
        r.append("")
        r.append(f"COMPATIBILIDAD CON LA OFERTA: {_n(compatibilidad['porcentaje'])} %")
        for requisito in compatibilidad["requisitos"]:
            evidencia = f" — «{_recortar(requisito['evidencia'], 70)}»" if requisito["evidencia"] else ""
            ajuste = f" [ajuste: {requisito['ajuste']}]" if requisito["ajuste"] else ""
            r.append(f"  {requisito['id']} ({requisito['tipo']}) {_recortar(requisito['texto'], 60)}: "
                     f"{requisito['estado'].upper()}{evidencia}{ajuste}")
    r.append("")
    r.append("TAGS (relevancia · impacto · meses · último uso):")
    for tag in resultado["tags"][:tags]:
        r.append(f"  {tag['nombre']:<28} {_n(tag['relevancia'], 2)} · {_n(tag['impacto'], 2)} · {tag['meses']:>3} · "
                 f"{tag['ultimo_uso'] or 'sin fecha'}")
    if len(resultado["tags"]) > tags:
        r.append(f"  … y {len(resultado['tags']) - tags} más (todos en --json)")
    for titulo, clave in (("FORTALEZAS", "fortalezas"), ("DEBILIDADES", "debilidades")):
        if resultado[clave]:
            r.append("")
            r.append(f"{titulo}:")
            r += [f"  - {x}" for x in resultado[clave]]
    if resultado["guardia"] is not None:
        g = resultado["guardia"]
        r.append("")
        tasa = f"; con información nueva antes de la guardia: {_n(100 * g['tasa_invencion_antes'], 0)} %" \
            if g["tasa_invencion_antes"] is not None else ""
        r.append(f"SUGERENCIAS ({cantidad(g['aprobadas'], 'aprobada', 'aprobadas')}, "
                 f"{cantidad(g['descartadas'], 'descartada', 'descartadas')} por la guardia, "
                 f"{cantidad(g['requieren_confirmacion'], 'palabra clave', 'palabras clave')} por confirmar{tasa}):")
        for s in resultado["sugerencias"]:
            cabecera = f"  {s['id']} [{TIPOS_SUGERENCIA[s['tipo']]} · {ESTADOS_SUGERENCIA[s['estado']]}]"
            if s["fragmento_original"] and s["tipo"] == "redaccion":
                r.append(f"{cabecera} «{_recortar(s['fragmento_original'], 80)}»")
                r.append(f"      → «{_recortar(s['propuesta'], 100)}»")
            else:
                r.append(f"{cabecera} {_recortar(s['propuesta'])}")
            if s["estado"] == "descartada":
                r.append(f"      motivo: {'; '.join(s['motivos'])}")
    no_verificados = resultado["extraccion"]["no_verificados"]
    if no_verificados:
        r.append("")
        r.append(f"Datos del modelo que no se encontraron en el texto y se descartaron: {len(no_verificados)} "
                 f"(detalle en --json).")
    if resultado["llamadas"]:
        tokens = sum(x["tokens_total"] for x in resultado["llamadas"])
        segundos = sum(x["latencia_s"] for x in resultado["llamadas"])
        intentos = sum(x["intentos"] for x in resultado["llamadas"])
        r.append(f"Llamadas al modelo: {len(resultado['llamadas'])} ({cantidad(intentos, 'intento', 'intentos')}) · {tokens} tokens · "
                 f"{formatear_duracion(segundos)}")
    return "\n".join(r)


def texto_comparacion(comparacion: dict[str, Any]) -> str:
    def signo(valor: float | None) -> str:
        return "—" if valor is None else ("+" if valor > 0 else "") + _n(valor)

    p = comparacion["puntuacion"]
    r = ["ANTES Y DESPUÉS", f"  Puntuación general: {_n(p['antes'])} → {_n(p['despues'])} ({signo(p['delta'])})"]
    for criterio in comparacion["criterios"]:
        r.append(f"    {criterio['nombre']:<34} {_n(criterio['antes']):>5} → {_n(criterio['despues']):>5} "
                 f"({signo(criterio['delta'])})")
    c = comparacion["compatibilidad"]
    if c:
        r.append(f"  Compatibilidad: {_n(c['antes'])} % → {_n(c['despues'])} % ({signo(c['delta'])})")
        for requisito in comparacion["requisitos"]:
            if requisito["antes"] != requisito["despues"]:
                r.append(f"    {requisito['id']} {_recortar(requisito['texto'], 60)}: {requisito['antes']} → {requisito['despues']}")
    cambios = [t for t in comparacion["tags"] if t["cambio"] != "igual" or (t["delta_impacto"] or 0) != 0]
    if cambios:
        r.append("  Tags que cambian:")
        for tag in cambios:
            r.append(f"    {tag['nombre']}: {tag['cambio']}, impacto {_n(tag['impacto_antes'], 2)} → "
                     f"{_n(tag['impacto_despues'], 2)}")
    if not comparacion["mismo_metodo"]:
        r.append("  AVISO: los dos análisis no usan la misma configuración; la comparación no es directa.")
    return "\n".join(r)
