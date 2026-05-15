"""Entry point para Vercel — expone la app FastAPI."""
import sys
from pathlib import Path

# Asegura que el módulo `backend` es importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.main import app  # noqa: E402,F401
