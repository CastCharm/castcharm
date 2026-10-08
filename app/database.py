from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings
from app.limits import WORKER_THREADS


# Pool sizing follows from how FastAPI runs this app, not from how many users
# there are. Every sync endpoint runs in anyio's worker threadpool and holds a
# connection for the life of its request, so the number of connections that can
# be wanted at once is WORKER_THREADS plus the handful of background threads —
# download workers and the scheduler — that open their own sessions.
#
# SQLAlchemy's stock pool is 5 + 10 overflow = 15. Under that, a burst of
# concurrent requests ran the pool dry, callers blocked for pool_timeout (30s by
# default) and then 500'd, and a transient spike became a stall long enough that
# the queue never drained.
#
# So the pool is derived from the thread ceiling rather than guessed at: sized
# above it by design, which is what makes exhaustion impossible rather than
# merely unlikely. Excess load queues for a worker thread — where waiting is
# cheap and bounded — instead of queueing for a connection.
_BACKGROUND_DB_USERS = 8

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    pool_size=WORKER_THREADS + _BACKGROUND_DB_USERS,
    max_overflow=8,
    # Short, because with the pool sized above demand, hitting this at all means
    # something is wrong. Failing fast sheds load; a 30s block does not.
    pool_timeout=10,
)

# Enable WAL mode and foreign keys for SQLite
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    # SQLite keeps a page cache per connection, and the pool holds many
    # connections open. At the 2 MB default that is tens of megabytes of cache
    # sitting behind an idle pool — real money on a 512 MB box, and of little use
    # here because WAL already lets readers work without blocking each other.
    # Negative means KiB rather than pages.
    cursor.execute("PRAGMA cache_size=-1024")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _migrate_db()


