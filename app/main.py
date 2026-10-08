import logging
import mimetypes
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from app.database import init_db, SessionLocal
from app.limits import (
    MAX_BULK_IDS,
    MAX_IDS_IN_URL,
    MAX_INDEX_IDS,
    MAX_PAGE_SIZE,
    MAX_REQUEST_BYTES,
    MAX_SEARCH_LEN,
    WORKER_THREADS,
)
from app.models import Episode, Feed
from app.scheduler import start_scheduler, stop_scheduler, is_running
from app.schemas import LimitsOut, StatusOut

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

# Attach the in-memory ring-buffer handler so logs are readable via /api/logs
from app.log_buffer import BufferHandler as _BufHandler  # noqa: E402
_buf_handler = _BufHandler()
_buf_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(_buf_handler)

_static_dir = (Path(__file__).parent.parent / "static").resolve()
_index_html: str = ""


def _asset_version() -> str:
    """Cache-busting token for /static/ URLs.

    APP_VERSION alone is not enough. It defaults to "dev" in the Dockerfile, so an
    instance built without an explicit version stamps every asset ?v=dev forever —
    while those same assets are served immutable for a year. The browser is then
    entitled to keep the JavaScript it first saw across every subsequent rebuild,
    which makes front-end changes invisible to exactly the people who already use
    the app, and looks for all the world like the change did not deploy.

    Mixing in the newest mtime under static/ means the token moves whenever the
    assets actually move, whatever APP_VERSION says. A tagged release still gets a
    stable token because its files are baked into the image at build time.
    """
    try:
        newest = max(
            (p.stat().st_mtime_ns for p in _static_dir.rglob("*") if p.is_file()),
            default=0,
        )
    except OSError:
        # An unreadable static dir is the server's problem, not the cache's; fall
        # back to the plain version rather than failing startup over a token.
        return APP_VERSION
    return f"{APP_VERSION}-{newest:x}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _index_html
    import anyio.to_thread

    anyio.to_thread.current_default_thread_limiter().total_tokens = WORKER_THREADS
    log.info("Worker threadpool limited to %d threads", WORKER_THREADS)

    init_db()
    _ensure_default_settings()
    _cleanup_interrupted_downloads()
    start_scheduler()
    # Pre-process index.html: inject ?v=<token> into all /static/ asset URLs so
    # that a container update (e.g. via Watchtower) busts the browser cache.
    raw = (_static_dir / "index.html").read_text()
    _index_html = re.sub(
        r'(src|href)="(/static/[^"]+)"', rf'\1="\2?v={_asset_version()}"', raw
    )
    yield
    stop_scheduler()


def _ensure_default_settings():
    """Create default GlobalSettings row if it doesn't exist."""
    from app.models import GlobalSettings
    from app.routers.settings import DEFAULT_ID3_MAPPING

    db = SessionLocal()
    try:
        if not db.query(GlobalSettings).first():
            db.add(GlobalSettings(default_id3_mapping=DEFAULT_ID3_MAPPING))
            db.commit()
    finally:
        db.close()


def _cleanup_interrupted_downloads():
    """Move any 'downloading' episodes to 'failed' and delete leftover .part files.

    Handles the case where the server was killed during an active download.
    """
    import logging
    import os
    from app.models import Episode, GlobalSettings

    log = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        stuck = db.query(Episode).filter(Episode.status == "downloading").all()
        if stuck:
            for ep in stuck:
                ep.status = "failed"
                ep.error_message = "Interrupted by server restart"
                ep.download_progress = 0
            db.commit()
            log.info("Marked %d interrupted download(s) as failed", len(stuck))

        # Episodes left in "queued" have no background tasks after a restart — reset
        # them to "pending" so the next download-all picks them up correctly.
        orphaned = db.query(Episode).filter(Episode.status == "queued").all()
        if orphaned:
            for ep in orphaned:
                ep.status = "pending"
                ep.download_progress = 0
            db.commit()
            log.info("Reset %d orphaned queued episode(s) to pending", len(orphaned))

        # Remove any orphaned .part files from the downloads directory
        gs = db.query(GlobalSettings).first()
        base_dir = (gs.download_path if gs else None) or "/downloads"
        if os.path.isdir(base_dir):
            removed = 0
            for root, _dirs, files in os.walk(base_dir):
                for fname in files:
                    if fname.endswith(".part"):
                        try:
                            os.remove(os.path.join(root, fname))
                            removed += 1
                        except OSError:
                            pass
            if removed:
                log.info("Removed %d orphaned .part file(s) from %s", removed, base_dir)
    finally:
        db.close()


