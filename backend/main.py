"""FastAPI multi-tenant: hub + sub-dashboard por creator."""
import logging
import os
from pathlib import Path
from typing import Optional

import json as json_lib

from fastapi import FastAPI, HTTPException, Depends, Request, Response, Form, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .database import init_db, get_db, SessionLocal
from .models import Creator, Video, ChecklistItem
from .seed import seed_creators
from .auth import (
    PasswordGate, password_required, make_session_token, is_valid_session,
    COOKIE_NAME, LOGIN_PATH,
)
from .transcript_parser import parse_transcript, format_mmss
from .claude_client import generate_paquete, stream_paquete
from . import assemblyai_client
from . import vercel_blob
from .exporters import (
    slugify, clean_title_from_input, detect_type, extract_code,
    next_available_code, suggest_publish_at, render_all,
)

logger = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

ROOT = Path(__file__).parent.parent
PUBLIC_DIR = ROOT / "frontend"

app = FastAPI(title="AgencyProduction")
app.add_middleware(PasswordGate)


@app.on_event("startup")
def on_startup():
    init_db()
    seed_creators()
    logger.info("Backend listo")


# ============== Páginas HTML ==============

@app.get("/")
def hub_page():
    return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/c/{slug}")
def creator_page(slug: str, db: Session = Depends(get_db)):
    if not db.query(Creator).filter(Creator.slug == slug).first():
        return PlainTextResponse("Creator no encontrado", status_code=404)
    return FileResponse(PUBLIC_DIR / "creator.html")


@app.get(LOGIN_PATH)
def login_page():
    return FileResponse(PUBLIC_DIR / "login.html")


# ============== Auth ==============

@app.post("/api/login")
def api_login(password: str = Form(...), next: str = Form("/")):
    expected = os.environ.get("SITE_PASSWORD", "").strip()
    if not expected:
        # Sin password configurado → todo público
        return RedirectResponse(url=next or "/", status_code=302)
    if password != expected:
        return RedirectResponse(url=f"{LOGIN_PATH}?error=1", status_code=302)
    resp = RedirectResponse(url=next or "/", status_code=302)
    token = make_session_token()
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax",
                    max_age=60 * 60 * 24 * 30, secure=False)
    return resp


@app.get("/api/logout")
def api_logout():
    resp = RedirectResponse(url=LOGIN_PATH, status_code=302)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.get("/api/auth-status")
def auth_status(request: Request):
    if not password_required():
        return {"required": False, "logged_in": True}
    return {"required": True, "logged_in": is_valid_session(request.cookies.get(COOKIE_NAME))}


# ============== Health ==============

@app.get("/api/health")
def health():
    return {"ok": True}


# ============== Creators ==============

def _creator_to_summary(c: Creator, video_count: int = 0) -> dict:
    return {
        "slug": c.slug,
        "name": c.name,
        "subtitle": c.subtitle or "",
        "avatar_initials": c.avatar_initials or "",
        "color_primary": c.color_primary,
        "color_secondary": c.color_secondary,
        "is_placeholder": bool((c.config or {}).get("is_placeholder")),
        "video_count": video_count,
    }


@app.get("/api/creators")
def list_creators(db: Session = Depends(get_db)):
    creators = db.query(Creator).filter(Creator.is_active == True).order_by(Creator.id).all()  # noqa: E712
    out = []
    for c in creators:
        n = db.query(Video).filter(Video.creator_id == c.id).count()
        out.append(_creator_to_summary(c, n))
    return {"creators": out}


@app.get("/api/creators/{slug}")
def get_creator(slug: str, db: Session = Depends(get_db)):
    c = db.query(Creator).filter(Creator.slug == slug).first()
    if not c:
        raise HTTPException(404, "Creator no encontrado")
    n = db.query(Video).filter(Video.creator_id == c.id).count()
    summary = _creator_to_summary(c, n)
    summary["checklist_template"] = c.checklist_template or []
    summary["config"] = c.config or {}
    return summary


# ============== Videos ==============

