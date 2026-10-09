"""Outbound notifications: "new episodes found" → ntfy / Apprise API / webhook.

Only open, generic targets on purpose: ntfy (a URL is the destination), an
Apprise API container (which in turn speaks to a hundred-odd services, all
configured on its side), and a plain JSON webhook. Vendor-specific shapes
(Discord, Slack, …) are deliberately not here — Apprise covers them.

Design constraints, in order: no new dependencies (httpx is already here),
configured by pasting one URL, and hard to abuse — the server is being asked
to make an HTTP request to an address the user typed in, which is the classic
way to probe a private network from a server.  So: the destination can only be
set with login on and from a browser session; only http(s); redirects are not
followed; a 10 s timeout; the response body is never surfaced or stored; the
message shapes are fixed; and deliveries are logged by kind, host and status
only, never by URL or token.

Batching: feeds sync on their own timers, so there is no natural "run
finished" moment.  New episode ids are collected and sent as one message once
the syncs have been quiet for QUIET_SECONDS; a caller that knows a run ended
(the daily sync-all) flushes immediately.  A process restart inside the quiet
window loses the pending batch — accepted, to keep this stateless.
"""
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

log = logging.getLogger(__name__)

KINDS = ("ntfy", "apprise", "webhook")
USER_AGENT = "CastCharm/1.0"
TIMEOUT_SECONDS = 10.0
MAX_LINES = 10
QUIET_SECONDS = 90.0


@dataclass
class Message:
    title: str
    text: str
    link: Optional[str] = None
    count: int = 0
    feeds: list = field(default_factory=list)   # [{id, title, episodes: [{id, title, published_at}]}]


@dataclass
class DeliveryResult:
    ok: bool
    status: Optional[int]
    detail: str


def host_of(url: Optional[str]) -> str:
    try:
        return urlsplit(url or "").hostname or ""
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

def build_new_episodes_message(episodes: list, public_url: Optional[str]) -> Message:
    """One message for a batch of newly discovered episodes, grouped by podcast."""
    base = (public_url or "").strip().rstrip("/") or None
    by_feed: dict[int, dict] = {}
    for ep in episodes:
        feed = getattr(ep, "feed", None)
        fid = ep.feed_id
        entry = by_feed.setdefault(fid, {"id": fid, "title": (feed.title if feed else None) or "Podcast", "episodes": []})
        entry["episodes"].append({
            "id": ep.id,
            "title": ep.title or "Untitled episode",
            "published_at": ep.published_at.isoformat() if ep.published_at else None,
        })
    feeds = sorted(by_feed.values(), key=lambda f: f["title"].lower())
    for f in feeds:
        f["episodes"].sort(key=lambda e: e["published_at"] or "", reverse=True)

    count = sum(len(f["episodes"]) for f in feeds)
    title = "1 new episode" if count == 1 else f"{count} new episodes"

    lines = [f"{f['title']} — {e['title']}" for f in feeds for e in f["episodes"]]
    if len(lines) > MAX_LINES:
        shown, rest = lines[:MAX_LINES], len(lines) - MAX_LINES
        lines = shown + [f"…and {rest} more"]
    text = "\n".join(lines)

    link = None
    if base:
        link = f"{base}/feeds/{feeds[0]['id']}" if len(feeds) == 1 else base
    return Message(title=title, text=text, link=link, count=count, feeds=feeds)


def test_message(public_url: Optional[str]) -> Message:
    base = (public_url or "").strip().rstrip("/") or None
    return Message(
        title="CastCharm notifications are working",
        text="New episodes will arrive here as they are found.",
        link=base, count=0, feeds=[],
    )


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def _split_userinfo(url: str) -> tuple[str, Optional[tuple[str, str]]]:
    """ntfy-style user:pass@host in the URL becomes HTTP basic auth."""
    parts = urlsplit(url)
    if not parts.username:
        return url, None
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    clean = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return clean, (parts.username, parts.password or "")


