"""Import existing audio files into a podcast feed.

Pipeline (preview and commit share every step except the DB writes):

  1. walk the folder in natural order          -> _collect_audio_files
  2. read sidecar / tags / filename / folder     -> _read_raw_file
  3. decide which number source to trust         -> _infer_number_scheme
  4. resolve one FileMeta per file               -> _finalize_meta
  5. score every plausible (file, episode) pair  -> _score_pair
  6. assign globally, best pair first            -> _assign_globally
  7. tier / ambiguity / alternatives / duplicates
  8. commit: link or create, never overwrite RSS metadata unless the user
     edited a field (item["overrides"]), then interpolate dates, recalc
     numbers, copy files.
"""
import difflib
import hashlib
import logging
import os
import re
import shutil
import unicodedata
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass, field
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

# Tag cache: (path, mtime_ns, size) → result dict.
# Keyed by stat fields so a changed file gets a fresh read while unchanged
# files (including re-scans and preview→import round-trips) skip the I/O.
# Bounded LRU so a very large library cannot grow it without limit.
_ID3_CACHE_MAX = 5000
_id3_cache: "OrderedDict[tuple, dict]" = OrderedDict()


def _cache_get(key: tuple) -> Optional[dict]:
    v = _id3_cache.get(key)
    if v is not None:
        _id3_cache.move_to_end(key)
    return v


def _cache_put(key: tuple, value: dict) -> None:
    _id3_cache[key] = value
    _id3_cache.move_to_end(key)
    while len(_id3_cache) > _ID3_CACHE_MAX:
        _id3_cache.popitem(last=False)


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


_ITUNES_DESCS = frozenset({"iTunNORM", "iTunSMPB", "iTunPGAP", "iTunes_CDDB_IDs"})
_HEX_DUMP_RE  = re.compile(r"^[0-9A-Fa-f ]{20,}$")


