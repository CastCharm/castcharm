"use strict";

// ============================================================
// Settings view
// ============================================================
async function viewSettings() {
  const [settings, id3Tags, rssSources, authStatus] = await Promise.all([
    API.getSettings(),
    API.getID3Tags(),
    API.getRSSSources(),
    API.getAuthStatus(),
  ]);

  const content = document.getElementById("content");
  content.innerHTML = `
    <div class="page" style="max-width:700px">
      <div class="page-header">
        <div>
          <div class="page-title">Settings</div>
          <div class="page-subtitle">Global defaults — overridable per-feed</div>
        </div>
      </div>

      <form id="global-settings-form">

        <!-- Appearance -->
        <div class="panel open" id="panel-appearance">
          <div class="panel-header" onclick="togglePanel('panel-appearance')">
            <div class="panel-header-title">
              ${svg('<circle cx="12" cy="12" r="3"/><path d="M12 2a10 10 0 1 0 10 10"/><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4"/>', 'width="16" height="16"')}
              Appearance
            </div>
            <svg class="panel-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="6 9 12 15 18 9"/>
            </svg>
          </div>
          <div class="panel-body">
            <div class="form-group">
              <label class="form-label">Theme</label>
              <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:6px">
                ${Object.entries(THEMES).map(([id, t]) => {
                  const active = (settings.theme || "midnight") === id;
                  return `<button type="button" data-theme-btn="${id}" onclick="selectTheme('${id}')"
                    style="background:${t.bg2};border:2px solid ${active ? t.primary : "transparent"};border-radius:10px;padding:10px 12px;cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:6px;min-width:72px;outline:none;transition:border-color 0.15s ease">
                    <div style="width:26px;height:26px;border-radius:50%;background:${t.primary}"></div>
                    <span style="font-size:11px;color:${t.labelColor};font-weight:500;white-space:nowrap">${t.label}</span>
                  </button>`;
                }).join("")}
              </div>
            </div>
            ${toggle("Show Suggested Listening", "show_suggested_listening",
              settings.show_suggested_listening ?? true,
              "Shows up to 3 random unplayed downloaded episodes per duration category on the dashboard.")}
          </div>
        </div>

        <!-- Storage -->
        <div class="panel open" id="panel-storage">
          <div class="panel-header" onclick="togglePanel('panel-storage')">
            <div class="panel-header-title">
              ${svg('<path d="M22 12H2"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>', 'width="16" height="16"')}
              Storage
            </div>
            <svg class="panel-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="6 9 12 15 18 9"/>
            </svg>
          </div>
          <div class="panel-body">
            <div class="form-group">
              <label class="form-label">Download Path</label>
              <input class="form-control" name="download_path" value="${settings.download_path}" />
              <div class="form-hint">Absolute path inside the container. Map this to a host directory via docker-compose volumes.</div>
            </div>
            <div class="form-group">
              <label class="form-label">Max Concurrent Downloads</label>
              <input class="form-control" name="max_concurrent_downloads" type="number"
                     min="1" max="10" value="${settings.max_concurrent_downloads}" data-numeric="1"
                     style="max-width:120px" />
            </div>
            <div class="form-group">
              <label class="form-label">Keep Latest Episodes (Global)</label>
              <label style="display:flex;align-items:center;gap:8px;margin-bottom:10px;cursor:pointer;color:var(--text-2)">
                <input type="checkbox" id="chk-keep-latest" style="width:16px;height:16px;cursor:pointer"
                       ${settings.keep_latest ? "checked" : ""}
                       onchange="document.getElementById('keep-latest-cfg').style.display=this.checked?'':'none'" />
                Enable auto-cleanup
              </label>
              <div id="keep-latest-cfg" style="${settings.keep_latest ? "" : "display:none"}">
                <input class="form-control" name="keep_latest" type="number"
                       min="1" value="${settings.keep_latest ?? 10}" data-numeric="1"
                       style="max-width:120px;margin-bottom:6px" />
                <div class="form-hint" style="color:var(--warning);margin-bottom:12px">
                  ⚠ Only the N most recently downloaded episodes per podcast are kept. Older files are deleted automatically after each sync.
                </div>
                ${toggle("Keep all unplayed episodes", "keep_unplayed",
                  settings.keep_unplayed ?? true,
                  "When Keep Latest is active, unplayed episodes are protected from cleanup regardless of age or count. Only played episodes count toward the limit.")}
              </div>
            </div>
            <div class="form-group">
              <label class="form-label">Episode Page Size</label>
              <input class="form-control" name="episode_page_size" type="number"
                     min="10" value="${settings.episode_page_size ?? 10000}"
                     data-numeric="1" style="max-width:160px" />
              <div class="form-hint">How many episodes to load at a time on the feed page. A "Load more" button appears when there are additional episodes. Default is 10,000 (effectively loads all but handles extremely large feeds).</div>
            </div>
            <div class="form-group">
              <label class="form-label">Max Log Entries</label>
              <input class="form-control" name="log_max_entries" type="number"
                     min="50" max="10000" value="${settings.log_max_entries ?? 500}" data-numeric="1"
                     style="max-width:120px" />
              <div class="form-hint">Maximum number of log lines kept in memory. Older entries are dropped when the limit is reached.</div>
            </div>
          </div>
        </div>

        <!-- File options -->
        <div class="panel open" id="panel-files">
          <div class="panel-header" onclick="togglePanel('panel-files')">
            <div class="panel-header-title">
              ${svg('<path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/>', 'width="16" height="16"')}
              File Options
            </div>
            <svg class="panel-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="6 9 12 15 18 9"/>
            </svg>
          </div>
          <div class="panel-body">
            <div class="form-group">
              <label class="form-label">Timezone</label>
              <div class="tz-combo">
                <input class="form-control" type="text" id="settings-tz-input"
                       autocomplete="off" spellcheck="false"
                       value="${escHTML(settings.timezone || "UTC")}"
                       placeholder="Search timezones…" />
                <div class="tz-dropdown" id="settings-tz-dropdown"></div>
              </div>
              <div class="form-hint" style="margin-top:4px">Used for date prefixes in filenames and year-based folder organization.</div>
            </div>

            ${toggle("Date prefix in filename (YYYY-MM-DD)", "filename_date_prefix",
              settings.filename_date_prefix,
              "Prepends the episode publication date to every filename.")}

            ${toggle("Episode number prefix in filename", "filename_episode_number",
              settings.filename_episode_number,
              "Prepends a zero-padded sequential number to filenames (e.g. 042 Title.mp3). Oldest episode = 1.")}

            ${toggle("Organize into year subfolders", "organize_by_year",
              settings.organize_by_year,
              "Episodes are filed into per-year subdirectories, e.g. Podcast Name/2024/Episode.mp3")}

            ${toggle("Save XML metadata sidecar", "save_xml",
              settings.save_xml,
              "Saves a .xml file adjacent to each audio file containing full RSS metadata.")}
          </div>
        </div>

        <!-- Playback -->
        <div class="panel open" id="panel-playback">
          <div class="panel-header" onclick="togglePanel('panel-playback')">
            <div class="panel-header-title">
              ${svg('<path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/>', 'width="16" height="16"')}
              Playback
            </div>
            <svg class="panel-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="6 9 12 15 18 9"/>
            </svg>
          </div>
          <div class="panel-body">
            <div class="form-group">
              <label class="form-label">Auto-mark Played Threshold (%)</label>
              <input class="form-control" name="auto_played_threshold" type="number"
                     min="0" max="100" value="${settings.auto_played_threshold ?? 95}" data-numeric="1"
                     style="max-width:120px" />
              <div class="form-hint">Mark an episode as played when this % of its duration has been listened to. Set to 0 to only mark played when the audio ends naturally. Default: 95%.</div>
            </div>
          </div>
        </div>

        <!-- Scheduling -->
        <div class="panel open" id="panel-schedule">
          <div class="panel-header" onclick="togglePanel('panel-schedule')">
            <div class="panel-header-title">
              ${svg('<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>', 'width="16" height="16"')}
              Scheduling
            </div>
            <svg class="panel-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="6 9 12 15 18 9"/>
            </svg>
          </div>
          <div class="panel-body">

            <!-- Feed Sync -->
            <div class="form-section-label">Feed Sync</div>

            ${toggle("Use daily sync schedule", "scheduled_sync_enabled",
              settings.scheduled_sync_enabled ?? false,
              "Sync all feeds once per day at a specific time instead of on a recurring interval.")}
            <div id="daily-sync-cfg" style="${settings.scheduled_sync_enabled ? "" : "display:none"}">
              <div class="form-group" style="margin-left:40px">
                <label class="form-label">Sync Time</label>
                <input class="form-control" name="scheduled_sync_time" type="time"
                       value="${settings.scheduled_sync_time || "03:00"}"
                       style="max-width:140px" />
                <div class="form-hint">All feeds will be checked once per day at this time (in your configured timezone).</div>
              </div>
            </div>

            <div id="interval-sync-cfg" style="${settings.scheduled_sync_enabled ? "display:none" : ""}">
              <div class="form-group">
                <label class="form-label">Check Interval (minutes)</label>
                <input class="form-control" name="check_interval" type="number"
                       min="1" value="${settings.check_interval}" data-numeric="1"
                       style="max-width:150px" />
                <div class="form-hint">How often to check all feeds for new episodes. Individual feeds can override this.</div>
              </div>
            </div>

            ${toggle("Auto-download new episodes", "auto_download_new",
              settings.auto_download_new,
              "Automatically queue new episodes for download when first detected (does not apply to the initial import when a feed is added.")}

            <hr style="border:none;border-top:1px solid var(--border);margin:16px 0" />

            <!-- Download Window -->
            <div class="form-section-label">Download Window</div>

            ${toggle("Restrict downloads to a time window", "download_window_enabled",
              settings.download_window_enabled ?? false,
              "When enabled, downloads only run during the specified window. Queued episodes wait until the window opens. In-progress downloads will finish.")}
            <div id="download-window-cfg" style="${settings.download_window_enabled ? "" : "display:none"}">
              <div style="display:flex;gap:12px;align-items:center;margin-left:40px;margin-bottom:12px;flex-wrap:wrap">
                <div class="form-group" style="margin-bottom:0">
                  <label class="form-label">Start</label>
                  <input class="form-control" name="download_window_start" type="time"
                         value="${settings.download_window_start || "21:00"}"
                         style="max-width:140px" />
                </div>
                <div class="form-group" style="margin-bottom:0">
                  <label class="form-label">End</label>
                  <input class="form-control" name="download_window_end" type="time"
                         value="${settings.download_window_end || "06:00"}"
                         style="max-width:140px" />
                </div>
              </div>
              <div class="form-hint" style="margin-left:40px">Times are in your configured timezone. Overnight windows (e.g. 21:00–06:00) are supported.</div>
            </div>

            <hr style="border:none;border-top:1px solid var(--border);margin:16px 0" />

            <!-- Maintenance -->
            <div class="form-section-label">Maintenance</div>

            ${toggle("Regenerate feed XML daily", "scheduled_xml_enabled",
              settings.scheduled_xml_enabled ?? true,
              "Rebuild the complete castcharm.xml feed file for all podcasts at a scheduled time.")}
            <div id="xml-regen-cfg" style="${settings.scheduled_xml_enabled !== false ? "" : "display:none"}">
              <div class="form-group" style="margin-left:40px">
                <label class="form-label">XML Regeneration Time</label>
                <input class="form-control" name="scheduled_xml_time" type="time"
                       value="${settings.scheduled_xml_time || "00:00"}"
                       style="max-width:140px" />
              </div>
            </div>

            ${toggle("Export OPML daily", "scheduled_opml_enabled",
              settings.scheduled_opml_enabled ?? true,
              "Save castcharm-export.opml to the root of the download folder at a scheduled time.")}
            <div id="opml-export-cfg" style="${settings.scheduled_opml_enabled !== false ? "" : "display:none"}">
              <div class="form-group" style="margin-left:40px">
                <label class="form-label">OPML Export Time</label>
                <input class="form-control" name="scheduled_opml_time" type="time"
                       value="${settings.scheduled_opml_time || "00:00"}"
                       style="max-width:140px" />
              </div>
            </div>

          </div>
        </div>

        <!-- Default ID3 mapping -->
        <div class="panel" id="panel-id3">
          <div class="panel-header" onclick="togglePanel('panel-id3')">
            <div class="panel-header-title">
              ${svg('<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>', 'width="16" height="16"')}
              Default ID3 Tag Mapping
            </div>
            <svg class="panel-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="6 9 12 15 18 9"/>
            </svg>
          </div>
          <div class="panel-body">
            <div class="form-hint" style="margin-bottom:12px">
              These mappings are used when a feed has ID3 tagging enabled but no custom mapping configured.
            </div>
            <div style="overflow-x:auto">
              <table class="id3-table">
                <thead>
                  <tr>
                    <th>ID3 Tag</th>
                    <th>Source Field</th>
                  </tr>
                </thead>
                <tbody>
                  ${id3Tags.map((tag) => {
                    const selected = (settings.default_id3_mapping || {})[tag.tag] || "";
                    return `<tr>
                      <td><strong>${tag.tag}</strong><br><span style="color:var(--text-3);font-size:11px">${tag.label}</span></td>
                      <td>
                        <select class="form-control" name="id3_${tag.tag}" style="font-size:12.5px">
                          <option value="">— skip —</option>
                          ${rssSources.map((s) =>
                            `<option value="${s.field}" ${selected === s.field ? "selected" : ""}>${s.label}</option>`
                          ).join("")}
                        </select>
                      </td>
                    </tr>`;
                  }).join("")}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <!-- Security -->
        <div class="panel" id="panel-security">
          <div class="panel-header" onclick="togglePanel('panel-security')">
            <div class="panel-header-title">
              ${svg('<rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>', 'width="16" height="16"')}
              Security
            </div>
            <svg class="panel-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="6 9 12 15 18 9"/>
            </svg>
          </div>
          <div class="panel-body">
            ${authStatus.auth_enabled ? `
              <p style="color:var(--text-2);font-size:13px;margin-bottom:16px">
                Login is <strong style="color:var(--success)">enabled</strong>.
                Change your credentials below, or disable login entirely.
              </p>
              <div class="form-group">
                <label class="form-label">Current Password</label>
                <input class="form-control" id="sec-current-pw" type="password"
                       autocomplete="current-password" style="max-width:300px"
                       placeholder="Required to make changes" />
              </div>
              <div class="form-group">
                <label class="form-label">New Username</label>
                <input class="form-control" id="sec-new-user" type="text"
                       autocomplete="username" style="max-width:300px" />
              </div>
              <div class="form-group">
                <label class="form-label">New Password</label>
                <input class="form-control" id="sec-new-pw" type="password"
                       autocomplete="new-password" style="max-width:300px"
                       placeholder="Minimum 8 characters" />
              </div>
              <div class="form-group">
                <label class="form-label">Confirm New Password</label>
                <input class="form-control" id="sec-confirm-pw" type="password"
                       autocomplete="new-password" style="max-width:300px" />
              </div>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                <button type="button" class="btn btn-primary" onclick="_secUpdateCredentials()">
                  Update Credentials
                </button>
                <button type="button" class="btn btn-ghost" style="color:var(--error)"
                        onclick="_secDisableAuth()">
                  Disable Login
                </button>
              </div>
            ` : `
              <p style="color:var(--text-2);font-size:13px;margin-bottom:16px">
                Login is <strong style="color:var(--text-3)">disabled</strong>.
                Set up a username and password to restrict access to this instance.
              </p>
              <div class="form-group">
                <label class="form-label">Username</label>
                <input class="form-control" id="sec-new-user" type="text"
                       autocomplete="username" style="max-width:300px" />
              </div>
              <div class="form-group">
                <label class="form-label">Password</label>
                <input class="form-control" id="sec-new-pw" type="password"
                       autocomplete="new-password" style="max-width:300px"
                       placeholder="Minimum 8 characters" />
              </div>
              <div class="form-group">
                <label class="form-label">Confirm Password</label>
                <input class="form-control" id="sec-confirm-pw" type="password"
                       autocomplete="new-password" style="max-width:300px" />
              </div>
              <button type="button" class="btn btn-primary" onclick="_secEnableAuth()">
                Enable Login
              </button>
            `}
          </div>
        </div>

<div style="display:flex;gap:10px;margin-top:4px">
          <button type="submit" class="btn btn-primary">Save Settings</button>
        </div>
      </form>
    </div>`;

  const form = document.getElementById("global-settings-form");

  // Populate timezone selector
  _initSettingsTzSelect(settings.timezone || "UTC");

  // Wire toggle visibility for scheduling sub-sections
  _wireToggleVisibility(form, "scheduled_sync_enabled", "daily-sync-cfg", "interval-sync-cfg");
  _wireToggleVisibility(form, "download_window_enabled", "download-window-cfg");
  _wireToggleVisibility(form, "scheduled_xml_enabled", "xml-regen-cfg");
  _wireToggleVisibility(form, "scheduled_opml_enabled", "opml-export-cfg");

  // Dirty tracking — reset on each visit, set on any change
  window._settingsDirty = false;
  form.addEventListener("change", () => { window._settingsDirty = true; });

  // Expose save logic so the navigation guard can call it programmatically
  window._settingsSave = async function() {
    const raw = collectForm(form);
    const id3Mapping = {};
    for (const [k, v] of Object.entries(raw)) {
      if (k.startsWith("id3_") && v) id3Mapping[k.slice(4)] = v;
    }
    const payload = {
      download_path: raw.download_path,
      check_interval: Number(raw.check_interval) || 60,
      max_concurrent_downloads: Number(raw.max_concurrent_downloads) || 2,
      filename_date_prefix: raw.filename_date_prefix ?? false,
      filename_episode_number: raw.filename_episode_number ?? true,
      organize_by_year: raw.organize_by_year ?? false,
      save_xml: raw.save_xml ?? false,
      auto_download_new: raw.auto_download_new ?? true,
      default_id3_mapping: id3Mapping,
      log_max_entries: Number(raw.log_max_entries) || 500,
      episode_page_size: Number(raw.episode_page_size) || 10000,
      keep_latest: document.getElementById("chk-keep-latest")?.checked && raw.keep_latest
        ? Number(raw.keep_latest) : null,
      keep_unplayed: raw.keep_unplayed ?? true,
      auto_played_threshold: Number(raw.auto_played_threshold) ?? 95,
      show_suggested_listening: raw.show_suggested_listening ?? true,
      timezone: document.getElementById("settings-tz-input")?.value || "UTC",
      scheduled_sync_enabled: raw.scheduled_sync_enabled ?? false,
      scheduled_sync_time: raw.scheduled_sync_time || "03:00",
      scheduled_xml_enabled: raw.scheduled_xml_enabled ?? true,
      scheduled_xml_time: raw.scheduled_xml_time || "00:00",
      scheduled_opml_enabled: raw.scheduled_opml_enabled ?? true,
      scheduled_opml_time: raw.scheduled_opml_time || "00:00",
      download_window_enabled: raw.download_window_enabled ?? false,
      download_window_start: raw.download_window_start || "21:00",
      download_window_end: raw.download_window_end || "06:00",
    };
    try {
      await API.putSettings(payload);
      Player.setThreshold(payload.auto_played_threshold);
      window._settingsDirty = false;
      Toast.success("Settings saved");
      return true;
    } catch (err) {
      Toast.error(err.message);
      return false;
    }
  };

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    await window._settingsSave();
  });

}

