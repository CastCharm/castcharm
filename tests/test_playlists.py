"""Manual vs smart playlists, the built-in Get Caught Up list, and rule evaluation."""
from datetime import datetime, timedelta

from app.models import Episode, Feed, Playlist


def _session():
    import app.database as adb
    return adb.SessionLocal()


def _feed(client, title):
    r = client.post("/api/feeds/manual", json={"title": title})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _episode(fid, title, days_ago, **kw):
    db = _session()
    try:
        e = Episode(feed_id=fid, title=title, guid=f"{fid}-{title}",
                    published_at=(datetime.utcnow() - timedelta(days=days_ago)) if days_ago is not None else None,
                    status=kw.pop("status", "downloaded"), duration=kw.pop("duration", "600"),
                    file_path=f"/nonexistent/{fid}-{title}.mp3", **kw)
        db.add(e); db.commit(); return e.id
    finally:
        db.close()


def _ids(resp):
    return [e["id"] for e in resp]


def test_catch_up_exists_and_is_protected(client):
    pls = client.get("/api/playlists").json()
    sys = [p for p in pls if p["is_system"]]
    assert len(sys) == 1 and sys[0]["name"] == "Get Caught Up" and sys[0]["type"] == "smart"
    assert sys[0]["rules"]["published"]["within_days"] == 14
    assert client.delete(f"/api/playlists/{sys[0]['id']}").status_code == 400
    r = client.put(f"/api/playlists/{sys[0]['id']}", json={"name": "Renamed", "rules": {"played": "unplayed", "published": {"within_days": 7}}})
    assert r.status_code == 200 and r.json()["name"] == "Get Caught Up" and r.json()["rules"]["published"]["within_days"] == 7


def test_create_validation(client):
    bad = [
        {"name": "x", "type": "feed"},
        {"name": "x", "type": "smart"},                                     # smart needs rules
        {"name": "x", "type": "custom", "rules": {"played": "any"}},         # manual cannot have rules
        {"name": "x", "type": "smart", "rules": {"played": "sometimes"}},
        {"name": "x", "type": "smart", "rules": {"sort": "random"}},
        {"name": "x", "type": "smart", "rules": {"limit": 0}},
        {"name": "x", "type": "smart", "rules": {"bogus": 1}},
        {"name": "x", "type": "smart", "rules": {"published": {"after": "yesterday"}}},
    ]
    for body in bad:
        assert client.post("/api/playlists", json=body).status_code == 422, body
    ok = client.post("/api/playlists", json={"name": "Short ones", "type": "smart",
                                             "rules": {"duration": {"max_minutes": 20}, "sort": "shortest"}})
    assert ok.status_code == 201 and ok.json()["type"] == "smart" and ok.json()["rules"]["sort"] == "shortest"
    manual = client.post("/api/playlists", json={"name": "Mine"})
    assert manual.status_code == 201 and manual.json()["type"] == "custom" and manual.json()["rules"] is None
    # The kind is fixed: rules on a manual playlist are refused.
    assert client.put(f"/api/playlists/{manual.json()['id']}", json={"rules": {"played": "any"}}).status_code == 400
    # Hand-picking into a smart playlist is refused.
    assert client.post(f"/api/playlists/{ok.json()['id']}/episodes", json={"episode_id": 1}).status_code == 400


