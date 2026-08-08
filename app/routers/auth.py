"""Authentication and first-run setup endpoints."""
import logging
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.auth import (
    COOKIE_NAME,
    SESSION_LIFETIME,
    SECURE_COOKIES,
    check_rate_limit,
    clear_failures,
    create_session,
    delete_session,
    extract_api_key,
    generate_api_key,
    hash_password,
    is_api_enabled,
    validate_api_key,
    record_failure,
    remaining_attempts,
    validate_session,
    verify_password,
    is_auth_required,
)
from app.database import get_db
from app.schemas import ApiKeyCreated, ApiKeyOut

log = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

# IPs from which X-Forwarded-For is trusted (loopback = same-host reverse proxy).
# The default covers a proxy running on the same host; CASTCHARM_TRUSTED_PROXIES
# accepts a comma-separated list of additional IPs for container-network setups
# where Traefik / nginx runs in its own container with a different address.
import os as _os
_extra_proxies = {
    p.strip() for p in _os.environ.get("CASTCHARM_TRUSTED_PROXIES", "").split(",") if p.strip()
}
_TRUSTED_PROXY_IPS = frozenset({"127.0.0.1", "::1"} | _extra_proxies)
if _extra_proxies:
    log.info("Trusting X-Forwarded-For from additional proxies: %s", sorted(_extra_proxies))


def _get_client_ip(request: Request) -> str:
    """Return the real client IP, honouring X-Forwarded-For only from trusted proxies."""
    direct_ip = request.client.host if request.client else "unknown"
    if direct_ip in _TRUSTED_PROXY_IPS:
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
    return direct_ip


# ── Schemas ───────────────────────────────────────────────────────────────────

class AuthStatusOut(BaseModel):
    setup_complete: bool
    auth_enabled: bool
    logged_in: bool


class LoginRequest(BaseModel):
    username: str
    password: str


class CredentialsUpdate(BaseModel):
    current_password: str
    new_username: str
    new_password: str
    # When True, delete every AuthSession row *except* the one the caller is
    # holding. Used for the "sign out other browser sessions" checkbox on the
    # Security panel — a natural expectation when changing the password because
    # of a suspected compromise.
    revoke_other_sessions: bool = False
    # When True, delete every ApiKey row so no Android/script credential
    # survives the password change. Also lets the user roll their keys wholesale
    # without visiting the External API panel.
    revoke_all_api_keys: bool = False

    @field_validator("new_password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

    @field_validator("new_username")
    @classmethod
    def username_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Username must not be empty")
        return v


class ExchangeKeyRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name must not be empty")
        return v


class SetupCompleteRequest(BaseModel):
    # Auth
    enable_auth: bool = False
    username: Optional[str] = None
    password: Optional[str] = None
    # Settings applied during wizard
    theme: Optional[str] = None
    download_path: Optional[str] = None
    filename_date_prefix: Optional[bool] = None
    filename_episode_number: Optional[bool] = None
    organize_by_year: Optional[bool] = None
    save_xml: Optional[bool] = None
    timezone: Optional[str] = None
    api_enabled: Optional[bool] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/api/auth/status", response_model=AuthStatusOut)
def auth_status(
    request: Request,
    db: Session = Depends(get_db),
    cc_session: Optional[str] = Cookie(default=None),
):
    from app.models import GlobalSettings
    gs = db.query(GlobalSettings).first()
    setup_complete = bool(gs and gs.setup_complete)
    auth_enabled = bool(gs and gs.auth_enabled and gs.auth_password_hash)
    if not auth_enabled:
        logged_in = True  # no auth configured = always accessible
    elif cc_session and validate_session(cc_session, db):
        logged_in = True
    else:
        # A client authenticating by API key is logged in just as much as one
        # holding a cookie. Without this, a native app whose cookie has lapsed
        # would be sent back to the login screen despite holding a valid key —
        # which is the exact problem keys exist to solve.
        api_key = extract_api_key(request)
        logged_in = bool(
            api_key and is_api_enabled(db) and validate_api_key(api_key, db) is not None
        )
    return AuthStatusOut(
        setup_complete=setup_complete,
        auth_enabled=auth_enabled,
        logged_in=logged_in,
    )


@router.post("/api/auth/login")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    ip = _get_client_ip(request)

    if not check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Too many login attempts. Please wait a few minutes.")

    from app.models import GlobalSettings
    gs = db.query(GlobalSettings).first()
    if not gs or not gs.auth_enabled or not gs.auth_password_hash:
        raise HTTPException(status_code=400, detail="Authentication is not configured")

    username_ok = (body.username == gs.auth_username)
    # Always run bcrypt regardless of username match so response time is constant
    # (prevents a timing oracle that would reveal whether a username exists).
    password_ok = verify_password(body.password, gs.auth_password_hash)

    if not username_ok or not password_ok:
        record_failure(ip)
        log.warning("Failed login attempt for username '%s' from %s", body.username, ip)
        left = remaining_attempts(ip)
        body_content: dict = {"detail": "Username and password do not match."}
        if left <= 3:
            body_content["remaining"] = left
        return JSONResponse(status_code=401, content=body_content)

    clear_failures(ip)
    token = create_session(db)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        samesite="strict",
        secure=SECURE_COOKIES,
        path="/",
    )
    log.info("Login successful for user '%s' from %s", body.username, ip)
    return {"ok": True}