def _request_for(kind: str, url: str, token: Optional[str], msg: Message) -> dict:
    """The httpx.post keyword arguments for one delivery."""
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    text_with_link = msg.text + (f"\n{msg.link}" if msg.link else "")
    if kind == "ntfy":
        headers["Title"] = msg.title
        headers["Tags"] = "headphones"
        if msg.link:
            headers["Click"] = msg.link
        return {"content": msg.text.encode("utf-8"), "headers": headers}
    if kind == "apprise":
        return {"json": {"title": msg.title, "body": text_with_link, "type": "info", "format": "text"}, "headers": headers}
    # generic webhook
    return {"json": {
        "event": "new_episodes" if msg.count else "test",
        "title": msg.title,
        "message": msg.text,
        "count": msg.count,
        "link": msg.link,
        "feeds": msg.feeds,
        "sent_at": datetime.utcnow().isoformat() + "Z",
    }, "headers": headers}


def _client(transport=None) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(TIMEOUT_SECONDS),
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
        transport=transport,
    )


# Swappable for tests (an httpx.MockTransport); None uses the network.
_transport = None


def send(kind: Optional[str], url: Optional[str], token: Optional[str], msg: Message) -> DeliveryResult:
    """Deliver one message.  Never raises; never returns the response body."""
    from app.utils import validate_http_url
    if kind not in KINDS:
        return DeliveryResult(False, None, "No notification method selected")
    if not url:
        return DeliveryResult(False, None, "No destination URL saved")
    try:
        validate_http_url(url)
    except ValueError as e:
        return DeliveryResult(False, None, str(e))

    clean_url, basic = _split_userinfo(url)
    kwargs = _request_for(kind, clean_url, token, msg)
    if basic:
        kwargs["auth"] = basic
    host = host_of(clean_url)
    try:
        with _client(_transport) as client:
            r = client.post(clean_url, **kwargs)
    except httpx.HTTPError as e:
        reason = type(e).__name__.replace("Error", " error").lower().strip() or "request failed"
        log.warning("Notification (%s) to %s failed: %s", kind, host, reason)
        return DeliveryResult(False, None, f"Could not reach {host or 'the destination'} ({reason})")

    ok = 200 <= r.status_code < 300
    if 300 <= r.status_code < 400:
        detail = f"HTTP {r.status_code} (redirect not followed)"
    else:
        detail = f"HTTP {r.status_code}"
    log.log(logging.INFO if ok else logging.WARNING,
            "Notification (%s) to %s: %s", kind, host, detail)
    return DeliveryResult(ok, r.status_code, detail)


def send_for_settings(gs, msg: Message) -> DeliveryResult:
    return send(gs.notify_kind, gs.notify_url, gs.notify_token, msg)


# ---------------------------------------------------------------------------
# Collector — one message per sync run
# ---------------------------------------------------------------------------

_pending: set[int] = set()
_lock = threading.Lock()
_timer: Optional[threading.Timer] = None


def queue_new_episodes(episode_ids) -> None:
    """Record newly discovered episodes; a message goes out once syncs go quiet."""
    global _timer
    ids = [int(i) for i in episode_ids or []]
    if not ids:
        return
    with _lock:
        _pending.update(ids)
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(QUIET_SECONDS, flush)
        _timer.daemon = True
        _timer.start()


def flush_now() -> Optional[DeliveryResult]:
    """Send whatever is pending right away (the daily sync-all knows it has finished)."""
    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
    return flush()


def flush() -> Optional[DeliveryResult]:
    global _timer
    with _lock:
        ids = list(_pending)
        _pending.clear()
        _timer = None
    if not ids:
        return None

    from app.database import SessionLocal
    from app.models import Episode, GlobalSettings
    from sqlalchemy.orm import joinedload

    db = SessionLocal()
    try:
        gs = db.query(GlobalSettings).first()
        if not gs or not gs.notify_enabled or not gs.notify_url:
            return None
        # Status is deliberately not a filter: by the time the batch flushes,
        # auto-download has usually moved a new episode from "pending" to
        # "queued" or even "downloaded". Only duplicates the server suppressed
        # ("skipped") and hidden rows are left out.
        episodes = (
            db.query(Episode)
            .options(joinedload(Episode.feed))
            .filter(Episode.id.in_(ids), Episode.status != "skipped", Episode.hidden.is_(False))
            .all()
        )
        if not episodes:
            return None
        msg = build_new_episodes_message(episodes, gs.public_url)
        return send_for_settings(gs, msg)
    except Exception as e:  # never let a notification failure touch a sync
        log.warning("Notification flush failed: %s", e)
        return None
    finally:
        db.close()
