"use strict";

// ============================================================
// VList — windowed list rendering
// ============================================================
//
// Only the rows near the viewport exist in the DOM. Before this, opening a
// 1,627-episode feed built every row up front: 124,423 nodes, and clearing the
// filter box froze the UI for 726ms because each interaction made the browser
// reconsider the whole tree. DOM size is now a function of the viewport rather
// than of the library, so a 10,000-episode feed costs the same as a 20-episode
// one.
//
// Three decisions worth understanding before changing anything here:
//
//   Nodes are mounted and discarded, never pooled. What was expensive is
//   *having* the nodes resident, not building ~30 of them per scroll frame.
//   Pooling is also precisely what breaks this codebase's idioms — handlers
//   read state out of `dataset`, dropdowns are absolutely positioned inside
//   their row, and focus lives on a real element. Recycling a node under any
//   of those silently corrupts it, so we don't.
//
//   Geometry comes from two spacer elements, not absolute positioning. The
//   hosts are a flex column (.episode-list), a real CSS grid (.feeds-grid) and
//   a <tbody>; absolute positioning would destroy all three layouts. Spacers
//   leave the host's own layout completely intact.
//
//   Heights are measured, never assumed. Rows are near-uniform at any one
//   width (87px for 90% of episode rows at 1440px) but the value changes with
//   viewport width (130px at 400px), and an open notes panel takes a single
//   row to min(800px, 60vh). So we seed from a measured sample and correct as
//   real rows land.
//
// `render` returns an HTML string, because episodeRow/renderDLRow/feedCard/
// _feedRow already do. No row template had to change to adopt this.

