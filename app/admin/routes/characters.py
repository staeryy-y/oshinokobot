from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from ... import db
from ..auth import require_admin
from ..images import decode_base64_image, extension_for_mime, resolve_media_path, save_image_bytes
from ..templating import templates

logger = logging.getLogger("oshinokobot.admin.characters")

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

# Guardrail, not a product constraint: keeps one import request's memory and
# transaction footprint sane. Nothing about the design requires this number.
MAX_IMPORT_BATCH = 200


async def _current_user_id(request: Request, username: str) -> int | None:
    user = await db.get_user_by_username(request.app.state.db, username)
    return user["id"] if user is not None else None


@router.get("/characters", response_class=HTMLResponse)
async def list_characters_page(request: Request) -> HTMLResponse:
    conn = request.app.state.db
    characters = await db.list_characters(conn)
    return templates.TemplateResponse(
        request, "characters.html", {"characters": characters}
    )


@router.post("/characters", response_class=HTMLResponse)
async def create_character(
    request: Request,
    name: Annotated[str, Form()],
    series: Annotated[str, Form()] = "",
    image: UploadFile = File(...),
    username: str = Depends(require_admin),
) -> HTMLResponse:
    conn = request.app.state.db
    config = request.app.state.config

    ext = extension_for_mime(image.content_type)
    if ext is None:
        raise HTTPException(400, f"Unsupported image type: {image.content_type}")

    data = await image.read()
    image_path = save_image_bytes(config.media_dir, data, image.content_type)

    user_id = await _current_user_id(request, username)
    await db.create_character(
        conn,
        name=name.strip(),
        series=series.strip() or None,
        image_path=image_path,
        source_url=None,
        uploaded_by=user_id,
    )

    characters = await db.list_characters(conn)
    return templates.TemplateResponse(
        request, "_character_list.html", {"characters": characters}
    )


@router.delete("/characters/{character_id}", response_class=HTMLResponse)
async def delete_character(request: Request, character_id: int) -> HTMLResponse:
    conn = request.app.state.db
    await db.delete_character(conn, character_id)
    characters = await db.list_characters(conn)
    return templates.TemplateResponse(
        request, "_character_list.html", {"characters": characters}
    )


@router.get("/media/{filename}")
async def get_media(request: Request, filename: str) -> FileResponse:
    path = resolve_media_path(request.app.state.config.media_dir, filename)
    if path is None:
        raise HTTPException(404, "Not found")
    return FileResponse(path)


@router.post("/characters/import", response_class=HTMLResponse)
async def import_characters(
    request: Request,
    import_file: UploadFile | None = File(None),
    import_text: Annotated[str, Form()] = "",
    username: str = Depends(require_admin),
) -> HTMLResponse:
    conn = request.app.state.db
    config = request.app.state.config

    if import_file is not None and import_file.filename:
        raw = (await import_file.read()).decode("utf-8", errors="replace")
    elif import_text.strip():
        raw = import_text
    else:
        return _import_response(request, error="Provide a JSON file or paste JSON to import.")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _import_response(request, error=f"Invalid JSON: {exc}")

    if not isinstance(payload, dict) or not isinstance(payload.get("characters"), list):
        return _import_response(
            request, error='Expected a top-level object with a "characters" array.'
        )

    entries = payload["characters"]
    if len(entries) > MAX_IMPORT_BATCH:
        return _import_response(
            request, error=f"Batch too large: {len(entries)} entries (max {MAX_IMPORT_BATCH})."
        )

    user_id = await _current_user_id(request, username)
    results = []
    for idx, entry in enumerate(entries, start=1):
        label = entry.get("name") if isinstance(entry, dict) else None
        label = label if isinstance(label, str) and label else f"entry #{idx}"
        try:
            if not isinstance(entry, dict):
                raise ValueError("not a JSON object")

            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("missing required field 'name'")

            image_b64 = entry.get("image_base64")
            if not isinstance(image_b64, str) or not image_b64:
                raise ValueError("missing required field 'image_base64'")

            series = entry.get("series")
            if series is not None and not isinstance(series, str):
                raise ValueError("'series' must be a string")
            series = series.strip() if series else None

            source_url = entry.get("source_url")
            if source_url is not None and not isinstance(source_url, str):
                raise ValueError("'source_url' must be a string")

            mime = entry.get("image_mime", "image/png")
            if not isinstance(mime, str):
                raise ValueError("'image_mime' must be a string")

            existing = await db.find_character_by_name_series(conn, name=name, series=series)
            if existing is not None:
                results.append({"name": label, "status": "skipped", "detail": "duplicate"})
                continue

            image_bytes = decode_base64_image(image_b64)
            image_path = save_image_bytes(config.media_dir, image_bytes, mime)
            await db.create_character(
                conn,
                name=name.strip(),
                series=series,
                image_path=image_path,
                source_url=source_url,
                uploaded_by=user_id,
            )
            results.append({"name": label, "status": "imported", "detail": ""})
        except ValueError as exc:
            results.append({"name": label, "status": "error", "detail": str(exc)})

    characters = await db.list_characters(conn)
    body = templates.env.get_template("_import_results.html").render(
        request=request, results=results
    )
    body += templates.env.get_template("_character_list_oob.html").render(
        request=request, characters=characters
    )
    return HTMLResponse(body)


def _import_response(request: Request, *, error: str) -> HTMLResponse:
    body = templates.env.get_template("_import_results.html").render(request=request, error=error)
    return HTMLResponse(body)
