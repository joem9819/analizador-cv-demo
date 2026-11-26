"""Extracción de ofertas por URL (pregunta P4; definición del proyecto, §4, §5.3 y §9).

Lee una lista de enlaces (un archivo de texto: una URL por renglón; «#» inicia un comentario) y pide cada
uno UNA vez con la misma función de la app (ofertas.obtener_oferta): no descarga los portales cuyos
términos prohíben el acceso automatizado, respeta robots.txt e intenta JSON-LD «JobPosting» y luego el
texto principal. De cada enlace guarda solo el resultado (estado, método, código HTTP y caracteres),
nunca el texto de la oferta. La lista debe tener solo sitios que no prohíben el acceso automatizado
(páginas de empleo de empresas, por ejemplo); quien la arma revisa sus términos.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import httpx

from analizador_cv.ofertas import obtener_oferta
from analizador_cv.utilidades import ahora_iso, escribir_json

Salida = Callable[[str], None]


def leer_lista(ruta: Path | str) -> list[str]:
    urls = []
    for renglon in Path(ruta).read_text(encoding="utf-8-sig").splitlines():
        renglon = renglon.split("#", 1)[0].strip()
        if renglon:
            urls.append(renglon)
    return urls


def medir_urls(urls: list[str], opciones: dict[str, Any], *, cliente: httpx.Client | None = None,
               salida: Salida = print) -> list[dict[str, Any]]:
    """Una petición por URL (más robots.txt). Devuelve una fila por URL, sin el texto de la oferta."""
    filas = []
    for numero, url in enumerate(urls, start=1):
        oferta = obtener_oferta(url, opciones, cliente=cliente)
        filas.append({"url": oferta.url or url, "dominio": oferta.dominio, "estado": oferta.estado,
                      "exito": oferta.exito, "metodo": oferta.metodo, "http_estado": oferta.http_estado,
                      "portal_prohibido": oferta.portal if oferta.estado == "portal_prohibido" else None,
                      "caracteres": oferta.caracteres, "fecha": oferta.fecha})
        salida(f"[{numero}/{len(urls)}] {oferta.dominio or url}: {oferta.estado}"
               + (f" ({oferta.metodo}, {oferta.caracteres} caracteres)" if oferta.exito else ""))
    return filas


def guardar_medicion(filas: list[dict[str, Any]], carpeta: Path) -> Path:
    ruta = Path(carpeta) / "urls" / "urls.json"
    escribir_json(ruta, {"descripcion": "Extracción de ofertas por URL (P4). Solo el resultado de cada enlace; "
                                        "no se guarda el texto de las ofertas.", "fecha": ahora_iso(), "filas": filas})
    return ruta


def resumen_urls(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Éxitos por método y casos por estado (para el informe)."""
    total = len(filas)

    def fila(medida: str, cantidad: int) -> dict[str, Any]:
        return {"medida": medida, "valor": cantidad, "porcentaje": round(100 * cantidad / total, 1) if total else None}

    resumen = [fila("enlaces", total), fila("extraídas", sum(bool(f["exito"]) for f in filas))]
    for metodo in sorted({f["metodo"] for f in filas if f["exito"] and f["metodo"]}):
        resumen.append(fila(f"método {metodo}", sum(f["exito"] and f["metodo"] == metodo for f in filas)))
    for estado in sorted({f["estado"] for f in filas if not f["exito"]}):
        resumen.append(fila(f"estado {estado}", sum(f["estado"] == estado for f in filas)))
    return resumen
