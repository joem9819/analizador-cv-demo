"""Línea base por palabras clave, sin modelo de lenguaje (RF14 y comparación de la hipótesis H1).

Produce los mismos JSON que el modelo (extracción, requisitos de la oferta y cobertura), así que pasa
por la misma verificación y las mismas fórmulas: lo único que cambia entre los dos métodos es quién
extrae y clasifica. Sirve como modo de demostración sin cuota de API y como comparación en la
evaluación técnica (definición del proyecto, §3.5).

Reglas:
- Secciones por sus encabezados (secciones.py); habilidades por el diccionario (habilidades.py).
- Experiencias y proyectos: en su sección, una entrada empieza en un renglón corto que no es viñeta
  (después de un renglón en blanco, de viñetas o al inicio). Sus renglones cortos siguientes son la
  cabecera (cargo, empresa y fechas); los demás, actividades. Un renglón que empieza en minúscula
  después de una actividad la continúa (viñeta partida en dos renglones). El cargo es lo que queda
  antes del primer separador («—», «|», « - », «,») tras quitar las fechas.
- Formación y certificaciones: cada renglón que no es viñeta es un estudio; un renglón que solo
  tiene fechas se une al anterior, y uno que lo continúa (el anterior deja un paréntesis abierto o
  termina en «de», «y», «—»…, o este empieza en minúscula) se pega a él (Etapa 8: PDF a columnas).
- Las fechas, duraciones y separadores no cuentan en el límite de 12 palabras de una cabecera, que sube a
  20 si el renglón tiene barras («Cargo | Empresa | fechas»); se usa el primer separador que da dos partes (Etapa 8).
- Una actividad es «logro» si tiene cifras o empieza con un verbo de resultado (reduje, mejoré…).
- Redacción: verbos de acción por la proporción de actividades que empiezan con un verbo de acción;
  claridad por la longitud media de las actividades; la ortografía no se evalúa (nivel neutro 2).
- Requisitos: cada renglón de la oferta con una habilidad del diccionario (un requisito con todas las
  habilidades del renglón), un tiempo de experiencia, una formación o un idioma. Es «deseable» si el
  renglón o su encabezado dice deseable, plus, valorable, preferible u opcional. Se omiten las
  secciones «Ofrecemos», «Beneficios» y similares y, si la oferta tiene encabezados («Requisitos:»),
  los renglones anteriores al primero (título y presentación del cargo).
- Cobertura: el requisito queda «cubierto» si alguna de sus habilidades se usó en una experiencia o
  proyecto, y «mencionado» si solo aparece en otra parte; el tiempo de experiencia se compara con los
  meses calculados.
"""

from __future__ import annotations

import re
from typing import Any

from analizador_cv.analisis.compatibilidad import Requisito
from analizador_cv.analisis.cotejo import normalizar
from analizador_cv.analisis.extraccion import Bloque, Extraccion, es_cuantificada
from analizador_cv.analisis.fechas import MESES, PALABRAS_ACTUAL, duracion_en_meses, meses_acumulados
from analizador_cv.analisis.habilidades import Diccionario
from analizador_cv.analisis.puntuacion import Redaccion
from analizador_cv.analisis.secciones import Secciones

_VINETA = re.compile(r"^\s*(?:[-•*·▪●◦–]|\d+[.)])\s+")
_MES = "|".join(sorted(MESES, key=len, reverse=True))
_ACTUALES = "|".join(sorted((re.escape(p) for p in PALABRAS_ACTUAL), key=len, reverse=True))
_FECHA = re.compile(rf"(?<![a-z])(?:(?:{_MES})\.?\s*(?:de\s+|del\s+)?(?:19|20)\d{{2}}|(?<!\d)\d{{1,2}}[/.-](?:19|20)\d{{2}}"
                    rf"|(?<!\d)(?:19|20)\d{{2}}[/.-]\d{{1,2}}(?!\d)|(?<!\d)(?:19|20)\d{{2}}(?!\d)|(?:{_ACTUALES})(?![a-z]))",
                    re.IGNORECASE)
