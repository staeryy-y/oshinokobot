from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from ... import db
from ..images import resolve_media_path
from ..poll_results import TIER_VALUES, build_voter_rows, nearest_tier
from ..templating import templates

# Deliberately no require_admin dependency anywhere in this file — this is
# the public-facing router (mounted without an /admin prefix in server.py).
# It only ever reads; nothing here can mutate state.
router = APIRouter()


@router.get("/media/{filename}")
async def public_media(request: Request, filename: str) -> FileResponse:
    # Separate from /admin/media/{filename} (auth-gated) - character art
    # needs to be visible on this public page, so it's served here without
    # a login requirement. Same path-safety check either way.
    path = resolve_media_path(request.app.state.config.media_dir, filename)
    if path is None:
        raise HTTPException(404, "Not found")
    return FileResponse(path)


@router.get("/results", response_class=HTMLResponse)
async def public_results_page(request: Request) -> HTMLResponse:
    conn = request.app.state.db

    # 1. Cumulative *average* tier — not the same as result_tier (the
    # per-poll majority winner): this averages every individual tier vote
    # a character ever received, across its one poll, into a single score.
    by_character: dict[int, dict] = {}
    for vote in await db.get_tier_votes_for_closed_polls(conn):
        entry = by_character.setdefault(
            vote["character_id"],
            {
                "name": vote["character_name"],
                "series": vote["character_series"],
                "poll_id": vote["poll_id"],
                "scores": [],
            },
        )
        entry["scores"].append(TIER_VALUES[vote["tier"]])

    average_rows = [
        {
            "name": entry["name"],
            "series": entry["series"],
            "poll_id": entry["poll_id"],
            "average": sum(entry["scores"]) / len(entry["scores"]),
            "nearest_tier": nearest_tier(sum(entry["scores"]) / len(entry["scores"])),
            "vote_count": len(entry["scores"]),
        }
        for entry in by_character.values()
    ]
    average_rows.sort(key=lambda row: (-row["average"], -row["vote_count"], row["name"].lower()))

    # 2. Characters grouped by "core" — the appeal tag that won each
    # character's poll (polls.result_tag_id, same majority-vote rule as
    # the tier dub, computed once at close time).
    by_core: dict[str, list[dict]] = {}
    no_core: list[dict] = []
    for row in await db.list_dubbed_characters(conn):
        item = {"name": row["character_name"], "series": row["character_series"], "poll_id": row["poll_id"]}
        if row["core_tag_name"]:
            by_core.setdefault(row["core_tag_name"], []).append(item)
        else:
            no_core.append(item)
    core_groups = sorted(by_core.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))

    return templates.TemplateResponse(
        request,
        "public_results.html",
        {"average_rows": average_rows, "core_groups": core_groups, "no_core": no_core},
    )


@router.get("/results/{poll_id}", response_class=HTMLResponse)
async def public_poll_detail_page(request: Request, poll_id: int) -> HTMLResponse:
    conn = request.app.state.db
    poll = await db.get_poll(conn, poll_id)
    # Only closed polls are published here — an open poll's outcome isn't
    # decided yet, and its message is already live on Discord for anyone
    # who wants to see the in-progress state.
    if poll is None or poll["status"] != "closed":
        raise HTTPException(404, "No such result")

    character = await db.get_character(conn, poll["character_id"])
    tags_by_id = {t["id"]: t["name"] for t in await db.list_tags(conn)}

    tier_votes = await db.get_tier_votes(conn, poll_id)
    appeal_votes = await db.get_appeal_votes(conn, poll_id)

    tier_counts: dict[str, int] = {}
    for vote in tier_votes:
        tier_counts[vote["tier"]] = tier_counts.get(vote["tier"], 0) + 1
    appeal_counts: dict[int, int] = {}
    for vote in appeal_votes:
        appeal_counts[vote["tag_id"]] = appeal_counts.get(vote["tag_id"], 0) + 1

    appeal_rows = sorted(
        ((tags_by_id.get(tag_id, "unknown tag"), count) for tag_id, count in appeal_counts.items()),
        key=lambda row: row[1],
        reverse=True,
    )
    voter_rows = build_voter_rows(tier_votes, appeal_votes, tags_by_id)
    core_tag_name = tags_by_id.get(poll["result_tag_id"]) if poll["result_tag_id"] else None

    return templates.TemplateResponse(
        request,
        "public_poll_detail.html",
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
