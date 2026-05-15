"""Middleware opcional de password compartido.
Si SITE_PASSWORD está vacío en el entorno, la app es pública (sin login)."""
import logging
import os
from typing import Optional

from itsdangerous import BadSignature, URLSafeSerializer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse, Response

logger = logging.getLogger(__name__)

COOKIE_NAME = "agency_session"
LOGIN_PATH = "/login"
PUBLIC_PATHS = (LOGIN_PATH, "/api/login", "/api/health", "/static")


def _serializer() -> URLSafeSerializer:
    secret = os.environ.get("SESSION_SECRET") or "dev-insecure-secret-change-me"
    return URLSafeSerializer(secret, salt="agency-session-v1")


def password_required() -> bool:
    return bool(os.environ.get("SITE_PASSWORD", "").strip())


def make_session_token() -> str:
    return _serializer().dumps({"ok": True})


def is_valid_session(token: Optional[str]) -> bool:
    if not token:
        return False
    try:
        data = _serializer().loads(token)
        return bool(data.get("ok"))
    except BadSignature:
        return False


class PasswordGate(BaseHTTPMiddleware):
    """Si SITE_PASSWORD está set, exige cookie firmada para todas las rutas
    salvo /login y /api/login."""

    async def dispatch(self, request, call_next):
        if not password_required():
            return await call_next(request)

        path = request.url.path
        if any(path == p or path.startswith(p + "/") or path.startswith(p) for p in PUBLIC_PATHS):
            return await call_next(request)

        token = request.cookies.get(COOKIE_NAME)
        if is_valid_session(token):
            return await call_next(request)

        if path.startswith("/api/"):
            return Response("Auth required", status_code=401)
        # Para HTML, redirigimos al login
        return RedirectResponse(url=f"{LOGIN_PATH}?next={path}", status_code=302)