_DURACION = re.compile(r"\d+\s*(?:años?|mes(?:es)?)(?:\s+y\s+(?:\d+\s*mes(?:es)?|medio))?", re.IGNORECASE)
_SEPARADORES = (" — ", " – ", " | ", " - ", " · ", ", ")
_PARENTESIS_VACIOS = re.compile(r"\(\s*(?:(?:de|del|a|al|hasta|desde)\s*|[-–—,;|/]\s*)*\)", re.IGNORECASE)
_BORDES = " \t,;:|–—-/"
_PARENTESIS_FINAL = re.compile(r"\s*\([^()]*\)\s*$")
_ABREVIATURA_FINAL = re.compile(r"(?:\b[A-Za-z]\.){2,}$|\b(?:Ltda|Inc|Ltd|Cía|Cia|Corp|Co|S\.?A)\.$")
_ES_ACTUAL = re.compile(rf"^(?:{_ACTUALES})$", re.IGNORECASE)
_FINAL_ABIERTO = re.compile(r"(?:[—–|,(-]|\b(?:de|del|y|e|en|con|para|a|al|la|las|los|el|por|o|u))\s*$", re.IGNORECASE)

VERBOS_ACCION = ("desarroll", "implement", "disen", "constru", "cre", "automatiz", "optimic", "optimiz", "reduj", "reduc",
                 "aument", "mejor", "lider", "coordin", "gestion", "administr", "configur", "instal", "document",
                 "analic", "analiz", "ejecut", "migr", "integr", "mantuv", "manten", "atend", "resolv", "solucion",
                 "capacit", "escrib", "program", "codific", "depur", "correg", "despleg", "monitore", "report",
                 "elabor", "defin", "planific", "valid", "verific", "entreg", "logr", "increment", "disminu", "dise",
                 "prob", "realic", "realiz", "conect", "actualic", "actualiz", "construy", "levant", "model")
VERBOS_RESULTADO = ("reduj", "reduc", "aument", "mejor", "logr", "optimic", "optimiz", "increment", "disminu", "ahorr",
                    "aceler", "gane", "gano", "obtuv")
_DESEABLE = re.compile(r"(?<![a-z])(?:deseable|deseables|plus|valorable|se valora|preferible|preferiblemente|ideal|"
                       r"opcional|bonus|se tendra en cuenta)(?![a-z])")
_SIN_REQUISITOS = re.compile(r"(?<![a-z])(?:ofrecemos|beneficios|condiciones|salario|contrato|sobre nosotros|"
                             r"quienes somos|la empresa)(?![a-z])")
_EXPERIENCIA = re.compile(r"(?<![a-z])experiencia(?![a-z])")
_FORMACION = re.compile(r"(?<![a-z])(?:ingenier\w*|tecnolog[oa]s?|tecnic[oa]s?|profesional(?:es)? en|estudiantes? de|"
                        r"carreras?|pregrado|titulo|egresad[oa]s?)(?![a-z])")
_IDIOMA = re.compile(r"(?<![a-z])(ingles|frances|portugues|aleman|italiano)(?![a-z])")
_NOMBRE_IDIOMA = {"ingles": "Inglés", "frances": "Francés", "portugues": "Portugués", "aleman": "Alemán",
                  "italiano": "Italiano"}
_IDIOMAS_CV = re.compile(r"(?<!\w)(ingl[eé]s|espa[ñn]ol|franc[eé]s|portugu[eé]s|alem[aá]n|italiano)(?!\w)"
                         r"(?:\s*[:(\-–]?\s*((?:[ABC][12])|b[aá]sico|intermedio|avanzado|nativo|materno|fluido))?",
                         re.IGNORECASE)
