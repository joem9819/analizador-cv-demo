"""Modelo SIMULADO para verificar la evaluación completa sin red y sin cuota (Etapa 8, primera corrida).

Responde por la API del backend (chat con mensajes y esquema) como si fuera un modelo, pero con reglas:
- extraccion_cv y requisitos_oferta: las mismas reglas de la línea base;
- evaluacion_general y evaluacion_especifica: niveles de redacción por la proporción de viñetas que
  empiezan con un verbo de acción y por su largo; cada requisito «cubierto» si alguna de sus habilidades
  está en una viñeta, «mencionado» si está en otra parte y «ausente» si no está;
- mejoras: hasta 3 reescrituras que conjugan el sustantivo inicial («Desarrollo de módulos…» →
  «Desarrollé módulos…»), que la guardia aprueba, y una que agrega una herramienta que no está en la
  hoja de vida, que la guardia debe descartar; más un consejo de estructura.
Sirve para probar todo el camino del modelo (caché, reintentos, guardia, antes/después, informe), NO
para medir: sus resultados se marcan como simulados.

Los tokens son una ESTIMACIÓN para Groq calibrada con la corrida real de la Etapa 6 (caracteres por token
de entrada y tokens de salida por etapa, escalados por el largo de la hoja de vida o de la oferta); en
«uso» va también la estimación para Gemini. La latencia simulada es 0.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from analizador_cv.analisis import linea_base
from analizador_cv.analisis.cotejo import normalizar
from analizador_cv.analisis.habilidades import Diccionario, cargar_diccionario
from analizador_cv.analisis.secciones import detectar_secciones
from analizador_cv.configuracion import Configuracion
from analizador_cv.llm.backends import InformacionModelo, RespuestaBackend
from analizador_cv.llm.modelo import ModeloLLM

#: Calibración con la corrida real de la Etapa 6 (hoja de vida de 1274 caracteres y oferta de 498).
CARACTERES_POR_TOKEN_ENTRADA = {"groq_gpt_oss_120b": 2.8, "gemini_flash_lite": 3.7}
TOKENS_SALIDA = {
    "groq_gpt_oss_120b": {"extraccion_cv": 1596, "requisitos_oferta": 372, "evaluacion_especifica": 828,
                          "evaluacion_general": 600, "mejoras": 746},
    "gemini_flash_lite": {"extraccion_cv": 534, "requisitos_oferta": 259, "evaluacion_especifica": 424,
                          "evaluacion_general": 300, "mejoras": 254},
}
LARGO_REFERENCIA = {"extraccion_cv": 1274, "requisitos_oferta": 498}
_CONTENIDO = re.compile(r"<<<\n(.*)\n>>>", re.DOTALL)
_REQUISITO = re.compile(r"^- (\S+) \((obligatorio|deseable); (\w+)\): (.+)$", re.MULTILINE)
_VINETA = re.compile(r"^\s*[-•]\s+(.+)$", re.MULTILINE)
#: Sustantivo inicial (normalizado) → verbo en primera persona que conserva sus 4 primeras letras.
VERBOS = {"desarrollo": "Desarrollé", "correccion": "Corregí", "construccion": "Construí", "diseño": "Diseñé",
          "ejecucion": "Ejecuté", "atencion": "Atendí", "registro": "Registré", "elaboracion": "Elaboré",
          "preparacion": "Preparé", "instalacion": "Instalé", "publicacion": "Publiqué", "configuracion": "Configuré",
          "redaccion": "Redacté", "automatizacion": "Automaticé", "implementacion": "Implementé",
          "actualizacion": "Actualicé", "revision": "Revisé", "solucion": "Solucioné", "supervision": "Supervisé",
          "reemplazo": "Reemplacé", "verificacion": "Verifiqué", "apoyo": "Apoyé", "participacion": "Participé"}
_NOMINAL = re.compile(r"^(\w+)\s+(de|del)\s+(.+)$", re.DOTALL)


def _estimacion(etapa: str, mensajes: list[dict[str, str]], contenido: str) -> dict[str, dict[str, int]]:
    entrada = sum(len(m["content"]) for m in mensajes)
    escala = len(contenido) / LARGO_REFERENCIA[etapa] if etapa in LARGO_REFERENCIA else 1.0
    resultado = {}
    for perfil, caracteres in CARACTERES_POR_TOKEN_ENTRADA.items():
        tokens_entrada = math.ceil(entrada / caracteres)
        tokens_salida = math.ceil(TOKENS_SALIDA[perfil][etapa] * escala)
        resultado[perfil] = {"entrada": tokens_entrada, "salida": tokens_salida, "total": tokens_entrada + tokens_salida}
    return resultado


def _niveles(vinetas: list[str]) -> dict[str, Any]:
    if not vinetas:
        return {"verbos_accion": {"nivel": 0, "evidencia": []}, "claridad": {"nivel": 1, "evidencia": []},
                "errores_ortografia": []}
    con_verbo = sum(normalizar(v).startswith(linea_base.VERBOS_ACCION) for v in vinetas) / len(vinetas)
    verbos = 3 if con_verbo >= 0.8 else 2 if con_verbo >= 0.5 else 1 if con_verbo >= 0.2 else 0
    largo = sum(len(v.split()) for v in vinetas) / len(vinetas)
    claridad = 3 if largo <= 20 else 2 if largo <= 30 else 1 if largo <= 45 else 0
    return {"verbos_accion": {"nivel": verbos, "evidencia": vinetas[:1]},
            "claridad": {"nivel": claridad, "evidencia": vinetas[:1]}, "errores_ortografia": []}


def reescribir(vineta: str) -> str | None:
    """«Desarrollo de módulos en Django…» → «Desarrollé módulos en Django…»; None si no empieza así."""
    coincidencia = _NOMINAL.match(vineta.strip())
    if not coincidencia:
        return None
    verbo = VERBOS.get(normalizar(coincidencia.group(1)))
    if verbo is None:
        return None
    resto = coincidencia.group(3)
    return f"{verbo} {'el ' if coincidencia.group(2).lower() == 'del' else ''}{resto}"


class BackendSimulado:
    nombre = "simulado"

    def __init__(self, diccionario: Diccionario | None = None):
        self.diccionario = diccionario or cargar_diccionario()
        self.llamadas = 0

    def informacion(self) -> InformacionModelo:
        return InformacionModelo(backend="simulado", modelo="reglas_de_la_linea_base")

    # ------------------------------------------------------------------ etapas
    def _requisitos_evaluados(self, sistema: str, cv: str) -> list[dict[str, Any]]:
        vinetas = _VINETA.findall(cv)
        cv_norma = normalizar(cv)
        resultado = []
        for identificador, _, categoria, texto in _REQUISITO.findall(sistema):
            terminos = [h.canonico for h, _ in self.diccionario.detectar(normalizar(texto))]
            estado, evidencia = "ausente", None
            if categoria in ("experiencia", "formacion", "idioma"):
                estado = "mencionado"
            for termino in terminos:
                en_vineta = next((v for v in vinetas if self.diccionario.en_texto(termino, normalizar(v))), None)
                if en_vineta:
                    estado, evidencia = "cubierto", en_vineta
                    break
                if self.diccionario.en_texto(termino, cv_norma):
                    estado = "mencionado"
                    evidencia = next((r.strip(" -•") for r in cv.split("\n")
                                      if self.diccionario.en_texto(termino, normalizar(r))), None)
            resultado.append({"id": identificador, "estado": estado, "evidencia": evidencia})
        return resultado

    def _mejoras(self, cv: str) -> dict[str, Any]:
        sugerencias = []
        vinetas = _VINETA.findall(cv)
        for vineta in vinetas:
            propuesta = reescribir(vineta)
            if propuesta and len(sugerencias) < 3:
                sugerencias.append({"tipo": "redaccion", "fragmento_original": vineta.strip(), "propuesta": propuesta,
                                    "justificacion": "Empieza con un verbo de acción en primera persona."})
        nueva = "Docker" if not self.diccionario.en_texto("Docker", normalizar(cv)) else "Kubernetes"
        if vinetas:
            sugerencias.append({"tipo": "redaccion", "fragmento_original": vinetas[0].strip(),
                                "propuesta": f"{vinetas[0].strip().rstrip('.')} con {nueva}.",
                                "justificacion": "Simulación de una sugerencia que inventa una herramienta."})
        sugerencias.append({"tipo": "estructura", "fragmento_original": None,
                            "propuesta": "Agrega una sección de idiomas con tu nivel.",
                            "justificacion": "Las ofertas de TI suelen pedir el nivel de inglés."})
        return {"sugerencias": sugerencias}

    def chat(self, mensajes: list[dict[str, str]], opciones: dict[str, Any]) -> RespuestaBackend:
        self.llamadas += 1
        etapa = opciones["response_format"]["json_schema"]["name"]
        sistema = mensajes[0]["content"]
        coincidencia = _CONTENIDO.search(mensajes[-1]["content"])
        contenido = coincidencia.group(1) if coincidencia else ""
        if etapa == "extraccion_cv":
            datos = linea_base.extraer(contenido, detectar_secciones(contenido), self.diccionario)
        elif etapa == "requisitos_oferta":
            datos = linea_base.requisitos(contenido, self.diccionario)
        elif etapa in ("evaluacion_general", "evaluacion_especifica"):
            datos = _niveles(_VINETA.findall(contenido))
            if etapa == "evaluacion_especifica":
                datos["requisitos"] = self._requisitos_evaluados(sistema, contenido)
        elif etapa == "mejoras":
            datos = self._mejoras(contenido)
        else:
            raise ValueError(f"Etapa desconocida para el modelo simulado: {etapa}")
        texto = json.dumps(datos, ensure_ascii=False)
        estimacion = _estimacion(etapa, mensajes, contenido)
        groq = estimacion["groq_gpt_oss_120b"]
        return RespuestaBackend(cuerpo_crudo=json.dumps({"simulado": True}), contenido=texto, motivo_fin="stop",
                                tokens_entrada=groq["entrada"], tokens_salida=groq["salida"], tokens_total=groq["total"],
                                latencia_s=0.0, modelo_respuesta="simulado", uso={"estimacion": estimacion})


def crear_modelo_simulado(cfg: Configuracion, backend: Any) -> ModeloLLM:
    """ModeloLLM con el backend simulado (o la caché que lo envuelve), con las opciones del perfil
    predeterminado (esquema JSON estricto) y una identidad propia para que no se confunda con Groq."""
    modelo = ModeloLLM(cfg, cfg.datos["perfil_predeterminado"], backend=backend,
                       congelado={"ajustes": [], "modo_salida": "simulado"})
    modelo.perfil = {**modelo.perfil, "nombre": "simulado", "proveedor": "Simulado (sin red)",
                     "modelo": "reglas_de_la_linea_base", "aviso_privacidad": None}
    return modelo