def _video_to_dict(v: Video, include_artifacts: bool = False) -> dict:
    by_key = {ci.item_key: ci.done for ci in v.checklist}
    checklist = {}
    template = (v.creator.checklist_template if v.creator else []) or []
    for tpl in template:
        checklist[tpl["key"]] = bool(by_key.get(tpl["key"], False))

    data = {
        "id": v.id,
        "creator_slug": v.creator.slug if v.creator else None,
        "code": v.code,
        "title": v.title,
        "type": v.type,
        "duration": v.duration or "—",
        "status": v.status,
        "transcript_source": v.transcript_source,
        "error_message": v.error_message,
        "checklist": checklist,
        "suggested_publish_at": v.suggested_publish_at.isoformat() if v.suggested_publish_at else None,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }
    if include_artifacts:
        data["paquete"] = v.paquete_json
        data["has_files"] = {
            "PAQUETE.md": bool(v.paquete_md),
            "transcripcion.txt": bool(v.transcript),
            "descripcion.txt": bool(v.descripcion_txt),
            "cortes_editor.csv": bool(v.cortes_csv),
            "miniatura.txt": bool(v.miniatura_txt),
            "paquete.json": bool(v.paquete_json),
        }
    return data


@app.get("/api/creators/{slug}/videos")
def list_videos(slug: str, db: Session = Depends(get_db)):
    c = db.query(Creator).filter(Creator.slug == slug).first()
    if not c:
        raise HTTPException(404, "Creator no encontrado")
    videos = db.query(Video).filter(Video.creator_id == c.id) \
        .order_by(Video.created_at.desc()).all()
    return {"videos": [_video_to_dict(v) for v in videos]}


def _sse(data: dict) -> str:
    return f"data: {json_lib.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/creators/{slug}/process")