_PALABRAS_VACIAS = {"de", "del", "la", "las", "el", "los", "en", "y", "o", "a", "con", "para", "por", "un", "una", "afin",
                    "afines", "area", "areas", "similar", "similares", "carrera", "carreras", "titulo", "formacion"}


# =============================================================================
# Extracción
# =============================================================================
def _sin_vineta(renglon: str) -> tuple[str, bool]:
    coincidencia = _VINETA.match(renglon)
    return (renglon[coincidencia.end():].strip(), True) if coincidencia else (renglon.strip(), False)


def _es_corto(contenido: str) -> bool:
    """Renglón de cabecera: hasta 12 palabras y sin punto final (salvo abreviaturas como «S.A.S.» o «Ltda.»).

    Las fechas, las duraciones y los separadores («|», «–») no cuentan como palabras, y un renglón con
    barras («Cargo | Empresa | fechas») admite hasta 20 (correcciones de la Etapa 8).
    """
    oracion = contenido.endswith(".") and not _ABREVIATURA_FINAL.search(contenido)
    palabras = [p for p in _DURACION.sub(" ", _FECHA.sub(" ", contenido)).split() if re.search(r"\w", p)]
    return len(palabras) <= (20 if " | " in contenido else 12) and not oracion


def _entradas(texto: str) -> list[dict[str, list[str]]]:
    """Divide una sección de experiencia o proyectos en entradas con cabecera y actividades."""
    entradas: list[dict[str, list[str]]] = []
    actual: dict[str, list[str]] | None = None
    blanco = True
    for renglon in texto.split("\n"):
        if not renglon.strip():
            blanco = True
            continue
        contenido, vineta = _sin_vineta(renglon)
        if not vineta and not blanco and actual is not None and actual["actividades"] and contenido[:1].islower():
            actual["actividades"][-1] += " " + contenido  # viñeta larga partida en dos renglones (PDF)
        elif not vineta and _es_corto(contenido) and (actual is None or actual["actividades"] or blanco):
            actual = {"cabecera": [contenido], "actividades": []}
            entradas.append(actual)
        elif not vineta and _es_corto(contenido) and actual is not None and len(actual["cabecera"]) < 3:
            actual["cabecera"].append(contenido)
        else:
            if actual is None:
                actual = {"cabecera": [], "actividades": []}
                entradas.append(actual)
            actual["actividades"].append(contenido)
        blanco = False
    return [e for e in entradas if e["cabecera"]]


def _fechas(texto: str) -> tuple[str | None, str | None, str | None]:
    """(inicio, fin, duración) tal como están escritos en la cabecera."""
    encontradas = [m.group(0) for m in _FECHA.finditer(texto)]
    inicio = next((f for f in encontradas if not _ES_ACTUAL.match(f)), None)
    resto = encontradas[encontradas.index(inicio) + 1:] if inicio else encontradas
    fin = resto[0] if resto else None
    duracion = None
    if not inicio and not fin:
        coincidencia = _DURACION.search(texto)
        duracion = coincidencia.group(0) if coincidencia else None
    return inicio, fin, duracion


def _limpiar(parte: str) -> str:
    """Quita los paréntesis vacíos que dejan las fechas y el paréntesis final («(en curso, 9.º semestre)»)."""
    parte = _PARENTESIS_FINAL.sub("", _PARENTESIS_VACIOS.sub(" ", parte))
    return re.sub(r"\s{2,}", " ", parte).strip(_BORDES)


def _partes(renglon: str) -> list[str]:
    """Cargo y empresa (o título e institución) de un renglón, sin sus fechas."""
    sin_fechas = _PARENTESIS_VACIOS.sub(" ", _DURACION.sub(" ", _FECHA.sub(" ", renglon)))
    respaldo: list[str] = []
    for separador in _SEPARADORES:
        if separador in sin_fechas:
            partes = [p for p in (_limpiar(x) for x in sin_fechas.split(separador)) if len(p) > 1]
            if len(partes) >= 2:  # el primer separador que de verdad separa (el «–» de las fechas no)
                return partes
            respaldo = respaldo or partes
    if respaldo:
        return respaldo
    limpio = _limpiar(sin_fechas)
    return [limpio] if len(limpio) > 1 else []


