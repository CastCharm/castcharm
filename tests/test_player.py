"""Listen-in-order: start point, stepping, idempotent played, next_up."""
from datetime import datetime, timedelta

import pytest

from app.models import Episode, Feed


def _session():
    import app.database as adb
    return adb.SessionLocal()


_counter = [0]


def _feed_with_episodes(client, play_order=None, n=3):
    _counter[0] += 1
    r = client.post("/api/feeds/manual", json={"title": f"Story {_counter[0]}"})
    assert r.status_code in (200, 201), r.text
    fid = r.json()["id"]
    db = _session()
    try:
        feed = db.get(Feed, fid)
        feed.play_order = play_order
        base = datetime(2024, 1, 1)
        ids = []
        for i in range(n):
            ep = Episode(feed_id=fid, title=f"Chapter {i + 1}", guid=f"g{i}", episode_number=i + 1,
                         published_at=base + timedelta(days=i), status="downloaded",
                         file_path=f"/nonexistent/{i}.mp3", duration="600")
            db.add(ep)
            db.flush()
            ids.append(ep.id)
        db.commit()
    finally:
        db.close()
    return fid, ids


def _play(client, fid, **extra):
    r = client.post("/api/player/play", json={"context_type": "feed", "context_id": fid,
                                             "context_filter": "unplayed", **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_in_order_feed_starts_at_oldest_unplayed(client):
    fid, ids = _feed_with_episodes(client, "oldest")
    st = _play(client, fid)
    assert st["current_episode_id"] == ids[0] and st["queue_position"] == 0
    assert [e["id"] for e in st["queue"]] == ids


def test_newest_first_feed_unchanged(client):
    fid, ids = _feed_with_episodes(client, "newest")
    assert _play(client, fid)["current_episode_id"] == ids[-1]


def test_resume_beats_oldest(client):
    fid, ids = _feed_with_episodes(client, "oldest")
    client.post(f"/api/episodes/{ids[1]}/progress", json={"position_seconds": 120})
    st = _play(client, fid)
    assert st["current_episode_id"] == ids[1]
    nu = client.get(f"/api/feeds/{fid}").json()["next_up"]
    assert nu["episode_id"] == ids[1] and nu["resume"] is True and nu["position_seconds"] == 120


def test_next_after_current_marked_played_steps_in_order(client):
    fid, ids = _feed_with_episodes(client, "oldest", n=4)
    _play(client, fid)                                             # ch1
    # Finish ch1 the way the players do: set played, then ask for next.
    client.post(f"/api/episodes/{ids[0]}/played", json={"played": True})
    assert client.post("/api/player/next").json()["current_episode_id"] == ids[1]
    # Skip ahead: ch2 is marked played by the user while ch3 was already played earlier.
    client.post(f"/api/episodes/{ids[2]}/played", json={"played": True})
    client.post(f"/api/episodes/{ids[1]}/played", json={"played": True})
    assert client.post("/api/player/next").json()["current_episode_id"] == ids[3]
    # End of queue clears the current episode but keeps the context.
    client.post(f"/api/episodes/{ids[3]}/played", json={"played": True})
    st = client.post("/api/player/next").json()
    assert st["current_episode_id"] is None and st["context_id"] == fid


def test_prev_steps_back_over_played(client):
    fid, ids = _feed_with_episodes(client, "oldest", n=3)
    _play(client, fid, episode_id=ids[2])
    client.post(f"/api/episodes/{ids[1]}/played", json={"played": True})
    assert client.post("/api/player/prev").json()["current_episode_id"] == ids[0]


def test_set_played_is_idempotent_and_toggle_still_toggles(client):
    fid, ids = _feed_with_episodes(client, "oldest")
    url = f"/api/episodes/{ids[0]}/played"
    assert client.post(url, json={"played": True}).json()["played"] is True
    assert client.post(url, json={"played": True}).json()["played"] is True
    assert client.post(url).json()["played"] is False
    assert client.post(url).json()["played"] is True


def test_next_up_absent_for_newest_first_and_when_caught_up(client):
    fid, ids = _feed_with_episodes(client, "newest")
    assert client.get(f"/api/feeds/{fid}").json()["next_up"] is None
    fid2, ids2 = _feed_with_episodes(client, "oldest", n=1)
    client.post(f"/api/episodes/{ids2[0]}/played", json={"played": True})
    body = client.get(f"/api/feeds/{fid2}").json()
    assert body["play_order"] == "oldest" and body["next_up"] is None


def test_play_order_round_trips_and_validates(client):
    fid, _ = _feed_with_episodes(client, None)
    assert client.put(f"/api/feeds/{fid}", json={"play_order": "oldest"}).json()["play_order"] == "oldest"
    assert client.get(f"/api/feeds/{fid}").json()["play_order"] == "oldest"
    assert client.put(f"/api/feeds/{fid}", json={"play_order": "sideways"}).status_code == 422


def test_serial_feed_autodetects_only_when_unset(client, monkeypatch):
    import feedparser
    import app.rss_parser as rp
    from app.rss_parser import sync_feed_episodes
    xml = """<?xml version="1.0"?><rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
    <channel><title>Serial Show</title><itunes:type>serial</itunes:type>
    <item><title>One</title><guid>s1</guid><enclosure url="https://x.test/1.mp3" type="audio/mpeg" length="1"/></item>
    </channel></rss>"""
    real_parse = feedparser.parse
    monkeypatch.setattr(rp.feedparser, "parse", lambda src, *a, **k: real_parse(xml))
    db = _session()
    try:
        f = Feed(title="Serial Show", url="https://x.test/feed.xml")
        db.add(f); db.commit()
        sync_feed_episodes(f, db)
        assert f.play_order == "oldest"
        f.play_order = "newest"; db.commit()
        sync_feed_episodes(f, db)
        assert f.play_order == "newest"          # user choice survives a resync
    finally:
        db.close()


def test_default_play_order_applies_to_feeds_without_their_own(client):
    # Out of the box, an unconfigured feed is listened to in chronological order.
    fid, ids = _feed_with_episodes(client, None)
    body = client.get(f"/api/feeds/{fid}").json()
    assert body["play_order"] == "oldest" and body["next_up"]["episode_id"] == ids[0]
    assert _play(client, fid)["current_episode_id"] == ids[0]
    # Changing the instance default changes every feed that has no setting of its own…
    r = client.put("/api/settings", json={"default_play_order": "newest"})
    assert r.status_code == 200 and r.json()["default_play_order"] == "newest"
    body = client.get(f"/api/feeds/{fid}").json()
    assert body["play_order"] == "newest" and body["next_up"] is None
    assert _play(client, fid)["current_episode_id"] == ids[-1]
    # …but a feed's own choice always wins.
    client.put(f"/api/feeds/{fid}", json={"play_order": "oldest"})
    assert _play(client, fid)["current_episode_id"] == ids[0]
    assert client.put("/api/settings", json={"default_play_order": "sideways"}).status_code == 422