def process_transcript(slug: str, payload: dict):
    """Streaming SSE: parsing → llamada Claude (tokens en vivo) → render → save.
    Emite eventos {stage, progress(0-100), message?, video?} hasta `done` o `error`."""

    def event_stream():
        db: Session = SessionLocal()
        video = None
        try:
            yield _sse({"stage": "parsing", "progress": 2, "message": "Cargando creator"})

            c = db.query(Creator).filter(Creator.slug == slug).first()
            if not c:
                yield _sse({"stage": "error", "error": "Creator no encontrado"})
                return

            raw_transcript = (payload.get("transcript") or "").strip()
            if not raw_transcript:
                yield _sse({"stage": "error", "error": "Falta la transcripción"})
                return

            title_hint = payload.get("title_hint") or ""
            type_hint = payload.get("type_hint") or ""

            yield _sse({"stage": "parsing", "progress": 5, "message": "Parseando transcripción"})
            parsed, duration_s, source = parse_transcript(raw_transcript)
            duration_str = format_mmss(duration_s) if duration_s > 0 else "—"

            cfg = c.config or {}
            title = clean_title_from_input(title_hint)
            vtype = type_hint or detect_type(
                title_hint, cfg.get("type_keywords", {}),
                cfg.get("default_type", "historia"),
            )

            existing = [row[0] for row in
                        db.query(Video.code).filter(Video.creator_id == c.id).all()]
            code = (extract_code(title_hint, c.code_prefix)
                    or next_available_code(existing, c.code_prefix))
            if code in existing:
                code = next_available_code(existing, c.code_prefix)
            slug_v = slugify(title)

            yield _sse({"stage": "saving", "progress": 8,
                        "message": f"Reservando registro {code}"})

            video = Video(
                creator_id=c.id, code=code, slug=slug_v, title=title, type=vtype,
                duration=duration_str, status="processing",
                transcript=parsed, transcript_source=source,
                suggested_publish_at=suggest_publish_at(
                    vtype, cfg.get("publishing_schedule", {})),
            )
            db.add(video); db.commit(); db.refresh(video)
            for tpl in c.checklist_template or []:
                db.add(ChecklistItem(video_id=video.id, item_key=tpl["key"], done=False))
            db.commit()

            yield _sse({"stage": "claude_start", "progress": 12,
                        "message": "Llamando a Claude…"})

            # Streaming con Claude — progreso real basado en tokens recibidos
            EXPECTED_CHARS = 9000  # un paquete típico
            state = {"last_pct": 12, "chars": 0}

            def on_progress(chars: int, _chunk: str):
                state["chars"] = chars
                # 12 → 88 según chars recibidos
                pct = 12 + min(76, (chars / EXPECTED_CHARS) * 76)
                state["last_pct"] = pct

            # Generador interno para emitir eventos durante el stream
            # Como stream_paquete es bloqueante, usamos un truco: poll de last_pct
            # via timer. Pero más simple: hacemos el stream "manual" aquí.
            from anthropic import Anthropic
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                yield _sse({"stage": "error", "error": "ANTHROPIC_API_KEY no configurada"})
                video.status = "error"
                video.error_message = "ANTHROPIC_API_KEY no configurada"
                db.commit()
                return

            client = Anthropic(api_key=api_key)
            model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
            user_msg = c.user_template.format(
                code=code, title=title, type=vtype,
                duration=duration_str, transcript=parsed,
            )

            paquete = None
            last_err = None
            for attempt in range(2):
                accumulated = []
                last_emit = 0
                try:
                    with client.messages.stream(
                        model=model, max_tokens=16000,
                        system=c.system_prompt,
                        messages=[{"role": "user", "content": user_msg}],
                    ) as stream:
                        for text_chunk in stream.text_stream:
                            accumulated.append(text_chunk)
                            chars = sum(len(x) for x in accumulated)
                            # Emite cada ~200 chars para no spamear
                            if chars - last_emit >= 200:
                                last_emit = chars
                                pct = 12 + min(76, (chars / EXPECTED_CHARS) * 76)
                                yield _sse({
                                    "stage": "claude_streaming",
                                    "progress": round(pct, 1),
                                    "chars": chars,
                                    "message": f"Claude generando… ({chars} caracteres)",
                                })

                    raw = "".join(accumulated)
                    from .claude_client import _extract_json, REQUIRED_KEYS
                    paquete = _extract_json(raw)
                    missing = [k for k in REQUIRED_KEYS if k not in paquete]
                    if missing:
                        raise ValueError(f"Faltan claves en el JSON: {missing}")
                    if not isinstance(paquete.get("midform"), list) \
                       or not isinstance(paquete.get("shorts"), list):
                        raise ValueError("midform y shorts deben ser listas")
                    break  # éxito
                except Exception as e:
                    last_err = e
                    logger.warning(f"Stream intento {attempt + 1}: {e}")
                    if attempt < 1:
                        yield _sse({"stage": "retrying", "progress": 12,
                                    "message": "JSON inválido, reintentando…"})

            if paquete is None:
                raise RuntimeError(f"No se obtuvo JSON válido: {last_err}")

            yield _sse({"stage": "rendering", "progress": 92,
                        "message": "Renderizando entregables"})
            artifacts = render_all(code, vtype, duration_str, paquete,
                                   cfg.get("thumb_templates", {}))

            yield _sse({"stage": "saving_final", "progress": 97,
                        "message": "Guardando en base de datos"})
            video.paquete_json = paquete
            video.paquete_md = artifacts["paquete_md"]
            video.descripcion_txt = artifacts["descripcion"]
            video.cortes_csv = artifacts["cortes_csv"]
            video.miniatura_txt = artifacts["miniatura"]
            video.status = "done"
            db.commit()
            db.refresh(video)

            yield _sse({
                "stage": "done", "progress": 100,
                "message": f"PAQUETE generado: {video.code}",
                "video": _video_to_dict(video, include_artifacts=True),
            })

        except Exception as e:
            logger.exception("Fallo en process_transcript stream")
            try:
                if video is not None:
                    video.status = "error"
                    video.error_message = f"{type(e).__name__}: {e}"
                    db.commit()
            except Exception:
                pass
            yield _sse({"stage": "error", "error": f"{type(e).__name__}: {e}"})
        finally:
            db.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Tamaño máximo aceptado para el upload de audio (Vercel Pro ~100 MB).
MAX_AUDIO_BYTES = 100 * 1024 * 1024


