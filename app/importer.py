"""Import existing audio files into a podcast feed."""
import hashlib
import logging
import os
import re
import shutil
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

from app.models import Episode, Feed
from app.utils import get_group_feed_ids
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wav", ".mp4", ".opus", ".wma"}

# In-memory job status: feed_id -> dict
_import_jobs: dict[int, dict] = {}

# In-memory scan progress: feed_id -> dict
_preview_jobs: dict[int, dict] = {}


def get_preview_status(feed_id: int) -> Optional[dict]:
    return _preview_jobs.get(feed_id)


def get_import_status(feed_id: int) -> Optional[dict]:
    return _import_jobs.get(feed_id)


def get_active_import_count() -> int:
    """Return the number of import jobs currently running."""
    return sum(1 for j in _import_jobs.values() if j.get("status") == "running")


# ---------------------------------------------------------------------------
# Metadata readers
# ---------------------------------------------------------------------------

def _read_xml_sidecar(audio_path: str) -> dict:
    """Read our own .xml sidecar adjacent to the audio file."""
    xml_path = audio_path + ".xml"
    if not os.path.exists(xml_path):
        return {}
    try:
        root = ET.parse(xml_path).getroot()

        def _t(tag):
            el = root.find(tag)
            return el.text.strip() if el is not None and el.text else None

        return {k: v for k, v in {
            "guid":           _t("guid"),
            "title":          _t("title"),
            "enclosure_url":  _t("enclosureUrl"),
            "enclosure_type": _t("enclosureType"),
            "published":      _t("published"),
            "duration":       _t("duration"),
            "episode_number": _t("episodeNumber"),
            "season_number":  _t("seasonNumber"),
            "author":         _t("author"),
            "image_url":      _t("imageUrl"),
            "description":    _t("description"),
            "link":           _t("link"),
        }.items() if v is not None}
    except Exception as e:
        log.warning("XML sidecar parse error %s: %s", xml_path, e)
        return {}


def _read_id3_tags(audio_path: str) -> dict:
    """Read mutagen tags from any audio file.

    Uses easy mode for standard fields, then a raw pass to extract the
    COMM (description) frame which easy mode doesn't expose.
    """
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(audio_path, easy=True)
        if audio is None:
            return {}
        tags = audio.tags or {}

        def _first(key):
            v = tags.get(key)
            return str(v[0]).strip() if v else None

        result = {
            "title":       _first("title"),
            "artist":      _first("artist"),
            "album":       _first("album"),
            "tracknumber": _first("tracknumber"),
            "date":        _first("date"),
            "comment":     _first("comment"),
        }
        if hasattr(audio, "info") and hasattr(audio.info, "length"):
            t = int(audio.info.length)
            h, rem = divmod(t, 3600)
            m, s = divmod(rem, 60)
            result["duration"] = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

        # Second pass: extract COMM description frame (easy mode doesn't expose it).
        # COMM frames have a `desc` attribute identifying them — skip iTunes
        # metadata frames (iTunNORM, iTunSMPB, etc.) and prefer the frame
        # with an empty desc (the "real" comment).
        _ITUNES_DESCS = {"iTunNORM", "iTunSMPB", "iTunPGAP", "iTunes_CDDB_IDs"}
        # Pattern that catches hex-dump strings like "000005AC 000005B0 ..."
        _HEX_DUMP_RE = re.compile(r"^[0-9A-Fa-f ]{20,}$")
        try:
            raw = MutagenFile(audio_path, easy=False)
            if raw and raw.tags:
                best_comm = None
                for key in raw.tags:
                    if not str(key).startswith("COMM"):
                        continue
                    frame = raw.tags[key]
                    desc_attr = getattr(frame, "desc", "") or ""
                    if desc_attr in _ITUNES_DESCS:
                        continue
                    text = (str(frame.text[0]) if hasattr(frame, "text") and frame.text
                            else str(frame)).strip()
                    if not text or len(text) <= 10:
                        continue
                    if _HEX_DUMP_RE.match(text):
                        continue
                    # Prefer frames with empty desc (the default comment field)
                    if not desc_attr:
                        best_comm = text
                        break
                    if best_comm is None:
                        best_comm = text
                if best_comm:
                    result.setdefault("description", best_comm)
        except Exception:
            pass

        return {k: v for k, v in result.items() if v is not None}
    except Exception as e:
        log.warning("Tag read error %s: %s", audio_path, e)
        return {}


# ---------------------------------------------------------------------------
# User-defined filename format parsing
# ---------------------------------------------------------------------------

# Tokens the user can place in a format string and what they capture.
_FORMAT_TOKENS: dict[str, tuple[str, str]] = {
    # token             → (regex,                    named group)
    "%EpisodeNum%":     (r"(?P<epnum>\d+)",          "epnum"),
    "%Title%":          (r"(?P<title>.+?)",          "title"),
    "%Date%":           (r"(?P<date>\d{4}-\d{2}-\d{2})", "date"),
    "%YYYY%":           (r"(?P<_year>\d{4})",        "_year"),
    "%MM%":             (r"(?P<_month>\d{2})",       "_month"),
    "%DD%":             (r"(?P<_day>\d{2})",         "_day"),
    "%Season%":         (r"(?P<season>\d+)",         "season"),
    "%Ignore%":         (r"(?:.+?)",                 None),
}

# Precompile a regex that finds any token in the format string
_TOKEN_RE = re.compile("|".join(re.escape(t) for t in _FORMAT_TOKENS))


def _compile_filename_format(fmt: str) -> Optional[re.Pattern]:
    """Convert a user format like ``%EpisodeNum% - %Title%`` into a compiled regex.

    Returns None if *fmt* is empty or contains no recognised tokens.
    """
    if not fmt or not fmt.strip():
        return None

    parts: list[str] = []
    last = 0
    has_token = False
    for m in _TOKEN_RE.finditer(fmt):
        # Literal text between tokens — escape and allow flexible whitespace around separators
        literal = fmt[last:m.start()]
        if literal:
            # Turn literal separators like " - " into flexible whitespace+dash patterns
            escaped = re.escape(literal)
            escaped = re.sub(r"(\\ )+", r"\\s+", escaped)          # spaces → \s+
            escaped = re.sub(r"\\-", r"[-–—]", escaped)             # hyphens → any dash
            parts.append(escaped)
        parts.append(_FORMAT_TOKENS[m.group()][0])
        has_token = True
        last = m.end()

    if not has_token:
        return None

    # Trailing literal
    trail = fmt[last:]
    if trail:
        escaped = re.escape(trail)
        escaped = re.sub(r"(\\ )+", r"\\s+", escaped)
        escaped = re.sub(r"\\-", r"[-–—]", escaped)
        parts.append(escaped)

    # Make %Title% at the end greedy (so it captures the rest of the stem)
    pattern_str = "^" + "".join(parts) + "$"
    pattern_str = pattern_str.replace("(?P<title>.+?)", "(?P<title>.+)")
    # But only make the LAST title group greedy — if it appears mid-pattern,
    # keep it non-greedy.  Simple approach: only the final occurrence.
    # (The replace above changed all; revert all but last.)
    count = pattern_str.count("(?P<title>.+)")
    if count > 1:
        pattern_str = pattern_str.replace("(?P<title>.+)", "(?P<title>.+?)", count - 1)

    try:
        return re.compile(pattern_str)
    except re.error:
        return None