def _tipo_actividad(texto: str) -> str:
    primera = normalizar(texto).split(" ", 1)[0] if texto.strip() else ""
    if es_cuantificada(texto) or primera.startswith(VERBOS_RESULTADO):
        return "logro"
    return "funcion"


def _habilidades_en(texto: str, diccionario: Diccionario) -> list[str]:
    return [h.canonico for h, _ in diccionario.detectar(normalizar(texto))]


def _bloques_experiencia(texto: str, diccionario: Diccionario, *, proyecto: bool) -> list[dict[str, Any]]:
    resultado = []
    for entrada in _entradas(texto):
        con_texto = [p for p in (_partes(r) for r in entrada["cabecera"]) if p]  # sin los renglones de solo fechas
        if not con_texto:
            continue
        partes = con_texto[0]
        cabecera = " | ".join(entrada["cabecera"])
        inicio, fin, duracion = _fechas(cabecera)
        empresa = partes[1] if len(partes) > 1 else (con_texto[1][0] if len(con_texto) > 1 else None)
        actividades = [{"texto": a, "tipo": _tipo_actividad(a)} for a in entrada["actividades"]]
        habilidades = _habilidades_en(cabecera + "\n" + "\n".join(entrada["actividades"]), diccionario)
        if proyecto:
            resultado.append({"nombre": partes[0], "inicio": inicio, "fin": fin, "actividades": actividades,
                              "habilidades": habilidades})
        else:
            resultado.append({"cargo": partes[0], "empresa": empresa, "inicio": inicio, "fin": fin, "duracion": duracion,
                              "actividades": actividades, "habilidades": habilidades})
    return resultado


def _tipo_formacion(texto: str, predeterminado: str) -> str:
    t = normalizar(texto)
    if re.search(r"(?<![a-z])(?:certificacion|certificado|certified|certificate)", t):
        return "certificacion"
    if re.search(r"(?<![a-z])(?<!en )(?:curso|diplomado|bootcamp|taller|seminario|horas)(?![a-z])", t):
        return "curso"
    return predeterminado


def _estado_formacion(texto: str, fin: str | None) -> str:
    t = normalizar(texto)
    if re.search(r"(?<![a-z])(?:en curso|cursando|semestre|actualmente|actualidad)(?![a-z])", t):
        return "en_curso"
    if fin or re.search(r"(?<![a-z])(?:graduad|titulad|egresad|culminad|finalizad|terminad)", t):
        return "completo"
    return "sin_dato"


def _continua(anterior: str, contenido: str) -> bool:
    """El renglón sigue al anterior (un estudio largo partido en dos renglones, como en un PDF a columnas):
    el anterior deja un paréntesis abierto o termina en un separador o en una palabra de enlace, o este
    empieza en minúscula."""
    return (anterior.count("(") > anterior.count(")") or bool(_FINAL_ABIERTO.search(anterior))
            or contenido[:1].islower())


