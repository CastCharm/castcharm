"use strict";

// ============================================================
// Themed app icon — self-contained SVG with embedded defs.
// Each call gets unique gradient/filter IDs derived from the
// CSS class so multiple instances on one page don't collide.
// ============================================================
const _ICON_PATH = "M 710.568 225.540 C 705.989 241.334, 701.226 252.462, 696.982 257.283 C 691.765 263.210, 684.244 266.960, 666.500 272.483 C 658.250 275.051, 650.873 277.353, 650.107 277.599 C 649.341 277.846, 648.899 278.233, 649.126 278.459 C 649.353 278.686, 656.157 280.927, 664.247 283.438 C 695.533 293.151, 699.844 297.398, 708.469 327 C 710.472 333.875, 712.356 340.175, 712.655 341 C 712.953 341.825, 714.961 336.650, 717.115 329.500 C 727.201 296.021, 730.886 292.220, 762.750 282.430 C 769.487 280.360, 774.997 278.403, 774.992 278.083 C 774.988 277.762, 769.025 275.793, 761.742 273.706 C 732.294 265.269, 726.762 260.269, 719.024 235.091 C 717.402 229.816, 715.486 223.268, 714.766 220.540 L 713.456 215.579 710.568 225.540 M 404.051 239.080 C 395.367 242.161, 377.312 251.155, 367.070 257.501 C 333.766 278.136, 304.302 308.427, 291.450 335.244 C 287.675 343.122, 287.159 347.016, 289.571 349.429 C 292.957 352.815, 294.385 352.360, 329.819 336.616 C 353.176 326.238, 366.550 321.148, 374.208 319.724 C 378.933 318.845, 381.034 318.959, 386.107 320.370 L 392.268 322.083 401.384 315.216 C 412.889 306.549, 417 303.711, 417 304.437 C 417 304.752, 412.213 312.195, 406.362 320.976 L 395.723 336.942 394.811 345.721 C 391.250 379.987, 388.318 399.702, 382.512 428.421 C 378.963 445.977, 375.708 460.664, 375.279 461.058 C 374.851 461.453, 366.603 465.790, 356.952 470.697 C 329.609 484.598, 307.547 500.063, 299.102 511.249 C 294.738 517.029, 292.321 524.157, 293.343 528.232 C 296.386 540.356, 321.687 548.558, 361.432 550.304 L 371.364 550.740 368.432 548.674 C 354.612 538.936, 353.500 529.307, 364.970 518.689 C 383.952 501.114, 427.327 479.751, 464.500 469.668 C 479.542 465.588, 493.550 462.755, 510.500 460.367 C 527.580 457.960, 565.952 457.964, 583.341 460.374 C 624.431 466.070, 654.041 479.531, 662.704 496.453 C 664.873 500.691, 665.172 502.352, 664.770 507.953 C 664.488 511.879, 663.431 516.201, 662.130 518.750 C 660.936 521.087, 660.348 523, 660.823 523 C 663.175 523, 674.123 518.544, 682.080 514.349 C 699.769 505.022, 713.069 491.849, 716.098 480.656 C 717.662 474.875, 716.736 464.347, 714.237 459.500 C 705.898 443.327, 683.007 430.865, 648.407 423.661 C 627.452 419.298, 616.864 418.500, 580 418.502 C 549.204 418.504, 538.520 419.021, 517.500 421.530 C 510.088 422.415, 517.160 420.265, 533.809 416.573 C 557.287 411.366, 567.491 409.993, 587.759 409.314 L 606.017 408.702 601.987 401.359 C 579.088 359.635, 542.851 316.687, 503.202 284.278 C 473.924 260.345, 445.777 243.275, 427.500 238.367 C 420.472 236.479, 410.527 236.782, 404.051 239.080 M 657.115 314.554 C 655.408 321.625, 650.640 329.524, 646.089 332.820 C 643.970 334.356, 639.482 336.446, 636.118 337.464 C 630.200 339.256, 628.081 341, 631.822 341 C 632.824 341, 635.846 341.750, 638.537 342.667 C 645.372 344.996, 652.110 351.961, 654.877 359.559 C 658.751 370.194, 658.601 370.034, 659.975 365.017 C 663.575 351.871, 671.724 343.643, 683.297 341.472 C 685.333 341.090, 687 340.449, 687 340.047 C 687 339.645, 685.087 338.745, 682.750 338.047 C 676.681 336.234, 671.906 333.634, 668.631 330.358 C 665.754 327.481, 661.038 319.141, 660.955 316.783 C 660.930 316.077, 660.330 314.150, 659.622 312.500 L 658.335 309.500 657.115 314.554 M 438.692 371.096 C 434.251 387.036, 436.103 385.289, 420.847 387.929 C 413.506 389.198, 406.907 390.556, 406.183 390.946 C 405.301 391.421, 408.110 394.071, 414.683 398.965 C 420.082 402.986, 424.859 406.595, 425.298 406.986 C 425.737 407.376, 424.949 412.775, 423.548 418.982 C 422.147 425.190, 421 430.886, 421 431.640 C 421 432.396, 426.254 429.190, 432.695 424.506 C 439.127 419.828, 444.980 416, 445.701 416 C 446.423 416, 451.969 418.541, 458.026 421.647 C 464.084 424.753, 469.229 427.105, 469.460 426.873 C 469.691 426.642, 468.235 421.132, 466.223 414.628 L 462.565 402.803 465.668 400.152 C 467.375 398.693, 471.546 395.137, 474.938 392.250 C 478.329 389.363, 480.968 386.928, 480.802 386.840 C 480.636 386.753, 473.793 386.190, 465.595 385.590 L 450.691 384.500 446.595 372.755 C 444.343 366.295, 442.275 361.011, 442 361.012 C 441.725 361.014, 440.236 365.551, 438.692 371.096 M 500.272 475.377 C 487.601 478.592, 473.845 488.312, 467.154 498.778 C 465.494 501.375, 462.901 507.100, 461.391 511.500 C 458.919 518.708, 458.604 521.208, 458.210 536.750 L 457.773 554 470.887 554 L 484 554 484 561 L 484 568 470.956 568 L 457.912 568 458.206 575.250 L 458.500 582.500 471 582.554 L 483.500 582.608 483.792 590.274 L 484.083 597.939 471.792 597.903 C 465.031 597.884, 458.871 597.897, 458.102 597.934 C 456.969 597.988, 456.828 599.556, 457.357 606.250 C 458.970 626.636, 466.493 640.635, 481.217 650.650 C 501.243 664.271, 529.030 664.896, 548.578 652.165 C 554.912 648.040, 563.222 639.716, 566.713 634 C 570.413 627.941, 574.076 615.302, 574.702 606.438 L 575.293 598.057 560.897 597.778 L 546.500 597.500 546.208 589.750 L 545.916 582 559.458 582 L 573 582 573 575 L 573 568 559.500 568 L 546 568 546 561 L 546 554 559.500 554 L 573 554 573 541.588 C 573 534.761, 572.336 525.704, 571.524 521.460 C 569.751 512.196, 563.691 499.340, 558.018 492.808 C 552.739 486.729, 542.208 479.774, 534 476.945 C 525.928 474.163, 508.290 473.343, 500.272 475.377 M 620.775 504.559 C 614.967 507.711, 614.554 512.835, 619.487 520.547 C 625.191 529.464, 629.819 539.370, 632.645 548.712 C 634.985 556.444, 635.316 559.302, 635.402 572.500 C 635.478 584.188, 635.089 589.046, 633.640 594.500 C 631.544 602.387, 625.893 615.041, 621.161 622.446 C 616.447 629.821, 617.085 635.441, 622.980 638.490 C 627.082 640.611, 628.471 640.403, 632.227 637.106 C 640.016 630.267, 650.586 605.408, 653.071 588.089 C 654.800 576.034, 653.756 557.849, 650.644 545.794 C 646.424 529.453, 634.407 506.873, 628.496 504.180 C 625.211 502.683, 624.134 502.736, 620.775 504.559 M 401.605 510.042 C 399.700 511.714, 396.072 516.613, 393.542 520.928 C 378.356 546.831, 375.451 578.455, 385.534 608.100 C 388.615 617.157, 395.645 630.419, 399.618 634.666 C 403.663 638.991, 409.718 639.373, 413.545 635.545 C 417.293 631.798, 416.739 628.086, 411.026 618.685 C 399.795 600.204, 395.802 580.092, 399.113 558.692 C 400.613 548.997, 405.298 536.393, 410.236 528.770 C 417.805 517.084, 418.190 513.654, 412.440 509.131 C 408.718 506.204, 405.686 506.459, 401.605 510.042 M 431.405 526.952 C 426.086 529.157, 418.046 543.510, 414.991 556.253 C 411.254 571.841, 413.019 588.195, 419.993 602.603 C 425.275 613.517, 430.391 619, 435.289 619 C 439.703 619, 443.770 615.716, 444.576 611.500 C 445.007 609.248, 444.471 607.503, 442.424 604.500 C 434.560 592.962, 431.501 582.142, 432.244 568.500 C 432.788 558.519, 435.469 550.540, 440.958 542.562 C 444.875 536.868, 444.891 531.702, 441 528.894 C 437.819 526.599, 434.082 525.842, 431.405 526.952 M 594 526.706 C 593.175 527.008, 591.487 528.176, 590.250 529.302 C 586.876 532.371, 587.380 536.409, 592.027 543.541 C 598.888 554.072, 600.470 559.691, 600.462 573.500 C 600.454 587.199, 598.935 592.410, 591.955 602.685 C 587.237 609.630, 587.039 612.193, 590.923 616.077 C 596.896 622.050, 603.966 619.067, 610.821 607.684 C 619.319 593.572, 622.279 573.448, 618.218 557.391 C 615.480 546.564, 610.480 536.539, 604.934 530.759 C 600.481 526.118, 598.062 525.222, 594 526.706 M 434.182 629.147 C 431.257 630.271, 428.636 631.784, 428.358 632.510 C 427.556 634.600, 432.206 645.036, 437.461 652.941 C 450.435 672.458, 469.035 685.496, 491.194 690.606 L 498.888 692.381 499.194 704.236 C 499.545 717.835, 500.696 716.473, 488 717.484 C 477.109 718.352, 461.912 721.007, 458.871 722.573 C 454.727 724.706, 453.697 729.695, 456.557 733.777 L 458.113 736 512.518 736 L 566.922 736 568.961 733.811 C 571.370 731.226, 571.593 727.291, 569.499 724.303 C 567.709 721.746, 559.416 719.680, 544 717.948 C 538.225 717.299, 532.938 716.609, 532.250 716.415 C 531.340 716.159, 531 712.836, 531 704.204 L 531 692.346 537.685 691.114 C 558.838 687.216, 580.611 672.603, 593.467 653.675 C 599.146 645.315, 604.305 634.303, 603.303 632.681 C 602.630 631.592, 592.449 626.998, 590.721 627.004 C 590.050 627.007, 587.815 630.291, 585.755 634.303 C 574.734 655.774, 554.560 670.707, 530.689 675.064 C 521.995 676.651, 505.866 676.390, 497 674.520 C 475.712 670.029, 455.258 653.777, 445.711 633.770 C 443.825 629.815, 441.881 627.053, 441 627.072 C 440.175 627.090, 437.107 628.024, 434.182 629.147";

