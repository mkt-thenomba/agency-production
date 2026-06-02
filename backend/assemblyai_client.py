"""Cliente AssemblyAI con upload + submit + poll.

Estructura del flujo:
1. upload_audio(bytes/iter) → URL temporal de AssemblyAI
2. submit_transcript(audio_url) → transcript_id
3. poll_transcript(transcript_id) → status + (cuando done) words + utterances
4. build_transcript_lines(data) → lista de "[MM:SS] texto" lista para Claude
"""
import logging
import os
import time
from typing import Iterator, Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.assemblyai.com/v2"
UPLOAD_URL = f"{BASE_URL}/upload"
TRANSCRIPT_URL = f"{BASE_URL}/transcript"


class AssemblyAIError(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise AssemblyAIError("ASSEMBLYAI_API_KEY no está configurada en las env vars")
    return key


def upload_audio(file_stream, content_type: str = "application/octet-stream",
                 timeout: float = 600.0) -> str:
    """Sube un audio (file-like o bytes) y devuelve la upload_url temporal.
    AssemblyAI acepta el body como stream raw."""
    headers = {
        "authorization": _api_key(),
        "content-type": content_type,
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(UPLOAD_URL, headers=headers, content=file_stream)
    if resp.status_code != 200:
        raise AssemblyAIError(f"Upload falló: {resp.status_code} {resp.text[:200]}")
    data = resp.json()
    url = data.get("upload_url")
    if not url:
        raise AssemblyAIError(f"Upload sin upload_url en la respuesta: {data}")
    return url


def submit_transcript(audio_url: str, language_code: str = "es",
                      timeout: float = 60.0) -> str:
    """Lanza un job de transcripción. Devuelve transcript_id."""
    payload = {
        "audio_url": audio_url,
        "language_code": language_code,
        "punctuate": True,
        "format_text": True,
        "speaker_labels": False,
        # Si Pablo quiere speaker labels en algún creator, lo activamos por config
    }
    headers = {"authorization": _api_key(), "content-type": "application/json"}
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(TRANSCRIPT_URL, headers=headers, json=payload)
    if resp.status_code not in (200, 201):
        raise AssemblyAIError(f"Submit falló: {resp.status_code} {resp.text[:200]}")
    data = resp.json()
    tid = data.get("id")
    if not tid:
        raise AssemblyAIError(f"Submit sin id en la respuesta: {data}")
    return tid


def get_transcript(transcript_id: str, timeout: float = 30.0) -> dict:
    """Consulta el estado actual."""
    headers = {"authorization": _api_key()}
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(f"{TRANSCRIPT_URL}/{transcript_id}", headers=headers)
    if resp.status_code != 200:
        raise AssemblyAIError(f"Poll falló: {resp.status_code} {resp.text[:200]}")
    return resp.json()


def poll_until_done(transcript_id: str, on_progress=None,
                    poll_interval: float = 3.0,
                    max_wait_seconds: float = 1800.0) -> dict:
    """Bloquea hasta que el transcript termina (status='completed') o falla.
    Llama on_progress(status, percent) en cada poll si se pasa."""
    start = time.time()
    last_percent = 0
    while True:
        elapsed = time.time() - start
        if elapsed > max_wait_seconds:
            raise AssemblyAIError(
                f"Timeout {max_wait_seconds}s sin completar (último estado consultado)"
            )

        data = get_transcript(transcript_id)
        status = data.get("status", "unknown")

        if on_progress:
            # AssemblyAI no expone un % real durante "processing" — estimamos
            # según tiempo transcurrido vs estimación típica (audio_duration / 2)
            if status == "queued":
                pct = 0
            elif status == "processing":
                # Estimación: ~3 s base + ~1s por cada s de audio (peor caso)
                audio_dur = data.get("audio_duration") or 60
                expected = max(20, audio_dur * 0.6)
                pct = min(95, (elapsed / expected) * 100)
            elif status == "completed":
                pct = 100
            else:
                pct = last_percent
            last_percent = max(last_percent, pct)
            on_progress(status, last_percent, data)

        if status == "completed":
            return data
        if status == "error":
            err = data.get("error") or "AssemblyAI devolvió status=error sin detalle"
            raise AssemblyAIError(f"Transcripción falló: {err}")

        time.sleep(poll_interval)


def _format_mmss(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def build_transcript_lines(transcript_data: dict,
                           chunk_seconds: float = 6.0) -> tuple[str, float]:
    """Construye 'texto con timestamps [MM:SS]' a partir de la respuesta de AssemblyAI.
    Devuelve (texto, duration_seconds).
    Estrategia: agrupa words consecutivos en bloques de ~6s o ~80 chars."""
    words = transcript_data.get("words") or []
    if not words:
        # Fallback: texto plano sin timestamps
        text = transcript_data.get("text", "").strip()
        duration_ms = transcript_data.get("audio_duration_ms") or 0
        return text, duration_ms / 1000.0

    lines: list[str] = []
    current_words: list[str] = []
    current_start: Optional[float] = None

    for w in words:
        start_s = (w.get("start") or 0) / 1000.0
        if current_start is None:
            current_start = start_s

        current_words.append((w.get("text") or "").strip())
        elapsed_in_chunk = start_s - current_start
        joined = " ".join(current_words)
        if elapsed_in_chunk >= chunk_seconds or len(joined) >= 80:
            lines.append(f"[{_format_mmss(current_start)}] {joined}")
            current_words = []
            current_start = None

    if current_words and current_start is not None:
        lines.append(f"[{_format_mmss(current_start)}] {' '.join(current_words)}")

    # Duration en segundos: usa el end del último word si está, si no audio_duration
    last_word_end = (words[-1].get("end") or 0) / 1000.0 if words else 0
    audio_dur_ms = transcript_data.get("audio_duration") or 0
    if isinstance(audio_dur_ms, (int, float)) and audio_dur_ms > 0:
        # AssemblyAI devuelve audio_duration en segundos (no ms a pesar del nombre)
        duration_s = float(audio_dur_ms)
    else:
        duration_s = last_word_end

    return "\n".join(lines), duration_s
