/* ==========================================
   Dashboard - username search + games list with flags/Elo
   ========================================== */

const form = document.getElementById("search-form");
const usernameInput = document.getElementById("username");
const statusEl = document.getElementById("status");
const wrapper = document.getElementById("games-wrapper");
const btnSearch = document.getElementById("btn-search");

// =============================================================
// Language toggle (IT default, EN optional)
// =============================================================
(function bindLangToggle() {
  const select = document.getElementById("lang-select");
  if (!select) return;
  select.value = getCurrentLang();
  select.addEventListener("change", (e) => {
    setLang(e.target.value);
    // Reload re-applies translations everywhere on the page (period notice,
    // games already rendered, etc.). Simpler than rewiring each piece.
    window.location.reload();
  });
})();

// =============================================================
// MRU cache of searched usernames (localStorage)
// - Stores up to RECENT_MAX unique names, MRU order.
// - Populates both the native <datalist> (for browser autocomplete)
// - And the row of clickable chips below the search bar.
// =============================================================
const RECENT_KEY = "chessreview.recent_usernames";
const RECENT_MAX = 6;

function loadRecent() {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); }
  catch { return []; }
}
function saveRecent(list) {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX))); }
  catch { /* storage full or disabled: ignore */ }
}
function pushRecent(name) {
  const norm = name.trim();
  if (!norm) return;
  const current = loadRecent().filter((n) => n.toLowerCase() !== norm.toLowerCase());
  current.unshift(norm);
  saveRecent(current);
  renderRecent();
}
function removeRecent(name) {
  const filtered = loadRecent().filter((n) => n.toLowerCase() !== name.toLowerCase());
  saveRecent(filtered);
  renderRecent();
}
function renderRecent() {
  const datalist = document.getElementById("recent-usernames");
  const chipBox = document.getElementById("recent-list");
  if (!datalist || !chipBox) return;
  const items = loadRecent();

  datalist.innerHTML = items.map((n) => `<option value="${n}"></option>`).join("");

  if (!items.length) {
    chipBox.innerHTML = `<span class="small text-muted">${t("dashboard.no_recent")}</span>`;
    return;
  }
  chipBox.innerHTML = items.map((n) => `
    <span class="badge bg-primary recent-chip" data-name="${n}" role="button">
      ${n} <span class="recent-chip-x ms-1" data-name="${n}" title="${t("dashboard.remove_recent")}">&times;</span>
    </span>
  `).join("");

  chipBox.querySelectorAll(".recent-chip").forEach((el) => {
    el.addEventListener("click", (e) => {
      // click on x -> removes; click elsewhere -> selects
      if (e.target.classList.contains("recent-chip-x")) {
        e.stopPropagation();
        removeRecent(e.target.dataset.name);
      } else {
        usernameInput.value = el.dataset.name;
        usernameInput.focus();
      }
    });
  });
}
renderRecent();

// =============================================================
// Period notice: the upstream public API only returns games
// from the 1st of the current month. The user must know this so
// they don't expect older games.
// =============================================================
(function showPeriodNotice() {
  const noticeEl = document.getElementById("period-notice");
  if (!noticeEl) return;
  const now = new Date();
  const monthName = t(`month.${now.getMonth()}`);
  const year = now.getFullYear();
  noticeEl.innerHTML = t("dashboard.period_notice", { month: monthName, year });
})();

function setStatus(msg, isError = false) {
  statusEl.innerHTML = msg
    ? `<div class="alert ${isError ? "alert-danger" : "alert-info"} d-inline-block">${msg}</div>`
    : "";
}

function resultLabel(r) {
  if (r === "win")  return `<span class="result-win">${t("game.win")}</span>`;
  if (r === "loss") return `<span class="result-loss">${t("game.loss")}</span>`;
  return `<span class="result-draw">${t("game.draw")}</span>`;
}

/**
 * Convert an ISO-2 country code (e.g. "IT") into the Unicode flag emoji.
 * Works because 🇮 = U+1F1EE and each capital letter is offset 'A' = U+1F1E6.
 */
function flagEmoji(iso2) {
  if (!iso2 || iso2.length !== 2) return "🏳";
  const A = 0x1F1E6;
  const a = "A".charCodeAt(0);
  const codepoints = [...iso2.toUpperCase()].map((c) => A + (c.charCodeAt(0) - a));
  return String.fromCodePoint(...codepoints);
}

/** Render a small player block: flag + name + (Elo). */
function playerBlock(name, country, rating) {
  return `
    <div class="player-line">
      <span class="flag">${flagEmoji(country)}</span>
      <span class="name">${name || "?"}</span>
      <span class="elo">(${rating || "—"})</span>
    </div>
  `;
}

function renderGames(username, games) {
  wrapper.innerHTML = "";
  if (!games.length) {
    setStatus(t("dashboard.no_games"), true);
    return;
  }
  setStatus(t("dashboard.found_games", { count: games.length, username }));

  for (const g of games) {
    const col = document.createElement("div");
    col.className = "col-12 col-md-6 col-lg-4";
    col.innerHTML = `
      <div class="card game-card bg-light text-dark h-100" data-id="${g.id}">
        <div class="card-body">
          <div class="game-players mb-2">
            ${playerBlock(g.white, g.white_country, g.white_rating)}
            <div class="vs-label">vs</div>
            ${playerBlock(g.black, g.black_country, g.black_rating)}
          </div>
          <p class="card-text small mb-1 text-muted">
            ${g.date || t("game.no_date")} · ${g.time_class || "?"}
          </p>
          <p class="card-text mb-2">${t("game.result_label")} ${resultLabel(g.result)}</p>
          <button class="btn btn-success btn-sm w-100">${t("game.analyze_btn")}</button>
        </div>
      </div>
    `;
    col.querySelector(".game-card").addEventListener("click", () => {
      sessionStorage.setItem("review_username", username);
      sessionStorage.setItem("review_game_id", g.id);
      window.location.href = "/review";
    });
    wrapper.appendChild(col);
  }
}

async function runSearch(username) {
  if (!username) return;
  btnSearch.disabled = true;
  setStatus(t("dashboard.fetching"));
  wrapper.innerHTML = "";

  try {
    const resp = await fetch(`/api/games?username=${encodeURIComponent(username)}`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "unknown error");
    pushRecent(data.username);
    renderGames(data.username, data.games);
  } catch (err) {
    setStatus(`${t("common.error")}: ${err.message}`, true);
  } finally {
    btnSearch.disabled = false;
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  runSearch(usernameInput.value.trim());
});

// Auto-load: when the user comes back to the dashboard from the review via
// the "Back" arrow, review.js sets `autoload_username` in sessionStorage.
// We consume it here to repopulate the profile's list automatically,
// without forcing the user to retype the name.
(function autoloadFromBack() {
  const auto = sessionStorage.getItem("autoload_username");
  if (!auto) return;
  sessionStorage.removeItem("autoload_username");
  usernameInput.value = auto;
  runSearch(auto);
})();
