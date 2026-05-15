"""FastAPI multi-tenant: hub + sub-dashboard por creator."""
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Request, Response, Form
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .database import init_db, get_db
from .models import Creator, Video, ChecklistItem
from .seed import seed_creators
from .auth import (
    PasswordGate, password_required, make_session_token, is_valid_session,
    COOKIE_NAME, LOGIN_PATH,
)
from .transcript_parser import parse_transcript, format_mmss
from .claude_client import generate_paquete
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


@app.post("/api/creators/{slug}/process")
def process_transcript(slug: str, payload: dict, db: Session = Depends(get_db)):
    """Procesa un transcript inmediatamente (síncrono, ~15-30s).
    Body: { "title_hint": "...", "transcript": "..." (texto o JSON AssemblyAI) }"""
    c = db.query(Creator).filter(Creator.slug == slug).first()
    if not c:
        raise HTTPException(404, "Creator no encontrado")

    raw_transcript = (payload.get("transcript") or "").strip()
    if not raw_transcript:
        raise HTTPException(400, "Falta el transcript")

    title_hint = payload.get("title_hint") or ""
    type_hint = payload.get("type_hint") or ""

    # Parse del transcript
    parsed, duration_s, source = parse_transcript(raw_transcript)
    duration_str = format_mmss(duration_s) if duration_s > 0 else "—"

    # Metadatos
    cfg = c.config or {}
    title = clean_title_from_input(title_hint)
    vtype = type_hint or detect_type(title_hint, cfg.get("type_keywords", {}),
                                     cfg.get("default_type", "historia"))

    existing = [vv.code for (vv,) in db.query(Video.code).filter(Video.creator_id == c.id).all()]
    code = extract_code(title_hint, c.code_prefix) or next_available_code(existing, c.code_prefix)
    if code in existing:
        code = next_available_code(existing, c.code_prefix)
    slug_v = slugify(title)

    # Reserva fila inicial
    video = Video(
        creator_id=c.id,
        code=code,
        slug=slug_v,
        title=title,
        type=vtype,
        duration=duration_str,
        status="processing",
        transcript=parsed,
        transcript_source=source,
        suggested_publish_at=suggest_publish_at(vtype, cfg.get("publishing_schedule", {})),
    )
    db.add(video)
    db.commit()
    db.refresh(video)

    # Siembra checklist
    for tpl in c.checklist_template or []:
        db.add(ChecklistItem(video_id=video.id, item_key=tpl["key"], done=False))
    db.commit()

    # Genera paquete con Claude
    try:
        paquete = generate_paquete(
            system_prompt=c.system_prompt,
            user_template=c.user_template,
            code=code, title=title, type_=vtype,
            duration=duration_str, transcript=parsed,
        )
    except Exception as e:
        logger.error(f"Falló Claude para video {video.id}: {e}")
        video.status = "error"
        video.error_message = f"{type(e).__name__}: {e}"
        db.commit()
        raise HTTPException(500, f"Error generando PAQUETE: {e}")

    # Renderiza artefactos
    artifacts = render_all(code, vtype, duration_str, paquete,
                          cfg.get("thumb_templates", {}))
    video.paquete_json = paquete
    video.paquete_md = artifacts["paquete_md"]
    video.descripcion_txt = artifacts["descripcion"]
    video.cortes_csv = artifacts["cortes_csv"]
    video.miniatura_txt = artifacts["miniatura"]
    video.status = "done"
    db.commit()

    return _video_to_dict(video, include_artifacts=True)


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
