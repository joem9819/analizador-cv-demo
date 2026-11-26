"""Guardia contra la información inventada en las mejoras (definición del proyecto, §6.5; RF10; H4).

Para cada sugerencia:
1. Redacción: el fragmento original debe estar en la hoja de vida (parecido ≥ 90 %) y la propuesta
   debe cambiar algo; si no, se descarta.
2. Se extraen las entidades de la propuesta y se buscan en TODO el texto original (reubicar
   información que ya está no es inventar):
   - cifras: números, años, porcentajes y duraciones («30 %», «2023», «18 meses»);
   - habilidades del diccionario («Docker», «pruebas unitarias»);
   - nombres propios y siglas: palabras con mayúscula que no inician una frase («Globant», «AWS»);
   - términos de cargo, nivel o grado («sénior», «líder», «gerente», «magíster»);
   - verbos que amplían la responsabilidad («diseñé», «lideré», «coordiné»…) sin una palabra de la
     misma raíz en el original: «Desarrollé» → «Diseñé y desarrollé» atribuye un diseño que no consta.
   Toda entidad ausente del original marca la sugerencia como «descartada» y queda listada. No cuentan
   como información nueva las palabras que nombran el propio documento («CV», «PDF», «ATS») ni las
   cifras de extensión de un consejo («al menos 250 palabras»).
3. Cambios de contenido (solo redacción; agregado en la Etapa 7 tras la prueba con el modelo real): la
   reescritura solo puede usar las palabras de su fragmento (con otra conjugación, otra tilde o en otro
   orden; se comparan las 4 primeras letras) y palabras de enlace. «…para la gestión de citas» →
   «…que automatizaron la gestión de citas» afirma un resultado que no consta. Tampoco puede quitar un
   atenuante: «Apoyo en la integración» → «Integré» atribuye a la persona todo el trabajo. Por precaución
   también se descartan los sinónimos («Construí» → «Implementé»). No cuentan como entidades nuevas en
   la tasa de invención: se reportan aparte (con_cambios_de_contenido).
4. Las palabras clave que faltan las propone el programa con estado «requiere_confirmacion»: nunca se
   insertan; la persona las agrega solo si realmente tiene esa habilidad.
Tasa de invención antes de la guardia = sugerencias del modelo con entidades nuevas ÷ sugerencias del
modelo. Después de la guardia ninguna sugerencia aprobada tiene entidades nuevas ni cambios de
contenido (por construcción); la revisión manual de una muestra mide la invención que la guardia no
detecta (Etapa 8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from analizador_cv.analisis.cotejo import contiene, normalizar, normalizar_con_mapa, ubicar_fragmento
from analizador_cv.analisis.habilidades import Diccionario

UMBRAL_FRAGMENTO_MEJORA = 0.9
TERMINOS_CARGO = ("senior", "sr", "semisenior", "semi senior", "ssr", "junior", "jr", "lider", "lead", "jefe", "jefa",
                  "gerente", "director", "directora", "coordinador", "coordinadora", "supervisor", "supervisora",
                  "arquitecto", "arquitecta", "manager", "head", "especialista", "experto", "experta", "magister",
                  "master", "maestria", "doctorado", "phd", "posgrado", "especializacion", "ingeniero", "ingeniera",
                  "tecnologo", "tecnologa")
#: Raíces (normalizadas) de verbos que amplían la responsabilidad de la persona.
RAICES_RESPONSABILIDAD = ("lider", "dirig", "coordin", "supervis", "diseñ", "gestion", "administr", "arquitect")
#: Palabras que nombran el documento o su formato: no son información sobre la persona.
TERMINOS_DOCUMENTO = {"cv", "hv", "ats", "pdf", "docx", "txt", "word"}
#: Palabras de enlace (normalizadas, de 4 letras o más) que una reescritura puede agregar sin afirmar nada.
PALABRAS_DE_ENLACE = {"para", "como", "entre", "sobre", "desde", "hasta", "durante", "mediante", "traves", "cual",
                      "cuales", "donde", "cuando", "tambien", "ademas", "segun", "hacia", "tras", "cada", "todo",
                      "todos", "toda", "todas", "este", "esta", "estos", "estas", "esto", "dicho", "dicha", "mismo",
                      "misma", "cuyo", "cuya", "cuyos", "cuyas", "usando", "utilizando", "empleando", "pero", "sino",
                      "aunque", "porque", "unos", "unas"}
#: Raíces (normalizadas) de las palabras que dicen que la persona apoyó o participó, sin hacerlo sola.
RAICES_ATENUANTES = ("apoy", "particip", "colabor", "ayud", "asist", "contribu", "acompañ", "auxili")
LARGO_RAIZ = 4
_MARCAS = re.compile(r"\[[A-Z_]+_\d+\]")
_NUMERO = re.compile(r"\d+(?:[.,]\d+)*")
_CIFRA_DE_EXTENSION = re.compile(r"(\d+(?:[.,]\d+)*)(?:\s*(?:-|‑|–|a)\s*(\d+))?\s*(?:palabras|p[áa]ginas|caracteres|"
                                 r"renglones|l[íi]neas|vi[ñn]etas|secciones|hojas)\b", re.IGNORECASE)
_PALABRA_MAYUSCULA = re.compile(r"(?<![\w.@/])([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñü.+#-]*)")
_INICIO_FRASE = re.compile(r"(?:^|[.!?:;\n(\"«“•]|(?:^|\s)[-*])\s*$")  # incluye «- » de una viñeta


@dataclass
class Sugerencia:
    id: str
    tipo: str  # redaccion, estructura o palabra_clave
    fragmento_original: str | None
    propuesta: str
    justificacion: str
    origen: str  # modelo o programa
    estado: str = "aprobada"  # aprobada, descartada o requiere_confirmacion
    motivos: list[str] = field(default_factory=list)
    entidades_nuevas: list[str] = field(default_factory=list)
    #: cambios de contenido de una reescritura: palabras que no están en el fragmento o el atenuante quitado
    palabras_nuevas: list[str] = field(default_factory=list)
    ubicacion: tuple[int, int] | None = None

    @property
    def aplicable(self) -> bool:
        """Solo las de redacción aprobadas se pueden aplicar al texto (reemplazan su fragmento)."""
        return self.tipo == "redaccion" and self.estado == "aprobada" and self.ubicacion is not None

    def a_dict(self) -> dict[str, Any]:
        return {"id": self.id, "tipo": self.tipo, "fragmento_original": self.fragmento_original,
                "propuesta": self.propuesta, "justificacion": self.justificacion, "origen": self.origen,
                "estado": self.estado, "motivos": list(self.motivos), "entidades_nuevas": list(self.entidades_nuevas),
                "palabras_nuevas": list(self.palabras_nuevas),
                "ubicacion": list(self.ubicacion) if self.ubicacion else None, "aplicable": self.aplicable}


def _numero_normalizado(numero: str) -> str:
    return re.sub(r"[.,](?=\d{3}(?!\d))", "", numero)  # 1.500 → 1500; 1,5 se conserva


def numeros(texto: str) -> set[str]:
    return {_numero_normalizado(n) for n in _NUMERO.findall(_MARCAS.sub(" ", texto))}


def nombres_propios(texto: str) -> list[str]:
    """Palabras con mayúscula inicial que no empiezan una frase, y siglas en cualquier posición."""
    limpio = _MARCAS.sub(" ", texto)
    encontrados = []
    for coincidencia in _PALABRA_MAYUSCULA.finditer(limpio):
        palabra = coincidencia.group(1).rstrip(".-")
        if len(palabra) < 2:
            continue
        sigla = sum(c.isupper() for c in palabra) >= 2
        if sigla or not _INICIO_FRASE.search(limpio[:coincidencia.start()]):
            encontrados.append(palabra)
    return encontrados


def entidades_nuevas(propuesta: str, texto_original_norm: str, numeros_originales: set[str],
                     diccionario: Diccionario) -> list[str]:
    """Entidades de la propuesta que no están en el texto original."""
    nuevas: list[str] = []

    def agregar(valor: str) -> None:
        if valor not in nuevas:
            nuevas.append(valor)

    de_extension = {_numero_normalizado(n) for par in _CIFRA_DE_EXTENSION.findall(propuesta) for n in par if n}
    for numero in sorted(numeros(propuesta) - numeros_originales - de_extension):
        agregar(numero)
    propuesta_norm = normalizar(_MARCAS.sub(" ", propuesta))
    for habilidad, _ in diccionario.detectar(propuesta_norm):
        if not any(contiene(texto_original_norm, t) for t in habilidad.terminos):
            agregar(habilidad.canonico)
    for palabra in nombres_propios(propuesta):
        if (normalizar(palabra) not in TERMINOS_DOCUMENTO and not contiene(texto_original_norm, palabra)
                and diccionario.buscar(palabra) is None):
            agregar(palabra)
    for termino in TERMINOS_CARGO:
        if contiene(propuesta_norm, termino) and not contiene(texto_original_norm, termino):
            agregar(termino)
    palabras_originales = set(re.findall(r"[a-zñ]+", texto_original_norm))
    for palabra in re.findall(r"[a-zñ]+", propuesta_norm):
        raiz = next((r for r in RAICES_RESPONSABILIDAD if palabra.startswith(r)), None)
        if raiz and not any(p.startswith(raiz) for p in palabras_originales):
            agregar(palabra)
    return nuevas


def _palabras(texto: str) -> list[str]:
    return re.findall(r"[a-zñ]+", normalizar(_MARCAS.sub(" ", texto)))


def cambios_de_contenido(propuesta: str, fragmento: str) -> tuple[list[str], str | None]:
    """(palabras con contenido de la propuesta que no están en el fragmento, atenuante del fragmento que la
    propuesta quita). Dos palabras son la misma si comparten las LARGO_RAIZ primeras letras."""
    originales, nuevas = _palabras(fragmento), _palabras(propuesta)
    raices = {p[:LARGO_RAIZ] for p in originales if len(p) >= LARGO_RAIZ}
    atenuantes = [p for p in originales if p.startswith(RAICES_ATENUANTES)]
    palabras: list[str] = []
    for palabra in nuevas:
        if (len(palabra) < LARGO_RAIZ or palabra in PALABRAS_DE_ENLACE or palabra[:LARGO_RAIZ] in raices
                or (atenuantes and palabra.startswith(RAICES_ATENUANTES)) or palabra in palabras):
            continue
        palabras.append(palabra)
    quitado = atenuantes[0] if atenuantes and not any(p.startswith(RAICES_ATENUANTES) for p in nuevas) else None
    return palabras, quitado


def revisar(crudas: list[dict[str, Any]], texto: str, *, diccionario: Diccionario, origen: str = "modelo",
            primer_numero: int = 1) -> list[Sugerencia]:
    """Aplica la guardia a las sugerencias del modelo (o del programa)."""
    normalizado = normalizar_con_mapa(texto)
    originales = numeros(texto)
    revisadas = []
    for numero, cruda in enumerate(crudas, start=primer_numero):
        sugerencia = Sugerencia(f"S{numero}", cruda["tipo"], (cruda.get("fragmento_original") or "").strip() or None,
                                cruda["propuesta"].strip(), (cruda.get("justificacion") or "").strip(), origen)
        if sugerencia.tipo == "palabra_clave":
            sugerencia.estado = "requiere_confirmacion"
            sugerencia.motivos.append("agrégala solo si realmente la tienes: nunca se inserta automáticamente")
            revisadas.append(sugerencia)
            continue
        if sugerencia.fragmento_original:
            ubicacion = ubicar_fragmento(sugerencia.fragmento_original, texto, umbral=UMBRAL_FRAGMENTO_MEJORA,
                                         normalizado=normalizado)
            if ubicacion:
                sugerencia.ubicacion = (ubicacion.inicio, ubicacion.fin)
            elif sugerencia.tipo == "redaccion":
                sugerencia.estado = "descartada"
                sugerencia.motivos.append("el fragmento original no está en la hoja de vida")
            else:
                sugerencia.motivos.append("el fragmento citado no está en la hoja de vida: se muestra sin cita")
                sugerencia.fragmento_original = None
        elif sugerencia.tipo == "redaccion":
            sugerencia.estado = "descartada"
            sugerencia.motivos.append("una sugerencia de redacción necesita el fragmento original")
        if (sugerencia.tipo == "redaccion" and sugerencia.fragmento_original
                and normalizar(sugerencia.fragmento_original) == normalizar(sugerencia.propuesta)):
            sugerencia.estado = "descartada"
            sugerencia.motivos.append("la propuesta no cambia el fragmento")
        sugerencia.entidades_nuevas = entidades_nuevas(sugerencia.propuesta, normalizado[0], originales, diccionario)
        if sugerencia.entidades_nuevas:
            sugerencia.estado = "descartada"
            sugerencia.motivos.append("agrega información que no está en la hoja de vida: "
                                      + ", ".join(f"«{e}»" for e in sugerencia.entidades_nuevas))
        if sugerencia.tipo == "redaccion" and sugerencia.ubicacion:
            palabras, quitado = cambios_de_contenido(sugerencia.propuesta, texto[slice(*sugerencia.ubicacion)])
            nuevas = [p for p in palabras if p not in {normalizar(e) for e in sugerencia.entidades_nuevas}]
            if nuevas:
                sugerencia.estado = "descartada"
                sugerencia.motivos.append("usa palabras que no están en el texto original y podrían afirmar algo "
                                          "nuevo: " + ", ".join(f"«{p}»" for p in nuevas))
            if quitado:
                sugerencia.estado = "descartada"
                sugerencia.motivos.append(f"quita que fue un apoyo o una participación («{quitado}»)")
            sugerencia.palabras_nuevas = nuevas + ([quitado] if quitado else [])
        revisadas.append(sugerencia)
    return revisadas


def resumen(sugerencias: list[Sugerencia]) -> dict[str, Any]:
    """Conteos y tasa de invención antes de la guardia (solo sugerencias del modelo)."""
    del_modelo = [s for s in sugerencias if s.origen == "modelo"]
    con_entidades = [s for s in del_modelo if s.entidades_nuevas]
    aprobadas = [s for s in del_modelo if s.estado == "aprobada"]
    return {
        "total": len(sugerencias),
        "aprobadas": sum(s.estado == "aprobada" for s in sugerencias),
        "descartadas": sum(s.estado == "descartada" for s in sugerencias),
        "requieren_confirmacion": sum(s.estado == "requiere_confirmacion" for s in sugerencias),
        "del_modelo": len(del_modelo),
        "con_informacion_nueva": len(con_entidades),
        "con_cambios_de_contenido": sum(bool(s.palabras_nuevas) and not s.entidades_nuevas for s in del_modelo),
        "tasa_invencion_antes": round(len(con_entidades) / len(del_modelo), 4) if del_modelo else None,
        "tasa_invencion_despues": (round(sum(bool(s.entidades_nuevas) for s in aprobadas) / len(aprobadas), 4)
                                   if aprobadas else None),
    }
