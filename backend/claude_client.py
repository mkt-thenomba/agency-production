"""Cliente Anthropic con reintentos y parsing robusto de JSON.
Recibe el system_prompt y el user_template del creator (multi-tenant)."""
import json
import logging
import os

from anthropic import Anthropic

logger = logging.getLogger(__name__)

REQUIRED_KEYS = [
    "title", "alternatives", "description", "chapters", "tags",
    "pinned_comment", "thumb_template", "thumb_textA", "thumb_textB",
    "thumb_prompt", "midform", "shorts", "alerts",
]


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text[3:]
        if text.startswith("json"):
            text = text[4:]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _extract_json(text: str) -> dict:
    text = _strip_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start == -1:
        raise ValueError("No se encontró '{' en la respuesta")
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    raise ValueError("JSON sin cerrar")


def generate_paquete(system_prompt: str, user_template: str, *,
                     code: str, title: str, type_: str,
                     duration: str, transcript: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY no está configurada")

    client = Anthropic(api_key=api_key)
    model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

    user_msg = user_template.format(
        code=code, title=title, type=type_, duration=duration, transcript=transcript,
    )

    last_err = None
    for attempt in range(3):
        try:
            logger.info(f"Llamada Claude (intento {attempt + 1}/3) modelo={model}")
            response = client.messages.create(
                model=model,
                max_tokens=16000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = "".join(
                block.text for block in response.content
                if getattr(block, "type", None) == "text"
            )
            data = _extract_json(raw)

            missing = [k for k in REQUIRED_KEYS if k not in data]
            if missing:
                raise ValueError(f"Faltan claves en el JSON: {missing}")
            if not isinstance(data.get("midform"), list) or not isinstance(data.get("shorts"), list):
                raise ValueError("midform y shorts deben ser listas")
            return data

        except (json.JSONDecodeError, ValueError) as e:
            last_err = e
            logger.warning(f"Intento {attempt + 1}: JSON inválido: {e}")
        except Exception as e:
            last_err = e
            logger.error(f"Error Claude (intento {attempt + 1}): {e}")

    raise RuntimeError(f"No se obtuvo JSON válido tras 3 intentos: {last_err}")
