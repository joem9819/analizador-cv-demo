"""Corrida de la evaluación técnica: plan de unidades y ejecución reanudable (python -m analizador_cv evaluar).

Métodos: «linea_base» (sin modelo), «simulado» (modelo simulado, sin red: verifica el camino completo) o
un perfil de modelo de configuracion.json (corrida real; gasta cuota de API).

Unidades, en orden de prioridad (si la cuota se agota, lo más importante ya quedó hecho):
1. requisitos: los requisitos de cada oferta, una vez por oferta (la app permite reutilizarlos);
2. principal: cada hoja de vida en TXT frente a la oferta A de su vacante, con mejoras (H1, H2, H4);
3. mejoras: aplica las sugerencias de redacción aprobadas, reanaliza y compara (H3). Solo con modelo;
4. estabilidad: repeticiones 2 y 3 del análisis principal de 9 hojas de vida (P2). Solo con modelo;
5. oferta_b: cada hoja de vida frente a la oferta B (narrativa) de su vacante (H2 con otra redacción);
6. control: las 6 variantes sin una competencia requerida (sensibilidad);
7. formato: las versiones DOCX y PDF (invarianza al formato; en el diseño «nucleo», 12 hojas de vida).
Diseños: «prueba» (3 hojas de vida: unidades 1 a 3), «nucleo» y «completo» (todas las unidades).

Cada unidad terminada queda en <carpeta>/unidades/<id>.json y no se repite. Las llamadas al modelo
quedan en <carpeta>/llamadas.sqlite (cache.py): al reanudar, una unidad interrumpida repite sus llamadas
desde la caché. Si el proveedor detiene la solicitud (cuota agotada, conexión, clave), la corrida se
detiene sin marcar la unidad y se reanuda con el mismo comando. Si el modelo no da una respuesta válida
tras los reintentos, la unidad queda «fallida» (cuenta en la calidad de la salida) y la corrida sigue.
La fecha de referencia es la del corpus (2026-10), para que los resultados sean reproducibles.

Partes (--particion K/N): con un proveedor lento, N procesos pueden correr a la vez sobre la misma carpeta.
Cada hoja de vida queda entera en una parte (sus unidades conservan el orden y comparten la extracción),
y los requisitos van en la parte 1, que los corre primero. Las partes no escriben corrida.json, sino
particion_K_de_N.json; al terminar, el mismo comando sin --particion completa lo que una parte haya omitido
(por ejemplo, un análisis cuyos requisitos todavía no estaban) y registra el estado de la corrida. Las
llamadas y los resultados son los mismos que en serie; solo cambia el tiempo total.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from analizador_cv.analisis.comparacion import aplicar_mejoras, comparar
from analizador_cv.analisis.motor import VERSION_MOTOR, Analizador, ErrorAnalisis, RequisitosOferta
from analizador_cv.configuracion import Configuracion
from analizador_cv.documentos import leer_documento
from analizador_cv.evaluacion.cache import BackendConCache
from analizador_cv.evaluacion.corpus import Corpus
from analizador_cv.evaluacion.simulado import BackendSimulado, crear_modelo_simulado
from analizador_cv.llm.backends import Backend
from analizador_cv.llm.modelo import ModeloLLM, crear_backend
from analizador_cv.llm.salida import Intento
from analizador_cv.utilidades import ahora_iso, escribir_json, leer_json

DISENOS = ("prueba", "nucleo", "completo")
METODOS_SIN_RED = ("linea_base", "simulado")
#: Errores del proveedor que detienen la corrida (se reanuda con el mismo comando).
ERRORES_QUE_DETIENEN = ("cupo_agotado", "conexion", "autenticacion", "servicio_no_disponible",
                        "modelo_no_encontrado", "solicitud_rechazada")
#: Posiciones (en su vacante) de las hojas de vida de cada submuestra.
POSICIONES_ESTABILIDAD = (0, 3, 7)  # el primer perfil alto, medio y bajo de la partición principal
POSICIONES_FORMATO_NUCLEO = (0, 1, 4, 7)  # una y dos columnas; niveles alto, alto, medio y bajo
POSICION_PRUEBA = 0
REPETICIONES = 3

Salida = Callable[[str], None]


class DetenerCorrida(Exception):
    """El proveedor detuvo la solicitud: la corrida se reanuda más tarde con el mismo comando."""


@dataclass(frozen=True)
class Unidad:
    tipo: str  # requisitos, analisis o reanalisis
    grupo: str  # requisitos, principal, mejoras, estabilidad, oferta_b, control o formato
    documento: str | None = None
    formato: str = "txt"
    oferta: str | None = None
    repeticion: int = 1
    mejoras: bool = False

    @property
    def id(self) -> str:
        partes = [self.tipo, self.oferta or ""] if self.tipo == "requisitos" else [
            self.tipo, self.documento or "", self.formato, self.oferta or "", f"r{self.repeticion}"]
        if self.mejoras:
            partes.append("mejoras")
        return re.sub(r"[^A-Za-z0-9_.-]", "_", "__".join(partes))

    def descripcion(self) -> str:
        if self.tipo == "requisitos":
            return f"requisitos de {self.oferta}"
        texto = f"{'reanálisis' if self.tipo == 'reanalisis' else 'análisis'} {self.documento} ({self.formato.upper()}) " \
                f"frente a {self.oferta}"
        if self.repeticion > 1:
            texto += f", repetición {self.repeticion}"
        return texto + (", con mejoras" if self.mejoras else "")


def oferta_de(documento: dict[str, Any], version: str) -> str:
    return f"{documento['vacante']}_{version}"


def planificar(corpus: Corpus, metodo: str, diseno: str) -> list[Unidad]:
    """Unidades de la corrida, en orden de prioridad."""
    if diseno not in DISENOS:
        raise ValueError(f"Diseño desconocido: {diseno} (use {', '.join(DISENOS)}).")
    con_modelo = metodo != "linea_base"
    base = [d for d in corpus.documentos if d["control_de"] is None]
    controles = [d for d in corpus.documentos if d["control_de"] is not None]
    if diseno == "prueba":
        base = [d for d in base if d["posicion"] == POSICION_PRUEBA]
    unidades = [Unidad("requisitos", "requisitos", oferta=o["id"]) for o in corpus.ofertas
                if o["version"] == "A" or diseno != "prueba"]
    unidades += [Unidad("analisis", "principal", d["id"], "txt", oferta_de(d, "A"), 1, True) for d in base]
    if con_modelo:
        unidades += [Unidad("reanalisis", "mejoras", d["id"], "txt", oferta_de(d, "A")) for d in base]
    if diseno == "prueba":
        return unidades
    if con_modelo:
        unidades += [Unidad("analisis", "estabilidad", d["id"], "txt", oferta_de(d, "A"), r)
                     for r in range(2, REPETICIONES + 1) for d in base if d["posicion"] in POSICIONES_ESTABILIDAD]
    unidades += [Unidad("analisis", "oferta_b", d["id"], "txt", oferta_de(d, "B")) for d in base]
    unidades += [Unidad("analisis", "control", d["id"], "txt", oferta_de(d, "A")) for d in controles]
    con_formatos = [d for d in base if diseno == "completo" or d["posicion"] in POSICIONES_FORMATO_NUCLEO]
    unidades += [Unidad("analisis", "formato", d["id"], formato, oferta_de(d, "A"))
                 for formato in ("docx", "pdf") for d in con_formatos]
    return unidades


def repartir(unidades: list[Unidad], partes: int) -> dict[str | None, int]:
    """Parte (0 a partes − 1) de cada hoja de vida; los requisitos (documento None) van en la parte 0.

    Reparte por carga: cada hoja de vida, de la de más unidades a la de menos, va a la parte con menos unidades.
    """
    carga: dict[str, int] = {}
    for unidad in unidades:
        if unidad.documento is not None:
            carga[unidad.documento] = carga.get(unidad.documento, 0) + 1
    totales = [0] * partes
    totales[0] = sum(u.documento is None for u in unidades)
    asignacion: dict[str | None, int] = {None: 0}
    for documento, cantidad in sorted(carga.items(), key=lambda par: -par[1]):
        parte = totales.index(min(totales))
        asignacion[documento] = parte
        totales[parte] += cantidad
    return asignacion


def _resumen_intentos(etapa: str, intentos: list[Intento]) -> dict[str, Any]:
    """Lo mismo que anota el motor por cada llamada (motor.Analizador._anotar)."""
    respuestas = [i.respuesta for i in intentos if i.respuesta is not None]
    return {"etapa": etapa, "intentos": len(intentos), "estado": intentos[-1].estado if intentos else None,
            "tokens_entrada": sum(r.tokens_entrada or 0 for r in respuestas),
            "tokens_salida": sum(r.tokens_salida or 0 for r in respuestas),
            "tokens_total": sum(r.tokens_total or 0 for r in respuestas),
            "latencia_s": round(sum(i.latencia_s or 0 for i in intentos), 3)}


class Corrida:
    """Una corrida de la evaluación: un método, un diseño y una carpeta de resultados."""

    def __init__(self, cfg: Configuracion, corpus: Corpus, metodo: str, *, carpeta: Path, diseno: str = "nucleo",
                 salida: Salida = print, backend: Backend | None = None, particion: tuple[int, int] | None = None):
        """«backend» reemplaza al del método (lo usan las pruebas para simular fallas del proveedor).

        «particion» = (K, N), con K de 1 a N: corre solo las unidades de la parte K (ver el docstring del módulo).
        """
        if particion is not None and not 1 <= particion[0] <= particion[1]:
            raise ValueError(f"Parte inválida: {particion[0]}/{particion[1]} (use K/N con K de 1 a N).")
        self.cfg = cfg
        self.corpus = corpus
        self.metodo = metodo
        self.diseno = diseno
        self.carpeta = Path(carpeta)
        self.salida = salida
        self.particion = particion
        self.cache: BackendConCache | None = None
        modelo = None
        if metodo != "linea_base":
            if backend is None:
                backend = BackendSimulado() if metodo == "simulado" else crear_backend(cfg.perfil(metodo), salida=salida)
            self.cache = BackendConCache(backend, self.carpeta / "llamadas.sqlite")
            modelo = (crear_modelo_simulado(cfg, self.cache) if metodo == "simulado"
                      else ModeloLLM(cfg, metodo, backend=self.cache, salida=salida))
        self.analizador = Analizador(cfg, modelo, referencia=corpus.fecha_referencia)
        self.unidades = planificar(corpus, metodo, diseno)
        self._parte = repartir(self.unidades, particion[1]) if particion else {}

    def en_particion(self, unidad: Unidad) -> bool:
        return self.particion is None or self._parte[unidad.documento] == self.particion[0] - 1

    # ------------------------------------------------------------- archivos
    def ruta_unidad(self, unidad: Unidad) -> Path:
        return self.carpeta / "unidades" / f"{unidad.id}.json"

    def hecha(self, unidad: Unidad) -> bool:
        return self.ruta_unidad(unidad).exists()

    def _identidad(self) -> dict[str, Any]:
        return {"metodo": self.metodo, "version_motor": VERSION_MOTOR, "huella_configuracion": self.analizador.huella("especifico"),
                "huella_corpus": self.corpus.datos["huella_fuentes"], "fecha_referencia": str(self.corpus.fecha_referencia)}

    def _comprobar_identidad(self) -> None:
        actual = self._identidad()
        for ruta in [self.carpeta / "corrida.json", *sorted(self.carpeta.glob("particion_*_de_*.json"))]:
            if not ruta.exists():
                continue
            anterior = leer_json(ruta)
            distintos = [k for k, v in actual.items() if anterior.get(k) != v]
            if distintos:
                raise ValueError(f"La carpeta {self.carpeta} tiene una corrida con otra configuración ({', '.join(distintos)}). "
                                 "No se mezclan resultados: use --corrida con otro nombre o borre esa carpeta.")

    def _guardar_corrida(self, estado: str, inicio: str) -> None:
        hechas = sum(self.hecha(u) for u in self.unidades)
        nombre = f"particion_{self.particion[0]}_de_{self.particion[1]}.json" if self.particion else "corrida.json"
        escribir_json(self.carpeta / nombre, {
            **self._identidad(), "diseno": self.diseno, "estado": estado, "unidades": len(self.unidades),
            "unidades_hechas": hechas, "inicio_ultima_ejecucion": inicio, "fin_ultima_ejecucion": ahora_iso(),
            "llamadas_nuevas_ultima_ejecucion": self.cache.nuevas if self.cache else 0,
            "llamadas_desde_cache_ultima_ejecucion": self.cache.aciertos if self.cache else 0,
            **({"particion": f"{self.particion[0]}/{self.particion[1]}",
                "unidades_de_la_particion": sum(self.en_particion(u) for u in self.unidades)} if self.particion else {})})

    # -------------------------------------------------------------- unidades
    def _requisitos(self, oferta: str) -> RequisitosOferta:
        unidad = Unidad("requisitos", "requisitos", oferta=oferta)
        datos = leer_json(self.ruta_unidad(unidad))
        if datos.get("error"):
            raise ErrorAnalisis("requisitos_oferta", f"Los requisitos de {oferta} fallaron: {datos['error']}")
        return RequisitosOferta.desde_dict(datos["requisitos"])

    def _leer(self, documento_id: str, formato: str):
        documento = self.corpus.documento(documento_id)
        ruta = self.corpus.ruta(documento["archivos"][formato])
        return leer_documento(ruta.name, ruta.read_bytes(), **self.cfg.documentos)

    def _ejecutar_unidad(self, unidad: Unidad) -> dict[str, Any]:
        if unidad.tipo == "requisitos":
            intentos: dict[str, list[Intento]] = {}
            texto = self.corpus.texto_oferta(unidad.oferta)
            requisitos = self.analizador.requisitos_de(
                texto, registrar=lambda etapa, intento, m, o: intentos.setdefault(etapa, []).append(intento))
            return {"requisitos": requisitos.a_dict(), "salida_cruda": requisitos.salida_cruda,
                    "llamadas": [_resumen_intentos(e, i) for e, i in intentos.items()]}
        requisitos = self._requisitos(unidad.oferta)
        if unidad.tipo == "analisis":
            documento = self._leer(unidad.documento, unidad.formato)
            resultado = self.analizador.analizar(documento.texto, requisitos=requisitos, con_mejoras=unidad.mejoras,
                                                 advertencias=documento.advertencias)
            return {"texto": documento.texto, "lectura": {"metodo": documento.metodo, "paginas": documento.paginas,
                                                          "advertencias": documento.advertencias},
                    "resultado": resultado}
        principal = Unidad("analisis", "principal", unidad.documento, "txt", unidad.oferta, 1, True)
        antes = leer_json(self.ruta_unidad(principal))
        mejorado, aplicadas, no_aplicadas = aplicar_mejoras(antes["texto"], antes["resultado"])
        if not aplicadas:
            return {"aplicadas": [], "no_aplicadas": no_aplicadas, "texto_mejorado": None, "resultado": None,
                    "comparacion": None}
        despues = self.analizador.analizar(mejorado, requisitos=requisitos, con_mejoras=False)
        return {"aplicadas": aplicadas, "no_aplicadas": no_aplicadas, "texto_mejorado": mejorado,
                "resultado": despues, "comparacion": comparar(antes["resultado"], despues)}

    def _lista_para_correr(self, unidad: Unidad) -> str | None:
        """Motivo por el que la unidad todavía no se puede correr (le falta una unidad previa), o None."""
        previas = []
        if unidad.tipo != "requisitos":
            previas.append(Unidad("requisitos", "requisitos", oferta=unidad.oferta))
        if unidad.tipo == "reanalisis":
            previas.append(Unidad("analisis", "principal", unidad.documento, "txt", unidad.oferta, 1, True))
        for previa in previas:
            if not self.hecha(previa):
                return f"falta {previa.descripcion()}"
            if leer_json(self.ruta_unidad(previa)).get("error"):
                return f"falló {previa.descripcion()}"
        return None

    def ejecutar(self, *, limite_tokens: int | None = None, reintentar_fallidas: bool = False) -> dict[str, Any]:
        """Corre las unidades pendientes. Devuelve el conteo de hechas, nuevas, fallidas y pendientes."""
        self._comprobar_identidad()
        self.carpeta.mkdir(parents=True, exist_ok=True)
        inicio = ahora_iso()
        propias = [u for u in self.unidades if self.en_particion(u)]
        pendientes = [u for u in propias if not self.hecha(u)
                      or (reintentar_fallidas and leer_json(self.ruta_unidad(u)).get("error"))]
        parte = f", parte {self.particion[0]} de {self.particion[1]}" if self.particion else ""
        self.salida(f"Corrida «{self.carpeta.name}» ({self.metodo}, diseño {self.diseno}{parte}): {len(propias)} unidades, "
                    f"{len(propias) - len(pendientes)} ya hechas, {len(pendientes)} pendientes.")
        tokens_nuevos = nuevas = fallidas = omitidas = 0
        estado = "completa"
        try:
            for numero, unidad in enumerate(pendientes, start=1):
                motivo = self._lista_para_correr(unidad)
                if motivo:
                    omitidas += 1
                    self.salida(f"[{numero}/{len(pendientes)}] {unidad.descripcion()}: se omite ({motivo}).")
                    continue
                if self.cache is not None:
                    self.cache.iniciar_unidad(unidad.repeticion)
                    nuevas_antes = self.cache.nuevas
                t0 = time.perf_counter()
                registro: dict[str, Any] = {"unidad": {**asdict(unidad), "id": unidad.id}, "metodo": self.metodo,
                                            "fecha": ahora_iso()}
                try:
                    registro.update(self._ejecutar_unidad(unidad))
                except ErrorAnalisis as error:
                    tipo = getattr(error.__cause__, "tipo", None)
                    if tipo in ERRORES_QUE_DETIENEN:
                        raise DetenerCorrida(f"{unidad.descripcion()}: {error.mensaje}") from error
                    fallidas += 1
                    registro.update({"error": str(error), "tipo_error": tipo,
                                     "intentos": [{"numero": i.numero, "estado": i.estado, "errores": i.errores,
                                                   "latencia_s": i.latencia_s} for i in error.intentos]})
                registro["duracion_s"] = round(time.perf_counter() - t0, 3)
                if self.cache is not None:
                    registro["llamadas_nuevas"] = self.cache.nuevas - nuevas_antes
                    registro["tokens_nuevos"] = self.cache.tokens_unidad
                    if self.cache.estimacion_unidad:
                        registro["estimacion_tokens_nuevos"] = dict(self.cache.estimacion_unidad)
                    tokens_nuevos += self.cache.tokens_unidad
                escribir_json(self.ruta_unidad(unidad), registro)
                nuevas += 1
                self.salida(f"[{numero}/{len(pendientes)}] {unidad.descripcion()}: {self._linea(registro)}")
                if limite_tokens and tokens_nuevos >= limite_tokens:
                    estado = "detenida_por_limite"
                    self.salida(f"Se alcanzó el límite de {limite_tokens} tokens nuevos: la corrida se reanuda con el mismo comando.")
                    break
        except DetenerCorrida as error:
            estado = "detenida_por_el_proveedor"
            self.salida(f"La corrida se detuvo: {error}. Lo hecho quedó guardado; reanúdela con el mismo comando "
                        "(por ejemplo, mañana si se agotó la cuota diaria).")
        finally:
            if estado == "completa" and any(not self.hecha(u) for u in propias):
                estado = "incompleta"
            self._guardar_corrida(estado, inicio)
            if self.cache is not None:
                self.cache.cerrar()
        hechas = sum(self.hecha(u) for u in self.unidades)
        return {"estado": estado, "unidades": len(self.unidades), "hechas": hechas, "nuevas": nuevas,
                "fallidas": fallidas, "omitidas": omitidas, "tokens_nuevos": tokens_nuevos}

    @staticmethod
    def _linea(registro: dict[str, Any]) -> str:
        if registro.get("error"):
            return f"FALLÓ ({registro['error'][:160]})"
        if "requisitos" in registro:
            return f"{len(registro['requisitos']['requisitos'])} requisitos"
        resultado = registro.get("resultado")
        if resultado is None:
            return "sin sugerencias de redacción aprobadas: no hay versión mejorada"
        partes = [f"puntuación {resultado['puntuacion']['total']}"]
        if resultado.get("compatibilidad"):
            partes.append(f"compatibilidad {resultado['compatibilidad']['porcentaje']} %")
        if registro.get("comparacion"):
            delta = registro["comparacion"]["puntuacion"]["delta"]
            partes.append(f"{len(registro['aplicadas'])} mejoras aplicadas, Δ puntuación {delta:+}")
        if resultado.get("llamadas"):
            partes.append(f"{len(resultado['llamadas'])} llamadas")
        return ", ".join(partes) + f" ({registro['duracion_s']} s)"