def _bloques_formacion(texto: str, diccionario: Diccionario, predeterminado: str) -> list[dict[str, Any]]:
    entradas: list[list[str]] = []
    blanco = True
    for renglon in texto.split("\n"):
        contenido, vineta = _sin_vineta(renglon)
        if not contenido:
            blanco = True
            continue
        solo_fechas = not re.search(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{3,}", _DURACION.sub(" ", _FECHA.sub(" ", contenido)))
        if entradas and not vineta and not blanco and _continua(entradas[-1][-1], contenido):
            entradas[-1][-1] += " " + contenido
        elif entradas and (vineta or solo_fechas):
            entradas[-1].append(contenido)
        else:
            entradas.append([contenido])
        blanco = False
    resultado = []
    for entrada in entradas:
        partes = _partes(entrada[0])
        if not partes:
            continue
        todo = " | ".join(entrada)
        inicio, fin, _ = _fechas(todo)
        resultado.append({"titulo": partes[0], "institucion": partes[1] if len(partes) > 1 else None,
                          "inicio": inicio, "fin": fin, "estado": _estado_formacion(todo, fin),
                          "tipo": _tipo_formacion(todo, predeterminado),
                          "habilidades": _habilidades_en(todo, diccionario)})
    return resultado


def extraer(texto: str, secciones: Secciones, diccionario: Diccionario) -> dict[str, Any]:
    """Extracción por reglas, con el mismo formato que devuelve el modelo (esquemas.ESQUEMA_EXTRACCION)."""
    bloques = secciones.bloques
    perfil = (bloques.get("perfil") or "").strip().split("\n\n")[0].strip() or None
    texto_idiomas = bloques.get("idiomas") or texto
    idiomas, vistos = [], set()
    for coincidencia in _IDIOMAS_CV.finditer(texto_idiomas):
        if (bloques.get("idiomas") or coincidencia.group(2)) and normalizar(coincidencia.group(1)) not in vistos:
            vistos.add(normalizar(coincidencia.group(1)))
            idiomas.append({"idioma": coincidencia.group(1), "nivel": coincidencia.group(2)})
    return {
        "perfil": perfil,
        "experiencias": _bloques_experiencia(bloques.get("experiencia", ""), diccionario, proyecto=False),
        "proyectos": _bloques_experiencia(bloques.get("proyectos", ""), diccionario, proyecto=True),
        "formacion": (_bloques_formacion(bloques.get("formacion", ""), diccionario, "formal")
                      + _bloques_formacion(bloques.get("certificaciones", ""), diccionario, "curso")),
        "habilidades": [{"nombre": h.canonico, "tipo": h.tipo} for h, _ in diccionario.detectar(normalizar(texto))],
        "idiomas": idiomas,
    }


# =============================================================================
# Redacción
# =============================================================================
def redaccion(extraccion: Extraccion) -> Redaccion:
    actividades = [a.texto for b in extraccion.experiencias + extraccion.proyectos for a in b.actividades]
    if not actividades:
        return Redaccion(0, 1, fuente="linea_base", ortografia_evaluada=False)
    con_verbo = sum(normalizar(a).startswith(VERBOS_ACCION) for a in actividades) / len(actividades)
    verbos = 3 if con_verbo >= 0.8 else 2 if con_verbo >= 0.5 else 1 if con_verbo >= 0.2 else 0
    media = sum(len(a.split()) for a in actividades) / len(actividades)
    claridad = 3 if media <= 20 else 2 if media <= 30 else 1 if media <= 45 else 0
    return Redaccion(verbos, claridad, fuente="linea_base", ortografia_evaluada=False,
                     evidencia={"verbos_accion": [f"{round(100 * con_verbo)} % de las actividades empieza con un verbo de acción"],
                                "claridad": [f"{round(media)} palabras por actividad en promedio"]})


# =============================================================================
# Oferta y cobertura
# =============================================================================
def requisitos(texto_oferta: str, diccionario: Diccionario) -> dict[str, Any]:
    """Requisitos por reglas, con el formato del modelo (esquemas.ESQUEMA_REQUISITOS)."""
    lista: list[dict[str, Any]] = []
    deseable_seccion = omitir_seccion = False
    contenidos = [c for c, _ in map(_sin_vineta, re.split(r"\n|;", texto_oferta)) if c]
    es_encabezado = [c.endswith(":") and len(normalizar(c).split()) <= 6 for c in contenidos]
    dentro = not any(es_encabezado)  # con encabezados, lo anterior al primero es el título y la presentación
    for contenido, encabezado in zip(contenidos, es_encabezado):
        norma = normalizar(contenido)
        if encabezado:
            dentro = True
            deseable_seccion = bool(_DESEABLE.search(norma))
            omitir_seccion = bool(_SIN_REQUISITOS.search(norma))
            continue
        if omitir_seccion or not dentro:
            continue
        tipo = "deseable" if deseable_seccion or _DESEABLE.search(norma) else "obligatorio"
        habilidades = diccionario.detectar(norma)
        if habilidades:
            categoria = "habilidad_blanda" if all(h.tipo == "blanda" for h, _ in habilidades) else "habilidad_tecnica"
            lista.append({"texto": contenido, "tipo": tipo, "categoria": categoria,
                          "terminos": [h.canonico for h, _ in habilidades]})
        elif _EXPERIENCIA.search(norma) and duracion_en_meses(norma):
            lista.append({"texto": contenido, "tipo": tipo, "categoria": "experiencia", "terminos": []})
        elif _IDIOMA.search(norma):
            lista.append({"texto": contenido, "tipo": tipo, "categoria": "idioma",
                          "terminos": [_NOMBRE_IDIOMA[_IDIOMA.search(norma).group(1)]]})
        elif _FORMACION.search(norma):
            lista.append({"texto": contenido, "tipo": tipo, "categoria": "formacion", "terminos": []})
    return {"cargo": None, "empresa": None, "requisitos": lista}


def _renglon_con(texto: str, termino: str, diccionario: Diccionario) -> str | None:
    for renglon in texto.split("\n"):
        if diccionario.en_texto(termino, normalizar(renglon)):
            return _sin_vineta(renglon)[0] or None
    return None


def _evidencia_en_bloques(termino: str, bloques: list[Bloque], diccionario: Diccionario) -> str | None:
    clave = diccionario.clave(termino)
    for bloque in bloques:
        if any(diccionario.clave(h) == clave for h in bloque.habilidades):
            for actividad in bloque.actividades:
                if diccionario.en_texto(termino, normalizar(actividad.texto)):
                    return actividad.texto
            return bloque.titulo
    return None


def cobertura(lista_requisitos: list[Requisito], extraccion: Extraccion, texto_cv: str,
              diccionario: Diccionario) -> list[dict[str, Any]]:
    """Cobertura por reglas, con el formato del modelo (la parte «requisitos» de la evaluación)."""
    aplicados = extraccion.experiencias + extraccion.proyectos
    texto_norm = normalizar(texto_cv)
    meses = meses_acumulados(e.periodo for e in extraccion.experiencias)
    resultado = []
    for requisito in lista_requisitos:
        estado, evidencia = "ausente", None
        if requisito.categoria == "experiencia":
            pedido = duracion_en_meses(requisito.texto)
            if extraccion.experiencias:
                evidencia = extraccion.experiencias[0].titulo
                estado = "cubierto" if pedido is None or meses >= pedido else "mencionado"
        elif requisito.categoria == "formacion":
            formales = [b for b in extraccion.formacion if b.tipo == "formal"]
            palabras = {p for p in normalizar(requisito.texto).split() if len(p) >= 5 and p not in _PALABRAS_VACIAS}
            afin = next((b for b in formales if palabras & set(normalizar(b.titulo).split())), None)
            if afin or formales:
                estado, evidencia = ("cubierto", afin.titulo) if afin else ("mencionado", formales[0].titulo)
        elif requisito.categoria == "idioma":
            idioma = next((i["idioma"] for i in extraccion.idiomas
                           if any(normalizar(i["idioma"]) == normalizar(t) for t in requisito.terminos)), None)
            if idioma:
                estado, evidencia = "cubierto", idioma
        else:
            for termino in requisito.terminos:
                encontrada = _evidencia_en_bloques(termino, aplicados, diccionario)
                if encontrada:
                    estado, evidencia = "cubierto", encontrada
                    break
                if diccionario.en_texto(termino, texto_norm) and estado == "ausente":
                    estado, evidencia = "mencionado", _renglon_con(texto_cv, termino, diccionario)
        resultado.append({"id": requisito.id, "estado": estado, "evidencia": evidencia})
    return resultado
