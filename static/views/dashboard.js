"use strict";

window._markCLPlayed = async function(epId) {
  try {
    await API.togglePlayed(epId);
    updateStatus();
    window._onEpisodePlayedChanged?.(epId, true);
    if (Player.currentId() === epId) Player.togglePause();
    const item = document.getElementById(`cl-ep-${epId}`);
    const card = item?.closest(".card");
    animateRemove(item, () => {
      // If no more Continue Listening items remain, also animate the card out
      if (!document.querySelector("[id^=cl-ep-]") && card) animateRemove(card);
    });
  } catch (e) { Toast.error(e.message); }
};

// 32×32 activity thumbnail with SVG fallback matching the feed card placeholder
function _thumb(url) {
  const placeholder = `<div style="width:32px;height:32px;border-radius:6px;background:var(--bg-3);display:flex;align-items:center;justify-content:center;flex-shrink:0">${_PODCAST_SVG}</div>`;
  if (!url) return placeholder;
  const hidden = `<div style="width:32px;height:32px;border-radius:6px;background:var(--bg-3);display:none;align-items:center;justify-content:center;flex-shrink:0">${_PODCAST_SVG}</div>`;
  return `<img src="${url}" style="width:32px;height:32px;border-radius:6px;object-fit:cover;flex-shrink:0" />${hidden}`;
}

function _miniPie(pct) {
  const r = 10, cx = 12, cy = 12, stroke = 3;
  const circ = 2 * Math.PI * r;
  const fill = Math.max(0, Math.min(1, pct / 100)) * circ;
  return `<svg width="24" height="24" viewBox="0 0 24 24" style="display:block">
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--bg-3)" stroke-width="${stroke}"/>
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--primary)" stroke-width="${stroke}"
            stroke-dasharray="${fill.toFixed(2)} ${circ.toFixed(2)}"
            stroke-linecap="round" transform="rotate(-90 ${cx} ${cy})"/>
  </svg>`;
}

window._refreshSuggestions = async function(btn) {
  btn.disabled = true;
  btn.style.opacity = "0.5";
  try {
    const suggestions = await API.getSuggestions();
    const buckets = [
      { key: "short",      label: "< 15 min",   eps: suggestions.short      || [] },
      { key: "medium",     label: "15–45 min",  eps: suggestions.medium     || [] },
      { key: "long",       label: "45–90 min",  eps: suggestions.long       || [] },
      { key: "extra_long", label: "90+ min",    eps: suggestions.extra_long || [] },
    ].filter(b => b.eps.length > 0);

    const grid = document.getElementById("suggested-listening-grid");
    const card = document.getElementById("suggested-listening-card");
    if (!grid) return;

    // Fade out
    grid.classList.remove("suggest-fade-in");
    grid.classList.add("suggest-fade-out");
    await new Promise(r => setTimeout(r, 250));

    if (!buckets.length) {
      card?.remove();
      return;
    }

    const epRow = (ep) => `
      <div class="activity-item" style="cursor:pointer"
           data-action="navigate" data-path="/feeds/${ep.feed_id}" data-ep-scroll="${ep.id}">
        <div class="activity-icon" style="flex-shrink:0">
          ${_thumb(ep.custom_image_url || ep.episode_image_url || ep.feed_image_url)}
        </div>
        <div class="activity-info" style="flex:1;min-width:0">
          <div class="activity-title truncate">${escHTML(ep.title || "Untitled")}</div>
          <div class="activity-sub">
            <span class="feed-link" data-action="navigate" data-path="/feeds/${ep.feed_id}">${escHTML(ep.feed_title || "")}</span>
          </div>
        </div>
        ${epPlayBtn(ep)}
      </div>`;

    grid.innerHTML = buckets.map(b => `
      <div>
        <div style="font-size:11px;font-weight:600;color:var(--text-3);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:4px;padding-bottom:4px;border-bottom:1px solid var(--border)">${b.label}</div>
        ${b.eps.map(epRow).join("")}
      </div>`).join("");

    // Fade in
    grid.classList.remove("suggest-fade-out");
    grid.classList.add("suggest-fade-in");
    grid.addEventListener("animationend", () => grid.classList.remove("suggest-fade-in"), { once: true });
  } catch (e) {
    Toast.error(e.message);
  } finally {
    btn.disabled = false;
    btn.style.opacity = "";
  }
};

