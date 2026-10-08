"""Shared fixtures: an isolated SQLite database and tiny tagged MP3 files."""
import os
import sys
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base  # noqa: E402
from app import models  # noqa: E402,F401
from app.models import Episode, Feed, GlobalSettings  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    s = Session()
    gs = GlobalSettings()
    gs.download_path = str(tmp_path / "downloads")
    s.add(gs)
    s.commit()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


@pytest.fixture()
def feed(db):
    f = Feed(title="The Long Show", url="manual:test", filename_date_prefix=True,
             filename_episode_number=True, organize_by_year=True)
    db.add(f)
    db.commit()
    return f


def add_episode(db, feed, *, title, number=None, date=None, guid=None, duration=None, seq=None):
    ep = Episode(feed_id=feed.id, title=title, guid=guid or f"guid-{title}",
                 episode_number=number, published_at=date, duration=duration, seq_number=seq)
    db.add(ep)
    db.commit()
    return ep


# One MPEG-1 Layer III frame: 128 kbps, 44.1 kHz, no padding → 417 bytes.
_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413


def make_mp3(path, *, title=None, tracknumber=None, date=None, artist=None, frames=40):
    """Write a small but real MP3 (mutagen can read its length) with easy ID3 tags."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Distinct audio payload per file so content hashes differ, as they would
    # for real recordings.
    import random
    rnd = random.Random(path)
    with open(path, "wb") as fh:
        for _ in range(frames):
            fh.write(_FRAME[:4] + bytes(rnd.getrandbits(8) for _ in range(413)))
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3NoHeaderError
    try:
        tags = EasyID3(path)
    except ID3NoHeaderError:
        tags = EasyID3()
    if title:
        tags["title"] = title
    if tracknumber is not None:
        tags["tracknumber"] = str(tracknumber)
    if date:
        tags["date"] = date
    if artist:
        tags["artist"] = artist
    tags.save(path)
    return path


D = datetime
