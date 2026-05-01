import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Playlist, PlaylistEpisode, Episode, Feed
from app.schemas import (
    PlaylistOut, PlaylistCreate, PlaylistUpdate,
    PlaylistEpisodeAdd, PlaylistReorder, EpisodeOut,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/playlists", tags=["playlists"])


def _episode_out(ep: Episode, db: Session) -> EpisodeOut:
    feed = db.get(Feed, ep.feed_id)
    d = EpisodeOut.model_validate(ep)
    d.feed_title = feed.title if feed else None
    d.feed_image_url = feed.image_url if feed else None
    return d


def _feed_queue(feed_id: int, filter_: str, db: Session) -> list[Episode]:
    q = (
        db.query(Episode)
        .filter(
            Episode.feed_id == feed_id,
            Episode.hidden.is_(False),
            Episode.status == "downloaded",
        )
    )
    if filter_ == "unplayed":
        q = q.filter(Episode.played.is_(False))
    return q.order_by(Episode.published_at.asc()).all()


def _playlist_queue(playlist_id: int, db: Session) -> list[Episode]:
    rows = (
        db.query(PlaylistEpisode)
        .filter(PlaylistEpisode.playlist_id == playlist_id)
        .order_by(PlaylistEpisode.position)
        .all()
    )
    return [r.episode for r in rows]


def get_queue(context_type: str, context_id: int, context_filter: str | None, db: Session) -> list[Episode]:
    if context_type == "feed":
        return _feed_queue(context_id, context_filter or "unplayed", db)
    return _playlist_queue(context_id, db)


@router.get("", response_model=list[PlaylistOut])
def list_playlists(db: Session = Depends(get_db)):
    rows = db.query(Playlist).order_by(Playlist.created_at).all()
    out = []
    for pl in rows:
        o = PlaylistOut.model_validate(pl)
        if pl.type == "feed" and pl.feed_id:
            o.episode_count = len(_feed_queue(pl.feed_id, pl.filter, db))
        else:
            o.episode_count = db.query(PlaylistEpisode).filter(
                PlaylistEpisode.playlist_id == pl.id
            ).count()
        out.append(o)
    return out


@router.post("", response_model=PlaylistOut, status_code=201)
def create_playlist(body: PlaylistCreate, db: Session = Depends(get_db)):
    pl = Playlist(
        name=body.name,
        description=body.description,
        type=body.type,
        feed_id=body.feed_id,
        filter=body.filter,
    )
    db.add(pl)
    db.commit()
    db.refresh(pl)
    o = PlaylistOut.model_validate(pl)
    o.episode_count = 0
    return o


@router.get("/feed-memberships")
def feed_memberships(feed_id: int, db: Session = Depends(get_db)):
    """Return IDs of episodes in the given feed that belong to any custom playlist."""
    rows = (
        db.query(PlaylistEpisode.episode_id)
        .join(Episode, Episode.id == PlaylistEpisode.episode_id)
        .filter(Episode.feed_id == feed_id)
        .all()
    )
    return {"episode_ids": [r.episode_id for r in rows]}


@router.get("/episode-memberships", response_model=list[PlaylistOut])
def episode_memberships(episode_id: int, db: Session = Depends(get_db)):
    """Return all custom playlists that contain the given episode."""
    rows = (
        db.query(PlaylistEpisode)
        .filter(PlaylistEpisode.episode_id == episode_id)
        .all()
    )
    playlist_ids = {r.playlist_id for r in rows}
    out = []
    for pl in db.query(Playlist).filter(Playlist.id.in_(playlist_ids)).all():
        o = PlaylistOut.model_validate(pl)
        o.episode_count = db.query(PlaylistEpisode).filter(PlaylistEpisode.playlist_id == pl.id).count()
        out.append(o)
    return out


@router.get("/{playlist_id}", response_model=PlaylistOut)
def get_playlist(playlist_id: int, db: Session = Depends(get_db)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(404, "Playlist not found")
    o = PlaylistOut.model_validate(pl)
    if pl.type == "feed" and pl.feed_id:
        o.episode_count = len(_feed_queue(pl.feed_id, pl.filter, db))
    else:
        o.episode_count = db.query(PlaylistEpisode).filter(
            PlaylistEpisode.playlist_id == pl.id
        ).count()
    return o


@router.put("/{playlist_id}", response_model=PlaylistOut)
def update_playlist(playlist_id: int, body: PlaylistUpdate, db: Session = Depends(get_db)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(404, "Playlist not found")
    if body.name is not None:
        pl.name = body.name
    if body.description is not None:
        pl.description = body.description
    if body.filter is not None:
        pl.filter = body.filter
    db.commit()
    db.refresh(pl)
    o = PlaylistOut.model_validate(pl)
    if pl.type == "feed" and pl.feed_id:
        o.episode_count = len(_feed_queue(pl.feed_id, pl.filter, db))
    else:
        o.episode_count = db.query(PlaylistEpisode).filter(
            PlaylistEpisode.playlist_id == pl.id
        ).count()
    return o


@router.delete("/{playlist_id}", status_code=204)
def delete_playlist(playlist_id: int, db: Session = Depends(get_db)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(404, "Playlist not found")
    db.delete(pl)
    db.commit()


@router.get("/{playlist_id}/episodes", response_model=list[EpisodeOut])
def get_playlist_episodes(playlist_id: int, db: Session = Depends(get_db)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(404, "Playlist not found")
    episodes = get_queue(pl.type, pl.feed_id if pl.type == "feed" else pl.id, pl.filter, db)
    return [_episode_out(ep, db) for ep in episodes]


@router.post("/{playlist_id}/episodes", status_code=204)
def add_episode(playlist_id: int, body: PlaylistEpisodeAdd, db: Session = Depends(get_db)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(404, "Playlist not found")
    if pl.type != "custom":
        raise HTTPException(400, "Cannot manually add episodes to a feed playlist")
    ep = db.get(Episode, body.episode_id)
    if not ep:
        raise HTTPException(404, "Episode not found")
    existing = db.query(PlaylistEpisode).filter(
        PlaylistEpisode.playlist_id == playlist_id,
        PlaylistEpisode.episode_id == body.episode_id,
    ).first()
    if existing:
        return  # already in playlist, silently ignore
    max_pos = db.query(PlaylistEpisode).filter(
        PlaylistEpisode.playlist_id == playlist_id
    ).count()
    db.add(PlaylistEpisode(playlist_id=playlist_id, episode_id=body.episode_id, position=max_pos))
    db.commit()


@router.delete("/{playlist_id}/episodes/{episode_id}", status_code=204)
def remove_episode(playlist_id: int, episode_id: int, db: Session = Depends(get_db)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(404, "Playlist not found")
    if pl.type != "custom":
        raise HTTPException(400, "Cannot remove episodes from a feed playlist")
    row = db.query(PlaylistEpisode).filter(
        PlaylistEpisode.playlist_id == playlist_id,
        PlaylistEpisode.episode_id == episode_id,
    ).first()
    if row:
        db.delete(row)
        db.commit()
        _repack_positions(playlist_id, db)


@router.put("/{playlist_id}/episodes/reorder", status_code=204)
def reorder_episodes(playlist_id: int, body: PlaylistReorder, db: Session = Depends(get_db)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(404, "Playlist not found")
    if pl.type != "custom":
        raise HTTPException(400, "Cannot reorder a feed playlist")
    rows = {
        r.episode_id: r
        for r in db.query(PlaylistEpisode).filter(PlaylistEpisode.playlist_id == playlist_id)
    }
    for pos, ep_id in enumerate(body.episode_ids):
        if ep_id in rows:
            rows[ep_id].position = pos
    db.commit()


def _repack_positions(playlist_id: int, db: Session):
    rows = (
        db.query(PlaylistEpisode)
        .filter(PlaylistEpisode.playlist_id == playlist_id)
        .order_by(PlaylistEpisode.position)
        .all()
    )
    for i, row in enumerate(rows):
        row.position = i
    db.commit()
