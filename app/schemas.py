from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.limits import MAX_CONCURRENT_DOWNLOADS, MAX_PAGE_SIZE


# ---------------------------------------------------------------------------
# Global settings
# ---------------------------------------------------------------------------

class GlobalSettingsBase(BaseModel):
    download_path: str = "/downloads"
    check_interval: int = 60
    filename_date_prefix: bool = True
    filename_episode_number: bool = True
    organize_by_year: bool = True
    save_xml: bool = True
    max_concurrent_downloads: int = 2
    auto_download_new: bool = True
    default_id3_mapping: dict[str, str] = {}
    log_max_entries: int = 1000
    episode_page_size: Optional[int] = 10000
    keep_latest: Optional[int] = None
    keep_unplayed: bool = True
    auto_played_threshold: int = 98
    theme: str = "midnight"
    show_suggested_listening: bool = True
    timezone: str = "UTC"
    scheduled_xml_enabled: bool = True
    scheduled_xml_time: str = "00:00"
    scheduled_opml_enabled: bool = True
    scheduled_opml_time: str = "00:00"
    scheduled_sync_enabled: bool = False
    scheduled_sync_time: str = "03:00"
    download_window_enabled: bool = False
    download_window_start: str = "21:00"
    download_window_end: str = "06:00"
    autoclean_enabled: bool = False
    autoclean_mode: str = "unplayed"
    autoclean_time: str = "02:00"
    sync_lookback_limit: int = 50
    api_enabled: bool = True


class GlobalSettingsUpdate(BaseModel):
    """Settings patch.

    The bounds below are not cosmetic. Several of these numbers are turned
    directly into resources — threads, in-memory buffers, scheduler wakeups — so
    without them a single settings write is enough to make the process
    unrecoverable on any hardware, and trivially so on the small boxes this is
    built for. They are deliberately generous; the point is that a ceiling exists.
    """

    download_path: Optional[str] = Field(default=None, max_length=4096)
    # Minutes between feed checks. Zero or negative would turn the scheduler into
    # a busy loop.
    check_interval: Optional[int] = Field(default=None, ge=1, le=60 * 24 * 366)
    filename_date_prefix: Optional[bool] = None
    filename_episode_number: Optional[bool] = None
    organize_by_year: Optional[bool] = None
    save_xml: Optional[bool] = None
    max_concurrent_downloads: Optional[int] = Field(
        default=None, ge=1, le=MAX_CONCURRENT_DOWNLOADS
    )
    auto_download_new: Optional[bool] = None
    default_id3_mapping: Optional[dict[str, str]] = None
    # Backs an in-memory ring buffer that is held for the life of the process.
    log_max_entries: Optional[int] = Field(default=None, ge=10, le=50_000)
    # How many episodes the web feed-detail page pulls per request. Clamped to the
    # same ceiling the episode endpoints enforce, so this setting can never be
    # configured to ask for a page the API would reject.
    episode_page_size: Optional[int] = Field(default=None, ge=1, le=MAX_PAGE_SIZE)
    keep_latest: Optional[int] = Field(default=None, ge=0, le=100_000)
    keep_unplayed: Optional[bool] = None
    # A percentage of an episode's duration.
    auto_played_threshold: Optional[int] = Field(default=None, ge=0, le=100)
    theme: Optional[str] = Field(default=None, max_length=64)
    show_suggested_listening: Optional[bool] = None
    timezone: Optional[str] = Field(default=None, max_length=64)
    # The time fields are "HH:MM" from an <input type="time">. The cap is loose on
    # purpose — it is here to stop a megabyte being stored, not to validate the
    # format, which the scheduler does. A max_length of exactly 5 would sit right
    # on the boundary of what a browser may legitimately submit.
    scheduled_xml_enabled: Optional[bool] = None
    scheduled_xml_time: Optional[str] = Field(default=None, max_length=16)
    scheduled_opml_enabled: Optional[bool] = None
    scheduled_opml_time: Optional[str] = Field(default=None, max_length=16)
    scheduled_sync_enabled: Optional[bool] = None
    scheduled_sync_time: Optional[str] = Field(default=None, max_length=16)
    download_window_enabled: Optional[bool] = None
    download_window_start: Optional[str] = Field(default=None, max_length=16)
    download_window_end: Optional[str] = Field(default=None, max_length=16)
    autoclean_enabled: Optional[bool] = None
    autoclean_mode: Optional[str] = Field(default=None, max_length=32)
    autoclean_time: Optional[str] = Field(default=None, max_length=16)
    # How many entries back into a feed each sync re-reads. No real feed has this
    # many entries, so the bound is a backstop rather than a restriction.
    sync_lookback_limit: Optional[int] = Field(default=None, ge=0, le=100_000)
    api_enabled: Optional[bool] = None


