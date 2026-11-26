"""Informe de la evaluación técnica: métricas, tablas CSV, figuras PNG y resumen (definición del proyecto, §9).

Lee las unidades de todas las corridas de resultados/evaluacion/ y escribe en resultados/evaluacion/informe/:
- extraccion_por_documento.csv y extraccion_resumen.csv (P1, H1): precisión, recall y F1 de habilidades,
  experiencias (con y sin fechas a ±1 mes) y formación, y error de temporalidad, por método y formato;
- h1_comparacion.csv: F1 de habilidades del modelo frente a la línea base, emparejado por documento;
- temporalidad.csv: meses de cada habilidad emparejada (referencia frente a extraída);
- compatibilidad.csv y h2_validez_orden.csv (P2, H2): compatibilidad y puntuación de cada análisis y
  ρ de Spearman con el nivel previsto, por versión de la oferta y por vacante;
- estabilidad.csv (P2), formatos.csv (P1), sensibilidad.csv (variantes de control);
- mejoras.csv y h3_mejora.csv (P3, H3): antes y después, con Wilcoxon para la puntuación y la compatibilidad;
- guardia.csv (P3, H4) y revision_manual_<corrida>.csv: muestra de sugerencias aprobadas para revisar a
  mano (no se sobrescribe; si ya tiene respuestas, se cuentan en guardia.csv);
- requisitos_oferta.csv: requisitos de cada oferta identificados frente a la referencia;
- llamadas.csv: calidad de la salida (respuestas válidas al primer intento), tokens y latencia;
- estimacion_cuota.csv (solo con la corrida simulada): tokens y llamadas estimados para Groq y Gemini;
- urls.csv y urls_resumen.csv (P4): extracción de ofertas por URL, si se midió (evaluacion/urls.py);
- figuras/*.png y resumen.md.
La extracción de una corrida con modelo se mide de dos formas: «modelo» (su salida verificada, sin el
complemento del diccionario, recalculada aquí sin llamar de nuevo al modelo) y «modelo + diccionario»
(lo que muestra la app, que agrega las habilidades del diccionario escritas en el texto).
"""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev
from typing import Any

from analizador_cv.analisis.cotejo import normalizar
from analizador_cv.analisis.extraccion import procesar_extraccion
from analizador_cv.analisis.fechas import Mes
from analizador_cv.analisis.habilidades import Diccionario, cargar_diccionario
from analizador_cv.analisis.secciones import detectar_secciones
from analizador_cv.analisis.temporalidad import construir_tags
from analizador_cv.evaluacion import metricas as m
from analizador_cv.evaluacion.corpus import NIVELES, Corpus
from analizador_cv.evaluacion.ejecucion import DISENOS, planificar
from analizador_cv.evaluacion.urls import resumen_urls
from analizador_cv.utilidades import leer_json

ETIQUETAS = {"linea_base": "Línea base", "simulado": "Modelo simulado", "gemini_flash_lite": "Gemini 3.5 Flash-Lite"}
VARIANTES = {"linea_base": "línea base", "modelo": "modelo", "modelo_y_diccionario": "modelo + diccionario"}
TOKENS_DIA_GROQ = 200_000
MUESTRA_REVISION = 30
COLUMNAS_REVISION = ("n", "documento", "sugerencia", "fragmento_original", "propuesta", "justificacion",
                     "agrega_informacion", "exagera", "observaciones")


# =============================================================================
# Carga
# =============================================================================
@dataclass
class DatosCorrida:
    nombre: str
    carpeta: Path
    info: dict[str, Any]
    unidades: dict[str, dict[str, Any]]

    @property
    def metodo(self) -> str:
        return self.info["metodo"]

    @property
    def con_modelo(self) -> bool:
        return self.metodo != "linea_base"

    @property
    def etiqueta(self) -> str:
        base = ETIQUETAS.get(self.metodo, self.metodo)
        return base if self.nombre == self.metodo else f"{base} ({self.nombre})"

    def registros(self, tipo: str, grupo: str | None = None, *, con_error: bool = False) -> list[dict[str, Any]]:
        return [r for r in self.unidades.values() if r["unidad"]["tipo"] == tipo
                and (grupo is None or r["unidad"]["grupo"] == grupo) and (con_error or not r.get("error"))]

    def variantes(self) -> list[str]:
        return ["modelo", "modelo_y_diccionario"] if self.con_modelo else ["linea_base"]


