"""Línea de comandos: «python -m analizador_cv <comando>».

Comandos:
- app [--puerto N] [--sin-navegador]: abre la interfaz web (Streamlit) en el navegador.
- leer ARCHIVO: extrae el texto de una hoja de vida (PDF, DOCX o TXT) y muestra el resultado.
- oferta URL | --html ARCHIVO | --texto ARCHIVO: obtiene una oferta laboral.
- analizar ARCHIVO [--oferta URL | --oferta-texto ARCHIVO | --oferta-html ARCHIVO]: analiza la hoja de
  vida (puntuación, tags, temporalidad, compatibilidad y mejoras); con --mejorar aplica las mejoras
  aprobadas, reanaliza y compara antes y después.
- evaluar (--linea-base | --simulado | --perfil P) [--diseno D]: evaluación técnica sobre el corpus sintético
  (Etapa 8); reanudable. Con --solo-informe recalcula las tablas, las figuras y el resumen; con --urls LISTA
  mide la extracción de ofertas por URL (P4).
- prueba-conexion [--perfil P]: comprueba la clave y el modelo y guarda los parámetros que acepta la API.
- perfiles: muestra los perfiles de modelo y si están listos para usarse.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from analizador_cv import __version__, bd
from analizador_cv.analisis.comparacion import aplicar_mejoras, comparar
from analizador_cv.analisis.fechas import Mes
from analizador_cv.analisis.motor import Analizador, ErrorAnalisis, RequisitosOferta, analizar_y_guardar
from analizador_cv.analisis.plantillas import ErrorPlantilla
from analizador_cv.analisis.resumen import texto_comparacion, texto_resultado
from analizador_cv.configuracion import (
    RAIZ_PROYECTO,
    Configuracion,
    ErrorConfiguracion,
    cargar_archivo_env,
    cargar_configuracion,
    clave_definida,
)
from analizador_cv.datos_personales import enmascarar, restaurar
from analizador_cv.documentos import Documento, ErrorDocumento, leer_archivo
from analizador_cv.llm.backends import ErrorBackend
from analizador_cv.llm.modelo import ModeloLLM
from analizador_cv.llm.negociacion import ErrorCongelado, NegociacionFallida, cargar_congelado
from analizador_cv.llm.prueba_conexion import prueba_conexion
from analizador_cv.ofertas import Oferta, desde_html, desde_texto, obtener_oferta
from analizador_cv.utilidades import cantidad, escribir_json, preparar_consola

VISTA_PREVIA = 1500


def _mostrar_texto(texto: str, completo: bool) -> None:
    if completo or len(texto) <= VISTA_PREVIA:
        print("--- Texto ---")
        print(texto)
    else:
        print(f"--- Texto (primeros {VISTA_PREVIA} de {len(texto)} caracteres; use --completo para verlo todo) ---")
        print(texto[:VISTA_PREVIA] + " …")


def _cmd_leer(cfg: Configuracion, args: argparse.Namespace) -> int:
    documento = leer_archivo(args.archivo, **cfg.documentos)
    print(f"Archivo: {documento.nombre} | formato: {documento.formato.upper()} | "
          f"páginas: {documento.paginas or '-'} | método: {documento.metodo}")
    print(f"Texto: {documento.caracteres} caracteres, {documento.palabras} palabras | hash: {documento.hash_texto[:12]}…")
    for advertencia in documento.advertencias:
        print(f"ADVERTENCIA: {advertencia}")
    enmascarado = False
    if args.enmascarar:
        resultado = enmascarar(documento.texto, nombres=args.nombre or [])
        documento.texto, enmascarado = resultado.texto, True
        detalle = ", ".join(f"{c} {n}" for c, n in resultado.conteo.items() if n) or "ninguno"
        print(f"Datos personales enmascarados: {resultado.total} ({detalle})")
    _mostrar_texto(documento.texto, args.completo)
    if args.guardar:
        bd.inicializar(cfg.ruta_bd, cfg.ruta_respaldos)
        with bd.abrir(cfg.ruta_bd) as conexion:
            identificador = bd.guardar_documento(conexion, documento, enmascarado=enmascarado)
        print(f"Guardado en el historial ({cfg.ruta_bd.name}) con el id {identificador}.")
    return 0


def _cmd_oferta(cfg: Configuracion, args: argparse.Namespace) -> int:
    if args.html:
        oferta = desde_html(Path(args.html).read_bytes(), cfg.ofertas, url=args.url)
    elif args.texto:
        oferta = desde_texto(Path(args.texto).read_text(encoding="utf-8-sig"))
    elif args.url:
        oferta = obtener_oferta(args.url, cfg.ofertas)
    else:
        print("ERROR: indique la URL de la oferta, --html ARCHIVO o --texto ARCHIVO.", file=sys.stderr)
        return 1
    print(f"Estado: {oferta.estado} | origen: {oferta.origen} | método: {oferta.metodo or '-'}"
          + (f" | HTTP {oferta.http_estado}" if oferta.http_estado else "")
          + (f" | redirecciones: {oferta.redirecciones}" if oferta.redirecciones else ""))
    for etiqueta, valor in (("URL", oferta.url_final or oferta.url), ("Portal", oferta.portal), ("Título", oferta.titulo),
                            ("Empresa", oferta.empresa), ("Ubicación", oferta.ubicacion)):
        if valor:
            print(f"{etiqueta}: {valor}")
    print(oferta.mensaje)
    if oferta.texto and oferta.exito:
        _mostrar_texto(oferta.texto, args.completo)
    if args.guardar:
        bd.inicializar(cfg.ruta_bd, cfg.ruta_respaldos)
        with bd.abrir(cfg.ruta_bd) as conexion:
            identificador = bd.guardar_oferta(conexion, oferta)
        print(f"Guardada en el historial ({cfg.ruta_bd.name}) con el id {identificador}.")
    return 0 if oferta.exito else 2


def _leer_oferta(cfg: Configuracion, args: argparse.Namespace) -> Oferta | None:
    if args.oferta_html:
        return desde_html(Path(args.oferta_html).read_bytes(), cfg.ofertas, url=args.oferta)
    if args.oferta_texto:
        return desde_texto(Path(args.oferta_texto).read_text(encoding="utf-8-sig"))
    if args.oferta:
        return obtener_oferta(args.oferta, cfg.ofertas)
    return None


def _cmd_analizar(cfg: Configuracion, args: argparse.Namespace) -> int:
    documento = leer_archivo(args.archivo, **cfg.documentos)
    print(f"Hoja de vida: {documento.nombre} ({documento.formato.upper()}, {documento.palabras} palabras)")
    for advertencia in documento.advertencias:
        print(f"ADVERTENCIA: {advertencia}")
    equivalencias: dict[str, str] = {}
    if args.enmascarar:
        enmascarado = enmascarar(documento.texto, nombres=args.nombre or [])
        documento.texto, equivalencias = enmascarado.texto, enmascarado.equivalencias
        print(f"Datos personales enmascarados antes del análisis: {enmascarado.total}")
    oferta = _leer_oferta(cfg, args)
    if oferta is not None:
        print(f"Oferta: {oferta.mensaje}")
        if not oferta.exito:
            print("En la línea de comandos: guarde la descripción en un archivo .txt y use --oferta-texto ARCHIVO.")
            return 2
    try:
        referencia = Mes.desde_texto(args.fecha_referencia) if args.fecha_referencia else None
    except ValueError as error:
        raise ErrorConfiguracion(str(error)) from error

    modelo = None
    if not args.linea_base:
        modelo = ModeloLLM(cfg, args.perfil)
        if modelo.perfil.get("aviso_privacidad"):
            print(f"Privacidad: {modelo.perfil['aviso_privacidad']}")
        if not args.enmascarar:
            print("Si es una hoja de vida real, use --enmascarar (y --nombre) para no enviar sus datos personales.")
    analizador = Analizador(cfg, modelo, referencia=referencia)
    print("Analizando…" if modelo is None else f"Analizando con {modelo.nombre} ({modelo.perfil['modelo']})…")
    con_mejoras = not args.sin_mejoras

    analisis_id = documento_id = None
    if args.guardar:
        bd.inicializar(cfg.ruta_bd, cfg.ruta_respaldos)
        with bd.abrir(cfg.ruta_bd) as conexion:
            analisis_id, documento_id, resultado = analizar_y_guardar(
                analizador, conexion, documento, enmascarado=bool(args.enmascarar), oferta=oferta, con_mejoras=con_mejoras)
    else:
        resultado = analizador.analizar(documento.texto, oferta=oferta.texto if oferta else None, con_mejoras=con_mejoras,
                                        advertencias=documento.advertencias)
    print()
    print(texto_resultado(resultado))
    if analisis_id is not None:
        print(f"Guardado en el historial ({cfg.ruta_bd.name}) como el análisis {analisis_id}.")
    if args.json:
        escribir_json(Path(args.json), resultado)
        print(f"Resultado completo en {args.json}.")

    if args.mejorar:
        mejorado, aplicadas, no_aplicadas = aplicar_mejoras(documento.texto, resultado)
        if not aplicadas:
            print("\nNo hay sugerencias de redacción aprobadas para aplicar: no se genera la versión mejorada.")
            return 0
        ruta = Path(args.mejorar)
        ruta.write_text(restaurar(mejorado, equivalencias), encoding="utf-8")
        print(f"\nVersión mejorada con {cantidad(len(aplicadas), 'sugerencia', 'sugerencias')} ({', '.join(aplicadas)}) "
              f"en {ruta}"
              + (" (con los datos personales restaurados)" if equivalencias else "") + ".")
        for item in no_aplicadas:
            print(f"  No se aplicó {item['id']}: {item['motivo']}")
        requisitos = (RequisitosOferta.desde_dict(resultado["compatibilidad"]["oferta"])
                      if resultado["compatibilidad"] else None)
        version = Documento(ruta.name, "editado", mejorado, "mejoras_aplicadas")
        if args.guardar:
            with bd.abrir(cfg.ruta_bd) as conexion:
                _, _, despues = analizar_y_guardar(analizador, conexion, version, enmascarado=bool(args.enmascarar),
                                                   oferta=oferta, requisitos=requisitos, analisis_previo_id=analisis_id,
                                                   origen_id=documento_id, con_mejoras=False)
        else:
            despues = analizador.analizar(mejorado, oferta=oferta.texto if oferta else None, requisitos=requisitos,
                                          con_mejoras=False)
        print()
        print(texto_comparacion(comparar(resultado, despues)))
    return 0


def esperar_app(url: str, proceso: subprocess.Popen | None = None, tiempo_max_s: float = 90) -> bool:
    """Espera a que el servidor de Streamlit responda en /_stcore/health (o a que el proceso termine)."""
    abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    limite = time.time() + tiempo_max_s
    while time.time() < limite and (proceso is None or proceso.poll() is None):
        try:
            with abridor.open(f"{url}/_stcore/health", timeout=3) as respuesta:
                if respuesta.status == 200:
                    return True
        except OSError:
            time.sleep(0.5)
    return False


def _cmd_app(cfg: Configuracion, args: argparse.Namespace) -> int:
    ruta = Path(__file__).resolve().parent / "app.py"
    comando = [sys.executable, "-m", "streamlit", "run", str(ruta), "--server.port", str(args.puerto),
               "--server.address", "localhost", "--server.headless", "true", "--browser.gatherUsageStats", "false"]
    # La app lee la configuración y la base de estas variables; .streamlit/config.toml está en la raíz del prototipo.
    entorno = {**os.environ, "ANALIZADOR_CV_CONFIG": str(cfg.ruta_archivo), "ANALIZADOR_CV_BD": str(cfg.ruta_bd)}
    url = f"http://localhost:{args.puerto}"
    print(f"Iniciando la app en {url} … (ciérrela con Ctrl+C en esta ventana)")
    proceso = subprocess.Popen(comando, cwd=str(RAIZ_PROYECTO), env=entorno)
    try:
        if esperar_app(url, proceso):
            print(f"App disponible en {url}")
            if not args.sin_navegador:
                webbrowser.open(url)
        elif proceso.poll() is not None:
            print(f"La app se cerró al iniciar. Si el puerto {args.puerto} está ocupado, use --puerto con otro número.")
        else:
            print("La app no respondió a tiempo; revise los mensajes de Streamlit arriba.")
        return proceso.wait()
    except KeyboardInterrupt:
        print("\nCerrando la app…")
        proceso.terminate()
        try:
            proceso.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proceso.kill()
        return 0


def _cmd_evaluar(cfg: Configuracion, args: argparse.Namespace) -> int:
    from analizador_cv.evaluacion.corpus import cargar_corpus
    from analizador_cv.evaluacion.ejecucion import Corrida
    from analizador_cv.evaluacion.informe import escribir_informe

    from analizador_cv.evaluacion.urls import guardar_medicion, leer_lista, medir_urls

    corpus = cargar_corpus(args.corpus)
    carpeta = Path(args.salida) if args.salida else cfg.ruta_resultados / "evaluacion"
    metodo = "linea_base" if args.linea_base else "simulado" if args.simulado else args.perfil
    codigo = 0
    if args.urls:
        urls = leer_lista(args.urls)
        print(f"Extracción por URL (P4): {len(urls)} enlaces, una petición por enlace (más robots.txt).")
        print(f"Resultado en {guardar_medicion(medir_urls(urls, cfg.ofertas), carpeta)} (sin el texto de las ofertas).")
    if not args.solo_informe and (metodo or not args.urls):
        if metodo is None:
            print("ERROR: indique --linea-base, --simulado o --perfil PERFIL (o --solo-informe o --urls).", file=sys.stderr)
            return 1
        if metodo not in ("linea_base", "simulado"):
            print(f"Corrida REAL con el perfil «{metodo}»: gasta cuota de la API. Se puede interrumpir y reanudar.")
        diseno = args.diseno or ("completo" if metodo in ("linea_base", "simulado") else "nucleo")
        particion = None
        if args.particion:
            coincidencia = re.fullmatch(r"(\d+)/(\d+)", args.particion.strip())
            if not coincidencia:
                raise ErrorConfiguracion(f"--particion debe ser K/N, por ejemplo 1/4 (se recibió «{args.particion}»).")
            particion = (int(coincidencia.group(1)), int(coincidencia.group(2)))
        try:
            corrida = Corrida(cfg, corpus, metodo, carpeta=carpeta / (args.corrida or metodo), diseno=diseno,
                              particion=particion)
            estado = corrida.ejecutar(limite_tokens=args.limite_tokens, reintentar_fallidas=args.reintentar_fallidas)
        except ValueError as error:
            raise ErrorConfiguracion(str(error)) from error
        print(f"Estado: {estado['estado']} ({estado['hechas']} de {estado['unidades']} unidades; {estado['nuevas']} nuevas en "
              f"esta ejecución, {estado['fallidas']} fallidas, {estado['omitidas']} omitidas; "
              f"{estado['tokens_nuevos']} tokens nuevos).")
        codigo = 0 if estado["estado"] == "completa" else 3
        if particion:
            print("Parte terminada. Cuando terminen todas, corra el mismo comando sin --particion: completa lo que "
                  "falte, registra el estado de la corrida y arma el informe.")
            return codigo
    archivos = escribir_informe(corpus, carpeta, figuras=not args.sin_figuras)
    print(f"Informe en {carpeta / 'informe'} ({len(archivos)} archivos; empiece por resumen.md).")
    return codigo


def _cmd_prueba_conexion(cfg: Configuracion, args: argparse.Namespace) -> int:
    resultado = prueba_conexion(cfg, args.perfil)
    return 0 if resultado.get("salida_valida") else 2


def _cmd_perfiles(cfg: Configuracion, args: argparse.Namespace) -> int:
    predeterminado = cfg.datos["perfil_predeterminado"]
    for nombre in cfg.nombres_perfiles:
        perfil = cfg.perfil(nombre)
        partes = [f"{nombre}{' (predeterminado)' if nombre == predeterminado else ''}",
                  f"{perfil['proveedor']} · {perfil['modelo']}"]
        if perfil["tipo"] == "openai_compatible":
            partes.append(f"clave {perfil['variable_clave']}: {'definida' if clave_definida(perfil) else 'FALTA'}")
            try:
                congelado = cargar_congelado(cfg, perfil)
                partes.append(f"prueba de conexión: {congelado['fecha'][:10]} ({congelado['modo_salida']})")
            except ErrorCongelado as error:
                partes.append("prueba de conexión: " + ("FALTA" if "Falta" in str(error) else "DESACTUALIZADA"))
        else:
            partes.append("local, sin clave")
        print(" | ".join(partes))
    return 0


def _crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m analizador_cv",
                                     description="Analizador CV: análisis y optimización de hojas de vida con IA.")
    parser.add_argument("--version", action="version", version=f"analizador_cv {__version__}")
    parser.add_argument("--config", help="ruta de configuracion.json (predeterminada: la de la carpeta del prototipo)")
    parser.add_argument("--bd", help="ruta de la base SQLite del historial")
    comandos = parser.add_subparsers(dest="comando", metavar="comando")

    app = comandos.add_parser("app", help="abre la interfaz web en el navegador")
    app.add_argument("--puerto", type=int, default=8501, help="puerto local (predeterminado: 8501)")
    app.add_argument("--sin-navegador", action="store_true", help="no abre el navegador")
    app.set_defaults(funcion=_cmd_app)

    leer = comandos.add_parser("leer", help="extrae el texto de una hoja de vida (PDF, DOCX o TXT)")
    leer.add_argument("archivo")
    leer.add_argument("--enmascarar", action="store_true", help="enmascara los datos personales")
    leer.add_argument("--nombre", action="append", help="nombre que se enmascara (se puede repetir)")
    leer.add_argument("--completo", action="store_true", help="muestra todo el texto")
    leer.add_argument("--guardar", action="store_true", help="guarda el documento en el historial")
    leer.set_defaults(funcion=_cmd_leer)

    oferta = comandos.add_parser("oferta", help="obtiene una oferta por URL, HTML guardado o texto")
    oferta.add_argument("url", nargs="?", help="dirección de la oferta")
    oferta.add_argument("--html", help="página HTML guardada (sin red)")
    oferta.add_argument("--texto", help="archivo de texto con la oferta")
    oferta.add_argument("--completo", action="store_true", help="muestra todo el texto")
    oferta.add_argument("--guardar", action="store_true", help="guarda la oferta en el historial")
    oferta.set_defaults(funcion=_cmd_oferta)

    analizar = comandos.add_parser("analizar", help="analiza una hoja de vida (general o frente a una oferta)")
    analizar.add_argument("archivo", help="hoja de vida en PDF, DOCX o TXT")
    analizar.add_argument("--oferta", help="URL de la oferta (modo específico)")
    analizar.add_argument("--oferta-texto", help="archivo de texto con la oferta (modo específico)")
    analizar.add_argument("--oferta-html", help="página HTML guardada de la oferta (modo específico)")
    analizar.add_argument("--perfil", help="perfil de modelo de configuracion.json (predeterminado: el de la configuración)")
    analizar.add_argument("--linea-base", action="store_true",
                          help="sin modelo de lenguaje: línea base por palabras clave (modo demostración)")
    analizar.add_argument("--sin-mejoras", action="store_true", help="no pide sugerencias de mejora (una llamada menos)")
    analizar.add_argument("--enmascarar", action="store_true", help="enmascara los datos personales antes de analizar")
    analizar.add_argument("--nombre", action="append", help="nombre que se enmascara (se puede repetir)")
    analizar.add_argument("--fecha-referencia", help="mes de referencia AAAA-MM para la recencia (predeterminado: el actual)")
    analizar.add_argument("--json", help="guarda el resultado completo en este archivo JSON")
    analizar.add_argument("--mejorar", metavar="SALIDA.txt",
                          help="aplica las mejoras de redacción aprobadas, guarda el texto y compara antes y después")
    analizar.add_argument("--guardar", action="store_true", help="guarda el análisis y sus llamadas en el historial")
    analizar.set_defaults(funcion=_cmd_analizar)

    evaluar = comandos.add_parser("evaluar", help="evaluación técnica sobre el corpus sintético (Etapa 8)")
    metodo = evaluar.add_mutually_exclusive_group()
    metodo.add_argument("--linea-base", action="store_true", help="sin modelo: línea base por palabras clave")
    metodo.add_argument("--simulado", action="store_true", help="modelo simulado, sin red (verificación y estimación de cuota)")
    metodo.add_argument("--perfil", help="perfil de modelo de configuracion.json: corrida REAL (gasta cuota)")
    evaluar.add_argument("--diseno", choices=("prueba", "nucleo", "completo"),
                         help="unidades a correr (predeterminado: completo sin red; nucleo con un perfil real)")
    evaluar.add_argument("--corrida", help="nombre de la carpeta de la corrida (predeterminado: el del método)")
    evaluar.add_argument("--limite-tokens", type=int, help="se detiene al gastar estos tokens nuevos en esta ejecución")
    evaluar.add_argument("--particion", metavar="K/N",
                         help="corre solo la parte K de N (varios procesos a la vez con un proveedor lento); "
                              "al final, el mismo comando sin --particion completa la corrida y arma el informe")
    evaluar.add_argument("--reintentar-fallidas", action="store_true", help="vuelve a correr las unidades fallidas")
    evaluar.add_argument("--solo-informe", action="store_true", help="no corre nada: recalcula tablas, figuras y resumen")
    evaluar.add_argument("--urls", metavar="LISTA.txt",
                         help="mide la extracción de ofertas por URL (P4): una URL por renglón; no guarda el texto")
    evaluar.add_argument("--sin-figuras", action="store_true", help="no dibuja las figuras PNG")
    evaluar.add_argument("--corpus", help="carpeta del corpus (predeterminada: datos/corpus)")
    evaluar.add_argument("--salida", help="carpeta de la evaluación (predeterminada: resultados/evaluacion)")
    evaluar.set_defaults(funcion=_cmd_evaluar)

    prueba = comandos.add_parser("prueba-conexion", help="comprueba la clave y negocia los parámetros con la API")
    prueba.add_argument("--perfil", help="perfil de configuracion.json (predeterminado: el de la configuración)")
    prueba.set_defaults(funcion=_cmd_prueba_conexion)

    perfiles = comandos.add_parser("perfiles", help="muestra los perfiles de modelo y su estado")
    perfiles.set_defaults(funcion=_cmd_perfiles)
    return parser


def main(argv: list[str] | None = None) -> int:
    preparar_consola()
    parser = _crear_parser()
    args = parser.parse_args(argv)
    if not args.comando:
        parser.print_help()
        return 0
    cargar_archivo_env()
    try:
        cfg = cargar_configuracion(args.config, bd=args.bd)
        return int(args.funcion(cfg, args))
    except ErrorBackend as error:
        print(f"ERROR ({error.tipo}): {error.mensaje}", file=sys.stderr)
    except ErrorAnalisis as error:
        print(f"ERROR: el análisis no se completó y no tiene puntaje. {error}", file=sys.stderr)
    except (ErrorConfiguracion, ErrorDocumento, NegociacionFallida, bd.ErrorBaseDatos, ErrorPlantilla) as error:
        print(f"ERROR: {error}", file=sys.stderr)
    except FileNotFoundError as error:
        print(f"ERROR: no se encontró el archivo {error.filename}", file=sys.stderr)
    except KeyboardInterrupt:
        print("Interrumpido.", file=sys.stderr)
        return 130
    return 1