def _parse_filename_with_format(stem: str, fmt_re: re.Pattern) -> dict:
    """Parse *stem* using a user-compiled format regex, assembling a date from
    individual %YYYY%/%MM%/%DD% tokens if present."""
    m = fmt_re.match(stem.strip())
    if not m:
        return {"title": stem.strip()}

    gd = {k: v for k, v in m.groupdict().items() if v is not None}
    result: dict = {}

    if "title" in gd:
        result["title"] = gd["title"].strip()
    if "date" in gd:
        dt = _parse_date(gd["date"])
        if dt:
            result["date"] = dt
    # Assemble date from individual components
    if "_year" in gd:
        try:
            y = int(gd["_year"])
            mo = int(gd.get("_month", "1"))
            d = int(gd.get("_day", "1"))
            result["date"] = datetime(y, mo, d)
        except (ValueError, TypeError):
            pass
    if "epnum" in gd and gd["epnum"].isdigit():
        result["episode_number"] = int(gd["epnum"])
    if "season" in gd and gd["season"].isdigit():
        result["season_number"] = int(gd["season"])
    return result


# ---------------------------------------------------------------------------
# Filename parsing (heuristic fallback)
# ---------------------------------------------------------------------------

_FN_PATTERNS = [
    # S01E05 - Podcast Name - Title
    re.compile(r"^[Ss](?P<season>\d{1,2})[Ee](?P<epnum>\d{1,3})\s*[-–—]\s*(?P<podcast>.+?)\s*[-–—]\s*(?P<title>.+)$"),
    # S01E05 - Title
    re.compile(r"^[Ss](?P<season>\d{1,2})[Ee](?P<epnum>\d{1,3})\s*[-–—]\s*(?P<title>.+)$"),
    # S01E05Title (no separator)
    re.compile(r"^[Ss](?P<season>\d{1,2})[Ee](?P<epnum>\d{1,3})(?P<title>.+)$"),
    # YYYY-MM-DD - ### - Title
    re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})\s*[-–—]\s*(?P<epnum>\d+)\s*[-–—]\s*(?P<title>.+)$"),
    # YYYY-MM-DD - Title
    re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})\s*[-–—]\s*(?P<title>.+)$"),
    # ### - Podcast Name - Title (number + two dashes)
    re.compile(r"^(?P<epnum>\d{1,4})\s*[-–—]\s*.+?\s*[-–—]\s*(?P<title>.+)$"),
    # ### - Title (1-4 leading digits)
    re.compile(r"^(?P<epnum>\d{1,4})\s*[-–—]\s*(?P<title>.+)$"),
    # "Episode N - Title" / "Ep N - Title"
    re.compile(r"^(?:episode|ep\.?)\s*(?P<epnum>\d+)\s*[-–—]\s*(?P<title>.+)$", re.IGNORECASE),
    # fallback: whole stem is the title
    re.compile(r"^(?P<title>.+)$"),
]


def _parse_date(s: str) -> Optional[datetime]:
    # len(fmt) != len(actual date string) because %Y is 2 chars but the year
    # is 4 digits, so we pair each format with the correct slice length.
    for fmt, n in [("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d", 10), ("%Y/%m/%d", 10)]:
        try:
            return datetime.strptime(s[:n], fmt)
        except (ValueError, TypeError):
            pass
    m = re.match(r"^(\d{4})$", s.strip())
    if m:
        return datetime(int(m.group(1)), 1, 1, 12, 0, 0)
    return None


def _is_year_only(s: str) -> bool:
    """Return True if the date string is just a 4-digit year (approximate date)."""
    return bool(re.match(r"^\d{4}$", str(s).strip()))


def _parse_filename(stem: str) -> dict:
    for pat in _FN_PATTERNS:
        m = pat.match(stem.strip())
        if not m:
            continue
        gd = {k: v for k, v in m.groupdict().items() if v is not None}
        result = {}
        if "title" in gd:
            result["title"] = gd["title"].strip()
        if "date" in gd:
            dt = _parse_date(gd["date"])
            if dt:
                result["date"] = dt
        if "epnum" in gd and gd["epnum"].isdigit():
            result["episode_number"] = int(gd["epnum"])
        if "season" in gd and gd["season"].isdigit():
            result["season_number"] = int(gd["season"])
        return result
    return {"title": stem.strip()}


# ---------------------------------------------------------------------------
# Folder analysis
# ---------------------------------------------------------------------------

def _parse_folder_context(audio_path: str, base_dir: str) -> dict:
    """Extract metadata hints from subfolder names between base_dir and file."""
    rel = os.path.relpath(os.path.dirname(audio_path), base_dir)
    if rel == ".":
        return {}
    result = {}
    for part in rel.split(os.sep):
        if re.match(r"^\d{4}$", part):
            result["folder_year"] = int(part)
        m = re.match(r"^(?:[Ss]eason\s*|[Ss])(\d{1,2})$", part)
        if m:
            result["folder_season"] = int(m.group(1))
    return result


def _detect_folder_type(directory: str, audio_files: list[str]) -> dict:
    """Classify folder structure and return stats for the UI."""
    xml_count = sum(1 for f in audio_files if os.path.exists(f + ".xml"))
    has_xml = xml_count > len(audio_files) * 0.5 if audio_files else False

    # Collect immediate subdirectory names
    subdirs = []
    try:
        for entry in os.scandir(directory):
            if entry.is_dir() and not entry.name.startswith("."):
                subdirs.append(entry.name)
    except OSError:
        pass

    year_folders = sorted([s for s in subdirs if re.match(r"^\d{4}$", s)])
    season_folders = sorted([s for s in subdirs if re.match(r"^(?:[Ss]eason\s*|[Ss])\d{1,2}$", s)])

    # Determine type
    if has_xml:
        folder_type = "castcharm"
    elif year_folders and len(year_folders) >= len(subdirs) * 0.5:
        folder_type = "year_organized"
    elif season_folders and len(season_folders) >= len(subdirs) * 0.5:
        folder_type = "season_organized"
    elif not subdirs or all(os.path.dirname(f) == directory for f in audio_files):
        folder_type = "flat"
    else:
        folder_type = "mixed"

    return {
        "type": folder_type,
        "has_xml_sidecars": has_xml,
        "subfolder_count": len(subdirs),
        "audio_file_count": len(audio_files),
        "year_folders": [int(y) for y in year_folders],
        "season_folders": season_folders,
        "sample_filenames": [os.path.basename(f) for f in audio_files[:5]],
    }


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _similarity(a: str, b: str) -> float:
    a_tok = set(_normalize(a).split())
    b_tok = set(_normalize(b).split())
    if not a_tok or not b_tok:
        return 0.0
    return len(a_tok & b_tok) / max(len(a_tok), len(b_tok))


