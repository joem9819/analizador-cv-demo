"""Caché de las llamadas al modelo durante la evaluación: permite reanudar una corrida sin repetir llamadas.

Envuelve un backend (la API del modelo o el simulado). Cada respuesta se guarda en SQLite con la clave
SHA-256 de (mensajes, opciones, repetición, ocurrencia):
- «repeticion» la fija la ejecución: la repetición 2 de un mismo análisis es otra llamada (estabilidad);
- «ocurrencia» cuenta las veces que se pidió lo mismo dentro de la unidad en curso: los reintentos de
  pedir_json reenvían el mismo mensaje y deben ser llamadas nuevas, no la respuesta inválida guardada.
  El contador vuelve a cero en cada unidad (iniciar_unidad), así que dos unidades con la misma hoja de
  vida comparten su extracción (por ejemplo, el análisis frente a las ofertas A y B).
Así, al reanudar, las llamadas se repiten en el mismo orden y salen de la caché con sus tokens y su
latencia originales. Los errores del backend (cuota agotada, conexión) no se guardan: se reintentan al reanudar.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from analizador_cv.llm.backends import Backend, InformacionModelo, RespuestaBackend
from analizador_cv.utilidades import ahora_iso, hash_objeto

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS llamadas (
    clave TEXT PRIMARY KEY,
    etapa TEXT,
    repeticion INTEGER,
    ocurrencia INTEGER,
    fecha TEXT,
    respuesta_json TEXT NOT NULL
)
"""


class BackendConCache:
    """Backend que responde desde la caché si ya hizo esa llamada; si no, llama al backend real y la guarda."""

    def __init__(self, backend: Backend, ruta: Path | str):
        self.backend = backend
        self.nombre = f"{backend.nombre}+cache"
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self._conexion = sqlite3.connect(self.ruta, timeout=60)  # varias partes pueden escribir a la vez
        self._conexion.execute(_ESQUEMA)
        self._conexion.commit()
        self.repeticion = 1
        self._ocurrencias: dict[str, int] = {}
        self.aciertos = 0
        self.nuevas = 0
        #: de la unidad en curso: tokens de las llamadas nuevas y, con el modelo simulado, su estimación por proveedor
        self.tokens_unidad = 0
        self.estimacion_unidad: dict[str, int] = {}

    def iniciar_unidad(self, repeticion: int = 1) -> None:
        self.repeticion = repeticion
        self._ocurrencias = {}
        self.tokens_unidad = 0
        self.estimacion_unidad = {}

    def informacion(self) -> InformacionModelo:
        return self.backend.informacion()

    def chat(self, mensajes: list[dict[str, str]], opciones: dict[str, Any]) -> RespuestaBackend:
        base = hash_objeto({"mensajes": mensajes, "opciones": opciones, "repeticion": self.repeticion})
        ocurrencia = self._ocurrencias.get(base, 0)
        self._ocurrencias[base] = ocurrencia + 1
        clave = hash_objeto({"base": base, "ocurrencia": ocurrencia})
        fila = self._conexion.execute("SELECT respuesta_json FROM llamadas WHERE clave = ?", (clave,)).fetchone()
        if fila:
            self.aciertos += 1
            return RespuestaBackend(**json.loads(fila[0]))
        respuesta = self.backend.chat(mensajes, opciones)  # un ErrorBackend se propaga y no se guarda
        etapa = ((opciones.get("response_format") or {}).get("json_schema") or {}).get("name")
        self._conexion.execute(
            "INSERT OR REPLACE INTO llamadas VALUES (?, ?, ?, ?, ?, ?)",
            (clave, etapa, self.repeticion, ocurrencia, ahora_iso(), json.dumps(asdict(respuesta), ensure_ascii=False)))
        self._conexion.commit()
        self.nuevas += 1
        self.tokens_unidad += respuesta.tokens_total or 0
        for perfil, tokens in ((respuesta.uso or {}).get("estimacion") or {}).items():
            self.estimacion_unidad[perfil] = self.estimacion_unidad.get(perfil, 0) + tokens["total"]
        return respuesta

    def cerrar(self) -> None:
        self._conexion.close()