// ============================================================
// Live Continue Listening upsert (called from Player hooks)
// ============================================================
function _upsertCLItem(playerEp, posSeconds) {
  // Only operates when the dashboard is currently rendered
  const anchor = document.getElementById("catchup-card");
  if (!anchor) return;

  const minsIn = posSeconds > 59 ? Math.floor(posSeconds / 60) + "m in" : "";
  const existing = document.getElementById(`cl-ep-${playerEp.id}`);

  if (existing) {
    const sub = existing.querySelector(".activity-sub");
    if (sub) {
      const feedLink = sub.querySelector(".feed-link");
      const feedLinkHTML = feedLink ? feedLink.outerHTML : escHTML(playerEp.feedTitle || "");
      sub.innerHTML = feedLinkHTML + (minsIn ? ` · ${minsIn}` : "");
    }
    return;
  }

  const isCurrentlyPlaying = Player.currentId() === playerEp.id && Player.isPlaying();
  const hasPos = posSeconds > 0;
  const btnIcon = isCurrentlyPlaying
    ? svg('<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>')
    : hasPos ? svg(Player.resumeIcon())
    : svg(Player.playIcon());
  const btnTitle = isCurrentlyPlaying ? "Pause" : hasPos ? "Resume" : "Play";
  const playBtnHTML = `<button class="btn btn-ghost btn-sm btn-icon ep-play-btn"
      data-action="play-episode" data-ep-id="${playerEp.id}"
      ${hasPos ? 'data-resumable="1" data-resume="1"' : ''}
      title="${btnTitle}">${btnIcon}</button>`;

  const itemHTML = `
    <div class="activity-item" id="cl-ep-${playerEp.id}">
      <div class="activity-icon" style="cursor:pointer"
           data-action="navigate" data-path="/feeds/${playerEp.feedId}" data-ep-scroll="${playerEp.id}">
        ${_thumb(playerEp.imageUrl)}
      </div>
      <div class="activity-info" style="flex:1;min-width:0">
        <div class="activity-title truncate" style="cursor:pointer"
             data-action="navigate" data-path="/feeds/${playerEp.feedId}" data-ep-scroll="${playerEp.id}">${escHTML(playerEp.title || "Untitled")}</div>
        <div class="activity-sub">
          <span class="feed-link" data-action="navigate" data-path="/feeds/${playerEp.feedId}">${escHTML(playerEp.feedTitle || "")}</span>${minsIn ? ` · ${minsIn}` : ""}
        </div>
      </div>
      ${playBtnHTML}
      <button class="btn btn-ghost btn-sm btn-icon" title="Mark as played"
              data-action="mark-cl-played" data-ep-id="${playerEp.id}">
        ${svg('<polyline points="20 6 9 17 4 12"/>')}
      </button>
    </div>`;

  const clCard = document.getElementById("cl-card");
  if (clCard) {
    clCard.querySelector(".section-title")?.insertAdjacentHTML("afterend", itemHTML);
  } else {
    anchor.insertAdjacentHTML("beforebegin", `
      <div class="card suggest-fade-in" id="cl-card" style="margin-bottom:12px">
        <div class="card-body">
          <div class="section-title">Continue Listening</div>
          ${itemHTML}
        </div>
      </div>`);
    const newCard = document.getElementById("cl-card");
    newCard?.addEventListener("animationend", () => newCard.classList.remove("suggest-fade-in"), { once: true });
  }
}

// ============================================================
// Get Caught Up: unplayed, downloaded, published in the last N days, oldest first.
// The list is the built-in smart playlist, so
// "Play all" and the row buttons simply start that context.
// ============================================================
function _durSeconds(d) {
  if (d == null || d === "") return 0;
  if (typeof d === "number") return d;
  const parts = String(d).split(":").map(Number);
  if (parts.some(isNaN)) return 0;
  return parts.reduce((acc, n) => acc * 60 + n, 0);
}

function _fmtHours(sec) {
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h ? `${h} h${m ? ` ${m} m` : ""}` : `${m} m`;
}

function _catchupSummary(eps, pl) {
  const total = eps.reduce((s, ep) => s + _durSeconds(ep.duration), 0);
  const n = eps.length;
  return `${n} episode${n === 1 ? "" : "s"}${total ? ` · ${_fmtHours(total)}` : ""}${pl ? ` · ${escHTML(_describeRules(pl.rules))}` : ""}`;
}

