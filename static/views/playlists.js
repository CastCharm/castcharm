"use strict";

// ============================================================
// Playlists view
// ============================================================

// Icons: a list for manual playlists, a brain for smart ones.
const _PL_LIST_PATH = `<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/>
    <circle cx="3.5" cy="6" r="1.5" fill="currentColor" stroke="none"/>
    <circle cx="3.5" cy="12" r="1.5" fill="currentColor" stroke="none"/>
    <circle cx="3.5" cy="18" r="1.5" fill="currentColor" stroke="none"/>`;
const _PL_BRAIN_PATH = `<path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z"/>
    <path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z"/>`;
function _plIcon(type, size = 22) {
  const smart = type === "smart";
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="${size}" height="${size}"
               class="${smart ? "pl-icon-smart" : ""}">${smart ? _PL_BRAIN_PATH : _PL_LIST_PATH}</svg>`;
}
const _plKind = (pl) => (pl.type === "smart" ? "Smart" : "Manual");
function _plHours(sec) {
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h ? `${h} h${m ? ` ${m} m` : ""}` : `${m} m`;
}
function _plCountLine(pl) {
  const n = pl.episode_count;
  return `${n} episode${n !== 1 ? "s" : ""}${pl.total_seconds ? ` · ${_plHours(pl.total_seconds)}` : ""}`;
}

// A one-line, human reading of smart rules, e.g. "Unplayed · last 14 days · oldest first".
window._describeRules = function (rules) {
  if (!rules) return "";
  const parts = [];
  const played = { unplayed: "Unplayed", in_progress: "In progress", played: "Played", any: "Any played state" };
  parts.push(played[rules.played || "unplayed"]);
  if (rules.feeds?.ids?.length) parts.push(`${rules.feeds.mode === "exclude" ? "not from" : "from"} ${rules.feeds.ids.length} podcast${rules.feeds.ids.length === 1 ? "" : "s"}`);
  const p = rules.published || {};
  if (p.within_days) parts.push(`last ${p.within_days} day${p.within_days === 1 ? "" : "s"}`);
  if (p.after) parts.push(`after ${p.after}`);
  if (p.before) parts.push(`before ${p.before}`);
  const d = rules.duration || {};
  if (d.min_minutes != null && d.max_minutes != null) parts.push(`${d.min_minutes}–${d.max_minutes} min`);
  else if (d.min_minutes != null) parts.push(`over ${d.min_minutes} min`);
  else if (d.max_minutes != null) parts.push(`under ${d.max_minutes} min`);
  if (rules.title_contains) parts.push(`title has “${rules.title_contains}”`);
  const sorts = { oldest: "oldest first", newest: "newest first", shortest: "shortest first", longest: "longest first" };
  parts.push(sorts[rules.sort || "oldest"]);
  if (rules.limit) parts.push(`first ${rules.limit}`);
  return parts.join(" · ");
};

async function renderPlaylists() {
  const el = document.getElementById("content");
  el.innerHTML = `
    <div class="page">
      <div class="view-header" style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">
        <h1 style="margin:0;font-size:22px;font-weight:700;color:var(--text-1)">Playlists</h1>
        <button class="btn btn-primary btn-sm" id="btn-new-playlist">+ New Playlist</button>
      </div>
      <div id="playlist-list" class="playlist-grid"></div>
    </div>`;

  document.getElementById("btn-new-playlist").addEventListener("click", _showCreateDialog);
  await _loadPlaylists();
}

async function _loadPlaylists() {
  const container = document.getElementById("playlist-list");
  if (!container) return;
  try {
    const playlists = await API.getPlaylists();
    if (!playlists.length) {
      container.innerHTML = `<p class="empty-msg" style="color:var(--text-3);font-size:14px;margin-top:8px">No playlists yet. Create one to get started.</p>`;
      return;
    }
    container.innerHTML = playlists.map(_playlistCard).join("");
    container.querySelectorAll("[data-action='play-playlist']").forEach((btn) => {
      btn.addEventListener("click", () => _playPlaylist(Number(btn.dataset.id)));
    });
    container.querySelectorAll("[data-action='delete-playlist']").forEach((btn) => {
      btn.addEventListener("click", () => _deletePlaylist(Number(btn.dataset.id), btn.dataset.name));
    });
  } catch (e) {
    container.innerHTML = `<p style="color:var(--error)">${escHTML(e.message)}</p>`;
  }
}

function _playlistCard(pl) {
  const smart = pl.type === "smart";
  const meta = smart
    ? `${_plKind(pl)} · ${_plCountLine(pl)}<div class="playlist-card-rules">${escHTML(_describeRules(pl.rules))}</div>`
    : `${_plKind(pl)} · ${_plCountLine(pl)}`;
  return `
    <div class="playlist-card${smart ? " playlist-card--smart" : ""}">
      <div class="playlist-card-icon">${_plIcon(pl.type)}</div>
      <div class="playlist-card-info">
        <div class="playlist-card-name">${escHTML(pl.name)}${pl.is_system ? ` <span class="pl-tag" title="Comes with CastCharm. Edit its rules; it can't be deleted.">Built-in</span>` : ""}</div>
        ${pl.description ? `<div class="playlist-card-desc">${escHTML(pl.description)}</div>` : ""}
        <div class="playlist-card-meta">${meta}</div>
      </div>
      <div class="playlist-card-actions">
        <button class="btn btn-primary btn-sm" data-action="play-playlist" data-id="${pl.id}" title="Play">
          <svg viewBox="0 0 24 24" fill="currentColor" width="12" height="12"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          Play
        </button>
        <a class="btn btn-ghost btn-sm" href="#/playlists/${pl.id}">View</a>
        ${pl.is_system ? "" : `<button class="btn btn-ghost btn-sm" style="color:var(--error)" data-action="delete-playlist"
                data-id="${pl.id}" data-name="${escHTML(pl.name)}">Delete</button>`}
      </div>
    </div>`;
}

// Start a playlist as the player's queue — at a chosen episode, or where the
// server's resume rule says (the one left part-way, else the first unplayed).
// The server owns the queue from here on, so auto-advance just works.
async function _playPlaylist(id, episodeId = null) {
  try {
    const body = { context_type: "playlist", context_id: id, context_filter: "unplayed" };
    if (episodeId) body.episode_id = episodeId;
    const state = await API.playerPlay(body);
    if (state?.current_episode) {
      window.playEpisode(state.current_episode.id, false, { contextSet: true });
    } else {
      Toast.info("Nothing to play in this playlist");
    }
  } catch (e) {
    Toast.error(e.message || "Could not start playback");
  }
}
window._playPlaylist = _playPlaylist;

// viewPlaylistDetail — full-page route handler for /playlists/:id
async function viewPlaylistDetail(playlistId) {
  const id = routeId(playlistId);
  if (id === null) {
    Router.navigate("/playlists");
    return;
  }
  const el = document.getElementById("content");
  el.innerHTML = `<div class="page pl-detail-page"><p style="color:var(--text-3);font-size:13px">Loading...</p></div>`;

  let pl, episodes;
  try {
    [pl, episodes] = await Promise.all([
      API.get(`/api/playlists/${id}`),
      API.getPlaylistEpisodes(id),
    ]);
  } catch (e) {
    el.innerHTML = `<div class="page pl-detail-page"><p style="color:var(--error)">${escHTML(e.message)}</p></div>`;
    return;
  }

  // Set up _epState so updateEpisodeRow and playlist membership tracking work.
  // Deliberately a different shape from the feed-detail one (no feed, no
  // offset/batch) — both views mount an element with id="episode-list" and share
  // episodeRow(), so the model helpers in feed-detail.js tolerate either. Keep
  // them in step if you change one.
  window._epState = {
    feed: null,
    playlistId: id,
    playlistMembers: new Set(episodes.map((ep) => ep.id)),
    rowOpts: { draggable: pl.type === "custom", hideSeqNumber: true },
  };
  _epSetAll(episodes);

  const smart = pl.type === "smart";
  const typeLabel = smart ? `Smart playlist · ${escHTML(_describeRules(pl.rules))}` : "Manual playlist";
  const countLabel = _plCountLine(pl);
  const listIconSvg = _plIcon(pl.type, 36);
  const playSvg = `<svg viewBox="0 0 24 24" fill="currentColor" width="13" height="13"><polygon points="5 3 19 12 5 21 5 3"/></svg>`;

  el.innerHTML = `
    <div class="page pl-detail-page">
      <a class="back-btn" href="#/playlists">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><polyline points="15 18 9 12 15 6"/></svg>
        Back to Playlists
      </a>
      <div class="pl-detail-header">
        <div class="pl-detail-art">${listIconSvg}</div>
        <div class="pl-detail-info">
          <div class="pl-detail-title">${escHTML(pl.name)}${pl.is_system ? ` <span class="pl-tag">Built-in</span>` : ""}</div>
          ${pl.description ? `<div class="pl-detail-desc">${escHTML(pl.description)}</div>` : ""}
          <div class="pl-detail-meta-line" id="pl-count-line">${countLabel} · ${typeLabel}</div>
          <div class="pl-detail-actions">
            <button class="btn btn-primary btn-sm" id="pl-play-btn">${playSvg} Play</button>
            <button class="btn btn-ghost btn-sm" id="pl-edit-btn">${smart ? "Edit rules" : "Edit"}</button>
          </div>
        </div>
      </div>
      <div class="episode-list${smart ? " pl-smart" : ""}" id="episode-list"></div>
    </div>`;

  // Rows come from the model; only those near the viewport are ever built.
  _epRender();

  // All shown episodes are in this playlist — initialize their "+" buttons as primary
  _applyPlaylistMemberStates(window._epState.playlistMembers);

  document.getElementById("pl-play-btn").addEventListener("click", () => _playPlaylist(pl.id));
  document.getElementById("pl-edit-btn").addEventListener("click", () => {
    _showEditDialog(pl, () => Router.navigate(`/playlists/${pl.id}`));
  });

  // A smart playlist is a live query: when an episode's played state changes
  // (marked by hand, or the player finished it), ask the server again and
  // let rows that no longer match slide out — no refresh needed.
  if (smart) {
    window._onEpisodePlayedChanged = async () => {
      try {
        const [fresh, plNow] = await Promise.all([API.getPlaylistEpisodes(pl.id), API.get(`/api/playlists/${pl.id}`)]);
        if (window._epState?.playlistId !== pl.id) return;      // navigated away meanwhile
        const keep = new Set(fresh.map((e) => e.id));
        const gone = (window._epState.eps || []).filter((e) => !keep.has(e.id));
        const line = document.getElementById("pl-count-line");
        if (line) line.innerHTML = `${_plCountLine(plNow)} · Smart playlist · ${escHTML(_describeRules(plNow.rules))}`;
        const commit = () => { if (window._epState?.playlistId === pl.id) { _epSetAll(fresh); _epRender(); } };
        const rows = gone.map((e) => document.getElementById(`ep-${e.id}`)).filter(Boolean);
        if (!rows.length) { commit(); return; }
        let left = rows.length;
        rows.forEach((row) => { window._epState?.vlist?.pin(Number(row.id.replace("ep-", ""))); animateRemove(row, () => { if (--left === 0) commit(); }); });
      } catch (_) { /* leave the list as it is */ }
    };
  }

  // A row's Play starts this playlist at that episode, so the rest follows.
  // The global dispatcher would start it alone (or in its podcast's order).
  // Clicking the episode that is already playing still just pauses it.
  document.getElementById("episode-list").addEventListener("click", (e) => {
    const btn = e.target.closest('[data-action="play-episode"]');
    if (!btn) return;
    const epId = Number(btn.dataset.epId);
    if (Player.currentId() === epId) return;
    e.stopPropagation();
    e.preventDefault();
    _playPlaylist(pl.id, epId);
  });

  // Drag-to-reorder (custom playlists only)
  if (pl.type === "custom") {
    const list = document.getElementById("episode-list");
    // A key, not a node. The dragged row can be discarded and rebuilt while the
    // user scrolls toward the drop point, so holding the element would leave us
    // pointing at something detached from the page.
    let dragKey = null;
    const _epIdOf = (row) => (row ? Number(row.id.replace("ep-", "")) : null);
    const _clearMarkers = () => list.querySelectorAll(".drag-over-top, .drag-over-bottom")
      .forEach((r) => r.classList.remove("drag-over-top", "drag-over-bottom"));

    list.addEventListener("dragstart", (e) => {
      const row = e.target.closest(".episode-item");
      if (!row) return;
      dragKey = _epIdOf(row);
      e.dataTransfer.effectAllowed = "move";
      row.classList.add("dragging");
      window._epState?.vlist?.pin(dragKey);
    });

    list.addEventListener("dragend", () => {
      if (dragKey != null) {
        window._epState?.vlist?.unpin(dragKey);
        window._epState?.vlist?.node(dragKey)?.classList.remove("dragging");
      }
      _clearMarkers();
      _stopEdgeScroll();
      dragKey = null;
    });

    // Without this, dragging to a position outside the current window is
    // impossible — the list no longer scrolls itself when the pointer reaches
    // the edge, because the rows beyond it do not exist to drag onto.
    let _edgeTimer = null;
    function _stopEdgeScroll() {
      if (_edgeTimer) { cancelAnimationFrame(_edgeTimer); _edgeTimer = null; }
    }
    function _edgeScroll(clientY) {
      const scroller = document.getElementById("content");
      if (!scroller) return;
      const { top, bottom } = scroller.getBoundingClientRect();
      const zone = 60;
      let dy = 0;
      if (clientY < top + zone) dy = -Math.ceil((top + zone - clientY) / 4);
      else if (clientY > bottom - zone) dy = Math.ceil((clientY - (bottom - zone)) / 4);
      _stopEdgeScroll();
      if (!dy) return;
      const step = () => { scroller.scrollTop += dy; _edgeTimer = requestAnimationFrame(step); };
      _edgeTimer = requestAnimationFrame(step);
    }

    list.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      const target = e.target.closest(".episode-item");
      _clearMarkers();
      if (target && _epIdOf(target) !== dragKey) {
        const { top, height } = target.getBoundingClientRect();
        target.classList.add(e.clientY < top + height / 2 ? "drag-over-top" : "drag-over-bottom");
      }
      _edgeScroll(e.clientY);
    });

    list.addEventListener("drop", async (e) => {
      e.preventDefault();
      _stopEdgeScroll();
      const target = e.target.closest(".episode-item");
      const targetKey = _epIdOf(target);
      if (targetKey == null || dragKey == null || targetKey === dragKey) return;

      // Reorder the model by index rather than reading the resulting DOM. The
      // rendered rows are only a window onto the list, so deriving the order
      // from them would post a partial array — and the server assigns positions
      // only for the ids it receives, leaving everything off-screen at its old
      // position and colliding with the new ones. That scrambles the ordering
      // the user just arranged.
      const eps = window._epState.eps;
      const from = eps.findIndex((ep) => ep.id === dragKey);
      let to = eps.findIndex((ep) => ep.id === targetKey);
      if (from === -1 || to === -1) return;
      const { top, height } = target.getBoundingClientRect();
      if (e.clientY >= top + height / 2) to += 1;

      const next = eps.slice();
      const [moved] = next.splice(from, 1);
      next.splice(to > from ? to - 1 : to, 0, moved);

      // Belt and braces: a short array here can only be a bug, and sending one
      // corrupts data on the server. Fail loudly instead.
      if (next.length !== eps.length) {
        Toast.error("Order not saved — please reload");
        return;
      }

      _epSetAll(next);
      window._epState.rowOpts = { draggable: true, hideSeqNumber: true };
      _epRender();

      try {
        await API.reorderPlaylist(pl.id, next.map((ep) => ep.id));
      } catch (_) {
        Toast.error("Could not save order");
      }
    });
  }
}


async function _deletePlaylist(id, name) {
  if (!confirm(`Delete playlist "${name}"?`)) return;
  try {
    await API.deletePlaylist(id);
    Toast.success("Playlist deleted");
    await _loadPlaylists();
  } catch (e) { Toast.error(e.message); }
}

function _playlistFormHTML(name = "", desc = "", kind = "Manual") {
  return `
    <div style="display:flex;flex-direction:column;gap:16px">
      <div class="form-group">
        <label class="form-label">Name <span class="pl-tag" style="margin-left:6px">${kind}</span></label>
        <input id="pl-name" class="form-control" placeholder="My Playlist" value="${escHTML(name)}"
               maxlength="500" autofocus>
      </div>
      <div class="form-group">
        <label class="form-label">Description <span style="font-weight:400;color:var(--text-3)">(optional)</span></label>
        <textarea id="pl-desc" class="form-control" rows="3" placeholder="What's in this playlist?"
                  maxlength="5000" style="resize:vertical">${escHTML(desc)}</textarea>
      </div>
    </div>
    <div class="modal-actions" style="margin-top:20px">
      <button class="btn btn-ghost" id="pl-cancel">Cancel</button>
      <button class="btn btn-primary" id="pl-save">Save</button>
    </div>`;
}

// New playlist: first the kind (fixed for life), then the form for it. When
// the picker's "+ New playlist" adds an episode straight away, the kind can
// only be manual, so that path skips the choice.
function _showCreateDialog(addEpisodeId = null) {
  if (addEpisodeId != null) { _showManualForm(addEpisodeId); return; }
  Modal.open("New Playlist", `
    <div class="pl-type-choice">
      <button class="pl-type-option" id="pl-type-manual">
        <div class="pl-type-icon">${_plIcon("custom", 28)}</div>
        <div class="pl-type-name">Manual</div>
        <div class="pl-type-desc">You pick the episodes and put them in order.</div>
      </button>
      <button class="pl-type-option" id="pl-type-smart">
        <div class="pl-type-icon">${_plIcon("smart", 28)}</div>
        <div class="pl-type-name">Smart</div>
        <div class="pl-type-desc">Rules pick the episodes for you and keep the list current, like "unplayed, from the last two weeks".</div>
      </button>
    </div>
    <div class="form-hint" style="margin-top:12px">This can't be changed later: a manual playlist never gains rules, and a smart one never takes hand-picked episodes.</div>
    <div class="modal-actions" style="margin-top:16px">
      <button class="btn btn-ghost" id="pl-cancel">Cancel</button>
    </div>`);
  document.getElementById("pl-cancel").addEventListener("click", Modal.close);
  document.getElementById("pl-type-manual").addEventListener("click", () => _showManualForm(null));
  document.getElementById("pl-type-smart").addEventListener("click", () => _showSmartForm(null));
}

function _showManualForm(addEpisodeId = null) {
  Modal.open("New Manual Playlist", _playlistFormHTML("", "", "Manual"));
  document.getElementById("pl-cancel").addEventListener("click", Modal.close);
  document.getElementById("pl-save").addEventListener("click", async () => {
    const name = document.getElementById("pl-name").value.trim();
    const desc = document.getElementById("pl-desc").value.trim();
    if (!name) { document.getElementById("pl-name").focus(); return; }
    try {
      const pl = await API.createPlaylist({ name, description: desc || null, type: "custom" });
      if (addEpisodeId != null) await API.addToPlaylist(pl.id, addEpisodeId);
      Modal.close();
      Toast.success(addEpisodeId != null ? `Playlist created and episode added` : "Playlist created");
      await _loadPlaylists();
    } catch (e) { Toast.error(e.message); }
  });
  document.getElementById("pl-name").addEventListener("keydown", (e) => {
    if (e.key === "Enter") document.getElementById("pl-save").click();
  });
}

function _showEditDialog(pl, onSuccess) {
  if (pl.type === "smart") { _showSmartForm(pl, onSuccess); return; }
  Modal.open("Edit Playlist", _playlistFormHTML(pl.name, pl.description || "", "Manual"));
  document.getElementById("pl-cancel").addEventListener("click", Modal.close);
  document.getElementById("pl-save").addEventListener("click", async () => {
    const newName = document.getElementById("pl-name").value.trim();
    const newDesc = document.getElementById("pl-desc").value.trim();
    if (!newName) { document.getElementById("pl-name").focus(); return; }
    try {
      await API.updatePlaylist(pl.id, { name: newName, description: newDesc });
      Modal.close();
      Toast.success("Playlist updated");
      if (typeof onSuccess === "function") onSuccess(); else await _loadPlaylists();
    } catch (e) { Toast.error(e.message); }
  });
}

// ── Smart playlist editor ─────────────────────────────────────────────────
// A saved query: base facts (played state, order, cap) plus any conditions
// the user layers in. Every change re-asks the server what it would match.

const _RULE_FIELDS = {
  feeds:          { label: "Podcast" },
  published:      { label: "Published" },
  duration:       { label: "Duration" },
  title_contains: { label: "Title contains" },
};

function _ruleRowHTML(field, rules, feeds) {
  const r = rules || {};
  let controls = "";
  if (field === "feeds") {
    const f = r.feeds || { mode: "include", ids: [] };
    const chosen = new Set(f.ids || []);
    controls = `
      <select class="form-control pl-rule-ctl" data-k="feeds.mode" style="max-width:130px">
        <option value="include" ${f.mode !== "exclude" ? "selected" : ""}>is any of</option>
        <option value="exclude" ${f.mode === "exclude" ? "selected" : ""}>is none of</option>
      </select>
      <div class="pl-feed-list">
        ${feeds.map((fd) => `<label><input type="checkbox" class="pl-rule-ctl" data-k="feeds.ids" value="${fd.id}" ${chosen.has(fd.id) ? "checked" : ""}> ${escHTML(fd.title || "Untitled")}</label>`).join("")}
      </div>`;
  } else if (field === "published") {
    const p = r.published || {};
    const kind = p.after ? "after" : p.before ? "before" : "within_days";
    const v = kind === "within_days" ? (p.within_days ?? 14) : (p[kind] || "");
    controls = `
      <select class="form-control pl-rule-ctl" data-k="published.kind" style="max-width:150px">
        <option value="within_days" ${kind === "within_days" ? "selected" : ""}>in the last</option>
        <option value="after" ${kind === "after" ? "selected" : ""}>after</option>
        <option value="before" ${kind === "before" ? "selected" : ""}>before</option>
      </select>
      <input class="form-control pl-rule-ctl" data-k="published.value" type="${kind === "within_days" ? "number" : "date"}"
             ${kind === "within_days" ? 'min="1" max="3650"' : ""} value="${escHTML(String(v))}" style="max-width:150px">
      <span class="pl-rule-unit">${kind === "within_days" ? "days" : ""}</span>`;
  } else if (field === "duration") {
    const d = r.duration || {};
    controls = `
      <span class="pl-rule-unit">between</span>
      <input class="form-control pl-rule-ctl" data-k="duration.min" type="number" min="0" placeholder="any" value="${d.min_minutes ?? ""}" style="max-width:90px">
      <span class="pl-rule-unit">and</span>
      <input class="form-control pl-rule-ctl" data-k="duration.max" type="number" min="1" placeholder="any" value="${d.max_minutes ?? ""}" style="max-width:90px">
      <span class="pl-rule-unit">minutes</span>`;
  } else if (field === "title_contains") {
    controls = `<input class="form-control pl-rule-ctl" data-k="title_contains" type="text" maxlength="200" placeholder="words in the title" value="${escHTML(r.title_contains || "")}">`;
  }
  return `
    <div class="pl-rule-row" data-field="${field}">
      <div class="pl-rule-label">${_RULE_FIELDS[field].label}</div>
      <div class="pl-rule-controls">${controls}</div>
      <button type="button" class="btn btn-ghost btn-sm btn-icon pl-rule-remove" title="Remove this condition">×</button>
    </div>`;
}

function _rulesFormHTML(pl, feeds) {
  const r = pl?.rules || { played: "unplayed", sort: "oldest" };
  const present = ["feeds", "published", "duration", "title_contains"].filter((k) => r[k] != null);
  const playedOpts = [["unplayed", "Unplayed (including part-played)"], ["in_progress", "In progress"], ["played", "Played"], ["any", "Any"]];
  const sortOpts = [["oldest", "Oldest first"], ["newest", "Newest first"], ["shortest", "Shortest first"], ["longest", "Longest first"]];
  return `
    <div style="display:flex;flex-direction:column;gap:14px">
      <div class="form-group">
        <label class="form-label">Name <span class="pl-tag" style="margin-left:6px">Smart</span></label>
        <input id="pl-name" class="form-control" placeholder="Get Caught Up" value="${escHTML(pl?.name || "")}" maxlength="500" ${pl?.is_system ? "disabled title=\"Built-in playlists keep their name\"" : ""}>
      </div>
      <div class="form-group">
        <label class="form-label">Description <span style="font-weight:400;color:var(--text-3)">(optional)</span></label>
        <input id="pl-desc" class="form-control" placeholder="What's in this playlist?" value="${escHTML(pl?.description || "")}" maxlength="5000">
      </div>
      <div class="form-group">
        <label class="form-label">Episodes that are</label>
        <div class="pl-rule-row pl-rule-row--base">
          <select class="form-control pl-rule-ctl" id="pl-rule-played" style="max-width:260px">
            ${playedOpts.map(([v, l]) => `<option value="${v}" ${(r.played || "unplayed") === v ? "selected" : ""}>${l}</option>`).join("")}
          </select>
          <span class="pl-rule-unit">and downloaded on this server</span>
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">Conditions</label>
        <div id="pl-rule-rows">${present.map((k) => _ruleRowHTML(k, r, feeds)).join("")}</div>
        <div class="pl-rule-add">
          <select class="form-control" id="pl-rule-add" style="max-width:220px">
            <option value="">+ Add condition…</option>
            ${Object.entries(_RULE_FIELDS).map(([k, f]) => `<option value="${k}">${f.label}</option>`).join("")}
          </select>
        </div>
      </div>
      <div class="pl-rule-row pl-rule-row--base" style="gap:12px;flex-wrap:wrap">
        <label class="form-label" style="margin:0">Order</label>
        <select class="form-control pl-rule-ctl" id="pl-rule-sort" style="max-width:160px">
          ${sortOpts.map(([v, l]) => `<option value="${v}" ${(r.sort || "oldest") === v ? "selected" : ""}>${l}</option>`).join("")}
        </select>
        <label class="form-label" style="margin:0 0 0 8px">Keep only the first</label>
        <input class="form-control pl-rule-ctl" id="pl-rule-limit" type="number" min="1" max="1000" placeholder="all" value="${r.limit ?? ""}" style="max-width:90px">
      </div>
      <div class="pl-preview" id="pl-preview">Checking what this matches…</div>
    </div>
    <div class="modal-actions" style="margin-top:20px">
      <button class="btn btn-ghost" id="pl-cancel">Cancel</button>
      <button class="btn btn-primary" id="pl-save">Save</button>
    </div>`;
}

// Read the editor back into a rules object. Empty controls mean "no condition".
function _collectRules() {
  const rules = {
    played: document.getElementById("pl-rule-played").value,
    sort: document.getElementById("pl-rule-sort").value,
  };
  const limit = Number(document.getElementById("pl-rule-limit").value);
  if (limit >= 1) rules.limit = Math.min(1000, Math.floor(limit));
  document.querySelectorAll("#pl-rule-rows .pl-rule-row").forEach((row) => {
    const f = row.dataset.field;
    const q = (k) => row.querySelector(`[data-k="${k}"]`);
    if (f === "feeds") {
      const ids = [...row.querySelectorAll('[data-k="feeds.ids"]:checked')].map((c) => Number(c.value));
      if (ids.length) rules.feeds = { mode: q("feeds.mode").value, ids };
    } else if (f === "published") {
      const kind = q("published.kind").value, v = q("published.value").value.trim();
      if (kind === "within_days") { const n = Number(v); if (n >= 1) rules.published = { within_days: Math.min(3650, Math.floor(n)) }; }
      else if (v) rules.published = { [kind]: v };
    } else if (f === "duration") {
      const d = {};
      if (q("duration.min").value !== "") d.min_minutes = Math.max(0, Math.floor(Number(q("duration.min").value)));
      if (q("duration.max").value !== "") d.max_minutes = Math.max(1, Math.floor(Number(q("duration.max").value)));
      if (Object.keys(d).length) rules.duration = d;
    } else if (f === "title_contains") {
      const v = q("title_contains").value.trim();
      if (v) rules.title_contains = v;
    }
  });
  return rules;
}

function _showSmartForm(pl, onSuccess) {
  let feeds = [];
  Modal.open(pl ? "Edit Smart Playlist" : "New Smart Playlist", `<p style="color:var(--text-3);font-size:13px">Loading…</p>`);
  document.getElementById("modal")?.classList.add("modal-wide");
  API.getFeeds().then((fs) => {
    feeds = fs.filter((f) => !f.primary_feed_id);
    document.getElementById("modal-body").innerHTML = _rulesFormHTML(pl, feeds);
    _wireSmartForm(pl, feeds, onSuccess);
  }).catch((e) => Toast.error(e.message));
}

function _wireSmartForm(pl, feeds, onSuccess) {
  let timer = null, seq = 0;
  const preview = async () => {
    const el = document.getElementById("pl-preview");
    if (!el) return;
    const mine = ++seq;
    try {
      const r = await API.previewPlaylist(_collectRules());
      if (mine !== seq || !document.getElementById("pl-preview")) return;
      const n = r.count;
      const sample = r.sample.slice(0, 2).map((s) => escHTML(s.title || "Untitled")).join(", ");
      el.innerHTML = n === 0
        ? "Matches <strong>no episodes</strong> right now."
        : `Matches <strong>${n} episode${n === 1 ? "" : "s"}</strong>${r.total_seconds ? ` · ${_plHours(r.total_seconds)}` : ""}${sample ? ` — starting with ${sample}${n > 2 ? ", …" : ""}` : ""}`;
    } catch (e) {
      el.textContent = e.message || "Could not check these rules";
    }
  };
  const schedule = () => { clearTimeout(timer); timer = setTimeout(preview, 300); };
  const body = document.getElementById("modal-body");
  body.addEventListener("input", (e) => { if (e.target.classList.contains("pl-rule-ctl")) schedule(); });
  body.addEventListener("change", (e) => {
    if (e.target.dataset.k === "published.kind") {
      // Days wants a number box; a date wants a date box.
      const row = e.target.closest(".pl-rule-row");
      const kind = e.target.value;
      const v = row.querySelector('[data-k="published.value"]');
      v.type = kind === "within_days" ? "number" : "date";
      v.value = kind === "within_days" ? "14" : "";
      row.querySelector(".pl-rule-unit").textContent = kind === "within_days" ? "days" : "";
    }
    if (e.target.classList.contains("pl-rule-ctl")) schedule();
  });
  body.addEventListener("click", (e) => {
    const rm = e.target.closest(".pl-rule-remove");
    if (rm) { rm.closest(".pl-rule-row").remove(); _refreshAddOptions(); schedule(); }
  });
  const _refreshAddOptions = () => {
    const present = new Set([...document.querySelectorAll("#pl-rule-rows .pl-rule-row")].map((r) => r.dataset.field));
    document.querySelectorAll("#pl-rule-add option").forEach((o) => { if (o.value) o.disabled = present.has(o.value); });
  };
  document.getElementById("pl-rule-add").addEventListener("change", (e) => {
    const f = e.target.value;
    e.target.value = "";
    if (!f) return;
    document.getElementById("pl-rule-rows").insertAdjacentHTML("beforeend", _ruleRowHTML(f, null, feeds));
    _refreshAddOptions();
    schedule();
  });
  _refreshAddOptions();
  preview();

  document.getElementById("pl-cancel").addEventListener("click", Modal.close);
  document.getElementById("pl-save").addEventListener("click", async () => {
    const nameEl = document.getElementById("pl-name");
    const name = nameEl.value.trim();
    const desc = document.getElementById("pl-desc").value.trim();
    if (!name && !pl?.is_system) { nameEl.focus(); return; }
    const rules = _collectRules();
    try {
      if (pl) {
        const body = { description: desc, rules };
        if (!pl.is_system) body.name = name;
        await API.updatePlaylist(pl.id, body);
        Toast.success("Playlist updated");
      } else {
        await API.createPlaylist({ name, description: desc || null, type: "smart", rules });
        Toast.success("Smart playlist created");
      }
      Modal.close();
      if (typeof onSuccess === "function") onSuccess(); else await _loadPlaylists();
    } catch (e) { Toast.error(e.message); }
  });
}
window._showPlaylistEditor = (pl, onSuccess) => _showEditDialog(pl, onSuccess);


// ── Playlist button color helper ───────────────────────────────────────────
function _updatePlaylistBtn(episodeId, inPlaylist) {
  const btn = document.querySelector(`[data-action="add-to-playlist"][data-ep-id="${episodeId}"]`);
  if (btn) {
    btn.classList.toggle("btn-primary", inPlaylist);
    btn.classList.toggle("btn-ghost", !inPlaylist);
  }
  const members = window._epState?.playlistMembers;
  if (members) {
    if (inPlaylist) members.add(episodeId);
    else members.delete(episodeId);
  }
  // On the playlist detail page, animate out a row that was removed from this playlist
  if (!inPlaylist && window._epState?.playlistId) {
    const row = document.getElementById(`ep-${episodeId}`);
    if (row) {
      row.style.transition = "opacity 0.3s, max-height 0.3s, padding 0.3s";
      row.style.overflow = "hidden";
      row.style.maxHeight = row.offsetHeight + "px";
      requestAnimationFrame(() => {
        row.style.opacity = "0";
        row.style.maxHeight = "0";
        row.style.padding = "0";
      });
      setTimeout(() => row.remove(), 310);
    }
  }
}

// ── Global: "Add to playlist" picker ──────────────────────────────────────
// Called from episode card "+" buttons throughout the app
window.showAddToPlaylist = async function (episodeId, episodeTitle) {
  const plIconSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
    <line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/>
    <circle cx="3.5" cy="6" r="1.5" fill="currentColor" stroke="none"/>
    <circle cx="3.5" cy="12" r="1.5" fill="currentColor" stroke="none"/>
    <circle cx="3.5" cy="18" r="1.5" fill="currentColor" stroke="none"/>
  </svg>`;

  try {
    const [allPlaylists, memberPlaylists] = await Promise.all([
      API.getPlaylists(),
      API.getEpisodePlaylists(episodeId),
    ]);
    const customPlaylists = allPlaylists.filter((p) => p.type === "custom");
    const memberIds = new Set(memberPlaylists.map((p) => p.id));
    const inAny = memberIds.size > 0;

    // Update triggering button color
    _updatePlaylistBtn(episodeId, inAny);

    const remainingPlaylists = customPlaylists.filter((p) => !memberIds.has(p.id));

    const memberSection = memberPlaylists.length ? `
      <div style="font-size:11px;font-weight:600;color:var(--text-3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Already in</div>
      <div class="pl-picker" style="margin-bottom:14px">
        ${memberPlaylists.map((p) => `
          <button class="btn btn-primary pl-remove-btn" data-pl-id="${p.id}" data-pl-name="${escHTML(p.name)}">
            ${plIconSvg}
            <span style="flex:1">${escHTML(p.name)}</span>
            <span style="font-size:11px;opacity:0.8">Remove</span>
          </button>`).join("")}
      </div>` : "";

    const addSection = remainingPlaylists.length ? `
      ${memberPlaylists.length ? `<div style="font-size:11px;font-weight:600;color:var(--text-3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Add to</div>` : ""}
      <div class="pl-picker">
        ${remainingPlaylists.map((p) => `
          <button class="btn btn-ghost pl-pick-btn" data-pl-id="${p.id}" data-pl-name="${escHTML(p.name)}">
            ${plIconSvg}
            <span style="flex:1">${escHTML(p.name)}</span>
            <span style="color:var(--text-3);font-size:11px">${p.episode_count} ep${p.episode_count !== 1 ? "s" : ""}</span>
          </button>`).join("")}
      </div>` : (!memberPlaylists.length ? `<div style="color:var(--text-3);font-size:13px;margin-bottom:12px">No manual playlists yet.</div>` : "");

    Modal.open("Add to Playlist", `
      <div style="margin-bottom:14px">
        <div style="font-size:13px;color:var(--text-2);margin-bottom:4px">Episode</div>
        <div style="font-size:14px;font-weight:600;color:var(--text-1)">${escHTML(episodeTitle || "Untitled")}</div>
      </div>
      ${memberSection}
      ${addSection}
      <div class="modal-actions" style="margin-top:16px">
        <button class="btn btn-ghost" id="pl-add-cancel">Cancel</button>
        <button class="btn btn-ghost" id="pl-add-new">+ New Playlist</button>
      </div>`);

    document.getElementById("pl-add-cancel").addEventListener("click", Modal.close);
    document.getElementById("pl-add-new").addEventListener("click", () => {
      Modal.close();
      _showCreateDialog(episodeId);
    });

    // Remove buttons (episode already in these playlists)
    document.querySelectorAll(".pl-remove-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const plId = Number(btn.dataset.plId);
        try {
          await API.removeFromPlaylist(plId, episodeId);
          memberIds.delete(plId);
          Modal.close();
          Toast.success(`Removed from "${btn.dataset.plName}"`);
          _updatePlaylistBtn(episodeId, memberIds.size > 0);
        } catch (e) { Toast.error(e.message); }
      });
    });

    // Add buttons (episode not yet in these playlists)
    document.querySelectorAll(".pl-pick-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const plId = Number(btn.dataset.plId);
        try {
          await API.addToPlaylist(plId, episodeId);
          memberIds.add(plId);
          Modal.close();
          Toast.success(`Added to "${btn.dataset.plName}"`);
          _updatePlaylistBtn(episodeId, true);
        } catch (e) { Toast.error(e.message); }
      });
    });
  } catch (e) { Toast.error(e.message); }
};
