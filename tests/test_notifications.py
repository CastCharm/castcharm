"""Notifications: message shape, senders (no network), settings guards, collector."""
import json
import threading

import httpx
import pytest

from app import notifications as N
from app.auth import hash_password
from app.models import Episode, Feed, GlobalSettings


# ── helpers ──────────────────────────────────────────────────────────────────

class _Ep:
    def __init__(self, id, feed_id, title, feed_title, published_at=None):
        self.id, self.feed_id, self.title, self.published_at = id, feed_id, title, published_at
        self.feed = type("F", (), {"title": feed_title})()


def _capture(status=200, headers=None):
    """A MockTransport that records the request and answers with *status*."""
    seen = {}
    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(status, headers=headers or {}, text="ignored body")
    return seen, httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(N, "_transport", httpx.MockTransport(lambda r: httpx.Response(599)))
    monkeypatch.setattr(N, "_pending", set())
    yield


def _enable_login(client, password="correct horse"):
    import app.database as adb
    db = adb.SessionLocal()
    try:
        gs = db.query(GlobalSettings).first()
        gs.auth_enabled, gs.auth_username, gs.auth_password_hash = True, "admin", hash_password(password)
        db.commit()
    finally:
        db.close()
    r = client.post("/api/auth/login", json={"username": "admin", "password": password})
    assert r.status_code == 200, r.text


# ── message ──────────────────────────────────────────────────────────────────

def test_message_single_feed_links_to_feed():
    m = N.build_new_episodes_message([_Ep(1, 7, "Ep A", "Show")], "https://pods.example.com/")
    assert m.title == "1 new episode" and m.count == 1
    assert m.text == "Show — Ep A"
    assert m.link == "https://pods.example.com/feeds/7"


def test_message_many_feeds_caps_lines_and_links_to_root():
    eps = [_Ep(i, 1 + i % 3, f"Ep {i}", f"Show {i % 3}") for i in range(14)]
    m = N.build_new_episodes_message(eps, "https://pods.example.com")
    assert m.title == "14 new episodes"
    lines = m.text.split("\n")
    assert len(lines) == 11 and lines[-1] == "…and 4 more"
    assert m.link == "https://pods.example.com"
    assert sorted(f["title"] for f in m.feeds) == ["Show 0", "Show 1", "Show 2"]


def test_message_without_public_url_has_no_link():
    assert N.build_new_episodes_message([_Ep(1, 7, "x", "y")], None).link is None


# ── senders ──────────────────────────────────────────────────────────────────

def test_ntfy_headers_bearer_and_body(monkeypatch):
    seen, transport = _capture()
    monkeypatch.setattr(N, "_transport", transport)
    msg = N.Message(title="2 new episodes", text="Show — Ep", link="https://p.example/feeds/1", count=2)
    res = N.send("ntfy", "https://ntfy.sh/topic", "tok123", msg)
    req = seen["request"]
    assert res.ok and res.status == 200
    assert req.method == "POST" and str(req.url) == "https://ntfy.sh/topic"
    assert req.headers["Title"] == "2 new episodes"
    assert req.headers["Click"] == "https://p.example/feeds/1"
    assert req.headers["Authorization"] == "Bearer tok123"
    assert req.content == b"Show \xe2\x80\x94 Ep"


def test_ntfy_userinfo_becomes_basic_auth(monkeypatch):
    seen, transport = _capture()
    monkeypatch.setattr(N, "_transport", transport)
    N.send("ntfy", "https://me:pw@ntfy.example/topic", None, N.Message("t", "b"))
    req = seen["request"]
    assert str(req.url) == "https://ntfy.example/topic"
    assert req.headers["Authorization"].startswith("Basic ")


def test_apprise_and_webhook_shapes(monkeypatch):
    msg = N.Message(title="1 new episode", text="Show — Ep", link="https://p.example", count=1,
                    feeds=[{"id": 1, "title": "Show", "episodes": [{"id": 9, "title": "Ep", "published_at": None}]}])
    shapes = {}
    for kind in ("apprise", "webhook"):
        seen, transport = _capture()
        monkeypatch.setattr(N, "_transport", transport)
        assert N.send(kind, "https://hook.example/x", None, msg).ok
        shapes[kind] = json.loads(seen["request"].content)
        assert seen["request"].headers["Content-Type"].startswith("application/json")
    assert shapes["apprise"] == {"title": "1 new episode", "body": "Show — Ep\nhttps://p.example", "type": "info", "format": "text"}
    w = shapes["webhook"]
    assert w["event"] == "new_episodes" and w["count"] == 1 and w["link"] == "https://p.example"
    assert w["feeds"][0]["episodes"][0]["id"] == 9 and "sent_at" in w