def _parse_tracknumber(raw: str) -> Optional[int]:
    m = re.match(r"(\d+)", raw)
    return int(m.group(1)) if m else None


def _match_to_episode(sidecar: dict, tags: dict, fn_info: dict,
                      candidates: list) -> Optional[Episode]:
    # 1. Guid (from our own sidecar — definitive)
    if sidecar.get("guid"):
        for ep in candidates:
            if ep.guid == sidecar["guid"]:
                return ep

    # 2. Enclosure URL (also definitive)
    if sidecar.get("enclosure_url"):
        for ep in candidates:
            if ep.enclosure_url == sidecar["enclosure_url"]:
                return ep

    # Gather best title and episode number candidates
    title = (sidecar.get("title") or tags.get("title") or fn_info.get("title") or "").strip()

    ep_num = None
    if sidecar.get("episode_number"):
        try:
            ep_num = int(sidecar["episode_number"])
        except (ValueError, TypeError):
            pass
    if ep_num is None and tags.get("tracknumber"):
        ep_num = _parse_tracknumber(tags["tracknumber"])
    if ep_num is None:
        ep_num = fn_info.get("episode_number")

    # 3. Episode number + title similarity (lenient)
    if ep_num is not None and title:
        for ep in candidates:
            if ep.episode_number == ep_num and ep.title:
                if _similarity(title, ep.title) >= 0.4:
                    return ep

    # 4. Title similarity (higher threshold)
    if title:
        best_ep, best_score = None, 0.0
        for ep in candidates:
            if ep.title:
                s = _similarity(title, ep.title)
                if s > best_score:
                    best_score, best_ep = s, ep
        if best_score >= 0.70:
            return best_ep

    return None


# ---------------------------------------------------------------------------
# Path building for renamed files
# ---------------------------------------------------------------------------

def _get_feed_filename_settings(feed: Feed, db: Session, overrides: Optional[dict] = None) -> dict:
    """Precompute the feed-level constants needed by _build_file_path.

    Returns a dict that can be passed to _build_expected_basename / _build_target_path
    without any further DB queries.
    """
    from app.downloader import (
        _build_file_path, _get_effective_settings, _effective, _sanitize_filename,
    )
    from sqlalchemy import func as _func

    overrides = overrides or {}
    gs = _get_effective_settings(feed, db)
    base_dir    = _effective(feed.download_path,           gs.download_path,           "/downloads")
    date_prefix = overrides.get("date_prefix",
                  _effective(feed.filename_date_prefix,    gs.filename_date_prefix,    True))
    ep_num_pfx  = overrides.get("ep_num_prefix",
                  _effective(feed.filename_episode_number, gs.filename_episode_number, True))
    by_year     = overrides.get("organize_by_year",
                  _effective(feed.organize_by_year,        gs.organize_by_year,        True))

    if feed.primary_feed_id:
        primary = db.query(Feed).filter(Feed.id == feed.primary_feed_id).first()
        folder_raw = (primary.podcast_group or primary.title) if primary else (feed.podcast_group or feed.title or "Unknown Podcast")
    else:
        folder_raw = feed.podcast_group or feed.title or "Unknown Podcast"

    primary_id = feed.primary_feed_id or feed.id
    grp_ids = get_group_feed_ids(db, primary_id)
    total_eps = (
        db.query(_func.count(Episode.id))
        .filter(Episode.feed_id.in_(grp_ids), Episode.hidden.is_(False))
        .scalar() or 0
    )

    return {
        "_build_file_path": _build_file_path,
        "_sanitize_filename": _sanitize_filename,
        "folder_name":  _sanitize_filename(folder_raw),
        "base_dir":     base_dir,
        "date_prefix":  date_prefix,
        "ep_num_pfx":   ep_num_pfx,
        "by_year":      by_year,
        "total_eps":    total_eps,
        "timezone":     gs.timezone or "UTC",
    }


def _build_expected_basename(ep: Episode, fs: dict, src_path: str) -> str:
    """Return the expected filename (basename only) for *ep* using pre-computed
    feed settings *fs* from _get_feed_filename_settings.  No DB access."""
    full_path = fs["_build_file_path"](
        ep, fs["folder_name"], fs["base_dir"],
        fs["date_prefix"], fs["ep_num_pfx"], fs["by_year"],
        None, src_path, total_episodes=fs["total_eps"], timezone=fs["timezone"],
    )
    return os.path.basename(full_path)


def _build_target_path(ep: Episode, feed: Feed, src_path: str, db: Session,
                       overrides: Optional[dict] = None) -> str:
    fs = _get_feed_filename_settings(feed, db, overrides)
    full_path = fs["_build_file_path"](
        ep, fs["folder_name"], fs["base_dir"],
        fs["date_prefix"], fs["ep_num_pfx"], fs["by_year"],
        None, src_path, total_episodes=fs["total_eps"], timezone=fs["timezone"],
    )
    return full_path


# ---------------------------------------------------------------------------
# Date interpolation
# ---------------------------------------------------------------------------

