"""Import pipeline: parsing, trust, scoring, assignment, numbering, no-clobber."""
import os
from datetime import datetime, timedelta

import pytest

from app import importer as I
from app.models import Episode
from app.routers.episodes import recalc_seq_numbers, _lnds_indices
from tests.conftest import add_episode, make_mp3


# ── parsing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("2009-04-17", datetime(2009, 4, 17)),
    ("20090417", datetime(2009, 4, 17)),
    ("2009.04.17", datetime(2009, 4, 17)),
    ("04-17-2009", datetime(2009, 4, 17)),
    ("17.04.2009", datetime(2009, 4, 17)),
    ("March 5, 2009", datetime(2009, 3, 5)),
    ("5 Mar 2009", datetime(2009, 3, 5)),
    ("Fri, 17 Apr 2009 10:00:00 +0000", datetime(2009, 4, 17, 10, 0)),
    ("2009", datetime(2009, 1, 1, 12)),
    ("2009-04", datetime(2009, 4, 15, 12)),
    ("not a date", None),
])
def test_parse_date_shapes(raw, expected):
    assert I._parse_date(raw) == expected


@pytest.mark.parametrize("stem, number, date, title", [
    ("123 - Dogs", 123, None, "Dogs"),
    ("2009 - Old Show", None, datetime(2009, 1, 1, 12), "Old Show"),      # a year, not ep 2009
    ("2009-04-17 - 123 - Dogs", 123, datetime(2009, 4, 17), "Dogs"),
    ("20090417 Dogs", None, datetime(2009, 4, 17), "Dogs"),
    ("Episode 7 - Cats", 7, None, "Cats"),
    ("S02E05 - Birds", 5, None, "Birds"),
    ("Dogs - 2009-04-17", None, datetime(2009, 4, 17), "Dogs"),
    ("Dogs - Ep 12", 12, None, "Dogs"),
])
def test_parse_filename(stem, number, date, title):
    r = I._parse_filename(stem)
    assert r.get("episode_number") == number
    assert r.get("date") == date
    assert r.get("title") == title


def test_natural_order():
    files = ["ep 10.mp3", "ep 9.mp3", "ep 100.mp3", "ep 1.mp3"]
    assert sorted(files, key=I._natural_key) == ["ep 1.mp3", "ep 9.mp3", "ep 10.mp3", "ep 100.mp3"]


def test_dur_seconds_plain_and_fractional():
    assert I._dur_seconds("3600") == 3600
    assert I._dur_seconds("1:00:00.5") == 3600
    assert I._dur_seconds("12:34") == 754
    assert I._dur_seconds(None) is None


def test_detect_pattern_reports_share():
    stems = [f"{n:03d} - Title {n}" for n in range(1, 10)] + ["oddball"]
    top = I._detect_filename_pattern(stems)
    assert top[0]["label"] == "### - Title"
    assert top[0]["format"] == "%EpisodeNum% - %Title%"
    assert top[0]["share"] == 0.9


# ── similarity ───────────────────────────────────────────────────────────────

def test_similarity_ignores_noise():
    a = "The Long Show - Episode 12: Dogs and Cats [128k]"
    b = "Dogs & Cats"
    assert I._similarity(a, b, feed_title="The Long Show") >= 0.9
    assert I._similarity("Dogs and Cats part 1", "Dogs and Cats part 2") < 1.0


def test_clean_title_keeps_pure_number():
    assert I._clean_title("42") == "42"


# ── number trust ─────────────────────────────────────────────────────────────

def _fm(stem, **kw):
    f = I.FileMeta(path=stem + ".mp3", filename=stem + ".mp3", stem=stem)
    f.fn_info = I._parse_filename(stem)
    f.tags = kw.get("tags", {})
    f.sidecar = kw.get("sidecar", {})
    return f


def test_id3_track_ones_are_not_trusted():
    raws = [_fm(f"{n:03d} - T{n}", tags={"tracknumber": "1/1"}) for n in range(1, 6)]
    scheme = I._infer_number_scheme(raws)
    assert scheme["trusted"]["filename"] is True
    assert scheme["trusted"]["id3"] is False
    m = I._finalize_meta(raws[2], scheme, "X")
    assert m.episode_number == 3 and m.number_trusted and m.sources["episode_number"] == "filename"


def test_single_file_prefers_filename_over_track():
    raw = _fm("045 - Solo", tags={"tracknumber": "1"})
    scheme = I._infer_number_scheme([raw])
    m = I._finalize_meta(raw, scheme, None)
    assert m.episode_number == 45 and m.number_trusted


def test_unnumbered_filenames_fall_back_to_untrusted_track():
    raw = _fm("Solo talk", tags={"tracknumber": "7"})
    m = I._finalize_meta(raw, I._infer_number_scheme([raw]), None)
    assert m.episode_number == 7 and not m.number_trusted