def test_rules_select_and_order(client):
    a, b = _feed(client, "A"), _feed(client, "B")
    old = _episode(a, "old", 30, duration="1800")
    mid = _episode(b, "mid", 10, duration="600")
    new = _episode(a, "new", 2, duration="3600")
    played = _episode(a, "played", 3, played=True)
    prog = _episode(b, "prog", 5, play_position_seconds=100)
    _episode(a, "pending", 1, status="pending")
    _episode(b, "hidden", 1, hidden=True)

    def q(rules):
        r = client.post("/api/playlists", json={"name": "t", "type": "smart", "rules": rules})
        assert r.status_code == 201, r.text
        return _ids(client.get(f"/api/playlists/{r.json()['id']}/episodes").json())

    assert q({}) == [old, mid, prog, new]                                   # unplayed, oldest first, downloaded only
    assert q({"published": {"within_days": 14}}) == [mid, prog, new]
    assert q({"played": "played"}) == [played]
    assert q({"played": "in_progress"}) == [prog]
    assert q({"played": "any", "feeds": {"mode": "include", "ids": [b]}}) == [mid, prog]
    assert q({"feeds": {"mode": "exclude", "ids": [b]}}) == [old, new]
    assert q({"duration": {"min_minutes": 20}}) == [old, new]
    assert q({"duration": {"max_minutes": 15}, "sort": "newest"}) == [prog, mid]
    assert q({"sort": "longest", "limit": 2}) == [new, old]
    assert q({"title_contains": "NE"}) == [new]
    assert q({"published": {"after": (datetime.utcnow() - timedelta(days=6)).strftime("%Y-%m-%d")}}) == [prog, new]
    assert q({"published": {"before": (datetime.utcnow() - timedelta(days=6)).strftime("%Y-%m-%d")}}) == [old, mid]


def test_preview_and_counts(client):
    a = _feed(client, "A")
    _episode(a, "one", 1, duration="600"); _episode(a, "two", 2, duration="1200")
    r = client.post("/api/playlists/preview", json={"played": "unplayed"})
    assert r.status_code == 200 and r.json()["count"] == 2 and r.json()["total_seconds"] == 1800
    assert [s["title"] for s in r.json()["sample"]] == ["two", "one"]
    pl = client.post("/api/playlists", json={"name": "s", "type": "smart", "rules": {}}).json()
    assert pl["episode_count"] == 2 and pl["total_seconds"] == 1800


def test_smart_playlist_plays_as_a_queue(client):
    a = _feed(client, "A")
    e1, e2, e3 = _episode(a, "e1", 9), _episode(a, "e2", 6), _episode(a, "e3", 3)
    pl = client.post("/api/playlists", json={"name": "s", "type": "smart", "rules": {"published": {"within_days": 14}}}).json()
    st = client.post("/api/player/play", json={"context_type": "playlist", "context_id": pl["id"]}).json()
    assert st["current_episode_id"] == e1 and _ids(st["queue"]) == [e1, e2, e3]
    client.post(f"/api/episodes/{e2}/progress", json={"position_seconds": 60})
    assert client.post("/api/player/play", json={"context_type": "playlist", "context_id": pl["id"]}).json()["current_episode_id"] == e2
    client.post(f"/api/episodes/{e2}/played", json={"played": True})
    assert client.post("/api/player/next").json()["current_episode_id"] == e3
    client.post(f"/api/episodes/{e3}/played", json={"played": True})
    st = client.post("/api/player/next").json()
    assert st["current_episode_id"] is None and st["context_type"] == "playlist"
    assert client.post("/api/player/play", json={"context_type": "playlist", "context_id": pl["id"], "episode_id": e1}).json()["current_episode_id"] == e1


def test_feed_playlists_migrate_to_smart(client):
    a = _feed(client, "A")
    db = _session()
    try:
        db.add(Playlist(name="Old feed list", type="feed", feed_id=a, filter="unplayed")); db.commit()
    finally:
        db.close()
    import app.database as adb
    adb._migrate_db()
    pls = {p["name"]: p for p in client.get("/api/playlists").json()}
    pl = pls["Old feed list"]
    assert pl["type"] == "smart" and pl["rules"]["feeds"] == {"mode": "include", "ids": [a]} and pl["rules"]["played"] == "unplayed"


def test_description_clears_and_title_match_is_literal(client):
    pl = client.post("/api/playlists", json={"name": "d", "description": "hello"}).json()
    assert client.put(f"/api/playlists/{pl['id']}", json={"description": ""}).json()["description"] is None
    a = _feed(client, "A")
    _episode(a, "100% true story", 1); _episode(a, "100 percent", 2)
    r = client.post("/api/playlists/preview", json={"title_contains": "100%"})
    assert r.status_code == 200 and r.json()["count"] == 1
