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


def effective_play_order(feed: Feed | None, db: Session) -> str:
    """A feed's own setting, else the instance-wide default for new podcasts."""
    if feed is not None and feed.play_order:
        return feed.play_order
    from app.models import GlobalSettings
    gs = db.query(GlobalSettings).first()
    return (gs.default_play_order if gs and gs.default_play_order else "oldest")


def _feed_play_order(feed_id: int, db: Session) -> str:
    return effective_play_order(db.get(Feed, feed_id), db)


def _smart_start(context_type: str, context_id: int, context_filter: str, db: Session) -> Episode | None:
    """Which episode "Play" should start.

    Resume the episode most recently left mid-way, if any.  Otherwise a
    listen-in-order feed starts at the oldest unplayed episode and an
    ordinary feed at the newest.  Playlists keep the newest-unplayed rule.
    """
    episodes = get_queue(context_type, context_id, context_filter, db)
    if not episodes:
        return None
    in_progress = [ep for ep in episodes if ep.play_position_seconds > 0 and not ep.played]
    if in_progress:
        return max(in_progress, key=lambda ep: ep.last_played_at or datetime.min)
    unplayed = [ep for ep in episodes if not ep.played]
    in_order = context_type == "feed" and _feed_play_order(context_id, db) == "oldest"
    if unplayed:
        return unplayed[0] if in_order else unplayed[-1]
    return episodes[0] if in_order else episodes[-1]


def next_up_for_feed(feed_id: int, db: Session) -> dict | None:
    """The "Continue" summary a feed page shows for a listen-in-order feed."""
    if _feed_play_order(feed_id, db) != "oldest":
        return None
    ep = _smart_start("feed", feed_id, "unplayed", db)
    if ep is None or ep.played:
        return None
    return {
        "episode_id": ep.id,
        "seq_number": ep.seq_number,
        "title": ep.title,
        "position_seconds": ep.play_position_seconds or 0,
        "resume": bool(ep.play_position_seconds and ep.play_position_seconds > 0),
    }


def _step(state: PlayerState, db: Session, direction: int) -> PlayerStateOut:
    """Move to the neighbouring episode in the context's order.

    The current episode is located in the *unfiltered* order of the context,
    then we walk from there to the nearest episode that is still in the
    filtered queue.  So an episode that was just marked played (and so left
    an "unplayed" queue) still anchors the step — no more jumping to the
    start of the queue, and no race with mark-played.
    """
    if not state.context_type or not state.context_id:
        raise HTTPException(400, "No active playlist context")

    queue = get_queue(state.context_type, state.context_id, state.context_filter, db)
    if not queue:
        # Nothing left to play (everything is played, or nothing is downloaded).
        # That is the end of the queue, not an error: clear the current episode
        # and keep the context so a later "Play" can resume from here.
        if direction > 0:
            state.current_episode_id = None
            state.updated_at = datetime.utcnow()
            db.commit()
        return _build_state_out(state, db)
    full = get_queue(state.context_type, state.context_id, "all", db) if state.context_type == "feed" else queue
    queue_ids = {ep.id for ep in queue}
    full_ids = [ep.id for ep in full]

    target = None
    if state.current_episode_id in full_ids:
        i = full_ids.index(state.current_episode_id) + direction
        while 0 <= i < len(full_ids):
            if full_ids[i] in queue_ids:
                target = full[i]
                break
            i += direction
    else:
        target = queue[0] if direction > 0 else queue[-1]

    if target is None:
        if direction > 0:
            state.current_episode_id = None          # end of queue; keep context
        else:
            target = queue[0]
    if target is not None:
        state.current_episode_id = target.id
    state.updated_at = datetime.utcnow()
    db.commit()
    return _build_state_out(state, db)


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
    return _step(_get_or_create_state(db), db, +1)


@router.post("/prev", response_model=PlayerStateOut)
def prev_episode(db: Session = Depends(get_db)):
    return _step(_get_or_create_state(db), db, -1)


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
