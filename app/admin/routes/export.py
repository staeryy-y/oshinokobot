from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from ... import db
from ..auth import require_admin
from ..images import resolve_media_path

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@router.get("/export")
async def export_all_data(request: Request) -> Response:
    """Everything the bot knows, as one .zip: data/*.json (one file per
    table — see db.EXPORT_TABLES) plus images/ holding every character
    image actually on disk. Built in memory — fine at this bot's scale
    (one guild's characters/polls/images); if the media library ever grows
    large enough for that to matter, this would need to stream to a temp
    file instead of a BytesIO.
    """
    conn = request.app.state.db
    config = request.app.state.config

    buffer = io.BytesIO()
    generated_at = datetime.now(timezone.utc).isoformat()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        dumped: dict[str, list[dict]] = {}
        for table in db.EXPORT_TABLES:
            rows = await db.dump_table(conn, table)
            dumped[table] = rows
            zf.writestr(f"data/{table}.json", json.dumps(rows, indent=2))

        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "generated_at": generated_at,
                    "tables": {table: len(rows) for table, rows in dumped.items()},
                    "notes": (
                        "data/*.json are verbatim dumps of the bot's SQLite tables. "
                        "Admin-login internals (users, sessions) are deliberately "
                        "excluded. Every id is that table's own row id, usable to "
                        "join across files (e.g. polls[].character_id -> "
                        "characters[].id). characters[].image_path names a file "
                        "under images/ by its basename."
                    ),
                },
                indent=2,
            ),
        )

        # Every image actually referenced by a character row, read straight
        # off disk. A row whose file has since gone missing is skipped
        # rather than failing the whole export; de-duped since nothing stops
        # two characters (in principle) pointing at the same file.
        seen_filenames: set[str] = set()
        for character in dumped["characters"]:
            filename = Path(character["image_path"]).name
            if filename in seen_filenames:
                continue
            seen_filenames.add(filename)
            source = resolve_media_path(config.media_dir, filename)
            if source is not None:
                zf.writestr(f"images/{filename}", source.read_bytes())

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"oshinokobot-export-{stamp}.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
