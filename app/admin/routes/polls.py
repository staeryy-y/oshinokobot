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

    # Per-voter detail (who voted for what), not just the aggregate tallies
    # above — the two vote tables are independent (someone can answer one
    # question without the other), so this merges on user_id rather than
    # joining, and a voter with only one answer just shows a blank for
    # the other.
    tier_by_user = {v["user_id"]: v for v in await db.get_tier_votes(conn, poll_id)}
    appeal_by_user = {v["user_id"]: v for v in await db.get_appeal_votes(conn, poll_id)}
    voter_rows = []
    for user_id in set(tier_by_user) | set(appeal_by_user):
        tier_vote = tier_by_user.get(user_id)
        appeal_vote = appeal_by_user.get(user_id)
        display_name = (tier_vote or appeal_vote)["display_name"] or f"user {user_id}"
        voter_rows.append(
            {
                "user_id": user_id,
                "display_name": display_name,
                "tier": tier_vote["tier"] if tier_vote else None,
                "appeal_tag": tags_by_id.get(appeal_vote["tag_id"], "unknown tag")
                if appeal_vote
                else None,
            }
        )
    voter_rows.sort(key=lambda row: row["display_name"].lower())

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
        },
    )
