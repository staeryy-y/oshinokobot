from __future__ import annotations

from datetime import datetime
from typing import Annotated
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from ... import db
from ..auth import require_admin
from ..templating import templates

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request) -> HTMLResponse:
    conn = request.app.state.db
    guild_config = await db.get_guild_config(conn)
    all_series = await db.list_distinct_series(conn)
    active_series = db.parse_active_series(guild_config)
    return templates.TemplateResponse(
        request,
        "config.html",
        {"guild_config": guild_config, "all_series": all_series, "active_series": active_series},
    )


@router.post("/config", response_class=HTMLResponse)
async def update_config(
    request: Request,
    channel_id: Annotated[str, Form()] = "",
    poll_post_time: Annotated[str, Form()] = "09:00",
    poll_timezone: Annotated[str, Form()] = "America/New_York",
    series_mode: Annotated[str, Form()] = "all",
    series: Annotated[list[str], Form()] = [],
) -> HTMLResponse:
    conn = request.app.state.db

    channel_id = channel_id.strip()
    poll_post_time = poll_post_time.strip()
    poll_timezone = poll_timezone.strip()

    error = None
    channel_id_value: int | None = None
    if channel_id:
        try:
            channel_id_value = int(channel_id)
        except ValueError:
            error = (
                "Channel ID must be a number — enable Developer Mode in Discord, "
                "right-click the target channel, Copy ID."
            )

    if error is None:
        try:
            datetime.strptime(poll_post_time, "%H:%M")
        except ValueError:
            error = "Post time must be HH:MM in 24-hour format."

    if error is None and poll_timezone not in available_timezones():
        error = f"Not a recognized IANA timezone: {poll_timezone!r}"

    # Tracked separately from what actually gets saved, so a validation
    # error anywhere else on the form (e.g. a bad channel id) still
    # redisplays whatever game selection was submitted, not the
    # last-saved one.
    submitted_active_series = series if series_mode == "selected" else None
    if error is None and series_mode == "selected" and not series:
        error = 'Select at least one game, or choose "All games" instead.'

    all_series = await db.list_distinct_series(conn)

    if error is None:
        await db.update_guild_config(
            conn,
            channel_id=channel_id_value,
            poll_post_time=poll_post_time,
            poll_timezone=poll_timezone,
            active_series=submitted_active_series,
        )
        guild_config = await db.get_guild_config(conn)
        active_series = db.parse_active_series(guild_config)
    else:
        # Redisplay what was actually typed, not the last-saved value, so a
        # typo doesn't wipe the rest of the form on a failed submit.
        guild_config = {
            "channel_id": channel_id,
            "poll_post_time": poll_post_time,
            "poll_timezone": poll_timezone,
        }
        active_series = submitted_active_series

    return templates.TemplateResponse(
        request,
        "_config_form.html",
        {
            "guild_config": guild_config,
            "all_series": all_series,
            "active_series": active_series,
            "error": error,
            "saved": error is None,
        },
    )
