"""Requisitos de la oferta y su cobertura en la hoja de vida (definición del proyecto, §6.2; RF07).

    Compatibilidad (%) = 100 × Σ (peso × cobertura) / Σ peso
    peso: obligatorio = 2, deseable = 1; cobertura: cubierto = 1, mencionado = 0,5, ausente = 0.

El modelo (o la línea base) clasifica cada requisito y cita la evidencia; el código la verifica:
- un requisito de la oferta cuyo texto no está en la oferta (ni sus términos) se descarta;
- «cubierto» o «mencionado» con una evidencia que no está en la hoja de vida baja a «mencionado» si
  algún término del requisito aparece en la hoja de vida, o a «ausente» si no;
- «ausente» sube a «mencionado» si un término del requisito que es una habilidad del diccionario
  aparece en la hoja de vida (el modelo lo pasó por alto);
- un requisito de tiempo de experiencia («mínimo 1 año») lo decide el código con los meses que
  calculó (§6.4): «cubierto» si alcanzan, «mencionado» si hay experiencia pero no alcanza y «ausente»
  si no hay experiencia. No se revisa el área de la experiencia.
Cada cambio queda en «ajuste»; la clasificación del modelo se conserva en «estado_modelo».
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from analizador_cv.analisis.cotejo import normalizar, normalizar_con_mapa, ubicar_fragmento
from analizador_cv.analisis.fechas import duracion_en_meses
from analizador_cv.analisis.habilidades import Diccionario
from analizador_cv.analisis.temporalidad import Tag

PESOS = {"obligatorio": 2, "deseable": 1}
COBERTURA = {"cubierto": 1.0, "mencionado": 0.5, "ausente": 0.0}
RELEVANCIA_OFERTA = {"obligatorio": 1.0, "deseable": 0.6}
RELEVANCIA_FUERA_DE_OFERTA = 0.2
UMBRAL_REQUISITO = 0.8


@dataclass
class Requisito:
    id: str
    texto: str
    tipo: str
    categoria: str
    terminos: list[str] = field(default_factory=list)

    @property
    def peso(self) -> int:
        return PESOS[self.tipo]

    def a_dict(self) -> dict[str, Any]:
        return {"id": self.id, "texto": self.texto, "tipo": self.tipo, "peso": self.peso, "categoria": self.categoria,
                "terminos": list(self.terminos)}

    @classmethod
    def desde_dict(cls, datos: dict[str, Any]) -> "Requisito":
        return cls(datos["id"], datos["texto"], datos["tipo"], datos["categoria"], list(datos.get("terminos") or []))


@dataclass
class Cobertura:
    requisito: Requisito
    estado: str
    evidencia: str | None
    estado_modelo: str
    ajuste: str | None = None

    @property
    def valor(self) -> float:
        return COBERTURA[self.estado]

    def a_dict(self) -> dict[str, Any]:
        return {**self.requisito.a_dict(), "estado": self.estado, "cobertura": self.valor, "evidencia": self.evidencia,
                "estado_modelo": self.estado_modelo, "ajuste": self.ajuste}


def procesar_requisitos(datos: dict[str, Any], texto_oferta: str, *,
                        diccionario: Diccionario) -> tuple[list[Requisito], list[dict[str, str]]]:
    """Verifica los requisitos contra la oferta, quita repetidos y asigna los identificadores R1, R2…"""
    normalizado = normalizar_con_mapa(texto_oferta)
    requisitos: list[Requisito] = []
    descartados: list[dict[str, str]] = []
    vistos: set[tuple[str, tuple[str, ...]]] = set()
    for crudo in datos["requisitos"]:
        texto = crudo["texto"].strip()
        terminos = [t.strip() for t in crudo.get("terminos") or [] if t.strip()]
        terminos_en_oferta = [t for t in terminos if diccionario.en_texto(t, normalizado[0])]
        en_oferta = ubicar_fragmento(texto, texto_oferta, umbral=UMBRAL_REQUISITO, normalizado=normalizado) is not None
        if not texto or not (en_oferta or (terminos and len(terminos_en_oferta) == len(terminos))):
            descartados.append({"texto": texto, "motivo": "no aparece en la oferta"})
            continue
        identidad = (normalizar(texto), tuple(sorted(diccionario.clave(t) for t in terminos_en_oferta)))
        if identidad in vistos:
            continue
        vistos.add(identidad)
        requisitos.append(Requisito(f"R{len(requisitos) + 1}", texto, crudo["tipo"], crudo["categoria"], terminos_en_oferta))
    return requisitos, descartados


def _termino_presente(requisito: Requisito, texto_norm: str, diccionario: Diccionario, *,
                      solo_diccionario: bool) -> str | None:
    for termino in requisito.terminos:
        if solo_diccionario and diccionario.buscar(termino) is None:
            continue
        if diccionario.en_texto(termino, texto_norm):
            return termino
    return None


def procesar_cobertura(requisitos: list[Requisito], datos: list[dict[str, Any]], texto_cv: str, *,
                       diccionario: Diccionario, meses_experiencia: int | None = None,
                       experiencias: list[str] | tuple[str, ...] = ()) -> list[Cobertura]:
    """Aplica las verificaciones a la clasificación de cada requisito.

    «meses_experiencia» y «experiencias» (cargos tal como están en el texto) son los que calculó el
    programa; con ellos se deciden los requisitos de tiempo de experiencia.
    """
    normalizado = normalizar_con_mapa(texto_cv)
    por_id = {d["id"]: d for d in datos}
    coberturas = []
    for requisito in requisitos:
        crudo = por_id.get(requisito.id, {"estado": "ausente", "evidencia": None})
        estado, evidencia, ajuste = crudo["estado"], (crudo.get("evidencia") or "").strip() or None, None
        pedido = duracion_en_meses(requisito.texto) if requisito.categoria == "experiencia" else None
        if pedido is not None and meses_experiencia is not None:
            estado = ("cubierto" if meses_experiencia >= pedido and experiencias
                      else "mencionado" if meses_experiencia > 0 else "ausente")
            evidencia = experiencias[0] if experiencias else None
            ajuste = f"decidido por el programa: {meses_experiencia} meses de experiencia calculados (se piden {pedido})"
        elif estado != "ausente":
            ubicada = evidencia and ubicar_fragmento(evidencia, texto_cv, normalizado=normalizado)
            if not ubicada:
                presente = _termino_presente(requisito, normalizado[0], diccionario, solo_diccionario=False)
                ajuste = "la evidencia citada no está en la hoja de vida"
                if presente and estado == "cubierto":
                    estado, ajuste = "mencionado", f"{ajuste}; el término «{presente}» sí aparece"
                elif not presente:
                    estado = "ausente"
                evidencia = None
        else:
            evidencia = None
            presente = _termino_presente(requisito, normalizado[0], diccionario, solo_diccionario=True)
            if presente:
                estado, ajuste = "mencionado", f"el término «{presente}» aparece en la hoja de vida"
        coberturas.append(Cobertura(requisito, estado, evidencia, crudo["estado"], ajuste))
    return coberturas


def porcentaje(coberturas: list[Cobertura]) -> float | None:
    total = sum(c.requisito.peso for c in coberturas)
    if not total:
        return None
    return round(100 * sum(c.requisito.peso * c.valor for c in coberturas) / total, 1)


def asignar_relevancia_oferta(tags: list[Tag], requisitos: list[Requisito], diccionario: Diccionario) -> None:
    """Relevancia de cada tag frente a la oferta: 1 (obligatorio), 0,6 (deseable) o 0,2 (no la pide)."""
    textos = [(r, normalizar(" ".join([r.texto, *r.terminos]))) for r in requisitos]
    for tag in tags:
        coincidencias = [r for r, texto in textos
                         if diccionario.en_texto(tag.nombre, texto) or any(diccionario.clave(t) == tag.clave for t in r.terminos)]
        tag.requisitos = [r.id for r in coincidencias]
        tag.relevancia_oferta = max((RELEVANCIA_OFERTA[r.tipo] for r in coincidencias), default=RELEVANCIA_FUERA_DE_OFERTA)