@app.post("/api/creators/{slug}/process-audio")
async def process_audio(
    slug: str,
    file: UploadFile = File(...),
    title_hint: str = Form(""),
    type_hint: str = Form(""),
):
    """Streaming SSE: upload AssemblyAI → poll → build transcript → Claude → render → save.
    Tramos de progreso:
      0→2   recibido en backend
      2→10  uploading a AssemblyAI
      10→55 AssemblyAI transcribiendo (poll)
      55→90 Claude streaming
      90→97 render
      97→100 save
    """

    # Leemos el audio en memoria. UploadFile en Starlette ya hace spooling
    # a disco para archivos > 1 MB, así que para 100 MB esto está bien.
    raw_bytes = await file.read()
    audio_size = len(raw_bytes)
    if audio_size == 0:
        raise HTTPException(400, "Archivo vacío")
    if audio_size > MAX_AUDIO_BYTES:
        raise HTTPException(
            413,
            f"Archivo demasiado grande: {audio_size/1024/1024:.1f} MB "
            f"(máx {MAX_AUDIO_BYTES/1024/1024:.0f} MB). Exporta a MP3 a 128 kbps."
        )

    content_type = file.content_type or "application/octet-stream"
    original_filename = file.filename or "audio.mp3"

    def event_stream():
        db: Session = SessionLocal()
        video = None
        try:
            yield _sse({"stage": "received", "progress": 2,
                        "message": f"Recibido {original_filename} ({audio_size/1024/1024:.1f} MB)"})

            c = db.query(Creator).filter(Creator.slug == slug).first()
            if not c:
                yield _sse({"stage": "error", "error": "Creator no encontrado"})
                return

            # ─── Subir a AssemblyAI ───
            yield _sse({"stage": "uploading", "progress": 4,
                        "message": "Subiendo audio a AssemblyAI…"})
            try:
                upload_url = assemblyai_client.upload_audio(
                    raw_bytes, content_type=content_type)
            except Exception as e:
                yield _sse({"stage": "error",
                            "error": f"AssemblyAI upload falló: {e}"})
                return
            yield _sse({"stage": "uploaded", "progress": 10,
                        "message": "Audio subido. Lanzando transcripción…"})

            # ─── Submit transcript job ───
            try:
                transcript_id = assemblyai_client.submit_transcript(
                    upload_url, language_code="es")
            except Exception as e:
                yield _sse({"stage": "error",
                            "error": f"AssemblyAI submit falló: {e}"})
                return

            # ─── Poll hasta completed (10→55%) ───
            # poll_until_done es bloqueante, así que aquí emitimos manualmente
            import time as _time
            start = _time.time()
            last_emitted_pct = 10
            transcript_data = None
            while True:
                if _time.time() - start > 600:
                    yield _sse({"stage": "error",
                                "error": "Timeout esperando AssemblyAI (>10 min)"})
                    return
                try:
                    data = assemblyai_client.get_transcript(transcript_id)
                except Exception as e:
                    yield _sse({"stage": "error", "error": f"Poll falló: {e}"})
                    return
                status = data.get("status", "unknown")
                elapsed = _time.time() - start
                if status == "queued":
                    pct = 10
                    msg = "En cola en AssemblyAI…"
                elif status == "processing":
                    audio_dur = data.get("audio_duration") or 60
                    expected = max(20, audio_dur * 0.5)
                    pct = 10 + min(43, (elapsed / expected) * 43)
                    msg = f"AssemblyAI transcribiendo… ({int(elapsed)}s)"
                elif status == "completed":
                    pct = 55
                    msg = "Transcripción terminada"
                    transcript_data = data
                elif status == "error":
                    err = data.get("error") or "AssemblyAI devolvió error"
                    yield _sse({"stage": "error",
                                "error": f"Transcripción falló: {err}"})
                    return
                else:
                    pct = last_emitted_pct
                    msg = f"Estado: {status}"

                # Emite cada cambio significativo de % o cada poll si processing
                if pct - last_emitted_pct >= 1 or status == "completed":
                    last_emitted_pct = pct
                    yield _sse({"stage": f"assemblyai_{status}",
                                "progress": round(pct, 1), "message": msg})

                if status == "completed":
                    break
                _time.sleep(3)

            # ─── Construir transcript con [MM:SS] ───
            yield _sse({"stage": "transcript_built", "progress": 56,
                        "message": "Componiendo transcripción con timestamps"})
            parsed, duration_s = assemblyai_client.build_transcript_lines(
                transcript_data)
            duration_str = format_mmss(duration_s) if duration_s > 0 else "—"
            if not parsed.strip():
                yield _sse({"stage": "error",
                            "error": "AssemblyAI no devolvió texto"})
                return

            # ─── Metadatos y creación de Video ───
            cfg = c.config or {}
            title = clean_title_from_input(title_hint or original_filename)
            vtype = type_hint or detect_type(
                title_hint or original_filename,
                cfg.get("type_keywords", {}),
                cfg.get("default_type", "historia"),
            )
            existing = [row[0] for row in
                        db.query(Video.code).filter(Video.creator_id == c.id).all()]
            code = (extract_code(title_hint or original_filename, c.code_prefix)
                    or next_available_code(existing, c.code_prefix))
            if code in existing:
                code = next_available_code(existing, c.code_prefix)
            slug_v = slugify(title)

            yield _sse({"stage": "saving", "progress": 58,
                        "message": f"Reservando registro {code}"})

            video = Video(
                creator_id=c.id, code=code, slug=slug_v, title=title, type=vtype,
                duration=duration_str, status="processing",
                transcript=parsed, transcript_source="assemblyai",
                suggested_publish_at=suggest_publish_at(
                    vtype, cfg.get("publishing_schedule", {})),
            )
            db.add(video); db.commit(); db.refresh(video)
            for tpl in c.checklist_template or []:
                db.add(ChecklistItem(
                    video_id=video.id, item_key=tpl["key"], done=False))
            db.commit()

            # ─── Claude streaming (60 → 90%) ───
            yield _sse({"stage": "claude_start", "progress": 60,
                        "message": "Llamando a Claude…"})

            from anthropic import Anthropic
            from .claude_client import _extract_json, REQUIRED_KEYS

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                yield _sse({"stage": "error",
                            "error": "ANTHROPIC_API_KEY no configurada"})
                video.status = "error"; db.commit()
                return

            client = Anthropic(api_key=api_key)
            model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
            user_msg = c.user_template.format(
                code=code, title=title, type=vtype,
                duration=duration_str, transcript=parsed,
            )
            EXPECTED_CHARS = 9000
            paquete = None
            last_err = None
            for attempt in range(2):
                accumulated = []
                last_emit = 0
                try:
                    with client.messages.stream(
                        model=model, max_tokens=16000,
                        system=c.system_prompt,
                        messages=[{"role": "user", "content": user_msg}],
                    ) as stream:
                        for text_chunk in stream.text_stream:
                            accumulated.append(text_chunk)
                            chars = sum(len(x) for x in accumulated)
                            if chars - last_emit >= 200:
                                last_emit = chars
                                pct = 60 + min(30, (chars / EXPECTED_CHARS) * 30)
                                yield _sse({
                                    "stage": "claude_streaming",
                                    "progress": round(pct, 1),
                                    "chars": chars,
                                    "message": f"Claude generando… ({chars} caracteres)",
                                })

                    raw = "".join(accumulated)
                    paquete = _extract_json(raw)
                    missing = [k for k in REQUIRED_KEYS if k not in paquete]
                    if missing:
                        raise ValueError(f"Faltan claves: {missing}")
                    if not isinstance(paquete.get("midform"), list) \
                       or not isinstance(paquete.get("shorts"), list):
                        raise ValueError("midform/shorts no son listas")
                    break
                except Exception as e:
                    last_err = e
                    logger.warning(f"Stream intento {attempt + 1}: {e}")
                    if attempt < 1:
                        yield _sse({"stage": "retrying", "progress": 60,
                                    "message": "JSON inválido, reintentando…"})

            if paquete is None:
                raise RuntimeError(f"No se obtuvo JSON válido: {last_err}")

            yield _sse({"stage": "rendering", "progress": 92,
                        "message": "Renderizando entregables"})
            artifacts = render_all(code, vtype, duration_str, paquete,
                                   cfg.get("thumb_templates", {}))

            yield _sse({"stage": "saving_final", "progress": 97,
                        "message": "Guardando en base de datos"})
            video.paquete_json = paquete
            video.paquete_md = artifacts["paquete_md"]
            video.descripcion_txt = artifacts["descripcion"]
            video.cortes_csv = artifacts["cortes_csv"]
            video.miniatura_txt = artifacts["miniatura"]
            video.status = "done"
            db.commit(); db.refresh(video)

            yield _sse({
                "stage": "done", "progress": 100,
                "message": f"PAQUETE generado: {video.code}",
                "video": _video_to_dict(video, include_artifacts=True),
            })

        except Exception as e:
            logger.exception("Fallo en process_audio stream")
            try:
                if video is not None:
                    video.status = "error"
                    video.error_message = f"{type(e).__name__}: {e}"
                    db.commit()
            except Exception:
                pass
            yield _sse({"stage": "error", "error": f"{type(e).__name__}: {e}"})
        finally:
            db.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ──────────────────────────────────────────────────────────────────────
