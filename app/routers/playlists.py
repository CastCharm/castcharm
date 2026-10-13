import json
import logging
import os
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Playlist, PlaylistEpisode, Episode, Feed
from app.schemas import (
    PlaylistOut, PlaylistCreate, PlaylistUpdate,
    PlaylistEpisodeAdd, PlaylistReorder, EpisodeOut,
    SmartRules, SmartPreviewOut,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/playlists", tags=["playlists"])


def _episode_out(ep: Episode, db: Session) -> EpisodeOut:
    feed = db.get(Feed, ep.feed_id)
    d = EpisodeOut.model_validate(ep)
    d.feed_title = feed.title if feed else None
    d.feed_play_order = (feed.play_order or "newest") if feed else None
    # Artwork always resolves to this server, never to the podcast host — the
    # same rule the episodes and feeds routers follow. This one served RSS URLs
    # straight from the feed record, so a playlist was one more page quietly
    # telling third parties who was reading it and when.
    from app.routers.feeds import feed_cover_url
    d.feed_image_url = feed_cover_url(feed, db) if feed else None
    if not ep.custom_image_url:
        art_path = os.path.splitext(ep.file_path)[0] + ".jpg" if ep.file_path else None
        d.episode_image_url = (
            f"/api/episodes/{ep.id}/cover.jpg"
            if art_path and os.path.exists(art_path)
            else None
        )
    return d


def _feed_queue(feed_id: int, filter_: str, db: Session) -> list[Episode]:
    """Playable episodes of a podcast (primary + supplementary feeds), oldest
    first using the same ordering as episode numbering."""
    from app.routers.episodes import episode_order_key
    from app.utils import get_group_feed_ids
    q = (
        db.query(Episode)
        .filter(
            Episode.feed_id.in_(get_group_feed_ids(db, feed_id)),
            Episode.hidden.is_(False),
            Episode.status == "downloaded",
        )
    )
    if filter_ == "unplayed":
        q = q.filter(Episode.played.is_(False))
    return q.order_by(*episode_order_key()).all()


# ---------------------------------------------------------------------------
# Smart playlists: a saved query (schemas.SmartRules) evaluated live.
# ---------------------------------------------------------------------------

CATCHUP_NAME = "Get Caught Up"
CATCHUP_RULES = {"played": "unplayed", "published": {"within_days": 14}, "sort": "oldest"}


def _rules_of(pl: Playlist) -> dict:
    try:
        return json.loads(pl.rules) if pl.rules else {}
    except ValueError:
        return {}


def _dur_s(ep: Episode) -> int:
    from app.routers.stats import _parse_seconds
    return _parse_seconds(ep.duration)


def _smart_queue(rules: dict, db: Session, ignore_played: bool = False) -> list[Episode]:
    """Evaluate smart-playlist rules.  Base set: downloaded, not hidden.  Every
    present condition narrows it (AND).  *ignore_played* drops the played
    condition so next/previous can anchor on an episode that was just marked
    played (the same trick the feed context uses with its "all" filter)."""
    from app.routers.episodes import episode_order_key
    from app.utils import get_group_feed_ids
    q = (
        db.query(Episode)
        .options(joinedload(Episode.feed))
        .filter(Episode.hidden.is_(False), Episode.status == "downloaded")
    )
    feeds = rules.get("feeds") or {}
    if feeds.get("ids"):
        ids: set[int] = set()
        for fid in feeds["ids"]:
            ids.update(get_group_feed_ids(db, int(fid)))
        q = q.filter(Episode.feed_id.notin_(ids) if feeds.get("mode") == "exclude" else Episode.feed_id.in_(ids))
    if not ignore_played:
        played = rules.get("played", "unplayed")
        if played == "unplayed":
            q = q.filter(Episode.played.is_(False))
        elif played == "in_progress":
            q = q.filter(Episode.played.is_(False), Episode.play_position_seconds > 0)
        elif played == "played":
            q = q.filter(Episode.played.is_(True))
    pub = rules.get("published") or {}
    if pub.get("within_days"):
        q = q.filter(Episode.published_at >= datetime.utcnow() - timedelta(days=int(pub["within_days"])))
    if pub.get("after"):
        q = q.filter(Episode.published_at >= datetime.strptime(pub["after"], "%Y-%m-%d"))
    if pub.get("before"):
        q = q.filter(Episode.published_at < datetime.strptime(pub["before"], "%Y-%m-%d") + timedelta(days=1))
    if rules.get("title_contains"):
        # Literal text, so a typed % or _ does not turn into a wildcard.
        needle = rules["title_contains"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        q = q.filter(Episode.title.ilike(f"%{needle}%", escape="\\"))
    eps = q.order_by(*episode_order_key()).all()

    dur = rules.get("duration") or {}
    if dur.get("min_minutes") is not None:
        eps = [e for e in eps if _dur_s(e) >= int(dur["min_minutes"]) * 60]
    if dur.get("max_minutes") is not None:
        eps = [e for e in eps if _dur_s(e) <= int(dur["max_minutes"]) * 60]
    sort = rules.get("sort", "oldest")
    if sort == "newest":
        eps.reverse()
    elif sort == "shortest":
        eps.sort(key=_dur_s)
    elif sort == "longest":
        eps.sort(key=_dur_s, reverse=True)
    if rules.get("limit"):
        eps = eps[: int(rules["limit"])]
    return eps


def _playlist_queue(pl: Playlist, filter_: str, db: Session) -> list[Episode]:
    if pl.type == "smart":
        return _smart_queue(_rules_of(pl), db, ignore_played=(filter_ == "all"))
    rows = (
        db.query(PlaylistEpisode)
        .filter(PlaylistEpisode.playlist_id == pl.id)
        .order_by(PlaylistEpisode.position)
        .all()
    )
    return [r.episode for r in rows]


def get_queue(context_type: str, context_id: int, context_filter: str | None, db: Session) -> list[Episode]:
    if context_type == "feed":
        return _feed_queue(context_id, context_filter or "unplayed", db)
    pl = db.get(Playlist, context_id)
    return _playlist_queue(pl, context_filter or "unplayed", db) if pl else []


def _count_and_seconds(pl: Playlist, db: Session) -> tuple[int, int]:
    eps = _playlist_queue(pl, "unplayed", db)
    return len(eps), sum(_dur_s(e) for e in eps)


def _out(pl: Playlist, db: Session) -> PlaylistOut:
    o = PlaylistOut.model_validate(pl)
    o.rules = _rules_of(pl) if pl.type == "smart" else None
    o.is_system = bool(pl.is_system)
    o.episode_count, o.total_seconds = _count_and_seconds(pl, db)
    return o


def ensure_system_playlists(db: Session) -> None:
    """The built-in "Get Caught Up" list: unplayed episodes from the last 14 days,
    oldest first.  Created once; its rules stay the user's to edit."""
    existing = db.query(Playlist).filter(Playlist.is_system.is_(True)).first()
    if existing:
        if existing.name != CATCHUP_NAME:        # renamed in a later release
            existing.name = CATCHUP_NAME
            db.commit()
        return
    db.add(Playlist(name=CATCHUP_NAME, type="smart", rules=json.dumps(CATCHUP_RULES), is_system=True,
                    description="Unplayed episodes from the last two weeks, oldest first. Edit the rules to taste."))
    db.commit()
    log.info("Created the built-in '%s' smart playlist", CATCHUP_NAME)


@router.post("/preview", response_model=SmartPreviewOut)
def preview_smart_rules(body: SmartRules, db: Session = Depends(get_db)):
    """What a set of rules would select right now — for the live line in the editor."""
    eps = _smart_queue(body.model_dump(exclude_none=True), db)
    return SmartPreviewOut(
        count=len(eps),
        total_seconds=sum(_dur_s(e) for e in eps),
        sample=[{"id": e.id, "title": e.title, "feed_title": e.feed.title if e.feed else None,
                 "published_at": e.published_at.isoformat() if e.published_at else None} for e in eps[:5]],
    )


@router.get("", response_model=list[PlaylistOut])
def list_playlists(db: Session = Depends(get_db)):
    # Built-in lists first, then creation order.
    rows = db.query(Playlist).order_by(Playlist.is_system.desc(), Playlist.created_at).all()
    return [_out(pl, db) for pl in rows]


@router.post("", response_model=PlaylistOut, status_code=201)
def create_playlist(body: PlaylistCreate, db: Session = Depends(get_db)):
    pl = Playlist(
        name=body.name,
        description=body.description,
        type=body.type,                       # fixed for the life of the playlist
        rules=json.dumps(body.rules.model_dump(exclude_none=True)) if body.rules else None,
    )
    db.add(pl)
    db.commit()
    db.refresh(pl)
    return _out(pl, db)


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
        out.append(_out(pl, db))
    return out


@router.get("/{playlist_id}", response_model=PlaylistOut)
def get_playlist(playlist_id: int, db: Session = Depends(get_db)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(404, "Playlist not found")
    return _out(pl, db)


@router.put("/{playlist_id}", response_model=PlaylistOut)
def update_playlist(playlist_id: int, body: PlaylistUpdate, db: Session = Depends(get_db)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(404, "Playlist not found")
    if body.rules is not None and pl.type != "smart":
        raise HTTPException(400, "A manual playlist cannot have rules")
    # A built-in list keeps its name so the Home page can find it; its rules
    # and description are the user's.
    if body.name is not None and not pl.is_system:
        pl.name = body.name
    if body.description is not None:
        pl.description = body.description or None     # "" clears it
    if body.rules is not None:
        pl.rules = json.dumps(body.rules.model_dump(exclude_none=True))
    db.commit()
    db.refresh(pl)
    return _out(pl, db)


@router.delete("/{playlist_id}", status_code=204)
def delete_playlist(playlist_id: int, db: Session = Depends(get_db)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(404, "Playlist not found")
    if pl.is_system:
        raise HTTPException(400, f"'{pl.name}' is built in and cannot be deleted; edit its rules instead")
    db.delete(pl)
    db.commit()


@router.get("/{playlist_id}/episodes", response_model=list[EpisodeOut])
def get_playlist_episodes(playlist_id: int, db: Session = Depends(get_db)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(404, "Playlist not found")
    return [_episode_out(ep, db) for ep in _playlist_queue(pl, "unplayed", db)]


@router.post("/{playlist_id}/episodes", status_code=204)
def add_episode(playlist_id: int, body: PlaylistEpisodeAdd, db: Session = Depends(get_db)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(404, "Playlist not found")
    if pl.type != "custom":
        raise HTTPException(400, "A smart playlist picks its own episodes; edit its rules instead")
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
        raise HTTPException(400, "A smart playlist picks its own episodes; edit its rules instead")
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
        raise HTTPException(400, "A smart playlist orders itself; change its sort rule instead")
    rows = {
        r.episode_id: r
        for r in db.query(PlaylistEpisode).filter(PlaylistEpisode.playlist_id == playlist_id)
    }
    # An incomplete body is never a meaningful request, and silently accepting
    # one corrupts the playlist: positions are assigned 0..n-1 to the ids that
    # were sent while every omitted episode keeps its original position, so the
    # two sets collide and the resulting order is arbitrary. The schema already
    # states the contract ("a complete list, and truncating it would silently
    # reorder into the wrong shape") — this enforces it, for every client rather
    # than only the ones that remember.
    sent = set(body.episode_ids)
    if sent != set(rows) or len(body.episode_ids) != len(sent):
        raise HTTPException(
            400,
            "Reorder must list every episode in the playlist exactly once "
            f"({len(rows)} expected, {len(body.episode_ids)} received)",
        )
    for pos, ep_id in enumerate(body.episode_ids):
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
