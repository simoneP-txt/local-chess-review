/* ==========================================
   Review - full flow:
   1) SUMMARY screen (chart + stats + phases)
   2) Move-by-move REVIEW screen with commentary and PV continuation

   Bilingual UI (IT default, EN optional). All user-facing strings come from
   i18n.js via t(...). Tactical comments returned by the analyzer use
   structured keys + params and are rendered locally with the current language.
   ========================================== */

const username = sessionStorage.getItem("review_username");
const gameId = sessionStorage.getItem("review_game_id");

let board = null;
let chess = null;
let analysis = null;
let meta = null;
let currentPly = 0;
let orientation = "white";
let wpChart = null;

let continuationActive = false;
let continuationTimer = null;
let continuationStep = 0;
let continuationMoves = [];

/* "Variation explorer" state: the user can play alternative moves by
   dragging pieces on the main board. */
let variationActive = false;
let variationPlyCount = 0;       // moves played in variation (off the actual game)
let variationBaselinePly = 0;    // the ply we deviated from

/* Mini-puzzle state on the loading screen */
let puzzleBoard = null;
let puzzleChess = null;
let puzzleData = null;           // {id, fen, moves: [], rating, themes}
let puzzleStep = 0;              // index in the moves sequence
let puzzleSolved = false;
let puzzleHintShown = false;
let progressTimer = null;
let progressStartedAt = 0;
let progressEstimateMs = 90000;  // initial estimate (refined from move count)

/* Classification labels: symbol stays language-independent, label is
   resolved via i18n at render time. */
const TAG_META = {
  brilliant:  { symbol: "‼",  className: "tag-brilliant"  },
  great:      { symbol: "!",  className: "tag-great"      },
  best:       { symbol: "★",  className: "tag-best"       },
  excellent:  { symbol: "👍", className: "tag-excellent"  },
  good:       { symbol: "✓",  className: "tag-good"       },
  book:       { symbol: "📖", className: "tag-book"       },
  inaccuracy: { symbol: "?!", className: "tag-inaccuracy" },
  mistake:    { symbol: "?",  className: "tag-mistake"    },
  miss:       { symbol: "❌", className: "tag-miss"       },
  blunder:    { symbol: "??", className: "tag-blunder"    },
};
function tagLabel(cls) { return t(`tag.${cls}`); }

/* Solid colors for chart dots (aligned to tag-* CSS classes). */
const DOT_COLORS = {
  brilliant:  "#1ba1a1",
  great:      "#5b9be0",
  best:       "#81b64c",
  excellent:  "#95c771",
  good:       "#a3a3a3",
  book:       "#b58863",
  inaccuracy: "#f7c12a",
  mistake:    "#e58a3a",
  miss:       "#d36e52",
  blunder:    "#ca3431",
};

/* Display order in the move-counts table (Brilliant and Great always separate). */
const STATS_ORDER = [
  "brilliant", "great", "best", "excellent", "good", "book",
  "inaccuracy", "mistake", "miss", "blunder",
];

const SHOW_ARROW_FOR = new Set(["inaccuracy", "mistake", "blunder", "miss"]);
// Commentary ONLY on errors (not on Brilliant/Great/Best/Good/Excellent).
const SHOW_COMMENTARY_FOR = new Set(["inaccuracy", "mistake", "blunder", "miss"]);
const SHOW_CONTINUATION_FOR = new Set(["inaccuracy", "mistake", "blunder", "miss"]);

/* Render a tactical comment from a structured object emitted by the backend.
   The backend returns either a string (legacy) OR an object {key, params}.
   For object shape we look up the i18n template and interpolate {piece}. */
function renderTacticalComment(commentField) {
  if (!commentField) return "";
  if (typeof commentField === "string") return commentField; // legacy
  if (typeof commentField === "object" && commentField.key) {
    const params = { ...(commentField.params || {}) };
    if (params.piece) params.piece = t(`piece.${params.piece}`);
    return t(`comment.${commentField.key}`, params);
  }
  return "";
}

/* ---------- helpers ---------- */
function flagEmoji(iso2) {
  if (!iso2 || iso2.length !== 2) return "🏳";
  const A = 0x1F1E6, a = "A".charCodeAt(0);
  const cps = [...iso2.toUpperCase()].map((c) => A + (c.charCodeAt(0) - a));
  return String.fromCodePoint(...cps);
}

/* =============================================================
   PROGRESS BAR (estimated based on PGN move count)
   ============================================================= */
