"""Vercel Blob client-upload token signing.

Replicates `generateClientTokenFromReadWriteToken` from @vercel/blob (TS SDK)
to permitir que el navegador suba ficheros directos a Blob storage sin pasar
por nuestras funciones serverless (que tienen un límite duro de 4.5 MB).

Protocol:
  1. Cliente (SDK @vercel/blob/client) POST /api/blob/handle-upload con:
       { type: "blob.generate-client-token", payload: { pathname, ... } }
  2. Backend firma un clientToken con BLOB_READ_WRITE_TOKEN y responde:
       { type: "blob.generate-client-token", clientToken: "vercel_blob_client_<storeId>_<sig>.<b64>" }
  3. Cliente usa ese clientToken para PUT directo a blob.vercel-storage.com.
"""
import base64
import hmac
import hashlib
import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

TOKEN_PREFIX_RW = "vercel_blob_rw_"
TOKEN_PREFIX_CLIENT = "vercel_blob_client_"
DEFAULT_TTL_SECONDS = 60 * 60  # 1 hora


class BlobConfigError(RuntimeError):
    pass


def _read_rw_token() -> tuple[str, str]:
    """Devuelve (store_id, secret) extraídos del BLOB_READ_WRITE_TOKEN."""
    rw = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
    if not rw:
        raise BlobConfigError("BLOB_READ_WRITE_TOKEN no configurada")
    if not rw.startswith(TOKEN_PREFIX_RW):
        raise BlobConfigError("BLOB_READ_WRITE_TOKEN no empieza por vercel_blob_rw_")
    rest = rw[len(TOKEN_PREFIX_RW):]
    parts = rest.split("_", 1)
    if len(parts) != 2:
        raise BlobConfigError("Formato BLOB_READ_WRITE_TOKEN inválido")
    return parts[0], parts[1]


def generate_client_token(
    pathname: str,
    *,
    allowed_content_types: Optional[list[str]] = None,
    max_size_bytes: Optional[int] = None,
    add_random_suffix: bool = True,
    cache_control_max_age: Optional[int] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    valid_until_ms: Optional[int] = None,
) -> str:
    """Firma un token de cliente para subir UN archivo concreto a Blob.

    Match con `generateClientTokenFromReadWriteToken` de @vercel/blob:
      - JSON payload con campos opcionales
      - base64 (no base64url) del JSON
      - HMAC-SHA256(secret, payload_b64) en hex
      - String final: `vercel_blob_client_<storeId>_<sig>.<payload_b64>`
    """
    store_id, secret = _read_rw_token()

    if valid_until_ms is None:
        valid_until_ms = int(time.time() * 1000) + ttl_seconds * 1000

    payload: dict = {"pathname": pathname, "validUntil": valid_until_ms}
    if allowed_content_types is not None:
        payload["allowedContentTypes"] = allowed_content_types
    if max_size_bytes is not None:
        payload["maximumSizeInBytes"] = max_size_bytes
    payload["addRandomSuffix"] = add_random_suffix
    if cache_control_max_age is not None:
        payload["cacheControlMaxAge"] = cache_control_max_age

    payload_json = json.dumps(payload, separators=(",", ":"))
    payload_b64 = base64.b64encode(payload_json.encode("utf-8")).decode("ascii")

    sig = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()

    return f"{TOKEN_PREFIX_CLIENT}{store_id}_{sig}.{payload_b64}"


# Tipos MIME aceptados para los uploads de audio
AUDIO_ALLOWED_CONTENT_TYPES = [
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/m4a",
    "audio/x-m4a",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/aac",
    "audio/ogg",
    "audio/flac",
    "audio/webm",
    # Algunos navegadores envían tipos genéricos cuando no detectan
    "application/octet-stream",
]
MAX_AUDIO_BYTES = 250 * 1024 * 1024  # 250 MB
