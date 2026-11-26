"""Núcleo de comunicación con los modelos de lenguaje (rescatado y adaptado del prototipo anterior).

- backends: tipos comunes (respuesta, errores, información del modelo).
- cliente_api: API compatibles con OpenAI (la app usa la de Gemini), con límites de uso y reintentos por 429/5xx.
- negociacion: parámetros que acepta cada API y opciones de cada solicitud según el esquema JSON.
- esquemas: validador mínimo de esquemas JSON.
- salida: pedir_json (respuesta JSON validada, con reintentos).
- modelo: ModeloLLM, que reúne el perfil, el backend y los parámetros negociados.
- prueba_conexion: comando que negocia y guarda los parámetros de un perfil.
"""