function startProgressBar(estimatedMs) {
  progressStartedAt = performance.now();
  progressEstimateMs = Math.max(15000, estimatedMs); // never below 15s
  if (progressTimer) clearInterval(progressTimer);
  progressTimer = setInterval(() => {
    const elapsed = performance.now() - progressStartedAt;
    // Log curve: reaches ~90% in estimated time, then asymptotes.
    const pct = Math.min(95, 100 * (1 - Math.exp(-elapsed / progressEstimateMs)));
    const bar = document.getElementById("loading-progress-bar");
    if (bar) bar.style.width = `${pct.toFixed(1)}%`;
  }, 200);
}
function finishProgressBar() {
  if (progressTimer) clearInterval(progressTimer);
  const bar = document.getElementById("loading-progress-bar");
  if (bar) bar.style.width = "100%";
}
function setLoadingStatus(msg) {
  const el = document.getElementById("loading-status");
  if (el) el.textContent = msg;
}

/* =============================================================
   MINI PUZZLE (Lichess) during loading
   - Loads /api/puzzle?difficulty=...
   - FEN: position BEFORE the opponent's "setup" move
   - moves[0] = setup (auto-play), then alternating: user, opponent, user...
   ============================================================= */
async function loadPuzzle() {
  const difficulty = document.getElementById("puzzle-difficulty").value;
  const metaEl = document.getElementById("puzzle-meta");
  setPuzzleStatus(t("puzzle.loading"), "info");
  metaEl.textContent = "";

  try {
    const r = await fetch(`/api/puzzle?difficulty=${difficulty}`);
    const data = await r.json();
    if (!r.ok) {
      if (data.missing_db) {
        setPuzzleStatus(t("puzzle.db_missing"), "error");
      } else {
        setPuzzleStatus(t("puzzle.error", { msg: data.error }), "error");
      }
      return;
    }
    puzzleData = data;
    puzzleStep = 0;
    puzzleSolved = false;
    puzzleHintShown = false;
    setupPuzzleBoard();
  } catch (err) {
    setPuzzleStatus(t("puzzle.error", { msg: err.message }), "error");
  }
}

function setupPuzzleBoard() {
  if (puzzleBoard) { puzzleBoard.destroy(); puzzleBoard = null; }
  puzzleChess = new Chess(puzzleData.fen);

  // The puzzle's solver is the side opposite to the one that just moved (setup).
  // Setup is moves[0]; after applying it, side-to-move will be the user.
  const setupUci = puzzleData.moves[0];
  const setupMove = { from: setupUci.slice(0, 2), to: setupUci.slice(2, 4), promotion: setupUci[4] };

  // Determine orientation AFTER setup (i.e. who moves next).
  const tmp = new Chess(puzzleData.fen);
  tmp.move(setupMove);
  const userColor = tmp.turn() === "w" ? "white" : "black";

  puzzleBoard = Chessboard("puzzle-board", {
    position: puzzleData.fen,
    orientation: userColor,
    pieceTheme: "https://chessboardjs.com/img/chesspieces/wikipedia/{piece}.png",
    showNotation: true,
    draggable: true,
    onDrop: handlePuzzleDrop,
  });

  document.getElementById("puzzle-meta").textContent = t("puzzle.meta", {
    rating: puzzleData.rating,
    themes: puzzleData.themes.slice(0, 3).join(", ") || "—",
  });

  // Auto-play setup move after a short pause.
  setTimeout(() => {
    puzzleChess.move(setupMove);
    puzzleStep = 1;
    puzzleBoard.position(puzzleChess.fen(), true);
    setPuzzleStatus(
      userColor === "white" ? t("puzzle.your_turn_white") : t("puzzle.your_turn_black"),
      "info"
    );
  }, 700);
}