def cargar_corridas(carpeta: Path) -> list[DatosCorrida]:
    corridas = []
    for subcarpeta in sorted(p for p in Path(carpeta).iterdir() if p.is_dir()):
        if not (subcarpeta / "corrida.json").exists():
            continue
        unidades = {}
        for ruta in sorted((subcarpeta / "unidades").glob("*.json")):
            registro = leer_json(ruta)
            unidades[registro["unidad"]["id"]] = registro
        corridas.append(DatosCorrida(subcarpeta.name, subcarpeta, leer_json(subcarpeta / "corrida.json"), unidades))
    orden = {"linea_base": 0, "simulado": 9}
    return sorted(corridas, key=lambda c: (orden.get(c.metodo, 1), c.nombre))


def _escribir_csv(ruta: Path, filas: list[dict[str, Any]]) -> Path | None:
    if not filas:
        return None
    columnas = list(dict.fromkeys(k for fila in filas for k in fila))
    with ruta.open("w", encoding="utf-8-sig", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=columnas)
        escritor.writeheader()
        for fila in filas:
            escritor.writerow({k: (";".join(map(str, v)) if isinstance(v, list) else v) for k, v in fila.items()})
    return ruta


# =============================================================================
# Cálculo
# =============================================================================
class Evaluacion:
    """Todas las métricas de las corridas cargadas, como listas de filas (una tabla por métrica)."""

    def __init__(self, corpus: Corpus, corridas: list[DatosCorrida], diccionario: Diccionario | None = None,
                 urls: list[dict[str, Any]] | None = None):
        self.corpus = corpus
        self.corridas = corridas
        self.urls = urls or []
        self.diccionario = diccionario or cargar_diccionario()
        self.referencia: Mes = corpus.fecha_referencia
        self.tablas: dict[str, list[dict[str, Any]]] = {}
        self._pares_temporales: list[dict[str, Any]] = []

    # ---------------------------------------------------------- extracción
    def _prediccion(self, registro: dict[str, Any], variante: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """(tags, extracción) de un análisis según la variante."""
        resultado = registro["resultado"]
        if variante == "modelo_y_diccionario":
            return resultado["tags"], resultado["extraccion"]
        texto = registro["texto"]
        extraccion = procesar_extraccion(resultado["salidas"]["extraccion_cv"], texto, referencia=self.referencia,
                                         diccionario=self.diccionario, secciones=detectar_secciones(texto),
                                         complementar=False)
        tags = construir_tags(extraccion, texto, referencia=self.referencia, diccionario=self.diccionario)
        return [t.a_dict() for t in tags], extraccion.a_dict()

    def extraccion(self) -> None:
        filas, temporales = [], []
        for corrida in self.corridas:
            for registro in corrida.registros("analisis"):
                unidad = registro["unidad"]
                if unidad["grupo"] not in ("principal", "formato"):
                    continue
                verdad = self.corpus.documento(unidad["documento"])
                for variante in corrida.variantes():
                    tags, extraccion = self._prediccion(registro, variante)
                    hab = m.evaluar_habilidades(tags, verdad["habilidades"], self.diccionario)
                    exp = m.evaluar_experiencias(extraccion["experiencias"], verdad["experiencias"])
                    form = m.evaluar_formacion(extraccion["formacion"], verdad["formacion"])
                    pares = [{"corrida": corrida.nombre, "variante": variante, "documento": unidad["documento"],
                              "formato": unidad["formato"], "habilidad": r["nombre"], "meses_referencia": r["meses"],
                              "meses_extraidos": p["meses"], "error_abs": abs(p["meses"] - r["meses"])}
                             for p, r in hab.pares]
                    temporales += pares
                    errores = [p["error_abs"] for p in pares]
                    fechadas = [p["error_abs"] for p in pares if p["meses_referencia"] > 0]
                    por_clase = hab.recall_por_clase(verdad["habilidades"])
                    filas.append({
                        "corrida": corrida.nombre, "metodo": corrida.metodo, "variante": variante,
                        "documento": unidad["documento"], "formato": unidad["formato"], "vacante": verdad["vacante"],
                        "nivel": verdad["nivel"], "plantilla": verdad["plantilla"], "estilo_fechas": verdad["estilo_fechas"],
                        **{f"habilidades_{k}": v for k, v in hab.conteo.a_dict().items()},
                        "habilidades_recall_tecnologia": m._r(por_clase.get("tecnologia", m.Conteo()).recall),
                        "habilidades_recall_practica": m._r(por_clase.get("practica", m.Conteo()).recall),
                        "habilidades_repetidas": len(hab.repetidas), "habilidades_opcionales": len(hab.opcionales),
                        **{f"experiencias_{k}": v for k, v in exp.conteo.a_dict().items()},
                        **{f"experiencias_fechas_{k}": v for k, v in exp.conteo_con_fechas.a_dict().items()},
                        **{f"formacion_{k}": v for k, v in form.conteo.a_dict().items()},
                        "temporalidad_pares": len(errores), "temporalidad_eam": m._r(mean(errores)) if errores else None,
                        "temporalidad_pares_fechados": len(fechadas),
                        "temporalidad_eam_fechados": m._r(mean(fechadas)) if fechadas else None,
                        "falsos_positivos": hab.falsos_positivos, "falsos_negativos": hab.falsos_negativos,
                    })
        self.tablas["extraccion_por_documento"] = filas
        self.tablas["temporalidad"] = temporales
        resumen = []
        grupos: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
        for fila in filas:
            grupos[(fila["corrida"], fila["variante"], fila["formato"])].append(fila)
        for (corrida, variante, formato), lista in grupos.items():
            fila = {"corrida": corrida, "variante": variante, "formato": formato, "documentos": len(lista)}
            for campo in ("habilidades", "experiencias", "experiencias_fechas", "formacion"):
                conteo = m.Conteo(sum(f[f"{campo}_vp"] for f in lista), sum(f[f"{campo}_fp"] for f in lista),
                                  sum(f[f"{campo}_fn"] for f in lista))
                fila.update({f"{campo}_precision": m._r(conteo.precision), f"{campo}_recall": m._r(conteo.recall),
                             f"{campo}_f1": m._r(conteo.f1)})
            f1s = [f["habilidades_f1"] for f in lista if f["habilidades_f1"] is not None]
            bajo, alto = m.intervalo_bootstrap(f1s)
            fila.update({"habilidades_f1_medio_por_documento": m._r(mean(f1s)) if f1s else None,
                         "habilidades_f1_ic95_bajo": bajo, "habilidades_f1_ic95_alto": alto})
            for clase in ("tecnologia", "practica"):
                valores = [f[f"habilidades_recall_{clase}"] for f in lista if f[f"habilidades_recall_{clase}"] is not None]
                fila[f"habilidades_recall_{clase}_medio"] = m._r(mean(valores)) if valores else None
            pares = [p for p in temporales if (p["corrida"], p["variante"], p["formato"]) == (corrida, variante, formato)]
            fechados = [p["error_abs"] for p in pares if p["meses_referencia"] > 0]
            fila.update({"temporalidad_eam": m._r(mean(p["error_abs"] for p in pares)) if pares else None,
                         "temporalidad_eam_fechados": m._r(mean(fechados)) if fechados else None,
                         "temporalidad_exactos_fechados": m._r(sum(e == 0 for e in fechados) / len(fechados)) if fechados else None,
                         "temporalidad_pares_fechados": len(fechados)})
            resumen.append(fila)
        self.tablas["extraccion_resumen"] = resumen

    def h1(self) -> None:
        filas = []
        base = next((c for c in self.corridas if c.metodo == "linea_base"), None)
        if base is None:
            self.tablas["h1_comparacion"] = []
            return
        por_doc = {(f["corrida"], f["variante"], f["documento"]): f for f in self.tablas["extraccion_por_documento"]
                   if f["formato"] == "txt"}
        for corrida in (c for c in self.corridas if c.con_modelo):
            for variante in corrida.variantes():
                for campo in ("habilidades_f1", "experiencias_fechas_f1", "formacion_f1"):
                    docs = sorted(d for (c, v, d) in por_doc if c == corrida.nombre and v == variante
                                  and (base.nombre, "linea_base", d) in por_doc)
                    pares = [(por_doc[(base.nombre, "linea_base", d)][campo], por_doc[(corrida.nombre, variante, d)][campo])
                             for d in docs]
                    pares = [(a, b) for a, b in pares if a is not None and b is not None]
                    if not pares:
                        continue
                    comparacion = m.comparacion_emparejada([a for a, _ in pares], [b for _, b in pares])
                    filas.append({"corrida": corrida.nombre, "variante": variante, "medida": campo,
                                  "linea_base": base.nombre, **{k: v for k, v in comparacion.items() if k != "wilcoxon"},
                                  "wilcoxon_n_sin_cero": comparacion["wilcoxon"]["n_sin_cero"],
                                  "wilcoxon_w_mas": comparacion["wilcoxon"]["w_mas"],
                                  "wilcoxon_p_unilateral": comparacion["wilcoxon"]["p_unilateral"]})
        self.tablas["h1_comparacion"] = filas

    # ---------------------------------------------------- compatibilidad
    def compatibilidad(self) -> None:
        filas = []
        for corrida in self.corridas:
            for registro in corrida.registros("analisis"):
                unidad, resultado = registro["unidad"], registro["resultado"]
                verdad = self.corpus.documento(unidad["documento"])
                filas.append({
                    "corrida": corrida.nombre, "grupo": unidad["grupo"], "documento": unidad["documento"],
                    "formato": unidad["formato"], "oferta": unidad["oferta"], "version_oferta": unidad["oferta"][-1],
                    "repeticion": unidad["repeticion"], "vacante": verdad["vacante"], "nivel": verdad["nivel"],
                    "nivel_numerico": NIVELES[verdad["nivel"]], "control_de": verdad["control_de"],
                    "puntuacion": resultado["puntuacion"]["total"],
                    "compatibilidad": resultado["compatibilidad"]["porcentaje"],
                    **{f"criterio_{c['id']}": c["puntos"] for c in resultado["puntuacion"]["criterios"]},
                    "estados": [f"{r['id']}={r['estado']}" for r in resultado["compatibilidad"]["requisitos"]],
                })
        self.tablas["compatibilidad"] = filas
        h2 = []
        for corrida in self.corridas:
            for version, grupo in (("A", "principal"), ("B", "oferta_b")):
                elegidas = [f for f in filas if f["corrida"] == corrida.nombre and f["grupo"] == grupo]
                for ambito in ("todas", "VAC-DES", "VAC-QA", "VAC-SOP"):
                    lista = [f for f in elegidas if ambito == "todas" or f["vacante"] == ambito]
                    if len(lista) < 3:
                        continue
                    niveles = [f["nivel_numerico"] for f in lista]
                    fila = {"corrida": corrida.nombre, "version_oferta": version, "vacante": ambito, "n": len(lista)}
                    for medida in ("compatibilidad", "puntuacion"):
                        resultado = m.spearman(niveles, [f[medida] for f in lista])
                        fila[f"rho_{medida}"] = resultado["rho"]
                        fila[f"p_unilateral_{medida}"] = resultado["p_unilateral"]
                    for nivel in ("alto", "medio", "bajo"):
                        valores = [f["compatibilidad"] for f in lista if f["nivel"] == nivel]
                        fila[f"compatibilidad_media_{nivel}"] = m._r(mean(valores), 2) if valores else None
                    h2.append(fila)
        self.tablas["h2_validez_orden"] = h2

    def estabilidad(self) -> None:
        filas = []
        for corrida in (c for c in self.corridas if c.con_modelo):
            repeticiones: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for registro in corrida.registros("analisis", "estabilidad"):
                repeticiones[registro["unidad"]["documento"]].append(registro)
            for documento, extra in sorted(repeticiones.items()):
                principal = [r for r in corrida.registros("analisis", "principal") if r["unidad"]["documento"] == documento]
                todas = sorted(principal + extra, key=lambda r: r["unidad"]["repeticion"])
                if len(todas) < 2:
                    continue
                puntuaciones = [r["resultado"]["puntuacion"]["total"] for r in todas]
                compat = [r["resultado"]["compatibilidad"]["porcentaje"] for r in todas]
                estados = [{q["id"]: q["estado"] for q in r["resultado"]["compatibilidad"]["requisitos"]} for r in todas]
                ids = set().union(*estados)
                iguales = sum(len({e.get(i) for e in estados}) == 1 for i in ids)
                filas.append({"corrida": corrida.nombre, "documento": documento,
                              "nivel": self.corpus.documento(documento)["nivel"], "repeticiones": len(todas),
                              "puntuaciones": puntuaciones, "de_puntuacion": m._r(stdev(puntuaciones), 3),
                              "rango_puntuacion": m._r(max(puntuaciones) - min(puntuaciones), 2),
                              "compatibilidades": compat, "de_compatibilidad": m._r(stdev(compat), 3),
                              "rango_compatibilidad": m._r(max(compat) - min(compat), 2),
                              "requisitos": len(ids), "requisitos_concordantes": iguales,
                              "concordancia_requisitos": m._r(iguales / len(ids)) if ids else None})
        self.tablas["estabilidad"] = filas

    def formatos(self) -> None:
        filas = []
        por_doc = {(f["corrida"], f["variante"], f["documento"], f["formato"]): f
                   for f in self.tablas["extraccion_por_documento"]}
        for corrida in self.corridas:
            analisis = {(r["unidad"]["documento"], r["unidad"]["formato"]): r for r in corrida.registros("analisis")
                        if r["unidad"]["grupo"] in ("principal", "formato")}
            variante = corrida.variantes()[0]
            for documento in sorted({d for d, f in analisis if f != "txt"}):
                if not all((documento, f) in analisis for f in ("txt", "docx", "pdf")):
                    continue
                fila = {"corrida": corrida.nombre, "documento": documento,
                        "plantilla": self.corpus.documento(documento)["plantilla"]}
                for formato in ("txt", "docx", "pdf"):
                    resultado = analisis[(documento, formato)]["resultado"]
                    fila[f"puntuacion_{formato}"] = resultado["puntuacion"]["total"]
                    fila[f"compatibilidad_{formato}"] = resultado["compatibilidad"]["porcentaje"]
                    fila[f"f1_habilidades_{formato}"] = por_doc[(corrida.nombre, variante, documento, formato)]["habilidades_f1"]
                for medida in ("puntuacion", "compatibilidad", "f1_habilidades"):
                    valores = [fila[f"{medida}_{f}"] for f in ("txt", "docx", "pdf")]
                    fila[f"diferencia_max_{medida}"] = m._r(max(valores) - min(valores), 4)
                filas.append(fila)
        self.tablas["formatos"] = filas

    def sensibilidad(self) -> None:
        filas = []
        for corrida in self.corridas:
            principales = {r["unidad"]["documento"]: r for r in corrida.registros("analisis", "principal")}
            for registro in corrida.registros("analisis", "control"):
                verdad = self.corpus.documento(registro["unidad"]["documento"])
                original = principales.get(verdad["control_de"])
                if original is None:
                    continue
                oferta = self.corpus.oferta(registro["unidad"]["oferta"])
                ancla = normalizar(next(r["ancla"] for r in oferta["requisitos"] if r["id"] == verdad["requisito_retirado"]))
                cubren = [r["id"] for r in original["resultado"]["compatibilidad"]["requisitos"]
                          if ancla in normalizar(r["texto"]) or normalizar(r["texto"]) in ancla]
                antes = {r["id"]: r["estado"] for r in original["resultado"]["compatibilidad"]["requisitos"]}
                despues = {r["id"]: r["estado"] for r in registro["resultado"]["compatibilidad"]["requisitos"]}
                ca, cd = original["resultado"]["compatibilidad"]["porcentaje"], registro["resultado"]["compatibilidad"]["porcentaje"]
                filas.append({"corrida": corrida.nombre, "documento": verdad["control_de"],
                              "requisito_retirado": verdad["requisito_retirado"],
                              "competencia_retirada": verdad["competencia_retirada"],
                              "compatibilidad_original": ca, "compatibilidad_sin_competencia": cd,
                              "diferencia": m._r(cd - ca, 2), "requisitos_que_la_piden": cubren,
                              "estado_antes": [antes.get(i) for i in cubren], "estado_despues": [despues.get(i) for i in cubren]})
        self.tablas["sensibilidad"] = filas

    # ------------------------------------------------------- mejoras (H3, H4)
    def mejoras(self) -> None:
        filas, h3 = [], []
        for corrida in (c for c in self.corridas if c.con_modelo):
            principales = {r["unidad"]["documento"]: r for r in corrida.registros("analisis", "principal")}
            for registro in corrida.registros("reanalisis"):
                documento = registro["unidad"]["documento"]
                antes = principales[documento]["resultado"]
                despues = registro["resultado"] or antes  # sin sugerencias aplicables: la versión mejorada es la misma
                filas.append({"corrida": corrida.nombre, "documento": documento,
                              "vacante": self.corpus.documento(documento)["vacante"],
                              "nivel": self.corpus.documento(documento)["nivel"], "aplicadas": len(registro["aplicadas"]),
                              "puntuacion_antes": antes["puntuacion"]["total"], "puntuacion_despues": despues["puntuacion"]["total"],
                              "delta_puntuacion": m._r(despues["puntuacion"]["total"] - antes["puntuacion"]["total"], 2),
                              "compatibilidad_antes": antes["compatibilidad"]["porcentaje"],
                              "compatibilidad_despues": despues["compatibilidad"]["porcentaje"],
                              "delta_compatibilidad": m._r(despues["compatibilidad"]["porcentaje"]
                                                           - antes["compatibilidad"]["porcentaje"], 2)})
            propias = [f for f in filas if f["corrida"] == corrida.nombre]
            for medida in ("puntuacion", "compatibilidad"):
                if not propias:
                    continue
                comparacion = m.comparacion_emparejada([f[f"{medida}_antes"] for f in propias],
                                                       [f[f"{medida}_despues"] for f in propias])
                h3.append({"corrida": corrida.nombre, "medida": medida,
                           "con_mejoras_aplicadas": sum(f["aplicadas"] > 0 for f in propias),
                           **{k: v for k, v in comparacion.items() if k != "wilcoxon"},
                           "wilcoxon_n_sin_cero": comparacion["wilcoxon"]["n_sin_cero"],
                           "wilcoxon_w_mas": comparacion["wilcoxon"]["w_mas"],
                           "wilcoxon_p_unilateral": comparacion["wilcoxon"]["p_unilateral"]})
        self.tablas["mejoras"] = filas
        self.tablas["h3_mejora"] = h3

    def guardia(self, carpeta_informe: Path | None = None) -> None:
        filas = []
        for corrida in (c for c in self.corridas if c.con_modelo):
            sugerencias = [(r["unidad"]["documento"], s) for r in corrida.registros("analisis", "principal")
                           for s in r["resultado"]["sugerencias"] if s["origen"] == "modelo"]
            del_modelo = len(sugerencias)
            con_entidades = sum(bool(s["entidades_nuevas"]) for _, s in sugerencias)
            cambios = sum(bool(s["palabras_nuevas"]) and not s["entidades_nuevas"] for _, s in sugerencias)
            aprobadas = [(d, s) for d, s in sugerencias if s["estado"] == "aprobada"]
            fila = {"corrida": corrida.nombre, "documentos": len(corrida.registros("analisis", "principal")),
                    "sugerencias_del_modelo": del_modelo, "con_informacion_nueva": con_entidades,
                    "con_cambios_de_contenido": cambios,
                    "descartadas_por_otros_motivos": sum(s["estado"] == "descartada" for _, s in sugerencias) - con_entidades - cambios,
                    "aprobadas": len(aprobadas), "aprobadas_de_redaccion": sum(s["tipo"] == "redaccion" for _, s in aprobadas),
                    "tasa_invencion_antes": m._r(con_entidades / del_modelo) if del_modelo else None,
                    "tasa_cambios_de_contenido": m._r(cambios / del_modelo) if del_modelo else None,
                    "tasa_invencion_despues": m._r(sum(bool(s["entidades_nuevas"]) for _, s in aprobadas) / len(aprobadas))
                    if aprobadas else None}
            if carpeta_informe is not None:
                fila.update(self._revision_manual(corrida, aprobadas, carpeta_informe))
            filas.append(fila)
        self.tablas["guardia"] = filas

    def _revision_manual(self, corrida: DatosCorrida, aprobadas: list[tuple[str, dict[str, Any]]],
                         carpeta: Path) -> dict[str, Any]:
        """Muestra estratificada por vacante de hasta 30 sugerencias aprobadas para revisar a mano (H4).
        El archivo no se sobrescribe: si ya tiene respuestas, se cuentan."""
        ruta = carpeta / f"revision_manual_{corrida.nombre}.csv"
        if not ruta.exists():
            generador = random.Random(m.SEMILLA)
            por_vacante: dict[str, list] = defaultdict(list)
            for documento, sugerencia in aprobadas:
                por_vacante[self.corpus.documento(documento)["vacante"]].append((documento, sugerencia))
            for lista in por_vacante.values():
                generador.shuffle(lista)
            muestra = []
            while len(muestra) < MUESTRA_REVISION and any(por_vacante.values()):
                for vacante in sorted(por_vacante):
                    if por_vacante[vacante] and len(muestra) < MUESTRA_REVISION:
                        muestra.append(por_vacante[vacante].pop())
            _escribir_csv(ruta, [{"n": i, "documento": d, "sugerencia": s["id"], "fragmento_original": s["fragmento_original"],
                                  "propuesta": s["propuesta"], "justificacion": s["justificacion"],
                                  "agrega_informacion": "", "exagera": "", "observaciones": ""}
                                 for i, (d, s) in enumerate(muestra, start=1)] or
                          [{c: "" for c in COLUMNAS_REVISION}])
        with ruta.open(encoding="utf-8-sig", newline="") as archivo:
            filas = [f for f in csv.DictReader(archivo) if f.get("n")]
        respondidas = [f for f in filas if normalizar(f.get("agrega_informacion")) in ("si", "no")]
        agrega = sum(normalizar(f["agrega_informacion"]) == "si" for f in respondidas)
        exagera = sum(normalizar(f.get("exagera")) == "si" for f in respondidas)
        return {"revision_manual_muestra": len(filas), "revision_manual_respondidas": len(respondidas),
                "revision_manual_agregan_informacion": agrega if respondidas else None,
                "revision_manual_exageran": exagera if respondidas else None,
                "tasa_invencion_residual_manual": m._r(agrega / len(respondidas)) if respondidas else None}

    # ------------------------------------------------- ofertas y llamadas
    def requisitos_oferta(self) -> None:
        filas = []
        for corrida in self.corridas:
            for registro in corrida.registros("requisitos"):
                oferta = self.corpus.oferta(registro["unidad"]["oferta"])
                resultado = m.evaluar_requisitos_oferta(registro["requisitos"]["requisitos"], oferta["requisitos"],
                                                        self.corpus.texto_oferta(oferta["id"]))
                filas.append({"corrida": corrida.nombre, "oferta": oferta["id"], "version": oferta["version"], **resultado})
        self.tablas["requisitos_oferta"] = filas

    def llamadas(self) -> None:
        filas = []
        for corrida in (c for c in self.corridas if c.con_modelo):
            llamadas, por_grupo = [], defaultdict(list)
            for registro in corrida.unidades.values():
                grupo = registro["unidad"]["grupo"]
                lista = registro.get("llamadas") or (registro.get("resultado") or {}).get("llamadas") or []
                if grupo == "oferta_b":  # la extracción de la hoja de vida es la misma del análisis principal (caché)
                    lista = [l for l in lista if l["etapa"] != "extraccion_cv"]
                llamadas += lista
                if lista and not registro.get("error"):
                    por_grupo[grupo].append(registro | {"_llamadas": lista})
            fallidas = [r for r in corrida.unidades.values() if r.get("error")]
            fila = {"corrida": corrida.nombre, "unidades": len(corrida.unidades), "unidades_fallidas": len(fallidas),
                    "llamadas": len(llamadas), "intentos": sum(l["intentos"] for l in llamadas),
                    "validas_al_primer_intento": sum(l["intentos"] == 1 and l["estado"] == "valida" for l in llamadas),
                    "tokens_totales": sum(l["tokens_total"] for l in llamadas)}
            fila["porcentaje_validas_al_primer_intento"] = (m._r(fila["validas_al_primer_intento"] / len(llamadas))
                                                            if llamadas else None)
            for grupo in ("principal", "mejoras", "estabilidad", "oferta_b", "control", "formato"):
                registros = por_grupo.get(grupo, [])
                tokens = m.resumen_numerico(sum(l["tokens_total"] for l in r["_llamadas"]) for r in registros)
                latencia = m.resumen_numerico(sum(l["latencia_s"] for l in r["_llamadas"]) for r in registros)
                reloj = m.resumen_numerico(r["duracion_s"] for r in registros if r.get("llamadas_nuevas"))
                fila.update({f"{grupo}_n": tokens["n"], f"{grupo}_tokens_mediana": tokens["mediana"],
                             f"{grupo}_tokens_p95": tokens["p95"], f"{grupo}_latencia_mediana_s": latencia["mediana"],
                             f"{grupo}_latencia_p95_s": latencia["p95"], f"{grupo}_reloj_mediana_s": reloj["mediana"],
                             f"{grupo}_reloj_p95_s": reloj["p95"]})
            filas.append(fila)
        self.tablas["llamadas"] = filas

    def estimacion_cuota(self) -> None:
        filas = []
        for corrida in (c for c in self.corridas if c.metodo == "simulado"):
            for diseno in DISENOS:
                ids = {u.id for u in planificar(self.corpus, "groq_gpt_oss_120b", diseno)}
                registros = [r for i, r in corrida.unidades.items() if i in ids]
                if len(registros) < len(ids):
                    continue  # la corrida simulada no cubre este diseño
                groq = sum((r.get("estimacion_tokens_nuevos") or {}).get("groq_gpt_oss_120b", 0) for r in registros)
                gemini = sum((r.get("estimacion_tokens_nuevos") or {}).get("gemini_flash_lite", 0) for r in registros)
                llamadas = sum(r.get("llamadas_nuevas") or 0 for r in registros)
                filas.append({"alcance": f"diseño {diseno}", "unidades": len(ids), "llamadas": llamadas, "tokens_groq": groq,
                              "dias_cuota_gratuita_groq": m._r(groq / TOKENS_DIA_GROQ, 1), "tokens_gemini": gemini,
                              "nota": "Estimación con la calibración de la Etapa 6; reintentos no incluidos."})
            por_grupo: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for registro in corrida.unidades.values():
                por_grupo[registro["unidad"]["grupo"]].append(registro)
            for grupo in ("requisitos", "principal", "mejoras", "estabilidad", "oferta_b", "control", "formato"):
                registros = por_grupo.get(grupo, [])
                if not registros:
                    continue
                groq = sum((r.get("estimacion_tokens_nuevos") or {}).get("groq_gpt_oss_120b", 0) for r in registros)
                filas.append({"alcance": f"grupo {grupo}", "unidades": len(registros),
                              "llamadas": sum(r.get("llamadas_nuevas") or 0 for r in registros), "tokens_groq": groq,
                              "dias_cuota_gratuita_groq": m._r(groq / TOKENS_DIA_GROQ, 1),
                              "tokens_gemini": sum((r.get("estimacion_tokens_nuevos") or {}).get("gemini_flash_lite", 0)
                                                   for r in registros),
                              "nota": "Formato: las 72 versiones DOCX y PDF (el diseño núcleo usa 24)."
                              if grupo == "formato" else ""})
        self.tablas["estimacion_cuota"] = filas

    def calcular(self, carpeta_informe: Path | None = None) -> dict[str, list[dict[str, Any]]]:
        self.extraccion()
        self.h1()
        self.compatibilidad()
        self.estabilidad()
        self.formatos()
        self.sensibilidad()
        self.mejoras()
        self.guardia(carpeta_informe)
        self.requisitos_oferta()
        self.llamadas()
        self.estimacion_cuota()
        self.tablas["urls"] = list(self.urls)
        self.tablas["urls_resumen"] = resumen_urls(self.urls) if self.urls else []
        return self.tablas


# =============================================================================
# Escritura
# =============================================================================
def escribir_informe(corpus: Corpus, carpeta_evaluacion: Path, *, figuras: bool = True,
                     salida=print) -> list[Path]:
    """Calcula todo y escribe las tablas, las figuras y el resumen en <carpeta_evaluacion>/informe/."""
    corridas = cargar_corridas(carpeta_evaluacion)
    destino = Path(carpeta_evaluacion) / "informe"
    destino.mkdir(parents=True, exist_ok=True)
    ruta_urls = Path(carpeta_evaluacion) / "urls" / "urls.json"
    evaluacion = Evaluacion(corpus, corridas, urls=leer_json(ruta_urls)["filas"] if ruta_urls.exists() else None)
    tablas = evaluacion.calcular(destino)
    archivos = [r for nombre, filas in tablas.items() if (r := _escribir_csv(destino / f"{nombre}.csv", filas))]
    archivos += [p for p in destino.glob("revision_manual_*.csv")]
    if figuras:
        try:
            from analizador_cv.evaluacion import figuras as modulo_figuras
        except ImportError:
            salida("Sin figuras: falta matplotlib (pip install -r requirements.txt).")
        else:
            archivos += modulo_figuras.dibujar(evaluacion, destino / "figuras")
    from analizador_cv.evaluacion.resumen import escribir_resumen
    archivos.append(escribir_resumen(evaluacion, destino / "resumen.md"))
    return archivos
