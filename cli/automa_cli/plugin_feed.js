// The live /api/plugins feed both plugin pages follow: one poll every 500 ms,
// the same status line, and the last drawn catalog dimmed while polls fail.
"use strict";

const PLUGIN_FEED_INTERVAL_MS = 500;

// render(catalog) draws each successful poll into view. After a failed poll,
// markStale() marks what the page last drew as unknown and returns whether
// there was anything to keep showing.
function followPluginCatalog({ view, status, render, markStale }) {
  async function poll() {
    try {
      const response = await fetch('/api/plugins', { cache: 'no-store' });
      const catalog = await response.json();
      if (!response.ok || !catalog.ok) throw new Error(catalog.error || `HTTP ${response.status}`);
      const selectionError = catalog.arming?.error;
      status.textContent = selectionError ? 'Plugin selection failed · ' + selectionError : '';
      status.hidden = !selectionError;
      render(catalog);
      view.classList.remove('stale');
    } catch (error) {
      view.classList.add('stale');
      const snapshot = markStale();
      status.hidden = false;
      status.textContent = 'Catalog unavailable · ' + error.message +
        (snapshot ? ' · Showing stale snapshot' : '') + ' · Retrying automatically';
    }
    setTimeout(poll, PLUGIN_FEED_INTERVAL_MS);
  }
  poll();
}

// The plugins a step runs, in order, or null when the host reports no selection for it.
function appliedPlugins(catalog, step) {
  const applied = catalog.arming?.applied?.[step];
  return Array.isArray(applied) ? applied : null;
}