function handlePuzzleDrop(source, target) {
  if (!puzzleData || puzzleSolved) return "snapback";
  if (source === target) return "snapback";

  const move = puzzleChess.move({ from: source, to: target, promotion: "q" });
  if (move === null) return "snapback";

  const expected = puzzleData.moves[puzzleStep];
  const expectedFrom = expected.slice(0, 2), expectedTo = expected.slice(2, 4);
  const userUci = source + target + (move.promotion || "");

  const isCorrect = (userUci === expected) ||
                    (source === expectedFrom && target === expectedTo);

  if (!isCorrect) {
    puzzleChess.undo();
    setPuzzleStatus(t("puzzle.wrong"), "error");
    return "snapback";
  }

  puzzleStep++;
  setPuzzleStatus(t("puzzle.correct"), "ok");

  // If there are more moves in the sequence, the opponent replies automatically.
  if (puzzleStep < puzzleData.moves.length) {
    setTimeout(() => {
      const reply = puzzleData.moves[puzzleStep];
      puzzleChess.move({
        from: reply.slice(0, 2),
        to: reply.slice(2, 4),
        promotion: reply[4],
      });
      puzzleStep++;
      puzzleBoard.position(puzzleChess.fen(), true);
      if (puzzleStep >= puzzleData.moves.length) {
        puzzleSolved = true;
        setPuzzleStatus(t("puzzle.solved"), "ok");
      } else {
        setPuzzleStatus(t("puzzle.continue"), "info");
      }
    }, 600);
  } else {
    puzzleSolved = true;
    setPuzzleStatus(t("puzzle.solved"), "ok");
  }
}

function setPuzzleStatus(msg, kind) {
  const el = document.getElementById("puzzle-status");
  if (!el) return;
  el.textContent = msg;
  el.classList.remove("puzzle-status-ok", "puzzle-status-error", "puzzle-status-info");
  if (kind === "ok") el.classList.add("puzzle-status-ok");
  else if (kind === "error") el.classList.add("puzzle-status-error");
  else el.classList.add("puzzle-status-info");
}

function bindPuzzleControls() {
  document.getElementById("puzzle-difficulty").addEventListener("change", loadPuzzle);
  document.getElementById("btn-puzzle-new").addEventListener("click", loadPuzzle);
  document.getElementById("btn-puzzle-hint").addEventListener("click", () => {
    if (!puzzleData || puzzleSolved) return;
    if (puzzleStep >= puzzleData.moves.length) return;
    const next = puzzleData.moves[puzzleStep];
    setPuzzleStatus(t("puzzle.hint", { square: next.slice(0, 2) }), "info");
    puzzleHintShown = true;
  });
}

/* ---------- bootstrap ---------- */
async function init() {
  if (!username || gameId === null) {
    alert("No game selected. Going back to dashboard.");
    window.location.href = "/";
    return;
  }
  try {
    meta = await fetch(`/api/game/${encodeURIComponent(username)}/${gameId}`).then((r) => r.json());
    if (meta.error) throw new Error(meta.error);

    document.getElementById("game-title").textContent =
      `${meta.white} (${t("review.color_white")[0]}) vs ${meta.black} (${t("review.color_black")[0]}) · ${meta.date}`;
    orientation = meta.user_color || "white";

    // Estimate analysis time: ~600ms per ply at depth 15.
    const plyEstimate = (meta.pgn.match(/[0-9]+\./g) || []).length * 2;
    startProgressBar(Math.max(20000, plyEstimate * 600));
    setLoadingStatus(t("review.loading_estimate", { plies: plyEstimate, depth: 15 }));

    // Run in parallel: puzzle + analysis.
    bindPuzzleControls();
    loadPuzzle();   // not awaited: runs in parallel with analysis

    const resp = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pgn: meta.pgn,
        white_elo: meta.white_rating,
        black_elo: meta.black_rating,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "analysis error");

    analysis = data;
    finishProgressBar();
    setLoadingStatus(t("review.loading_complete"));
    setTimeout(() => {
      document.getElementById("loading").classList.add("d-none");
      showSummary();
    }, 350);
  } catch (err) {
    finishProgressBar();
    document.getElementById("loading").innerHTML =
      `<div class="alert alert-danger">${t("common.error")}: ${err.message}</div>`;
  }
}

/* ============================================================
   SUMMARY
   ============================================================ */
function showSummary() {
  document.getElementById("main").classList.add("d-none");
  document.getElementById("summary-screen").classList.remove("d-none");

  document.getElementById("summary-white-name").textContent = meta.white;
  document.getElementById("summary-black-name").textContent = meta.black;
  document.getElementById("summary-white-meta").innerHTML =
    `${flagEmoji(meta.white_country)} Elo ${meta.white_rating || "—"}`;
  document.getElementById("summary-black-meta").innerHTML =
    `Elo ${meta.black_rating || "—"} ${flagEmoji(meta.black_country)}`;

  document.getElementById("summary-white-accuracy").textContent = analysis.accuracy.white.toFixed(1);
  document.getElementById("summary-black-accuracy").textContent = analysis.accuracy.black.toFixed(1);
  document.getElementById("summary-white-elo").textContent = analysis.performance_elo.white;
  document.getElementById("summary-black-elo").textContent = analysis.performance_elo.black;

  renderPhases();
  renderMoveStats();
  renderChart();
}

