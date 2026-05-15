"""Convierte cualquier input de transcripción a `[MM:SS] texto` por línea."""
import json
import re
from typing import Optional, Tuple


def format_mmss(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def _from_assemblyai_json(raw: str) -> Optional[Tuple[str, float]]:
    """Si es un JSON de AssemblyAI, devuelve (texto formateado, duración_s)."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    segments = None
    # Lo más común es `utterances` (con speaker labels) o `words`
    if isinstance(data.get("utterances"), list) and data["utterances"]:
        segments = [
            (u.get("start", 0) / 1000.0, (u.get("text") or "").strip())
            for u in data["utterances"]
            if (u.get("text") or "").strip()
        ]
    elif isinstance(data.get("words"), list) and data["words"]:
        # Agrupamos words por bloques de ~10s para no llenar de timestamps
        chunk_seconds = 10.0
        cur_start = None
        cur_text = []
        segments = []
        for w in data["words"]:
            t = w.get("start", 0) / 1000.0
            if cur_start is None:
                cur_start = t
            if t - cur_start >= chunk_seconds and cur_text:
                segments.append((cur_start, " ".join(cur_text)))
                cur_start = t
                cur_text = []
            cur_text.append((w.get("text") or "").strip())
        if cur_text:
            segments.append((cur_start, " ".join(cur_text)))
    elif isinstance(data.get("text"), str):
        # Solo texto plano — fallback sin timestamps reales
        segments = [(0.0, data["text"].strip())]

    if not segments:
        return None

    lines = [f"[{format_mmss(t)}] {text}" for t, text in segments if text]
    last_time = segments[-1][0] if segments else 0.0
    duration_s = float(data.get("audio_duration") or last_time)
    return "\n".join(lines), duration_s


_SRT_RE = re.compile(
    r"(\d+)\s*\n(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*\n([\s\S]*?)(?=\n\n|\Z)",
    re.MULTILINE,
)


def _from_srt(raw: str) -> Optional[Tuple[str, float]]:
    matches = list(_SRT_RE.finditer(raw))
    if not matches:
        return None
    lines = []
    last_end = 0.0
    for m in matches:
        h, mi, s = int(m.group(2)), int(m.group(3)), int(m.group(4))
        start_s = h * 3600 + mi * 60 + s
        eh, em, es = int(m.group(6)), int(m.group(7)), int(m.group(8))
        end_s = eh * 3600 + em * 60 + es
        text = re.sub(r"\s+", " ", m.group(10)).strip()
        if text:
            lines.append(f"[{format_mmss(start_s)}] {text}")
        last_end = max(last_end, end_s)
    return "\n".join(lines), float(last_end)


def _from_plain_text(raw: str) -> Tuple[str, float]:
    """Acepta texto que ya viene con timestamps `[MM:SS]` o sin nada.
    Si no hay timestamps, devuelve el texto crudo (Claude inferirá los cortes
    pero menos preciso) y duración 0."""
    raw = raw.strip()
    if not raw:
        return "", 0.0

    # ¿Ya tiene timestamps tipo [MM:SS]?
    if re.search(r"^\s*\[\d{1,2}:\d{2}\]", raw, re.MULTILINE):
        # Derivar duración del último timestamp
        matches = re.findall(r"\[(\d{1,2}):(\d{2})\]", raw)
        last = 0
        for mm, ss in matches:
            t = int(mm) * 60 + int(ss)
            if t > last:
                last = t
        return raw, float(last)

    # Sin timestamps — buscar otros formatos comunes (HH:MM:SS al inicio de línea)
    hhmmss = re.findall(r"^\s*(\d{1,2}):(\d{2}):(\d{2})\s+(.+)$", raw, re.MULTILINE)
    if hhmmss:
        lines = []
        last_s = 0
        for h, m, s, text in hhmmss:
            total = int(h) * 3600 + int(m) * 60 + int(s)
            last_s = max(last_s, total)
            lines.append(f"[{format_mmss(total)}] {text.strip()}")
        return "\n".join(lines), float(last_s)

    # Último recurso: texto plano sin timestamps
    return raw, 0.0


def parse_transcript(raw: str) -> Tuple[str, float, str]:
    """Devuelve (texto formateado [MM:SS] línea por línea, duración_s, fuente).
    `fuente` ∈ {"assemblyai-json", "srt", "text"}."""
    raw = (raw or "").strip()
    if not raw:
        return "", 0.0, "text"

    if raw.startswith("{"):
        out = _from_assemblyai_json(raw)
        if out:
            return out[0], out[1], "assemblyai-json"

    out = _from_srt(raw)
    if out:
        return out[0], out[1], "srt"

    text, dur = _from_plain_text(raw)
    return text, dur, "text"


def build_transcript_file(code: str, transcript: str, duration_s: float, source: str) -> str:
    header = (
        f"# Transcripción {code}\n"
        f"# Duración: {format_mmss(duration_s)}\n"
        f"# Fuente: {source}\n\n"
    )
    return header + transcript + "\n"
