"use strict";

// ============================================================
// Playlists view
// ============================================================

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
  const count = pl.episode_count;
  const typeLabel = pl.type === "feed"
    ? `Feed · ${pl.filter === "unplayed" ? "Unplayed only" : "All episodes"}`
    : "Custom playlist";
  const countLabel = `${count} episode${count !== 1 ? "s" : ""}`;
  return `
    <div class="playlist-card">
      <div class="playlist-card-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="22" height="22">
          <line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/>
          <circle cx="3.5" cy="6" r="1.5" fill="currentColor" stroke="none"/>
          <circle cx="3.5" cy="12" r="1.5" fill="currentColor" stroke="none"/>
          <circle cx="3.5" cy="18" r="1.5" fill="currentColor" stroke="none"/>
        </svg>
      </div>
      <div class="playlist-card-info">
        <div class="playlist-card-name">${escHTML(pl.name)}</div>
        ${pl.description ? `<div class="playlist-card-desc">${escHTML(pl.description)}</div>` : ""}
        <div class="playlist-card-meta">${typeLabel} · ${countLabel}</div>
      </div>
      <div class="playlist-card-actions">
        <button class="btn btn-primary btn-sm" data-action="play-playlist" data-id="${pl.id}" title="Play">
          <svg viewBox="0 0 24 24" fill="currentColor" width="12" height="12"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          Play
        </button>
        <a class="btn btn-ghost btn-sm" href="#/playlists/${pl.id}">View</a>
        <button class="btn btn-ghost btn-sm" style="color:var(--error)" data-action="delete-playlist"
                data-id="${pl.id}" data-name="${escHTML(pl.name)}">Delete</button>
      </div>
    </div>`;
}

async function _playPlaylist(id) {
  try {
    const playlists = await API.getPlaylists();
    const pl = playlists.find((p) => p.id === id);
    if (!pl) { Toast.error("Playlist not found"); return; }

    const contextType = pl.type === "feed" ? "feed" : "playlist";
    const contextId   = pl.type === "feed" ? pl.feed_id : id;

    if (!contextId) { Toast.info("This playlist has no feed associated with it"); return; }

    const state = await API.playerPlay({
      context_type: contextType,
      context_id: contextId,
      context_filter: pl.filter || "unplayed",
    });
    if (state?.current_episode) {
      window.playEpisode(state.current_episode.id);
      Toast.success(`Playing: ${escHTML(state.current_episode.title || "episode")}`);
    } else {
      Toast.info("No downloaded episodes to play in this playlist");
    }
  } catch (e) {
    Toast.error(e.message || "Could not start playback");
  }
}

