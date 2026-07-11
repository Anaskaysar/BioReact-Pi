#!/usr/bin/env python3
"""Start the BioReact-Pi dashboard server."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("cloud.api.main:app", host="0.0.0.0", port=8000, reload=True)
