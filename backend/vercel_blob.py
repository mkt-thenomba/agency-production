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


def _read_rw_token() -> str:
    """Devuelve el BLOB_READ_WRITE_TOKEN tal cual (lo usamos COMPLETO como clave HMAC)."""
    rw = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
    if not rw:
        raise BlobConfigError("BLOB_READ_WRITE_TOKEN no configurada")
    if not rw.startswith(TOKEN_PREFIX_RW):
        raise BlobConfigError("BLOB_READ_WRITE_TOKEN no empieza por vercel_blob_rw_")
    return rw


def _parse_store_id(rw_token: str) -> str:
    """Replica `parseStoreIdFromReadWriteToken`: split('_')[3]."""
    parts = rw_token.split("_")
    if len(parts) < 5:
        raise BlobConfigError("Formato BLOB_READ_WRITE_TOKEN inválido")
    return parts[3]


def generate_client_token(
    pathname: str,
    *,
    allowed_content_types: Optional[list[str]] = None,
    max_size_bytes: Optional[int] = None,
    add_random_suffix: bool = True,
    cache_control_max_age: Optional[int] = None,
    ttl_seconds: int = 30,
    valid_until_ms: Optional[int] = None,
) -> str:
    """Replica EXACTA de @vercel/blob `generateClientTokenFromReadWriteToken`.

    Algoritmo (verificado contra packages/blob/src/client.ts):
      1. JSON.stringify({...args, validUntil}) → utf-8
      2. base64(json) → `payload`
      3. HMAC-SHA256 con el rw-token COMPLETO como clave, sobre `payload`
         → digest hex → `securedKey`
      4. concat `${securedKey}.${payload}` → utf-8
      5. base64(concat)
      6. Token final: `vercel_blob_client_${storeId}_${step5}`

    El SDK default ttl es 30 segundos (suficiente porque el PUT empieza
    inmediatamente y la validación es al inicio del request).
    """
    rw_token = _read_rw_token()
    store_id = _parse_store_id(rw_token)

    if valid_until_ms is None:
        valid_until_ms = int(time.time() * 1000) + ttl_seconds * 1000

    args: dict = {"pathname": pathname}
    if allowed_content_types is not None:
        args["allowedContentTypes"] = allowed_content_types
    if max_size_bytes is not None:
        args["maximumSizeInBytes"] = max_size_bytes
    args["addRandomSuffix"] = add_random_suffix
    if cache_control_max_age is not None:
        args["cacheControlMaxAge"] = cache_control_max_age
    args["validUntil"] = valid_until_ms

    payload_json = json.dumps(args, separators=(",", ":"))
    payload_b64 = base64.b64encode(payload_json.encode("utf-8")).decode("ascii")

    # HMAC-SHA256 con rw_token COMPLETO como clave (no solo la parte secret)
    secured_key = hmac.new(
        rw_token.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()

    # base64(`${securedKey}.${payload}`) — todo junto, NO `sig.payload` literal
    combined = f"{secured_key}.{payload_b64}"
    combined_b64 = base64.b64encode(combined.encode("utf-8")).decode("ascii")

    return f"{TOKEN_PREFIX_CLIENT}{store_id}_{combined_b64}"


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