#  Vercel Blob client-upload: bypass del límite 4.5MB de las funciones
# ──────────────────────────────────────────────────────────────────────

@app.post("/api/blob/handle-upload")
async def blob_handle_upload(request: Request):
    """Endpoint que firma tokens de cliente para @vercel/blob/client SDK.
    El SDK del navegador POSTea aquí pidiendo permiso, devolvemos un JWT
    custom firmado con BLOB_READ_WRITE_TOKEN. Luego el navegador sube
    DIRECTO a blob.vercel-storage.com sin pasar por nosotros."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Body JSON inválido")

    msg_type = body.get("type")
    payload = body.get("payload") or {}

    if msg_type == "blob.generate-client-token":
        pathname = (payload.get("pathname") or "").strip()
        if not pathname:
            raise HTTPException(400, "Falta pathname")
        # Por seguridad obligamos prefijo audio/ — evita que alguien use
        # nuestros tokens para subir cualquier otra cosa
        if not pathname.startswith("audio/"):
            pathname = f"audio/{pathname}"

        try:
            client_token = vercel_blob.generate_client_token(
                pathname=pathname,
                allowed_content_types=vercel_blob.AUDIO_ALLOWED_CONTENT_TYPES,
                max_size_bytes=vercel_blob.MAX_AUDIO_BYTES,
                add_random_suffix=True,
                cache_control_max_age=60,
            )
        except vercel_blob.BlobConfigError as e:
            logger.error(f"Blob config: {e}")
            raise HTTPException(500, str(e))

        return {
            "type": "blob.generate-client-token",
            "clientToken": client_token,
        }

    if msg_type == "blob.upload-completed":
        # No usamos el callback (procesamos por URL después). 200 OK.
        return {"response": "ok"}

    raise HTTPException(400, f"Tipo no soportado: {msg_type}")


@app.post("/api/creators/{slug}/process-audio-url")
async def process_audio_url(slug: str, payload: dict):
    """Streaming SSE: recibe `blob_url` (ya subido por el navegador a Vercel Blob)
    y dispara el flujo AssemblyAI → Claude → render → save.

    Body JSON: { blob_url, title_hint?, type_hint?, original_filename? }
    """
    blob_url = (payload.get("blob_url") or "").strip()
    if not blob_url:
        raise HTTPException(400, "Falta blob_url")
    if not (blob_url.startswith("https://") and
            ".public.blob.vercel-storage.com/" in blob_url):
        raise HTTPException(400, "blob_url no es una URL de Vercel Blob válida")

    title_hint = payload.get("title_hint") or ""
    type_hint = payload.get("type_hint") or ""
    original_filename = payload.get("original_filename") or "audio.mp3"

    def event_stream():
        db: Session = SessionLocal()
        video = None
        try:
            yield _sse({"stage": "received", "progress": 3,
                        "message": "URL del audio recibida"})

            c = db.query(Creator).filter(Creator.slug == slug).first()
            if not c:
                yield _sse({"stage": "error", "error": "Creator no encontrado"})
                return

            # Submit transcript job (AssemblyAI descarga el audio desde la URL pública)
            yield _sse({"stage": "uploading", "progress": 6,
                        "message": "Enviando URL a AssemblyAI…"})
            try:
                transcript_id = assemblyai_client.submit_transcript(
                    blob_url, language_code="es")
            except Exception as e:
                yield _sse({"stage": "error",
                            "error": f"AssemblyAI submit falló: {e}"})
                return

            # Poll hasta completed (6 → 55%)
            import time as _time
            start = _time.time()
            last_emitted_pct = 6
            transcript_data = None
            while True:
                if _time.time() - start > 600:
                    yield _sse({"stage": "error",
                                "error": "Timeout esperando AssemblyAI (>10 min)"})
                    return
                try:
                    data = assemblyai_client.get_transcript(transcript_id)
                except Exception as e:
                    yield _sse({"stage": "error",
                                "error": f"Poll falló: {e}"})
                    return
                status = data.get("status", "unknown")
                elapsed = _time.time() - start
                if status == "queued":
                    pct = 8
                    msg = "En cola en AssemblyAI…"
                elif status == "processing":
                    audio_dur = data.get("audio_duration") or 60
                    expected = max(20, audio_dur * 0.5)
                    pct = 8 + min(45, (elapsed / expected) * 45)
                    msg = f"AssemblyAI transcribiendo… ({int(elapsed)}s)"
                elif status == "completed":
                    pct = 55
                    msg = "Transcripción terminada"
                    transcript_data = data
                elif status == "error":
                    err = data.get("error") or "AssemblyAI devolvió error"
                    yield _sse({"stage": "error",
                                "error": f"Transcripción falló: {err}"})
                    return
                else:
                    pct = last_emitted_pct
                    msg = f"Estado: {status}"

                if pct - last_emitted_pct >= 1 or status == "completed":
                    last_emitted_pct = pct
                    yield _sse({"stage": f"assemblyai_{status}",
                                "progress": round(pct, 1), "message": msg})

                if status == "completed":
                    break
                _time.sleep(3)

            yield _sse({"stage": "transcript_built", "progress": 56,
                        "message": "Componiendo transcripción con timestamps"})
            parsed, duration_s = assemblyai_client.build_transcript_lines(
                transcript_data)
            duration_str = format_mmss(duration_s) if duration_s > 0 else "—"
            if not parsed.strip():
                yield _sse({"stage": "error",
                            "error": "AssemblyAI no devolvió texto"})
                return

            # Metadatos
            cfg = c.config or {}
            title = clean_title_from_input(title_hint or original_filename)
            vtype = type_hint or detect_type(
                title_hint or original_filename,
                cfg.get("type_keywords", {}),
                cfg.get("default_type", "historia"),
            )
            existing = [row[0] for row in
                        db.query(Video.code).filter(Video.creator_id == c.id).all()]
            code = (extract_code(title_hint or original_filename, c.code_prefix)
                    or next_available_code(existing, c.code_prefix))
            if code in existing:
                code = next_available_code(existing, c.code_prefix)
            slug_v = slugify(title)

            yield _sse({"stage": "saving", "progress": 58,
                        "message": f"Reservando registro {code}"})
            video = Video(
                creator_id=c.id, code=code, slug=slug_v, title=title, type=vtype,
                duration=duration_str, status="processing",
                transcript=parsed, transcript_source="assemblyai+blob",
                suggested_publish_at=suggest_publish_at(
                    vtype, cfg.get("publishing_schedule", {})),
            )
            db.add(video); db.commit(); db.refresh(video)
            for tpl in c.checklist_template or []:
                db.add(ChecklistItem(
                    video_id=video.id, item_key=tpl["key"], done=False))
            db.commit()

            # Claude streaming (60 → 90%)
            yield _sse({"stage": "claude_start", "progress": 60,
                        "message": "Llamando a Claude…"})
            from anthropic import Anthropic
            from .claude_client import _extract_json, REQUIRED_KEYS

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                yield _sse({"stage": "error",
                            "error": "ANTHROPIC_API_KEY no configurada"})
                video.status = "error"; db.commit()
                return

            client = Anthropic(api_key=api_key)
            model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
            user_msg = c.user_template.format(
                code=code, title=title, type=vtype,
                duration=duration_str, transcript=parsed,
            )
            EXPECTED_CHARS = 9000
            paquete = None
            last_err = None
            for attempt in range(2):
                accumulated = []
                last_emit = 0
                try:
                    with client.messages.stream(
                        model=model, max_tokens=16000,
                        system=c.system_prompt,
                        messages=[{"role": "user", "content": user_msg}],
                    ) as stream:
                        for text_chunk in stream.text_stream:
                            accumulated.append(text_chunk)
                            chars = sum(len(x) for x in accumulated)
                            if chars - last_emit >= 200:
                                last_emit = chars
                                pct = 60 + min(30, (chars / EXPECTED_CHARS) * 30)
                                yield _sse({
                                    "stage": "claude_streaming",
                                    "progress": round(pct, 1),
                                    "chars": chars,
                                    "message": f"Claude generando… ({chars} caracteres)",
                                })
                    raw = "".join(accumulated)
                    paquete = _extract_json(raw)
                    missing = [k for k in REQUIRED_KEYS if k not in paquete]
                    if missing:
                        raise ValueError(f"Faltan claves: {missing}")
                    if not isinstance(paquete.get("midform"), list) \
                       or not isinstance(paquete.get("shorts"), list):
                        raise ValueError("midform/shorts no son listas")
                    break
                except Exception as e:
                    last_err = e
                    logger.warning(f"Stream intento {attempt + 1}: {e}")
                    if attempt < 1:
                        yield _sse({"stage": "retrying", "progress": 60,
                                    "message": "JSON inválido, reintentando…"})

            if paquete is None:
                raise RuntimeError(f"No se obtuvo JSON válido: {last_err}")

            yield _sse({"stage": "rendering", "progress": 92,
                        "message": "Renderizando entregables"})
            artifacts = render_all(code, vtype, duration_str, paquete,
                                   cfg.get("thumb_templates", {}))

            yield _sse({"stage": "saving_final", "progress": 97,
                        "message": "Guardando en base de datos"})
            video.paquete_json = paquete
            video.paquete_md = artifacts["paquete_md"]
            video.descripcion_txt = artifacts["descripcion"]
            video.cortes_csv = artifacts["cortes_csv"]
            video.miniatura_txt = artifacts["miniatura"]
            video.status = "done"
            db.commit(); db.refresh(video)

            yield _sse({
                "stage": "done", "progress": 100,
                "message": f"PAQUETE generado: {video.code}",
                "video": _video_to_dict(video, include_artifacts=True),
            })

        except Exception as e:
            logger.exception("Fallo en process_audio_url stream")
            try:
                if video is not None:
                    video.status = "error"
                    video.error_message = f"{type(e).__name__}: {e}"
                    db.commit()
            except Exception:
                pass
            yield _sse({"stage": "error", "error": f"{type(e).__name__}: {e}"})
        finally:
            db.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/videos/{video_id}")
def get_video(video_id: int, db: Session = Depends(get_db)):
    v = db.query(Video).filter(Video.id == video_id).first()
    if not v:
        raise HTTPException(404, "Vídeo no encontrado")
    return _video_to_dict(v, include_artifacts=True)


FILE_MAP = {
    "PAQUETE.md": ("paquete_md", "text/markdown"),
    "transcripcion.txt": ("transcript", "text/plain"),
    "descripcion.txt": ("descripcion_txt", "text/plain"),
    "cortes_editor.csv": ("cortes_csv", "text/csv"),
    "miniatura.txt": ("miniatura_txt", "text/plain"),
    "paquete.json": ("paquete_json", "application/json"),
}


@app.get("/api/videos/{video_id}/files/{filename}")
def download_file(video_id: int, filename: str, db: Session = Depends(get_db)):
    if filename not in FILE_MAP:
        raise HTTPException(400, "Archivo desconocido")
    v = db.query(Video).filter(Video.id == video_id).first()
    if not v:
        raise HTTPException(404, "Vídeo no encontrado")
    attr, mime = FILE_MAP[filename]
    content = getattr(v, attr, None)
    if attr == "paquete_json":
        import json as _json
        content = _json.dumps(content, ensure_ascii=False, indent=2) if content else ""
    if not content:
        raise HTTPException(404, f"Archivo no generado: {filename}")
    return Response(
        content=content,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.patch("/api/videos/{video_id}/checklist")
def set_check(video_id: int, payload: dict, db: Session = Depends(get_db)):
    v = db.query(Video).filter(Video.id == video_id).first()
    if not v:
        raise HTTPException(404, "Vídeo no encontrado")
    item_key = payload.get("item_key")
    done = bool(payload.get("done", False))
    if not item_key:
        raise HTTPException(400, "Falta item_key")
    valid_keys = {x["key"] for x in (v.creator.checklist_template or [])}
    if item_key not in valid_keys:
        raise HTTPException(400, "item_key desconocido para este creator")
    item = db.query(ChecklistItem).filter(
        ChecklistItem.video_id == video_id, ChecklistItem.item_key == item_key,
    ).first()
    if item is None:
        item = ChecklistItem(video_id=video_id, item_key=item_key, done=done)
        db.add(item)
    else:
        item.done = done
    db.commit()
    return {"ok": True, "item_key": item_key, "done": done}


@app.delete("/api/videos/{video_id}/checklist")
def reset_check(video_id: int, db: Session = Depends(get_db)):
    if not db.query(Video).filter(Video.id == video_id).first():
        raise HTTPException(404, "Vídeo no encontrado")
    db.query(ChecklistItem).filter(ChecklistItem.video_id == video_id).update({"done": False})
    db.commit()
    return {"ok": True}


@app.delete("/api/videos/{video_id}")
def delete_video(video_id: int, db: Session = Depends(get_db)):
    v = db.query(Video).filter(Video.id == video_id).first()
    if not v:
        raise HTTPException(404, "Vídeo no encontrado")
    db.delete(v)
    db.commit()
    return {"ok": True}


# ============== Estáticos ==============
# Defensive: si por alguna razón Vercel no incluye el frontend, no crashee al import
if PUBLIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=PUBLIC_DIR), name="static")
else:
    logger.warning(f"No existe PUBLIC_DIR={PUBLIC_DIR}; estáticos no montados")
