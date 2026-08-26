"""Vercel static file handler for CSS, JS, and favicon."""
import os
from pathlib import Path

STATIC_DIR = Path(__file__).parent.parent / "static"

MIME_TYPES = {
    ".css": "text/css",
    ".js": "application/javascript",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json",
}

def handler(request, response):
    """Serve static files."""
    path = request.path.lstrip("/")
    file_path = STATIC_DIR / path

    if not file_path.exists() or not file_path.is_file():
        response.status_code = 404
        return response

    ext = file_path.suffix.lower()
    content_type = MIME_TYPES.get(ext, "application/octet-stream")

    response.headers["Content-Type"] = content_type
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    response.content = file_path.read_bytes()
    return response