class GlobalSettingsOut(GlobalSettingsBase):
    id: int

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

class ApiKeyCreate(BaseModel):
    name: str = Field(max_length=200)

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name must not be empty")
        return v


class ApiKeyRename(BaseModel):
    name: str = Field(max_length=200)

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name must not be empty")
        return v


class ApiKeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    created_at: datetime
    last_used_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ApiKeyCreated(ApiKeyOut):
    # Only the create endpoint returns this. The plaintext is never stored, so
    # this is the one and only time it can be read.
    key: str


class ApiKeyPurgeResult(BaseModel):
    revoked: int


# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------

class FeedCreate(BaseModel):
    # Bounded like every other stored string: the cost of an oversized value
    # here outlives the request that carried it.
    url: str = Field(max_length=2048)
    download_all: bool = False
    title_override: Optional[str] = Field(default=None, max_length=500)
    # Set only after the user has been shown the folder-already-exists prompt and
    # has chosen to go ahead. Without it the add is refused with a 409 so the
    # decision is never made on their behalf.
    allow_existing_folder: bool = False

    @field_validator("url")
    @classmethod
    def url_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("URL must not be empty")
        return v


class ManualFeedCreate(BaseModel):
    title: str = Field(max_length=500)
    # See FeedCreate.allow_existing_folder.
    allow_existing_folder: bool = False

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Title must not be empty")
        return v


class FeedUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=500)
    url: Optional[str] = Field(default=None, max_length=2048)
    active: Optional[bool] = None
    download_path: Optional[str] = Field(default=None, max_length=4096)
    # Minutes. Zero or negative would be a busy scheduler loop; the read path in
    # app/scheduler.py already floors it, and this stops it being stored at all.
    check_interval: Optional[int] = Field(default=None, ge=1, le=60 * 24 * 366)
    filename_date_prefix: Optional[bool] = None
    filename_episode_number: Optional[bool] = None
    organize_by_year: Optional[bool] = None
    save_xml: Optional[bool] = None
    id3_enabled: Optional[bool] = None
    id3_field_mapping: Optional[dict[str, str]] = None
    podcast_group: Optional[str] = Field(default=None, max_length=500)
    auto_download_new: Optional[bool] = None
    episode_number_start: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    custom_image_url: Optional[str] = Field(default=None, max_length=2048)
    keep_latest: Optional[int] = Field(default=None, ge=0, le=100_000)
    keep_unplayed: Optional[bool] = None
    autoclean_enabled: Optional[bool] = None
    autoclean_mode: Optional[str] = Field(default=None, max_length=32)
    autoclean_exclude: Optional[bool] = None
    # Not a Feed column. Set only after the user has been shown the
    # folder-already-exists prompt for a podcast_group rename and chosen to proceed;
    # update_feed() pops it before applying the rest of the fields.
    allow_existing_folder: bool = False