function renderPhases() {
  const target = document.getElementById("phases-rows");
  target.innerHTML = "";
  const phases = analysis.phases || {};
  const phaseLabels = {
    opening:    t("summary.phase_opening"),
    middlegame: t("summary.phase_middlegame"),
    endgame:    t("summary.phase_endgame"),
  };
  for (const phaseKey of ["opening", "middlegame", "endgame"]) {
    const ph = phases[phaseKey] || {};
    const w = ph.white || {};
    const b = ph.black || {};

    const wIcon = w.label
      ? `<span class="phase-icon ${TAG_META[w.label].className}" title="${tagLabel(w.label)}">${TAG_META[w.label].symbol}</span>`
      : `<span class="phase-icon empty">—</span>`;
    const bIcon = b.label
      ? `<span class="phase-icon ${TAG_META[b.label].className}" title="${tagLabel(b.label)}">${TAG_META[b.label].symbol}</span>`
      : `<span class="phase-icon empty">—</span>`;

    const row = document.createElement("div");
    row.className = "phase-row";
    row.innerHTML = `
      <div>${wIcon}</div>
      <div class="phase-name">${phaseLabels[phaseKey]}</div>
      <div>${bIcon}</div>
    `;
    target.appendChild(row);
  }
}

function renderMoveStats() {
  const target = document.getElementById("moves-stats");
  target.innerHTML = "";
  const counts = analysis.move_counts;

  for (const cls of STATS_ORDER) {
    const w = counts.white[cls] || 0;
    const b = counts.black[cls] || 0;
    if (w === 0 && b === 0) continue;
    const meta_ = TAG_META[cls];
    const row = document.createElement("div");
    row.className = "stat-move-row";
    row.innerHTML = `
      <div class="stat-move-count white-bg">${w}</div>
      <div class="stat-move-tag">
        <span class="badge-circle ${meta_.className}">${meta_.symbol}</span>
        <span>${tagLabel(cls)}</span>
      </div>
      <div class="stat-move-count black-bg">${b}</div>
    `;
    target.appendChild(row);
  }
}

/* ============================================================
   EVAL CHART (split area chart)
   ============================================================ */
function renderChart() {
  if (wpChart) { wpChart.destroy(); wpChart = null; }

  const labels = [0, ...analysis.wp_series.map((p) => p.ply)];
  const values = [50, ...analysis.wp_series.map((p) => p.wp_white)];
  const dotColors = ["transparent", ...analysis.wp_series.map((p) => DOT_COLORS[p.classification] || "#888")];
  const dotRadii = [0, ...analysis.wp_series.map((p) =>
    ["blunder", "mistake", "miss", "brilliant", "great"].includes(p.classification) ? 5 : 3.2
  )];

  const ctx = document.getElementById("wp-chart").getContext("2d");

  // Plugin that draws the clean zero line (50%) at the chart center.
  const zeroLinePlugin = {
    id: "zeroLine",
    beforeDatasetsDraw(chart) {
      const { ctx, chartArea, scales } = chart;
      if (!chartArea) return;
      const y = scales.y.getPixelForValue(50);
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(chartArea.left, y);
      ctx.lineTo(chartArea.right, y);
      ctx.strokeStyle = "rgba(180,180,180,0.55)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.restore();
    },
  };

  wpChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "WP White",
        data: values,
        borderColor: "rgba(180,180,180,0.85)",
        borderWidth: 1.2,
        tension: 0.18,
        pointRadius: dotRadii,
        pointHoverRadius: 7,
        pointBackgroundColor: dotColors,
        pointBorderColor: "#ffffff",
        pointBorderWidth: 1,
        // Two-color fill: solid WHITE above 50%, dark/black BELOW.
        fill: {
          target: { value: 50 },
          above: "rgba(245,243,238,0.96)",
          below: "rgba(20,18,16,0.96)",
        },
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      layout: { padding: { top: 4, bottom: 4, left: 2, right: 2 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const idx = ctx.dataIndex;
              if (idx === 0) return t("review.initial_position");
              const p = analysis.wp_series[idx - 1];
              const sym = TAG_META[p.classification]?.symbol || "";
              return `Ply ${p.ply} (${p.color === "white" ? "W" : "B"}): WP ${p.wp_white}% · ${sym} ${tagLabel(p.classification)}`;
            },
          },
        },
      },
      scales: {
        // No grid, no ticks. The zero line is drawn by the plugin.
        y: { min: 0, max: 100, display: false, grid: { display: false } },
        x: { display: false, grid: { display: false } },
      },
    },
    plugins: [zeroLinePlugin],
  });
}

