"""Verificación de lo extraído y armado de experiencias, proyectos y formación (RF02).

Cada dato que devuelve el modelo (o la línea base) se busca en el texto original:
- cargo, nombre del proyecto o título: si no aparece (parecido ≥ 85 %), el bloque se descarta;
- empresa o institución: si no aparece, se quita del bloque;
- actividades (viñetas): si no aparecen, se descartan;
- habilidades de un bloque: deben aparecer dentro del texto de ese bloque, que va desde su cargo
  hasta el siguiente bloque o encabezado de sección; si no, se quitan del bloque;
- habilidades de la lista general e idiomas: deben aparecer en algún lugar del texto;
- fechas: el año copiado debe estar en el texto, y «actual» solo vale si el bloque lo dice.
Todo lo descartado queda en «no_verificados» con su motivo, para el detalle técnico y la evaluación.

Complemento determinista (opcional, activo por defecto): las habilidades del diccionario que están
escritas en el texto de un bloque, o en cualquier parte de la hoja de vida, y que el modelo omitió se
agregan y quedan en «complementadas». No inventa nada (solo agrega lo que está literalmente) y hace
más estable la temporalidad entre repeticiones. La evaluación puede apagarlo para medir el modelo solo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from analizador_cv.analisis.cotejo import Ubicacion, normalizar, normalizar_con_mapa, ubicar_fragmento
from analizador_cv.analisis.fechas import Mes, Periodo, leer_periodo, menciona_actual
from analizador_cv.analisis.habilidades import Diccionario
from analizador_cv.analisis.secciones import Secciones

CLASES = ("experiencia", "proyecto", "formacion")
_CAMPOS = {"experiencia": ("experiencias", "cargo", "empresa"), "proyecto": ("proyectos", "nombre", None),
           "formacion": ("formacion", "titulo", "institucion")}
_ANIOS = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_MARCAS = re.compile(r"\[[A-Z_]+_\d+\]")


def es_cuantificada(texto: str) -> bool:
    """True si la actividad tiene una cifra (sin contar años ni las marcas de datos personales)."""
    limpio = _ANIOS.sub(" ", _MARCAS.sub(" ", texto))
    return bool(re.search(r"\d", limpio))


@dataclass
class Actividad:
    texto: str
    es_logro: bool

    @property
    def cuantificada(self) -> bool:
        return es_cuantificada(self.texto)


@dataclass
class Bloque:
    """Una experiencia, un proyecto o un estudio, ya verificado."""

    clase: str
    indice: int
    titulo: str
    entidad: str | None
    periodo: Periodo
    actividades: list[Actividad] = field(default_factory=list)
    habilidades: list[str] = field(default_factory=list)
    tipo: str | None = None  # formación: formal, curso o certificacion
    estado: str | None = None  # formación: completo, en_curso o sin_dato
    fechas_texto: dict[str, str | None] = field(default_factory=dict)

    @property
    def tiene_logro(self) -> bool:
        return any(a.es_logro for a in self.actividades)

    @property
    def etiqueta(self) -> str:
        return f"{self.titulo} ({self.entidad})" if self.entidad else self.titulo

    def a_dict(self) -> dict[str, Any]:
        return {"clase": self.clase, "indice": self.indice, "titulo": self.titulo, "entidad": self.entidad,
                "periodo": self.periodo.a_dict(), "fechas_texto": dict(self.fechas_texto),
                "actividades": [{"texto": a.texto, "es_logro": a.es_logro, "cuantificada": a.cuantificada}
                                for a in self.actividades],
                "habilidades": list(self.habilidades), "tipo": self.tipo, "estado": self.estado}


@dataclass
class Extraccion:
    perfil: str | None
    experiencias: list[Bloque]
    proyectos: list[Bloque]
    formacion: list[Bloque]
    habilidades: list[dict[str, str]]  # {"nombre", "tipo"}
    idiomas: list[dict[str, str | None]]
    no_verificados: list[dict[str, str]] = field(default_factory=list)
    elementos_extraidos: int = 0
    complementadas: list[dict[str, str]] = field(default_factory=list)

    @property
    def bloques(self) -> list[Bloque]:
        return self.experiencias + self.proyectos + self.formacion

    @property
    def tasa_verificacion(self) -> float | None:
        """Proporción de elementos extraídos que se encontraron en el texto."""
        if not self.elementos_extraidos:
            return None
        return round(1 - len(self.no_verificados) / self.elementos_extraidos, 4)

    def a_dict(self) -> dict[str, Any]:
        return {"perfil": self.perfil, "experiencias": [b.a_dict() for b in self.experiencias],
                "proyectos": [b.a_dict() for b in self.proyectos], "formacion": [b.a_dict() for b in self.formacion],
                "habilidades": [dict(h) for h in self.habilidades], "idiomas": [dict(i) for i in self.idiomas],
                "no_verificados": [dict(n) for n in self.no_verificados],
                "elementos_extraidos": self.elementos_extraidos, "tasa_verificacion": self.tasa_verificacion,
                "complementadas": [dict(c) for c in self.complementadas]}


class _Cotejo:
    """Texto original con su forma normalizada, para ubicar muchos fragmentos."""

    def __init__(self, texto: str):
        self.texto = texto
        self.normalizado = normalizar_con_mapa(texto)

    @property
    def norma(self) -> str:
        return self.normalizado[0]

    def ubicar(self, fragmento: str | None, umbral: float = 0.85) -> Ubicacion | None:
        return ubicar_fragmento(fragmento, self.texto, umbral=umbral, normalizado=self.normalizado)

    def ocurrencias(self, fragmento: str | None) -> list[int]:
        """Posiciones (en el original) de cada aparición literal (normalizada) del fragmento."""
        buscado = normalizar(fragmento).strip(" .;,:-")
        if not buscado:
            return []
        norma, mapa = self.normalizado
        return [mapa[m.start()] for m in re.finditer(re.escape(buscado), norma)]


def _verificar_fecha(valor: str | None, texto_norm: str, bloque_norm: str) -> str | None:
    """Devuelve el motivo si la fecha copiada no está respaldada por el texto; None si está bien."""
    if not valor:
        return None
    escrito = normalizar(valor)
    if menciona_actual(escrito):
        return None if menciona_actual(bloque_norm) else "el bloque no dice que siga vigente"
    for anio in _ANIOS.findall(escrito):
        if anio not in texto_norm:
            return f"el año {anio} no aparece en la hoja de vida"
    return None


def procesar_extraccion(datos: dict[str, Any], texto: str, *, referencia: Mes, diccionario: Diccionario,
                        secciones: Secciones, complementar: bool = True) -> Extraccion:
    """Verifica la extracción contra el texto y arma los bloques con sus periodos."""
    cotejo = _Cotejo(texto)
    no_verificados: list[dict[str, str]] = []
    complementadas: list[dict[str, str]] = []
    contador = 0

    def descartar(campo: str, valor: Any, motivo: str) -> None:
        no_verificados.append({"campo": campo, "valor": str(valor), "motivo": motivo})

    # 1. Ubicar cada bloque: primero sus actividades; luego el título más cercano antes de ellas.
    candidatos: list[dict[str, Any]] = []
    for clase in CLASES:
        lista_datos, campo_titulo, campo_entidad = _CAMPOS[clase]
        for indice, crudo in enumerate(datos.get(lista_datos) or []):
            ruta = f"{lista_datos}[{indice}]"
            contador += 1
            titulo = (crudo.get(campo_titulo) or "").strip()
            actividades = []
            for n, actividad in enumerate(crudo.get("actividades") or []):
                contador += 1
                ubicacion = cotejo.ubicar(actividad["texto"])
                if ubicacion is None:
                    descartar(f"{ruta}.actividades[{n}]", actividad["texto"], "no aparece en la hoja de vida")
                else:
                    actividades.append((actividad, ubicacion))
            primera = min((u.inicio for _, u in actividades), default=None)
            ocurrencias = cotejo.ocurrencias(titulo)
            if ocurrencias:
                antes = [p for p in ocurrencias if primera is None or p <= primera]
                inicio = antes[-1] if antes else ocurrencias[0]
            else:
                ubicacion = cotejo.ubicar(titulo)
                inicio = ubicacion.inicio if ubicacion else None
            if inicio is None:
                descartar(f"{ruta}.{campo_titulo}", titulo or "(vacío)", "no aparece en la hoja de vida: se descarta el bloque")
                continue
            candidatos.append({"clase": clase, "indice": indice, "ruta": ruta, "crudo": crudo, "titulo": titulo,
                               "campo_entidad": campo_entidad, "inicio": inicio, "actividades": actividades})

    # 2. Texto de cada bloque: hasta el siguiente bloque o encabezado de sección.
    cortes = sorted({c["inicio"] for c in candidatos} | set(secciones.inicios()) | {len(texto)})
    bloques: dict[str, list[Bloque]] = {clase: [] for clase in CLASES}
    for candidato in candidatos:
        inicio = candidato["inicio"]
        fin = next(c for c in cortes if c > inicio)
        fin = max([fin] + [u.fin for _, u in candidato["actividades"]])
        bloque_norm = normalizar(texto[inicio:fin])
        crudo, ruta = candidato["crudo"], candidato["ruta"]

        entidad = None
        if candidato["campo_entidad"]:
            entidad = (crudo.get(candidato["campo_entidad"]) or "").strip() or None
            if entidad:
                contador += 1
                if cotejo.ubicar(entidad) is None:
                    descartar(f"{ruta}.{candidato['campo_entidad']}", entidad, "no aparece en la hoja de vida")
                    entidad = None

        fechas = {"inicio": crudo.get("inicio"), "fin": crudo.get("fin"), "duracion": crudo.get("duracion")}
        for clave in ("inicio", "fin"):
            if fechas[clave]:
                contador += 1
                motivo = _verificar_fecha(fechas[clave], cotejo.norma, bloque_norm)
                if motivo:
                    descartar(f"{ruta}.{clave}", fechas[clave], motivo)
                    fechas[clave] = None
        if fechas["duracion"] and not re.search(r"\d", normalizar(fechas["duracion"])):
            fechas["duracion"] = None
        periodo = leer_periodo(fechas["inicio"], fechas["fin"], fechas["duracion"], referencia=referencia)

        habilidades: list[str] = []
        vistas: set[str] = set()
        for nombre in crudo.get("habilidades") or []:
            contador += 1
            nombre = nombre.strip()
            if not nombre:
                continue
            if not diccionario.en_texto(nombre, bloque_norm):
                descartar(f"{ruta}.habilidades", nombre, "no aparece en el texto de ese bloque")
                continue
            clave = diccionario.clave(nombre)
            if clave not in vistas:
                vistas.add(clave)
                habilidades.append(nombre)
        if complementar:
            for habilidad, _ in diccionario.detectar(bloque_norm):
                if normalizar(habilidad.canonico) not in vistas:
                    vistas.add(normalizar(habilidad.canonico))
                    habilidades.append(habilidad.canonico)
                    complementadas.append({"campo": f"{ruta}.habilidades", "valor": habilidad.canonico})

        actividades = [Actividad(a["texto"].strip(), a["tipo"] == "logro") for a, _ in candidato["actividades"]]
        bloques[candidato["clase"]].append(Bloque(
            candidato["clase"], candidato["indice"], candidato["titulo"], entidad, periodo, actividades, habilidades,
            tipo=crudo.get("tipo"), estado=crudo.get("estado"), fechas_texto=fechas))

    # 3. Lista general de habilidades e idiomas: deben aparecer en el texto.
    habilidades_generales: list[dict[str, str]] = []
    vistas = set()
    for n, habilidad in enumerate(datos.get("habilidades") or []):
        contador += 1
        nombre = habilidad["nombre"].strip()
        if not nombre or not diccionario.en_texto(nombre, cotejo.norma):
            descartar(f"habilidades[{n}]", nombre or "(vacío)", "no aparece en la hoja de vida")
            continue
        clave = diccionario.clave(nombre)
        if clave not in vistas:
            vistas.add(clave)
            encontrada = diccionario.buscar(nombre)
            habilidades_generales.append({"nombre": nombre, "tipo": encontrada.tipo if encontrada else habilidad["tipo"]})
    if complementar:
        for habilidad, _ in diccionario.detectar(cotejo.norma):
            if normalizar(habilidad.canonico) not in vistas:
                vistas.add(normalizar(habilidad.canonico))
                habilidades_generales.append({"nombre": habilidad.canonico, "tipo": habilidad.tipo})
                complementadas.append({"campo": "habilidades", "valor": habilidad.canonico})
    idiomas = []
    for n, idioma in enumerate(datos.get("idiomas") or []):
        contador += 1
        if diccionario.en_texto(idioma["idioma"], cotejo.norma):
            idiomas.append({"idioma": idioma["idioma"].strip(), "nivel": idioma.get("nivel")})
        else:
            descartar(f"idiomas[{n}]", idioma["idioma"], "no aparece en la hoja de vida")

    perfil = (datos.get("perfil") or "").strip() or None
    if perfil:
        contador += 1
        if cotejo.ubicar(perfil, umbral=0.8) is None:
            descartar("perfil", perfil[:120], "no aparece en la hoja de vida")
            perfil = None

    return Extraccion(perfil, bloques["experiencia"], bloques["proyecto"], bloques["formacion"], habilidades_generales,
                      idiomas, no_verificados, contador, complementadas)
