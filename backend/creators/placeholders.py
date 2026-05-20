"""Creators que existen en el hub pero todavía no tienen config completa.
Se muestran como tarjetas en el hub pero al entrar avisan de que falta config."""

GENERIC_PLACEHOLDER_PROMPT = """Eres un agente de producción para un canal de YouTube. Vas a recibir la transcripción de un vídeo y debes generar el PAQUETE COMPLETO.

⚠️ ESTE CREATOR TODAVÍA NO TIENE CONFIG ESPECÍFICA. Pablo aún no ha pasado las indicaciones del canal (voz de marca, libros, líneas rojas, plantillas de miniatura). Cuando las pase, este prompt se sustituirá.

Mientras tanto, devuelve EXCLUSIVAMENTE un JSON con la siguiente estructura genérica:

{
  "title": "string · 60-65 caracteres",
  "alternatives": ["alt1", "alt2", "alt3"],
  "description": "string · descripción YouTube",
  "chapters": "string · uno por línea formato 'MM:SS Título'",
  "tags": "string · 20-25 tags separados por coma",
  "pinned_comment": "string · 3-5 líneas terminando en pregunta",
  "thumb_template": 1,
  "thumb_textA": "TEXTO A",
  "thumb_textB": "TEXTO B",
  "thumb_prompt": "string · prompt en inglés para image-gen",
  "midform": [{"title":"","in":"00:00","out":"00:00","phrase_in":"","phrase_out":"","duration":"00:00","burn_text":""}],
  "shorts": [{"title":"","in":"00:00","out":"00:00","phrase_in":"","duration":"00:00","burn_text":"","why_works":""}],
  "alerts": []
}
"""

GENERIC_USER_TEMPLATE = """METADATOS DEL VÍDEO:
- Código: {code}
- Título de trabajo: {title}
- Tipo: {type}
- Duración bruto: {duration}

TRANSCRIPCIÓN:

{transcript}

Devuelve JSON puro.
"""

DEFAULT_CHECKLIST = [
    {"key": "revisar_paquete", "phase": "Pre-producción", "label": "Revisar PAQUETE"},
    {"key": "miniatura_a", "phase": "Producción", "label": "Miniatura A"},
    {"key": "miniatura_b", "phase": "Producción", "label": "Miniatura B"},
    {"key": "enviar_editor", "phase": "Producción", "label": "Enviar a editor"},
    {"key": "subir_largo", "phase": "Publicación", "label": "Subir largo"},
    {"key": "programar_publicacion", "phase": "Publicación", "label": "Programar publicación"},
]


def _placeholder(slug: str, name: str, initials: str, subtitle: str = "",
                 primary: str = "#1F3A4C", secondary: str = "#D4B97D") -> dict:
    return {
        "slug": slug,
        "name": name,
        "subtitle": subtitle or "Pendiente de configurar voz de marca",
        "avatar_initials": initials,
        "color_primary": primary,
        "color_secondary": secondary,
        "code_prefix": "V",
        "system_prompt": GENERIC_PLACEHOLDER_PROMPT,
        "user_template": GENERIC_USER_TEMPLATE,
        "checklist_template": DEFAULT_CHECKLIST,
        "config": {
            "thumb_templates": {1: "Plantilla por defecto"},
            "type_keywords": {},
            "publishing_schedule": {},
            "default_type": "historia",
            "title_min_chars": 50,
            "title_max_chars": 70,
            "is_placeholder": True,
        },
    }


PLACEHOLDERS = [
    # Vacío — los 4 creators iniciales ya tienen config completa
    # (marcelo_gullo, raices_europa, jose_ballesteros, gonzalo_rodriguez).
    # Si Pablo añade nuevos creators sin config aún, vuelven aquí.
]
