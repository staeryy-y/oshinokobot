from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from ... import db
from ..auth import require_admin
from ..templating import templates

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@router.get("/polls", response_class=HTMLResponse)
async def polls_page(request: Request) -> HTMLResponse:
    conn = request.app.state.db
    polls = await db.list_polls(conn, limit=50)
    return templates.TemplateResponse(request, "polls.html", {"polls": polls})


@router.get("/polls/{poll_id}", response_class=HTMLResponse)
async def poll_detail_page(request: Request, poll_id: int) -> HTMLResponse:
    conn = request.app.state.db
    poll = await db.get_poll(conn, poll_id)
    if poll is None:
        raise HTTPException(404, "No such poll")

    character = await db.get_character(conn, poll["character_id"])
    tier_counts = await db.get_tier_vote_counts(conn, poll_id)
    appeal_counts = await db.get_appeal_vote_counts(conn, poll_id)
    tags_by_id = {t["id"]: t["name"] for t in await db.list_tags(conn)}
    appeal_rows = sorted(
        ((tags_by_id.get(tag_id, "unknown tag"), count) for tag_id, count in appeal_counts.items()),
        key=lambda row: row[1],
        reverse=True,
    )

    return templates.TemplateResponse(
        request,
        "poll_detail.html",
        {
            "poll": poll,
            "character": character,
            "tier_counts": tier_counts,
            "tiers": ["S", "A", "B", "C", "D"],
            "appeal_rows": appeal_rows,
        },
    )
