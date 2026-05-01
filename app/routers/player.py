import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Episode, Feed, PlayerState
from app.routers.playlists import get_queue, _episode_out
from app.schemas import PlayerStateOut, PlayerPlayRequest, EpisodeOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/player", tags=["player"])


def _get_or_create_state(db: Session) -> PlayerState:
    state = db.get(PlayerState, 1)
    if not state:
        state = PlayerState(id=1)
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


def _build_state_out(state: PlayerState, db: Session) -> PlayerStateOut:
    out = PlayerStateOut(
        current_episode_id=state.current_episode_id,
        context_type=state.context_type,
        context_id=state.context_id,
        context_filter=state.context_filter,
    )
    if state.current_episode_id:
        ep = db.get(Episode, state.current_episode_id)
        if ep:
            out.current_episode = _episode_out(ep, db)

    if state.context_type and state.context_id:
        episodes = get_queue(state.context_type, state.context_id, state.context_filter, db)
        out.queue = [_episode_out(ep, db) for ep in episodes]
        if state.current_episode_id:
            ids = [ep.id for ep in episodes]
            idx = next((i for i, ep in enumerate(episodes) if ep.id == state.current_episode_id), None)
            out.queue_position = idx

    return out


def _smart_start(context_type: str, context_id: int, context_filter: str, db: Session) -> Episode | None:
    episodes = get_queue(context_type, context_id, context_filter, db)
    if not episodes:
        return None
    # Most recent partially played episode (has position, not fully played)
    in_progress = [ep for ep in episodes if ep.play_position_seconds > 0 and not ep.played]
    if in_progress:
        return max(in_progress, key=lambda ep: ep.last_played_at or datetime.min)
    # Otherwise most recent unplayed (queue is oldest-first, so last = most recent)
    unplayed = [ep for ep in episodes if not ep.played]
    if unplayed:
        return unplayed[-1]
    return episodes[-1]


@router.get("/state", response_model=PlayerStateOut)
def get_state(db: Session = Depends(get_db)):
    state = _get_or_create_state(db)
    return _build_state_out(state, db)


@router.post("/play", response_model=PlayerStateOut)
def play(body: PlayerPlayRequest, db: Session = Depends(get_db)):
    state = _get_or_create_state(db)

    if body.episode_id:
        ep = db.get(Episode, body.episode_id)
        if not ep:
            raise HTTPException(404, "Episode not found")
        start_ep = ep
    else:
        start_ep = _smart_start(body.context_type, body.context_id, body.context_filter, db)
        if not start_ep:
            raise HTTPException(404, "No playable episodes found")

    state.context_type = body.context_type
    state.context_id = body.context_id
    state.context_filter = body.context_filter
    state.current_episode_id = start_ep.id
    state.updated_at = datetime.utcnow()
    db.commit()

    return _build_state_out(state, db)


@router.post("/next", response_model=PlayerStateOut)
def next_episode(db: Session = Depends(get_db)):
    state = _get_or_create_state(db)
    if not state.context_type or not state.context_id:
        raise HTTPException(400, "No active playlist context")

    episodes = get_queue(state.context_type, state.context_id, state.context_filter, db)
    if not episodes:
        raise HTTPException(404, "Queue is empty")

    ids = [ep.id for ep in episodes]
    if state.current_episode_id and state.current_episode_id in ids:
        idx = ids.index(state.current_episode_id)
        next_idx = idx + 1
    else:
        next_idx = 0

    if next_idx >= len(episodes):
        # End of queue — clear current episode but keep context
        state.current_episode_id = None
        state.updated_at = datetime.utcnow()
        db.commit()
        return _build_state_out(state, db)

    state.current_episode_id = episodes[next_idx].id
    state.updated_at = datetime.utcnow()
    db.commit()
    return _build_state_out(state, db)


@router.post("/prev", response_model=PlayerStateOut)
def prev_episode(db: Session = Depends(get_db)):
    state = _get_or_create_state(db)
    if not state.context_type or not state.context_id:
        raise HTTPException(400, "No active playlist context")

    episodes = get_queue(state.context_type, state.context_id, state.context_filter, db)
    if not episodes:
        raise HTTPException(404, "Queue is empty")

    ids = [ep.id for ep in episodes]
    if state.current_episode_id and state.current_episode_id in ids:
        idx = ids.index(state.current_episode_id)
        prev_idx = idx - 1
    else:
        prev_idx = len(episodes) - 1

    if prev_idx < 0:
        prev_idx = 0

    state.current_episode_id = episodes[prev_idx].id
    state.updated_at = datetime.utcnow()
    db.commit()
    return _build_state_out(state, db)


@router.put("/state", response_model=PlayerStateOut)
def update_state(body: PlayerPlayRequest, db: Session = Depends(get_db)):
    """Direct state update — sets context and current episode without smart-start logic."""
    state = _get_or_create_state(db)
    state.context_type = body.context_type
    state.context_id = body.context_id
    state.context_filter = body.context_filter
    if body.episode_id:
        state.current_episode_id = body.episode_id
    state.updated_at = datetime.utcnow()
    db.commit()
    return _build_state_out(state, db)
