from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import db
from ..auth import require_admin
from ..poll_results import build_voter_rows
from ..templating import templates

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@router.get("/polls", response_class=HTMLResponse)
async def polls_page(request: Request) -> HTMLResponse:
    conn = request.app.state.db
    polls = await db.list_polls(conn, limit=50)
    return templates.TemplateResponse(request, "polls.html", {"polls": polls})


@router.post("/polls/trigger", response_class=HTMLResponse)
async def trigger_poll(request: Request) -> HTMLResponse:
    bot = request.app.state.bot
    if bot is None:
        ok, message = False, "Discord bot isn't running (no DISCORD_BOT_TOKEN set) — can't post a poll."
    else:
        cog = bot.get_cog("Polls")
        if cog is None:
            ok, message = False, "Polls cog isn't loaded — check server logs."
        else:
            ok, message = await cog.post_new_poll()

    conn = request.app.state.db
    polls = await db.list_polls(conn, limit=50)
    body = templates.env.get_template("_poll_trigger_result.html").render(
        request=request, ok=ok, message=message
    )
    body += templates.env.get_template("_polls_list_oob.html").render(request=request, polls=polls)
    return HTMLResponse(body)


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

    voter_rows = build_voter_rows(
        await db.get_tier_votes(conn, poll_id), await db.get_appeal_votes(conn, poll_id), tags_by_id
    )
    core_tag_name = tags_by_id.get(poll["result_tag_id"]) if poll["result_tag_id"] else None

    return templates.TemplateResponse(
        request,
        "poll_detail.html",
        {
            "poll": poll,
            "character": character,
            "tier_counts": tier_counts,
            "tiers": ["S", "A", "B", "C", "D"],
            "appeal_rows": appeal_rows,
            "voter_rows": voter_rows,
            "core_tag_name": core_tag_name,
        },
    )


@router.delete("/polls/{poll_id}", response_class=HTMLResponse)
async def delete_poll_from_list(request: Request, poll_id: int) -> HTMLResponse:
    """Used by the delete button on the poll list — returns the updated
    list partial, same pattern as characters/tags delete."""
    conn = request.app.state.db
    await db.delete_poll(conn, poll_id)
    polls = await db.list_polls(conn, limit=50)
    return templates.TemplateResponse(request, "_polls_list.html", {"polls": polls})


@router.post("/polls/{poll_id}/delete")
async def delete_poll_from_detail(request: Request, poll_id: int) -> RedirectResponse:
    """Used by the delete button on the poll detail page — a plain form
    POST (not htmx) since the whole page it's deleting stops existing;
    redirects back to the list rather than swapping a partial in place."""
    conn = request.app.state.db
    await db.delete_poll(conn, poll_id)
    return RedirectResponse(url="/admin/polls", status_code=303)