def test_failure_statuses_and_no_redirect(monkeypatch):
    seen, transport = _capture(500)
    monkeypatch.setattr(N, "_transport", transport)
    r = N.send("ntfy", "https://ntfy.sh/t", None, N.Message("t", "b"))
    assert not r.ok and r.status == 500 and r.detail == "HTTP 500"

    seen, transport = _capture(302, {"Location": "http://10.0.0.1/internal"})
    monkeypatch.setattr(N, "_transport", transport)
    r = N.send("ntfy", "https://ntfy.sh/t", None, N.Message("t", "b"))
    assert not r.ok and r.status == 302 and "redirect not followed" in r.detail

    def boom(request):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(N, "_transport", httpx.MockTransport(boom))
    r = N.send("ntfy", "https://ntfy.sh/t", None, N.Message("t", "b"))
    assert not r.ok and r.status is None and "Could not reach ntfy.sh" in r.detail


def test_send_rejects_bad_kind_and_scheme():
    assert not N.send("carrier-pigeon", "https://x", None, N.Message("t", "b")).ok
    assert not N.send("discord", "https://x", None, N.Message("t", "b")).ok   # deliberately unsupported
    assert not N.send("ntfy", "ftp://x/topic", None, N.Message("t", "b")).ok
    assert not N.send("ntfy", None, None, N.Message("t", "b")).ok


# ── settings guards ──────────────────────────────────────────────────────────

def test_settings_never_expose_credentials_and_url_is_write_only(client):
    _enable_login(client)
    r = client.put("/api/settings", json={"notify_kind": "ntfy", "notify_url": "https://ntfy.sh/secret-topic",
                                          "notify_token": "tok", "notify_enabled": True, "public_url": "https://p.example"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "notify_url" not in body and "notify_token" not in body
    assert body["notify_url_set"] is True and body["notify_url_host"] == "ntfy.sh" and body["notify_token_set"] is True
    assert client.get("/api/settings").json()["notify_url_set"] is True
    # Omitting the field keeps it; "" clears it.
    assert client.put("/api/settings", json={"public_url": "https://p.example"}).json()["notify_url_set"] is True
    body = client.put("/api/settings", json={"notify_url": "", "notify_token": ""}).json()
    assert body["notify_url_set"] is False and body["notify_token_set"] is False


def test_settings_refuse_destination_without_login(client):
    r = client.put("/api/settings", json={"notify_url": "https://ntfy.sh/t"})
    assert r.status_code == 403 and "login" in r.json()["detail"].lower()
    assert client.put("/api/settings", json={"notify_enabled": True}).status_code == 403
    assert client.post("/api/settings/notifications/test").status_code == 403


def test_settings_validate_urls_and_kind(client):
    _enable_login(client)
    assert client.put("/api/settings", json={"notify_url": "ftp://x/y"}).status_code == 422
    assert client.put("/api/settings", json={"public_url": "not a url"}).status_code == 422
    assert client.put("/api/settings", json={"notify_kind": "pigeon"}).status_code == 422


def test_test_endpoint_sends_and_rate_limits(client, monkeypatch):
    _enable_login(client)
    client.put("/api/settings", json={"notify_kind": "ntfy", "notify_url": "https://ntfy.sh/t", "notify_enabled": True})
    seen, transport = _capture()
    monkeypatch.setattr(N, "_transport", transport)
    import app.routers.settings as sr
    monkeypatch.setattr(sr, "_last_notify_test_at", 0.0)
    r = client.post("/api/settings/notifications/test")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert seen["request"].headers["Title"] == "CastCharm notifications are working"
    assert client.post("/api/settings/notifications/test").status_code == 429


# ── collector ────────────────────────────────────────────────────────────────

def test_collector_batches_into_one_message(client, monkeypatch):
    _enable_login(client)
    client.put("/api/settings", json={"notify_kind": "webhook", "notify_url": "https://hook.example/x", "notify_enabled": True})
    import app.database as adb
    db = adb.SessionLocal()
    try:
        f = Feed(title="Show", url="manual:n", initial_sync_complete=True)
        db.add(f); db.commit()
        ids = []
        # Mixed statuses on purpose: auto-download moves new episodes to
        # "queued"/"downloaded" before the batch flushes, and they must still count.
        for i, status in enumerate(("pending", "queued", "downloaded")):
            ep = Episode(feed_id=f.id, title=f"Ep {i}", guid=f"n{i}", status=status)
            db.add(ep); db.flush(); ids.append(ep.id)
        db.commit()
    finally:
        db.close()

    sent = []
    def handler(request):
        sent.append(json.loads(request.content)); return httpx.Response(200)
    monkeypatch.setattr(N, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(N, "QUIET_SECONDS", 0.2)

    N.queue_new_episodes(ids[:2])
    N.queue_new_episodes(ids[2:])          # within the quiet window → same batch
    assert sent == []
    import time
    time.sleep(0.6)
    assert len(sent) == 1 and sent[0]["count"] == 3 and sent[0]["title"] == "3 new episodes"

    N.queue_new_episodes(ids[:1])
    assert N.flush_now().ok and len(sent) == 2 and sent[1]["count"] == 1

    client.put("/api/settings", json={"notify_enabled": False})
    N.queue_new_episodes(ids)
    assert N.flush_now() is None and len(sent) == 2