class FeedOut(BaseModel):
    id: int
    title: Optional[str]
    url: str
    description: Optional[str]
    image_url: Optional[str]
    website_url: Optional[str]
    author: Optional[str]
    language: Optional[str]
    category: Optional[str]
    download_path: Optional[str]
    check_interval: Optional[int]
    filename_date_prefix: Optional[bool]
    filename_episode_number: Optional[bool]
    organize_by_year: Optional[bool]
    save_xml: Optional[bool]
    id3_enabled: bool
    id3_field_mapping: dict[str, str]
    active: bool
    last_checked: Optional[datetime]
    last_error: Optional[str]
    created_at: datetime
    updated_at: datetime
    podcast_group: Optional[str]
    primary_feed_id: Optional[int]
    auto_download_new: Optional[bool]
    episode_number_start: int = 1
    custom_image_url: Optional[str] = None
    # Computed
    episode_count: int = 0
    downloaded_count: int = 0
    available_count: int = 0           # pending + failed (not yet downloaded)
    unplayed_available_count: int = 0  # pending + failed + not played + not partially played
    skipped_count: int = 0             # duplicates suppressed at sync time
    hidden_count: int = 0              # user-hidden episodes
    unplayed_count: int = 0            # downloaded + not played
    needs_rename: bool = False  # downloaded episodes with outdated filenames
    initial_sync_complete: bool = False
    has_custom_cover: bool = False  # True when a local cover.jpg or custom_image_url is set
    last_download_at: Optional[datetime] = None  # most recent episode download_date across this feed
    keep_latest: Optional[int] = None
    podcast_folder: Optional[str] = None  # effective on-disk folder for this podcast
    keep_unplayed: bool = True
    autoclean_enabled: bool = False
    autoclean_mode: Optional[str] = None
    autoclean_exclude: bool = False

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Episodes
# ---------------------------------------------------------------------------

class EpisodeOut(BaseModel):
    id: int
    feed_id: int
    title: Optional[str]
    guid: str
    enclosure_url: Optional[str]
    enclosure_type: Optional[str]
    enclosure_length: Optional[int]
    published_at: Optional[datetime]
    description: Optional[str]
    duration: Optional[str]
    episode_number: Optional[int]
    season_number: Optional[int]
    episode_image_url: Optional[str]
    author: Optional[str]
    link: Optional[str]
    status: str
    file_size: Optional[int]
    download_progress: int
    download_date: Optional[datetime]
    error_message: Optional[str]
    hidden: bool = False
    seq_number: Optional[int] = None
    seq_number_locked: bool = False
    filename_outdated: bool = False
    custom_id3_tags: Optional[dict] = None
    id3_tags_outdated: bool = False
    imported: bool = False
    custom_image_url: Optional[str] = None
    file_missing: bool = False
    played: bool = False
    play_position_seconds: int = 0
    last_played_at: Optional[datetime] = None
    date_is_approximate: bool = False
    created_at: datetime
    # Feed info for list views
    feed_title: Optional[str] = None
    feed_image_url: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Playlists
# ---------------------------------------------------------------------------

class PlaylistCreate(BaseModel):
    # Bounded because these are stored: without a limit a client can write an
    # arbitrarily large string into the database, where the cost outlives the
    # request. Same reasoning as SetImageBody in app/routers/episodes.py.
    name: str = Field(max_length=500)
    description: Optional[str] = Field(default=None, max_length=5000)
    type: str = Field(default="custom", max_length=32)   # 'feed' | 'custom'
    feed_id: Optional[int] = None
    filter: str = Field(default="unplayed", max_length=32)  # 'all' | 'unplayed'


class PlaylistUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=5000)
    filter: Optional[str] = Field(default=None, max_length=32)


class PlaylistEpisodeOut(BaseModel):
    episode_id: int
    position: int
    added_at: datetime
    episode: "EpisodeOut"

    model_config = {"from_attributes": True}


class PlaylistOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    type: str
    feed_id: Optional[int] = None
    filter: str
    created_at: datetime
    episode_count: int = 0

    model_config = {"from_attributes": True}


class PlaylistEpisodeAdd(BaseModel):
    episode_id: int


class PlaylistReorder(BaseModel):
    # The playlist's full ordering, so the ceiling has to clear the largest real
    # playlist — MAX_BULK_IDS would be wrong here, since this is not a batch of
    # work but a complete list, and truncating it would silently reorder into the
    # wrong shape. MAX_PAGE_SIZE is the same "episode records in one request"
    # budget the list endpoints use.
    #
    # Ids not already in the playlist are ignored by the handler, so an oversized
    # body was never dangerous — just unbounded work to no effect.
    episode_ids: list[int] = Field(max_length=MAX_PAGE_SIZE)


