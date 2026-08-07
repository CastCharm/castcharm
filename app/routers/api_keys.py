"""External API key management.

Keys let non-browser clients (scripts, mobile apps) reach the same /api/*
surface the web UI uses. Managing the keys themselves is deliberately
session-only: a leaked key must not be able to mint replacements for itself or
quietly revoke the key you would use to lock it out.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth import generate_api_key
from app.database import get_db
from app.models import ApiKey
from app.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut

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
