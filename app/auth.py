"""Authentication utilities for CastCharm.

Uses bcrypt (via passlib) for password hashing and server-side session tokens
stored in the database. Sessions are identified by an httpOnly cookie named
COOKIE_NAME. All sensitive comparisons go through passlib so timing is
constant and salting is automatic.
"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from passlib.context import CryptContext
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# bcrypt: slow by design (~100 ms per verify), auto-salted, immune to
# length-extension attacks. Industry standard for password storage.
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

COOKIE_NAME = "cc_session"
SESSION_LIFETIME = timedelta(days=30)


def cookie_secure_for(request) -> bool:
    """Return True when the session cookie should be marked Secure.

    Decided per request: if the client reached us over HTTPS, the cookie is
    Secure (the browser then refuses to leak it back over plain HTTP). If the
    request came in over plain HTTP — the common home-LAN case — the flag is
    off so the browser will actually store and send the cookie.

    Uvicorn is launched with --proxy-headers, so request.url.scheme reflects
    X-Forwarded-Proto when CastCharm sits behind a reverse proxy that
    terminates TLS. No configuration required either way.
    """
    return request.url.scheme == "https"

# ── Rate limiting ──────────────────────────────────────────────────────────────
# Simple in-memory counter per remote IP. Resets on restart — acceptable for a
# single-user self-hosted app. Goal: slow automated brute-force, not lockout.
_FAILURE_WINDOW = timedelta(minutes=5)
_MAX_FAILURES = 10
_failure_log: dict[str, list[datetime]] = {}

# Separate tracker for bad API-key attempts. Keys are 256-bit random so brute
# force is not the real risk — the goal here is to stop a hostile scanner from
# filling the log with a WARNING per rejected key.
API_KEY_FAILURE_LOG_THRESHOLD = 10
_api_key_failure_log: dict[str, list[datetime]] = {}


def check_rate_limit(ip: str) -> bool:
    """Return False (and prune stale entries) if this IP is over the limit."""
    now = datetime.utcnow()
    cutoff = now - _FAILURE_WINDOW
    times = [t for t in _failure_log.get(ip, []) if t > cutoff]
    _failure_log[ip] = times
    if len(times) >= _MAX_FAILURES:
        log.warning("Login rate limit exceeded for IP %s (%d failures in last %dm)", ip, len(times), int(_FAILURE_WINDOW.total_seconds() / 60))
        return False
    return True


def record_failure(ip: str) -> None:
    _failure_log.setdefault(ip, []).append(datetime.utcnow())


def remaining_attempts(ip: str) -> int:
    """Return how many attempts remain for this IP in the current window."""
    now = datetime.utcnow()
    cutoff = now - _FAILURE_WINDOW
    times = [t for t in _failure_log.get(ip, []) if t > cutoff]
    return max(0, _MAX_FAILURES - len(times))


def clear_failures(ip: str) -> None:
    _failure_log.pop(ip, None)


def record_api_key_failure(ip: str) -> int:
    """Record a bad API-key attempt and return the current count in the window.

    Prunes stale entries in the same call so the map cannot grow forever from
    a scanner cycling through source IPs.
    """
    now = datetime.utcnow()
    cutoff = now - _FAILURE_WINDOW
    times = [t for t in _api_key_failure_log.get(ip, []) if t > cutoff]
    times.append(now)
    _api_key_failure_log[ip] = times
    return len(times)


# ── Password helpers ───────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_ctx.verify(plain, hashed)


# ── Session helpers ────────────────────────────────────────────────────────────

def create_session(db: Session) -> str:
    """Create a new auth session and return the opaque token."""
    from app.models import AuthSession
    token = secrets.token_urlsafe(32)
    expires = datetime.utcnow() + SESSION_LIFETIME
    db.add(AuthSession(token=token, expires_at=expires))
    db.commit()
    return token


def validate_session(token: str, db: Session) -> bool:
    """Return True if the token is valid and not expired. Extends expiry on use."""
    from app.models import AuthSession
    session = db.query(AuthSession).filter(AuthSession.token == token).first()
    if not session:
        return False
    if session.expires_at < datetime.utcnow():
        db.delete(session)
        db.commit()
        log.info("Expired session cleaned up")
        return False
    # Rolling expiry: extend on each use so active users stay logged in
    session.expires_at = datetime.utcnow() + SESSION_LIFETIME
    session.last_used_at = datetime.utcnow()
    db.commit()
    return True


def delete_session(token: str, db: Session) -> None:
    from app.models import AuthSession
    session = db.query(AuthSession).filter(AuthSession.token == token).first()
    if session:
        db.delete(session)
        db.commit()


def cleanup_expired_sessions(db: Session) -> None:
    from app.models import AuthSession
    db.query(AuthSession).filter(AuthSession.expires_at < datetime.utcnow()).delete()
    db.commit()


# ── API key helpers ────────────────────────────────────────────────────────────
# API keys are 256-bit random tokens, so unlike passwords there is nothing to
# guess and no need for a slow KDF. Plain SHA-256 keeps verification cheap —
# bcrypt's ~100 ms would be crippling when it runs on every single API request.

API_KEY_PREFIX = "cc_"
# "cc_" plus 8 characters — enough to tell keys apart in the settings list
_API_KEY_DISPLAY_CHARS = 11


def hash_api_key(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Return (plaintext, hash, display_prefix) for a brand-new key."""
    plain = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return plain, hash_api_key(plain), plain[:_API_KEY_DISPLAY_CHARS]


def extract_api_key(request) -> Optional[str]:
    """Pull a key from either 'Authorization: Bearer <key>' or 'X-API-Key: <key>'."""
    header = request.headers.get("Authorization", "")
    if header[:7].lower() == "bearer ":
        return header[7:].strip() or None
    return request.headers.get("X-API-Key", "").strip() or None


def validate_api_key(plain: str, db: Session) -> Optional[int]:
    """Return the key's id if it exists, else None. Records last_used_at.

    The id is returned rather than a bool so the request can know *which* key
    authenticated it, which is what lets a client revoke its own key on logout.
    """
    from app.models import ApiKey
    key = db.query(ApiKey).filter(ApiKey.key_hash == hash_api_key(plain)).first()
    if not key:
        return None
    key.last_used_at = datetime.utcnow()
    db.commit()
    return key.id


def is_api_enabled(db: Session) -> bool:
    """True when external API access has been switched on in settings."""
    from app.models import GlobalSettings
    gs = db.query(GlobalSettings).first()
    return bool(gs and gs.api_enabled)


# ── Auth state helpers ─────────────────────────────────────────────────────────

def is_auth_required(db: Session) -> bool:
    """True when the user has set up a login and it is currently enabled."""
    from app.models import GlobalSettings
    gs = db.query(GlobalSettings).first()
    return bool(gs and gs.auth_enabled and gs.auth_password_hash)