def _interpolate_missing_dates(feed_id: int, db: Session) -> int:
    """Assign interpolated approximate dates to episodes that lack a real date.

    Episodes are ordered by episode_number (natural sequence) then id (insertion
    order).  Any contiguous run of episodes without a known date that is bounded
    on both sides by episodes with known dates gets equally-spaced dates assigned
    between the two anchors.  Runs at the very start or end of the sequence
    (no anchor on one side) are left untouched.

    Returns the number of episodes updated.
    """
    feed = db.query(Feed).filter(Feed.id == feed_id).first()
    if not feed:
        return 0

    primary_id = feed.primary_feed_id or feed.id
    all_ids = get_group_feed_ids(db, primary_id)

    episodes = (
        db.query(Episode)
        .filter(Episode.feed_id.in_(all_ids), Episode.hidden.is_(False))
        .order_by(Episode.episode_number.asc().nullslast(), Episode.id.asc())
        .all()
    )

    def _has_real_date(ep: Episode) -> bool:
        return ep.published_at is not None and not ep.date_is_approximate

    updated = 0
    n = len(episodes)
    i = 0

    while i < n:
        if _has_real_date(episodes[i]):
            i += 1
            continue

        # Start of a run of episodes without a known date
        run_start = i
        while i < n and not _has_real_date(episodes[i]):
            i += 1
        run_end = i  # exclusive — episodes[run_end] is the right anchor (if it exists)

        left_idx = run_start - 1
        right_idx = run_end

        # Both sides need a known-date anchor
        if left_idx < 0 or right_idx >= n:
            continue
        if not _has_real_date(episodes[left_idx]) or not _has_real_date(episodes[right_idx]):
            continue

        d_left = episodes[left_idx].published_at
        d_right = episodes[right_idx].published_at

        if d_right <= d_left:
            # Dates are inverted — can't interpolate meaningfully
            continue

        span = d_right - d_left
        count = run_end - run_start

        for j, ep in enumerate(episodes[run_start:run_end]):
            frac = (j + 1) / (count + 1)
            ep.published_at = d_left + span * frac
            ep.date_is_approximate = True
            updated += 1

    if updated:
        db.commit()

    return updated


# ---------------------------------------------------------------------------
# Scored matching (used by preview and staged import)
# ---------------------------------------------------------------------------

def _dur_seconds(s: Optional[str]) -> Optional[int]:
    """Parse a duration string (H:MM:SS or M:SS) to total seconds."""
    if not s:
        return None
    try:
        parts = str(s).split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, TypeError):
        pass
    return None


def _match_to_episode_scored(
    sidecar: dict, tags: dict, fn_info: dict,
    candidates: list,
    file_dur_s: Optional[int] = None,
) -> list:
    """Return (episode, confidence, method) triples sorted by confidence desc.

    Confidence factors (max 1.0):
      title similarity  × 0.50
      episode number    × 0.20  (checks ep.episode_number OR ep.seq_number)
      date match        × 0.15  (exact date match on published_at)
      duration ≤5% diff × 0.15  (partial credit down to ≤15% diff)
    GUID / enclosure URL hits return confidence 1.0 and skip scoring.
    """
    title = (sidecar.get("title") or tags.get("title") or fn_info.get("title") or "").strip()

    ep_num = None
    if sidecar.get("episode_number"):
        try:
            ep_num = int(sidecar["episode_number"])
        except (ValueError, TypeError):
            pass
    if ep_num is None and tags.get("tracknumber"):
        ep_num = _parse_tracknumber(tags["tracknumber"])
    if ep_num is None:
        ep_num = fn_info.get("episode_number")

    file_date = fn_info.get("date") or (
        _parse_date(sidecar.get("published", "")) if sidecar.get("published") else None
    )

    results = []
    for ep in candidates:
        # Definitive matches via our own sidecar metadata
        if sidecar.get("guid") and ep.guid == sidecar["guid"]:
            results.append((ep, 1.0, "guid"))
            continue
        if sidecar.get("enclosure_url") and ep.enclosure_url == sidecar["enclosure_url"]:
            results.append((ep, 1.0, "url"))
            continue

        score = 0.0

        # Title similarity (weight 0.50)
        if title and ep.title:
            score += _similarity(title, ep.title) * 0.50

        # Episode number match (weight 0.20)
        # Check both the RSS episode_number tag and the system seq_number —
        # castcharm filenames embed seq_number, not the RSS episode number.
        if ep_num is not None and (ep.episode_number == ep_num or ep.seq_number == ep_num):
            score += 0.20

        # Date match (weight 0.15) — exact calendar-day match on published_at
        if file_date and ep.published_at:
            if (file_date.year == ep.published_at.year
                    and file_date.month == ep.published_at.month
                    and file_date.day == ep.published_at.day):
                score += 0.15

        # Duration proximity (weight 0.15)
        ep_dur_s = _dur_seconds(ep.duration)
        if file_dur_s and ep_dur_s and ep_dur_s > 0:
            ratio = min(file_dur_s, ep_dur_s) / max(file_dur_s, ep_dur_s)
            if ratio >= 0.95:
                score += 0.15
            elif ratio >= 0.85:
                score += 0.08

        if score < 0.15:
            continue  # not worth returning

        ep_num_match = ep_num is not None and (ep.episode_number == ep_num or ep.seq_number == ep_num)
        title_match  = title and ep.title and _similarity(title, ep.title) >= 0.35
        if ep_num_match and title_match:
            method = "ep_num_title"
        elif title_match:
            method = "title"
        elif ep_num_match:
            method = "ep_num"
        else:
            method = "fuzzy"

        results.append((ep, min(score, 0.99), method))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Preview (dry-run — no DB writes)
# ---------------------------------------------------------------------------

