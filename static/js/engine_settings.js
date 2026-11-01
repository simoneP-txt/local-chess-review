/* ==========================================
   Engine settings shared by the dashboard (panel) and the review page (analysis).
   Stored in localStorage as {"depth": n, "threads": n}. The valid ranges come from
   the server (GET /api/engine, the host that runs Stockfish), so a stored value is
   brought back into the current range on every load.
   ========================================== */

const ENGINE_SETTINGS_KEY = "chessreview.engine_settings";

function loadEngineSettings(limits) {
  let stored = null;
  try { stored = JSON.parse(localStorage.getItem(ENGINE_SETTINGS_KEY)); }
  catch { /* unreadable: use the defaults */ }
  const pick = (name) => {
    const range = limits[name];
    const value = Number.isInteger(stored?.[name]) ? stored[name] : range.default;
    return Math.min(range.max, Math.max(range.min, value));
  };
  return { depth: pick("depth"), threads: pick("threads") };
}

function saveEngineSettings(settings) {
  try { localStorage.setItem(ENGINE_SETTINGS_KEY, JSON.stringify(settings)); }
  catch { /* storage full or disabled: ignore */ }
}
