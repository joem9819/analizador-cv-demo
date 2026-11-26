"""Motor de análisis: orquesta las etapas y arma el resultado (RF02 a RF10, RNF03 y RNF04).

Con modelo (método «llm») hace 3 llamadas en modo general y 4 en modo específico:
1. extraccion_cv: experiencias, proyectos, formación, habilidades e idiomas;
2. requisitos_oferta (solo en modo específico; se puede reutilizar con varias hojas de vida);
3. evaluacion_general o evaluacion_especifica: niveles de redacción y cobertura de requisitos;
4. mejoras (opcional).
Sin modelo (método «linea_base»), esas mismas salidas las producen reglas (linea_base.py).
Todo lo demás lo hace el código: verificación contra el texto, temporalidad, tags, puntuación,
compatibilidad, fortalezas y debilidades y la guardia contra la información inventada.

Si una llamada no da una respuesta válida tras los reintentos, el análisis falla con ErrorAnalisis:
nunca se inventa un puntaje (RNF03). La huella de la configuración (motor, rúbrica, diccionario,
plantillas, modelo y fecha de referencia) queda en cada resultado (RNF04).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable

from analizador_cv import bd
from analizador_cv.analisis import guardia, linea_base
from analizador_cv.analisis.compatibilidad import (
    Requisito,
    asignar_relevancia_oferta,
    porcentaje,
    procesar_cobertura,
    procesar_requisitos,
)
from analizador_cv.analisis.diagnostico import (
    fortalezas_y_debilidades,
    sugerencias_del_programa,
    texto_diagnostico,
    texto_experiencia,
)
from analizador_cv.analisis.esquemas import (
    ESQUEMA_EVALUACION_GENERAL,
    ESQUEMA_EXTRACCION,
    ESQUEMA_MEJORAS,
    ESQUEMA_REQUISITOS,
    esquema_evaluacion_especifica,
    recortar,
    sin_limites,
    validador_evaluacion,
    validar_requisitos,
)
from analizador_cv.analisis.extraccion import procesar_extraccion
from analizador_cv.analisis.fechas import Mes, meses_acumulados
from analizador_cv.analisis.habilidades import Diccionario, cargar_diccionario
from analizador_cv.analisis.plantillas import Plantilla, cargar_plantillas
from analizador_cv.analisis.puntuacion import PARAMETROS, VERSION_RUBRICA, Redaccion, calcular_puntuacion
from analizador_cv.analisis.secciones import detectar_secciones
from analizador_cv.analisis.temporalidad import construir_tags
from analizador_cv.configuracion import Configuracion
from analizador_cv.documentos import Documento
from analizador_cv.llm.modelo import ModeloLLM
from analizador_cv.llm.salida import ErrorSalidaLLM, Intento
from analizador_cv.ofertas import Oferta
from analizador_cv.utilidades import ahora_iso, hash_objeto, hash_texto, json_canonico

#: 2 (Etapa 7): la guardia descarta los cambios de contenido y la línea base ignora el título de la oferta.
#: 3 (Etapa 8): la línea base reconoce las cabeceras «Cargo | Empresa | fechas» y los estudios partidos en dos
#: renglones, y el lector de PDF tolera el encabezado a todo el ancho de una hoja de vida corta a dos columnas.
#: 4 (después de la evaluación, 20/10/2026): los textos de las respuestas del modelo llegan con las entidades HTML
#: decodificadas (llm/salida.py). La evaluación técnica se hizo con la versión 3.
VERSION_MOTOR = "4"
#: registrar(etapa, intento, mensajes, opciones): guarda cada llamada (bd.registrar_llamada).
Registrar = Callable[[str, Intento, list[dict[str, str]], dict[str, Any]], None]
ETAPAS_POR_MODO = {"general": ("extraccion_cv", "evaluacion_general", "mejoras"),
                   "especifico": ("extraccion_cv", "requisitos_oferta", "evaluacion_especifica", "mejoras")}


class ErrorAnalisis(Exception):
    """Una etapa no obtuvo una respuesta válida del modelo: el análisis no tiene puntaje."""

    def __init__(self, etapa: str, mensaje: str, intentos: list[Intento] | None = None):
        super().__init__(f"Etapa «{etapa}»: {mensaje}")
        self.etapa = etapa
        self.mensaje = mensaje
        self.intentos = intentos or []


@dataclass
class RequisitosOferta:
    """Requisitos verificados de una oferta. Se pueden reutilizar al analizar varias hojas de vida."""

    requisitos: list[Requisito]
    cargo: str | None
    empresa: str | None
    hash_oferta: str
    metodo: str
    descartados: list[dict[str, str]] = field(default_factory=list)
    salida_cruda: dict[str, Any] | None = None

    def a_dict(self) -> dict[str, Any]:
        return {"requisitos": [r.a_dict() for r in self.requisitos], "cargo": self.cargo, "empresa": self.empresa,
                "hash_oferta": self.hash_oferta, "metodo": self.metodo, "descartados": [dict(d) for d in self.descartados]}

    @classmethod
    def desde_dict(cls, datos: dict[str, Any]) -> "RequisitosOferta":
        """Reconstruye los requisitos guardados en un resultado (resultado["compatibilidad"]["oferta"])."""
        return cls([Requisito.desde_dict(r) for r in datos["requisitos"]], datos.get("cargo"), datos.get("empresa"),
                   datos["hash_oferta"], datos["metodo"], list(datos.get("descartados") or []))


def _referencia_configurada(cfg: Configuracion) -> Mes | None:
    valor = (cfg.datos.get("analisis") or {}).get("fecha_referencia")
    return Mes.desde_texto(valor) if valor else None


def _texto_requisitos(requisitos: list[Requisito]) -> str:
    return "\n".join(f"- {r.id} ({r.tipo}; {r.categoria}): {r.texto}" for r in requisitos)


def _contiene_literal(texto: str, fragmento: str) -> bool:
    """Búsqueda exacta (con tildes), sin distinguir mayúsculas y con los espacios unificados."""
    unificar = lambda t: re.sub(r"\s+", " ", t).strip().casefold()  # noqa: E731
    return bool(fragmento.strip()) and unificar(fragmento) in unificar(texto)


def redaccion_del_modelo(datos: dict[str, Any], texto: str) -> Redaccion:
    """Niveles del modelo y errores de ortografía verificados: el fragmento con el error debe estar
    literalmente (con sus tildes) en el texto y ser distinto de la corrección."""
    errores, descartados = [], 0
    for error in datos["errores_ortografia"]:
        fragmento, correccion = error["fragmento"].strip(), error["correccion"].strip()
        if fragmento and fragmento != correccion and _contiene_literal(texto, fragmento):
            errores.append({"fragmento": fragmento, "correccion": correccion})
        else:
            descartados += 1
    evidencia = {clave: [e for e in datos[clave]["evidencia"] if _contiene_literal(texto, e)]
                 for clave in ("verbos_accion", "claridad")}
    return Redaccion(datos["verbos_accion"]["nivel"], datos["claridad"]["nivel"], errores, evidencia,
                     fuente="modelo", errores_descartados=descartados)


class Analizador:
    """Analiza hojas de vida con un modelo (ModeloLLM) o, sin él, con la línea base por palabras clave."""

    def __init__(self, cfg: Configuracion, modelo: ModeloLLM | None = None, *, referencia: Mes | None = None,
                 diccionario: Diccionario | None = None, complementar: bool = True):
        """«complementar»: agrega las habilidades del diccionario escritas en el texto que el modelo omitió
        (extraccion.py). La evaluación lo apaga para medir la extracción del modelo solo."""
        self.cfg = cfg
        self.modelo = modelo
        self.complementar = complementar
        self.metodo = "llm" if modelo is not None else "linea_base"
        self.referencia = referencia or _referencia_configurada(cfg) or Mes.hoy()
        self.diccionario = diccionario or cargar_diccionario()
        self.plantillas: dict[str, Plantilla] = cargar_plantillas(cfg.ruta_plantillas) if modelo is not None else {}
        self._llamadas: list[dict[str, Any]] = []

    # ------------------------------------------------------------- identidad
    def huella(self, modo: str) -> str:
        plantillas = {e: [p.version, p.huella] for e, p in self.plantillas.items() if e in ETAPAS_POR_MODO[modo]}
        return hash_objeto({"motor": VERSION_MOTOR, "rubrica": VERSION_RUBRICA, "parametros": PARAMETROS,
                            "diccionario": self.diccionario.huella, "plantillas": plantillas,
                            "complementar": self.complementar,
                            "modelo": self.modelo.huella if self.modelo else "linea_base",
                            "referencia": str(self.referencia)})

    def informacion_modelo(self) -> dict[str, Any] | None:
        if self.modelo is None:
            return None
        perfil = self.modelo.perfil
        return {"perfil": perfil["nombre"], "proveedor": perfil.get("proveedor"), "modelo": perfil["modelo"],
                "huella": self.modelo.huella, "modo_salida": (self.modelo.congelado or {}).get("modo_salida")}

    # ---------------------------------------------------------------- modelo
    def _anotar(self, etapa: str, intentos: list[Intento]) -> None:
        respuestas = [i.respuesta for i in intentos if i.respuesta is not None]
        self._llamadas.append({
            "etapa": etapa, "intentos": len(intentos), "estado": intentos[-1].estado if intentos else None,
            "tokens_entrada": sum(r.tokens_entrada or 0 for r in respuestas),
            "tokens_salida": sum(r.tokens_salida or 0 for r in respuestas),
            "tokens_total": sum(r.tokens_total or 0 for r in respuestas),
            "latencia_s": round(sum(i.latencia_s or 0 for i in intentos), 3),
        })

    def _pedir(self, etapa: str, valores: dict[str, str], esquema: dict[str, Any], validador: Any,
               registrar: Registrar | None) -> dict[str, Any]:
        assert self.modelo is not None
        # El prompt lleva el esquema completo (con los máximos); la API, el esquema sin maxItems (esquemas.py).
        mensajes = self.plantillas[etapa].mensajes(**valores, FORMATO=json_canonico(esquema))
        try:
            resultado = self.modelo.pedir(
                mensajes, sin_limites(esquema), nombre=etapa, validador=validador,
                registrar=(lambda intento, m, o: registrar(etapa, intento, m, o)) if registrar else None)
        except ErrorSalidaLLM as error:
            self._anotar(etapa, error.intentos)
            raise ErrorAnalisis(etapa, error.mensaje, error.intentos) from error
        self._anotar(etapa, resultado.intentos)
        return recortar(resultado.datos, esquema)

    # ----------------------------------------------------------------- etapas
    def requisitos_de(self, texto_oferta: str, *, registrar: Registrar | None = None) -> RequisitosOferta:
        """Requisitos de la oferta (con modelo o por reglas), verificados contra su texto."""
        if self.modelo is not None:
            crudo = self._pedir("requisitos_oferta", {"OFERTA": texto_oferta}, ESQUEMA_REQUISITOS, validar_requisitos,
                                registrar)
        else:
            crudo = linea_base.requisitos(texto_oferta, self.diccionario)
        requisitos, descartados = procesar_requisitos(crudo, texto_oferta, diccionario=self.diccionario)
        if not requisitos:
            raise ErrorAnalisis("requisitos_oferta", "No se identificaron requisitos verificables en la oferta. "
                                                     "Revise que el texto pegado sea la descripción completa.")
        return RequisitosOferta(requisitos, crudo.get("cargo"), crudo.get("empresa"), hash_texto(texto_oferta),
                                self.metodo, descartados, crudo)

    def analizar(self, texto: str, *, oferta: str | None = None, requisitos: RequisitosOferta | None = None,
                 con_mejoras: bool = True, advertencias: list[str] | tuple[str, ...] = (),
                 registrar: Registrar | None = None) -> dict[str, Any]:
        """Analiza el texto de una hoja de vida. Con «oferta» o «requisitos», en modo específico."""
        self._llamadas = []
        modo = "especifico" if (oferta or requisitos) else "general"
        salidas: dict[str, Any] = {}
        secciones = detectar_secciones(texto)

        if self.modelo is not None:
            salidas["extraccion_cv"] = self._pedir("extraccion_cv", {"CV": texto}, ESQUEMA_EXTRACCION, None, registrar)
        else:
            salidas["extraccion_cv"] = linea_base.extraer(texto, secciones, self.diccionario)
        extraccion = procesar_extraccion(salidas["extraccion_cv"], texto, referencia=self.referencia,
                                         diccionario=self.diccionario, secciones=secciones,
                                         complementar=self.complementar)

        if modo == "especifico" and requisitos is None:
            requisitos = self.requisitos_de(oferta or "", registrar=registrar)
            salidas["requisitos_oferta"] = requisitos.salida_cruda

        coberturas = None
        if self.modelo is not None:
            if modo == "especifico":
                ids = [r.id for r in requisitos.requisitos]
                salidas["evaluacion"] = self._pedir(
                    "evaluacion_especifica",
                    {"CV": texto, "REQUISITOS": _texto_requisitos(requisitos.requisitos),
                     "EXPERIENCIA_CALCULADA": texto_experiencia(extraccion)},
                    esquema_evaluacion_especifica(ids), validador_evaluacion(ids), registrar)
            else:
                salidas["evaluacion"] = self._pedir("evaluacion_general", {"CV": texto}, ESQUEMA_EVALUACION_GENERAL,
                                                    None, registrar)
            redaccion = redaccion_del_modelo(salidas["evaluacion"], texto)
            crudos_cobertura = salidas["evaluacion"].get("requisitos")
        else:
            redaccion = linea_base.redaccion(extraccion)
            crudos_cobertura = (linea_base.cobertura(requisitos.requisitos, extraccion, texto, self.diccionario)
                                if modo == "especifico" else None)
            salidas["evaluacion"] = {"redaccion": redaccion.a_dict(), "requisitos": crudos_cobertura}
        if modo == "especifico":
            coberturas = procesar_cobertura(
                requisitos.requisitos, crudos_cobertura, texto, diccionario=self.diccionario,
                meses_experiencia=meses_acumulados(e.periodo for e in extraccion.experiencias),
                experiencias=[e.titulo for e in extraccion.experiencias])

        tags = construir_tags(extraccion, texto, referencia=self.referencia, diccionario=self.diccionario)
        if modo == "especifico":
            asignar_relevancia_oferta(tags, requisitos.requisitos, self.diccionario)
        palabras = len(texto.split())
        puntuacion = calcular_puntuacion(
            secciones=secciones, extraccion=extraccion, redaccion=redaccion, tags=tags, palabras=palabras,
            referencia=self.referencia, problema_extraccion=any("sin texto seleccionable" in a for a in advertencias))
        fortalezas, debilidades = fortalezas_y_debilidades(puntuacion, tags, coberturas)

        sugerencias: list[guardia.Sugerencia] = []
        if con_mejoras:
            if self.modelo is not None:
                salidas["mejoras"] = self._pedir("mejoras", {"CV": texto, "DIAGNOSTICO": texto_diagnostico(
                    puntuacion, redaccion, coberturas)}, ESQUEMA_MEJORAS, None, registrar)
                sugerencias = guardia.revisar(salidas["mejoras"]["sugerencias"], texto, diccionario=self.diccionario)
            del_programa = sugerencias_del_programa(extraccion=extraccion, puntuacion=puntuacion, coberturas=coberturas,
                                                    texto=texto, diccionario=self.diccionario,
                                                    incluir_generales=self.modelo is None)
            sugerencias += guardia.revisar(del_programa, texto, diccionario=self.diccionario, origen="programa",
                                           primer_numero=len(sugerencias) + 1)

        return {
            "version_motor": VERSION_MOTOR,
            "metodo": self.metodo,
            "modo": modo,
            "fecha": ahora_iso(),
            "fecha_referencia": str(self.referencia),
            "huella_configuracion": self.huella(modo),
            "modelo": self.informacion_modelo(),
            "texto": {"caracteres": len(texto), "palabras": palabras, "hash": hash_texto(texto)},
            "advertencias": list(advertencias),
            "secciones": {"presentes": sorted(secciones.presentes),
                          "encabezados": [{"seccion": e.seccion, "renglon": e.renglon + 1} for e in secciones.encabezados]},
            "extraccion": extraccion.a_dict(),
            "redaccion": redaccion.a_dict(),
            "tags": [t.a_dict() for t in tags],
            "puntuacion": puntuacion.a_dict(),
            "compatibilidad": None if coberturas is None else {
                "porcentaje": porcentaje(coberturas), "oferta": requisitos.a_dict(),
                "requisitos": [c.a_dict() for c in coberturas]},
            "fortalezas": fortalezas,
            "debilidades": debilidades,
            "sugerencias": [s.a_dict() for s in sugerencias],
            "guardia": guardia.resumen(sugerencias) if con_mejoras else None,
            "llamadas": list(self._llamadas),
            "salidas": salidas,
        }


def analizar_y_guardar(analizador: Analizador, conexion: sqlite3.Connection, documento: Documento, *,
                       enmascarado: bool = False, oferta: Oferta | None = None,
                       requisitos: RequisitosOferta | None = None, analisis_previo_id: int | None = None,
                       origen_id: int | None = None, con_mejoras: bool = True,
                       registrar: Registrar | None = None) -> tuple[int, int, dict[str, Any]]:
    """Analiza y guarda en el historial el documento, la oferta, el análisis y cada llamada al modelo.

    En modo específico se pasa la oferta y, si ya se extrajeron, sus requisitos (para no pedirlos de
    nuevo). Si el análisis falla, queda «fallido» (sin puntaje) con sus llamadas y se vuelve a lanzar
    el error. «registrar» también recibe cada llamada después de guardarla (la app la usa para mostrar
    el avance). Devuelve (analisis_id, documento_id, resultado).
    """
    if requisitos is not None and oferta is None:
        raise ValueError("Para guardar un análisis específico hace falta la oferta, no solo sus requisitos.")
    modo = "especifico" if oferta is not None else "general"
    documento_id = bd.guardar_documento(conexion, documento, enmascarado=enmascarado, origen_id=origen_id)
    oferta_id = bd.guardar_oferta(conexion, oferta) if oferta is not None else None
    informacion = analizador.informacion_modelo()
    analisis_id = bd.crear_analisis(
        conexion, documento_id=documento_id, modo=modo, metodo=analizador.metodo,
        huella_configuracion=analizador.huella(modo), oferta_id=oferta_id,
        perfil=informacion["perfil"] if informacion else None, modelo=informacion["modelo"] if informacion else None,
        analisis_previo_id=analisis_previo_id)

    def guardar_llamada(etapa: str, intento: Intento, mensajes: list[dict[str, str]], opciones: dict[str, Any]) -> None:
        bd.registrar_llamada(conexion, intento, mensajes, opciones, etapa=etapa, perfil=analizador.modelo.perfil,
                             analisis_id=analisis_id)
        if registrar is not None:
            registrar(etapa, intento, mensajes, opciones)

    try:
        resultado = analizador.analizar(documento.texto, oferta=oferta.texto if oferta is not None else None,
                                        requisitos=requisitos, con_mejoras=con_mejoras,
                                        advertencias=documento.advertencias,
                                        registrar=guardar_llamada if analizador.modelo is not None else None)
    except ErrorAnalisis as error:
        bd.finalizar_analisis(conexion, analisis_id, error=str(error))
        conexion.commit()  # el análisis fallido y sus llamadas quedan aunque el error se propague
        raise
    compatibilidad = resultado["compatibilidad"]["porcentaje"] if resultado["compatibilidad"] else None
    bd.finalizar_analisis(conexion, analisis_id, resultado=resultado, puntuacion=resultado["puntuacion"]["total"],
                          compatibilidad=compatibilidad)
    return analisis_id, documento_id, resultado
