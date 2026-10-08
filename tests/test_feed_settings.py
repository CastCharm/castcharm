"""Feed settings over HTTP: the ID3 toggle must save, and the contract the
client has to respect (tag map values are strings) is spelled out by a 422."""
import os

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """The real FastAPI app on a throwaway database.

    The engine is created at import time from DATABASE_URL, so instead of
    reloading modules (which would split Base from the models) we swap the
    engine and session factory wherever they were imported."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    import app.database as adb
    import app.main
    import app.startup_scan

    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False})
    event.listen(engine, "connect", adb.set_sqlite_pragma)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(adb, "engine", engine)
    monkeypatch.setattr(adb, "SessionLocal", Session)
    monkeypatch.setattr(app.main, "SessionLocal", Session)
    monkeypatch.setattr(app.startup_scan, "SessionLocal", Session)

    from fastapi.testclient import TestClient
    with TestClient(app.main.app) as c:
        yield c
    engine.dispose()


def _make_feed(client):
    r = client.post("/api/feeds/manual", json={"title": "Tag Test"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_enable_id3_with_empty_mapping_saves(client):
    fid = _make_feed(client)
    r = client.put(f"/api/feeds/{fid}", json={"id3_enabled": True, "id3_field_mapping": {}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id3_enabled"] is True and body["id3_field_mapping"] == {}


def test_enable_id3_with_mapping_saves(client):
    fid = _make_feed(client)
    mapping = {"TIT2": "episode.title", "TALB": "feed.title"}
    r = client.put(f"/api/feeds/{fid}", json={"id3_enabled": True, "id3_field_mapping": mapping})
    assert r.status_code == 200, r.text
    assert r.json()["id3_field_mapping"] == mapping
    assert client.get(f"/api/feeds/{fid}").json()["id3_field_mapping"] == mapping


def test_boolean_in_mapping_is_rejected_with_a_named_field(client):
    """The payload the old UI sent when the toggle was on."""
    fid = _make_feed(client)
    r = client.put(f"/api/feeds/{fid}", json={"id3_enabled": True, "id3_field_mapping": {"enabled": True}})
    assert r.status_code == 422
    loc = r.json()["detail"][0]["loc"]
    assert "id3_field_mapping" in loc and "enabled" in loc
