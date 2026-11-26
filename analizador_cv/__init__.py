"""Prototipo «Analizador CV».

Aplicación con IA que analiza y optimiza hojas de vida en PDF, DOCX y TXT: extrae habilidades,
experiencia, formación y temporalidad con un modelo de lenguaje (Gemini 3.5 Flash-Lite, por su API;
sin modelo, la línea base por palabras clave en modo demostración), calcula la puntuación con fórmulas
fijas y sugiere mejoras sin inventar información. Puede comparar la hoja de vida con una oferta
obtenida por URL (solo en sitios que permiten el acceso automatizado) o pegada por el usuario.
"""

__version__ = "0.1.0"
