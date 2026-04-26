"""Shared utility helpers used across multiple modules."""
import os
import re
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models import Feed

# Directories that the app must never read from or write to on behalf of a user request.
_SENSITIVE_PATH_PREFIXES = ("/etc", "/proc", "/sys", "/root", "/boot", "/dev", "/run")


def validate_http_url(url: str) -> None:
    """Raise ValueError if url is not a plain http(s) URL.

    Prevents SSRF via file://, gopher://, ftp://, and other schemes that
    feedparser and httpx would otherwise happily follow.
    """
    scheme = urlparse(url).scheme
    if scheme not in ("http", "https"):
        raise ValueError(f"URL scheme '{scheme}' is not permitted; only http and https are allowed")


def assert_safe_path(path: str) -> str:
    """Resolve symlinks and raise ValueError for paths inside sensitive system directories.

    Returns the resolved real path so callers can use it directly.
    """
    real = os.path.realpath(path)
    for prefix in _SENSITIVE_PATH_PREFIXES:
        if real == prefix or real.startswith(prefix + os.sep):
            raise ValueError(f"Access to '{prefix}' is not permitted")
    return real


def sanitize_filename(name: str) -> str:
    """Remove characters that are unsafe in filenames."""
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name)
    return name[:200]


def get_group_feed_ids(db: Session, feed_id: int) -> list[int]:
    """Return [feed_id] + all supplementary feed IDs for a podcast group."""
    sub_ids = [r[0] for r in db.query(Feed.id).filter(Feed.primary_feed_id == feed_id).all()]
    return [feed_id] + sub_ids
