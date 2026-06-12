"""Re-aligna los `in`/`out` de cada clip al timestamp REAL donde aparece la
frase citada en la transcripción.

Claude a veces inventa timestamps que parecen razonables pero no coinciden
con el contenido. Esta función:
  1. Parsea las líneas `[MM:SS] texto` de la transcripción
  2. Para cada clip, busca `phrase_in` (literal, después fuzzy) en el texto
  3. Si la encuentra, reescribe `in` con el timestamp real
  4. Igual para `phrase_out` con un buffer de unos segundos
  5. Marca `_in_verified` / `_out_verified` para debugging
"""
import re
import unicodedata
from typing import Optional

TS_LINE_RE = re.compile(r"^\[(\d+):(\d+)\]\s+(.+)$")


def _normalize(s: str) -> str:
    """Lowercase, strip acentos, deja solo \w y espacios colapsados."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s]", " ", s.lower())
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_lines(transcript: str) -> list[tuple[int, str, str]]:
    """Devuelve [(seconds, raw_text, normalized_text), ...]."""
    out = []
    for line in transcript.splitlines():
        m = TS_LINE_RE.match(line.strip())
        if not m:
            continue
        mm, ss, text = m.groups()
        out.append((int(mm) * 60 + int(ss), text, _normalize(text)))
    return out


def _find_phrase_ts(phrase: str,
                    lines: list[tuple[int, str, str]]) -> Optional[int]:
    """Encuentra el timestamp en segundos donde aparece la frase.

    Estrategia (en orden):
      1. Coincidencia exacta normalizada en una línea
      2. Coincidencia de los primeros 4-8 palabras en una línea
      3. Coincidencia en el flujo continuo (cruzando fronteras de línea)
    """
    if not phrase or not lines:
        return None
    p_norm = _normalize(phrase)
    if not p_norm:
        return None

    # 1. Exacta línea por línea
    for ts, _, norm in lines:
        if p_norm in norm:
            return ts

    # 2. Primeras N palabras (4-8)
    words = p_norm.split()
    if len(words) >= 3:
        for n in (8, 6, 4, 3):
            if len(words) < n:
                continue
            head = " ".join(words[:n])
            for ts, _, norm in lines:
                if head in norm:
                    return ts

    # 3. Concatenado plano: busca cruzando líneas
    if len(words) >= 4:
        flat_parts = []
        offsets: list[tuple[int, int]] = []  # (char_offset, ts)
        cursor = 0
        for ts, _, norm in lines:
            offsets.append((cursor, ts))
            flat_parts.append(norm)
            cursor += len(norm) + 1
        flat = " ".join(flat_parts)
        # Busca con varios prefijos por si la frase está cortada
        for n in (10, 8, 6, 5, 4):
            if len(words) < n:
                continue
            head = " ".join(words[:n])
            idx = flat.find(head)
            if idx != -1:
                # localiza la última línea cuyo offset ≤ idx
                for offset, ts in reversed(offsets):
                    if offset <= idx:
                        return ts

    return None


def _fmt_ts(seconds: int) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _parse_ts_str(ts: str) -> Optional[int]:
    """'02:15' → 135. Devuelve None si no parsea."""
    if not ts:
        return None
    m = re.match(r"^\s*(\d+):(\d+)\s*$", str(ts))
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def snap_clip_timestamps(paquete: dict, transcript: str) -> dict:
    """Modifica `paquete` in-place: reescribe `in`/`out` de cada clip en
    `midform` y `shorts` al timestamp REAL donde aparece la frase citada.
    Añade `_in_verified`, `_out_verified` y `_original_timestamps`."""
    lines = _parse_lines(transcript)
    if not lines:
        return paquete

    last_ts_in_transcript = lines[-1][0]

    for key in ("midform", "shorts"):
        clips = paquete.get(key)
        if not isinstance(clips, list):
            continue
        for clip in clips:
            phrase_in = clip.get("phrase_in", "") or ""
            phrase_out = clip.get("phrase_out", "") or phrase_in
            orig_in = clip.get("in", "")
            orig_out = clip.get("out", "")

            real_in = _find_phrase_ts(phrase_in, lines)
            real_out = _find_phrase_ts(phrase_out, lines)

            if real_in is not None:
                clip["in"] = _fmt_ts(real_in)
                clip["_in_verified"] = True
            else:
                clip["_in_verified"] = False

            if real_out is not None:
                # Buffer ~6s para capturar el final de la frase hablada
                # pero sin pasarse del final del audio
                snapped_out = min(real_out + 6, last_ts_in_transcript)
                # Si tenemos in verificado, garantizar que out > in
                if real_in is not None and snapped_out <= real_in:
                    snapped_out = real_in + 10
                clip["out"] = _fmt_ts(snapped_out)
                clip["_out_verified"] = True
            else:
                clip["_out_verified"] = False

            # Guarda los originales si cambiaron, para auditar
            changed = (
                (clip["_in_verified"] and orig_in != clip["in"]) or
                (clip["_out_verified"] and orig_out != clip["out"])
            )
            if changed:
                clip["_original_timestamps"] = {"in": orig_in, "out": orig_out}

            # Recalcula `duration` si tenemos ambos verificados
            if clip["_in_verified"] and clip["_out_verified"]:
                ts_in = _parse_ts_str(clip["in"]) or 0
                ts_out = _parse_ts_str(clip["out"]) or ts_in
                clip["duration"] = _fmt_ts(max(0, ts_out - ts_in))

    return paquete


def _clip_duration_seconds(clip: dict) -> Optional[int]:
    """Duración del clip en segundos. Prefiere in/out, si no usa el campo duration."""
    in_ts = _parse_ts_str(clip.get("in", ""))
    out_ts = _parse_ts_str(clip.get("out", ""))
    if in_ts is not None and out_ts is not None and out_ts > in_ts:
        return out_ts - in_ts
    return _parse_ts_str(clip.get("duration", ""))


def filter_midform_by_duration(paquete: dict,
                               min_seconds: int = 300,
                               max_seconds: int = 720) -> dict:
    """Elimina midforms fuera del rango [min, max] segundos.
    Default 300-720 = 5-12 minutos (calibrado para vídeos de ~20 min de media).
    Los rechazados quedan en `paquete["_rejected_midform"]` para auditoría."""
    midform = paquete.get("midform")
    if not isinstance(midform, list):
        return paquete

    valid: list[dict] = []
    rejected: list[dict] = []
    for clip in midform:
        dur = _clip_duration_seconds(clip)
        if dur is None:
            clip["_rejected_reason"] = "duración no parseable"
            rejected.append(clip)
            continue
        if dur < min_seconds:
            clip["_rejected_reason"] = (
                f"duración {dur}s < mínimo {min_seconds}s ({min_seconds//60} min)"
            )
            rejected.append(clip)
        elif dur > max_seconds:
            clip["_rejected_reason"] = (
                f"duración {dur}s > máximo {max_seconds}s ({max_seconds//60} min)"
            )
            rejected.append(clip)
        else:
            valid.append(clip)

    paquete["midform"] = valid
    if rejected:
        paquete["_rejected_midform"] = rejected
    return paquete
