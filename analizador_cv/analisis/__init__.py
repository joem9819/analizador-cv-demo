"""Motor de análisis de hojas de vida (Etapa 6; definición del proyecto, sección 6).

Principio: el modelo extrae, clasifica y cita evidencia; el código verifica esa evidencia contra el
texto original y calcula todos los puntajes con fórmulas fijas. El modelo nunca da el porcentaje.

- cotejo: normalización de textos y búsqueda de términos y fragmentos en el original.
- fechas: lectura de fechas y duraciones, periodos y meses acumulados (fusiona solapamientos).
- habilidades: diccionario propio de habilidades y sinónimos (datos/habilidades.json).
- secciones: detección determinista de las secciones de la hoja de vida.
- esquemas: esquema JSON y validador de cada etapa que usa el modelo.
- plantillas: mensajes versionados al modelo (datos/plantillas/).
- extraccion: verificación de lo extraído por el modelo y armado de experiencias, proyectos y formación.
- temporalidad: tags con primer y último uso, meses, relevancia e impacto (§6.3 y §6.4).
- compatibilidad: requisitos de la oferta y su cobertura (§6.2).
- puntuacion: rúbrica de 100 puntos con desglose (§6.1).
- guardia: guardia contra la información inventada en las mejoras (§6.5).
- linea_base: análisis sin modelo, por palabras clave (modo demostración y comparación).
- motor: orquesta las etapas y arma el resultado.
- comparacion: aplica las mejoras aceptadas y compara antes y después.
"""
