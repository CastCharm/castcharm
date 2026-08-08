"""External API key management.

Keys let non-browser clients (scripts, mobile apps) reach the same /api/*
surface the web UI uses. Managing the keys themselves is deliberately
session-only: a leaked key must not be able to mint replacements for itself or
quietly revoke the key you would use to lock it out.
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth import generate_api_key
from app.database import get_db
from app.models import ApiKey
from app.schemas import (
    ApiKeyCreate, ApiKeyCreated, ApiKeyOut, ApiKeyPurgeResult, ApiKeyRename,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings/api-keys", tags=["api-keys"])


def _require_session(request: Request) -> None:
    """Reject callers authenticated by an API key; browser sessions only.

    auth_method is set by AuthMiddleware. It is absent only when auth is off
    entirely, which is already an open instance — no reason to block there.
    """
    if getattr(request.state, "auth_method", None) == "api_key":
        raise HTTPException(
            status_code=403,
            detail="API keys cannot manage API keys. Sign in to the web UI.",
        )


@router.get("", response_model=list[ApiKeyOut])
def list_api_keys(request: Request, db: Session = Depends(get_db)):
    _require_session(request)
    return db.query(ApiKey).order_by(ApiKey.created_at.desc()).all()


@router.post("", response_model=ApiKeyCreated)
def create_api_key(body: ApiKeyCreate, request: Request, db: Session = Depends(get_db)):
    _require_session(request)
    plain, key_hash, prefix = generate_api_key()
    key = ApiKey(name=body.name, key_hash=key_hash, key_prefix=prefix)
    db.add(key)
    db.commit()
    db.refresh(key)
    log.info("API key '%s' created (%s…)", key.name, key.key_prefix)
    return ApiKeyCreated(**ApiKeyOut.model_validate(key).model_dump(), key=plain)


@router.post("/purge-unused", response_model=ApiKeyPurgeResult)
def purge_unused_keys(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(default=30, ge=0, le=3650),
):
    """Delete every key that is either never-used or unused for `days` days.

    Two orphan sources this cleans up: (1) enrolments where the exchange-key
    response was lost in flight so the client never received the plaintext, and
    (2) devices where the user uninstalled without logging out. The default of
    30 days is aggressive enough to matter without churning healthy devices.
    """
    _require_session(request)
    cutoff = datetime.utcnow() - timedelta(days=days)
    q = db.query(ApiKey).filter(
        or_(ApiKey.last_used_at.is_(None), ApiKey.last_used_at < cutoff)
    )
    count = q.delete(synchronize_session=False)
    db.commit()
    log.info("Purged %d unused API key(s) (threshold=%d days)", count, days)
    return ApiKeyPurgeResult(revoked=count)


# Declared before /{key_id} so "self" isn't captured by the int path parameter.
@router.delete("/self", status_code=204)
def revoke_own_key(request: Request, db: Session = Depends(get_db)):
    """Let a client revoke the key it is currently using — native-app logout.

    This is the one endpoint here that accepts API-key auth. Deleting your own
    credential is not an escalation, and without it a device that logs out would
    strand a live key on the server forever.
    """
    key_id = getattr(request.state, "api_key_id", None)
    if key_id is None:
        raise HTTPException(
            status_code=400,
            detail="This endpoint requires API key authentication",
        )
    key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")
    name, prefix = key.name, key.key_prefix
    db.delete(key)
    db.commit()
    log.info("API key '%s' revoked itself (%s…)", name, prefix)


@router.delete("/{key_id}", status_code=204)
def revoke_api_key(key_id: int, request: Request, db: Session = Depends(get_db)):
    _require_session(request)
    key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")
    # Read these before the delete — the instance is expired once committed.
    name, prefix = key.name, key.key_prefix
    db.delete(key)
    db.commit()
    log.info("API key '%s' revoked (%s…)", name, prefix)


@router.patch("/{key_id}", response_model=ApiKeyOut)
def rename_api_key(
    key_id: int,
    body: ApiKeyRename,
    request: Request,
    db: Session = Depends(get_db),
):
    """Rename a key so the user can tell two identical devices apart."""
    _require_session(request)
    key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")
    old_name = key.name
    key.name = body.name
    db.commit()
    db.refresh(key)
    log.info("API key %s… renamed '%s' → '%s'", key.key_prefix, old_name, key.name)
    return key
