# Deploy a Vercel — guía paso a paso

Tiempo estimado: ~15 minutos. Te llevará por:

1. Subir el código a GitHub
2. Importar el repo en Vercel
3. Crear la base de datos Neon **desde dentro de Vercel** (no necesitas cuenta separada)
4. Añadir las variables de entorno
5. Verificar que funciona

## 1. GitHub: crea el repo y sube el código

Desde la carpeta `agency-production/`:

```bash
git init
git add .
git commit -m "Initial commit: AgencyProduction multi-tenant"
```

Crea un repo nuevo en GitHub (https://github.com/new). Lo puedes hacer público o privado, tú decides. Apunta el remote:

```bash
git remote add origin https://github.com/TU_USUARIO/agency-production.git
git branch -M main
git push -u origin main
```

> **No subas tu `.env` real**. Ya está en `.gitignore`, pero verifica con `git status` antes del push.

## 2. Vercel: importa el repo

1. Ve a https://vercel.com/new
2. Click **Import Git Repository** → autoriza GitHub → selecciona `agency-production`
3. En la pantalla de configuración:
   - **Framework Preset**: `Other` (Vercel detectará Python automático)
   - **Root Directory**: déjalo en `./`
   - **Build/Output**: déjalo todo por defecto
4. **No hagas Deploy todavía** — primero las env vars y la BD.

## 3. Neon Postgres: crear la BD desde Vercel

En el dashboard de tu proyecto Vercel:

1. Pestaña **Storage** → **Create Database**
2. Elige **Neon** (Postgres). Te aparece un wizard:
   - Database name: `agency-prod-db`
   - Region: la más cercana a ti (Frankfurt si estás en España)
3. Click **Create**. Vercel:
   - Te crea la BD en Neon (no necesitas registrarte aparte; usa OAuth automático)
   - **Auto-añade la variable de entorno `DATABASE_URL` a tu proyecto**

Para verlo: pestaña **Settings → Environment Variables** → verás `DATABASE_URL` ya seteada con un valor `postgres://…`.

## 4. Variables de entorno

En **Settings → Environment Variables**, añade:

| Nombre | Valor | Obligatorio |
|---|---|---|
| `ANTHROPIC_API_KEY` | tu API key real de Anthropic | ✅ |
| `DATABASE_URL` | ya está, autogenerada por Neon | ✅ (auto) |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` (o `claude-opus-4-6`) | opcional |
| `SITE_PASSWORD` | déjalo vacío (sin auth, como pediste) | opcional |
| `SESSION_SECRET` | un string aleatorio largo (genera con `openssl rand -hex 32`) | recomendado |

> Si más adelante quieres activar la contraseña: pones `SITE_PASSWORD=algo` y redeploy. Eso es todo.

## 5. Deploy

1. Pestaña **Deployments** → **Redeploy** (o haz un `git push` cualquiera y se dispara solo)
2. Espera ~2 min
3. Cuando termine, click el dominio (algo como `agency-production-xxx.vercel.app`)
4. Debería abrir el hub AgencyProduction con la tarjeta de Marcelo Gullo + los 3 placeholders

## 6. Verifica

- Click la tarjeta de Marcelo → se abre el sub-dashboard
- Pega cualquier texto en el textarea (ej. la transcripción de un vídeo viejo)
- Click "Generar PAQUETE"
- Espera 15-30s → debería aparecer el vídeo en la lista con sus archivos descargables

## Si algo falla

**"500 Internal Server Error"**
- Pestaña **Logs** en Vercel → busca el traceback de Python
- 90% de las veces es: `ANTHROPIC_API_KEY` mal escrita o sin el `sk-ant-` al inicio

**La BD parece vacía**
- Las funciones lazy crean tablas al primer request, pero a veces el `on_startup` tarda
- Visita `/api/health` primero. Luego `/api/creators` — debería listar Marcelo + placeholders

**Falla la primera vez pero las siguientes funcionan**
- Cold start. Normal en serverless. La primera request "despierta" la función Python. Después va rápido.

## Dominio personalizado (opcional)

Si tienes un dominio propio (ej. `agencyproduction.com`):
- **Settings → Domains** → Add → escribe tu dominio
- Vercel te da el CNAME / A record que tienes que añadir en tu DNS
- En 5-30 min está activo con HTTPS automático

## Costes esperables

| Servicio | Free tier | Cuándo pasa a pago |
|---|---|---|
| Vercel | Hobby plan gratis ilimitado para uso personal | Si invitas equipo + features de prod ($20/mes Pro) |
| Neon | 0.5 GB / 1 BD activa | ~25.000 vídeos sin pagar nada |
| Anthropic Claude | $0 fijo | ~$0.10-0.50 por PAQUETE generado (uso real) |

Para tu volumen (~2 vídeos × 4 creators × 4 semanas = 32 PAQUETEs/mes) calcula ~$5-15/mes de Anthropic. El resto, gratis.

## Actualizar el código

Cada `git push` a `main` despliega automático. ~2 min y está live.

Para añadir un nuevo creator: edita `backend/creators/`, push, y aparecerá en el hub.