// ── Schedule toggle visibility ───────────────────────────────────────────────

function _wireToggleVisibility(form, checkboxName, showId, hideId) {
  const cb = form.querySelector(`input[name="${checkboxName}"]`);
  if (!cb) return;
  cb.addEventListener("change", () => {
    const showEl = document.getElementById(showId);
    if (showEl) showEl.style.display = cb.checked ? "" : "none";
    if (hideId) {
      const hideEl = document.getElementById(hideId);
      if (hideEl) hideEl.style.display = cb.checked ? "none" : "";
    }
  });
}

// ── Timezone selector ────────────────────────────────────────────────────────

async function _initSettingsTzSelect(current) {
  const inputEl    = document.getElementById("settings-tz-input");
  const dropdownEl = document.getElementById("settings-tz-dropdown");
  if (!inputEl || !dropdownEl) return;
  try {
    const tzData = await API.getTimezones();
    const allZones = (tzData.timezones || []).sort();
    initTzCombo(inputEl, dropdownEl, allZones, current, (val) => {
      window._settingsDirty = true;
    });
  } catch (_) {
    // If fetch fails, the input still shows the current value and can be edited manually
  }
}

// ── Security panel helpers ───────────────────────────────────────────────────

window._secUpdateCredentials = async function() {
  const currentPw = document.getElementById("sec-current-pw")?.value || "";
  const newUser = (document.getElementById("sec-new-user")?.value || "").trim();
  const newPw = document.getElementById("sec-new-pw")?.value || "";
  const confirmPw = document.getElementById("sec-confirm-pw")?.value || "";
  if (!newUser) { Toast.error("Username is required"); return; }
  if (newPw.length < 8) { Toast.error("Password must be at least 8 characters"); return; }
  if (newPw !== confirmPw) { Toast.error("Passwords do not match"); return; }
  try {
    await API.updateCredentials(currentPw, newUser, newPw);
    Toast.success("Credentials updated");
    document.getElementById("sec-current-pw").value = "";
    document.getElementById("sec-new-pw").value = "";
    document.getElementById("sec-confirm-pw").value = "";
  } catch (err) { Toast.error(err.message); }
};