document.getElementById("btn-start-review").addEventListener("click", () => {
  document.getElementById("summary-screen").classList.add("d-none");
  document.getElementById("main").classList.remove("d-none");
  setupBoard();
  renderPlayerCards();
  renderMovesList();
  renderAccuracy();
  goToPly(0);
  setTimeout(() => board.resize(), 50);
});

/* ============================================================
   REVIEW (move-by-move)
   ============================================================ */
function renderPlayerCards() {
  const bottomColor = orientation;
  const topColor = bottomColor === "white" ? "black" : "white";
  document.getElementById("player-bottom").innerHTML = playerCardHtml(bottomColor);
  document.getElementById("player-top").innerHTML = playerCardHtml(topColor);
}

function playerCardHtml(color) {
  const name = color === "white" ? meta.white : meta.black;
  const rating = color === "white" ? meta.white_rating : meta.black_rating;
  const country = color === "white" ? meta.white_country : meta.black_country;
  const chip = color === "white" ? "white-chip" : "black-chip";
  const colorLabel = color === "white" ? t("review.color_white") : t("review.color_black");
  return `
    <div class="player-color-chip ${chip}"></div>
    <div class="player-info">
      <div class="player-name">
        <span class="flag">${flagEmoji(country)}</span>
        <strong>${name || "?"}</strong>
        <span class="player-elo">(${rating || "—"})</span>
      </div>
      <div class="player-color-label">${colorLabel}</div>
    </div>
  `;
}

function setupBoard() {
  chess = new Chess();
  board = Chessboard("board", {
    position: "start",
    orientation,
    pieceTheme: "https://chessboardjs.com/img/chesspieces/wikipedia/{piece}.png",
    showNotation: true,
    // Drag-and-drop active: serves the "variation explorer" (alternative moves).
    draggable: true,
    onDrop: handleBoardDrop,
    onSnapEnd: () => board.position(chess.fen(), false),
  });
  window.addEventListener("resize", () => {
    board.resize();
    redrawArrowAndOverlay();
  });
}

/* =============================================================
   VARIATION EXPLORER
   - When the user drags a piece:
     a) if the move matches the actual game and we're NOT already in
        variation, navigate forward normally (natural UX).
     b) otherwise enter (or stay in) variation mode: call /api/eval with
        the new FEN, update the eval bar live and draw the engine's
        best-move arrow.
   ============================================================= */
function handleBoardDrop(source, target) {
  if (!analysis) return "snapback";
  if (continuationActive) return "snapback"; // do not interfere with "punishment"
  if (source === target) return "snapback";

  const moveResult = chess.move({ from: source, to: target, promotion: "q" });
  if (moveResult === null) return "snapback";

  const userUci = source + target + (moveResult.promotion || "");

  // a) Match with the actual game move -> standard navigation.
  if (!variationActive && currentPly < analysis.moves.length) {
    const expected = analysis.moves[currentPly];
    if (expected.uci === userUci) {
      currentPly++;
      board.position(chess.fen(), false);
      highlightActiveRow();
      updateCurrentMoveInfo();
      updateCommentary();
      updateEvalBar();
      setTimeout(redrawArrowAndOverlay, 30);
      return;
    }
  }

  // b) Variation: update state and call the engine.
  if (!variationActive) enterVariationMode();
  variationPlyCount++;
  document.getElementById("variation-ply-text").textContent =
    t("variation.ply_count", { n: variationPlyCount });
  board.position(chess.fen(), false);
  fetchVariationEval(chess.fen());
}

function enterVariationMode() {
  variationActive = true;
  variationBaselinePly = currentPly;
  variationPlyCount = 0;
  document.getElementById("variation-banner").classList.remove("d-none");
  // Hide overlay tag and game arrow (they no longer apply).
  clearOverlay();
}

function exitVariationMode() {
  // goToPly() turns off the banner and restores the "real" position.
  goToPly(currentPly);
}