const VList = (() => {
  const _instances = new Set();

  // The shapes differ only in how a spacer is built and how many items sit in
  // one logical row. Everything else — measurement, windowing, pinning — is
  // shared.
  const SHAPES = {
    block: {
      spacer() {
        const d = document.createElement("div");
        d.className = "vlist-spacer";
        return d;
      },
      setSpacer(el, h) { el.style.height = h + "px"; },
      perRow() { return 1; },
    },
    grid: {
      spacer() {
        const d = document.createElement("div");
        d.className = "vlist-spacer";
        d.style.gridColumn = "1 / -1";
        return d;
      },
      setSpacer(el, h) { el.style.height = h + "px"; },
      dynamicCols: true,
      // auto-fill means the column count is viewport-derived, so it has to be
      // read back rather than assumed. Returns "none" when the host holds only
      // spacers on first paint, which would otherwise give a wildly wrong
      // single-column layout that only fixes itself on the next resize.
      perRow(host) {
        const tracks = getComputedStyle(host).gridTemplateColumns;
        if (!tracks || tracks === "none") return 0;
        return Math.max(1, tracks.split(" ").filter(Boolean).length);
      },
    },
    table: {
      spacer(host) {
        const tr = document.createElement("tr");
        tr.className = "vlist-spacer";
        const td = document.createElement("td");
        td.colSpan = host.__vlistCols || 1;
        td.style.cssText = "padding:0;border:0";
        tr.appendChild(td);
        return tr;
      },
      setSpacer(el, h) { el.firstChild.style.height = h + "px"; },
      perRow() { return 1; },
    },
  };

  class Instance {
    constructor(host, opts) {
      this.host = host;
      this.shape = SHAPES[opts.shape || "block"];
      this.keyOf = opts.key || ((it) => it.id);
      this.render = opts.render;
      this.onMount = opts.onMount || null;
      this.emptyHTML = opts.emptyHTML || "";
      this.overscan = opts.overscan != null ? opts.overscan : 6;
      this.scroller = opts.scrollParent || document.getElementById("content");

      this.items = [];
      this.heights = [];        // per logical row
      this.prefix = [0];        // prefix[i] = offset of logical row i
      this.prefixDirty = true;
      // Must be non-zero before the first paint. With a zero estimate every
      // prefix offset is zero, the binary search below returns the *last* row
      // for any scroll position, and the window opens at the wrong end of the
      // list. The exact value does not matter — the first real measurement
      // replaces it — but it has to be a plausible row height, not nothing.
      this.seedH = opts.estimateHeight || 60;
      this.estH = this.seedH;
      this.measured = 0;
      this._lastWidth = 0;
      this.mounted = new Map(); // key -> node
      this.pinned = new Set();  // keys that must stay mounted
      this.perRow = 1;
      this.range = [0, -1];
      this.destroyed = false;

      if (opts.cols) host.__vlistCols = opts.cols;

      // Chrome's scroll anchoring tries to compensate when content above the
      // viewport resizes — which is exactly what a spacer does every frame.
      // Left on, it fights the window and produces judder that is very hard to
      // attribute back to here.
      host.style.overflowAnchor = "none";
      if (this.scroller) this.scroller.style.overflowAnchor = "none";

      this.topSpacer = this.shape.spacer(host);
      this.botSpacer = this.shape.spacer(host);

      this._onScroll = () => this._schedule();
      this.scroller?.addEventListener("scroll", this._onScroll, { passive: true });

      // Width changes change row heights, so every measurement is void.
      this._ro = new ResizeObserver(() => this._onResize());
      this._ro.observe(host);

      this._frame = 0;
      _instances.add(this);
      this.setItems(opts.items || []);
    }

    // ---- geometry ------------------------------------------------------

    get rowCount() {
      return this.perRow > 0 ? Math.ceil(this.items.length / this.perRow) : 0;
    }

    _rebuildPrefix() {
      const n = this.rowCount;
      const p = new Array(n + 1);
      p[0] = 0;
      for (let i = 0; i < n; i++) {
        p[i + 1] = p[i] + (this.heights[i] || this.estH);
      }
      this.prefix = p;
      this.prefixDirty = false;
    }

    _offsets() {
      if (this.prefixDirty) this._rebuildPrefix();
      return this.prefix;
    }

    _rowAtOffset(y) {
      const p = this._offsets();
      let lo = 0, hi = p.length - 1;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (p[mid] <= y) lo = mid; else hi = mid - 1;
      }
      return lo;
    }

    // Where the host starts inside the scroll container. Recomputed rather
    // than cached across renders because headers above it (the feed header,
    // the filter bar) can change height independently.
    _hostTop() {
      if (!this.scroller) return 0;
      const h = this.host.getBoundingClientRect();
      const s = this.scroller.getBoundingClientRect();
      return (h.top - s.top) + this.scroller.scrollTop;
    }

    // ---- windowing -----------------------------------------------------

    _schedule() {
      if (this.destroyed || this._frame) return;
      this._frame = requestAnimationFrame(() => {
        this._frame = 0;
        this._paint();
      });
    }

    _paint() {
      if (this.destroyed || !this.host.isConnected) return;

      if (this.shape.dynamicCols) {
        const pr = this.shape.perRow(this.host);
        // 0 means the grid could not be measured yet (only spacers present).
        // Try again next frame rather than committing to a wrong layout.
        if (pr === 0) { this._schedule(); return; }
        if (pr !== this.perRow) { this.perRow = pr; this.heights = []; this.prefixDirty = true; }
      }

      const n = this.rowCount;
      if (n === 0) { this._commit(0, -1); return; }

      const p = this._offsets();
      const viewTop = Math.max(0, (this.scroller?.scrollTop || 0) - this._hostTop());
      const viewH = this.scroller?.clientHeight || 0;

      let first = this._rowAtOffset(viewTop) - this.overscan;
      let last = this._rowAtOffset(viewTop + viewH) + this.overscan;
      first = Math.max(0, first);
      last = Math.min(n - 1, last);

      this._commit(first, last);
      this.shape.setSpacer(this.topSpacer, p[first] || 0);
      this.shape.setSpacer(this.botSpacer, Math.max(0, p[n] - p[Math.min(n, last + 1)]));
    }

    _commit(first, last) {
      const host = this.host;
      const wanted = new Map();
      for (let r = first; r <= last; r++) {
        for (let c = 0; c < this.perRow; c++) {
          const i = r * this.perRow + c;
          if (i >= this.items.length) break;
          wanted.set(this.keyOf(this.items[i]), i);
        }
      }

      // Unmount what fell out, unless something in it must survive: an open
      // dropdown, the focused element, or an explicit pin (a row mid removal
      // animation). Pinning is one mechanism for all three — cheaper and more
      // reliable than saving and restoring focus, which has to re-find "the
      // same button" afterwards and fights :focus-visible.
      for (const [key, node] of this.mounted) {
        if (wanted.has(key)) continue;
        if (this._mustKeep(key, node)) continue;
        node.remove();
        this.mounted.delete(key);
      }

      const fresh = [];
      for (const [key, i] of wanted) {
        if (this.mounted.has(key)) continue;
        const node = this._build(this.items[i], i);
        if (node) { this.mounted.set(key, node); fresh.push([key, node, i]); }
      }

      // Rows must sit between the spacers in index order. This runs on every
      // commit, not only when something new was built: when a list is merely
      // reordered — a playlist drag, a re-sort, the downloads poll — the set of
      // keys is unchanged, so nothing mounts and nothing unmounts, and gating
      // this on newly-built nodes left the DOM in its old order while the model
      // held the new one. insertBefore is skipped for nodes already in place,
      // so the common case where nothing moved costs one pass and no mutations.
      const ordered = [...this.mounted.entries()]
        .map(([k, node]) => [wanted.has(k) ? wanted.get(k) : Infinity, node])
        .sort((a, b) => a[0] - b[0]);
      let ref = this.botSpacer;
      for (let j = ordered.length - 1; j >= 0; j--) {
        const node = ordered[j][1];
        if (node.nextSibling !== ref) host.insertBefore(node, ref);
        ref = node;
      }

      this.range = [first, last];
      if (fresh.length) {
        this._measure(first, last);
        if (this.onMount) this.onMount(fresh.map((f) => f[1]), fresh.map((f) => this.items[f[2]]));
      }
    }

    _mustKeep(key, node) {
      if (this.pinned.has(key)) return true;
      if (node.querySelector && node.querySelector("[data-open]")) return true;
      const active = document.activeElement;
      return !!(active && active !== document.body && node.contains(active));
    }

    _build(item, i) {
      const html = this.render(item, i);
      if (!html) return null;
      if (this.shape === SHAPES.table) {
        const tpl = document.createElement("tbody");
        tpl.innerHTML = html;
        return tpl.firstElementChild;
      }
      const tpl = document.createElement("template");
      tpl.innerHTML = html.trim();
      return tpl.content.firstElementChild;
    }

    // Real heights replace the estimate as rows land. Pinned rows are skipped:
    // animateRemove() writes an inline height onto a row while collapsing it,
    // and recording that as the row's natural height would poison the estimate.
    _measure(first, last) {
      let changed = false;
      for (let r = first; r <= last; r++) {
        let h = 0;
        for (let c = 0; c < this.perRow; c++) {
          const i = r * this.perRow + c;
          if (i >= this.items.length) break;
          const node = this.mounted.get(this.keyOf(this.items[i]));
          if (!node || this.pinned.has(this.keyOf(this.items[i]))) continue;
          const rect = node.getBoundingClientRect().height;
          const mb = parseFloat(getComputedStyle(node).marginBottom) || 0;
          h = Math.max(h, rect + mb);
        }
        if (h > 0 && Math.abs((this.heights[r] || 0) - h) > 0.5) {
          this.heights[r] = h;
          changed = true;
          if (this.measured < 20) {
            this.measured++;
            this.estH = this.estH ? (this.estH * (this.measured - 1) + h) / this.measured : h;
          }
        }
      }
      if (changed) { this.prefixDirty = true; this._schedule(); }
    }

    // Only a *width* change invalidates measurements — height changes are the
    // list itself growing. Guarding on width also absorbs the callback that
    // ResizeObserver fires the moment observe() is called, which would
    // otherwise discard the seed estimate before the first paint and leave
    // every offset at zero.
    _onResize() {
      const w = this.host.clientWidth;
      if (w === this._lastWidth) return;
      this._lastWidth = w;
      this.heights = [];
      this.measured = 0;
      this.estH = this.seedH;
      this.prefixDirty = true;
      this._schedule();
    }

    // ---- public --------------------------------------------------------

    setItems(items) {
      this.items = items || [];
      const host = this.host;

      if (!host.contains(this.topSpacer)) {
        host.innerHTML = "";
        host.appendChild(this.topSpacer);
        host.appendChild(this.botSpacer);
        this.mounted.clear();
      }

      if (this.items.length === 0) {
        for (const [, node] of this.mounted) node.remove();
        this.mounted.clear();
        this.pinned.clear();
        if (this.emptyHTML && !host.querySelector(".vlist-empty")) {
          const d = document.createElement("div");
          d.className = "vlist-empty";
          d.innerHTML = this.emptyHTML;
          host.insertBefore(d, this.botSpacer);
        }
      } else {
        host.querySelector(".vlist-empty")?.remove();
      }

      // Keys that no longer exist can never be unmounted by the window loop.
      const live = new Set(this.items.map(this.keyOf));
      for (const [key, node] of this.mounted) {
        if (!live.has(key)) { node.remove(); this.mounted.delete(key); this.pinned.delete(key); }
      }

      this.heights.length = this.rowCount;
      this.prefixDirty = true;
      this._paint();
      return this;
    }

    // Re-render one row in place if it is mounted; a no-op otherwise, because
    // the model already holds the new value and the row will render from it
    // when it next scrolls in.
    invalidate(key) {
      const node = this.mounted.get(key);
      if (!node) return this;
      const i = this.items.findIndex((it) => this.keyOf(it) === key);
      if (i === -1) return this;
      const next = this._build(this.items[i], i);
      if (!next) return this;
      node.replaceWith(next);
      this.mounted.set(key, next);
      if (this.onMount) this.onMount([next], [this.items[i]]);
      this.prefixDirty = true;
      this._schedule();
      return this;
    }

    indexOf(key) {
      return this.items.findIndex((it) => this.keyOf(it) === key);
    }

    // Jumping to a row far outside the current window is necessarily iterative.
    // Every offset past the measured region is an estimate, so the first scroll
    // lands somewhere approximate; mounting rows there measures them, which
    // changes the estimate, which moves the target's offset again. A single
    // correction pass is not enough — it converges over several frames, and
    // stopping early leaves the caller looking at the wrong part of the list
    // with the target not mounted at all.
    //
    // So: scroll, repaint, and repeat until the position stops moving. Instant
    // rather than smooth, because animating toward an offset that is still
    // being revised looks like a glitch.
    scrollToKey(key, { block = "center", tries = 12 } = {}) {
      const i = this.indexOf(key);
      if (i === -1 || !this.scroller) return false;
      const row = Math.floor(i / this.perRow);

      let prevTarget = null;
      const step = (remaining) => {
        if (this.destroyed || !this.host.isConnected) return;
        // Paint first so the spacers reflect the latest measurements before the
        // target is computed from them.
        this._paint();
        const q = this._offsets();
        const h = this.heights[row] || this.estH;
        const top = this._hostTop() + q[row];
        const target = Math.max(0, block === "center"
          ? top - (this.scroller.clientHeight - h) / 2
          : top);
        this.scroller.scrollTop = target;
        this._paint();

        // Convergence is judged on the computed target, not on scrollTop.
        // Early on the whole list is estimated, so the content is shorter than
        // it will turn out to be and the browser clamps scrollTop to the
        // current scrollHeight. Comparing the clamped values makes two
        // different targets look identical and stops the loop while the window
        // is still nowhere near the row.
        const settled = prevTarget !== null && Math.abs(target - prevTarget) <= 2;
        prevTarget = target;
        if (remaining > 0 && !settled) {
          requestAnimationFrame(() => step(remaining - 1));
        }
      };
      step(tries);
      return true;
    }

    pin(key) { this.pinned.add(key); return this; }
    unpin(key) { this.pinned.delete(key); this._schedule(); return this; }
    node(key) { return this.mounted.get(key) || null; }

    destroy() {
      if (this.destroyed) return;
      this.destroyed = true;
      if (this._frame) cancelAnimationFrame(this._frame);
      this.scroller?.removeEventListener("scroll", this._onScroll);
      this._ro?.disconnect();
      this.mounted.clear();
      this.pinned.clear();
      _instances.delete(this);
    }
  }

  return {
    mount(host, opts) { return new Instance(host, opts); },

    // #content is never itself replaced on navigation — router.js swaps only
    // its innerHTML — so a scroll listener registered on it outlives the view
    // that created it and keeps firing against a detached host forever. The
    // router calls this in the same cleanup block that stops the pollers.
    destroyAll() {
      for (const inst of [..._instances]) inst.destroy();
    },

    get count() { return _instances.size; },
  };
})();
