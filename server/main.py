import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

from server import auth
from server.config import ROOT, Settings
from server.db import closing_connection, initialize
from server.routes import bookings, requests

logger = logging.getLogger(__name__)


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.url.path.startswith(("/api/", "/auth/")):
            response.headers["Cache-Control"] = "no-store"
        else:
            # Revalidate pages and assets so clients never run stale JavaScript after a deploy.
            response.headers["Cache-Control"] = "no-cache"
        if request.app.state.settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        initialize(settings)
        async with httpx.AsyncClient(timeout=10) as client:
            app.state.oauth_client = client
            yield

    app = FastAPI(title="Seat Share", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.limiter = auth.MutationLimiter(settings.mutation_limit)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="seat_share",
        max_age=60 * 60 * 24 * 7,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    app.add_middleware(SecurityHeaders)
    app.include_router(auth.router)
    app.include_router(bookings.router)
    app.include_router(requests.router)
    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(map(str, error["loc"])), "message": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=400, content={"detail": details})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("Request failed: %s %s", request.url.path, exc.status_code)
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers
        )

    @app.get("/healthz")
    def health() -> dict[str, str]:
        with closing_connection(settings) as connection:
            connection.execute("SELECT 1").fetchone()
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    @app.get("/new", include_in_schema=False)
    @app.get("/mine", include_in_schema=False)
    def page() -> FileResponse:
        return FileResponse(ROOT / "web" / "index.html")

    @app.get("/requests", include_in_schema=False)
    def legacy_requests_page() -> RedirectResponse:
        return RedirectResponse("/mine", status_code=308)

    return app