@router.post("/api/auth/logout")
def logout(
    response: Response,
    db: Session = Depends(get_db),
    cc_session: Optional[str] = Cookie(default=None),
):
    if cc_session:
        delete_session(cc_session, db)
    response.delete_cookie(key=COOKIE_NAME, path="/")
    log.info("User logged out")
    return {"ok": True}


@router.put("/api/auth/credentials")
def update_credentials(
    body: CredentialsUpdate,
    db: Session = Depends(get_db),
    cc_session: Optional[str] = Cookie(default=None),
):
    """Update username and password. Requires current password to confirm identity.

    Two optional flags let the caller nuke ambient credentials in the same
    request — natural if they're changing password because of a suspected
    compromise:
      revoke_other_sessions — deletes every AuthSession except the caller's
      revoke_all_api_keys   — deletes every ApiKey (every device must re-enrol)
    """
    from app.models import AuthSession, ApiKey, GlobalSettings
    gs = db.query(GlobalSettings).first()
    if not gs:
        raise HTTPException(status_code=404, detail="Settings not found")

    # Must be authenticated to change credentials
    if is_auth_required(db):
        if not cc_session or not validate_session(cc_session, db):
            raise HTTPException(status_code=401, detail="Authentication required")
        if not verify_password(body.current_password, gs.auth_password_hash):
            raise HTTPException(status_code=401, detail="Current password is incorrect")

    gs.auth_username = body.new_username
    gs.auth_password_hash = hash_password(body.new_password)
    gs.auth_enabled = True

    revoked_sessions = 0
    if body.revoke_other_sessions:
        q = db.query(AuthSession)
        if cc_session:
            q = q.filter(AuthSession.token != cc_session)
        revoked_sessions = q.delete(synchronize_session=False)

    revoked_keys = 0
    if body.revoke_all_api_keys:
        revoked_keys = db.query(ApiKey).delete(synchronize_session=False)

    db.commit()
    log.info(
        "Credentials updated for user '%s' (revoked_sessions=%d, revoked_keys=%d)",
        body.new_username, revoked_sessions, revoked_keys,
    )
    return {"ok": True, "revoked_sessions": revoked_sessions, "revoked_keys": revoked_keys}


@router.post("/api/auth/disable")
def disable_auth(
    response: Response,
    db: Session = Depends(get_db),
    cc_session: Optional[str] = Cookie(default=None),
):
    """Disable login requirement entirely. Requires current login to confirm."""
    from app.models import GlobalSettings, AuthSession
    gs = db.query(GlobalSettings).first()
    if not gs:
        raise HTTPException(status_code=404, detail="Settings not found")

    if is_auth_required(db):
        if not cc_session or not validate_session(cc_session, db):
            raise HTTPException(status_code=401, detail="Authentication required")

    gs.auth_enabled = False
    gs.auth_username = None
    gs.auth_password_hash = None
    db.query(AuthSession).delete()
    db.commit()
    response.delete_cookie(key=COOKIE_NAME, path="/")
    log.info("Authentication disabled")
    return {"ok": True}