function appIconSvg(cssClass) {
  const s = cssClass.replace(/\s+/g, "-");
  return `<svg class="${cssClass}" viewBox="0 0 1024 1024" aria-hidden="true" focusable="false"><defs><linearGradient id="cc-bg1-${s}" x1="0" y1="0" x2="1024" y2="1024" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="var(--icon-bg-1,#12082e)"/><stop offset="100%" stop-color="var(--icon-bg-2,#0b163a)"/></linearGradient><radialGradient id="cc-bg2-${s}" cx="512" cy="490" r="400" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="var(--icon-glow-bg,#5b21b6)" stop-opacity="0.45"/><stop offset="100%" stop-color="var(--icon-glow-bg,#5b21b6)" stop-opacity="0"/></radialGradient><linearGradient id="cc-ig-${s}" x1="800" y1="200" x2="460" y2="780" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="var(--icon-grad-a,#fef08a)"/><stop offset="45%" stop-color="var(--icon-grad-b,#9333ea)"/><stop offset="100%" stop-color="var(--icon-grad-c,#38bdf8)"/></linearGradient><filter id="cc-gl-${s}" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur in="SourceAlpha" stdDeviation="14" result="blur"/><feFlood flood-color="var(--icon-glow-color,#a855f7)" flood-opacity="0.6" result="color"/><feComposite in="color" in2="blur" operator="in" result="glow"/><feMerge><feMergeNode in="glow"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs><rect width="1024" height="1024" rx="180" fill="url(#cc-bg1-${s})"/><rect width="1024" height="1024" rx="180" fill="url(#cc-bg2-${s})"/><g filter="url(#cc-gl-${s})" transform="translate(512 512) scale(1.4) translate(-512 -512) translate(0 17)"><path d="${_ICON_PATH}" stroke="none" fill="url(#cc-ig-${s})" fill-rule="evenodd"/></g></svg>`;
}

