import logging
from typing import Optional
from fastapi import Request, APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

from app.database import get_db
from app.models import GlobalSettings
from app.schemas import (
    GlobalSettingsOut, GlobalSettingsUpdate,
    ID3TagInfo, RSSSourceInfo,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Canonical ID3 tag definitions surfaced to the UI
ID3_TAGS: list[ID3TagInfo] = [
    ID3TagInfo(tag="TIT2", label="Title"),
    ID3TagInfo(tag="TPE1", label="Artist / Author"),
    ID3TagInfo(tag="TALB", label="Album (Podcast Name)"),
    ID3TagInfo(tag="TDRC", label="Recording Date"),
    ID3TagInfo(tag="TRCK", label="Track Number (Episode #)"),
    ID3TagInfo(tag="TPOS", label="Part of Set (Season #)"),
    ID3TagInfo(tag="TCON", label="Genre / Category"),
    ID3TagInfo(tag="COMM", label="Comment / Description"),
    ID3TagInfo(tag="APIC", label="Cover Art"),
    ID3TagInfo(tag="TIT3", label="Subtitle"),
    ID3TagInfo(tag="TPUB", label="Publisher"),
    ID3TagInfo(tag="TENC", label="Encoded By"),
]

# RSS source fields that can be mapped to ID3 tags
RSS_SOURCES: list[RSSSourceInfo] = [
    RSSSourceInfo(field="episode.title", label="Episode Title"),
    RSSSourceInfo(field="episode.author", label="Episode Author"),
    RSSSourceInfo(field="episode.description", label="Episode Description"),
    RSSSourceInfo(field="episode.published", label="Episode Publication Date"),
    RSSSourceInfo(field="episode.episode_number", label="Episode Number"),
    RSSSourceInfo(field="episode.season_number", label="Season Number"),
    RSSSourceInfo(field="episode.duration", label="Episode Duration"),
    RSSSourceInfo(field="feed.title", label="Podcast Title"),
    RSSSourceInfo(field="feed.author", label="Podcast Author"),
    RSSSourceInfo(field="feed.category", label="Podcast Category"),
    RSSSourceInfo(field="feed.image", label="Podcast Cover Art"),
    RSSSourceInfo(field="episode.image", label="Episode Cover Art (falls back to feed)"),
]

DEFAULT_ID3_MAPPING: dict[str, str] = {
    "TIT2": "episode.title",
    "TPE1": "episode.author",
    "TALB": "feed.title",
    "TDRC": "episode.published",
    "TRCK": "episode.episode_number",
    "TPOS": "episode.season_number",
    "TCON": "feed.category",
    "COMM": "episode.description",
    "APIC": "episode.image",
    "TPUB": "feed.author",
}


def _get_or_create_settings(db: Session) -> GlobalSettings:
    settings = db.query(GlobalSettings).first()
    if not settings:
        settings = GlobalSettings(default_id3_mapping=DEFAULT_ID3_MAPPING)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _settings_out(settings: GlobalSettings) -> GlobalSettingsOut:
    """The API view of settings: everything except credentials, plus the
    'is a destination saved, and where' facts the form needs."""
    from app.notifications import host_of
    out = GlobalSettingsOut.model_validate(settings)
    out.notify_url_set = bool(settings.notify_url)
    out.notify_url_host = host_of(settings.notify_url) if settings.notify_url else None
    out.notify_token_set = bool(settings.notify_token)
    return out


_NOTIFY_FIELDS = {"notify_enabled", "notify_kind", "notify_url", "notify_token", "public_url"}


@router.get("", response_model=GlobalSettingsOut)
def get_settings(db: Session = Depends(get_db)):
    return _settings_out(_get_or_create_settings(db))


@router.put("", response_model=GlobalSettingsOut)
def update_settings(body: GlobalSettingsUpdate, request: Request, db: Session = Depends(get_db)):
    settings = _get_or_create_settings(db)
    updates = body.model_dump(exclude_unset=True)

    # Pointing the server at a notification destination is admin-only, and
    # only meaningful once there is an admin: with login off anyone on the
    # network could do it. API keys can't do it either.
    if updates.keys() & _NOTIFY_FIELDS:
        from app.auth import require_login_enabled, require_session
        require_session(request)
        if updates.get("notify_url") or updates.get("notify_token") or updates.get("notify_enabled"):
            require_login_enabled(db)
    # Write-only credentials: "" clears, a value replaces.
    for secret in ("notify_url", "notify_token"):
        if secret in updates:
            updates[secret] = updates[secret] or None

    for field, value in updates.items():
        setattr(settings, field, value)
    db.commit()
    db.refresh(settings)
    # Inline imports below avoid circular imports at module load time:
    # log_buffer and downloader both import from app.models / app.database,
    # so importing them at the top of this file would create a cycle.
    if body.log_max_entries is not None:
        from app.log_buffer import set_maxlen
        set_maxlen(body.log_max_entries)
    if "max_concurrent_downloads" in updates:
        from app import downloader as _dl
        _dl._cached_max_concurrent = None  # force re-read on next enqueue
    # Reschedule cron jobs when schedule-related settings change
    _schedule_fields = {
        "scheduled_xml_enabled", "scheduled_xml_time",
        "scheduled_opml_enabled", "scheduled_opml_time",
        "scheduled_sync_enabled", "scheduled_sync_time",
        "autoclean_enabled", "autoclean_mode", "keep_latest", "autoclean_time",
        "timezone",
    }
    changed = set(updates.keys())
    if changed & _schedule_fields:
        from app.scheduler import schedule_xml_regen, schedule_opml_export, schedule_daily_sync, schedule_autoclean
        if changed & {"scheduled_xml_enabled", "scheduled_xml_time", "timezone"}:
            schedule_xml_regen()
        if changed & {"scheduled_opml_enabled", "scheduled_opml_time", "timezone"}:
            schedule_opml_export()
        if changed & {"scheduled_sync_enabled", "scheduled_sync_time", "timezone"}:
            schedule_daily_sync()
        if changed & {"autoclean_enabled", "autoclean_mode", "keep_latest", "autoclean_time", "timezone"}:
            schedule_autoclean()
    log.info("Settings updated: %s", ", ".join(updates.keys()))
    return _settings_out(settings)


_last_notify_test_at = 0.0


@router.post("/notifications/test")
def test_notification(request: Request, db: Session = Depends(get_db)):
    """Send a fixed test message to the saved destination."""
    import time
    from app import notifications
    from app.auth import require_login_enabled, require_session
    global _last_notify_test_at
    require_session(request)
    require_login_enabled(db)
    now = time.monotonic()
    if now - _last_notify_test_at < 10.0:
        raise HTTPException(status_code=429, detail="Please wait a few seconds between test messages.")
    _last_notify_test_at = now
    settings = _get_or_create_settings(db)
    result = notifications.send_for_settings(settings, notifications.test_message(settings.public_url))
    return {"ok": result.ok, "status": result.status, "detail": result.detail}


@router.post("/autoclean/run")
def run_autoclean_now(db: Session = Depends(get_db)):
    """Immediately run auto-cleanup across all feeds."""
    from app.cleanup import run_autoclean_all_feeds
    from app.activity import mark_autoclean_start, mark_autoclean_done
    mark_autoclean_start()
    try:
        deleted = run_autoclean_all_feeds(db)
    finally:
        mark_autoclean_done()
    return {"deleted": deleted}


@router.get("/logs")
def get_logs(
    limit: int = Query(default=1000, le=5000),
    level: Optional[str] = Query(default=None),
):
    """Return recent application log entries from the in-memory buffer."""
    from app.log_buffer import get_logs as _get_logs  # inline: circular import avoidance
    return _get_logs(limit=limit, min_level=level)


@router.get("/server-timezone")
def get_server_timezone():
    """Detect the server's IANA timezone — used to pre-fill the setup wizard."""
    import os
    from zoneinfo import available_timezones
    known = available_timezones()

    # 1. TZ env var — the standard Docker/docker-compose mechanism
    tz_env = os.environ.get("TZ", "").strip()
    if tz_env and tz_env in known:
        return {"timezone": tz_env}

    # 2. /etc/localtime symlink (present when system tzdata is installed)
    try:
        link = os.readlink("/etc/localtime")
        tz_name = link.split("/zoneinfo/", 1)[-1]
        if tz_name and tz_name in known:
            return {"timezone": tz_name}
    except OSError:
        pass

    # 3. Python's local timezone (works when tzdata pkg is installed)
    try:
        import datetime
        local_tz = datetime.datetime.now(datetime.timezone.utc).astimezone().tzinfo
        if hasattr(local_tz, "key") and local_tz.key in known:
            return {"timezone": local_tz.key}
    except Exception:
        pass

    return {"timezone": "UTC"}


@router.get("/timezones")
def list_timezones():
    """Return all available IANA timezone names as a flat sorted list."""
    from zoneinfo import available_timezones
    return {"timezones": sorted(available_timezones())}


@router.get("/id3-tags", response_model=list[ID3TagInfo])
def list_id3_tags():
    return ID3_TAGS


@router.get("/rss-sources", response_model=list[RSSSourceInfo])
def list_rss_sources():
    return RSS_SOURCES