@router.post("/api/auth/exchange-key", response_model=ApiKeyCreated)
def exchange_session_for_key(
    body: ExchangeKeyRequest,
    db: Session = Depends(get_db),
    cc_session: Optional[str] = Cookie(default=None),
):
    """Trade a valid login session for a long-lived API key.

    Lets a native client log in once with username and password, then hold a
    credential that never expires — instead of a session cookie whose client-side
    lifetime is fixed at issue time and silently lapses.

    SECURITY: /api/auth/ is exempt from AuthMiddleware (see _AUTH_EXEMPT_PREFIXES
    in app/main.py), so this endpoint has to check the session itself, the same
    way update_credentials and disable_auth do. Without that check anyone able to
    reach the port could mint a key without ever logging in.

    A caller holding only an API key has no session cookie, so it fails the check
    below — keys cannot mint further keys, matching app/routers/api_keys.py.
    """
    from app.models import ApiKey, GlobalSettings

    if is_auth_required(db):
        if not cc_session or not validate_session(cc_session, db):
            raise HTTPException(status_code=401, detail="Authentication required")

    gs = db.query(GlobalSettings).first()
    if not gs or not gs.api_enabled:
        raise HTTPException(
            status_code=403,
            detail="External API access is disabled. Enable it under Settings → External API.",
        )

    plain, key_hash, prefix = generate_api_key()
    key = ApiKey(name=body.name, key_hash=key_hash, key_prefix=prefix)
    db.add(key)
    db.commit()
    db.refresh(key)
    log.info("API key '%s' issued via session exchange (%s…)", key.name, key.key_prefix)
    return ApiKeyCreated(**ApiKeyOut.model_validate(key).model_dump(), key=plain)


@router.post("/api/setup/complete")
def complete_setup(
    body: SetupCompleteRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    """Mark setup as complete, apply wizard settings, and trigger the startup scan."""
    from app.models import GlobalSettings
    gs = db.query(GlobalSettings).first()
    if not gs:
        raise HTTPException(status_code=404, detail="Settings not found")

    if gs.setup_complete:
        raise HTTPException(status_code=403, detail="Setup has already been completed")

    if body.enable_auth:
        if not body.username or not body.password:
            raise HTTPException(status_code=400, detail="Username and password are required")
        if len(body.password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
        gs.auth_enabled = True
        gs.auth_username = body.username.strip()
        gs.auth_password_hash = hash_password(body.password)
    else:
        gs.auth_enabled = False

    if body.theme is not None:
        gs.theme = body.theme
    if body.download_path is not None:
        gs.download_path = body.download_path
    if body.filename_date_prefix is not None:
        gs.filename_date_prefix = body.filename_date_prefix
    if body.filename_episode_number is not None:
        gs.filename_episode_number = body.filename_episode_number
    if body.organize_by_year is not None:
        gs.organize_by_year = body.organize_by_year
    if body.save_xml is not None:
        gs.save_xml = body.save_xml
    if body.timezone is not None:
        gs.timezone = body.timezone
    if body.api_enabled is not None:
        gs.api_enabled = body.api_enabled

    gs.setup_complete = True
    db.commit()
    log.info("Setup completed. Auth: %s", "enabled" if gs.auth_enabled else "disabled")

    # Create a session cookie if auth was just configured
    if gs.auth_enabled:
        token = create_session(db)
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            max_age=int(SESSION_LIFETIME.total_seconds()),
            httponly=True,
            samesite="strict",
            path="/",
        )

    # Now that setup is complete, kick off the startup scan
    from app.startup_scan import run_in_background as _startup_scan
    _startup_scan()

    return {"ok": True}
