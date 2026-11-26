"""Exportación (RF12): la hoja de vida mejorada en TXT y DOCX y los resultados en CSV dentro de un .zip.

Los CSV usan UTF-8 con BOM (Excel los abre con tildes), coma como separador y punto decimal, para que
también los lean otros programas sin configuración. El .zip incluye el resultado completo en JSON y un
LEEME con el método, la fecha y, si es el caso, el aviso de que es una demostración sin modelo.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from typing import Any

from docx import Document
from docx.shared import Pt

from analizador_cv.interfaz import tablas
from analizador_cv.utilidades import json_legible

_ENCABEZADO = re.compile(r"^[A-ZÁÉÍÓÚÑ0-9 ,()/&-]{4,}$")
AVISO_DEMOSTRACION = ("DEMOSTRACIÓN: este análisis se hizo con la línea base por palabras clave, sin modelo de "
                      "lenguaje. La ortografía no se evaluó.")


def hoja_txt(texto: str) -> bytes:
    return texto.encode("utf-8")


def hoja_docx(texto: str) -> bytes:
    """Documento de Word con un párrafo por renglón: el primero (el nombre) y los encabezados en negrita."""
    documento = Document()
    estilo = documento.styles["Normal"]
    estilo.font.name, estilo.font.size = "Arial", Pt(10.5)
    primero = True
    for renglon in texto.split("\n"):
        parrafo = documento.add_paragraph()
        parrafo.paragraph_format.space_after = Pt(2)
        if not renglon.strip():
            continue
        run = parrafo.add_run(renglon)
        if primero:
            run.bold, run.font.size, primero = True, Pt(15), False
        elif _ENCABEZADO.match(renglon.strip()):
            run.bold, run.font.size = True, Pt(11.5)
    salida = io.BytesIO()
    documento.save(salida)
    return salida.getvalue()


def _csv(filas: list[dict[str, Any]]) -> bytes:
    salida = io.StringIO()
    if filas:
        escritor = csv.DictWriter(salida, fieldnames=list(filas[0]), lineterminator="\n")
        escritor.writeheader()
        escritor.writerows(filas)
    return ("﻿" + salida.getvalue()).encode("utf-8")


def _leeme(resultado: dict[str, Any], comparacion: dict[str, Any] | None) -> str:
    modelo = resultado["modelo"]
    renglones = [
        "Resultados del Analizador CV (prototipo del Seminario de Investigación, ITM)",
        "",
        f"Fecha del análisis: {resultado['fecha']}",
        f"Mes de referencia (recencia): {resultado['fecha_referencia']}",
        f"Modo: {'frente a una oferta' if resultado['modo'] == 'especifico' else 'general'}",
        "Método: " + (f"modelo {modelo['perfil']} ({modelo['modelo']})" if modelo else "línea base por palabras clave"),
        f"Huella de la configuración: {resultado['huella_configuracion']}",
        f"Puntuación general: {resultado['puntuacion']['total']} de 100 (rúbrica v{resultado['puntuacion']['version_rubrica']})",
    ]
    if resultado["compatibilidad"]:
        renglones.append(f"Compatibilidad con la oferta: {resultado['compatibilidad']['porcentaje']} %")
    if comparacion:
        renglones.append(f"Después de las mejoras: {comparacion['puntuacion']['despues']} de 100")
    if modelo is None:
        renglones += ["", AVISO_DEMOSTRACION]
    renglones += ["", "Archivos: criterios.csv, tags.csv, bloques.csv, sugerencias.csv y, si aplica, requisitos.csv, "
                      "llamadas.csv y comparacion_*.csv. resultado.json tiene el resultado completo."]
    return "\n".join(renglones) + "\n"


def resultados_zip(resultado: dict[str, Any], comparacion: dict[str, Any] | None = None,
                   despues: dict[str, Any] | None = None) -> bytes:
    archivos = {
        "criterios.csv": tablas.filas_criterios(resultado),
        "tags.csv": tablas.filas_tags(resultado),
        "bloques.csv": tablas.filas_bloques(resultado),
        "sugerencias.csv": tablas.filas_sugerencias(resultado),
        "requisitos.csv": tablas.filas_requisitos(resultado),
        "llamadas.csv": tablas.filas_llamadas(resultado),
    }
    if comparacion:
        archivos["comparacion_puntuacion.csv"] = tablas.filas_comparacion(comparacion)
        archivos["comparacion_tags.csv"] = tablas.filas_comparacion_tags(comparacion)
        archivos["comparacion_requisitos.csv"] = tablas.filas_comparacion_requisitos(comparacion)
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as comprimido:
        comprimido.writestr("LEEME.txt", _leeme(resultado, comparacion))
        for nombre, filas in archivos.items():
            if filas:
                comprimido.writestr(nombre, _csv(filas))
        comprimido.writestr("resultado.json", json_legible(resultado) + "\n")
        if despues is not None:
            comprimido.writestr("resultado_despues.json", json_legible(despues) + "\n")
    return salida.getvalue()