async function fetchVariationEval(fen) {
  const evalEl = document.getElementById("variation-eval");
  const bestEl = document.getElementById("variation-best");
  evalEl.textContent = t("variation.eval_loading");
  bestEl.textContent = "";

  try {
    const r = await fetch("/api/eval", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fen }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "/api/eval error");

    const wpWhite = data.wp_white;
    const evalWhiteCp = data.eval_white_cp;
    const barWhite = document.getElementById("eval-bar-white");
    const text = document.getElementById("eval-bar-text");
    barWhite.style.height = `${(wpWhite * 100).toFixed(1)}%`;
    let label;
    if (Math.abs(evalWhiteCp) >= 9000) {
      label = evalWhiteCp > 0 ? "M+" : "M-";
    } else {
      const pawns = (evalWhiteCp / 100).toFixed(1);
      label = (evalWhiteCp >= 0 ? "+" : "") + pawns;
    }
    text.textContent = label;

    let badge;
    if (Math.abs(evalWhiteCp) >= 9000) {
      badge = evalWhiteCp > 0 ? t("variation.eval_mate_w") : t("variation.eval_mate_b");
    } else {
      const v = `${(evalWhiteCp >= 0 ? "+" : "")}${(evalWhiteCp / 100).toFixed(2)}`;
      badge = t("variation.eval_value", { value: v });
    }
    evalEl.textContent = badge;
    bestEl.textContent = data.best_move_san ? t("variation.best", { san: data.best_move_san }) : "";

    clearOverlay();
    if (data.best_move_uci) {
      drawArrow(data.best_move_uci.slice(0, 2), data.best_move_uci.slice(2, 4));
    }
  } catch (err) {
    evalEl.textContent = t("variation.error", { msg: err.message });
    bestEl.textContent = "";
  }
}

document.getElementById("btn-variation-back").addEventListener("click", exitVariationMode);

function renderMovesList() {
  const list = document.getElementById("moves-list");
  list.innerHTML = "";
  const grouped = {};
  for (const m of analysis.moves) {
    if (!grouped[m.move_number]) grouped[m.move_number] = {};
    grouped[m.move_number][m.color] = m;
  }
  for (const num of Object.keys(grouped).sort((a, b) => +a - +b)) {
    const pair = grouped[num];
    for (const color of ["white", "black"]) {
      const m = pair[color];
      if (!m) continue;
      const meta_ = TAG_META[m.classification] || TAG_META.good;
      const row = document.createElement("div");
      row.className = "move-row";
      row.dataset.ply = m.ply;
      row.innerHTML = `
        <span class="move-num">${color === "white" ? num + "." : num + "..."}</span>
        <span class="move-san">${m.san}</span>
        <span class="move-tag ${meta_.className}">${meta_.symbol} ${tagLabel(m.classification)}</span>
      `;
      row.addEventListener("click", () => goToPly(m.ply));
      list.appendChild(row);
    }
  }
}

function renderAccuracy() {
  const a = analysis.accuracy || {};
  document.getElementById("accuracy-summary").textContent =
    t("review.accuracy_short", { white: a.white ?? "-", black: a.black ?? "-" });
}

/* ---------- move navigation ---------- */
function goToPly(ply) {
  stopContinuation();
  // Any "official" navigation exits variation mode.
  if (variationActive) {
    variationActive = false;
    variationPlyCount = 0;
    document.getElementById("variation-banner").classList.add("d-none");
  }
  currentPly = Math.max(0, Math.min(ply, analysis.moves.length));

  chess.reset();
  for (let i = 0; i < currentPly; i++) {
    const m = analysis.moves[i];
    chess.move({ from: m.uci.slice(0, 2), to: m.uci.slice(2, 4), promotion: m.uci[4] });
  }
  board.position(chess.fen(), false);

  highlightActiveRow();
  updateCurrentMoveInfo();
  updateCommentary();
  updateEvalBar();
  setTimeout(redrawArrowAndOverlay, 30);
}

