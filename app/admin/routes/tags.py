from __future__ import annotations

from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from ... import db
from ..auth import require_admin
from ..templating import templates

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@router.get("/tags", response_class=HTMLResponse)
async def tags_page(request: Request) -> HTMLResponse:
    conn = request.app.state.db
    tags = await db.list_tags(conn)
    return templates.TemplateResponse(request, "tags.html", {"tags": tags})


@router.post("/tags", response_class=HTMLResponse)
async def create_tag(request: Request, name: Annotated[str, Form()]) -> HTMLResponse:
    conn = request.app.state.db
    name = name.strip()

    error = None
    if not name:
        error = "Tag name cannot be empty."
    elif await db.get_tag_by_name(conn, name) is not None:
        error = f"Tag {name!r} already exists."
    else:
        await db.create_tag(conn, name=name)

    tags = await db.list_tags(conn)
    return templates.TemplateResponse(request, "_tag_list.html", {"tags": tags, "error": error})


@router.delete("/tags/{tag_id}", response_class=HTMLResponse)
async def delete_tag(request: Request, tag_id: int) -> HTMLResponse:
    conn = request.app.state.db
    error = None
    try:
        await db.delete_tag(conn, tag_id)
    except aiosqlite.IntegrityError:
        # FK-referenced by appeal_votes from a past poll — deleting would
        # orphan that poll's history, so this is a hard no rather than a
        # cascade. Retiring a tag means just not using it going forward.
        error = "Can't delete — this tag has votes recorded against it in a past poll."

    tags = await db.list_tags(conn)
    return templates.TemplateResponse(request, "_tag_list.html", {"tags": tags, "error": error})
