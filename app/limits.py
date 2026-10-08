"""Every ceiling the API enforces, in one place.

CastCharm runs on Pi-class hardware, so the memory a response needs has to be
decided by the server, not by whoever wrote the query string. Before these
existed, ?limit=1000000 was a million ORM objects, a million Pydantic models and
a million dicts of JSON, all live at once — one GET was enough to end the
process.

The rule, applied uniformly below: every ceiling sits at or above the largest
request the app itself ever makes, and is derived from a response-size budget of
roughly a few tens of megabytes. Nothing a user can do through the web UI or the
Android app comes near one. They exist so that a caller doing something neither
app does — walking a whole library through the API in a single call — has to
paginate rather than take the server down. Being savvy is the price of the
unusual request; the ordinary ones are untouched.

This module deliberately imports nothing. It is read by the routers, by the
Pydantic schemas and by the settings form, and none of those should have to think
about import order to find out what the limits are.

Client-side counterparts, which must not exceed what is here — a server ceiling
below what a client asks for is a 422, not a shorter list:
  - static/views/settings.js — max= attributes on the settings form
  - EpisodeListViewModel.kt  — MAX_SERVER_PAGE
"""

# Full episode rows in one response. Description-dominated, so a few KB each:
# 10,000 is the top of the budget.
#
# This is also exactly the default episode_page_size, which is what the web feed
# detail page asks for — it loads a whole podcast per request and has done since
# long before this ceiling existed. GlobalSettingsUpdate clamps episode_page_size
# to this same constant, which is what stops the setting and the endpoint ever
# disagreeing: the UI cannot be configured to ask for a page the API will refuse.
MAX_PAGE_SIZE = 10_000

# Ids in a ?ids= query string. Not a policy choice — it is how many fit. At ~7
# bytes per id the request line stays well inside the 8 KB uvicorn accepts, and
# past that the request fails at the HTTP layer with something far less legible
# than a 400.
MAX_IDS_IN_URL = 500

# Ids in one bulk mutation. Bounded by work rather than by size: 500 delete_file
# actions is 500 unlinks plus their sidecars, and a request that large should
# still finish promptly enough to stay interruptible and retryable.
MAX_BULK_IDS = 500

# Bare ids in an episode-index response. Same budget as MAX_PAGE_SIZE, far cheaper
# unit — an id is ~7 bytes against a few KB for a row — so the count can be much
# higher for the same memory. A backstop, not a page size: no real feed approaches
# it, and a client that hits it gets a shortened feed rather than a dead server.
MAX_INDEX_IDS = 50_000

# Longest accepted free-text search term. Longer than this is not a search.
MAX_SEARCH_LEN = 200

# Largest request body accepted, from Content-Length. Set by the biggest genuine
# upload, the 50 MB feed-XML import; every upload handler also reads with its own
# explicit byte cap, so this is the cheap outer check rather than the only one.
MAX_REQUEST_BYTES = 50 * 1024 * 1024

# Concurrent episode downloads. Each one is a dedicated OS thread holding a
# database connection and an HTTP client for as long as it runs, so this is a
# thread count as much as a setting. Enforced on write by GlobalSettingsUpdate and
# again on read in app/downloader.py — a database written before this ceiling
# existed can still hold a larger number, and only the read path can contain that.
MAX_CONCURRENT_DOWNLOADS = 16

# Concurrent sync request handlers, and with it the app's real concurrency limit —
# every non-async route runs in anyio's worker threadpool and holds a database
# connection for its whole duration. app/database.py sizes the connection pool
# just above this so the pool can never be the thing that runs out.
WORKER_THREADS = 24
