"""Tags (habilidades) con temporalidad, relevancia e impacto (definición del proyecto, §6.3 y §6.4).

- Usos de un tag: las experiencias, proyectos, cursos y certificaciones en cuyo texto aparece. Los
  títulos formales no cuentan como uso: un programa de varios años no es uso de una herramienta.
- Primer uso, último uso y meses acumulados, con los periodos solapados fusionados.
- evidencia: 1 si se usó en una experiencia o proyecto con un logro; 0,6 si se usó en una experiencia
  o proyecto sin logro; 0,3 si solo aparece listado (o solo en cursos y certificaciones).
- recencia: 1 si el último uso fue hace 12 meses o menos; 0,6 si hace 13 a 36; 0,3 si hace más;
  0 si ningún uso tiene fechas.
- duracion: meses acumulados ÷ 36, con un máximo de 1.
- impacto = 0,5 × evidencia + 0,3 × recencia + 0,2 × duracion.
- relevancia general = 0,5 × (apariciones ÷ apariciones del tag más frecuente) + 0,5 × presencia,
  con presencia 1 si se usó en una experiencia o proyecto en los últimos 36 meses; 0,5 si ese uso es
  más antiguo o sin fechas, o si solo se usó en cursos; 0 si solo está listado.
- La relevancia frente a la oferta se asigna en compatibilidad.asignar_relevancia_oferta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from analizador_cv.analisis.cotejo import normalizar
from analizador_cv.analisis.extraccion import Bloque, Extraccion
from analizador_cv.analisis.fechas import Mes, meses_acumulados, meses_entre
from analizador_cv.analisis.habilidades import Diccionario

PESOS_IMPACTO = {"evidencia": 0.5, "recencia": 0.3, "duracion": 0.2}
MESES_DURACION_COMPLETA = 36
#: (meses desde el último uso, valor de recencia)
TRAMOS_RECENCIA = ((12, 1.0), (36, 0.6))
RECENCIA_ANTIGUA = 0.3
EVIDENCIA = {"con_logro": 1.0, "sin_logro": 0.6, "listado": 0.3}


@dataclass
class Uso:
    clase: str
    indice: int
    etiqueta: str
    periodo: str
    con_logro: bool


@dataclass
class Tag:
    nombre: str
    clave: str
    tipo: str
    apariciones: int
    usos: list[Uso] = field(default_factory=list)
    primer_uso: Mes | None = None
    ultimo_uso: Mes | None = None
    meses: int = 0
    evidencia: float = 0.0
    recencia: float = 0.0
    duracion: float = 0.0
    impacto: float = 0.0
    relevancia_general: float = 0.0
    relevancia_oferta: float | None = None
    requisitos: list[str] = field(default_factory=list)

    @property
    def relevancia(self) -> float:
        """La que se muestra: frente a la oferta en modo específico; la general en modo general."""
        return self.relevancia_oferta if self.relevancia_oferta is not None else self.relevancia_general

    def a_dict(self) -> dict[str, Any]:
        return {"nombre": self.nombre, "clave": self.clave, "tipo": self.tipo, "apariciones": self.apariciones,
                "usos": [u.__dict__.copy() for u in self.usos],
                "primer_uso": str(self.primer_uso) if self.primer_uso else None,
                "ultimo_uso": str(self.ultimo_uso) if self.ultimo_uso else None, "meses": self.meses,
                "evidencia": self.evidencia, "recencia": self.recencia, "duracion": self.duracion,
                "impacto": self.impacto, "relevancia_general": self.relevancia_general,
                "relevancia_oferta": self.relevancia_oferta, "relevancia": self.relevancia,
                "requisitos": list(self.requisitos)}


def recencia_de(ultimo_uso: Mes | None, referencia: Mes) -> float:
    if ultimo_uso is None:
        return 0.0
    transcurridos = meses_entre(ultimo_uso, referencia)
    for limite, valor in TRAMOS_RECENCIA:
        if transcurridos <= limite:
            return valor
    return RECENCIA_ANTIGUA


def _cuenta_como_uso(bloque: Bloque) -> bool:
    return bloque.clase in ("experiencia", "proyecto") or bloque.tipo in ("curso", "certificacion")


def construir_tags(extraccion: Extraccion, texto: str, *, referencia: Mes, diccionario: Diccionario) -> list[Tag]:
    """Un tag por habilidad (unificadas con el diccionario), con su temporalidad, impacto y relevancia general."""
    texto_norm = normalizar(texto)
    nombres: dict[str, tuple[str, str]] = {}  # clave → (nombre, tipo)
    for habilidad in extraccion.habilidades:
        nombres.setdefault(diccionario.clave(habilidad["nombre"]), (habilidad["nombre"], habilidad["tipo"]))
    for bloque in extraccion.bloques:
        for nombre in bloque.habilidades:
            encontrada = diccionario.buscar(nombre)
            nombres.setdefault(diccionario.clave(nombre), (nombre, encontrada.tipo if encontrada else "tecnica"))

    tags: list[Tag] = []
    presencia: dict[str, float] = {}
    for clave, (nombre, tipo) in nombres.items():
        bloques = [b for b in extraccion.bloques
                   if _cuenta_como_uso(b) and any(diccionario.clave(n) == clave for n in b.habilidades)]
        tag = Tag(diccionario.nombre_canonico(nombre), clave, tipo, max(1, diccionario.contar(nombre, texto_norm)))
        tag.usos = [Uso(b.clase, b.indice, b.etiqueta, b.periodo.texto(), b.tiene_logro) for b in bloques]
        periodos = [b.periodo for b in bloques]
        ubicados = [p for p in periodos if p.ubicado]
        tag.primer_uso = min((p.inicio for p in ubicados), default=None)
        tag.ultimo_uso = max((p.fin for p in ubicados), default=None)
        tag.meses = meses_acumulados(periodos)
        aplicados = [b for b in bloques if b.clase in ("experiencia", "proyecto")]
        if any(b.tiene_logro for b in aplicados):
            tag.evidencia = EVIDENCIA["con_logro"]
        elif aplicados:
            tag.evidencia = EVIDENCIA["sin_logro"]
        else:
            tag.evidencia = EVIDENCIA["listado"]
        tag.recencia = recencia_de(tag.ultimo_uso, referencia)
        tag.duracion = round(min(1.0, tag.meses / MESES_DURACION_COMPLETA), 4)
        tag.impacto = round(PESOS_IMPACTO["evidencia"] * tag.evidencia + PESOS_IMPACTO["recencia"] * tag.recencia
                            + PESOS_IMPACTO["duracion"] * tag.duracion, 4)
        recientes = [b for b in aplicados if b.periodo.ubicado and meses_entre(b.periodo.fin, referencia) <= 36]
        presencia[clave] = 1.0 if recientes else (0.5 if bloques else 0.0)
        tags.append(tag)

    maximo = max((t.apariciones for t in tags), default=1)
    for tag in tags:
        tag.relevancia_general = round(0.5 * tag.apariciones / maximo + 0.5 * presencia[tag.clave], 4)
    tags.sort(key=lambda t: (-t.impacto, -t.apariciones, t.nombre.lower()))
    return tags