function _catchupCardBody(pl, eps) {
  const title = pl ? escHTML(pl.name) : "Get Caught Up";
  return `
    <div class="flex items-center justify-between gap-2" style="margin-bottom:4px">
      <div>
        <div class="section-title" style="margin-bottom:2px">${title}</div>
        <div class="activity-sub">${_catchupSummary(eps, pl)}</div>
      </div>
      <div class="flex items-center gap-2">
        ${pl ? `<button class="btn btn-ghost btn-sm" data-action="catchup-edit" title="Change what this list gathers">Edit rules</button>` : ""}
        ${eps.length ? `<button class="btn btn-primary btn-sm" data-action="catchup-play" title="Play every episode below, in order">
          ${svg('<polygon points="5 3 19 12 5 21 5 3"/>')} Play all
        </button>` : ""}
      </div>
    </div>
    ${eps.length === 0
      ? `<div class="empty-state" style="padding:24px 16px">
          <div class="empty-state-title" style="font-size:13px">You're all caught up!</div>
          <div class="empty-state-desc" style="max-width:none">${pl ? `You don't have any episodes that match the following criteria:<br><span style="color:var(--text-2)">${escHTML(_describeRules(pl.rules))}</span>` : "The built-in Get Caught Up playlist is missing; restart the server to recreate it."}</div>
        </div>`
      : `${eps.slice(0, 8).map(_catchupRow).join("")}
         ${eps.length > 8 ? `<details class="catchup-more">
           <summary class="activity-sub" style="cursor:pointer;padding:8px 0">Show ${eps.length - 8} more</summary>
           ${eps.slice(8).map(_catchupRow).join("")}
         </details>` : ""}`}`;
}

// Re-ask the server what the list holds now. Rows that left animate out,
// then the card redraws (count, hours, empty state) from the fresh answer.
async function _catchupRefresh() {
  const card = document.querySelector("#catchup-card .card-body");
  const pl0 = window._catchupPlaylist;
  if (!card || !pl0) return;
  try {
    const [pls, eps] = await Promise.all([API.getPlaylists(), API.getPlaylistEpisodes(pl0.id)]);
    const pl = pls.find((p) => p.id === pl0.id) || pl0;
    window._catchupPlaylist = pl;
    if (!document.querySelector("#catchup-card .card-body")) return;
    const keep = new Set(eps.map((e) => e.id));
    const gone = [...card.querySelectorAll("[id^='cu-ep-']")].filter((row) => !keep.has(Number(row.id.replace("cu-ep-", ""))));
    const redraw = () => { const c = document.querySelector("#catchup-card .card-body"); if (c) c.innerHTML = _catchupCardBody(pl, eps); };
    if (!gone.length) { redraw(); return; }
    let left = gone.length;
    gone.forEach((row) => animateRemove(row, () => { if (--left === 0) redraw(); }));
  } catch (_) { /* next full render will catch up */ }
}

function _catchupRow(ep) {
  const resumable = !ep.played && ep.play_position_seconds > 0;
  return `
    <div class="activity-item" id="cu-ep-${ep.id}" style="cursor:pointer"
         data-action="navigate" data-path="/feeds/${ep.feed_id}" data-ep-scroll="${ep.id}">
      <div class="activity-icon">
        ${_thumb(ep.custom_image_url || ep.episode_image_url || ep.feed_image_url)}
      </div>
      <div class="activity-info">
        <div class="activity-title truncate">${escHTML(ep.title || "Untitled")}</div>
        <div class="activity-sub">
          <span class="feed-link" data-action="navigate" data-path="/feeds/${ep.feed_id}">${escHTML(ep.feed_title || "")}</span> · ${timeAgo(ep.published_at)}${ep.duration ? ` · ${_fmtHours(_durSeconds(ep.duration))}` : ""}${resumable ? " · in progress" : ""}
        </div>
      </div>
      <button class="btn btn-ghost btn-sm btn-icon ep-play-btn" data-action="catchup-play" data-ep-id="${ep.id}"
              title="${resumable ? "Resume, then continue down the list" : "Play, then continue down the list"}">
        ${svg(resumable ? Player.resumeIcon() : Player.playIcon())}
      </button>
    </div>`;
}

// Play the built-in playlist, optionally from a given episode. The server
// owns the queue, so auto-advance (window._autoPlayNext → /api/player/next)
// just works.
window._catchupPlay = function (epId) {
  const pl = window._catchupPlaylist;
  if (!pl) { Toast.info("The Get Caught Up playlist is missing."); return; }
  window._playPlaylist(pl.id, epId || null);
};
window._catchupEdit = function () {
  const pl = window._catchupPlaylist;
  if (pl) window._showPlaylistEditor(pl, () => viewDashboard());
};