def preview_import_directory(feed_id: int, directory: str, db: Session,
                             filename_format: Optional[str] = None) -> dict:
    """Scan *directory* and return match results without committing anything to DB.

    Returns a dict with:
      files        — list of per-file preview objects
      total_files  — total audio files found
      matched      — files with a confident episode match
      unmatched    — files that would create a new episode
      registered   — files already linked to an episode (shown as read-only)
    """
    _preview_jobs[feed_id] = {"phase": "counting", "current": 0, "total": 0,
                              "message": "Counting files\u2026"}

    feed = db.query(Feed).filter(Feed.id == feed_id).first()
    if not feed:
        _preview_jobs.pop(feed_id, None)
        return {"error": "Feed not found", "files": [], "total_files": 0, "matched": 0, "unmatched": 0, "registered": 0}

    # Collect audio files
    audio_files: list[str] = []
    for root, _dirs, files in os.walk(directory):
        for fname in sorted(files):
            if os.path.splitext(fname)[1].lower() in AUDIO_EXTENSIONS:
                audio_files.append(os.path.join(root, fname))

    total_files = len(audio_files)
    _preview_jobs[feed_id] = {"phase": "scanning", "current": 0, "total": total_files,
                              "message": f"Found {total_files} file{'' if total_files == 1 else 's'} \u2014 reading metadata\u2026"}

    # Folder analysis for the UI
    folder_analysis = _detect_folder_type(directory, audio_files)

    # Compile user-supplied filename format (if any)
    fmt_re = _compile_filename_format(filename_format) if filename_format else None

    # Precompute feed filename settings once — used later for filename_exact matching.
    # This avoids per-episode DB queries in the inner loop.
    try:
        _feed_filename_settings = _get_feed_filename_settings(feed, db)
    except Exception:
        _feed_filename_settings = None

    # Normalized feed title for smart stripping
    feed_title_norm = _normalize(feed.title) if feed.title else ""

    # All feed episodes (including supplementary feeds)
    primary_id = feed.primary_feed_id or feed.id
    all_feed_ids = get_group_feed_ids(db, primary_id)

    existing = (
        db.query(Episode)
        .filter(Episode.feed_id.in_(all_feed_ids), Episode.hidden.is_(False))
        .all()
    )

    registered_paths = {
        os.path.normpath(ep.file_path): ep
        for ep in existing
        if ep.file_path and os.path.exists(ep.file_path)
    }
    # Also index by filename so that importing from a different directory
    # (e.g. an archive copy) still recognises an already-downloaded episode.
    registered_filenames = {
        os.path.basename(ep.file_path): ep
        for ep in existing
        if ep.file_path and os.path.exists(ep.file_path)
    }
    # Episodes without a file on disk are candidates for matching
    candidates = [ep for ep in existing if not (ep.file_path and os.path.exists(ep.file_path))]

    matched_ep_ids: set[int] = set()
    file_previews = []

    for audio_path in audio_files:
        norm_path = os.path.normpath(audio_path)
        stem = os.path.splitext(os.path.basename(audio_path))[0]

        _preview_jobs[feed_id]["current"] += 1
        _preview_jobs[feed_id]["message"] = os.path.basename(audio_path)

        sidecar = _read_xml_sidecar(audio_path)
        tags    = _read_id3_tags(audio_path)
        fn_info = _parse_filename_with_format(stem, fmt_re) if fmt_re else _parse_filename(stem)
        folder_ctx = _parse_folder_context(audio_path, directory)

        # Smart podcast-name stripping: if the parsed title starts with
        # the feed title, strip it for better matching
        if feed_title_norm and fn_info.get("title"):
            title_norm = _normalize(fn_info["title"])
            if title_norm.startswith(feed_title_norm):
                remainder = fn_info["title"][len(feed.title):].lstrip(" -\u2013\u2014")
                if remainder:
                    fn_info["title_stripped"] = remainder

        # Track which source provided each field
        sources = {}

        # Title cascade
        if sidecar.get("title"):
            title = sidecar["title"].strip()
            sources["title"] = "sidecar"
        elif tags.get("title"):
            title = tags["title"].strip()
            sources["title"] = "id3"
        elif fn_info.get("title"):
            title = fn_info["title"].strip()
            sources["title"] = "filename"
        else:
            title = stem
            sources["title"] = "filename"

        duration = sidecar.get("duration") or tags.get("duration")
        if duration:
            sources["duration"] = "sidecar" if sidecar.get("duration") else "id3"

        # Date cascade (includes folder context)
        published_at = None
        date_is_approximate = False
        if sidecar.get("published"):
            published_at = _parse_date(sidecar["published"])
            if published_at:
                sources["date"] = "sidecar"
                if _is_year_only(sidecar["published"]):
                    date_is_approximate = True
        if published_at is None and fn_info.get("date"):
            published_at = fn_info["date"]
            sources["date"] = "filename"
        if published_at is None and tags.get("date"):
            published_at = _parse_date(tags["date"])
            if published_at:
                sources["date"] = "id3"
                if _is_year_only(tags["date"]):
                    date_is_approximate = True
        if published_at is None and folder_ctx.get("folder_year"):
            published_at = datetime(folder_ctx["folder_year"], 1, 1, 12, 0, 0)
            date_is_approximate = True
            sources["date"] = "folder"

        # Episode number cascade
        ep_num = None
        if sidecar.get("episode_number"):
            try:
                ep_num = int(sidecar["episode_number"])
                sources["episode_number"] = "sidecar"
            except (ValueError, TypeError):
                pass
        if ep_num is None and tags.get("tracknumber"):
            ep_num = _parse_tracknumber(tags["tracknumber"])
            if ep_num is not None:
                sources["episode_number"] = "id3"
        if ep_num is None and fn_info.get("episode_number") is not None:
            ep_num = fn_info["episode_number"]
            sources["episode_number"] = "filename"

        # Season number cascade
        season_num = None
        if sidecar.get("season_number"):
            try:
                season_num = int(sidecar["season_number"])
                sources["season_number"] = "sidecar"
            except (ValueError, TypeError):
                pass
        if season_num is None and fn_info.get("season_number") is not None:
            season_num = fn_info["season_number"]
            sources["season_number"] = "filename"
        if season_num is None and folder_ctx.get("folder_season") is not None:
            season_num = folder_ctx["folder_season"]
            sources["season_number"] = "folder"

        # Already registered to an episode?  Check full path first, then
        # filename alone (handles importing from an archive/different directory
        # when the episode was originally downloaded to the feed folder).
        owning_ep = registered_paths.get(norm_path) or registered_filenames.get(os.path.basename(audio_path))
        if owning_ep:
            file_previews.append({
                "path": audio_path,
                "filename": os.path.basename(audio_path),
                "title": title,
                "date": published_at.strftime("%Y-%m-%d") if published_at else None,
                "date_is_approximate": date_is_approximate,
                "episode_number": ep_num,
                "season_number": season_num,
                "duration": duration,
                "already_registered": True,
                "match": {"episode_id": owning_ep.id, "episode_title": owning_ep.title,
                          "confidence": 1.0, "method": "registered"},
                "alternatives": [],
                "metadata_sources": sources,
            })
            continue

        # Definitive match by filename: handles two cases:
        # (a) ep.file_path still set but file deleted/moved elsewhere — compare basenames
        # (b) ep.file_path cleared (app deleted the file) — reconstruct the expected
        #     filename from feed settings and compare against the import basename.
        import_basename = os.path.basename(audio_path)
        filename_ep = next(
            (ep for ep in candidates
             if ep.id not in matched_ep_ids
             and ep.file_path
             and os.path.basename(ep.file_path) == import_basename),
            None,
        )
        if filename_ep is None and _feed_filename_settings is not None:
            # Case (b): file_path cleared — reconstruct expected filename per feed settings.
            # Uses pre-computed settings (no DB queries per episode).
            for ep in candidates:
                if ep.id in matched_ep_ids:
                    continue
                try:
                    if _build_expected_basename(ep, _feed_filename_settings, audio_path) == import_basename:
                        filename_ep = ep
                        break
                except Exception:
                    pass

        if filename_ep:
            best_match = {
                "episode_id": filename_ep.id,
                "episode_title": filename_ep.title,
                "confidence": 1.0,
                "method": "filename_exact",
            }
            matched_ep_ids.add(filename_ep.id)
            file_previews.append({
                "path": audio_path,
                "filename": os.path.basename(audio_path),
                "title": title,
                "date": published_at.strftime("%Y-%m-%d") if published_at else None,
                "date_is_approximate": date_is_approximate,
                "episode_number": ep_num,
                "season_number": season_num,
                "duration": duration,
                "already_registered": False,
                "match": best_match,
                "alternatives": [],
                "metadata_sources": sources,
            })
            continue

        # Score against unmatched candidates — use stripped title for matching
        if fn_info.get("title_stripped"):
            match_fn_info = {**fn_info, "title": fn_info["title_stripped"]}
        else:
            match_fn_info = fn_info
        available = [ep for ep in candidates if ep.id not in matched_ep_ids]
        file_dur_s = _dur_seconds(duration)
        scored = _match_to_episode_scored(sidecar, tags, match_fn_info, available, file_dur_s)

        best_match = None
        alternatives = []

        if scored:
            top_ep, top_conf, top_method = scored[0]
            if top_conf >= 0.35:
                best_match = {
                    "episode_id": top_ep.id,
                    "episode_title": top_ep.title,
                    "confidence": round(top_conf, 2),
                    "method": top_method,
                }
                matched_ep_ids.add(top_ep.id)

            alternatives = [
                {"episode_id": ep.id, "episode_title": ep.title,
                 "confidence": round(conf, 2), "method": method}
                for ep, conf, method in scored[1:4]
                if conf >= 0.20 and ep.id not in matched_ep_ids
            ]

        file_previews.append({
            "path": audio_path,
            "filename": os.path.basename(audio_path),
            "title": title,
            "date": published_at.strftime("%Y-%m-%d") if published_at else None,
            "date_is_approximate": date_is_approximate,
            "episode_number": ep_num,
            "season_number": season_num,
            "duration": duration,
            "already_registered": False,
            "match": best_match,
            "alternatives": alternatives,
            "metadata_sources": sources,
        })

    n_registered = sum(1 for f in file_previews if f["already_registered"])
    n_matched    = sum(1 for f in file_previews if not f["already_registered"] and f["match"])
    n_unmatched  = sum(1 for f in file_previews if not f["already_registered"] and not f["match"])

    # Determine whether unmatched files may cause renumbering of existing episodes.
    # Only relevant when the feed already has episodes.
    has_existing = len(existing) > 0
    may_renumber = False
    if has_existing and n_unmatched > 0:
        unmatched_files = [f for f in file_previews if not f["already_registered"] and not f["match"]]
        existing_ep_nums = {ep.episode_number for ep in existing if ep.episode_number is not None}
        existing_dates   = [ep.published_at for ep in existing if ep.published_at is not None]
        oldest_existing  = min(existing_dates) if existing_dates else None

        for f in unmatched_files:
            # Episode number detected but not present in the feed → likely fills a gap
            if f["episode_number"] is not None and f["episode_number"] not in existing_ep_nums:
                may_renumber = True
                break
            # Date is earlier than the oldest episode we know about
            if f["date"] and oldest_existing:
                try:
                    f_dt = datetime.strptime(f["date"], "%Y-%m-%d")
                    if f_dt < oldest_existing:
                        may_renumber = True
                        break
                except ValueError:
                    pass

    _preview_jobs.pop(feed_id, None)
    return {
        "folder_analysis": folder_analysis,
        "files": file_previews,
        "total_files": len(audio_files),
        "matched": n_matched,
        "unmatched": n_unmatched,
        "registered": n_registered,
        "has_existing_episodes": has_existing,
        "may_renumber": may_renumber,
    }