function highlightActiveRow() {
  document.querySelectorAll(".move-row").forEach((r) => r.classList.remove("active"));
  if (currentPly > 0) {
    const row = document.querySelector(`.move-row[data-ply="${currentPly}"]`);
    if (row) {
      row.classList.add("active");
      row.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }
}

function updateCurrentMoveInfo() {
  const box = document.getElementById("current-move-info");
  if (currentPly === 0) {
    box.innerHTML = `<em>${t("review.initial_position")}</em>`;
    return;
  }
  const m = analysis.moves[currentPly - 1];
  const meta_ = TAG_META[m.classification] || TAG_META.good;
  const bestNotice = m.is_best
    ? t("review.best_played")
    : t("review.best_was", { san: m.best_move_san, drop: (m.wp_drop * 100).toFixed(1) });
  box.innerHTML = `
    <div>
      <span class="badge ${meta_.className}">${meta_.symbol} ${tagLabel(m.classification)}</span>
      <strong class="ms-2">${m.move_number}${m.color === "white" ? "." : "..."} ${m.san}</strong>
    </div>
    <div class="small mt-1">${bestNotice}</div>
  `;
}

/* ---------- COMMENTARY + PV CONTINUATION ---------- */
function updateCommentary() {
  const wrap = document.getElementById("move-commentary");
  const text = document.getElementById("commentary-text");
  const btnShow = document.getElementById("btn-show-continuation");
  const btnStop = document.getElementById("btn-stop-continuation");
  const status = document.getElementById("continuation-status");
  status.textContent = "";
  btnStop.classList.add("d-none");

  if (currentPly === 0) {
    wrap.classList.add("d-none");
    return;
  }

  const m = analysis.moves[currentPly - 1];
  if (!SHOW_COMMENTARY_FOR.has(m.classification)) {
    wrap.classList.add("d-none");
    return;
  }

  // Render the dynamic comment generated by the backend (key + params),
  // falling back to a localized default if missing.
  let commentText = renderTacticalComment(m.comment);
  if (!commentText) {
    commentText = t(`comment.${m.classification}_default`);
  }
  text.textContent = commentText;
  wrap.classList.remove("d-none");

  // The "punishment" uses the PV after the played move (refutation line).
  const hasPv = Array.isArray(m.pv_after_uci) && m.pv_after_uci.length >= 1;
  if (SHOW_CONTINUATION_FOR.has(m.classification) && hasPv) {
    btnShow.classList.remove("d-none");
  } else {
    btnShow.classList.add("d-none");
  }
}

document.getElementById("btn-show-continuation").addEventListener("click", () => {
  startContinuation();
});

document.getElementById("btn-stop-continuation").addEventListener("click", () => {
  stopContinuation();
  goToPly(currentPly);
});

function startContinuation() {
  if (currentPly === 0) return;
  const m = analysis.moves[currentPly - 1];
  if (!Array.isArray(m.pv_after_uci) || m.pv_after_uci.length < 1) return;

  continuationActive = true;
  continuationMoves = m.pv_after_uci.slice(0, 6);
  continuationStep = 0;

  // The punishment PV starts AFTER the played move.
  chess.reset();
  for (let i = 0; i < currentPly; i++) {
    const past = analysis.moves[i];
    chess.move({ from: past.uci.slice(0, 2), to: past.uci.slice(2, 4), promotion: past.uci[4] });
  }
  board.position(chess.fen(), false);
  setTimeout(redrawArrowAndOverlay, 30);

  document.getElementById("btn-show-continuation").classList.add("d-none");
  document.getElementById("btn-stop-continuation").classList.remove("d-none");
  document.getElementById("continuation-status").textContent = t("review.cont_playing");

  playNextContinuationStep();
}

function playNextContinuationStep() {
  if (!continuationActive) return;
  if (continuationStep >= continuationMoves.length) {
    document.getElementById("continuation-status").textContent =
      t("review.cont_end", { total: continuationMoves.length });
    return;
  }
  const uci = continuationMoves[continuationStep++];
  const moveObj = { from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] };
  const result = chess.move(moveObj);
  if (!result) {
    document.getElementById("continuation-status").textContent = t("review.cont_diverge");
    return;
  }
  board.position(chess.fen(), true);
  document.getElementById("continuation-status").textContent =
    t("review.cont_step", { n: continuationStep, total: continuationMoves.length, san: result.san });
  continuationTimer = setTimeout(playNextContinuationStep, 1100);
}

function stopContinuation() {
  continuationActive = false;
  if (continuationTimer) {
    clearTimeout(continuationTimer);
    continuationTimer = null;
  }
  document.getElementById("btn-stop-continuation").classList.add("d-none");
}

/* ---------- eval bar ---------- */
function updateEvalBar() {
  const barWhite = document.getElementById("eval-bar-white");
  const text = document.getElementById("eval-bar-text");

  let wpWhite = 0.5, evalWhiteCp = 0;
  if (currentPly > 0) {
    const m = analysis.moves[currentPly - 1];
    wpWhite = m.wp_white_after;
    evalWhiteCp = m.eval_white_cp;
  }
  barWhite.style.height = `${(wpWhite * 100).toFixed(1)}%`;

  let label;
  if (Math.abs(evalWhiteCp) >= 9000) {
    label = evalWhiteCp > 0 ? "M+" : "M-";
  } else {
    const pawns = (evalWhiteCp / 100).toFixed(1);
    label = (evalWhiteCp >= 0 ? "+" : "") + pawns;
  }
  text.textContent = label;
}