// ============================================================
// Dashboard view
// ============================================================
async function viewDashboard() {
  window._onSyncIdle = viewDashboard;

  // "Get Caught Up" is the built-in smart playlist; the card simply shows it.
  const playlists = await API.getPlaylists().catch(() => []);
  const catchupPl = playlists.find((p) => p.is_system) || null;
  window._catchupPlaylist = catchupPl;
  const [status, feeds, catchup, continueEps, suggestions] = await Promise.all([
    API.getStatus(),
    API.getFeeds(),
    catchupPl ? API.getPlaylistEpisodes(catchupPl.id).catch(() => []) : Promise.resolve([]),
    API.continueListening(5),
    API.getSuggestions(),
  ]);

  const content = document.getElementById("content");
  content.innerHTML = `
    <div class="page">
      <div class="page-header">
        <div>
          <div class="page-title">Dashboard</div>
          <div class="page-subtitle">Your podcast overview</div>
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn btn-ghost" id="btn-sync-all-dash">
            ${svg('<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>')}
            Sync All Feeds
          </button>
          <button class="btn btn-primary" data-action="add-feed">
            ${svg('<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>')}
            Add Podcast
          </button>
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card" style="cursor:pointer" data-action="navigate" data-path="/feeds">
          <div class="stat-label">Podcasts</div>
          <div class="stat-value">${status.podcasts_total}</div>
          <div class="stat-sub">${status.feeds_total} total feeds</div>
        </div>
        <div class="stat-card" style="cursor:pointer" data-action="navigate" data-path="/stats">
          <div class="stat-label">Storage</div>
          <div class="stat-value">${fmtBytes(status.storage_bytes)}</div>
          <div class="stat-sub">used by downloads</div>
        </div>
      </div>

    <div class="card" style="margin-bottom:12px">
      <div class="card">
          <div class="card-body">
            <div class="section-title">Feed Health</div>
            ${(() => {
              const errFeeds = feeds.filter((f) => f.last_error).slice(0, 6);
              if (errFeeds.length === 0) {
                return `<div style="display:flex;align-items:center;gap:8px;padding:12px 0;color:var(--success);font-size:13px">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px;flex-shrink:0"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                  All feeds healthy
                </div>`;
              }
              return errFeeds.map((f) => `
                <div class="activity-item" style="cursor:pointer" data-action="navigate" data-path="/feeds/${f.id}">
                  <div class="activity-icon" style="color:var(--error)">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:22px;height:22px"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                  </div>
                  <div class="activity-info">
                    <div class="activity-title truncate" style="color:var(--text)">${escHTML(f.title || f.url)}</div>
                    <div class="activity-sub truncate" style="color:var(--error);max-width:100%">${escHTML((f.last_error || "").slice(0, 120))}</div>
                  </div>
                </div>`).join("");
            })()}
          </div>
        </div>
    </div>

      ${continueEps.length > 0 ? `
      <div class="card" id="cl-card" style="margin-bottom:12px">
        <div class="card-body">
          <div class="section-title">Continue Listening</div>
          ${continueEps.map((ep) => {
            const minsIn = ep.play_position_seconds ? Math.floor(ep.play_position_seconds / 60) + "m in" : "";
            return `
            <div class="activity-item" id="cl-ep-${ep.id}">
              <div class="activity-icon" style="cursor:pointer"
                   data-action="navigate" data-path="/feeds/${ep.feed_id}" data-ep-scroll="${ep.id}">
                ${_thumb(ep.custom_image_url || ep.episode_image_url || ep.feed_image_url)}
              </div>
              <div class="activity-info" style="flex:1;min-width:0">
                <div class="activity-title truncate" style="cursor:pointer"
                     data-action="navigate" data-path="/feeds/${ep.feed_id}" data-ep-scroll="${ep.id}">${escHTML(ep.title || "Untitled")}</div>
                <div class="activity-sub">
                  <span class="feed-link" data-action="navigate" data-path="/feeds/${ep.feed_id}">${escHTML(ep.feed_title || "")}</span>${minsIn ? ` · ${minsIn}` : ""}
                </div>
              </div>
              ${epPlayBtn(ep)}
              <button class="btn btn-ghost btn-sm btn-icon" title="Mark as played"
                      data-action="mark-cl-played" data-ep-id="${ep.id}">
                ${svg('<polyline points="20 6 9 17 4 12"/>')}
              </button>
            </div>`;
          }).join("")}
        </div>
      </div>` : ""}

      <div class="card" id="catchup-card" style="margin-bottom:12px">
        <div class="card-body">${_catchupCardBody(catchupPl, catchup)}</div>
      </div>

      ${(() => {
        const buckets = [
          { key: "short",      label: "< 15 min",   eps: suggestions.short      || [] },
          { key: "medium",     label: "15–45 min",  eps: suggestions.medium     || [] },
          { key: "long",       label: "45–90 min",  eps: suggestions.long       || [] },
          { key: "extra_long", label: "90+ min",    eps: suggestions.extra_long || [] },
        ].filter(b => b.eps.length > 0);
        if (!buckets.length) return "";

        const epRow = (ep) => `
          <div class="activity-item" style="cursor:pointer"
               data-action="navigate" data-path="/feeds/${ep.feed_id}" data-ep-scroll="${ep.id}">
            <div class="activity-icon" style="flex-shrink:0">
              ${_thumb(ep.custom_image_url || ep.episode_image_url || ep.feed_image_url)}
            </div>
            <div class="activity-info" style="flex:1;min-width:0">
              <div class="activity-title truncate">${escHTML(ep.title || "Untitled")}</div>
              <div class="activity-sub">
                <span class="feed-link" data-action="navigate" data-path="/feeds/${ep.feed_id}">${escHTML(ep.feed_title || "")}</span>
              </div>
            </div>
            ${epPlayBtn(ep)}
          </div>`;

        return `
        <div class="card" style="margin-bottom:12px" id="suggested-listening-card">
          <div class="card-body">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
              <div class="section-title">Suggested Listening</div>
              <button class="btn btn-ghost btn-sm btn-icon" title="Refresh suggestions"
                      data-action="refresh-suggestions" style="flex-shrink:0">
                ${svg('<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>')}
              </button>
            </div>
            <div id="suggested-listening-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:0 24px;align-items:start">
              ${buckets.map(b => `
                <div>
                  <div style="font-size:11px;font-weight:600;color:var(--text-3);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:4px;padding-bottom:4px;border-bottom:1px solid var(--border)">${b.label}</div>
                  ${b.eps.map(epRow).join("")}
                </div>`).join("")}
            </div>
          </div>
        </div>`;
      })()}

      <div class="dash-grid">

        <div class="card">
          <div class="card-body">
            <div class="section-title">Top Backlog</div>
            ${(() => {
              const backlog = feeds
                .filter((f) => f.unplayed_count > 0)
                .sort((a, b) => b.unplayed_count - a.unplayed_count)
                .slice(0, 6);
              if (backlog.length === 0) {
                return `<div style="padding:12px 0;color:var(--text-3);font-size:13px">All caught up</div>`;
              }
              return backlog.map((f) => `
                <div class="activity-item" style="cursor:pointer" data-action="navigate" data-path="/feeds/${f.id}">
                  <div class="activity-icon">
                    ${_thumb(f.custom_image_url || f.image_url)}
                  </div>
                  <div class="activity-info">
                    <div class="activity-title truncate" style="color:var(--text)">${escHTML(f.title || f.url)}</div>
                    <div class="activity-sub">${f.unplayed_count} of ${f.downloaded_count} unplayed</div>
                  </div>
                  <div style="display:flex;flex-direction:column;align-items:center;flex-shrink:0;gap:2px">
                    ${_miniPie(Math.round(f.unplayed_count / f.downloaded_count * 100))}
                    <span style="font-size:11px;font-weight:600;color:var(--primary)">${Math.round(f.unplayed_count / f.downloaded_count * 100)}% backlogged</span>
                  </div>
                </div>`).join("");
            })()}
          </div>
        </div>
      </div>
    </div>`;

  wireSyncAllBtn("#btn-sync-all-dash");

  // Get Caught Up drops an episode the moment it is marked played (by hand or
  // by the player finishing it) and picks one back up when it is un-marked.
  window._onEpisodePlayedChanged = () => _catchupRefresh();

  // Keep Continue Listening in sync with the player while this view is rendered
  window._onPlayerEpisodeStarted = (ep) => {
    if ((ep.resumeAt || 0) > 30) _upsertCLItem(ep, ep.resumeAt);
  };
  window._onPlayerEpisodeStopped = (ep, pos) => {
    if (pos > 30) _upsertCLItem(ep, pos);
  };
}
