#!/usr/bin/env python3
"""Start the BioReact-Pi dashboard server."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn

from ui.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "ui.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        reload_dirs=[str(ROOT / "ui")],
    )