// ============================================================
// Toast notifications
// ============================================================
const Toast = {
  show(msg, type = "info", duration = 3500) {
    const container = document.getElementById("toast-container");
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    const icons = { success: "✓", error: "✕", info: "ℹ" };
    const iconEl = document.createElement("span");
    iconEl.style.cssText = "font-weight:700;font-size:15px;flex-shrink:0";
    iconEl.textContent = icons[type] || "ℹ";
    const msgEl = document.createElement("span");
    msgEl.textContent = msg;  // textContent: message is never interpreted as HTML
    el.appendChild(iconEl);
    el.appendChild(msgEl);
    container.appendChild(el);
    setTimeout(() => {
      el.classList.add("removing");
      el.addEventListener("animationend", () => el.remove());
    }, duration);
  },
  success: (m) => Toast.show(m, "success"),
  error: (m) => Toast.show(m, "error", 5000),
  info: (m) => Toast.show(m, "info"),
};

// ============================================================
// Modal
// ============================================================
const Modal = {
  open(title, bodyHTML, onOpen) {
    document.getElementById("modal-title").textContent = title;
    document.getElementById("modal-body").innerHTML = bodyHTML;
    document.getElementById("modal-overlay").classList.remove("hidden");
    if (onOpen) onOpen(document.getElementById("modal-body"));
  },
  close() {
    document.getElementById("modal-overlay").classList.add("hidden");
    document.getElementById("modal-body").innerHTML = "";
    document.getElementById("modal").classList.remove("modal-wide");
  },
};

document.getElementById("modal-overlay").addEventListener("click", (e) => {
  if (window._opmlImporting) return;
  if (e.target === document.getElementById("modal-overlay")) Modal.close();
});

