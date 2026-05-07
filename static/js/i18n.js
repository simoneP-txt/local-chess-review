/* =========================================================================
   i18n - bilingual UI (Italian default, English optional).

   Usage from any other JS file:
     - t("key", {param: value, ...})  -> returns translated string
     - applyI18nToDom()                -> walks the DOM and replaces all
                                          [data-i18n="key"] elements
     - getCurrentLang() / setLang(L)   -> read/write current language
     - LANG_STORAGE_KEY                -> localStorage key

   The current language is persisted in localStorage so it survives reloads.
   ========================================================================= */

const LANG_STORAGE_KEY = "chessreview.lang";
const DEFAULT_LANG = "it";

const TRANSLATIONS = {
  it: {
    // ----- common -----
    "common.back":              "← Indietro",
    "common.error":              "Errore",
    "common.search":             "Cerca",
    "common.loading":            "Caricamento...",

    // ----- dashboard -----
    "dashboard.brand":           "♟️ Chess Review Locale",
    "dashboard.title":           "Cerca giocatore",
    "dashboard.placeholder":     "es. magnuscarlsen",
    "dashboard.intro":           "Inserisci uno username della piattaforma per scaricare le sue partite.",
    "dashboard.period_notice":   "⚠ Vengono scaricate solo le partite del mese corrente: <strong>dal 1° {month} {year}</strong> a oggi. Le partite dei mesi precedenti non sono incluse.",
    "dashboard.no_recent":       "Nessuna ricerca recente.",
    "dashboard.no_games":        "Nessuna partita trovata per il mese corrente.",
    "dashboard.found_games":     "Trovate <strong>{count}</strong> partite per <strong>{username}</strong>.",
    "dashboard.fetching":        "Scarico le partite...",
    "dashboard.lang_label":      "Lingua",
    "dashboard.remove_recent":   "Rimuovi",

    // ----- game card -----
    "game.win":                  "Vittoria",
    "game.loss":                 "Sconfitta",
    "game.draw":                 "Patta",
    "game.no_date":              "data n/d",
    "game.result_label":         "Risultato:",
    "game.analyze_btn":          "Analizza",

    // ----- review loading -----
    "review.loading_title":      "Analisi in corso con Stockfish",
    "review.loading_status":     "Avvio analisi (profondità {depth})...",
    "review.loading_estimate":   "Analizzo ~{plies} mosse a profondità {depth}...",
    "review.loading_complete":   "Analisi completata.",

    // ----- puzzle card -----
    "puzzle.title":              "🧩 Allenati con un puzzle mentre aspetti",
    "puzzle.diff_easy":          "Facile (600-1200)",
    "puzzle.diff_medium":        "Medio (1200-1700)",
    "puzzle.diff_hard":          "Difficile (1700-2200)",
    "puzzle.diff_expert":        "Esperto (2200-2800)",
    "puzzle.btn_new":            "⟳ Nuovo",
    "puzzle.btn_hint":           "💡 Suggerimento",
    "puzzle.loading":            "Caricamento puzzle...",
    "puzzle.your_turn_white":    "Tocca a te (Bianco): trova la mossa migliore",
    "puzzle.your_turn_black":    "Tocca a te (Nero): trova la mossa migliore",
    "puzzle.wrong":              "Sbagliato! Riprova.",
    "puzzle.correct":            "Corretto! ✓",
    "puzzle.continue":           "Continua: trova la mossa successiva.",
    "puzzle.solved":             "Puzzle risolto! 🎉 Premi 'Nuovo' per un altro.",
    "puzzle.hint":               "Suggerimento: muovi da {square} ...",
    "puzzle.db_missing":         "DB puzzle non installato. Esegui: python download_puzzles.py",
    "puzzle.error":              "Errore puzzle: {msg}",
    "puzzle.meta":               "Rating {rating} · {themes}",

    // ----- summary -----
    "summary.title":             "★ Revisione partita",
    "summary.precision":         "Precisione",
    "summary.performance":       "Punteggio partita",
    "summary.performance_sub":   "(Performance Elo)",
    "summary.phases_title":      "Fasi della partita",
    "summary.phase_opening":     "Apertura",
    "summary.phase_middlegame":  "Mediogioco",
    "summary.phase_endgame":     "Finale",
    "summary.start_review":      "▶ Inizia revisione",
    "summary.vs":                "VS",

    // ----- review main -----
    "review.btn_start":          "⏮ Inizio",
    "review.btn_prev":           "◀ Precedente",
    "review.btn_next":           "Successiva ▶",
    "review.btn_end":            "Fine ⏭",
    "review.btn_flip":           "⇅ Ruota",
    "review.btn_summary":        "📊 Riepilogo",
    "review.moves_header":       "Mosse",
    "review.accuracy_short":     "Precisione: B {white}% / N {black}%",
    "review.color_white":        "BIANCO",
    "review.color_black":        "NERO",
    "review.initial_position":   "Posizione iniziale",
    "review.best_played":        "Mossa migliore secondo Stockfish.",
    "review.best_was":           "Mossa migliore: <strong>{san}</strong> · WP drop: {drop}%",
    "review.btn_show_cont":      "▶ Mostra la continuazione",
    "review.btn_stop_cont":      "◼ Torna alla mossa",
    "review.cont_playing":       "Linea di punizione: ecco come l'avversario sfrutta l'errore...",
    "review.cont_step":          "Mossa {n}/{total}: {san}",
    "review.cont_end":           "Fine della linea ({total} semimosse). Premi \"Torna alla mossa\" per uscire.",
    "review.cont_diverge":       "La PV diverge dalla posizione corrente, interrompo.",

    // ----- variation explorer -----
    "variation.title":           "📐 In variazione",
    "variation.ply_count":       "({n} mosse esplorate)",
    "variation.btn_back":        "↩ Torna alla partita",
    "variation.eval_loading":    "eval ...",
    "variation.eval_mate_w":     "Matto per il Bianco",
    "variation.eval_mate_b":     "Matto per il Nero",
    "variation.eval_value":      "eval {value}",
    "variation.best":            "best: {san}",
    "variation.error":           "errore: {msg}",

    // ----- move classification labels -----
    "tag.brilliant":             "Brillante",
    "tag.great":                 "Grande mossa",
    "tag.best":                  "Migliore",
    "tag.excellent":             "Eccellente",
    "tag.good":                  "Buona",
    "tag.book":                  "Teoria",
    "tag.inaccuracy":            "Imprecisione",
    "tag.mistake":               "Errore",
    "tag.miss":                  "Occasione persa",
    "tag.blunder":               "Grave errore",

    // ----- piece names (for tactical comments) -----
    "piece.pawn":                "il pedone",
    "piece.knight":              "il cavallo",
    "piece.bishop":              "l'alfiere",
    "piece.rook":                "la torre",
    "piece.queen":               "la donna",
    "piece.king":                "il re",
    "piece.generic":             "questo pezzo",

    // ----- tactical comment templates (key from analyzer) -----
    "comment.miss_default":           "Avevi un'opportunità tattica importante e questa mossa non la sfrutta: l'avversario rientra in partita.",
    "comment.hanging_severe":         "Così lasci {piece} senza adeguata difesa: alla fine degli scambi perdi un pezzo importante.",
    "comment.exchanges_lose_piece":   "Le difese di {piece} non bastano: alla fine degli scambi sulla casa di destinazione perdi materiale.",
    "comment.exchanges_lose_minor":   "Negli scambi che seguono perdi una piccola quantità di materiale (qualità o pedone in più per l'avversario).",
    "comment.punishment_capture":     "L'avversario può ora catturare {piece} con una sequenza tattica: avevi lasciato quel pezzo senza difese sufficienti.",
    "comment.blunder_default":        "Permetti all'avversario una sequenza tattica concreta che gli dà un vantaggio decisivo (materiale o di posizione).",
    "comment.mistake_default":        "Concedi all'avversario un'iniziativa concreta e una posizione sensibilmente migliore.",
    "comment.inaccuracy_default":     "L'avversario può sfruttare questa mossa per migliorare un po' la sua posizione.",

    // ----- months (for dashboard period notice) -----
    "month.0":  "gennaio", "month.1":  "febbraio", "month.2":  "marzo",
    "month.3":  "aprile",  "month.4":  "maggio",   "month.5":  "giugno",
    "month.6":  "luglio",  "month.7":  "agosto",   "month.8":  "settembre",
    "month.9":  "ottobre", "month.10": "novembre", "month.11": "dicembre",
  },

  en: {
    // ----- common -----
    "common.back":              "← Back",
    "common.error":              "Error",
    "common.search":             "Search",
    "common.loading":            "Loading...",

    // ----- dashboard -----
    "dashboard.brand":           "♟️ Chess Review Local",
    "dashboard.title":           "Search player",
    "dashboard.placeholder":     "e.g. magnuscarlsen",
    "dashboard.intro":           "Enter the username of the chess platform to download their games.",
    "dashboard.period_notice":   "⚠ Only games from the current month are downloaded: <strong>from {month} 1, {year}</strong> until today. Games from previous months are not included.",
    "dashboard.no_recent":       "No recent searches.",
    "dashboard.no_games":        "No games found for the current month.",
    "dashboard.found_games":     "Found <strong>{count}</strong> games for <strong>{username}</strong>.",
    "dashboard.fetching":        "Downloading games...",
    "dashboard.lang_label":      "Language",
    "dashboard.remove_recent":   "Remove",

    // ----- game card -----
    "game.win":                  "Win",
    "game.loss":                 "Loss",
    "game.draw":                 "Draw",
    "game.no_date":              "no date",
    "game.result_label":         "Result:",
    "game.analyze_btn":          "Analyze",

    // ----- review loading -----
    "review.loading_title":      "Analysis in progress with Stockfish",
    "review.loading_status":     "Starting analysis (depth {depth})...",
    "review.loading_estimate":   "Analyzing ~{plies} moves at depth {depth}...",
    "review.loading_complete":   "Analysis complete.",

    // ----- puzzle card -----
    "puzzle.title":              "🧩 Train with a puzzle while you wait",
    "puzzle.diff_easy":          "Easy (600-1200)",
    "puzzle.diff_medium":        "Medium (1200-1700)",
    "puzzle.diff_hard":          "Hard (1700-2200)",
    "puzzle.diff_expert":        "Expert (2200-2800)",
    "puzzle.btn_new":            "⟳ New",
    "puzzle.btn_hint":           "💡 Hint",
    "puzzle.loading":            "Loading puzzle...",
    "puzzle.your_turn_white":    "Your turn (White): find the best move",
    "puzzle.your_turn_black":    "Your turn (Black): find the best move",
    "puzzle.wrong":              "Wrong! Try again.",
    "puzzle.correct":            "Correct! ✓",
    "puzzle.continue":           "Continue: find the next move.",
    "puzzle.solved":             "Puzzle solved! 🎉 Press 'New' for another.",
    "puzzle.hint":               "Hint: move from {square} ...",
    "puzzle.db_missing":         "Puzzle DB not installed. Run: python download_puzzles.py",
    "puzzle.error":              "Puzzle error: {msg}",
    "puzzle.meta":               "Rating {rating} · {themes}",

    // ----- summary -----
    "summary.title":             "★ Game review",
    "summary.precision":         "Accuracy",
    "summary.performance":       "Game score",
    "summary.performance_sub":   "(Performance Elo)",
    "summary.phases_title":      "Game phases",
    "summary.phase_opening":     "Opening",
    "summary.phase_middlegame":  "Middlegame",
    "summary.phase_endgame":     "Endgame",
    "summary.start_review":      "▶ Start review",
    "summary.vs":                "VS",

    // ----- review main -----
    "review.btn_start":          "⏮ Start",
    "review.btn_prev":           "◀ Previous",
    "review.btn_next":           "Next ▶",
    "review.btn_end":            "End ⏭",
    "review.btn_flip":           "⇅ Flip",
    "review.btn_summary":        "📊 Summary",
    "review.moves_header":       "Moves",
    "review.accuracy_short":     "Accuracy: W {white}% / B {black}%",
    "review.color_white":        "WHITE",
    "review.color_black":        "BLACK",
    "review.initial_position":   "Initial position",
    "review.best_played":        "Best move according to Stockfish.",
    "review.best_was":           "Best move: <strong>{san}</strong> · WP drop: {drop}%",
    "review.btn_show_cont":      "▶ Show continuation",
    "review.btn_stop_cont":      "◼ Back to move",
    "review.cont_playing":       "Punishment line: here's how the opponent exploits the mistake...",
    "review.cont_step":          "Move {n}/{total}: {san}",
    "review.cont_end":           "End of line ({total} half-moves). Press \"Back to move\" to exit.",
    "review.cont_diverge":       "PV diverges from current position, stopping.",

    // ----- variation explorer -----
    "variation.title":           "📐 In variation",
    "variation.ply_count":       "({n} moves explored)",
    "variation.btn_back":        "↩ Back to game",
    "variation.eval_loading":    "eval ...",
    "variation.eval_mate_w":     "Mate for White",
    "variation.eval_mate_b":     "Mate for Black",
    "variation.eval_value":      "eval {value}",
    "variation.best":            "best: {san}",
    "variation.error":           "error: {msg}",

    // ----- move classification labels -----
    "tag.brilliant":             "Brilliant",
    "tag.great":                 "Great move",
    "tag.best":                  "Best",
    "tag.excellent":             "Excellent",
    "tag.good":                  "Good",
    "tag.book":                  "Book",
    "tag.inaccuracy":            "Inaccuracy",
    "tag.mistake":                "Mistake",
    "tag.miss":                  "Missed opportunity",
    "tag.blunder":               "Blunder",

    // ----- piece names -----
    "piece.pawn":                "the pawn",
    "piece.knight":              "the knight",
    "piece.bishop":              "the bishop",
    "piece.rook":                "the rook",
    "piece.queen":               "the queen",
    "piece.king":                "the king",
    "piece.generic":             "this piece",

    // ----- tactical comment templates -----
    "comment.miss_default":           "You had an important tactical opportunity and this move doesn't take it: your opponent gets back into the game.",
    "comment.hanging_severe":         "You leave {piece} without adequate defense: at the end of the exchanges you lose an important piece.",
    "comment.exchanges_lose_piece":   "The defenders of {piece} aren't enough: at the end of the exchanges on the destination square you lose material.",
    "comment.exchanges_lose_minor":   "In the exchanges that follow you lose a small amount of material (the exchange or an extra pawn for your opponent).",
    "comment.punishment_capture":     "Your opponent can now capture {piece} with a tactical sequence: you left that piece without enough defenders.",
    "comment.blunder_default":        "You allow your opponent a concrete tactical sequence that gives them a decisive advantage (material or positional).",
    "comment.mistake_default":        "You give your opponent concrete initiative and a significantly better position.",
    "comment.inaccuracy_default":     "Your opponent can use this move to slightly improve their position.",

    // ----- months -----
    "month.0":  "January",   "month.1":  "February", "month.2":  "March",
    "month.3":  "April",     "month.4":  "May",      "month.5":  "June",
    "month.6":  "July",      "month.7":  "August",   "month.8":  "September",
    "month.9":  "October",   "month.10": "November", "month.11": "December",
  },
};

