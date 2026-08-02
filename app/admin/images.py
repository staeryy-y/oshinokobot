from __future__ import annotations

import base64
import binascii
import uuid
from pathlib import Path

_MIME_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


def extension_for_mime(mime: str | None) -> str | None:
    if mime is None:
        return None
    return _MIME_EXTENSIONS.get(mime.lower())


def save_image_bytes(media_dir: str, data: bytes, mime: str | None) -> str:
    ext = extension_for_mime(mime)
    if ext is None:
        raise ValueError(f"unsupported image type: {mime!r}")
    directory = Path(media_dir)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    path = directory / filename
    path.write_bytes(data)
    return str(path)


def decode_base64_image(raw_base64: str) -> bytes:
    try:
        return base64.b64decode(raw_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"invalid base64: {exc}") from exc


def resolve_media_path(media_dir: str, filename: str) -> Path | None:
    """filename is always a bare uuid-hex.ext this module generated itself
    (see save_image_bytes) — reject anything containing a path separator
    so this can't be walked outside media_dir. Returns None for an unsafe
    or nonexistent filename rather than raising, since both the admin and
    public media routes just want a clean 404/400 either way."""
    if "/" in filename or "\\" in filename or filename in (".", ".."):
        return None
    path = Path(media_dir) / filename
    return path if path.is_file() else None
