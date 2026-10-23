import re
import secrets
import time
from collections import OrderedDict, deque
from threading import Lock
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from server.config import Settings

router = APIRouter()


def settings_for(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


class MutationLimiter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.entries: OrderedDict[str, deque[float]] = OrderedDict()
        self.lock = Lock()

    def check(self, login: str) -> None:
        timestamp = time.monotonic()
        with self.lock:
            while self.entries and next(iter(self.entries.values()))[-1] <= timestamp - 60:
                self.entries.popitem(last=False)
            if login not in self.entries and len(self.entries) >= 10000:
                raise HTTPException(429, "Server busy; retry later", headers={"Retry-After": "60"})
            entries = self.entries.setdefault(login, deque())
            while entries and entries[0] <= timestamp - 60:
                entries.popleft()
            if len(entries) >= self.limit:
                raise HTTPException(
                    429, "Too many changes; retry later", headers={"Retry-After": "60"}
                )
            entries.append(timestamp)
            self.entries.move_to_end(login)


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def require_user(request: Request) -> str:
    login = request.session.get("login")
    if not isinstance(login, str) or not login:
        raise HTTPException(401, "Sign in with GitHub to continue")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        token = request.headers.get("X-CSRF-Token", "")
        expected = request.session.get("csrf")
        if not isinstance(expected, str) or not secrets.compare_digest(
            token.encode(), expected.encode()
        ):
            raise HTTPException(403, "Invalid CSRF token; refresh the page")
        origin = request.headers.get("Origin")
        if origin and origin != settings_for(request).base_url.rstrip("/"):
            raise HTTPException(403, "Cross-origin changes are not allowed")
        limiter: MutationLimiter = request.app.state.limiter
        limiter.check(login)
    return login


@router.get("/api/me")
def me(request: Request) -> dict[str, str | None]:
    return {
        "login": request.session.get("login"),
        "csrf_token": csrf_token(request),
    }


@router.get("/auth/login")
def login(request: Request) -> RedirectResponse:
    settings = settings_for(request)
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    request.session["oauth_started"] = time.time()
    query = urlencode(
        {
            "client_id": settings.github_client_id,
            "redirect_uri": settings.base_url.rstrip("/") + "/auth/callback",
            "state": state,
            "scope": "",
        }
    )
    return RedirectResponse("https://github.com/login/oauth/authorize?" + query)


@router.get("/auth/callback")
async def callback(request: Request, state: str = "", code: str = "") -> RedirectResponse:
    expected = request.session.pop("oauth_state", None)
    started = request.session.pop("oauth_started", 0)
    if (
        not isinstance(expected, str)
        or not secrets.compare_digest(state.encode(), expected.encode())
        or not isinstance(started, (int, float))
        or not 0 <= time.time() - started <= 600
        or not code
    ):
        raise HTTPException(400, "Invalid or expired OAuth callback; start sign-in again")
    settings = settings_for(request)
    client: httpx.AsyncClient = request.app.state.oauth_client
    try:
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": settings.base_url.rstrip("/") + "/auth/callback",
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        token_body = response.json()
        if not isinstance(token_body, dict):
            raise HTTPException(502, "GitHub returned an invalid token response")
        token = token_body.get("access_token")
        if not isinstance(token, str) or not token:
            raise HTTPException(502, "GitHub did not provide an access token; try signing in again")
        response = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        user_body = response.json()
        if not isinstance(user_body, dict):
            raise HTTPException(502, "GitHub returned an invalid user response")
        username = user_body.get("login")
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "GitHub authentication unavailable; try again later") from exc
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", username):
        raise HTTPException(502, "GitHub returned an invalid identity")
    request.session.clear()
    request.session["login"] = username.lower()
    csrf_token(request)
    return RedirectResponse("/", status_code=303)


@router.post("/auth/logout", status_code=204)
def logout(request: Request) -> None:
    require_user(request)
    request.session.clear()