def _read_id3_tags(audio_path: str) -> dict:
    """Read mutagen tags from any audio file.

    Results are cached by (path, mtime_ns, size) so repeated calls for the
    same unchanged file (e.g. preview then import) cost only one stat() instead
    of two full file opens.  A single easy=False open extracts both the
    standard fields and the COMM description frame.
    """
    # --- cache lookup ---
    cache_key: tuple | None = None
    try:
        st = os.stat(audio_path)
        cache_key = (audio_path, st.st_mtime_ns, st.st_size)
        cached_hit = _cache_get(cache_key)
        if cached_hit is not None:
            return cached_hit
    except OSError:
        pass

    try:
        from mutagen import File as MutagenFile

        # Single open with easy=True for normalized field names across all
        # container formats (MP3, M4A, OGG, FLAC, …).
        audio = MutagenFile(audio_path, easy=True)
        if audio is None:
            cached: dict = {}
            if cache_key:
                _cache_put(cache_key, cached)
            return cached
        tags = audio.tags or {}

        def _first(key):
            v = tags.get(key)
            return str(v[0]).strip() if v else None

        result: dict = {
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

        # Second pass (raw) only for COMM description frame — easy mode doesn't
        # expose it.  Reuse the already-opened easy object's underlying file
        # path; mutagen re-opens quickly since the OS page cache is warm.
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
                    if not desc_attr:
                        best_comm = text
                        break
                    if best_comm is None:
                        best_comm = text
                if best_comm:
                    result.setdefault("description", best_comm)
        except Exception:
            pass

        result = {k: v for k, v in result.items() if v is not None}
        if cache_key:
            _cache_put(cache_key, result)
        return result
    except Exception as e:
        log.warning("Tag read error %s: %s", audio_path, e)
        empty: dict = {}
        if cache_key:
            _cache_put(cache_key, empty)
        return empty


# ---------------------------------------------------------------------------
# User-defined filename format parsing
# ---------------------------------------------------------------------------

# Tokens the user can place in a format string and what they capture.
_FORMAT_TOKENS: dict[str, tuple[str, str]] = {
    # token             → (regex,                    named group)
    "%EpisodeNum%":     (r"(?P<epnum>\d+)",          "epnum"),
    "%Title%":          (r"(?P<title>.+?)",          "title"),
    "%Date%":           (r"(?P<date>\d{4}[-._/]\d{2}[-._/]\d{2}|\d{8}|\d{2}[-.]\d{2}[-.]\d{4})", "date"),
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
            result["date_precision"] = "day"
    # Assemble date from individual components
    if "_year" in gd:
        try:
            y = int(gd["_year"])
            mo = int(gd.get("_month", "1"))
            d = int(gd.get("_day", "1"))
            result["date"] = datetime(y, mo, d)
            result["date_precision"] = "day" if "_day" in gd else ("month" if "_month" in gd else "year")
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

# A date as it commonly appears in a filename.  Parsed by _parse_date.
_FN_DATE = r"\d{4}[-._/]\d{2}[-._/]\d{2}|\d{8}|\d{2}[-.]\d{2}[-.]\d{4}"
# A separator character with optional spaces, or just whitespace ("20090417 Title").
_SEP = r"(?:\s*[-–—_:.]\s*|\s+)"
# Strict form for trailing numbers, so "Dogs Part 2" is not "Dogs Part", ep 2.
_SEP_STRICT = r"\s*[-–—_:.]\s*"

# (label, %Token% equivalent, compiled regex).  Tried in order; the label and
# format string are what the preview reports as the "detected pattern".
_FN_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("S01E05 - Podcast - Title", "S%Season%E%EpisodeNum% - %Ignore% - %Title%",
     re.compile(r"^[Ss](?P<season>\d{1,3})[Ee](?P<epnum>\d{1,4})" + _SEP_STRICT + r"(?P<podcast>[^-–—_]+?)" + _SEP_STRICT + r"(?P<title>.+)$")),
    ("S01E05 - Title", "S%Season%E%EpisodeNum% - %Title%",
     re.compile(r"^[Ss](?P<season>\d{1,3})[Ee](?P<epnum>\d{1,4})" + _SEP + r"(?P<title>.+)$")),
    ("S01E05Title", "S%Season%E%EpisodeNum%%Title%",
     re.compile(r"^[Ss](?P<season>\d{1,3})[Ee](?P<epnum>\d{1,4})(?P<title>.+)$")),
    ("Date - ### - Title", "%Date% - %EpisodeNum% - %Title%",
     re.compile(r"^(?P<date>" + _FN_DATE + r")" + _SEP + r"(?P<epnum>\d+)" + _SEP + r"(?P<title>.+)$")),
    ("### - Date - Title", "%EpisodeNum% - %Date% - %Title%",
     re.compile(r"^(?P<epnum>\d{1,4})" + _SEP + r"(?P<date>" + _FN_DATE + r")" + _SEP + r"(?P<title>.+)$")),
    ("Date - Title", "%Date% - %Title%",
     re.compile(r"^(?P<date>" + _FN_DATE + r")" + _SEP + r"(?P<title>.+)$")),
    ("Title - Date", "%Title% - %Date%",
     re.compile(r"^(?P<title>.+?)" + _SEP + r"(?P<date>" + _FN_DATE + r")$")),
    ("Episode N - Title", "Episode %EpisodeNum% - %Title%",
     re.compile(r"^(?:episode|ep\.?|epi|show|no\.?|#)\s*(?P<epnum>\d+)" + _SEP + r"(?P<title>.+)$", re.IGNORECASE)),
    ("### - Podcast - Title", "%EpisodeNum% - %Ignore% - %Title%",
     re.compile(r"^(?P<epnum>\d{1,4})" + _SEP_STRICT + r"(?P<podcast>[^-–—_]+?)" + _SEP_STRICT + r"(?P<title>.+)$")),
    ("### - Title", "%EpisodeNum% - %Title%",
     re.compile(r"^(?P<epnum>\d{1,4})" + _SEP + r"(?P<title>.+)$")),
    ("Title - ###", "%Title% - %EpisodeNum%",
     re.compile(r"^(?P<title>.+?)" + _SEP_STRICT + r"(?:episode|ep\.?|#)?\s*(?P<epnum>\d{1,4})$", re.IGNORECASE)),
    ("###Title", "%EpisodeNum%%Title%",
     re.compile(r"^(?P<epnum>\d{2,4})(?P<title>[A-Za-z].+)$")),
]
_FN_FALLBACK = re.compile(r"^(?P<title>.+)$")

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9


def _parse_date(s: str) -> Optional[datetime]:
    """Parse the date shapes that turn up in sidecars, tags and filenames.

    Returns a naive datetime.  Year-only strings become Jan 1 12:00 and
    month-only strings the 15th 12:00 — see _date_precision, which callers
    use to flag those as approximate.
    """
    if not s:
        return None
    s = str(s).strip()
    # ISO-ish with optional time / zone: take the leading date part.
    for fmt, n in [("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d %H:%M:%S", 19),
                   ("%Y-%m-%d", 10), ("%Y/%m/%d", 10), ("%Y.%m.%d", 10), ("%Y_%m_%d", 10),
                   ("%Y%m%d", 8)]:
        try:
            return datetime.strptime(s[:n], fmt)
        except (ValueError, TypeError):
            pass
    # MM-DD-YYYY or DD.MM.YYYY: decide by which field can be a month.
    m = re.match(r"^(\d{2})[-./](\d{2})[-./](\d{4})", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            if a > 12 and b <= 12:
                return datetime(y, b, a)
            return datetime(y, a, b)
        except ValueError:
            return None
    # "March 5, 2009" / "5 March 2009" / "Mar 5 2009"
    m = re.match(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m.group(1).lower() in _MONTHS:
        try:
            return datetime(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", s)
    if m and m.group(2).lower() in _MONTHS:
        try:
            return datetime(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            return None
    # RFC 2822 (pubDate style)
    if "," in s and re.search(r"\d{4}", s):
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(s)
            return dt.replace(tzinfo=None) if dt else None
        except Exception:
            pass
    m = re.match(r"^(\d{4})-(\d{2})$", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), 15, 12, 0, 0)
        except ValueError:
            return None
    m = re.match(r"^(\d{4})$", s)
    if m and 1900 <= int(m.group(1)) <= 2099:
        return datetime(int(m.group(1)), 1, 1, 12, 0, 0)
    return None


def _date_precision(s: str) -> str:
    """'year', 'month' or 'day' for the string _parse_date was given."""
    s = str(s or "").strip()
    if re.match(r"^\d{4}$", s):
        return "year"
    if re.match(r"^\d{4}-\d{2}$", s):
        return "month"
    return "day"


def _is_year_only(s: str) -> bool:
    """Return True if the date string is just a 4-digit year (approximate date)."""
    return _date_precision(s) == "year"


def _natural_key(path: str) -> tuple:
    """Sort key so 'ep 9' precedes 'ep 10' and folders sort with their files."""
    parts = re.split(r"(\d+)", path.lower())
    return tuple(int(p) if p.isdigit() else p for p in parts)


def _parse_filename(stem: str) -> dict:
    """Heuristic filename parse.  Returns any of: title, date, date_precision,
    episode_number, season_number, pattern (index into _FN_PATTERNS)."""
    stem = stem.strip()
    for idx, (_label, _fmt, pat) in enumerate(_FN_PATTERNS):
        m = pat.match(stem)
        if not m:
            continue
        gd = {k: v for k, v in m.groupdict().items() if v is not None}
        result: dict = {"pattern": idx}
        if "title" in gd:
            result["title"] = gd["title"].strip()
        if "date" in gd:
            dt = _parse_date(gd["date"])
            if dt:
                result["date"] = dt
                result["date_precision"] = "day"
        if "epnum" in gd and gd["epnum"].isdigit():
            n = int(gd["epnum"])
            # A bare 4-digit year in the number slot ("2009 - Title") is a
            # date, not episode two-thousand-and-nine.
            if 1900 <= n <= 2099 and len(gd["epnum"]) == 4 and "date" not in result:
                result["date"] = datetime(n, 1, 1, 12, 0, 0)
                result["date_precision"] = "year"
            else:
                result["episode_number"] = n
        if "season" in gd and gd["season"].isdigit():
            result["season_number"] = int(gd["season"])
        # A pattern that captured a number/date but an empty title is not a
        # useful parse; keep looking.
        if result.get("title") or "epnum" in gd or "date" in gd:
            return result
    return {"title": stem, "pattern": None}


def _detect_filename_pattern(stems: list[str]) -> list[dict]:
    """Which built-in patterns describe this folder?  Returns the top hits as
    [{label, format, count, share}] so the UI can say "Detected: ### - Title
    (94%)" and offer the matching %Token% string as a starting point."""
    if not stems:
        return []
    counts: dict[int, int] = {}
    for st in stems:
        idx = _parse_filename(st).get("pattern")
        if idx is not None:
            counts[idx] = counts.get(idx, 0) + 1
    out = []
    for idx, c in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:3]:
        label, fmt, _ = _FN_PATTERNS[idx]
        out.append({"label": label, "format": fmt, "count": c,
                    "share": round(c / len(stems), 2)})
    return out


# ---------------------------------------------------------------------------
# Folder analysis
# ---------------------------------------------------------------------------

def _parse_folder_context(audio_path: str, base_dir: Optional[str] = None) -> dict:
    """Extract metadata hints (year, season) from the folder names around the file.

    With *base_dir*, only the folders between it and the file are examined.
    Without one — the commit path does not know where the scan started — the
    nearest four ancestors are examined instead, so a file in ``…/2009/`` is
    read the same way whether it is being previewed or imported.
    """
    d = os.path.dirname(audio_path)
    parts: list[str]
    if base_dir:
        rel = os.path.relpath(d, base_dir)
        parts = [] if rel == "." or rel.startswith("..") else rel.split(os.sep)
    else:
        parts = []
        for _ in range(4):
            head, tail = os.path.split(d)
            if not tail or head == d:
                break
            parts.append(tail)
            d = head
    result = {}
    for part in parts:
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
# Title similarity
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


# "[128k]", "(mp3)", "(final)", "[HQ]" … — encoder/format noise in brackets.
_BRACKET_TAG_RE = re.compile(
    r"[\[(][^\])]*?(?:\d{2,3}\s*k(?:bps)?|mp3|m4a|aac|ogg|flac|wav|mono|stereo|"
    r"hq|lq|final|remaster(?:ed)?|edit|clean|explicit)[^\])]*?[\])]", re.IGNORECASE)
# "Episode 12 -", "Ep. 12:", "#12", "012 " at the start of a title.
_NOISE_PREFIX_RE = re.compile(
    r"^(?:(?:episode|ep\.?|epi|show|no\.?|number|#)\s*)?\d{1,4}\s*[-–—:.)\]]*\s+", re.IGNORECASE)
_NOISE_WORD_RE = re.compile(r"^(?:episode|ep|the|a|an|and|of|with)$")


def _clean_title(s: Optional[str], feed_title: Optional[str] = None) -> str:
    """Normalised title with the noise that hides real similarity removed:
    bracketed format tags, the podcast's own name, and a leading episode
    number.  A title that is nothing but a number is left alone."""
    if not s:
        return ""
    raw = _BRACKET_TAG_RE.sub(" ", str(s))
    t = _normalize(raw)
    if feed_title:
        ft = _normalize(feed_title)
        if ft and len(ft) >= 3:
            if t.startswith(ft + " "):
                t = t[len(ft):].strip()
            elif t.endswith(" " + ft):
                t = t[:-len(ft)].strip()
    stripped = _NOISE_PREFIX_RE.sub("", t).strip()
    if stripped:
        t = stripped
    return t


def _tokens(clean: str) -> frozenset:
    return frozenset(w for w in clean.split() if not _NOISE_WORD_RE.match(w))


def _similarity(a: str, b: str, feed_title: Optional[str] = None) -> float:
    """0..1 similarity of two titles: the better of token overlap and a
    character-level ratio, both on cleaned strings."""
    ca, cb = _clean_title(a, feed_title), _clean_title(b, feed_title)
    return _similarity_clean(ca, cb, _tokens(ca), _tokens(cb))


def _similarity_clean(ca: str, cb: str, ta: frozenset, tb: frozenset) -> float:
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0
    jac = len(ta & tb) / max(len(ta), len(tb)) if ta and tb else 0.0
    # The character ratio is comparatively slow; only pay for it when the
    # token view says the pair is at least in the neighbourhood, or when one
    # side has too few tokens for the token view to mean anything.
    if jac >= 0.15 or min(len(ta), len(tb)) <= 2:
        sm = difflib.SequenceMatcher(None, ca, cb)
        if sm.quick_ratio() >= max(0.5, jac):
            return max(jac, sm.ratio())
    return jac


def _parse_tracknumber(raw: str) -> Optional[int]:
    m = re.match(r"\s*(\d+)", str(raw))
    return int(m.group(1)) if m else None


def _dur_seconds(s) -> Optional[int]:
    """Parse a duration (H:MM:SS, M:SS, plain seconds, with optional
    fractional part) to whole seconds."""
    if s is None or s == "":
        return None
    try:
        txt = str(s).strip()
        if re.match(r"^\d+(\.\d+)?$", txt):
            return int(float(txt))
        parts = txt.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(float(parts[1]))
    except (ValueError, TypeError):
        pass
    return None


# ---------------------------------------------------------------------------
# Per-file metadata
# ---------------------------------------------------------------------------

@dataclass
class FileMeta:
    """Everything we know about one audio file, with where each fact came from."""
    path: str
    filename: str
    stem: str
    size: int = 0
    mtime: float = 0.0
    sidecar: dict = field(default_factory=dict)
    tags: dict = field(default_factory=dict)
    fn_info: dict = field(default_factory=dict)
    folder_ctx: dict = field(default_factory=dict)
    # resolved
    title: str = ""
    title_for_match: str = ""
    date: Optional[datetime] = None
    date_is_approximate: bool = False
    date_precision: str = "day"
    episode_number: Optional[int] = None
    number_trusted: bool = False
    season_number: Optional[int] = None
    duration: Optional[str] = None
    duration_s: Optional[int] = None
    sources: dict = field(default_factory=dict)
    # derived for scoring
    clean: str = ""
    toks: frozenset = frozenset()


def _collect_audio_files(directory: str) -> list[str]:
    out: list[str] = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for fname in files:
            if os.path.splitext(fname)[1].lower() in AUDIO_EXTENSIONS:
                out.append(os.path.join(root, fname))
    out.sort(key=_natural_key)
    return out


def _read_raw_file(audio_path: str, base_dir: str, fmt_re: Optional[re.Pattern]) -> FileMeta:
    """Step 2: read every source for one file.  No decisions yet."""
    stem = os.path.splitext(os.path.basename(audio_path))[0]
    fm = FileMeta(path=audio_path, filename=os.path.basename(audio_path), stem=stem)
    try:
        st = os.stat(audio_path)
        fm.size, fm.mtime = st.st_size, st.st_mtime
    except OSError:
        pass
    fm.sidecar = _read_xml_sidecar(audio_path)
    fm.tags = _read_id3_tags(audio_path)
    fm.fn_info = _parse_filename_with_format(stem, fmt_re) if fmt_re else _parse_filename(stem)
    fm.folder_ctx = _parse_folder_context(audio_path, base_dir)
    return fm


def _raw_numbers(fm: FileMeta) -> dict[str, Optional[int]]:
    """Candidate episode numbers by source, before any trust decision."""
    out: dict[str, Optional[int]] = {"sidecar": None, "filename": None, "id3": None}
    if fm.sidecar.get("episode_number"):
        try:
            out["sidecar"] = int(fm.sidecar["episode_number"])
        except (ValueError, TypeError):
            pass
    if fm.fn_info.get("episode_number") is not None:
        out["filename"] = fm.fn_info["episode_number"]
    if fm.tags.get("tracknumber"):
        out["id3"] = _parse_tracknumber(fm.tags["tracknumber"])
    return out


def _infer_number_scheme(raws: list[FileMeta]) -> dict:
    """Step 3: which number sources can be believed for this folder?

    A source is trusted when, across the files that have it, the values are
    mostly distinct and mostly rise along natural filename order.  ID3 track
    numbers additionally need a few files to prove themselves — a lone "1" is
    far more often an album track than episode one.  Our own sidecars are
    always trusted.
    """
    per_source: dict[str, list[int]] = {"sidecar": [], "filename": [], "id3": []}
    for fm in raws:
        for src, n in _raw_numbers(fm).items():
            if n is not None:
                per_source[src].append(n)

    scheme: dict = {"trusted": {}, "stats": {}}
    for src, vals in per_source.items():
        n = len(vals)
        if n == 0:
            scheme["trusted"][src] = False
            scheme["stats"][src] = {"count": 0}
            continue
        distinct = len(set(vals)) / n
        rises = sum(1 for a, b in zip(vals, vals[1:]) if b >= a)
        monotone = rises / (n - 1) if n > 1 else 1.0
        if src == "sidecar":
            ok = True
        elif src == "id3":
            ok = n >= 3 and distinct >= 0.7 and monotone >= 0.6 and max(vals) > 3
        else:  # filename
            ok = n == 1 or (distinct >= 0.7 and monotone >= 0.6)
        scheme["trusted"][src] = ok
        scheme["stats"][src] = {"count": n, "distinct": round(distinct, 2),
                                "monotone": round(monotone, 2),
                                "min": min(vals), "max": max(vals)}
    return scheme


def _finalize_meta(fm: FileMeta, scheme: dict, feed_title: Optional[str]) -> FileMeta:
    """Step 4: pick one value per field, recording its source."""
    sc, tags, fn, folder = fm.sidecar, fm.tags, fm.fn_info, fm.folder_ctx
    src = fm.sources

    # Title
    if sc.get("title"):
        fm.title, src["title"] = sc["title"].strip(), "sidecar"
    elif tags.get("title"):
        fm.title, src["title"] = tags["title"].strip(), "id3"
    elif fn.get("title"):
        fm.title, src["title"] = fn["title"].strip(), "filename"
    else:
        fm.title, src["title"] = fm.stem, "filename"
    fm.clean = _clean_title(fm.title, feed_title)
    fm.toks = _tokens(fm.clean)
    fm.title_for_match = fm.clean

    # Duration
    fm.duration = sc.get("duration") or tags.get("duration")
    if fm.duration:
        src["duration"] = "sidecar" if sc.get("duration") else "id3"
    fm.duration_s = _dur_seconds(fm.duration)

    # Episode number: sidecar → filename → id3, each only if trusted
    nums = _raw_numbers(fm)
    trusted = scheme.get("trusted", {})
    for s_name in ("sidecar", "filename", "id3"):
        if nums[s_name] is not None:
            fm.episode_number, src["episode_number"] = nums[s_name], s_name
            fm.number_trusted = bool(trusted.get(s_name))
            if fm.number_trusted:
                break
            # keep looking for a trusted source; fall back to this one otherwise
    if fm.episode_number is not None and not fm.number_trusted:
        # Prefer the highest-priority untrusted value already recorded above.
        for s_name in ("sidecar", "filename", "id3"):
            if nums[s_name] is not None:
                fm.episode_number, src["episode_number"] = nums[s_name], s_name
                break

    # Date: sidecar → filename → id3 → folder year → file mtime
    if sc.get("published"):
        d = _parse_date(sc["published"])
        if d:
            fm.date, src["date"] = d, "sidecar"
            fm.date_precision = _date_precision(sc["published"])
    if fm.date is None and fn.get("date"):
        fm.date, src["date"] = fn["date"], "filename"
        fm.date_precision = fn.get("date_precision", "day")
    if fm.date is None and tags.get("date"):
        d = _parse_date(tags["date"])
        if d:
            fm.date, src["date"] = d, "id3"
            fm.date_precision = _date_precision(tags["date"])
    if fm.date is None and folder.get("folder_year"):
        fm.date = datetime(folder["folder_year"], 1, 1, 12, 0, 0)
        fm.date_precision, src["date"] = "year", "folder"
    # A file's modification time is a weak hint (copies reset it).  Use it
    # only when nothing else — no date, no trusted number — could place the
    # file; otherwise leave the date empty so interpolation orders it by number.
    if fm.date is None and fm.mtime and not fm.number_trusted:
        try:
            d = datetime.fromtimestamp(fm.mtime)
            if 1990 <= d.year <= datetime.now().year:
                fm.date, src["date"] = d.replace(microsecond=0), "mtime"
                fm.date_precision = "day"
        except (OverflowError, OSError, ValueError):
            pass
    fm.date_is_approximate = fm.date is not None and (
        fm.date_precision != "day" or src.get("date") == "mtime")

    # Season
    if sc.get("season_number"):
        try:
            fm.season_number, src["season_number"] = int(sc["season_number"]), "sidecar"
        except (ValueError, TypeError):
            pass
    if fm.season_number is None and fn.get("season_number") is not None:
        fm.season_number, src["season_number"] = fn["season_number"], "filename"
    if fm.season_number is None and folder.get("folder_season") is not None:
        fm.season_number, src["season_number"] = folder["folder_season"], "folder"

    return fm


def _extract_file_metadata(audio_path: str, base_dir: Optional[str], fmt_re: Optional[re.Pattern],
                           feed_title: Optional[str], scheme: Optional[dict] = None) -> FileMeta:
    """One-file convenience for the commit paths: read + finalize.  When no
    folder-wide scheme is available, a single file's own filename number is
    trusted and an ID3 track number is not."""
    raw = _read_raw_file(audio_path, base_dir, fmt_re)
    if scheme is None:
        scheme = {"trusted": {"sidecar": True, "filename": True, "id3": False}}
    return _finalize_meta(raw, scheme, feed_title)


def _content_guid(audio_path: str) -> str:
    """Stable synthetic GUID for a file without one: size plus a 64 KiB slice
    from the middle of the audio, so retagging (which rewrites the head/tail)
    or moving the file does not change it, and a rescan of the original folder
    recognises files that were copied into the library."""
    try:
        st = os.stat(audio_path)
        key = ("cg", audio_path, st.st_mtime_ns, st.st_size)
        hit = _cache_get(key)
        if hit is not None:
            return hit["guid"]
        size = st.st_size
        h = hashlib.sha256(str(size).encode())
        with open(audio_path, "rb") as fh:
            fh.seek(max(0, size // 2 - 32 * 1024))
            h.update(fh.read(64 * 1024))
        guid = "import:" + h.hexdigest()[:24]
        _cache_put(key, {"guid": guid})
        return guid
    except OSError:
        return "import:" + hashlib.sha256(audio_path.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Candidate episodes
# ---------------------------------------------------------------------------

@dataclass
class _Cand:
    ep: Episode
    clean: str
    toks: frozenset
    date: Optional[datetime]
    dur_s: Optional[int]
    numbers: frozenset  # {episode_number, seq_number} minus None


def _make_cands(episodes: list, feed_title: Optional[str]) -> list[_Cand]:
    out = []
    for ep in episodes:
        c = _clean_title(ep.title or "", feed_title)
        nums = frozenset(n for n in (ep.episode_number, ep.seq_number) if n is not None)
        out.append(_Cand(ep=ep, clean=c, toks=_tokens(c), date=ep.published_at,
                         dur_s=_dur_seconds(ep.duration), numbers=nums))
    return out


class _CandIndex:
    """Token / number / date indices so each file is scored only against the
    episodes it could plausibly be — not the whole feed."""

    def __init__(self, cands: list[_Cand]):
        self.cands = cands
        self.by_token: dict[str, set[int]] = {}
        self.by_number: dict[int, set[int]] = {}
        self.by_day: dict = {}
        for i, c in enumerate(cands):
            for t in c.toks:
                self.by_token.setdefault(t, set()).add(i)
            for n in c.numbers:
                self.by_number.setdefault(n, set()).add(i)
            if c.date:
                self.by_day.setdefault(c.date.date(), set()).add(i)

    def plausible(self, fm: FileMeta, top_k: int = 30) -> set[int]:
        """Episodes worth scoring in full: every number and date-window hit,
        plus the *top_k* by cheap token overlap.  Keeps the expensive
        character-level comparison off the thousands of episodes that merely
        share a common word with the file."""
        hits: dict[int, int] = {}
        for t in fm.toks:
            for ci in self.by_token.get(t, ()):
                hits[ci] = hits.get(ci, 0) + 1
        out: set[int] = set()
        if fm.episode_number is not None:
            out |= self.by_number.get(fm.episode_number, set())
        if fm.date and fm.date_precision == "day":
            from datetime import timedelta
            d0 = fm.date.date()
            for delta in range(-7, 8):
                out |= self.by_day.get(d0 + timedelta(days=delta), set())
        if hits:
            n_file = max(len(fm.toks), 1)
            ranked = sorted(hits.items(),
                            key=lambda kv: kv[1] / max(n_file, len(self.cands[kv[0]].toks)),
                            reverse=True)
            out.update(ci for ci, _ in ranked[:top_k])
        return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_pair(fm: FileMeta, c: _Cand) -> Optional[tuple[float, str, list[str]]]:
    """Confidence that *fm* is episode *c*.

      title similarity           × 0.50
      episode number             + 0.20 trusted / + 0.05 untrusted
      trusted number disagrees   − 0.10
      date  same day +0.15 · ±1d +0.13 · ±3d +0.08 · ±7d +0.04
      duration ≤5% +0.15 · ≤15% +0.08

    Returns (score, method, reasons) or None when there is nothing to say.
    """
    score = 0.0
    reasons: list[str] = []

    sim = _similarity_clean(fm.clean, c.clean, fm.toks, c.toks)
    if sim > 0:
        score += sim * 0.50
        if sim >= 0.35:
            reasons.append(f"title {int(round(sim * 100))}%")

    num_match = False
    if fm.episode_number is not None and c.numbers:
        if fm.episode_number in c.numbers:
            num_match = True
            score += 0.20 if fm.number_trusted else 0.05
            reasons.append(f"number #{fm.episode_number}")
        elif fm.number_trusted and c.ep.episode_number is not None:
            score -= 0.10
            reasons.append(f"number #{fm.episode_number} ≠ #{c.ep.episode_number}")

    if fm.date and c.date and fm.date_precision == "day":
        dd = abs((fm.date.date() - c.date.date()).days)
        if dd == 0:
            score += 0.15; reasons.append("date ✓")
        elif dd <= 1:
            score += 0.13; reasons.append("date ±1d")
        elif dd <= 3:
            score += 0.08; reasons.append(f"date ±{dd}d")
        elif dd <= 7:
            score += 0.04; reasons.append(f"date ±{dd}d")
    elif fm.date and c.date and fm.date_precision == "year" and fm.date.year == c.date.year:
        score += 0.02

    if fm.duration_s and c.dur_s and c.dur_s > 0:
        ratio = min(fm.duration_s, c.dur_s) / max(fm.duration_s, c.dur_s)
        if ratio >= 0.95:
            score += 0.15; reasons.append("duration ✓")
        elif ratio >= 0.85:
            score += 0.08; reasons.append("duration ~")

    if score < 0.15:
        return None

    title_match = sim >= 0.35
    if num_match and title_match:
        method = "ep_num_title"
    elif title_match:
        method = "title"
    elif num_match:
        method = "ep_num"
    else:
        method = "fuzzy"
    return (min(round(score, 4), 0.99), method, reasons)


def _corroboration(reasons: list[str]) -> int:
    """How many independent non-title facts agree (number, date, duration);
    -1 when a trusted number contradicts."""
    if any("≠" in r for r in reasons):
        return -1
    return sum(1 for r in reasons if r.startswith(("number #", "date", "duration")))


def _tier_for(score: Optional[float], method: str, ambiguous: bool,
              reasons: Optional[list[str]] = None) -> str:
    if method in ("guid", "url", "filename_exact", "registered"):
        return "certain"
    if score is None:
        return "none"
    if not ambiguous and (score >= 0.75 or
                          (score >= 0.60 and _corroboration(reasons or []) >= 2)):
        return "strong"
    if score >= 0.50:
        return "likely"
    if score >= 0.30:
        return "weak"
    return "none"


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------

def _assign_globally(files: list[FileMeta], cands: list[_Cand],
                     definite: dict[int, tuple[int, str]]) -> tuple[dict, dict]:
    """Steps 5–6.  *definite* maps file index → (cand index, method) for
    guid/url/filename matches that skip scoring.

    Returns (assignment, scored) where assignment[file_idx] = (cand_idx,
    score, method, reasons) and scored[file_idx] = that file's candidates
    sorted best first (for alternatives / ambiguity).
    """
    idx = _CandIndex(cands)
    scored: dict[int, list[tuple[int, float, str, list[str]]]] = {}
    edges: list[tuple[float, int, int, str, list[str]]] = []

    for fi, fm in enumerate(files):
        if fi in definite:
            continue
        rows = []
        for ci in idx.plausible(fm):
            r = _score_pair(fm, cands[ci])
            if r:
                rows.append((ci, r[0], r[1], r[2]))
        rows.sort(key=lambda x: x[1], reverse=True)
        scored[fi] = rows[:6]
        for ci, sc, m, rs in rows:
            edges.append((sc, fi, ci, m, rs))

    assignment: dict[int, tuple[int, float, str, list[str]]] = {}
    taken_c: set[int] = set()
    for fi, (ci, method) in definite.items():
        assignment[fi] = (ci, 1.0, method, [method.replace("_", " ")])
        taken_c.add(ci)

    edges.sort(key=lambda e: e[0], reverse=True)
    for sc, fi, ci, m, rs in edges:
        if fi in assignment or ci in taken_c:
            continue
        if sc < 0.30:
            break
        assignment[fi] = (ci, sc, m, rs)
        taken_c.add(ci)
    return assignment, scored


def _numbering_alignment(files: list[FileMeta], existing: list, assignment: dict,
                         cands: list[_Cand], skip: Optional[set[int]] = None) -> dict:
    """Step 7a: how do the numbers in the folder relate to the feed's numbers?
    *skip* holds indices of files that are already registered."""
    skip = skip or set()
    file_nums = sorted({fm.episode_number for fi, fm in enumerate(files)
                        if fi not in skip and fm.episode_number is not None and fm.number_trusted})
    feed_nums = sorted({(ep.episode_number if ep.episode_number is not None else ep.seq_number)
                        for ep in existing
                        if (ep.episode_number is not None or ep.seq_number is not None)})
    out: dict = {"file_min": file_nums[0] if file_nums else None,
                 "file_max": file_nums[-1] if file_nums else None,
                 "feed_min": feed_nums[0] if feed_nums else None,
                 "feed_max": feed_nums[-1] if feed_nums else None,
                 "overlap_count": 0, "overlap_matched": 0}
    if not file_nums:
        out["kind"] = "unnumbered"
        out["summary"] = "No trustworthy episode numbers were found in the file names or tags."
        return out
    if not feed_nums:
        out["kind"] = "no_feed_numbers"
        out["summary"] = f"Files are numbered {file_nums[0]}–{file_nums[-1]}; the feed has no numbering yet."
        return out
    overlap = set(file_nums) & set(feed_nums)
    out["overlap_count"] = len(overlap)
    if overlap:
        matched_by_num = 0
        for fi, (ci, sc, m, rs) in assignment.items():
            fm = files[fi]
            if fm.episode_number in overlap and fm.episode_number in cands[ci].numbers:
                matched_by_num += 1
        out["overlap_matched"] = matched_by_num
        out["kind"] = "overlaps"
        out["summary"] = (f"Files are numbered {file_nums[0]}–{file_nums[-1]}; "
                          f"{len(overlap)} of them overlap the feed's numbers "
                          f"({feed_nums[0]}–{feed_nums[-1]}), and {matched_by_num} matched an episode by number.")
    elif file_nums[-1] < feed_nums[0]:
        gap = feed_nums[0] - file_nums[-1] - 1
        out["kind"] = "precedes"
        out["summary"] = (f"Files are numbered {file_nums[0]}–{file_nums[-1]}; the feed starts at #{feed_nums[0]}"
                          + (" — the numbering lines up." if gap == 0 else f" — {gap} number(s) are missing in between."))
    elif file_nums[0] > feed_nums[-1]:
        out["kind"] = "follows"
        out["summary"] = f"Files are numbered {file_nums[0]}–{file_nums[-1]}, after the feed's last number #{feed_nums[-1]}."
    else:
        out["kind"] = "interleaved"
        out["summary"] = (f"Files are numbered {file_nums[0]}–{file_nums[-1]} and the feed {feed_nums[0]}–{feed_nums[-1]}, "
                          "but no numbers are shared — the two numbering schemes may differ.")
    return out


def _find_duplicates(files: list[FileMeta], new_idx: list[int]) -> dict[int, int]:
    """Step 7b: among files that would create new episodes, find copies of the
    same episode (same trusted number, or near-identical title and length).
    Returns {dup_file_idx: kept_file_idx}; the largest file is kept."""
    dup_of: dict[int, int] = {}
    by_num: dict[int, list[int]] = {}
    for fi in new_idx:
        fm = files[fi]
        if fm.episode_number is not None and fm.number_trusted:
            by_num.setdefault(fm.episode_number, []).append(fi)
    grouped: set[int] = set()
    for members in by_num.values():
        if len(members) > 1:
            keep = max(members, key=lambda i: files[i].size)
            for fi in members:
                if fi != keep:
                    dup_of[fi] = keep
            grouped.update(members)
    rest = sorted((fi for fi in new_idx if fi not in grouped), key=lambda i: files[i].clean)
    for a in range(len(rest)):
        fa = files[rest[a]]
        if rest[a] in dup_of or not fa.clean:
            continue
        for b in range(a + 1, min(a + 6, len(rest))):
            fb = files[rest[b]]
            if rest[b] in dup_of:
                continue
            if (fa.number_trusted and fb.number_trusted and fa.episode_number is not None
                    and fb.episode_number is not None and fa.episode_number != fb.episode_number):
                continue
            if fa.toks != fb.toks or _similarity_clean(fa.clean, fb.clean, fa.toks, fb.toks) < 0.9:
                continue
            if fa.duration_s and fb.duration_s:
                ratio = min(fa.duration_s, fb.duration_s) / max(fa.duration_s, fb.duration_s)
                if ratio < 0.98:
                    continue
            keep, other = (rest[a], rest[b]) if fa.size >= fb.size else (rest[b], rest[a])
            dup_of[other] = keep
    return dup_of


# ---------------------------------------------------------------------------
# Path building for renamed files
# ---------------------------------------------------------------------------

def _get_feed_filename_settings(feed: Feed, db: Session, overrides: Optional[dict] = None) -> dict:
    """Precompute the feed-level constants needed to build file names.

    Returns a dict that can be passed to _build_expected_basename / _build_target_path
    without any further DB queries.
    """
    from app.downloader import (
        _build_file_path, _get_effective_settings, _effective, _sanitize_filename,
        build_expected_basename,
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
        "_expected_basename": build_expected_basename,
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
    """Expected filename (basename only) for *ep*.  Pure: no directories are
    created and no collision suffix is added, so a preview can call it."""
    return fs["_expected_basename"](
        ep, fs["date_prefix"], fs["ep_num_pfx"], None, src_path,
        total_episodes=fs["total_eps"], timezone=fs["timezone"],
    )


def _build_target_path(ep: Episode, feed: Feed, src_path: str, db: Session,
                       overrides: Optional[dict] = None, fs: Optional[dict] = None) -> str:
    fs = fs or _get_feed_filename_settings(feed, db, overrides)
    return fs["_build_file_path"](
        ep, fs["folder_name"], fs["base_dir"],
        fs["date_prefix"], fs["ep_num_pfx"], fs["by_year"],
        None, src_path, total_episodes=fs["total_eps"], timezone=fs["timezone"],
    )


# ---------------------------------------------------------------------------
# Date interpolation
# ---------------------------------------------------------------------------

def _is_year_marker(dt: Optional[datetime]) -> bool:
    """Approximate dates that carry only a year are stored as Jan 1 12:00:00."""
    return bool(dt) and dt.month == 1 and dt.day == 1 and dt.hour == 12 and dt.minute == 0 and dt.second == 0


def _interpolate_missing_dates(feed_id: int, db: Session) -> int:
    """Give undated and year-only episodes a plausible date so they sort and
    number sensibly.

    Year-only episodes (published_at = Jan 1 12:00 and date_is_approximate)
    are spread evenly through their year in sequence order.  Episodes with no
    date at all get evenly spaced dates between the nearest dated neighbours
    in sequence order; runs at either end of the sequence are left alone.
    Everything touched is marked date_is_approximate.

    Returns the number of episodes updated.
    """
    from datetime import timedelta

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

    updated = 0

    # Pass A: year-only dates → spread through the year.
    by_year: dict[int, list[Episode]] = {}
    for ep in episodes:
        if ep.date_is_approximate and _is_year_marker(ep.published_at):
            by_year.setdefault(ep.published_at.year, []).append(ep)
    for year, group in by_year.items():
        start = datetime(year, 1, 1, 12)
        span = timedelta(days=364)
        n = len(group)
        for j, ep in enumerate(group):
            ep.published_at = start + span * ((j + 1) / (n + 1))
            ep.date_is_approximate = True
            updated += 1

    # Pass B: no date at all → between dated anchors.
    def _has_date(ep: Episode) -> bool:
        return ep.published_at is not None

    n = len(episodes)
    i = 0
    while i < n:
        if _has_date(episodes[i]):
            i += 1
            continue
        run_start = i
        while i < n and not _has_date(episodes[i]):
            i += 1
        run_end = i
        left_idx, right_idx = run_start - 1, run_end
        if left_idx < 0 or right_idx >= n:
            continue
        d_left, d_right = episodes[left_idx].published_at, episodes[right_idx].published_at
        if d_right <= d_left:
            continue
        span = d_right - d_left
        count = run_end - run_start
        for j, ep in enumerate(episodes[run_start:run_end]):
            ep.published_at = d_left + span * ((j + 1) / (count + 1))
            ep.date_is_approximate = True
            updated += 1

    if updated:
        db.commit()
    return updated


# ---------------------------------------------------------------------------
# Scan (shared by preview and the legacy one-shot import)
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    files: list[FileMeta]
    existing: list
    cands: list[_Cand]
    candidate_eps: list           # episodes without a file (matchable)
    registered: dict[int, Episode]  # file idx → episode already owning that file
    assignment: dict              # file idx → (cand idx, score, method, reasons)
    scored: dict                  # file idx → [(cand idx, score, method, reasons)]
    dup_of: dict[int, int]
    alignment: dict
    scheme: dict
    folder_analysis: dict
    detected_patterns: list


def _scan_directory(feed: Feed, directory: str, db: Session,
                    filename_format: Optional[str], progress: Optional[dict] = None) -> ScanResult:
    fmt_re = _compile_filename_format(filename_format) if filename_format else None
    audio_files = _collect_audio_files(directory)
    total = len(audio_files)
    if progress is not None:
        progress.update({"phase": "scanning", "current": 0, "total": total,
                         "message": f"Found {total} file{'' if total == 1 else 's'} — reading metadata…"})

    folder_analysis = _detect_folder_type(directory, audio_files)

    # Step 2: raw reads
    raws: list[FileMeta] = []
    for p in audio_files:
        if progress is not None:
            progress["current"] += 1
            progress["message"] = os.path.basename(p)
        raws.append(_read_raw_file(p, directory, fmt_re))

    # Step 3–4
    scheme = _infer_number_scheme(raws)
    files = [_finalize_meta(fm, scheme, feed.title) for fm in raws]
    detected_patterns = _detect_filename_pattern([fm.stem for fm in files]) if not fmt_re else []

    if progress is not None:
        progress["message"] = "Matching files to episodes…"

    primary_id = feed.primary_feed_id or feed.id
    all_feed_ids = get_group_feed_ids(db, primary_id)
    existing = (
        db.query(Episode)
        .filter(Episode.feed_id.in_(all_feed_ids), Episode.hidden.is_(False))
        .all()
    )
    ep_has_file = {ep.id: bool(ep.file_path and os.path.exists(ep.file_path)) for ep in existing}
    registered_paths = {os.path.normpath(ep.file_path): ep for ep in existing if ep_has_file[ep.id]}
    registered_names = {os.path.basename(ep.file_path): ep for ep in existing if ep_has_file[ep.id]}
    candidate_eps = [ep for ep in existing if not ep_has_file[ep.id]]
    cands = _make_cands(candidate_eps, feed.title)
    cand_pos = {c.ep.id: i for i, c in enumerate(cands)}
    guid_idx = {c.ep.guid: i for i, c in enumerate(cands) if c.ep.guid}
    url_idx = {c.ep.enclosure_url: i for i, c in enumerate(cands) if c.ep.enclosure_url}
    stale_name_idx = {os.path.basename(c.ep.file_path): i for i, c in enumerate(cands) if c.ep.file_path}

    try:
        fs = _get_feed_filename_settings(feed, db)
    except Exception:
        fs = None
    expected_name_idx: dict[str, int] = {}
    if fs is not None:
        for i, c in enumerate(cands):
            try:
                expected_name_idx.setdefault(_build_expected_basename(c.ep, fs, ""), i)
            except Exception:
                pass

    # Episodes that already own a file, by the GUID an earlier import gave
    # them: a rescan of the original archive must not re-import copies.
    guid_registered = {ep.guid: ep for ep in existing
                       if ep_has_file[ep.id] and ep.guid and ep.guid.startswith("import:")}

    # Files linked to RSS episodes keep the RSS GUID and were renamed on copy,
    # so recognise those by content instead: same byte size (copy2 preserves
    # it) and the same middle-of-file hash.  Only same-size files are hashed.
    size_idx: dict[int, list[Episode]] = {}
    for ep in existing:
        if ep_has_file[ep.id]:
            try:
                sz = ep.file_size or os.path.getsize(ep.file_path)
            except OSError:
                continue
            size_idx.setdefault(int(sz), []).append(ep)

    registered: dict[int, Episode] = {}
    definite: dict[int, tuple[int, str]] = {}
    claimed: set[int] = set()
    def _same_size(ep: Episode) -> bool:
        try:
            return int(ep.file_size or os.path.getsize(ep.file_path)) == fm.size
        except OSError:
            return False

    for fi, fm in enumerate(files):
        own = registered_paths.get(os.path.normpath(fm.path))
        if own is None:
            # Same name elsewhere (an archive copy of a download) counts only
            # when the bytes agree too; two different files can share a name.
            by_name = registered_names.get(fm.filename)
            if by_name is not None and _same_size(by_name):
                own = by_name
        if own is None and fm.size and (guid_registered or fm.size in size_idx):
            cg = _content_guid(fm.path)
            own = guid_registered.get(cg)
            if own is None:
                for ep in size_idx.get(fm.size, []):
                    if _content_guid(ep.file_path) == cg:
                        own = ep
                        break
        if own is not None:
            registered[fi] = own
            continue
        # Our own sidecar → definitive
        sg, su = fm.sidecar.get("guid"), fm.sidecar.get("enclosure_url")
        ci = None
        method = ""
        if sg and sg in guid_idx and guid_idx[sg] not in claimed:
            ci, method = guid_idx[sg], "guid"
        elif su and su in url_idx and url_idx[su] not in claimed:
            ci, method = url_idx[su], "url"
        else:
            # Content GUID from an earlier import of the same audio
            cg = _content_guid(fm.path) if fm.size else None
            if cg and cg in guid_idx and guid_idx[cg] not in claimed:
                ci, method = guid_idx[cg], "guid"
            else:
                stem_ext = fm.filename
                j = stale_name_idx.get(stem_ext)
                if j is None:
                    # expected names ignore the extension the file actually has
                    base_no_ext = os.path.splitext(stem_ext)[0]
                    for name, k in expected_name_idx.items():
                        if os.path.splitext(name)[0] == base_no_ext:
                            j = k
                            break
                if j is not None and j not in claimed:
                    ci, method = j, "filename_exact"
        if ci is not None:
            definite[fi] = (ci, method)
            claimed.add(ci)

    assignment, scored = _assign_globally(files, cands, definite)

    alignment = _numbering_alignment(files, existing, assignment, cands, skip=set(registered))

    new_idx = [fi for fi in range(len(files)) if fi not in registered and fi not in assignment]
    dup_of = _find_duplicates(files, new_idx)

    return ScanResult(files=files, existing=existing, cands=cands, candidate_eps=candidate_eps,
                      registered=registered, assignment=assignment, scored=scored, dup_of=dup_of,
                      alignment=alignment, scheme=scheme, folder_analysis=folder_analysis,
                      detected_patterns=detected_patterns)


# ---------------------------------------------------------------------------
# Preview (dry-run — no DB writes)
# ---------------------------------------------------------------------------

def preview_import_directory(feed_id: int, directory: str, db: Session,
                             filename_format: Optional[str] = None) -> dict:
    """Scan *directory* and return match results without committing anything.

    Per file: title/date/number/season/duration with their sources, the chosen
    match (with tier, confidence, method, reasons), up to three alternatives,
    an ``ambiguous`` flag when the runner-up is close, ``needs_decision`` when
    the suggestion is too weak to pre-select, and ``duplicate_of`` when the
    file is a copy of another file in the same batch.
    """
    _preview_jobs[feed_id] = {"phase": "counting", "current": 0, "total": 0,
                              "message": "Counting files…"}
    feed = db.query(Feed).filter(Feed.id == feed_id).first()
    if not feed:
        _preview_jobs.pop(feed_id, None)
        return {"error": "Feed not found", "files": [], "total_files": 0, "matched": 0, "unmatched": 0, "registered": 0}

    try:
        scan = _scan_directory(feed, directory, db, filename_format, progress=_preview_jobs[feed_id])
    finally:
        _preview_jobs.pop(feed_id, None)

    files, cands = scan.files, scan.cands
    taken_by: dict[int, str] = {ci: files[fi].filename for fi, (ci, *_r) in scan.assignment.items()}
    precedes = scan.alignment.get("kind") == "precedes"

    def _ep_brief(c: _Cand) -> dict:
        ep = c.ep
        return {"episode_id": ep.id, "episode_title": ep.title,
                "episode_number": ep.episode_number, "seq_number": ep.seq_number,
                "date": ep.published_at.strftime("%Y-%m-%d") if ep.published_at else None}

    out_files = []
    tier_counts: dict[str, int] = {}
    for fi, fm in enumerate(files):
        base = {
            "path": fm.path,
            "filename": fm.filename,
            "title": fm.title,
            "date": fm.date.strftime("%Y-%m-%d") if fm.date else None,
            "date_is_approximate": fm.date_is_approximate,
            "date_precision": fm.date_precision,
            "episode_number": fm.episode_number,
            "number_trusted": fm.number_trusted,
            "season_number": fm.season_number,
            "duration": fm.duration,
            "size": fm.size,
            "metadata_sources": fm.sources,
            "already_registered": False,
            "match": None,
            "suggestion": None,
            "alternatives": [],
            "tier": "none",
            "ambiguous": False,
            "needs_decision": False,
            "duplicate_of": None,
        }
        if fi in scan.registered:
            ep = scan.registered[fi]
            base.update(already_registered=True, tier="certain",
                        match={"episode_id": ep.id, "episode_title": ep.title,
                               "confidence": 1.0, "method": "registered", "reasons": ["already linked"]})
            tier_counts["registered"] = tier_counts.get("registered", 0) + 1
            out_files.append(base)
            continue

        chosen = scan.assignment.get(fi)
        rows = scan.scored.get(fi, [])
        ambiguous = False
        if chosen:
            ci, sc, method, reasons = chosen
            runner = next((r for r in rows if r[0] != ci), None)
            if runner and method not in ("guid", "url", "filename_exact") and sc < 0.80 and (sc - runner[1]) < 0.10:
                ambiguous = True
            tier = _tier_for(sc, method, ambiguous, reasons)
            m = {**_ep_brief(cands[ci]), "confidence": round(sc, 2), "method": method, "reasons": reasons}
            if tier in ("certain", "strong", "likely"):
                base["match"] = m
            else:
                base["suggestion"] = m
                base["needs_decision"] = not precedes
            base["tier"] = tier
            base["ambiguous"] = ambiguous
            alts = [r for r in rows if r[0] != ci][:3]
        else:
            top = rows[0] if rows else None
            if top and top[1] >= 0.15:
                base["suggestion"] = {**_ep_brief(cands[top[0]]), "confidence": round(top[1], 2),
                                      "method": top[2], "reasons": top[3]}
                base["needs_decision"] = top[1] >= 0.30 and not precedes
                base["tier"] = _tier_for(top[1], top[2], False, top[3])
                alts = rows[1:4]
            else:
                alts = []
        base["alternatives"] = [
            {**_ep_brief(cands[ci]), "confidence": round(sc, 2), "method": m, "reasons": rs,
             "claimed_by": taken_by.get(ci)}
            for ci, sc, m, rs in alts
        ]
        if fi in scan.dup_of:
            base["duplicate_of"] = files[scan.dup_of[fi]].path
            base["tier"] = "duplicate"
        tier_counts[base["tier"]] = tier_counts.get(base["tier"], 0) + 1
        out_files.append(base)

    n_registered = sum(1 for f in out_files if f["already_registered"])
    n_matched = sum(1 for f in out_files if not f["already_registered"] and f["match"])
    n_unmatched = sum(1 for f in out_files if not f["already_registered"] and not f["match"] and not f["duplicate_of"])
    n_duplicates = sum(1 for f in out_files if f["duplicate_of"])
    n_decisions = sum(1 for f in out_files if f["needs_decision"] or f["ambiguous"])

    has_existing = len(scan.existing) > 0
    may_renumber = False
    if has_existing and n_unmatched > 0:
        existing_nums = {ep.episode_number for ep in scan.existing if ep.episode_number is not None}
        dates = [ep.published_at for ep in scan.existing if ep.published_at is not None]
        oldest = min(dates) if dates else None
        for f, fm in zip(out_files, files):
            if f["already_registered"] or f["match"] or f["duplicate_of"]:
                continue
            if fm.episode_number is not None and fm.number_trusted and fm.episode_number not in existing_nums:
                may_renumber = True
                break
            if fm.date and oldest and fm.date < oldest:
                may_renumber = True
                break

    return {
        "folder_analysis": scan.folder_analysis,
        "detected_patterns": scan.detected_patterns,
        "number_scheme": scan.scheme,
        "alignment": scan.alignment,
        "files": out_files,
        "total_files": len(files),
        "matched": n_matched,
        "unmatched": n_unmatched,
        "registered": n_registered,
        "duplicates": n_duplicates,
        "needs_decision": n_decisions,
        "tier_counts": tier_counts,
        "has_existing_episodes": has_existing,
        "may_renumber": may_renumber,
    }


# ---------------------------------------------------------------------------
# Commit helpers
# ---------------------------------------------------------------------------

def _apply_overrides(ep: Episode, overrides: dict) -> bool:
    """Write the fields the user explicitly edited.  Returns True when the
    user supplied an episode number (which pins the sequence number)."""
    if not overrides:
        return False
    if overrides.get("title"):
        ep.title = str(overrides["title"]).strip()
    if overrides.get("date"):
        d = _parse_date(str(overrides["date"]))
        if d:
            ep.published_at = d
            ep.date_is_approximate = bool(overrides.get("date_is_approximate", False))
    if overrides.get("season_number") is not None:
        try:
            ep.season_number = int(overrides["season_number"])
        except (ValueError, TypeError):
            pass
    if overrides.get("episode_number") is not None:
        try:
            ep.episode_number = int(overrides["episode_number"])
            return True
        except (ValueError, TypeError):
            pass
    return False


def _new_episode_from_meta(feed_id: int, fm: FileMeta, guid: str) -> Episode:
    ep = Episode(
        feed_id             = feed_id,
        title               = fm.title,
        guid                = guid,
        published_at        = fm.date,
        date_is_approximate = fm.date_is_approximate,
        duration            = fm.duration,
        # Only a number we believe goes into the feed's own numbering (and
        # from there into itunes:episode).  An untrusted one is dropped.
        episode_number      = fm.episode_number if fm.number_trusted else None,
        season_number       = fm.season_number,
        enclosure_url       = fm.sidecar.get("enclosure_url"),
        enclosure_type      = fm.sidecar.get("enclosure_type"),
        author              = fm.sidecar.get("author") or fm.tags.get("artist"),
        description         = fm.sidecar.get("description") or fm.tags.get("description"),
        link                = fm.sidecar.get("link"),
        episode_image_url   = fm.sidecar.get("image_url"),
    )
    return ep


def _finish_numbering(feed_id: int, primary_id: int, db: Session) -> None:
    """Dates first, then numbers — numbering depends on order, and order
    depends on the dates interpolation fills in."""
    from app.routers.episodes import recalc_seq_numbers
    try:
        n = _interpolate_missing_dates(feed_id, db)
        if n:
            log.info("Interpolated approximate dates for %d episode(s) in feed %d", n, feed_id)
    except Exception as e:
        log.warning("Date interpolation failed for feed %d: %s", feed_id, e)
    try:
        recalc_seq_numbers(primary_id, db)
        db.commit()
    except Exception as e:
        log.warning("recalc_seq_numbers failed during import: %s", e)


def _copy_into_place(ep: Episode, audio_path: str, feed: Feed, db: Session,
                     fs: Optional[dict], fallback_in_place: bool) -> str:
    """Pass 2 for one file.  Returns the final path the episode should point at.
    Raises on copy failure unless *fallback_in_place* (legacy import) is set."""
    target = _build_target_path(ep, feed, audio_path, db, fs=fs)
    if os.path.normpath(target) == os.path.normpath(audio_path):
        return audio_path
    try:
        if not os.path.exists(target):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(audio_path, target)
            xml_src = audio_path + ".xml"
            if os.path.exists(xml_src):
                try:
                    shutil.copy2(xml_src, target + ".xml")
                except OSError:
                    pass
        return target
    except Exception as cp_err:
        if fallback_in_place:
            log.warning("Copy to managed folder failed for %s: %s (linking in-place)", audio_path, cp_err)
            return audio_path
        raise


def _mark_downloaded(ep: Episode, final_path: str) -> None:
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


def _rename_needed(all_feed_ids: list, db: Session) -> int:
    return (
        db.query(Episode)
        .filter(
            Episode.feed_id.in_(all_feed_ids),
            Episode.filename_outdated.is_(True),
            Episode.file_path.isnot(None),
        )
        .count()
    )


# ---------------------------------------------------------------------------
# Staged import (commit phase)
# ---------------------------------------------------------------------------

def import_staged(feed_id: int, items: list, db: Session,
                  filename_format: Optional[str] = None) -> dict:
    """Execute an import using explicit file→episode mappings from the review UI.

    Each *item* dict has:
      path           — audio file path
      episode_id     — existing episode to link (None → create new)
      skip           — if True, skip this file entirely
      overrides      — {title, date, date_is_approximate, episode_number,
                        season_number}: ONLY fields the user edited.  These are
                        the only way file-derived values ever replace what the
                        RSS feed said about an existing episode, and a
                        user-entered episode_number is the only thing that
                        pins a sequence number.
    The legacy top-level title/date/episode_number fields are ignored for
    existing episodes and only used as hints when creating a new one.
    """
    to_process = [item for item in items if not item.get("skip", False)]
    fmt_re = _compile_filename_format(filename_format) if filename_format else None

    job = {
        "status": "running", "total": len(to_process), "processed": 0,
        "matched": 0, "created": 0, "renamed": 0, "errors": 0,
        "file_errors": [], "message": "Starting import…",
    }
    _import_jobs[feed_id] = job

    def _fail(path: str, stage: str, msg: str) -> None:
        job["errors"] += 1
        if len(job["file_errors"]) < 200:
            job["file_errors"].append({"path": path, "stage": stage, "message": msg})

    feed = db.query(Feed).filter(Feed.id == feed_id).first()
    if not feed:
        _import_jobs[feed_id] = {"status": "error", "message": "Feed not found", "file_errors": []}
        return _import_jobs[feed_id]

    primary_id = feed.primary_feed_id or feed.id
    all_feed_ids = get_group_feed_ids(db, primary_id)

    # The number scheme is judged across the whole batch, exactly as the
    # preview did, so commit and preview agree on which numbers to believe.
    from app.utils import assert_safe_path
    safe_paths: dict[str, str] = {}
    for item in to_process:
        try:
            p = assert_safe_path(item["path"])
        except ValueError as e:
            _fail(item["path"], "validate", str(e))
            continue
        if os.path.splitext(p)[1].lower() not in AUDIO_EXTENSIONS:
            _fail(p, "validate", "not an audio file")
            continue
        if not os.path.exists(p):
            _fail(p, "validate", "file not found")
            continue
        safe_paths[item["path"]] = p
    raws = {orig: _read_raw_file(p, None, fmt_re) for orig, p in safe_paths.items()}
    scheme = _infer_number_scheme(list(raws.values()))
    metas = {orig: _finalize_meta(fm, scheme, feed.title) for orig, fm in raws.items()}

    matched = created = 0
    pending: list[tuple] = []   # (episode, audio_path, created?)

    # ── Pass 1: link or create (no file copies yet) ──
    for item in to_process:
        if item["path"] not in metas:
            continue
        fm = metas[item["path"]]
        audio_path = fm.path
        episode_id = item.get("episode_id")
        overrides = item.get("overrides") or {}
        try:
            if episode_id:
                ep = db.query(Episode).filter(
                    Episode.id == episode_id,
                    Episode.feed_id.in_(all_feed_ids),
                ).first()
                if not ep:
                    _fail(audio_path, "link", f"episode {episode_id} not found in this feed")
                    continue
                pinned = _apply_overrides(ep, overrides)
                if pinned:
                    ep.seq_number, ep.seq_number_locked = ep.episode_number, True
                # Fill blanks the feed never had, never replace what it did.
                if not ep.duration and fm.duration:
                    ep.duration = fm.duration
                if ep.published_at is None and fm.date:
                    ep.published_at, ep.date_is_approximate = fm.date, fm.date_is_approximate
                matched += 1
                was_created = False
            else:
                # Legacy hint fields from older clients only apply to new episodes.
                if not overrides:
                    hints = {k: item.get(k) for k in ("title", "date", "episode_number", "season_number")
                             if item.get(k) is not None}
                    # A hint identical to what we parsed is not a user edit.
                    if hints.get("title") == fm.title:
                        hints.pop("title", None)
                    if hints.get("date") == (fm.date.strftime("%Y-%m-%d") if fm.date else None):
                        hints.pop("date", None)
                    if hints.get("episode_number") == fm.episode_number:
                        hints.pop("episode_number", None)
                    if hints.get("season_number") == fm.season_number:
                        hints.pop("season_number", None)
                    overrides = hints
                guid = fm.sidecar.get("guid") or _content_guid(audio_path)
                dup = db.query(Episode).filter(Episode.feed_id.in_(all_feed_ids), Episode.guid == guid).first()
                if dup is not None and not (dup.file_path and os.path.exists(dup.file_path)):
                    ep = dup                      # re-import of a file we already know
                    was_created = False
                    matched += 1
                elif dup is not None:
                    _fail(audio_path, "create", f"already imported as episode {dup.id}")
                    continue
                else:
                    ep = _new_episode_from_meta(feed_id, fm, guid)
                    was_created = True
                pinned = _apply_overrides(ep, overrides)
                if pinned:
                    ep.seq_number, ep.seq_number_locked = ep.episode_number, True
                if was_created:
                    db.add(ep)
                    db.flush()
                    created += 1
            pending.append((ep, audio_path, was_created))
        except Exception as e:
            log.error("Staged import error (pass 1) for %s: %s", audio_path, e)
            _fail(audio_path, "link", str(e))
            try:
                db.rollback()
            except Exception:
                pass

    db.commit()
    job["message"] = "Working out dates and numbers…"
    _finish_numbering(feed_id, primary_id, db)

    # ── Pass 2: copy files with correct names ──
    try:
        fs = _get_feed_filename_settings(feed, db)
    except Exception:
        fs = None
    for ep, audio_path, was_created in pending:
        job["processed"] += 1
        job["message"] = os.path.basename(audio_path)
        try:
            final_path = _copy_into_place(ep, audio_path, feed, db, fs, fallback_in_place=False)
            _mark_downloaded(ep, final_path)
            db.commit()
        except Exception as e:
            log.error("Staged import error (pass 2) for %s: %s", audio_path, e)
            _fail(audio_path, "copy", str(e))
            try:
                db.rollback()
                if was_created:
                    # Do not leave a fileless episode behind.
                    db.query(Episode).filter(Episode.id == ep.id).delete()
                    db.commit()
                    created -= 1
            except Exception:
                pass

    if any(c for _e, _p, c in pending):
        # Numbers may shift once a pass-2 failure removed an episode.
        _finish_numbering(feed_id, primary_id, db)

    errors = job["errors"]
    summary = {
        "status":    "done",
        "total":     len(to_process),
        "processed": len(to_process),
        "matched":   matched,
        "created":   created,
        "renamed":   0,
        "errors":    errors,
        "file_errors": job["file_errors"],
        "rename_needed": _rename_needed(all_feed_ids, db),
        "message": (
            f"Import complete: {matched} linked to existing episodes, {created} new"
            + (f", {errors} error{'s' if errors != 1 else ''}" if errors else "")
        ),
    }
    _import_jobs[feed_id] = summary
    log.info("Staged import feed %d: %s", feed_id, summary["message"])
    return summary


# ---------------------------------------------------------------------------
# One-shot import (legacy endpoint and the startup scan)
# ---------------------------------------------------------------------------

def import_directory(feed_id: int, directory: str, rename_files: bool,
                     db: Session, overrides: Optional[dict] = None,
                     filename_format: Optional[str] = None) -> dict:
    """Scan and import in one go, accepting only certain/strong/likely matches.
    Weak suggestions create new episodes — this path has no human in the loop,
    so it errs towards not attaching a file to the wrong episode."""
    job = {
        "status": "running", "total": 0, "processed": 0,
        "matched": 0, "created": 0, "renamed": 0, "errors": 0,
        "file_errors": [], "message": "Scanning…",
    }
    _import_jobs[feed_id] = job

    feed = db.query(Feed).filter(Feed.id == feed_id).first()
    if not feed:
        _import_jobs[feed_id] = {"status": "error", "message": "Feed not found", "file_errors": []}
        return _import_jobs[feed_id]

    scan = _scan_directory(feed, directory, db, filename_format)
    if not scan.files:
        result = {"status": "done", "total": 0, "processed": 0,
                  "matched": 0, "created": 0, "renamed": 0, "errors": 0, "file_errors": [],
                  "message": "No audio files found in that directory."}
        _import_jobs[feed_id] = result
        return result

    job["total"] = len(scan.files)
    job["message"] = f"Processing {len(scan.files)} files…"

    primary_id = feed.primary_feed_id or feed.id
    all_feed_ids = get_group_feed_ids(db, primary_id)
    fmt_given = bool(filename_format)

    matched = created = renamed = 0
    pending: list[tuple] = []

    # ── Pass 1 ──
    for fi, fm in enumerate(scan.files):
        try:
            if fi in scan.registered or fi in scan.dup_of:
                job["processed"] += 1
                continue
            chosen = scan.assignment.get(fi)
            ep = None
            if chosen:
                ci, sc, method, _rs = chosen
                if method in ("guid", "url", "filename_exact") or sc >= 0.50:
                    ep = scan.cands[ci].ep
            if ep is not None:
                matched += 1
                if not ep.published_at and fm.date:
                    ep.published_at, ep.date_is_approximate = fm.date, fm.date_is_approximate
                if not ep.duration and fm.duration:
                    ep.duration = fm.duration
                if ep.episode_number is None and fm.episode_number is not None and fm.number_trusted:
                    ep.episode_number = fm.episode_number
                was_created = False
            else:
                guid = fm.sidecar.get("guid") or _content_guid(fm.path)
                dup = db.query(Episode).filter(Episode.feed_id.in_(all_feed_ids), Episode.guid == guid).first()
                if dup is not None:
                    if dup.file_path and os.path.exists(dup.file_path):
                        job["processed"] += 1
                        continue
                    ep, was_created = dup, False
                    matched += 1
                else:
                    ep = _new_episode_from_meta(feed_id, fm, guid)
                    # A user-supplied filename format makes the number authoritative.
                    if fmt_given and fm.episode_number is not None and fm.sources.get("episode_number") == "filename":
                        ep.seq_number, ep.seq_number_locked = fm.episode_number, True
                    db.add(ep)
                    db.flush()
                    created += 1
                    was_created = True
            pending.append((ep, fm.path, was_created))
        except Exception as e:
            log.error("Import error (pass 1) for %s: %s", fm.path, e)
            job["errors"] += 1
            job["file_errors"].append({"path": fm.path, "stage": "link", "message": str(e)})
            try:
                db.rollback()
            except Exception:
                pass

    db.commit()
    _finish_numbering(feed_id, primary_id, db)

    # ── Pass 2 ──
    try:
        fs = _get_feed_filename_settings(feed, db, overrides)
    except Exception:
        fs = None
    for ep, audio_path, _was_created in pending:
        job["processed"] += 1
        try:
            final_path = _copy_into_place(ep, audio_path, feed, db, fs, fallback_in_place=True)
            if final_path != audio_path:
                renamed += 1
            _mark_downloaded(ep, final_path)
            db.commit()
        except Exception as e:
            log.error("Import error (pass 2) for %s: %s", audio_path, e)
            job["errors"] += 1
            job["file_errors"].append({"path": audio_path, "stage": "copy", "message": str(e)})
            try:
                db.rollback()
            except Exception:
                pass

    errors = job["errors"]
    summary = {
        "status":    "done",
        "total":     len(scan.files),
        "processed": len(scan.files),
        "matched":   matched,
        "created":   created,
        "renamed":   renamed,
        "errors":    errors,
        "file_errors": job["file_errors"],
        "rename_needed": _rename_needed(all_feed_ids, db),
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