# ── scoring ──────────────────────────────────────────────────────────────────

def _cand(title, number=None, date=None, dur=None, seq=None):
    ep = Episode(title=title, episode_number=number, published_at=date, duration=dur, seq_number=seq, guid="g")
    return I._make_cands([ep], "The Long Show")[0]


def test_date_off_by_one_still_scores():
    f = I._finalize_meta(_fm("2019-03-08 - Dogs"), {"trusted": {}}, None)
    exact = I._score_pair(f, _cand("Dogs", date=datetime(2019, 3, 8)))
    off1 = I._score_pair(f, _cand("Dogs", date=datetime(2019, 3, 9)))
    off5 = I._score_pair(f, _cand("Dogs", date=datetime(2019, 3, 13)))
    assert exact[0] > off1[0] > off5[0]
    assert "date ±1d" in off1[2]
    assert off1[0] >= 0.60


def test_trusted_number_disagreement_penalises():
    f = I._finalize_meta(_fm("412 - Dogs"), {"trusted": {"filename": True}}, None)
    same = I._score_pair(f, _cand("Dogs", number=412))
    other = I._score_pair(f, _cand("Dogs", number=413))
    assert same[0] - other[0] > 0.25


def test_global_assignment_prefers_better_later_file():
    files = [I._finalize_meta(_fm("Dogs"), {"trusted": {}}, None),
             I._finalize_meta(_fm("2019-03-08 - Dogs"), {"trusted": {}}, None)]
    cands = [_cand("Dogs", date=datetime(2019, 3, 8))]
    assignment, scored = I._assign_globally(files, cands, {})
    # The second (dated) file wins the only episode even though the first
    # file came first alphabetically.
    assert 1 in assignment and 0 not in assignment


# ── numbering ────────────────────────────────────────────────────────────────

def test_lnds_marks_outlier():
    vals = [1, 2, 3, 1, 4, 5]        # the second "1" is a stray track number
    keep = _lnds_indices(vals)
    assert keep == {0, 1, 2, 4, 5}


def test_recalc_ignores_stray_and_year_like_numbers(db, feed):
    base = datetime(2020, 1, 1)
    eps = []
    for i, n in enumerate([100, 101, 1, 103, 2020, 105]):
        eps.append(add_episode(db, feed, title=f"E{i}", number=n, date=base + timedelta(days=i)))
    recalc_seq_numbers(feed.id, db)
    db.commit()
    assert [e.seq_number for e in eps] == [100, 101, 102, 103, 104, 105]


def test_recalc_no_duplicate_with_low_lock(db, feed):
    base = datetime(2020, 1, 1)
    a = add_episode(db, feed, title="A", date=base)
    b = add_episode(db, feed, title="B", date=base + timedelta(days=1))
    c = add_episode(db, feed, title="C", date=base + timedelta(days=2))
    c.seq_number, c.seq_number_locked = 1, True
    db.commit()
    recalc_seq_numbers(feed.id, db)
    db.commit()
    nums = [a.seq_number, b.seq_number, c.seq_number]
    assert len(set(nums)) == 3 and c.seq_number == 1


def test_year_only_dates_spread_through_year(db, feed):
    for i in range(3):
        ep = add_episode(db, feed, title=f"Y{i}", number=i + 1, date=datetime(2009, 1, 1, 12))
        ep.date_is_approximate = True
    db.commit()
    assert I._interpolate_missing_dates(feed.id, db) == 3
    dates = [e.published_at for e in db.query(Episode).order_by(Episode.episode_number).all()]
    assert dates == sorted(dates) and all(d.year == 2009 for d in dates)
    assert dates[0].month > 1 and dates[-1].month < 12


# ── end to end: the user's archive ───────────────────────────────────────────

@pytest.fixture()
def archive(tmp_path, db, feed):
    """Feed knows #612–#626 (2019-03-01 onward, weekly).  Folder holds
    001–020 from 2005 (predating the feed), 617–626 overlapping the feed with
    dates one day late and slightly different titles, a stray copy of 610 in a
    year folder tagged track 1, and two copies of the same new episode."""
    d = tmp_path / "archive"
    feed_start = datetime(2019, 3, 1)
    for k, n in enumerate(range(612, 627)):
        add_episode(db, feed, title=f"Show {n}: Topic {n}", number=n,
                    date=feed_start + timedelta(days=7 * k), duration="1800")
    for n in range(1, 21):
        make_mp3(str(d / f"{n:03d} - Topic {n}.mp3"), title=f"Topic {n}", date="2005", tracknumber=1)
    for k, n in enumerate(range(617, 627)):
        dt = feed_start + timedelta(days=7 * (n - 612) + 1)   # one day late
        make_mp3(str(d / f"{dt:%Y-%m-%d} - {n} - Topic {n} (final).mp3"), title=f"Topic {n} final", tracknumber=1)
    make_mp3(str(d / "2009" / "610 - Topic 610.mp3"), title="Topic 610", tracknumber=1)
    make_mp3(str(d / "700 - Brand New.mp3"), title="Brand New", frames=40)
    make_mp3(str(d / "copies" / "700 - Brand New.mp3"), title="Brand New", frames=60)
    return d