# ---------------------------------------------------------------------------
# Player state
# ---------------------------------------------------------------------------

class PlayerStateOut(BaseModel):
    current_episode_id: Optional[int] = None
    context_type: Optional[str] = None
    context_id: Optional[int] = None
    context_filter: Optional[str] = None
    current_episode: Optional["EpisodeOut"] = None
    queue: list["EpisodeOut"] = []
    queue_position: Optional[int] = None   # index of current episode in queue

    model_config = {"from_attributes": True}


class PlayerPlayRequest(BaseModel):
    context_type: str              # 'feed' | 'playlist'
    context_id: int
    episode_id: Optional[int] = None   # if omitted, smart-start logic applies
    context_filter: str = "unplayed"   # only used when context_type='feed'


# ---------------------------------------------------------------------------
# System status
# ---------------------------------------------------------------------------

class LimitsOut(BaseModel):
    """The ceilings this server enforces, so clients need not assume them.

    Every value mirrors a constant in app/limits.py. A client that reads these
    sizes its requests to whatever the server it is actually talking to will
    accept, rather than to a number compiled into the client months earlier —
    which is how the two drift apart and requests start coming back 422.

    Fields are additive only. A client written against an older shape must keep
    working, so nothing here is ever removed or narrowed in meaning.
    """

    max_page_size: int
    max_ids_in_url: int
    max_bulk_ids: int
    max_index_ids: int
    max_search_len: int
    max_request_bytes: int


class StatusOut(BaseModel):
    scheduler_running: bool
    download_queue_size: int
    active_downloads: int
    podcasts_total: int
    feeds_total: int
    episodes_total: int
    episodes_downloaded: int
    episodes_failed: int = 0
    storage_bytes: int
    version: str = "1.0.0"
    syncing_count: int = 0
    next_sync_at: Optional[datetime] = None
    importing_count: int = 0   # active file-import jobs
    scanning: bool = False     # startup folder scan in progress
    downloading_feed_ids: list[int] = []  # primary feed IDs with queued/active downloads
    syncing_feed_ids: list[int] = []      # feed IDs currently syncing (active + pending)
    xml_regenerating: bool = False        # complete-feed.xml rebuild in progress
    opml_generating: bool = False         # OPML export in progress
    autoclean_running: bool = False       # auto-cleanup job in progress
    download_window_paused: bool = False  # downloads paused (outside window)
    download_window_next_open: Optional[datetime] = None  # next time window opens


# ---------------------------------------------------------------------------
# ID3 field mapping helpers (returned by the API for UI dropdowns)
# ---------------------------------------------------------------------------

class ID3TagInfo(BaseModel):
    tag: str
    label: str


class RSSSourceInfo(BaseModel):
    field: str
    label: str


# ---------------------------------------------------------------------------
# File import
# ---------------------------------------------------------------------------

class ImportFilesRequest(BaseModel):
    directory: str
    rename_files: bool = True
    organize_by_year: Optional[bool] = None
    date_prefix: Optional[bool] = None
    ep_num_prefix: Optional[bool] = None
    save_as_defaults: bool = False


# ---------------------------------------------------------------------------
# Staged import (preview + commit)
# ---------------------------------------------------------------------------

class ImportPreviewRequest(BaseModel):
    directory: str
    filename_format: Optional[str] = None


class ImportStageItem(BaseModel):
    path: str
    episode_id: Optional[int] = None  # None → create new episode
    skip: bool = False
    title: Optional[str] = None        # override detected title
    date: Optional[str] = None         # override date (YYYY-MM-DD)
    episode_number: Optional[int] = None
    season_number: Optional[int] = None


class ImportStageRequest(BaseModel):
    items: list[ImportStageItem]
    filename_format: Optional[str] = None