def _migrate_db():
    """Add columns introduced after initial schema without dropping existing data."""
    migrations = [
        ("global_settings", "auto_download_new",       "BOOLEAN DEFAULT 1"),
        ("global_settings", "filename_episode_number", "BOOLEAN DEFAULT 1"),
        ("feeds", "podcast_group",                     "VARCHAR"),
        ("feeds", "primary_feed_id",                   "INTEGER REFERENCES feeds(id)"),
        ("feeds", "auto_download_new",                 "BOOLEAN"),
        ("feeds", "initial_sync_complete",             "BOOLEAN DEFAULT 0"),
        ("feeds", "filename_episode_number",           "BOOLEAN"),
        ("feeds", "episode_number_start",              "INTEGER DEFAULT 1"),
        ("feeds", "custom_image_url",                  "VARCHAR"),
        ("episodes", "custom_image_url",               "VARCHAR"),
        ("episodes", "hidden",                         "BOOLEAN DEFAULT 0"),
        ("episodes", "seq_number",                     "INTEGER"),
        ("episodes", "seq_number_locked",              "BOOLEAN DEFAULT 0"),
        ("episodes", "filename_outdated",              "BOOLEAN DEFAULT 0"),
        ("episodes", "custom_id3_tags",               "JSON"),
        ("episodes", "id3_tags_outdated",             "BOOLEAN DEFAULT 0"),
        ("global_settings", "log_max_entries",         "INTEGER DEFAULT 500"),
        ("global_settings", "episode_page_size",         "INTEGER DEFAULT 10000"),
        ("global_settings", "keep_latest",              "INTEGER"),
        ("global_settings", "keep_unplayed",            "BOOLEAN DEFAULT 1"),
        ("feeds",           "keep_latest",              "INTEGER"),
        ("feeds",           "keep_unplayed",            "BOOLEAN DEFAULT 1"),
        ("global_settings", "auto_played_threshold",     "INTEGER DEFAULT 95"),
        ("episodes",        "played",                   "BOOLEAN DEFAULT 0"),
        ("episodes",        "play_position_seconds",    "INTEGER DEFAULT 0"),
        ("episodes",        "last_played_at",           "DATETIME"),
        ("feeds",           "download_all_on_first_sync", "BOOLEAN DEFAULT 0"),
        ("episodes",        "date_is_approximate",        "BOOLEAN DEFAULT 0"),
        ("global_settings", "theme",                      "VARCHAR DEFAULT 'midnight'"),
        ("global_settings", "show_suggested_listening",   "BOOLEAN DEFAULT 1"),
        # Existing installs are already set up — default 1 so wizard doesn't appear for them.
        # Fresh installs get setup_complete=False from the model default (column created by
        # create_all before this migration runs, so ALTER TABLE is a no-op on fresh DBs).
        ("global_settings", "setup_complete",              "BOOLEAN DEFAULT 1"),
        ("global_settings", "auth_enabled",                "BOOLEAN DEFAULT 0"),
        ("global_settings", "auth_username",               "VARCHAR"),
        ("global_settings", "auth_password_hash",          "VARCHAR"),
        ("episodes",        "queued_at",                   "DATETIME"),
        ("global_settings", "timezone",                    "VARCHAR DEFAULT 'UTC'"),
        ("global_settings", "scheduled_xml_enabled",        "BOOLEAN DEFAULT 1"),
        ("global_settings", "scheduled_xml_time",           "VARCHAR DEFAULT '00:00'"),
        ("global_settings", "scheduled_opml_enabled",       "BOOLEAN DEFAULT 1"),
        ("global_settings", "scheduled_opml_time",          "VARCHAR DEFAULT '00:00'"),
        ("global_settings", "scheduled_sync_enabled",       "BOOLEAN DEFAULT 0"),
        ("global_settings", "scheduled_sync_time",          "VARCHAR DEFAULT '03:00'"),
        ("global_settings", "download_window_enabled",      "BOOLEAN DEFAULT 0"),
        ("global_settings", "download_window_start",        "VARCHAR DEFAULT '21:00'"),
        ("global_settings", "download_window_end",          "VARCHAR DEFAULT '06:00'"),
        ("global_settings", "autoclean_enabled",             "BOOLEAN DEFAULT 0"),
        ("global_settings", "autoclean_mode",               "VARCHAR DEFAULT 'recent'"),
        ("global_settings", "autoclean_time",               "VARCHAR DEFAULT '02:00'"),
        ("feeds",           "autoclean_enabled",             "BOOLEAN DEFAULT 0"),
        ("feeds",           "autoclean_mode",                "VARCHAR"),
        ("feeds",           "autoclean_exclude",             "BOOLEAN DEFAULT 0"),
        ("global_settings", "sync_lookback_limit",           "INTEGER DEFAULT 50"),
        ("episodes",        "imported",                      "BOOLEAN DEFAULT 0"),
        ("playlists",       "description",                   "TEXT"),
        # On by default. Safe on upgrade because an existing install has no API
        # keys, and the gate is "enabled AND valid key" — so this alone opens
        # nothing. Being on is what lets a native client enrol itself later.
        ("global_settings", "api_enabled",                   "BOOLEAN DEFAULT 1"),
    ]
    new_tables = [
        """CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR NOT NULL,
            description TEXT,
            type VARCHAR NOT NULL,
            feed_id INTEGER REFERENCES feeds(id) ON DELETE CASCADE,
            filter VARCHAR DEFAULT 'unplayed',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS playlist_episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
            episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR NOT NULL,
            key_hash VARCHAR NOT NULL UNIQUE,
            key_prefix VARCHAR NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_used_at DATETIME
        )""",
        """CREATE TABLE IF NOT EXISTS player_state (
            id INTEGER PRIMARY KEY,
            current_episode_id INTEGER REFERENCES episodes(id) ON DELETE SET NULL,
            context_type VARCHAR,
            context_id INTEGER,
            context_filter VARCHAR,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    with engine.connect() as conn:
        for stmt in new_tables:
            conn.execute(text(stmt))
        conn.commit()

        for table, column, col_def in migrations:
            existing = {
                row[1]
                for row in conn.execute(text(f"PRAGMA table_info({table})"))
            }
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"))
                conn.commit()

        # Indexes introduced after initial schema — IF NOT EXISTS makes these idempotent.
        #
        # episodes.feed_id had none. It is a ForeignKey, and neither SQLAlchemy nor
        # SQLite indexes those automatically, so every per-feed query was a full
        # scan of the episodes table: opening a podcast, listing its episodes,
        # counting them for the feed cards, pruning, renumbering. On a library of
        # any size that is the single most expensive thing the app does, and the
        # episode-index endpoint made it hotter by running on every feed open.
        #
        # All five columns are here so the index COVERS the query: feed_id for the
        # equality, published_at and id for the ordering, and hidden and status
        # because they are filtered too. Leaving those last two out looks like it
        # should still help, and does not — measured against real data, SQLite
        # declined to use a (feed_id, published_at, id) index at all and scanned
        # instead, because it would have had to visit all 1,609 rows anyway to
        # test hidden and status. Covering removes the table lookups entirely:
        # 2.2 ms -> 0.26 ms on a 1,609-episode feed, and it holds without ANALYZE.
        #
        # Column ORDER is what makes it work for both callers. published_at and id
        # sit immediately after feed_id so the ORDER BY is satisfied whether or not
        # hidden is being filtered — the web UI passes include_hidden=true, and
        # putting hidden second instead cost that path a sort (17 ms vs 6.8 ms on a
        # 10,000-row page).
        #
        # played is last so that /episode-index?filter=unplayed stays covering too.
        #
        # Nothing else is added. The per-feed aggregate behind the feed cards is
        # deliberately NOT covered: doing so would need enclosure_url,
        # play_position_seconds, filename_outdated, id3_tags_outdated and
        # download_date as well — roughly a second copy of the table — to take it
        # from 1.1 ms to 0.4 ms on real storage. It uses this index for the feed_id
        # seek and reads the rows it needs, which is the right trade.
        #
        # Every column here was checked against the queries that actually run.
        # file_size was in an earlier version of this index and has been removed:
        # no feed-scoped query reads it, so it was pure write amplification on
        # every episode inserted during a sync.
        indexes = [
            "CREATE INDEX IF NOT EXISTS ix_episodes_status         ON episodes (status)",
            "CREATE INDEX IF NOT EXISTS ix_episodes_hidden         ON episodes (hidden)",
            "CREATE INDEX IF NOT EXISTS ix_episodes_played         ON episodes (played)",
            # Renamed rather than redefined: CREATE INDEX IF NOT EXISTS will not
            # replace an index that already exists under the same name, so an
            # instance that booted with the earlier three-column version would
            # silently keep it. Dropping the old name is a no-op everywhere else.
            "DROP INDEX IF EXISTS ix_episodes_feed_published",
            "CREATE INDEX IF NOT EXISTS ix_episodes_feed_window ON episodes (feed_id, published_at, id, hidden, status, played)",
            # The Downloads view: "downloaded episodes, most recently fetched
            # first". Covering, so SQLite answers it from the index alone.
            "CREATE INDEX IF NOT EXISTS ix_episodes_status_download ON episodes (status, download_date)",
            # playlist_episodes is joined and filtered on both its foreign keys and
            # ordered by position, and like every FK here it had no index of its
            # own. Small today, but it grows with every episode added to a playlist.
            "CREATE INDEX IF NOT EXISTS ix_playlist_episodes_playlist ON playlist_episodes (playlist_id, position)",
            "CREATE INDEX IF NOT EXISTS ix_playlist_episodes_episode  ON playlist_episodes (episode_id)",
            "CREATE INDEX IF NOT EXISTS ix_feeds_primary_feed_id   ON feeds    (primary_feed_id)",
            "CREATE INDEX IF NOT EXISTS ix_api_keys_key_hash       ON api_keys (key_hash)",
        ]
        for stmt in indexes:
            conn.execute(text(stmt))
        conn.commit()