function getCurrentLang() {
  const stored = localStorage.getItem(LANG_STORAGE_KEY);
  return (stored === "it" || stored === "en") ? stored : DEFAULT_LANG;
}

function setLang(lang) {
  if (lang !== "it" && lang !== "en") return;
  localStorage.setItem(LANG_STORAGE_KEY, lang);
}

/* Translate a key, optionally interpolating {param} placeholders. */
function t(key, params) {
  const lang = getCurrentLang();
  const dict = TRANSLATIONS[lang] || TRANSLATIONS[DEFAULT_LANG];
  let str = dict[key];
  if (str === undefined) {
    // fallback to default lang, then to the key itself for visibility
    str = TRANSLATIONS[DEFAULT_LANG][key] || key;
  }
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      str = str.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
    }
  }
  return str;
}

/* Walk the DOM and translate every element with [data-i18n]. The element's
   innerHTML is replaced with the translated string (HTML allowed). For
   placeholders, [data-i18n-placeholder] is supported. */
function applyI18nToDom() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    el.innerHTML = t(key);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    el.setAttribute("placeholder", t(el.getAttribute("data-i18n-placeholder")));
  });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    el.setAttribute("title", t(el.getAttribute("data-i18n-title")));
  });
  // <html lang="..."> is updated for accessibility / SEO.
  document.documentElement.setAttribute("lang", getCurrentLang());
}

/* Apply on initial load. */
document.addEventListener("DOMContentLoaded", applyI18nToDom);

// Expose globally (no module loader configured).
window.t = t;
window.applyI18nToDom = applyI18nToDom;
window.getCurrentLang = getCurrentLang;
window.setLang = setLang;
window.LANG_STORAGE_KEY = LANG_STORAGE_KEY;