# ---------------------------------------------------------------------------
# Staged import (commit phase)
# ---------------------------------------------------------------------------

def import_staged(feed_id: int, items: list, db: Session,
                   filename_format: Optional[str] = None) -> dict:
    """Execute an import using explicit file→episode mappings from the staging UI.

    Each *item* dict has:
      path           — audio file path
      episode_id     — existing episode to link (None → create new)
      skip           — if True, skip this file entirely
      title          — optional title override (for new episodes or overwrites)
      date           — optional date override "YYYY-MM-DD"
      episode_number — optional episode number override
    """
    to_process = [item for item in items if not item.get("skip", False)]
    fmt_re = _compile_filename_format(filename_format) if filename_format else None

    _import_jobs[feed_id] = {
        "status": "running", "total": len(to_process), "processed": 0,
        "matched": 0, "created": 0, "renamed": 0, "errors": 0,
        "message": "Starting import…",
    }

    feed = db.query(Feed).filter(Feed.id == feed_id).first()
    if not feed:
        _import_jobs[feed_id] = {"status": "error", "message": "Feed not found"}
        return _import_jobs[feed_id]

    primary_id = feed.primary_feed_id or feed.id
    all_feed_ids = get_group_feed_ids(db, primary_id)

    matched = created = errors = 0
    pending: list[tuple] = []  # (episode, audio_path) for pass 2

    # ── Pass 1: create/match all episodes (no file copies yet) ──
    for item in to_process:
        audio_path = item["path"]
        episode_id = item.get("episode_id")

        try:
            if not os.path.exists(audio_path):
                log.warning("Staged import: file not found: %s", audio_path)
                errors += 1
                continue

            if episode_id:
                ep = db.query(Episode).filter(
                    Episode.id == episode_id,
                    Episode.feed_id.in_(all_feed_ids),
                ).first()
                if not ep:
                    log.warning("Staged import: episode %d not found for feed %d", episode_id, feed_id)
                    errors += 1
                    continue
                # Apply any user overrides to the matched episode
                if item.get("title"):
                    ep.title = item["title"]
                if item.get("date"):
                    ep.published_at = _parse_date(item["date"])
                if item.get("episode_number") is not None:
                    ep.episode_number = item["episode_number"]
                matched += 1
            else:
                # Create a new episode from file metadata + overrides
                stem = os.path.splitext(os.path.basename(audio_path))[0]
                sidecar = _read_xml_sidecar(audio_path)
                tags    = _read_id3_tags(audio_path)
                fn_info = _parse_filename_with_format(stem, fmt_re) if fmt_re else _parse_filename(stem)

                title = (item.get("title") or sidecar.get("title") or
                         tags.get("title") or fn_info.get("title") or stem).strip()
                guid  = sidecar.get("guid") or "import:" + hashlib.sha256(audio_path.encode()).hexdigest()[:24]

                ep_num = item.get("episode_number")
                if ep_num is None and sidecar.get("episode_number"):
                    try:
                        ep_num = int(sidecar["episode_number"])
                    except (ValueError, TypeError):
                        pass
                if ep_num is None and tags.get("tracknumber"):
                    ep_num = _parse_tracknumber(tags["tracknumber"])
                if ep_num is None:
                    ep_num = fn_info.get("episode_number")

                published_at: Optional[datetime] = None
                date_is_approximate = False
                if item.get("date"):
                    published_at = _parse_date(item["date"])
                if published_at is None and sidecar.get("published"):
                    published_at = _parse_date(sidecar["published"])
                    if published_at and _is_year_only(sidecar["published"]):
                        date_is_approximate = True
                if published_at is None and fn_info.get("date"):
                    published_at = fn_info["date"]
                if published_at is None and tags.get("date"):
                    published_at = _parse_date(tags["date"])
                    if published_at and _is_year_only(tags["date"]):
                        date_is_approximate = True

                duration = sidecar.get("duration") or tags.get("duration")

                # Season number cascade
                season_num = item.get("season_number")
                if season_num is None and sidecar.get("season_number"):
                    try:
                        season_num = int(sidecar["season_number"])
                    except (ValueError, TypeError):
                        pass
                if season_num is None:
                    season_num = fn_info.get("season_number")

                ep = Episode(
                    feed_id             = feed_id,
                    title               = title,
                    guid                = guid,
                    published_at        = published_at,
                    date_is_approximate = date_is_approximate,
                    duration            = duration,
                    episode_number      = ep_num,
                    season_number       = season_num,
                    enclosure_url       = sidecar.get("enclosure_url"),
                    enclosure_type      = sidecar.get("enclosure_type"),
                    author              = sidecar.get("author") or tags.get("artist"),
                    description         = sidecar.get("description") or tags.get("description"),
                    link                = sidecar.get("link"),
                    episode_image_url   = sidecar.get("image_url"),
                )
                # Lock the seq_number when the user provided an explicit episode
                # number — this anchors it during recalc_seq_numbers so the rest
                # of the numbering flows around it.
                if ep_num is not None and item.get("episode_number") is not None:
                    ep.seq_number = ep_num
                    ep.seq_number_locked = True
                db.add(ep)
                db.flush()
                created += 1

            pending.append((ep, audio_path))

        except Exception as e:
            log.error("Staged import error (pass 1) for %s: %s", audio_path, e)
            errors += 1
            try:
                db.rollback()
            except Exception:
                pass

    db.commit()

    # ── Between passes: calculate seq_numbers so filenames are correct ──
    from app.routers.episodes import recalc_seq_numbers
    try:
        recalc_seq_numbers(primary_id, db)
        db.commit()
    except Exception as e:
        log.warning("recalc_seq_numbers failed during staged import: %s", e)

    # Interpolate dates for episodes without real date info
    try:
        interpolated = _interpolate_missing_dates(feed_id, db)
        if interpolated:
            log.info("Interpolated approximate dates for %d episode(s) in feed %d", interpolated, feed_id)
    except Exception as e:
        log.warning("Date interpolation failed for feed %d: %s", feed_id, e)

    # ── Pass 2: copy files with correct names ──
    for ep, audio_path in pending:
        _import_jobs[feed_id]["processed"] += 1
        try:
            final_path = audio_path
            try:
                target = _build_target_path(ep, feed, audio_path, db)
                if os.path.normpath(target) != os.path.normpath(audio_path) and not os.path.exists(target):
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copy2(audio_path, target)
                    xml_src = audio_path + ".xml"
                    if os.path.exists(xml_src):
                        try:
                            shutil.copy2(xml_src, target + ".xml")
                        except OSError:
                            pass
                    final_path = target
            except Exception as cp_err:
                log.warning("Copy to managed folder failed for %s: %s (linking in-place)", audio_path, cp_err)

            ep.status            = "downloaded"
            ep.file_path         = final_path
            ep.download_progress = 100
            ep.imported          = True
            if os.path.exists(final_path):
                ep.file_size = os.path.getsize(final_path)
            if not ep.download_date:
                try:
                    ep.download_date = datetime.fromtimestamp(os.path.getmtime(final_path))
                except OSError:
                    ep.download_date = datetime.utcnow()

            db.commit()

        except Exception as e:
            log.error("Staged import error (pass 2) for %s: %s", audio_path, e)
            errors += 1
            try:
                db.rollback()
            except Exception:
                pass

    # Count existing episodes that now need a file rename due to seq_number shift
    rename_needed = (
        db.query(Episode)
        .filter(
            Episode.feed_id.in_(get_group_feed_ids(db, primary_id)),
            Episode.filename_outdated.is_(True),
            Episode.file_path.isnot(None),
        )
        .count()
    )

    summary = {
        "status":    "done",
        "total":     len(to_process),
        "processed": len(to_process),
        "matched":   matched,
        "created":   created,
        "renamed":   0,
        "errors":    errors,
        "rename_needed": rename_needed,
        "message": (
            f"Import complete: {matched} linked to existing episodes, {created} new"
            + (f", {errors} error{'s' if errors != 1 else ''}" if errors else "")
        ),
    }
    _import_jobs[feed_id] = summary
    log.info("Staged import feed %d: %s", feed_id, summary["message"])
    return summary