/* ---------- overlay tag + best-move arrow ---------- */
function clearOverlay() {
  document.querySelectorAll(".square-tag-overlay").forEach((n) => n.remove());
  const svg = document.getElementById("arrow-layer");
  svg.querySelectorAll("line, polygon").forEach((n) => n.remove());
}

function redrawArrowAndOverlay() {
  clearOverlay();
  if (currentPly === 0 || continuationActive || variationActive) return;
  const m = analysis.moves[currentPly - 1];
  drawTagOverlay(m.uci.slice(2, 4), m.classification);
  if (SHOW_ARROW_FOR.has(m.classification) && m.best_move_uci) {
    drawArrow(m.best_move_uci.slice(0, 2), m.best_move_uci.slice(2, 4));
  }
}

function drawTagOverlay(square, classification) {
  const meta_ = TAG_META[classification];
  if (!meta_) return;
  const sqEl = document.querySelector(`#board .square-${square}`);
  if (!sqEl) return;
  const overlay = document.createElement("div");
  overlay.className = `square-tag-overlay ${meta_.className}`;
  overlay.textContent = meta_.symbol;
  overlay.title = tagLabel(classification);
  sqEl.appendChild(overlay);
}

function drawArrow(fromSq, toSq) {
  const svg = document.getElementById("arrow-layer");
  const [fx, fy] = squareToCenter(fromSq);
  const [tx, ty] = squareToCenter(toSq);
  const ns = "http://www.w3.org/2000/svg";
  const line = document.createElementNS(ns, "line");
  line.setAttribute("x1", fx); line.setAttribute("y1", fy);
  line.setAttribute("x2", tx); line.setAttribute("y2", ty);
  line.setAttribute("stroke", "#1976d2");
  line.setAttribute("stroke-width", "2.2");
  line.setAttribute("stroke-linecap", "round");
  line.setAttribute("opacity", "0.85");
  line.setAttribute("marker-end", "url(#arrowhead)");
  svg.appendChild(line);
}

function squareToCenter(square) {
  const file = square.charCodeAt(0) - "a".charCodeAt(0);
  const rank = parseInt(square[1], 10) - 1;
  let col = file, rowFromTop = 7 - rank;
  if (orientation === "black") { col = 7 - file; rowFromTop = rank; }
  return [col * 12.5 + 6.25, rowFromTop * 12.5 + 6.25];
}

/* ---------- buttons / keyboard ---------- */
document.getElementById("btn-prev").addEventListener("click", () => goToPly(currentPly - 1));
document.getElementById("btn-next").addEventListener("click", () => goToPly(currentPly + 1));
document.getElementById("btn-start").addEventListener("click", () => goToPly(0));
document.getElementById("btn-end").addEventListener("click", () => goToPly(analysis.moves.length));
document.getElementById("btn-flip").addEventListener("click", () => {
  orientation = orientation === "white" ? "black" : "white";
  board.orientation(orientation);
  renderPlayerCards();
  setTimeout(redrawArrowAndOverlay, 30);
});
document.getElementById("btn-back-summary").addEventListener("click", () => {
  stopContinuation();
  document.getElementById("main").classList.add("d-none");
  document.getElementById("summary-screen").classList.remove("d-none");
});

/* Navbar "Back" arrow - contextual behavior:
   - on the move-by-move review: go back to the SUMMARY (no navigation)
   - on the SUMMARY: go to the dashboard with the same profile preloaded
   - still loading: default link behavior (dashboard) */
document.getElementById("btn-back-nav").addEventListener("click", (e) => {
  const onReview = !document.getElementById("main").classList.contains("d-none");
  const onSummary = !document.getElementById("summary-screen").classList.contains("d-none");

  if (onReview) {
    e.preventDefault();
    stopContinuation();
    document.getElementById("main").classList.add("d-none");
    document.getElementById("summary-screen").classList.remove("d-none");
    return;
  }
  if (onSummary) {
    e.preventDefault();
    if (username) sessionStorage.setItem("autoload_username", username);
    window.location.href = "/";
    return;
  }
});

document.addEventListener("keydown", (e) => {
  if (!analysis) return;
  if (e.key === "ArrowLeft")  goToPly(currentPly - 1);
  if (e.key === "ArrowRight") goToPly(currentPly + 1);
});

init();