// ============================================================
// Global image error handler
// ============================================================
// Replaces all inline onerror= attributes on <img> elements.
// Hides the broken image; if the next sibling was hidden (display:none)
// it is a placeholder fallback — reveal it.
document.addEventListener("error", (e) => {
  const img = e.target;
  if (img.tagName !== "IMG") return;
  img.style.display = "none";
  const sibling = img.nextElementSibling;
  if (sibling && sibling.style.display === "none") {
    sibling.style.display = "flex";
  }
}, true); // useCapture required — error events don't bubble

// ============================================================
// Global data-action dispatcher
// ============================================================
// All inline onclick= attributes in templates are replaced with
// data-action="..." + data-* attributes and handled here.
// This lets us drop 'unsafe-inline' from the CSP script-src.
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-action]");
  if (!el) return;
  const action = el.dataset.action;

  // ── Navigation ────────────────────────────────────────────
  if (action === "navigate") {
    if (el.dataset.epScroll) window._pendingEpScroll = Number(el.dataset.epScroll);
    if (el.dataset.dlTab)    window._pendingDLTab    = el.dataset.dlTab;
    Router.navigate(el.dataset.path);
    return;
  }

  // ── Search navigate (also hides search overlay) ───────────
  if (action === "search-navigate") {
    hideSearch();
    window._pendingEpScroll = Number(el.dataset.epId);
    Router.navigate(`/feeds/${el.dataset.feedId}`);
    return;
  }

  // ── Modal close ───────────────────────────────────────────
  if (action === "modal-close") { Modal.close(); return; }

  // ── Toggle collapsible panel ──────────────────────────────
  if (action === "toggle-panel") { togglePanel(el.dataset.panel); return; }

  // ── Play episode ──────────────────────────────────────────
  if (action === "play-episode") {
    e.stopPropagation();
    playEpisode(Number(el.dataset.epId), el.dataset.resume === "1");
    return;
  }

  // ── Toggle episode notes panel ───────────────────────────
  if (action === "toggle-ep-notes") { _toggleEpNotes(Number(el.dataset.epId)); return; }

  // ── Stop propagation only (dropdown wrapper divs) ─────────
  if (action === "stop-prop") { e.stopPropagation(); return; }

  // ── Toggle .ep-more-wrap open/closed ─────────────────────
  if (action === "toggle-more-wrap") {
    const w = el.closest(".ep-more-wrap");
    if (!w) return;
    w.toggleAttribute("data-open");
    if (w.hasAttribute("data-open")) {
      const d = w.querySelector(".ep-more-dropdown");
      if (d) positionDropdown(d);
    }
    document.querySelectorAll(".ep-more-wrap[data-open]").forEach(ow => {
      if (ow !== w) ow.removeAttribute("data-open");
    });
    return;
  }

  // ── Dismiss import banner ────────────────────────────────
  if (action === "dismiss-import-banner") {
    el.closest(".import-banner")?.parentElement && (el.closest(".import-banner").parentElement.style.display = "none");
    return;
  }

  // ── Apply file renames after import ──────────────────────
  if (action === "import-apply-renames") {
    const feedId = window._epState?.id;
    if (!feedId) return;
    el.disabled = true;
    el.textContent = "Applying…";
    API.applyFileUpdates(feedId).then((r) => {
      const parts = [];
      if (r.renamed > 0) parts.push(`${r.renamed} file${r.renamed !== 1 ? "s" : ""} renamed`);
      if (r.tagged > 0) parts.push(`${r.tagged} file${r.tagged !== 1 ? "s" : ""} tagged`);
      const msg = parts.length ? parts.join(", ") : "No changes needed";
      Toast.success(msg + (r.errors.length ? ` (${r.errors.length} error${r.errors.length !== 1 ? "s" : ""})` : ""));
      el.closest(".import-banner-rename")?.remove();
      if (typeof _refreshEpisodeList === "function") _refreshEpisodeList();
    }).catch((e) => {
      el.disabled = false;
      el.textContent = "Apply File Updates";
      Toast.error(e.message);
    });
    return;
  }

  // ── ep-more-wrap close-only (used on download <a> links) ─
  if (action === "ep-more-close") {
    el.closest(".ep-more-wrap")?.removeAttribute("data-open");
    return; // let default href/download proceed
  }

  // ── ep-more-wrap: close then run action ───────────────────
  if (action === "ep-more-close-seq") {
    el.closest(".ep-more-wrap")?.removeAttribute("data-open");
    const seqNum = el.dataset.seq !== "" ? Number(el.dataset.seq) : null;
    showSetNumberModal(Number(el.dataset.epId), seqNum, el.dataset.locked === "true");
    return;
  }
  if (action === "ep-more-close-tags") {
    el.closest(".ep-more-wrap")?.removeAttribute("data-open");
    showEpisodeTagsModal(Number(el.dataset.epId), JSON.parse(el.dataset.tags));
    return;
  }
  if (action === "ep-more-close-hide") {
    el.closest(".ep-more-wrap")?.removeAttribute("data-open");
    hideEpisode(Number(el.dataset.epId));
    return;
  }

  // ── Episode actions (feed-detail) ────────────────────────
  if (action === "bulk-toggle") {
    e.stopPropagation();
    _bulkToggle(Number(el.dataset.epId));
    return;
  }
  if (action === "unhide-episode") {
    e.stopPropagation();
    unhideEpisode(Number(el.dataset.epId));
    return;
  }
  if (action === "upload-ep-image") {
    e.stopPropagation();
    uploadEpisodeImageClick(Number(el.dataset.epId));
    return;
  }
  if (action === "queue-episode") {
    e.stopPropagation();
    queueEpisode(Number(el.dataset.epId));
    return;
  }
  if (action === "delete-episode-file") {
    e.stopPropagation();
    deleteEpisodeFile(Number(el.dataset.epId));
    return;
  }
  if (action === "cancel-episode") {
    e.stopPropagation();
    cancelEpisode(Number(el.dataset.epId));
    return;
  }
  if (action === "toggle-ep-played") {
    e.stopPropagation();
    toggleEpPlayed(Number(el.dataset.epId));
    return;
  }
  if (action === "add-to-playlist") {
    e.stopPropagation();
    window.showAddToPlaylist(Number(el.dataset.epId), el.dataset.epTitle || "");
    return;
  }
  if (action === "unlink-feed") {
    unlinkSupplementaryFeed(Number(el.dataset.podcastId), Number(el.dataset.feedId));
    return;
  }
  if (action === "dismiss-feed-error") {
    window._dismissFeedError(Number(el.dataset.feedId));
    return;
  }
  if (action === "feed-autoclean-now") {
    _runFeedAutocleanNow(Number(el.dataset.feedId));
    return;
  }

  // ── Bulk-selection actions (feed-detail) ─────────────────
  if (action === "bulk-select-all")     { _bulkSelectAll();     return; }
  if (action === "bulk-select-none")    { _bulkSelectNone();    return; }
  if (action === "bulk-select-inverse") { _bulkSelectInverse(); return; }
  if (action === "bulk-act-close-download") {
    document.getElementById("bulk-apply-wrap")?.removeAttribute("data-open");
    _bulkAct("download");
    return;
  }
  if (action === "bulk-act-close-played") {
    document.getElementById("bulk-apply-wrap")?.removeAttribute("data-open");
    _bulkActPlayed();
    return;
  }
  if (action === "bulk-act-close-hidden") {
    document.getElementById("bulk-apply-wrap")?.removeAttribute("data-open");
    _bulkActHidden();
    return;
  }
  if (action === "bulk-act-close-delete") {
    document.getElementById("bulk-apply-wrap")?.removeAttribute("data-open");
    _bulkAct("delete_file");
    return;
  }

  // ── Dashboard ─────────────────────────────────────────────
  if (action === "add-feed")        { showAddFeedModal(); return; }
  if (action === "mark-cl-played")  { window._markCLPlayed(Number(el.dataset.epId)); return; }
  if (action === "refresh-suggestions") { window._refreshSuggestions(el); return; }

  // ── Downloads ─────────────────────────────────────────────
  if (action === "dl-tab") { switchDLTab(el.dataset.tab, el); return; }
  if (action === "dl-global-unplayed") {
    el.closest(".ep-more-wrap")?.removeAttribute("data-open");
    _doGlobalUnplayed();
    return;
  }
  if (action === "dl-global-all") {
    el.closest(".ep-more-wrap")?.removeAttribute("data-open");
    _doGlobalAll();
    return;
  }
  if (action === "dl-feed") {
    el.closest(".ep-more-wrap")?.removeAttribute("data-open");
    downloadFeedFromDL(Number(el.dataset.feedId), el.dataset.mode, el);
    return;
  }
  if (action === "dl-load-more") {
    _dlLoadMore(el.dataset.tab, Number(el.dataset.offset));
    return;
  }
  if (action === "dl-clear-list")  { _clearDLList(); return; }
  if (action === "dl-queue")       { queueEpisodeDL(Number(el.dataset.epId)); return; }
  if (action === "dl-cancel")      { cancelEpisodeDL(Number(el.dataset.epId)); return; }
  if (action === "dl-dismiss")     { dismissEpisodeDL(Number(el.dataset.epId)); return; }
  if (action === "dl-delete")      { deleteEpisodeFileDL(Number(el.dataset.epId)); return; }

  // ── Settings ──────────────────────────────────────────────
  if (action === "select-theme")   { selectTheme(el.dataset.theme); return; }
  if (action === "autoclean-now")  { _runAutocleanNow(); return; }
  if (action === "sec-update-credentials") { _secUpdateCredentials(); return; }
  if (action === "sec-disable-auth")       { _secDisableAuth(); return; }
  if (action === "sec-enable-auth")        { _secEnableAuth(); return; }
  if (action === "sec-do-disable") {
    Modal.close();
    _secDoDisable();
    return;
  }
  if (action === "settings-stay") {
    Modal.close();
    window._settingsPendingNav = null;
    return;
  }
  if (action === "settings-discard") { window._settingsNavDiscard(); return; }
  if (action === "settings-save")    { window._settingsNavSave(); return; }

  // ── Stats ─────────────────────────────────────────────────
  if (action === "stats-toggle-feed") {
    toggleFeedFocus(Number(el.dataset.feedId));
    return;
  }

  // ── Setup wizard ──────────────────────────────────────────
  if (action === "wiz-select-theme") { _wizardSelectTheme(el.dataset.theme); return; }
  if (action === "wiz-load-dir") {
    _loadDirBrowser(document.getElementById("wiz-dl-path")?.value || "/");
    return;
  }
  if (action === "wiz-select-dir") {
    const p = el.dataset.path;
    const inp = document.getElementById("wiz-dl-path");
    if (inp) inp.value = p;
    if (window._setupState) window._setupState.downloadPath = p;
    _loadDirBrowser(p);
    return;
  }
});

