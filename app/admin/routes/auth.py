from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import db
from ..auth import SESSION_COOKIE_NAME, SESSION_TTL, authenticate, create_session
from ..templating import templates

router = APIRouter(prefix="/admin")


def _safe_next(path: str | None) -> str:
    # Guards against an open redirect via a crafted ?next= (e.g. a
    # protocol-relative "//evil.example") — only a same-site path is honored.
    if path and path.startswith("/") and not path.startswith("//"):
        return path
    return "/admin/characters"


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "login.html", {"next": _safe_next(next), "error": None}
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "",
) -> HTMLResponse:
    conn = request.app.state.db
    safe_next = _safe_next(next)

    user_id = await authenticate(conn, username=username, password=password)
    if user_id is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": safe_next, "error": "Invalid username or password."},
            status_code=401,
        )

    await db.delete_expired_sessions(conn)
    session_id = await create_session(conn, user_id=user_id)

    response = RedirectResponse(url=safe_next, status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        # Not marked Secure: the app itself only ever speaks plain HTTP on
        # 127.0.0.1 (see architecture.md). If this is ever exposed publicly
        # through a TLS-terminating reverse proxy, that's worth revisiting.
    )
    return response


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    conn = request.app.state.db
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id is not None:
        await db.delete_session(conn, session_id)

    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
