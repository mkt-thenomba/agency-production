#!/usr/bin/env python3
"""Launcher local — abre navegador automáticamente."""
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path


def main():
    root = Path(__file__).parent
    env_path = root / ".env"
    if not env_path.exists():
        print("[!] No se encontró .env")
        print(f"    cp {root}/.env.example {env_path}")
        print("    y rellena ANTHROPIC_API_KEY")
        sys.exit(1)

    from dotenv import load_dotenv
    load_dotenv(env_path, override=True)

    if not os.environ.get("ANTHROPIC_API_KEY") or "tu-clave" in os.environ.get("ANTHROPIC_API_KEY", ""):
        print("[!] ANTHROPIC_API_KEY no está configurada en .env")
        sys.exit(1)

    port = int(os.environ.get("PORT", "8765"))
    url = f"http://127.0.0.1:{port}"

    def open_browser():
        time.sleep(1.5)
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()

    print(f"\n  AgencyProduction")
    print(f"  Abriendo {url} ...\n")

    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