import os as _os
APP_VERSION = _os.environ.get("APP_VERSION", "dev")

app = FastAPI(
    title="CastCharm",
    description=(
        "Self-hosted podcast manager.\n\n"
        "External clients authenticate with an API key generated under "
        "Settings → External API, sent as either `Authorization: Bearer <key>` "
        "or `X-API-Key: <key>`."
    ),
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    # Served under /api/ so AuthMiddleware covers it — at the default
    # /openapi.json the schema sits outside the middleware's path check and is
    # readable by anyone who can reach the port.
    openapi_url="/api/openapi.json",
)


# Register the API-key auth methods in the OpenAPI schema so the "Authorize"
# button appears in Swagger UI. Without this, users testing endpoints from
# /api/docs can only authenticate as their current browser session — there is
# no way to try a specific key.
def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema.setdefault("components", {})["securitySchemes"] = {
        "APIKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
        "BearerAuth": {"type": "http", "scheme": "bearer"},
    }
    # Applied globally — every endpoint accepts either scheme. The browser
    # cookie still works for callers with a session, so this is additive.
    schema["security"] = [{"APIKeyHeader": []}, {"BearerAuth": []}]
    app.openapi_schema = schema
    return schema

app.openapi = _custom_openapi

@app.get("/api/docs", include_in_schema=False)
async def swagger_ui():
    # ?v=<version> matches what index.html does for its assets. Static files are
    # served immutable for a year, so without it an upgraded container would keep
    # serving a browser-cached swagger-init.js pointing at the old schema URL.
    html = f"""<!DOCTYPE html>
<html><head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>CastCharm API</title>
  <link rel="stylesheet" href="/static/swagger/swagger-ui.css?v={APP_VERSION}">
</head><body>
  <div id="swagger-ui"></div>
  <script src="/static/swagger/swagger-ui-bundle.js?v={APP_VERSION}"></script>
  <script src="/static/swagger/swagger-init.js?v={APP_VERSION}"></script>
</body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

app.add_middleware(GZipMiddleware, minimum_size=1000)


# ── Security headers middleware ────────────────────────────────────────────────
# CSP notes:
#   script-src  — 'unsafe-inline' removed; all event handlers use data-action
#                 delegation rather than inline onclick/onerror attributes.
#   style-src   — 'unsafe-inline' needed; JS sets element.style.* throughout.
#   img-src     — podcast cover art can come from any HTTPS host in the RSS feed;
#                 blob: is needed for the local file-upload cover-art preview.
#   media-src   — audio streaming goes through the local server only.
#   connect-src — all XHR/fetch calls go to self (the local API).
#   object-src  — block Flash and other legacy plugin content entirely.
#   base-uri    — prevent a <base> tag injection from hijacking relative URLs.
#   frame-ancestors — supersedes X-Frame-Options for modern browsers; kept both
#                     for compatibility with older reverse-proxy stacks.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' https: data: blob:; "
    "media-src 'self' blob:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'"
)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        # Keep X-Frame-Options for reverse proxies / older browsers that don't
        # honour the frame-ancestors CSP directive yet.
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # Versioned static assets can be cached indefinitely; index.html sets no-cache itself
        if request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        return response


app.add_middleware(SecurityHeadersMiddleware)


# ── Request size limit ─────────────────────────────────────────────────────────
# Starlette buffers a request body in memory before a handler ever sees it, so
# without this the size of an incoming request is entirely the client's choice.
# On a Pi-class box a single multi-gigabyte POST to any endpoint at all — even one
# whose body is a single integer — is enough to end the process.
#
# This reads Content-Length, so a chunked request without one slips past. That is
# accepted: the handlers that take uploads all read with an explicit byte cap, so
# the streaming path has its own limit, and this is here to stop the trivial case
# before any of it is buffered.
class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        raw_length = request.headers.get("content-length")
        if raw_length:
            try:
                length = int(raw_length)
            except ValueError:
                return JSONResponse(
                    status_code=400, content={"detail": "Invalid Content-Length"}
                )
            if length > MAX_REQUEST_BYTES:
                log.warning(
                    "Rejected %s %s: body of %d bytes exceeds the %d byte limit",
                    request.method,
                    request.url.path,
                    length,
                    MAX_REQUEST_BYTES,
                )
                return JSONResponse(
                    status_code=413, content={"detail": "Request body too large"}
                )
        return await call_next(request)


app.add_middleware(RequestSizeLimitMiddleware)


# ── Auth middleware ────────────────────────────────────────────────────────────
# Routes that are always accessible regardless of auth state:
_AUTH_EXEMPT_PREFIXES = (
    "/api/auth/",
    "/api/setup/",
)
# /api/limits is exempt alongside /api/status because it is pure constants — the
# same numbers a caller would discover by being refused — and because a client
# that cannot read it falls back to guessing. Making it answerable before login
# removes the case where a request timed slightly early gets a 401 and the client
# spends the next stretch sizing itself to assumptions instead.
_AUTH_EXEMPT_EXACT = {"/api/status", "/api/limits"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        path = request.url.path
        # Recorded so key-management endpoints can refuse API-key callers.
        # See app/routers/api_keys.py.
        request.state.auth_method = "none"

        # Non-API routes (SPA shell, static assets) are always served
        if not path.startswith("/api/"):
            return await call_next(request)

        # Auth/setup endpoints are always accessible
        if any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES):
            return await call_next(request)
        if path in _AUTH_EXEMPT_EXACT:
            return await call_next(request)

        # Resolve the decision in a worker thread and — critically — let go of
        # the database session before handing the request downstream.
        #
        # This used to run its queries inline and keep the session open across
        # `await call_next(...)`. Two things went wrong with that. The session
        # holds its pooled connection until it is closed, so every in-flight
        # request pinned TWO connections: this one for the whole round trip plus
        # the one the endpoint itself takes via get_db. And the queries ran on
        # the event loop, so each of them stalled every other request in the
        # process. Together those turned a burst of concurrent art requests into
        # pool exhaustion, 30-second waits and a cascade of 500s.
        allowed, auth_method, api_key_id = await run_in_threadpool(
            self._resolve_auth, request, path
        )
        if not allowed:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
            )

        request.state.auth_method = auth_method
        if api_key_id is not None:
            request.state.api_key_id = api_key_id
        return await call_next(request)

    @staticmethod
    def _resolve_auth(request: StarletteRequest, path: str) -> tuple[bool, str, int | None]:
        """Return (allowed, auth_method, api_key_id) using a short-lived session.

        Runs in a worker thread. Everything that touches the database must stay
        inside this function so the connection is returned to the pool the moment
        the decision is made.
        """
        from app.auth import (
            API_KEY_FAILURE_LOG_THRESHOLD, COOKIE_NAME, extract_api_key,
            is_api_enabled, is_auth_required, record_api_key_failure,
            validate_api_key, validate_session,
        )

        db = SessionLocal()
        try:
            # API keys are checked before the session so that last_used_at is
            # recorded even on instances with login turned off.
            api_key = extract_api_key(request)
            if api_key:
                # Skip validation entirely when the master switch is off, so a
                # disabled key never has its last_used_at touched.
                key_id = validate_api_key(api_key, db) if is_api_enabled(db) else None
                if key_id is not None:
                    return True, "api_key", key_id
                # Throttle the log — a hostile scanner cycling through guesses
                # would otherwise emit a WARNING per request. First N failures
                # in the window log at WARNING; the rest drop to DEBUG so the
                # signal survives without drowning the log file.
                client_host = request.client.host if request.client else "unknown"
                count = record_api_key_failure(client_host)
                if count <= API_KEY_FAILURE_LOG_THRESHOLD:
                    log.warning("Rejected API key for %s from %s", path, client_host)
                elif count == API_KEY_FAILURE_LOG_THRESHOLD + 1:
                    log.warning(
                        "Suppressing further bad-key warnings for %s (>%d in window)",
                        client_host, API_KEY_FAILURE_LOG_THRESHOLD,
                    )
                else:
                    log.debug("Rejected API key for %s from %s (throttled)", path, client_host)
                # Fall through: a bad key is treated as no key, so an instance
                # with login disabled stays as open as it was before.

            if not is_auth_required(db):
                return True, "none", None

            token = request.cookies.get(COOKIE_NAME)
            if token and validate_session(token, db):
                return True, "session", None

            return False, "none", None
        finally:
            db.close()


app.add_middleware(AuthMiddleware)

# API routers
from app.routers import feeds, episodes, settings as settings_router, stats as stats_router  # noqa: E402
from app.routers.api_keys import router as api_keys_router  # noqa: E402
from app.routers.auth import router as auth_router  # noqa: E402
from app.routers.playlists import router as playlists_router  # noqa: E402
from app.routers.player import router as player_router  # noqa: E402
app.include_router(auth_router)
app.include_router(api_keys_router)
app.include_router(feeds.router)
app.include_router(episodes.router)
app.include_router(settings_router.router)
app.include_router(stats_router.router)
app.include_router(playlists_router)
app.include_router(player_router)


@app.exception_handler(OverflowError)
async def _overflow_to_404(request: StarletteRequest, exc: OverflowError):
    """Turn out-of-range path ids into a 404 instead of a 500.

    FastAPI validates `feed_id: int` with Python's unbounded int, so an id of
    2**63 or more passes validation and only blows up further down, when SQLite
    refuses to bind it. That surfaced as an unhandled 500 plus a ~200-line
    traceback per request, on every router with an integer path param.

    404 is the honest answer: the id cannot name a row, because no row can ever
    have it. Handled centrally rather than per-route so routes added later are
    covered without anyone having to remember this.
    """
    return JSONResponse(status_code=404, content={"detail": "Not found"})


@app.get("/api/limits", response_model=LimitsOut, tags=["system"])
def get_limits():
    """Publish the request ceilings so clients can size themselves to this server.

    Touches no database and no filesystem — it is a handful of constants — so a
    client is free to re-read it whenever it likes.

    The point is that a limit lives in exactly one place. Before this, the Android
    app carried its own copy of these numbers, which is fine right up until a
    server is upgraded and the two disagree: the client keeps asking for what the
    server used to allow and every request comes back 422, for a reason visible
    nowhere in the client.
    """
    return LimitsOut(
        max_page_size=MAX_PAGE_SIZE,
        max_ids_in_url=MAX_IDS_IN_URL,
        max_bulk_ids=MAX_BULK_IDS,
        max_index_ids=MAX_INDEX_IDS,
        max_search_len=MAX_SEARCH_LEN,
        max_request_bytes=MAX_REQUEST_BYTES,
    )


@app.get("/api/status", response_model=StatusOut, tags=["system"])
def get_status(request: StarletteRequest):
    db = SessionLocal()
    try:
        # /api/status is auth-exempt so the SPA can render before login and the
        # setup wizard can poll it. On a locked-down instance we don't want an
        # unauthenticated caller to fingerprint library size, so return only
        # the fields the login screen actually needs.
        from app.auth import (
            COOKIE_NAME, extract_api_key, is_api_enabled,
            is_auth_required, validate_api_key, validate_session,
        )
        authenticated = not is_auth_required(db)
        if not authenticated:
            api_key = extract_api_key(request)
            if api_key and is_api_enabled(db) and validate_api_key(api_key, db) is not None:
                authenticated = True
            else:
                token = request.cookies.get(COOKIE_NAME)
                if token and validate_session(token, db):
                    authenticated = True
        if not authenticated:
            return StatusOut(
                scheduler_running=is_running(),
                download_queue_size=0,
                active_downloads=0,
                podcasts_total=0,
                feeds_total=0,
                episodes_total=0,
                episodes_downloaded=0,
                episodes_failed=0,
                storage_bytes=0,
                version=APP_VERSION,
            )

        podcasts_total = db.query(func.count(Feed.id)).filter(Feed.primary_feed_id.is_(None)).scalar() or 0
        feeds_total = db.query(func.count(Feed.id)).scalar() or 0
        episodes_total = db.query(func.count(Episode.id)).scalar() or 0
        episodes_downloaded = (
            db.query(func.count(Episode.id)).filter(Episode.status == "downloaded").scalar() or 0
        )
        active_downloads = (
            db.query(func.count(Episode.id)).filter(Episode.status == "downloading").scalar() or 0
        )
        download_queue_size = (
            db.query(func.count(Episode.id)).filter(Episode.status == "queued").scalar() or 0
        )

        storage = (
            db.query(func.sum(Episode.file_size))
            .filter(Episode.status == "downloaded", Episode.file_size.isnot(None))
            .scalar() or 0
        )

        episodes_failed = (
            db.query(func.count(Episode.id)).filter(Episode.status == "failed").scalar() or 0
        )

        from app.activity import get_syncing_count, get_syncing_feed_ids, is_xml_regenerating, is_opml_generating, is_autoclean_running
        from app.scheduler import get_next_run_any, get_download_window_status
        from app.importer import get_active_import_count

        # Primary feed IDs with any queued or active downloads (handles supplementary feeds)
        _active_feed_ids = [
            r[0] for r in db.query(Episode.feed_id)
            .filter(Episode.status.in_(["queued", "downloading"]))
            .distinct().all()
        ]
        if _active_feed_ids:
            _feed_rows = db.query(Feed.id, Feed.primary_feed_id).filter(Feed.id.in_(_active_feed_ids)).all()
            downloading_feed_ids = list({f.primary_feed_id or f.id for f in _feed_rows})
        else:
            downloading_feed_ids = []

        return StatusOut(
            scheduler_running=is_running(),
            download_queue_size=download_queue_size,
            active_downloads=active_downloads,
            podcasts_total=podcasts_total,
            feeds_total=feeds_total,
            episodes_total=episodes_total,
            episodes_downloaded=episodes_downloaded,
            episodes_failed=episodes_failed,
            storage_bytes=storage,
            version=APP_VERSION,
            syncing_count=get_syncing_count(),
            next_sync_at=get_next_run_any(),
            importing_count=get_active_import_count(),
            scanning=False,
            downloading_feed_ids=downloading_feed_ids,
            syncing_feed_ids=get_syncing_feed_ids(),
            xml_regenerating=is_xml_regenerating(),
            opml_generating=is_opml_generating(),
            autoclean_running=is_autoclean_running(),
            **dict(zip(("download_window_paused", "download_window_next_open"), get_download_window_status())),
        )
    finally:
        db.close()


@app.get("/api/system/browse-dirs", tags=["system"])
def browse_dirs(path: str = Query(default="/")):
    """List immediate subdirectories of a server-side path (for the setup folder picker)."""
    import os
    from app.utils import assert_safe_path
    path = os.path.normpath(path)
    try:
        path = assert_safe_path(path)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access to this directory is not permitted")
    if not os.path.isdir(path):
        raise HTTPException(status_code=404, detail="Directory not found")
    entries = []
    try:
        for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if entry.is_dir() and not entry.name.startswith("."):
                entries.append({"name": entry.name, "path": entry.path})
    except PermissionError:
        pass  # return whatever we managed to collect before hitting a denied dir
    parent = str(Path(path).parent) if path != "/" else None
    return {"path": path, "parent": parent, "entries": entries}


# Serve the SPA index for the root path only.
# Cache-Control: no-cache ensures the browser revalidates this on every load,
# so asset URLs (which carry ?v=<version>) are always current after an update.
@app.get("/", include_in_schema=False)
def serve_root():
    return HTMLResponse(content=_index_html, headers={"Cache-Control": "no-cache"})


# Mount static files last, with no catch-all above to shadow it.
# Assets are served with a long max-age — safe because index.html injects
# ?v=<APP_VERSION> into every URL, so a version bump busts the cache.
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