def test_preview_matches_overlap_and_flags(db, feed, archive):
    pv = I.preview_import_directory(feed.id, str(archive), db)
    by_name = {f["filename"]: f for f in pv["files"]}

    # Overlapping files match their episode despite the +1 day and "(final)".
    for n in range(617, 627):
        row = next(f for name, f in by_name.items() if f" - {n} - " in name)
        assert row["match"] and row["match"]["episode_number"] == n, row
        assert row["tier"] in ("strong", "certain"), row["tier"]
        assert any(r.startswith("date") for r in row["match"]["reasons"])

    # Old files create new episodes, their numbers are trusted (from filenames),
    # and nothing weak was auto-linked.
    for n in range(1, 21):
        row = by_name[f"{n:03d} - Topic {n}.mp3"]
        assert row["match"] is None and row["episode_number"] == n and row["number_trusted"]
        assert row["metadata_sources"]["episode_number"] == "filename"
    assert not any(f["match"] and f["tier"] == "weak" for f in pv["files"])

    # Duplicate copies: the larger one is kept.
    dups = [f for f in pv["files"] if f["duplicate_of"]]
    assert len(dups) == 1 and dups[0]["filename"] == "700 - Brand New.mp3" and "copies" not in dups[0]["path"]

    assert pv["alignment"]["kind"] == "overlaps"
    assert pv["alignment"]["overlap_matched"] == 10
    assert pv["detected_patterns"][0]["label"] == "### - Title"
    assert pv["number_scheme"]["trusted"]["id3"] is False


def test_commit_never_clobbers_feed_metadata(db, feed, archive):
    pv = I.preview_import_directory(feed.id, str(archive), db)
    before = {e.id: (e.title, e.published_at, e.episode_number)
              for e in db.query(Episode).all()}
    items = [{"path": f["path"], "skip": bool(f["duplicate_of"]),
              "episode_id": f["match"]["episode_id"] if f["match"] else None,
              "overrides": {}} for f in pv["files"]]
    summary = I.import_staged(feed.id, items, db)
    assert summary["errors"] == 0, summary["file_errors"]
    assert summary["matched"] == 10 and summary["created"] == 22

    linked = {i["episode_id"] for i in items if i["episode_id"]}
    for e in db.query(Episode).all():
        if e.id in before:
            assert (e.title, e.published_at, e.episode_number) == before[e.id]
            assert not e.seq_number_locked
            if e.id in linked:
                assert e.file_path and os.path.exists(e.file_path)
                assert e.imported and e.status == "downloaded"

    # Numbering: the 2005 files are #1–#20, then 610, then the feed keeps 612+.
    eps = db.query(Episode).filter(Episode.hidden.is_(False)).order_by(Episode.seq_number).all()
    seqs = [e.seq_number for e in eps]
    assert seqs[:20] == list(range(1, 21))
    assert 610 in seqs and 612 in seqs and 626 in seqs and 700 in seqs
    assert len(seqs) == len(set(seqs))
    # Year-only 2005 dates were spread through 2005 in number order.
    old = [e for e in eps if e.seq_number <= 20]
    assert all(e.published_at.year == 2005 and e.date_is_approximate for e in old)
    assert [e.published_at for e in old] == sorted(e.published_at for e in old)

    # Re-running the preview on the same folder recognises every imported
    # file — including the ten linked to RSS episodes, whose copies were
    # renamed — and offers only the skipped duplicate copy again.
    pv2 = I.preview_import_directory(feed.id, str(archive), db)
    assert pv2["registered"] == 32 and pv2["matched"] == 0 and pv2["unmatched"] == 1


def test_overrides_pin_number_and_set_title(db, feed, archive):
    pv = I.preview_import_directory(feed.id, str(archive), db)
    row = next(f for f in pv["files"] if f["filename"] == "001 - Topic 1.mp3")
    items = [{"path": row["path"], "episode_id": None,
              "overrides": {"title": "Pilot", "episode_number": 5, "date": "2005-06-01"}}]
    I.import_staged(feed.id, items, db)
    ep = db.query(Episode).filter(Episode.title == "Pilot").one()
    assert ep.seq_number == 5 and ep.seq_number_locked and ep.episode_number == 5
    assert ep.published_at == datetime(2005, 6, 1) and not ep.date_is_approximate