// ============================================================
// Global input / change dispatcher
// ============================================================
// Handles oninput= and onchange= attributes replaced with data-action.
document.addEventListener("input", (e) => {
  const action = e.target.dataset.action;
  if (!action) return;
  if (action === "year-chart-slide")   { _yearChartSlide(e.target.value); return; }
  if (action === "filter-episodes")    { _filterEpisodes(); return; }
  if (action === "filter-feed-cards")  { filterFeedCards(); return; }
  if (action === "setup-dl-path") {
    if (window._setupState) window._setupState.downloadPath = e.target.value;
    return;
  }
  if (action === "ep-art-url-preview") {
    const p = document.getElementById("ep-art-modal-preview");
    if (p) { p.src = e.target.value; p.style.display = e.target.value ? "" : "none"; }
    return;
  }
});

document.addEventListener("change", (e) => {
  const action = e.target.dataset.action;
  if (!action) return;
  if (action === "autoclean-mode")      { _updateAutocleanModeHints(); return; }
  if (action === "feed-autoclean-mode") { _feedUpdateAutocleanMode(); return; }
});

// ============================================================
// Utilities
// ============================================================
// All datetimes from the API are naive UTC strings without a timezone suffix.
// _utcDate() appends "Z" so JS parses them correctly as UTC rather than local time.
function _utcDate(date) {
  if (!date) return null;
  if (date instanceof Date) return date;
  return new Date(date.includes("Z") || date.includes("+") ? date : date + "Z");
}