# ---------------------------------------------------------------------------
# Main import function
# ---------------------------------------------------------------------------

def import_directory(feed_id: int, directory: str, rename_files: bool,
                     db: Session, overrides: Optional[dict] = None,
                     filename_format: Optional[str] = None) -> dict:
    _import_jobs[feed_id] = {
        "status": "running", "total": 0, "processed": 0,
        "matched": 0, "created": 0, "renamed": 0, "errors": 0,
        "message": "Scanning…",
    }
    fmt_re = _compile_filename_format(filename_format) if filename_format else None

    feed = db.query(Feed).filter(Feed.id == feed_id).first()
    if not feed:
        _import_jobs[feed_id] = {"status": "error", "message": "Feed not found"}
        return _import_jobs[feed_id]

    # Gather all audio files under the directory
    audio_files: list[str] = []
    for root, _dirs, files in os.walk(directory):
        for fname in sorted(files):
            if os.path.splitext(fname)[1].lower() in AUDIO_EXTENSIONS:
                audio_files.append(os.path.join(root, fname))

    if not audio_files:
        result = {"status": "done", "total": 0, "processed": 0,
                  "matched": 0, "created": 0, "renamed": 0, "errors": 0,
                  "message": "No audio files found in that directory."}
        _import_jobs[feed_id] = result
        return result

    _import_jobs[feed_id]["total"] = len(audio_files)
    _import_jobs[feed_id]["message"] = f"Processing {len(audio_files)} files…"

    # Load all feed episodes (including supplementary feeds)
    primary_id = feed.primary_feed_id or feed.id
    all_feed_ids = get_group_feed_ids(db, primary_id)
    existing = db.query(Episode).filter(
        Episode.feed_id.in_(all_feed_ids), Episode.hidden.is_(False)
    ).all()

    # Files already registered to an episode — skip them entirely so we don't
    # create duplicates when running import on a folder that already has downloads.
    registered_paths: set[str] = {
        os.path.normpath(ep.file_path)
        for ep in existing
        if ep.file_path and os.path.exists(ep.file_path)
    }

    # Only unregistered episodes are candidates for new file→episode linking
    candidates = [
        ep for ep in existing
        if not (ep.file_path and os.path.exists(ep.file_path))
    ]

    matched = created = renamed = errors = 0
    pending: list[tuple] = []  # (episode, audio_path) for pass 2

    # ── Pass 1: create/match all episodes (no file copies yet) ──
    for audio_path in audio_files:
        try:
            if os.path.normpath(audio_path) in registered_paths:
                _import_jobs[feed_id]["processed"] += 1
                continue

            stem = os.path.splitext(os.path.basename(audio_path))[0]

            sidecar  = _read_xml_sidecar(audio_path)
            tags     = _read_id3_tags(audio_path)
            fn_info  = _parse_filename_with_format(stem, fmt_re) if fmt_re else _parse_filename(stem)

            title       = (sidecar.get("title") or tags.get("title") or fn_info.get("title") or stem).strip()
            duration    = sidecar.get("duration") or tags.get("duration")
            author      = sidecar.get("author") or tags.get("artist")

            published_at: Optional[datetime] = None
            date_is_approximate = False
            if sidecar.get("published"):
                published_at = _parse_date(sidecar["published"])
                if published_at and _is_year_only(sidecar["published"]):
                    date_is_approximate = True
            if published_at is None and fn_info.get("date"):
                published_at = fn_info["date"]
            if published_at is None and tags.get("date"):
                published_at = _parse_date(tags["date"])
                if published_at and _is_year_only(tags["date"]):
                    date_is_approximate = True

            ep_num = None
            if sidecar.get("episode_number"):
                try:
                    ep_num = int(sidecar["episode_number"])
                except (ValueError, TypeError):
                    pass
            if ep_num is None and tags.get("tracknumber"):
                ep_num = _parse_tracknumber(tags["tracknumber"])
            if ep_num is None:
                ep_num = fn_info.get("episode_number")

            ep = _match_to_episode(sidecar, tags, fn_info, candidates)

            if ep:
                candidates.remove(ep)
                matched += 1
                if not ep.published_at and published_at:
                    ep.published_at = published_at
                    ep.date_is_approximate = date_is_approximate
                if not ep.duration and duration:
                    ep.duration = duration
                if not ep.episode_number and ep_num:
                    ep.episode_number = ep_num
            else:
                guid = (
                    sidecar.get("guid")
                    or "import:" + hashlib.sha256(audio_path.encode()).hexdigest()[:24]
                )
                ep = Episode(
                    feed_id             = feed_id,
                    title               = title,
                    guid                = guid,
                    published_at        = published_at,
                    date_is_approximate = date_is_approximate,
                    duration            = duration,
                    episode_number      = ep_num,
                    enclosure_url       = sidecar.get("enclosure_url"),
                    enclosure_type      = sidecar.get("enclosure_type"),
                    author              = author,
                    description         = sidecar.get("description") or tags.get("description"),
                    link                = sidecar.get("link"),
                    episode_image_url   = sidecar.get("image_url"),
                )
                # Lock the seq_number when the episode number was extracted
                # via a user-supplied filename format — the user explicitly
                # defined the pattern, so the number is authoritative.
                if ep_num is not None and fmt_re is not None:
                    ep.seq_number = ep_num
                    ep.seq_number_locked = True
                db.add(ep)
                db.flush()
                created += 1

            pending.append((ep, audio_path))

        except Exception as e:
            log.error("Import error (pass 1) for %s: %s", audio_path, e)
            errors += 1
            try:
                db.rollback()
            except Exception:
                pass

    db.commit()

    # ── Between passes: calculate seq_numbers so filenames are correct ──
    from app.routers.episodes import recalc_seq_numbers
    try:
        recalc_seq_numbers(primary_id, db)
        db.commit()
    except Exception as e:
        log.warning("recalc_seq_numbers failed during import: %s", e)

    # Interpolate dates for episodes without real date info
    try:
        interpolated = _interpolate_missing_dates(feed_id, db)
        if interpolated:
            log.info("Interpolated approximate dates for %d episode(s) in feed %d", interpolated, feed_id)
    except Exception as e:
        log.warning("Date interpolation failed for feed %d: %s", feed_id, e)

    # ── Pass 2: copy files with correct names ──
    for ep, audio_path in pending:
        _import_jobs[feed_id]["processed"] += 1
        try:
            final_path = audio_path
            try:
                target = _build_target_path(ep, feed, audio_path, db, overrides=overrides)
                if os.path.normpath(target) != os.path.normpath(audio_path) and not os.path.exists(target):
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copy2(audio_path, target)
                    xml_src = audio_path + ".xml"
                    if os.path.exists(xml_src):
                        try:
                            shutil.copy2(xml_src, target + ".xml")
                        except OSError:
                            pass
                    final_path = target
                    renamed += 1
            except Exception as cp_err:
                log.warning("Copy to managed folder failed for %s: %s (linking in-place)", audio_path, cp_err)

            ep.status            = "downloaded"
            ep.file_path         = final_path
            ep.download_progress = 100
            ep.imported          = True
            if os.path.exists(final_path):
                ep.file_size = os.path.getsize(final_path)
            if not ep.download_date:
                try:
                    ep.download_date = datetime.fromtimestamp(os.path.getmtime(final_path))
                except OSError:
                    ep.download_date = datetime.utcnow()

            db.commit()

        except Exception as e:
            log.error("Import error (pass 2) for %s: %s", audio_path, e)
            errors += 1
            try:
                db.rollback()
            except Exception:
                pass

    # Count existing episodes that now need a file rename due to seq_number shift
    rename_needed = (
        db.query(Episode)
        .filter(
            Episode.feed_id.in_(all_feed_ids),
            Episode.filename_outdated.is_(True),
            Episode.file_path.isnot(None),
        )
        .count()
    )

    summary = {
        "status":    "done",
        "total":     len(audio_files),
        "processed": len(audio_files),
        "matched":   matched,
        "created":   created,
        "renamed":   renamed,
        "errors":    errors,
        "rename_needed": rename_needed,
        "message": (
            f"Import complete: {matched} matched to feed episodes, "
            f"{created} new"
            + (f", {renamed} renamed" if renamed else "")
            + (f", {errors} error{'s' if errors != 1 else ''}" if errors else "")
        ),
    }

    _import_jobs[feed_id] = summary
    log.info("Import feed %d: %s", feed_id, summary["message"])
    return summary
