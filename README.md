# AgencyProduction

Plataforma multi-tenant para gestionar la producción de varios canales de YouTube. Cada creator (Marcelo Gullo, Raíces de Europa, José Ballesteros, Gonzalo Rodríguez…) tiene su sub-dashboard con su propia voz de marca, libros, plantillas de miniatura y checklist.

**Input**: transcripción ya hecha (JSON de AssemblyAI, SRT, o texto con/sin timestamps). **Output**: PAQUETE.md + descripción + capítulos + CSV de cortes + miniatura + checklist de seguimiento — todo generado por Claude en 15-30 segundos.

## Stack

- **Backend**: FastAPI (Python) — corre tanto en local como en Vercel Python runtime.
- **DB**: SQLite en local, Postgres (Neon) en producción.
- **Frontend**: HTML/CSS/JS plano. Sin frameworks.
- **AI**: Claude API (Anthropic).
- **Auth opcional**: contraseña compartida vía cookie firmada.

## Uso en local

```bash
cd agency-production
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edita .env y añade ANTHROPIC_API_KEY
python run.py
```

Abre http://127.0.0.1:8765. Marcelo Gullo ya viene preconfigurado.

## Despliegue en Vercel

Guía paso a paso en [DEPLOY.md](./DEPLOY.md).

Resumen:

1. `git init && git add . && git commit -m "init"`
2. Crea repo en GitHub y `git push`
3. En Vercel: **Add New → Project** → importa el repo
4. En el panel del proyecto: **Storage → Create Database → Neon** (Postgres). Auto-conecta `DATABASE_URL`.
5. **Settings → Environment Variables**: añade `ANTHROPIC_API_KEY`. Opcional: `SITE_PASSWORD` para proteger con contraseña, `SESSION_SECRET` para firmar cookies.
6. Redeploy. Live.

## Añadir un nuevo creator

Edita `backend/creators/marcelo_gullo.py` como plantilla y crea `backend/creators/raices_europa.py` (o donde toque). Luego añádelo a `backend/creators/__init__.py`. Al reiniciar la app se siembra solo.

Si solo quieres editar a uno existente (cambiar system prompt, libros, plantillas de miniatura, checklist), modifica su archivo en `backend/creators/` y reinicia. La siembra es idempotente y actualiza la config existente sin perder vídeos.

## Multi-tenant: cómo funciona

- `Creator` tiene: `slug`, `name`, `system_prompt`, `user_template`, `checklist_template`, `config` (JSON con thumb_templates, type_keywords, schedule).
- Cuando procesas un transcript, las rutas van a `/api/creators/{slug}/process` y todo lo que genera Claude es específico de ese creator.
- Los archivos generados (PAQUETE.md, CSV, miniatura.txt) se guardan **como columnas TEXT/JSONB** en la BD, no como archivos en disco. Por eso funciona en Vercel sin storage adicional.

## Formato de transcripción aceptado

| Formato | Detección | Calidad de cortes |
|---|---|---|
| JSON de AssemblyAI (con `utterances` o `words`) | Auto si empieza por `{` | Excelente (timestamps al ms) |
| SRT / VTT | Auto si detecta el patrón `HH:MM:SS,mmm --> HH:MM:SS,mmm` | Excelente |
| Texto con timestamps tipo `[MM:SS] línea` | Auto si la primera línea matchea | Excelente |
| Texto con `HH:MM:SS texto` al inicio de cada línea | Auto | Bueno |
| Texto plano sin timestamps | Fallback | Pobre (Claude infiere) |

## Auth

- **Sin password (default)**: la app es pública. Cualquiera con la URL puede usarla.
- **Con password**: pon `SITE_PASSWORD=algoSeguro` en `.env` (local) o env vars de Vercel (prod). La primera visita pide el password una vez, queda en cookie por 30 días.

## Líneas rojas (Marcelo Gullo)

1. No endosar partidos ni candidatos.
2. No clickbait barato.
3. No inventar contenido fuera del transcript.
4. Timestamps MM:SS exactos.
5. Títulos 60-65 caracteres.