window._secEnableAuth = async function() {
  const newUser = (document.getElementById("sec-new-user")?.value || "").trim();
  const newPw = document.getElementById("sec-new-pw")?.value || "";
  const confirmPw = document.getElementById("sec-confirm-pw")?.value || "";
  if (!newUser) { Toast.error("Username is required"); return; }
  if (newPw.length < 8) { Toast.error("Password must be at least 8 characters"); return; }
  if (newPw !== confirmPw) { Toast.error("Passwords do not match"); return; }
  try {
    await API.updateCredentials("", newUser, newPw);
    Toast.success("Login enabled");
    // Re-render settings so the panel reflects the new auth state
    viewSettings();
  } catch (err) { Toast.error(err.message); }
};

window._secDisableAuth = async function() {
  Modal.open("Disable Login", `
    <p style="color:var(--text-2);font-size:14px;margin-bottom:20px">
      This will remove the login requirement. Anyone who can reach this instance
      will have full access. Are you sure?
    </p>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="Modal.close()">Cancel</button>
      <button class="btn btn-primary" style="background:var(--error);border-color:var(--error)"
              onclick="Modal.close();_secDoDisable()">Disable Login</button>
    </div>
  `);
};

window._secDoDisable = async function() {
  try {
    await API.disableAuth();
    Toast.success("Login disabled");
    viewSettings();
  } catch (err) { Toast.error(err.message); }
};

window.selectTheme = function (name) {
  if (!THEMES[name]) return;
  applyTheme(name);
  localStorage.setItem("cc_theme", name);
  document.querySelectorAll("[data-theme-btn]").forEach((btn) => {
    const id = btn.dataset.themeBtn;
    btn.style.borderColor = id === name ? THEMES[id].primary : "transparent";
  });
  API.putSettings({ theme: name }).catch(() => {});
};
