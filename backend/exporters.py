"""Generan los artefactos (PAQUETE.md, descripcion.txt, cortes.csv, miniatura.txt)
como STRINGS, no como archivos. Para Vercel almacenamos en BD."""
import csv
import io
import json
import re
import unicodedata
from datetime import datetime, time, timedelta
from typing import Optional


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip()
    text = re.sub(r"[\s-]+", "_", text)
    return text or "video"


def clean_title_from_input(raw: Optional[str]) -> str:
    if not raw:
        return "Vídeo sin título"
    name = raw.strip()
    name = re.sub(r"\.[a-zA-Z0-9]{1,5}$", "", name)  # remueve extensión si la pegó
    name = re.sub(r"V\d{2,3}", "", name, flags=re.I)
    name = re.sub(r"[-_]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return "Vídeo sin título"
    return " ".join(w.capitalize() if not w.isupper() else w for w in name.split())


def detect_type(hint: str, type_keywords: dict, default_type: str = "historia") -> str:
    low = (hint or "").lower()
    for vtype, keywords in (type_keywords or {}).items():
        if any(k in low for k in keywords):
            return vtype
    return default_type


def extract_code(hint: str, prefix: str = "V") -> Optional[str]:
    if not hint:
        return None
    pattern = re.compile(rf"{re.escape(prefix)}(\d{{2,3}})", re.I)
    m = pattern.search(hint)
    if m:
        return f"{prefix}{int(m.group(1)):02d}"
    return None


def next_available_code(existing_codes, prefix: str = "V") -> str:
    used = set()
    pattern = re.compile(rf"{re.escape(prefix)}(\d+)", re.I)
    for c in existing_codes or []:
        m = pattern.match(c or "")
        if m:
            used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return f"{prefix}{n:02d}"


def next_weekday(weekday: int, hour: int = 10, base: datetime = None) -> datetime:
    base = base or datetime.now()
    days_ahead = (weekday - base.weekday()) % 7
    if days_ahead == 0 and base.time() >= time(hour, 0):
        days_ahead = 7
    target = base + timedelta(days=days_ahead)
    return target.replace(hour=hour, minute=0, second=0, microsecond=0)


def suggest_publish_at(video_type: str, schedule: dict) -> Optional[datetime]:
    if not schedule:
        return None
    rule = schedule.get(video_type)
    if not rule:
        return None
    return next_weekday(rule.get("weekday", 1), rule.get("hour", 10))


# ---------- generación de artefactos ----------

def render_descripcion(paquete: dict) -> str:
    return (paquete.get("description", "") or "").strip() + "\n"


def render_cortes_csv(paquete: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "Tipo", "Numero", "Titulo", "IN", "OUT", "Duracion",
        "Frase_entrada", "Texto_a_quemar",
    ])
    for i, m in enumerate(paquete.get("midform", []) or [], 1):
        w.writerow([
            "midform", i, m.get("title", ""), m.get("in", ""), m.get("out", ""),
            m.get("duration", ""), m.get("phrase_in", ""), m.get("burn_text", ""),
        ])
    for i, s in enumerate(paquete.get("shorts", []) or [], 1):
        w.writerow([
            "short", i, s.get("title", ""), s.get("in", ""), s.get("out", ""),
            s.get("duration", ""), s.get("phrase_in", ""), s.get("burn_text", ""),
        ])
    return buf.getvalue()


def render_miniatura(paquete: dict, thumb_templates: dict) -> str:
    tpl = paquete.get("thumb_template", 1)
    tpl_name = thumb_templates.get(int(tpl), "N/D") if thumb_templates else "N/D"
    lines = [
        f"Plantilla: {tpl} — {tpl_name}",
        f"Texto A: {paquete.get('thumb_textA', '')}",
        f"Texto B: {paquete.get('thumb_textB', '')}",
        "",
        "Prompt image-gen (inglés):",
        (paquete.get("thumb_prompt", "") or "").strip(),
    ]
    return "\n".join(lines) + "\n"