// fmt renders a date string.  When isApproximate is true we wrap the value in a
// dotted underline with a tooltip explaining that the date was inferred rather than
// read from metadata — this happens for files imported without ID3 date tags.
function fmt(date, isApproximate) {
  if (!date) return "—";
  const d = _utcDate(date);
  if (!d || isNaN(d)) return "—";
  const s = d.toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
    timeZone: window._appTimezone || "UTC",
  });
  if (!isApproximate) return s;
  return `<span title="Approximate date — no date metadata found in this file. Set a date in the ID3 tags to resolve this." style="border-bottom:1px dashed var(--text-2);cursor:help">${s}~</span>`;
}

// fmtDateTime renders a date+time string in the configured server timezone.
function fmtDateTime(date) {
  if (!date) return "—";
  const d = _utcDate(date);
  if (!d || isNaN(d)) return "—";
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
    timeZone: window._appTimezone || "UTC",
  });
}

function fmtBytes(bytes) {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function timeAgo(date) {
  if (!date) return "never";
  const d = _utcDate(date);
  if (!d || isNaN(d)) return "never";
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function statusBadge(status) {
  const map = {
    pending: ["badge-default", "Not Downloaded"],
    queued: ["badge-warning", "Queued"],
    downloading: ["badge-primary", "Downloading"],
    downloaded: ["badge-success", "Downloaded"],
    failed: ["badge-error", "Failed"],
    skipped: ["badge-default", "Skipped"],
  };
  const [cls, label] = map[status] || ["badge-default", status];
  return `<span class="badge status-badge ${cls}">${label}</span>`;
}

function svg(path, extra = "") {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ${extra}>${path}</svg>`;
}

const _PODCAST_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="40%" height="40%" opacity="0.5"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="8" y1="22" x2="16" y2="22"/></svg>`;

// Only allow http/https/relative/data:image URLs in src attributes — everything else becomes ""
function _safeImgUrl(url) {
  if (!url) return "";
  const s = url.trim();
  if (s.startsWith("http://") || s.startsWith("https://") || s.startsWith("/") || s.startsWith("data:image/")) return s;
  return "";
}

function artImg(url, fallbackEmoji = "", size = "", paused = false) {
  url = _safeImgUrl(url);
  const placeholder = fallbackEmoji
    ? `<div class="feed-card-art-placeholder">${fallbackEmoji}</div>`
    : `<div class="feed-card-art-placeholder">${_PODCAST_SVG}</div>`;
  const inner = url
    ? `<img src="${url}" alt="" loading="lazy" />
       <div class="feed-card-art-placeholder" style="display:none">${fallbackEmoji || _PODCAST_SVG}</div>`
    : placeholder;
  const pauseOverlay = paused
    ? `<div class="art-paused-overlay">
         <svg viewBox="0 0 24 24" fill="currentColor" width="40%" height="40%">
           <rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>
         </svg>
       </div>`
    : "";
  const style = size
    ? `style="width:${size};height:${size};position:relative"`
    : `style="width:100%;height:100%;position:relative"`;
  return `<div ${style}>${inner}${pauseOverlay}</div>`;
}

/**
 * Render a standardised episode play/pause/resume button.
 * Handles all three states: currently playing (pause icon), resumable (|▶ icon), play from start (▶ icon).
 * Uses `ep-play-btn` + `data-ep-id` so Player._syncPlayBtns() keeps it live.
 */
function epPlayBtn(ep, { extraClasses = "" } = {}) {
  const playing   = Player.currentId() === ep.id && Player.isPlaying();
  const resumable = !ep.played && (ep.play_position_seconds > 0);
  const icon = playing   ? svg('<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>')
             : resumable ? svg(Player.resumeIcon())
             :             svg(Player.playIcon());
  const title = playing ? "Pause" : resumable ? "Resume" : "Play";
  return `<button class="btn btn-ghost btn-sm btn-icon ep-play-btn${extraClasses ? " " + extraClasses : ""}"
          data-action="play-episode" data-ep-id="${ep.id}"${resumable ? ' data-resume="1"' : ''}
          ${resumable ? 'data-resumable="1"' : ''}
          title="${title}">${icon}</button>`;
}

function toggle(label, name, checked, hint = "") {
  return `<div class="form-group">
    <div class="flex items-center gap-2">
      <label class="toggle">
        <input type="checkbox" name="${name}" ${checked ? "checked" : ""} />
        <span class="toggle-slider"></span>
      </label>
      <span class="form-check-label">${label}</span>
    </div>
    ${hint ? `<div class="form-hint">${hint}</div>` : ""}
  </div>`;
}

function collectForm(form) {
  const data = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") {
      data[el.name] = el.checked;
    } else if (el.value !== "") {
      const num = el.dataset.numeric;
      data[el.name] = num ? Number(el.value) : el.value;
    }
  }
  return data;
}

window.togglePanel = function (id) {
  document.getElementById(id).classList.toggle("open");
};

// ============================================================
// Dropdown viewport boundary detection
// ============================================================
// Reposition a dropdown element so it stays within the viewport.
// Resets left/right first to measure the natural position, then
// flips if the dropdown would be clipped at the right or left edge.
function positionDropdown(el) {
  el.style.right = "";
  el.style.left = "";
  const rect = el.getBoundingClientRect();
  if (rect.right > window.innerWidth - 8) {
    el.style.right = "0";
    el.style.left = "auto";
  } else if (rect.left < 8) {
    el.style.left = "0";
    el.style.right = "auto";
  }
}

// ============================================================
// Animated removal
// ============================================================
/**
 * Smoothly remove an element: fade out, then collapse its height, then remove
 * from the DOM.  Safe to call on already-removing or already-removed elements.
 * onDone() is called after the element is removed.
 */
function animateRemove(el, onDone) {
  if (!el || el._removing) return;
  el._removing = true;
  el.style.pointerEvents = "none";

  // Read height now so the browser commits the layout before we start animating.
  const h = el.offsetHeight;
  el.style.overflow = "hidden";
  el.style.height = h + "px";

  // Second offsetHeight read forces the browser to commit height: Xpx before
  // we set it to 0, ensuring the CSS transition actually fires.
  void el.offsetHeight;

  el.style.transition =
    "opacity 0.15s ease, " +
    "height 0.22s ease 0.1s, " +
    "margin-top 0.22s ease 0.1s, " +
    "margin-bottom 0.22s ease 0.1s, " +
    "padding-top 0.22s ease 0.1s, " +
    "padding-bottom 0.22s ease 0.1s";
  el.style.opacity = "0";
  el.style.height = "0";
  el.style.marginTop = "0";
  el.style.marginBottom = "0";
  el.style.paddingTop = "0";
  el.style.paddingBottom = "0";

  // 0.1 (delay) + 0.22 (duration) = 0.32 s → wait a touch longer to be safe
  setTimeout(() => {
    el.remove();
    if (onDone) onDone();
  }, 340);
}

// animateEnter is the exact reverse of animateRemove: the element starts
// fully collapsed (height 0, opacity 0) and expands to its natural height
// while fading in.  We read scrollHeight before collapsing so the transition
// knows its target, then force a reflow to commit the start state.
function animateEnter(el) {
  if (!el) return;
  const h = el.scrollHeight;
  el.style.overflow = "hidden";
  el.style.height = "0";
  el.style.opacity = "0";
  void el.offsetHeight; // commit collapsed state before transition starts
  el.style.transition =
    "opacity 0.15s ease 0.1s, " +
    "height 0.22s ease";
  el.style.height = h + "px";
  el.style.opacity = "1";
  setTimeout(() => {
    el.style.transition = "";
    el.style.height = "";
    el.style.overflow = "";
  }, 340);
}

// ── Shared escape helpers ───────────────────────────────────────────────────

function escHTML(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function escJS(s) {
  return String(s ?? "").replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

// ── Shared "Sync All Feeds" button wiring ───────────────────────────────────

// ── Timezone combobox ─────────────────────────────────────────────────────────
// initTzCombo(inputEl, dropdownEl, allZones, initialValue, onChange)
// Wires a text input + dropdown div into a searchable timezone picker.
function initTzCombo(inputEl, dropdownEl, allZones, initialValue, onChange) {
  let current = initialValue;
  inputEl.value = current;

  const renderList = (zones) => {
    dropdownEl.innerHTML = zones.map(z =>
      `<div class="tz-option${z === current ? " tz-highlighted" : ""}" data-tz="${escHTML(z)}">${escHTML(z)}</div>`
    ).join("") || `<div class="tz-option" style="color:var(--text-3);cursor:default">No matches</div>`;
    dropdownEl.querySelector(".tz-highlighted")?.scrollIntoView({ block: "nearest" });
  };

  const openWith = (query) => {
    const q = query.toLowerCase();
    const filtered = q ? allZones.filter(z => z.toLowerCase().includes(q)) : allZones;
    renderList(filtered);
    dropdownEl.classList.add("open");
  };

  const commit = (value) => {
    if (!allZones.includes(value)) return;
    current = value;
    inputEl.value = value;
    dropdownEl.classList.remove("open");
    onChange(value);
  };

  inputEl.addEventListener("focus",  () => openWith(inputEl.value === current ? "" : inputEl.value));
  inputEl.addEventListener("input",  () => openWith(inputEl.value));
  inputEl.addEventListener("blur",   () => setTimeout(() => {
    dropdownEl.classList.remove("open");
    inputEl.value = current; // restore if user typed something invalid
  }, 160));

  dropdownEl.addEventListener("mousedown", (e) => {
    const opt = e.target.closest(".tz-option[data-tz]");
    if (opt) commit(opt.dataset.tz);
  });

  inputEl.addEventListener("keydown", (e) => {
    const isOpen = dropdownEl.classList.contains("open");
    if (!isOpen && (e.key === "ArrowDown" || e.key === "Enter")) {
      openWith(""); return;
    }
    if (!isOpen) return;
    const opts = [...dropdownEl.querySelectorAll(".tz-option[data-tz]")];
    const hi = dropdownEl.querySelector(".tz-highlighted");
    const idx = hi ? opts.indexOf(hi) : -1;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      opts.forEach(o => o.classList.remove("tz-highlighted"));
      (opts[idx + 1] ?? opts[0])?.classList.add("tz-highlighted");
      dropdownEl.querySelector(".tz-highlighted")?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      opts.forEach(o => o.classList.remove("tz-highlighted"));
      (opts[idx - 1] ?? opts[opts.length - 1])?.classList.add("tz-highlighted");
      dropdownEl.querySelector(".tz-highlighted")?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      const active = dropdownEl.querySelector(".tz-highlighted[data-tz]");
      if (active) commit(active.dataset.tz);
    } else if (e.key === "Escape") {
      dropdownEl.classList.remove("open");
      inputEl.value = current;
    }
  });
}

function wireSyncAllBtn(selector) {
  document.querySelector(selector)?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "Syncing\u2026";
    try {
      await API.syncAllFeeds();
      document.querySelectorAll("#feeds-grid .badge-error").forEach(el => el.remove());
      updateStatus();
      Toast.success("Sync started for all active feeds");
    } catch (err) {
      Toast.error(err.message);
    } finally {
      btn.disabled = false;
      btn.innerHTML = `${svg('<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>')} Sync All Feeds`;
    }
  });
}

