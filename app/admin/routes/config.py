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
    return templates.TemplateResponse(request, "config.html", {"guild_config": guild_config})


@router.post("/config", response_class=HTMLResponse)
async def update_config(
    request: Request,
    channel_id: Annotated[str, Form()] = "",
    poll_post_time: Annotated[str, Form()] = "09:00",
    poll_timezone: Annotated[str, Form()] = "America/New_York",
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

    if error is None:
        await db.update_guild_config(
            conn,
            channel_id=channel_id_value,
            poll_post_time=poll_post_time,
            poll_timezone=poll_timezone,
        )
        guild_config = await db.get_guild_config(conn)
    else:
        # Redisplay what was actually typed, not the last-saved value, so a
        # typo doesn't wipe the rest of the form on a failed submit.
        guild_config = {
            "channel_id": channel_id,
            "poll_post_time": poll_post_time,
            "poll_timezone": poll_timezone,
        }

    return templates.TemplateResponse(
        request,
        "_config_form.html",
        {"guild_config": guild_config, "error": error, "saved": error is None},
    )