// viewPlaylistDetail — full-page route handler for /playlists/:id
async function viewPlaylistDetail(playlistId) {
  const id = Number(playlistId);
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

  // Set up _epState so updateEpisodeRow and playlist membership tracking work
  window._epState = {
    feed: null,
    playlistId: id,
    playlistMembers: new Set(episodes.map((ep) => ep.id)),
  };

  const typeLabel = pl.type === "feed"
    ? `Feed playlist · ${pl.filter === "unplayed" ? "Unplayed only" : "All episodes"}`
    : "Custom playlist";
  const countLabel = `${episodes.length} episode${episodes.length !== 1 ? "s" : ""}`;

  const listIconSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="36" height="36">
    <line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/>
    <circle cx="3.5" cy="6" r="1.5" fill="currentColor" stroke="none"/>
    <circle cx="3.5" cy="12" r="1.5" fill="currentColor" stroke="none"/>
    <circle cx="3.5" cy="18" r="1.5" fill="currentColor" stroke="none"/>
  </svg>`;
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
          <div class="pl-detail-title">${escHTML(pl.name)}</div>
          ${pl.description ? `<div class="pl-detail-desc">${escHTML(pl.description)}</div>` : ""}
          <div class="pl-detail-meta-line" id="pl-count-line">${typeLabel} · ${countLabel}</div>
          <div class="pl-detail-actions">
            <button class="btn btn-primary btn-sm" id="pl-play-btn">${playSvg} Play</button>
            <button class="btn btn-ghost btn-sm" id="pl-edit-btn">Edit</button>
          </div>
        </div>
      </div>
      <div class="episode-list" id="episode-list">
        ${episodes.length
          ? episodes.map((ep) => episodeRow(ep, null, { draggable: pl.type === "custom", hideSeqNumber: true })).join("")
          : `<p style="color:var(--text-3);font-size:13px;margin-top:8px">No episodes yet.</p>`}
      </div>
    </div>`;

  // All shown episodes are in this playlist — initialize their "+" buttons as primary
  _applyPlaylistMemberStates(window._epState.playlistMembers);

  document.getElementById("pl-play-btn").addEventListener("click", () => _playPlaylist(pl.id));
  document.getElementById("pl-edit-btn").addEventListener("click", () => {
    _showEditDialog(pl.id, pl.name, pl.description || "", () => Router.navigate(`/playlists/${pl.id}`));
  });

  // Drag-to-reorder (custom playlists only)
  if (pl.type === "custom") {
    const list = document.getElementById("episode-list");
    let dragSrc = null;

    list.addEventListener("dragstart", (e) => {
      dragSrc = e.target.closest(".episode-item");
      if (!dragSrc) return;
      e.dataTransfer.effectAllowed = "move";
      dragSrc.classList.add("dragging");
    });

    list.addEventListener("dragend", () => {
      dragSrc?.classList.remove("dragging");
      list.querySelectorAll(".drag-over-top, .drag-over-bottom").forEach((r) => {
        r.classList.remove("drag-over-top", "drag-over-bottom");
      });
      dragSrc = null;
    });

    list.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      const target = e.target.closest(".episode-item");
      list.querySelectorAll(".drag-over-top, .drag-over-bottom").forEach((r) => {
        r.classList.remove("drag-over-top", "drag-over-bottom");
      });
      if (target && target !== dragSrc) {
        const { top, height } = target.getBoundingClientRect();
        target.classList.add(e.clientY < top + height / 2 ? "drag-over-top" : "drag-over-bottom");
      }
    });

    list.addEventListener("drop", async (e) => {
      e.preventDefault();
      const target = e.target.closest(".episode-item");
      if (!target || !dragSrc || target === dragSrc) return;
      const { top, height } = target.getBoundingClientRect();
      if (e.clientY < top + height / 2) {
        list.insertBefore(dragSrc, target);
      } else {
        list.insertBefore(dragSrc, target.nextSibling);
      }
      const newOrder = [...list.querySelectorAll(".episode-item")].map((r) => Number(r.id.replace("ep-", "")));
      try {
        await API.reorderPlaylist(pl.id, newOrder);
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

function _playlistFormHTML(name = "", desc = "") {
  return `
    <div style="display:flex;flex-direction:column;gap:16px">
      <div class="form-group">
        <label class="form-label">Name</label>
        <input id="pl-name" class="form-control" placeholder="My Playlist" value="${escHTML(name)}" autofocus>
      </div>
      <div class="form-group">
        <label class="form-label">Description <span style="font-weight:400;color:var(--text-3)">(optional)</span></label>
        <textarea id="pl-desc" class="form-control" rows="3" placeholder="What's in this playlist?"
                  style="resize:vertical">${escHTML(desc)}</textarea>
      </div>
    </div>
    <div class="modal-actions" style="margin-top:20px">
      <button class="btn btn-ghost" id="pl-cancel">Cancel</button>
      <button class="btn btn-primary" id="pl-save">Save</button>
    </div>`;
}

function _showCreateDialog(addEpisodeId = null) {
  Modal.open("New Playlist", _playlistFormHTML());
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

function _showEditDialog(id, name, desc, onSuccess) {
  Modal.open("Edit Playlist", _playlistFormHTML(name, desc));
  document.getElementById("pl-cancel").addEventListener("click", Modal.close);
  document.getElementById("pl-save").addEventListener("click", async () => {
    const newName = document.getElementById("pl-name").value.trim();
    const newDesc = document.getElementById("pl-desc").value.trim();
    if (!newName) { document.getElementById("pl-name").focus(); return; }
    try {
      await API.updatePlaylist(id, { name: newName, description: newDesc || null });
      Modal.close();
      Toast.success("Playlist updated");
      if (typeof onSuccess === "function") {
        onSuccess();
      } else {
        await _loadPlaylists();
      }
    } catch (e) { Toast.error(e.message); }
  });
}


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
      </div>` : (!memberPlaylists.length ? `<div style="color:var(--text-3);font-size:13px;margin-bottom:12px">No custom playlists yet.</div>` : "");

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
