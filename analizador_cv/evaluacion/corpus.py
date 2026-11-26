"""Corpus sintético de la evaluación técnica con su verdad de referencia (definición del proyecto, §4 y §9).

Fuentes (datos/corpus/):
- fuentes/perfiles_base.json y fuentes/vacantes.json: los 36 perfiles (12 por vacante, con nivel de
  adecuación alto, medio o bajo) y las 3 vacantes del proyecto anterior, copiados sin cambios de la
  etiqueta git proyecto-curriculos-espejo-final;
- complementos.json: lo agregado en la Etapa 8 (empresas, instituciones y años de la formación formal,
  todos ficticios, y las ofertas en dos redacciones);
- lexico_referencia.json: qué términos de los textos son habilidades, con su nivel (núcleo u opcional).

Este módulo arma cada hoja de vida con fechas, la escribe como texto (el TXT; herramientas/generar_corpus.py
genera el DOCX y el PDF con ese mismo texto) y calcula su verdad de referencia:
- experiencias, proyectos y formación con sus fechas: salen de los datos estructurados;
- habilidades: cada término del léxico que aparece en el texto, con su nivel y su clase;
- meses de cada habilidad: los de las experiencias, proyectos, cursos y certificaciones en cuyo texto
  aparece, sin contar dos veces los meses solapados (la regla de la definición, §6.4). Se calcula aquí, sin
  usar el código de temporalidad de la app, para no compartir sus errores.

Reglas fijas de las fechas (las fuentes solo traen duraciones); n es la posición del perfil en su vacante (0 a 11):
- la experiencia más reciente sigue vigente si n % 3 != 2; si no, terminó hace 2 + n % 4 meses. Cada
  experiencia anterior termina 2 meses antes de que empiece la siguiente y dura lo que dice la fuente;
- el proyecto j termina hace 3 + 7j + n % 3 meses y dura 3 + (j + n) % 3 meses;
- el curso o certificación c (en el orden de la fuente): los cursos terminan hace 9 + 6c + n % 4 meses y
  duran 2, 3 o 6 meses según sus horas (≤ 60, ≤ 120 o más); las certificaciones tienen un solo mes, hace
  5 + 6c + n % 4 meses.
Variación de forma: plantilla de una o dos columnas (n % 2), estilo de las fechas (n % 3: «mayo de 2025»,
«05/2025» o «may. 2025») y nombres de las secciones ((n // 2) % 2).

Variantes de control: los 6 perfiles con «control_competencia» en la fuente se repiten sin una competencia
que pide la vacante, con las ediciones exactas de la fuente. Sirven para comprobar que la compatibilidad
baja cuando falta la evidencia (sensibilidad).
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from analizador_cv.analisis.cotejo import normalizar, patron_termino
from analizador_cv.analisis.fechas import Mes, duracion_en_meses
from analizador_cv.configuracion import RAIZ_PROYECTO
from analizador_cv.utilidades import hash_objeto, leer_json

CARPETA_CORPUS = RAIZ_PROYECTO / "datos" / "corpus"
VERSION_CORPUS = "1"
PLANTILLAS = ("una_columna", "dos_columnas")
ESTILOS = ("texto", "numerico", "abreviado")
NIVELES = {"alto": 3, "medio": 2, "bajo": 1}

_NOMBRES_MES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
                "noviembre", "diciembre")
_ABREVIATURAS = ("ene.", "feb.", "mar.", "abr.", "may.", "jun.", "jul.", "ago.", "sept.", "oct.", "nov.", "dic.")
_ACTUAL = {"texto": "actualidad", "numerico": "actual", "abreviado": "presente"}
_GUION = {"texto": " – ", "numerico": " - ", "abreviado": " – "}
ENCABEZADOS = {
    "A": {"perfil": "PERFIL PROFESIONAL", "habilidades": "HABILIDADES TÉCNICAS", "experiencia": "EXPERIENCIA LABORAL",
          "proyectos": "PROYECTOS", "formacion": "FORMACIÓN ACADÉMICA", "cursos": None},
    "B": {"perfil": "RESUMEN", "habilidades": "CONOCIMIENTOS", "experiencia": "EXPERIENCIA PROFESIONAL",
          "proyectos": "PROYECTOS ACADÉMICOS Y PERSONALES", "formacion": "EDUCACIÓN",
          "cursos": "CURSOS Y CERTIFICACIONES"},
}
_HORAS = re.compile(r"(\d+)\s*horas")
_ORACIONES = re.compile(r"(?<=\.)\s+(?=[A-ZÁÉÍÓÚÑ¿])")


# =============================================================================
# Perfiles con fechas
# =============================================================================
@dataclass
class BloqueCorpus:
    """Una experiencia, un proyecto o un estudio de la hoja de vida sintética."""

    clase: str  # experiencia, proyecto o formacion
    titulo: str
    entidad: str | None
    inicio: Mes | None
    fin: Mes | None
    actual: bool = False
    descripcion: str = ""
    tipo: str | None = None  # formación: formal, curso o certificacion
    estado: str | None = None  # formación: completo o en_curso
    detalle: str | None = None  # formación formal en curso: el semestre
    fuente: str | None = None  # formación: el texto de la fuente

    @property
    def meses(self) -> int | None:
        if self.inicio is None or self.fin is None:
            return None
        return self.fin.indice - self.inicio.indice + 1

    @property
    def cuenta_como_uso(self) -> bool:
        """Los títulos formales no cuentan como uso de una habilidad (definición, §6.4)."""
        return self.clase in ("experiencia", "proyecto") or self.tipo in ("curso", "certificacion")


@dataclass
class PerfilCorpus:
    id: str
    vacante: str
    nivel: str
    particion: str
    posicion: int
    numero: int  # posición en todo el corpus (para el teléfono ficticio)
    perfil: str
    habilidades: list[str]
    experiencias: list[BloqueCorpus]
    proyectos: list[BloqueCorpus]
    formacion: list[BloqueCorpus]
    control_de: str | None = None
    requisito_retirado: str | None = None
    competencia_retirada: str | None = None

    @property
    def plantilla(self) -> str:
        return PLANTILLAS[self.posicion % 2]

    @property
    def estilo(self) -> str:
        return ESTILOS[self.posicion % 3]

    @property
    def encabezados(self) -> str:
        return "AB"[(self.posicion // 2) % 2]


def _menos(mes: Mes, meses: int) -> Mes:
    return Mes.desde_indice(mes.indice - meses)


def _meses_de_curso(titulo: str) -> int:
    horas = _HORAS.search(titulo)
    cantidad = int(horas.group(1)) if horas else 40
    return 2 if cantidad <= 60 else 3 if cantidad <= 120 else 6


def construir_perfiles(fuentes: dict[str, Any], complementos: dict[str, Any]) -> list[PerfilCorpus]:
    """Los 36 perfiles de la fuente con empresas, instituciones y fechas (reglas del docstring)."""
    referencia = Mes.desde_texto(complementos["fecha_referencia"])
    posiciones: dict[str, int] = {}
    perfiles = []
    for numero, fuente in enumerate(fuentes["perfiles"]):
        n = posiciones.get(fuente["vacante_id"], 0)
        posiciones[fuente["vacante_id"]] = n + 1
        extra = complementos["perfiles"][fuente["perfil_id"]]
        if len(extra["empresas"]) != len(fuente["experiencia"]):
            raise ValueError(f"{fuente['perfil_id']}: hay {len(fuente['experiencia'])} experiencias y "
                             f"{len(extra['empresas'])} empresas en los complementos.")
        if [f["fuente"] for f in extra["formacion"]] != fuente["formacion"]:
            raise ValueError(f"{fuente['perfil_id']}: la formación de los complementos no coincide con la fuente.")

        experiencias = []
        fin = referencia if n % 3 != 2 else _menos(referencia, 2 + n % 4)
        actual = n % 3 != 2
        for k, (experiencia, empresa) in enumerate(zip(fuente["experiencia"], extra["empresas"])):
            if k:
                fin, actual = _menos(experiencias[-1].inicio, 2), False
            meses = duracion_en_meses(experiencia["duracion"])
            if not meses:
                raise ValueError(f"{fuente['perfil_id']}: duración no reconocida «{experiencia['duracion']}».")
            experiencias.append(BloqueCorpus("experiencia", experiencia["rol"], empresa, _menos(fin, meses - 1), fin,
                                             actual, experiencia["descripcion"]))

        proyectos = []
        for j, proyecto in enumerate(fuente["proyectos"]):
            fin_p = _menos(referencia, 3 + 7 * j + n % 3)
            proyectos.append(BloqueCorpus("proyecto", proyecto["nombre"], None,
                                          _menos(fin_p, 3 + (j + n) % 3 - 1), fin_p, False, proyecto["descripcion"]))

        formacion, c = [], 0
        for estudio in extra["formacion"]:
            tipo = estudio["tipo"]
            bloque = BloqueCorpus("formacion", estudio["titulo"], estudio.get("institucion"), None, None,
                                  tipo=tipo, estado=estudio.get("estado"), detalle=estudio.get("detalle"),
                                  fuente=estudio["fuente"])
            if tipo == "formal":
                if estudio.get("inicio"):
                    bloque.inicio = Mes(estudio["inicio"], 1)
                if estudio.get("fin"):
                    bloque.fin = Mes(estudio["fin"], 12)
                if bloque.estado == "en_curso":
                    bloque.fin, bloque.actual = referencia, True
            elif tipo == "curso":
                bloque.fin = _menos(referencia, 9 + 6 * c + n % 4)
                bloque.inicio = _menos(bloque.fin, _meses_de_curso(estudio["titulo"]) - 1)
                bloque.estado = "completo"
                c += 1
            else:
                bloque.inicio = bloque.fin = _menos(referencia, 5 + 6 * c + n % 4)
                bloque.estado = "completo"
                c += 1
            formacion.append(bloque)

        perfiles.append(PerfilCorpus(fuente["perfil_id"], fuente["vacante_id"], fuente["nivel_adecuacion_previsto"],
                                     fuente["particion"], n, numero, fuente["perfil"], list(fuente["habilidades"]),
                                     experiencias, proyectos, formacion))
    return perfiles


def _reemplazar(texto: str, original: str, nuevo: str, donde: str) -> str:
    if original not in texto:
        raise ValueError(f"Edición de control: no se encontró «{original}» en {donde}.")
    return texto.replace(original, nuevo, 1)


def variante_de_control(perfil: PerfilCorpus, control: dict[str, Any]) -> PerfilCorpus:
    """El perfil sin la competencia retirada (ediciones exactas de la fuente). Las fechas no cambian."""
    variante = copy.deepcopy(perfil)
    variante.id = f"{perfil.id}-C"
    variante.control_de = perfil.id
    variante.requisito_retirado = control["requisito_id"]
    variante.competencia_retirada = control["competencia_retirada"]
    for edicion in control["ediciones"]:
        campo = edicion["campo"]
        if campo == "perfil":
            variante.perfil = _reemplazar(variante.perfil, edicion["original"], edicion["nuevo"], "el perfil")
        elif campo == "habilidades":
            variante.habilidades.remove(edicion["eliminar"])
        elif campo in ("experiencia", "proyectos"):
            bloques = variante.experiencias if campo == "experiencia" else variante.proyectos
            if "eliminar" in edicion:
                quitar = [b for b in bloques if b.titulo == edicion["eliminar"]]
                if not quitar:
                    raise ValueError(f"Edición de control: no hay un proyecto «{edicion['eliminar']}».")
                bloques.remove(quitar[0])
            else:
                con_texto = [b for b in bloques if edicion["original"] in b.descripcion]
                if not con_texto:
                    raise ValueError(f"Edición de control: no se encontró «{edicion['original']}» en {campo}.")
                bloque = con_texto[0]
                bloque.descripcion = _reemplazar(bloque.descripcion, edicion["original"], edicion["nuevo"], campo).strip()
        elif campo == "formacion":
            quitar = [b for b in variante.formacion if b.fuente == edicion["eliminar"]]
            if not quitar:
                raise ValueError(f"Edición de control: no hay un estudio «{edicion['eliminar']}».")
            variante.formacion.remove(quitar[0])
        else:
            raise ValueError(f"Edición de control con un campo desconocido: {campo}.")
    return variante


# =============================================================================
# Texto de la hoja de vida
# =============================================================================
def _mes(mes: Mes, estilo: str) -> str:
    if estilo == "texto":
        return f"{_NOMBRES_MES[mes.mes - 1]} de {mes.anio}"
    if estilo == "numerico":
        return f"{mes.mes:02d}/{mes.anio}"
    return f"{_ABREVIATURAS[mes.mes - 1]} {mes.anio}"


def _rango(bloque: BloqueCorpus, estilo: str) -> str:
    fin = _ACTUAL[estilo] if bloque.actual else _mes(bloque.fin, estilo)
    return f"{_mes(bloque.inicio, estilo)}{_GUION[estilo]}{fin}"


def _mayuscula(texto: str) -> str:
    return texto[:1].upper() + texto[1:]


def _fechas_formal(bloque: BloqueCorpus, estilo: str) -> str:
    if bloque.estado == "en_curso":
        return f"{bloque.inicio.anio}{_GUION[estilo]}{_ACTUAL[estilo]} (en curso, {bloque.detalle})"
    if bloque.inicio is None:
        return str(bloque.fin.anio)
    return f"{bloque.inicio.anio}{_GUION[estilo]}{bloque.fin.anio} (título obtenido)"


def _renglones_estudio(bloque: BloqueCorpus, estilo: str) -> str:
    if bloque.tipo == "formal":
        fechas = _fechas_formal(bloque, estilo)
    elif bloque.tipo == "curso":
        fechas = _rango(bloque, estilo)
    else:
        fechas = _mes(bloque.inicio, estilo)
    nombre = f"{bloque.titulo} — {bloque.entidad}" if bloque.entidad else bloque.titulo
    return f"{nombre} | {fechas}"


@dataclass
class HojaCorpus:
    """Texto de la hoja de vida y la posición de cada bloque en él (para la verdad de referencia)."""

    texto: str
    bloques: list[tuple[BloqueCorpus, int, int]]
    columna_izquierda: list[str]  # encabezados que van a la izquierda en la plantilla de dos columnas


def escribir_hoja(perfil: PerfilCorpus) -> HojaCorpus:
    """Texto de la hoja de vida (el TXT del corpus) con la plantilla, el estilo y los encabezados del perfil."""
    estilo, dos_columnas = perfil.estilo, perfil.plantilla == "dos_columnas"
    titulos = ENCABEZADOS[perfil.encabezados]
    vineta = "• " if dos_columnas else "- "
    partes: list[str] = []
    bloques: list[tuple[BloqueCorpus, int, int]] = []
    largo = 0

    def agregar(texto: str, bloque: BloqueCorpus | None = None) -> None:
        nonlocal largo
        if bloque is not None:
            bloques.append((bloque, largo, largo + len(texto)))
        partes.append(texto)
        largo += len(texto) + 1  # el salto de renglón

    correo = perfil.id.lower().replace("-", ".")
    agregar(f"Hoja de vida ficticia {perfil.id}")
    agregar("Corpus sintético de evaluación del prototipo Analizador CV")
    agregar(f"Correo: {correo}@correo.example | Teléfono: +57 300 555 {100 + perfil.numero:04d}")
    agregar("")
    agregar(titulos["perfil"])
    agregar(perfil.perfil)
    agregar("")
    agregar(titulos["habilidades"])
    for habilidad in perfil.habilidades:
        agregar(habilidad if perfil.encabezados == "A" else f"- {habilidad}")
    agregar("")
    agregar(titulos["experiencia"])
    for i, bloque in enumerate(perfil.experiencias):
        if i:
            agregar("")
        nombre = f"{bloque.titulo} — {bloque.entidad}" if bloque.entidad else bloque.titulo
        actividades = "\n".join(vineta + o for o in _ORACIONES.split(bloque.descripcion.strip()))
        if dos_columnas:
            separado = f"{bloque.titulo} | {bloque.entidad}" if bloque.entidad else bloque.titulo
            agregar(f"{separado} | {_rango(bloque, estilo)}\n{actividades}", bloque)
        else:
            agregar(f"{nombre}\n{_mayuscula(_rango(bloque, estilo))}\n{actividades}", bloque)
    agregar("")
    agregar(titulos["proyectos"])
    for i, bloque in enumerate(perfil.proyectos):
        if i:
            agregar("")
        actividades = "\n".join(vineta + o for o in _ORACIONES.split(bloque.descripcion.strip()))
        cabecera = (f"{bloque.titulo} | {_rango(bloque, estilo)}" if dos_columnas
                    else f"{bloque.titulo} ({_rango(bloque, estilo)})")
        agregar(f"{cabecera}\n{actividades}", bloque)
    formales = [b for b in perfil.formacion if b.tipo == "formal" or not titulos["cursos"]]
    otros = [b for b in perfil.formacion if b not in formales]
    for titulo, grupo in ((titulos["formacion"], formales), (titulos["cursos"], otros)):
        if grupo:
            agregar("")
            agregar(titulo)
            for bloque in grupo:
                agregar(_renglones_estudio(bloque, estilo), bloque)
    return HojaCorpus("\n".join(partes) + "\n", bloques, [titulos["habilidades"]])


# =============================================================================
# Verdad de referencia de las habilidades
# =============================================================================
@dataclass
class EntradaLexico:
    nombre: str
    clase: str
    formas: dict[str, str]  # forma normalizada → nucleo u opcional
    excluir: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._patrones = [(patron_termino(f), nivel) for f, nivel in self.formas.items()]
        self._exclusiones = [patron_termino(e) for e in self.excluir]

    def nivel_en(self, texto_normalizado: str) -> str | None:
        """«nucleo» u «opcional» si alguna forma aparece en el texto (fuera de las frases excluidas); si no, None."""
        texto = texto_normalizado
        for exclusion in self._exclusiones:
            texto = exclusion.sub(lambda m: " " * len(m.group(0)), texto)
        niveles = {nivel for patron, nivel in self._patrones if patron.search(texto)}
        return "nucleo" if "nucleo" in niveles else ("opcional" if niveles else None)


def cargar_lexico(ruta: Path | None = None) -> list[EntradaLexico]:
    datos = leer_json(ruta or CARPETA_CORPUS / "lexico_referencia.json")
    entradas = []
    for crudo in datos["habilidades"]:
        formas = {normalizar(f): nivel for f, nivel in crudo["formas"].items()}
        if any(nivel not in ("nucleo", "opcional") for nivel in formas.values()):
            raise ValueError(f"Nivel desconocido en el léxico: {crudo['nombre']}")
        entradas.append(EntradaLexico(crudo["nombre"], crudo["clase"], formas,
                                      [normalizar(e) for e in crudo.get("excluir", [])]))
    return entradas


def etiquetar(texto: str, lexico: list[EntradaLexico]) -> dict[str, str]:
    """Habilidades del léxico presentes en el texto: nombre → nivel."""
    norma = normalizar(texto)
    encontradas = {}
    for entrada in lexico:
        nivel = entrada.nivel_en(norma)
        if nivel:
            encontradas[entrada.nombre] = nivel
    return encontradas


def _meses_union(bloques: list[BloqueCorpus]) -> int:
    """Meses de los bloques, sin contar dos veces los solapados (cálculo propio, independiente de la app)."""
    meses: set[int] = set()
    for bloque in bloques:
        if bloque.inicio is not None and bloque.fin is not None:
            meses.update(range(bloque.inicio.indice, bloque.fin.indice + 1))
    return len(meses)


def verdad_de(perfil: PerfilCorpus, hoja: HojaCorpus, lexico: list[EntradaLexico]) -> dict[str, Any]:
    """Verdad de referencia de una hoja de vida (lo que debería extraer un método perfecto)."""
    por_nombre = {e.nombre: e for e in lexico}
    en_documento = etiquetar(hoja.texto, lexico)
    usos: dict[str, list[BloqueCorpus]] = {}
    for bloque, inicio, fin in hoja.bloques:
        if bloque.cuenta_como_uso:
            for nombre in etiquetar(hoja.texto[inicio:fin], lexico):
                usos.setdefault(nombre, []).append(bloque)
    habilidades = [{"nombre": nombre, "nivel": nivel, "clase": por_nombre[nombre].clase,
                    "formas": sorted(por_nombre[nombre].formas), "meses": _meses_union(usos.get(nombre, []))}
                   for nombre, nivel in en_documento.items()]
    texto_mes = lambda m: str(m) if m else None  # noqa: E731
    return {
        "id": perfil.id, "vacante": perfil.vacante, "nivel": perfil.nivel, "particion": perfil.particion,
        "posicion": perfil.posicion, "plantilla": perfil.plantilla, "estilo_fechas": perfil.estilo,
        "encabezados": perfil.encabezados, "control_de": perfil.control_de,
        "requisito_retirado": perfil.requisito_retirado, "competencia_retirada": perfil.competencia_retirada,
        "palabras": len(hoja.texto.split()),
        "habilidades": habilidades,
        "experiencias": [{"cargo": b.titulo, "empresa": b.entidad, "inicio": texto_mes(b.inicio),
                          "fin": texto_mes(b.fin), "actual": b.actual, "meses": b.meses} for b in perfil.experiencias],
        "proyectos": [{"nombre": b.titulo, "inicio": texto_mes(b.inicio), "fin": texto_mes(b.fin), "meses": b.meses}
                      for b in perfil.proyectos],
        "formacion": [{"titulo": b.titulo, "institucion": b.entidad, "tipo": b.tipo, "estado": b.estado,
                       "inicio": texto_mes(b.inicio), "fin": texto_mes(b.fin)} for b in perfil.formacion],
        "meses_experiencia": _meses_union(perfil.experiencias),
    }


# =============================================================================
# Ofertas
# =============================================================================
def escribir_oferta(vacante: dict[str, Any], extra: dict[str, Any], version: str) -> str:
    """Texto de la oferta: A, estructurada con encabezados y viñetas; B, narrativa en párrafos."""
    if version == "B":
        return "\n\n".join(extra["narrativa"]) + "\n"
    renglones = [f"{extra['cargo']} — {extra['empresa']} (oferta ficticia)", "", vacante["descripcion"], "",
                 "Requisitos:"]
    renglones += [f"- {r['texto']}" for r in vacante["requisitos"]]
    renglones += [f"- {extra['experiencia']}", "", "Deseable:"]
    renglones += [f"- {d}" for d in extra["deseables"]]
    renglones += ["", "Ofrecemos:", f"- {extra['ofrecemos']}"]
    return "\n".join(renglones) + "\n"


def verdad_oferta(vacante_id: str, extra: dict[str, Any], version: str, texto: str) -> dict[str, Any]:
    requisitos = []
    for requisito in extra["requisitos"]:
        ancla = requisito[f"ancla_{version}"]
        if normalizar(ancla) not in normalizar(texto):
            raise ValueError(f"{vacante_id} {version}: el ancla «{ancla}» de {requisito['id']} no está en la oferta.")
        requisitos.append({"id": requisito["id"], "tipo": requisito["tipo"], "categoria": requisito["categoria"],
                           "ancla": ancla})
    return {"id": f"{vacante_id}_{version}", "vacante": vacante_id, "version": version,
            "archivo": f"ofertas/{vacante_id}_{version}.txt", "requisitos": requisitos}


# =============================================================================
# Corpus completo
# =============================================================================
@dataclass
class CorpusGenerado:
    hojas: list[tuple[PerfilCorpus, HojaCorpus, dict[str, Any]]]
    ofertas: list[tuple[str, dict[str, Any]]]  # (texto, verdad)
    huella_fuentes: str


def generar(carpeta: Path | None = None) -> CorpusGenerado:
    """Arma los perfiles, sus variantes de control, los textos, las ofertas y la verdad de referencia."""
    carpeta = Path(carpeta or CARPETA_CORPUS)
    fuentes = leer_json(carpeta / "fuentes" / "perfiles_base.json")
    vacantes = leer_json(carpeta / "fuentes" / "vacantes.json")
    complementos = leer_json(carpeta / "complementos.json")
    lexico = cargar_lexico(carpeta / "lexico_referencia.json")
    perfiles = construir_perfiles(fuentes, complementos)
    controles = {p["perfil_id"]: p["control_competencia"] for p in fuentes["perfiles"] if p.get("control_competencia")}
    perfiles += [variante_de_control(p, controles[p.id]) for p in list(perfiles) if p.id in controles]
    hojas = []
    for perfil in perfiles:
        hoja = escribir_hoja(perfil)
        hojas.append((perfil, hoja, verdad_de(perfil, hoja, lexico)))
    ofertas = []
    for vacante in vacantes["vacantes"]:
        extra = complementos["ofertas"][vacante["vacante_id"]]
        for version in ("A", "B"):
            texto = escribir_oferta(vacante, extra, version)
            ofertas.append((texto, verdad_oferta(vacante["vacante_id"], extra, version, texto)))
    huella = hash_objeto({"fuentes": fuentes, "vacantes": vacantes, "complementos": complementos,
                          "lexico": leer_json(carpeta / "lexico_referencia.json")})
    return CorpusGenerado(hojas, ofertas, huella)


# =============================================================================
# Lectura del corpus generado (la usa la evaluación)
# =============================================================================
@dataclass
class Corpus:
    carpeta: Path
    datos: dict[str, Any]

    @property
    def documentos(self) -> list[dict[str, Any]]:
        return self.datos["documentos"]

    @property
    def ofertas(self) -> list[dict[str, Any]]:
        return self.datos["ofertas"]

    @property
    def fecha_referencia(self) -> Mes:
        return Mes.desde_texto(self.datos["fecha_referencia"])

    def documento(self, identificador: str) -> dict[str, Any]:
        return next(d for d in self.documentos if d["id"] == identificador)

    def oferta(self, identificador: str) -> dict[str, Any]:
        return next(o for o in self.ofertas if o["id"] == identificador)

    def ruta(self, relativa: str) -> Path:
        return self.carpeta / relativa

    def texto_oferta(self, identificador: str) -> str:
        return self.ruta(self.oferta(identificador)["archivo"]).read_text(encoding="utf-8")


def cargar_corpus(carpeta: Path | str | None = None) -> Corpus:
    carpeta = Path(carpeta or CARPETA_CORPUS)
    ruta = carpeta / "verdad_referencia.json"
    if not ruta.exists():
        raise FileNotFoundError(ruta)
    return Corpus(carpeta, leer_json(ruta))