// ============================================================
// Directory browser component (reusable folder picker)
// ============================================================
const DirBrowser = {
  async load(containerId, inputId, path) {
    const el = document.getElementById(containerId);
    const input = document.getElementById(inputId);
    if (!el) return;
    el.innerHTML = `<div style="padding:8px 12px;color:var(--text-3);font-size:12px">Loading\u2026</div>`;
    try {
      const data = await API.browseDirs(path || "/");
      if (input) input.value = data.path;
      const rows = [];
      if (data.parent !== null) {
        rows.push(`<div class="dir-entry dir-up" data-path="${data.parent}">
          <span class="dir-icon">\u2191</span><span>..</span></div>`);
      }
      for (const entry of data.entries) {
        rows.push(`<div class="dir-entry" data-path="${entry.path}">
          <span class="dir-icon">\uD83D\uDCC1</span><span>${escHTML(entry.name)}</span></div>`);
      }
      if (!data.entries.length && data.parent === null) {
        rows.push(`<div style="padding:8px 12px;color:var(--text-3);font-size:12px">No subdirectories</div>`);
      }
      el.innerHTML = `<div class="dir-browser-path">${escHTML(data.path)}</div>${rows.join("")}`;
      el.querySelectorAll(".dir-entry[data-path]").forEach(div => {
        div.addEventListener("click", () => DirBrowser.load(containerId, inputId, div.dataset.path));
      });
    } catch (err) {
      el.innerHTML = `<div style="padding:8px 12px;color:var(--error);font-size:12px">${escHTML(err.message)}</div>`;
    }
  }
};