def render_paquete_md(code: str, type_: str, duration: str,
                     paquete: dict, thumb_templates: dict) -> str:
    title = paquete.get("title", "")
    alts = list(paquete.get("alternatives", []) or [])
    while len(alts) < 3:
        alts.append("—")

    out = []
    out.append(f"# PAQUETE — {code}\n")
    out.append("## A. METADATOS")
    out.append(f"- Vídeo: {code}")
    out.append(f"- Tema: {title}")
    out.append(f"- Tipo: {type_}")
    out.append(f"- Duración bruto: {duration}\n")

    out.append("## B. TÍTULO PRINCIPAL")
    out.append(f"**{title}** *({len(title)} caracteres)*\n")

    out.append("## C. ALTERNATIVAS A/B")
    for i, alt in enumerate(alts[:3], 1):
        out.append(f"{i}. {alt}")
    out.append("")

    out.append("## D. DESCRIPCIÓN YOUTUBE\n")
    out.append("```")
    out.append((paquete.get("description", "") or "").strip())
    out.append("```\n")

    chapters = (paquete.get("chapters", "") or "").strip()
    if chapters:
        out.append("## E. CAPÍTULOS")
        out.append(chapters + "\n")

    tags = (paquete.get("tags", "") or "").strip()
    if tags:
        out.append("## F. TAGS")
        out.append(tags + "\n")

    pinned = (paquete.get("pinned_comment", "") or "").strip()
    if pinned:
        out.append("## G. COMENTARIO FIJADO")
        out.append(pinned + "\n")

    out.append("## H. MINIATURA")
    tpl = paquete.get("thumb_template", 1)
    tpl_name = thumb_templates.get(int(tpl), "N/D") if thumb_templates else "N/D"
    out.append(f"- Plantilla fondo: {tpl} — {tpl_name}")
    out.append(f"- Texto A: **{paquete.get('thumb_textA', '')}**")
    out.append(f"- Texto B: **{paquete.get('thumb_textB', '')}**\n")
    out.append("**Prompt image-gen:**\n")
    out.append("```")
    out.append((paquete.get("thumb_prompt", "") or "").strip())
    out.append("```\n")

    midforms = paquete.get("midform", []) or []
    if midforms:
        out.append("## I. MIDFORM\n")
    for i, m in enumerate(midforms, 1):
        out.append(f"### Midform {i} — {m.get('title','')}")
        out.append(f"- **IN:** `{m.get('in','')}` · **OUT:** `{m.get('out','')}` · **Duración:** {m.get('duration','')}")
        out.append(f"- **Frase entrada:** *\"{m.get('phrase_in','')}\"*")
        out.append(f"- **Frase salida:** *\"{m.get('phrase_out','')}\"*")
        out.append(f"- **Miniatura:** `{m.get('burn_text','')}`")
        thumb = (m.get("thumb_prompt") or "").strip()
        if thumb:
            out.append("- **Prompt imagen (en):**")
            out.append("```")
            out.append(thumb)
            out.append("```")
        out.append("")  # línea en blanco

    shorts = paquete.get("shorts", []) or []
    if shorts:
        out.append("## J. SHORTS\n")
        for i, s in enumerate(shorts, 1):
            out.append(f"### Short {i} — {s.get('title','')}")
            out.append(f"- **IN:** `{s.get('in','')}` · **OUT:** `{s.get('out','')}` · **Dur:** {s.get('duration','')}")
            out.append(f"- **Frase:** *\"{s.get('phrase_in','')}\"*")
            out.append(f"- **Burn:** `{s.get('burn_text','')}`")
            out.append(f"- **Por qué funciona:** {s.get('why_works','')}\n")

    trailer = paquete.get("trailer") or {}
    trailer_clips = trailer.get("clips") if isinstance(trailer, dict) else None
    if isinstance(trailer_clips, list) and trailer_clips:
        total = sum(_parse_seconds(c.get("duration", "")) or 0 for c in trailer_clips)
        target = trailer.get("target_seconds", 30)
        arc = trailer.get("narrative_arc", "")
        out.append(f"## L. TRAILER ({total}s aprox · objetivo {target}s)\n")
        if arc:
            out.append(f"**Arco:** {arc}\n")
        for c in sorted(trailer_clips, key=lambda x: x.get("order", 0)):
            out.append(
                f"### Clip {c.get('order','?')} · {c.get('role','')} · "
                f"`{c.get('in','')}` → `{c.get('out','')}` ({c.get('duration','')})"
            )
            out.append(f"- **IN literal:** *\"{c.get('phrase_in','')}\"*")
            out.append(f"- **OUT literal:** *\"{c.get('phrase_out','')}\"*")
            if c.get("why_here"):
                out.append(f"- **Por qué aquí:** {c['why_here']}")
            out.append("")

    alerts = paquete.get("alerts", []) or []
    if alerts:
        out.append("## K. ALERTAS DE MONETIZACIÓN\n")
        out.append("| Timestamp | Sección | Riesgo | Ajuste |")
        out.append("|---|---|---|---|")
        for a in alerts:
            out.append(
                f"| {a.get('timestamp','')} | {a.get('section','')} "
                f"| {a.get('risk','')} | {a.get('adjustment','')} |"
            )

    return "\n".join(out) + "\n"


def _parse_seconds(dur: str):
    """Convierte 'MM:SS' o 'SS' o 'M:SS' a segundos int. None si no parsea."""
    if not dur or not isinstance(dur, str):
        return None
    parts = dur.strip().split(":")
    try:
        if len(parts) == 1:
            return int(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        pass
    return None


def render_all(code: str, type_: str, duration: str, paquete: dict,
               thumb_templates: dict) -> dict:
    return {
        "paquete_md": render_paquete_md(code, type_, duration, paquete, thumb_templates),
        "descripcion": render_descripcion(paquete),
        "cortes_csv": render_cortes_csv(paquete),
        "miniatura": render_miniatura(paquete, thumb_templates),
        "paquete_json_str": json.dumps(paquete, ensure_ascii=False, indent=2),
    }
