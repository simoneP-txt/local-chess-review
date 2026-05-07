# local-chess-review

Web app **locale** per la revisione di partite di scacchi: scarica le tue
partite tramite l'API pubblica della piattaforma su cui giochi, le analizza
con [Stockfish](https://stockfishchess.org/) in locale e mostra una scacchiera
interattiva con etichette per mossa (Migliore, Imprecisione, Errore, Grave
errore, Brillante, …) e una eval bar.

Pensata per girare sul PC e per essere usata anche dal telefono sulla stessa
rete Wi-Fi (LAN).

UI bilingue (Italiano default, Inglese opzionale) selezionabile dalla
dashboard.

> [!IMPORTANT]
> ## Disclaimer
>
> - Questo è un **progetto personale, non commerciale, a scopo
>   educativo / di studio**.
> - **Non** è affiliato, sponsorizzato o approvato da alcun fornitore di
>   servizi scacchistici.
> - I dati delle partite vengono recuperati esclusivamente tramite l'**API
>   HTTP pubblica** documentata della piattaforma di scacchi scelta. Non
>   vengono usate scraping techniques, credenziali, sessioni private né
>   API non documentate.
> - Tutta l'**analisi avviene in locale** sulla macchina dell'utente: non
>   c'è nessun server proprio che riceve, memorizza o ridistribuisce le
>   partite.
> - Lo stile grafico è generico (Bootstrap 5 + chessboard.js) e non riproduce
>   marchi, loghi o asset di alcun fornitore terzo.
> - L'utente è responsabile di rispettare i Termini di Servizio della
>   piattaforma da cui scarica le proprie partite, in particolare i limiti
>   di rate dell'API pubblica.
> - **Stockfish** è un motore scacchistico open source distribuito sotto
>   licenza GPLv3: scaricarlo e usarlo è gratis ma l'eseguibile **non è
>   incluso** in questa repo (vedi sezione *Setup*).
> - I puzzle (opzionali, mostrati durante il caricamento) provengono dal
>   [Lichess Puzzle Database](https://database.lichess.org/), distribuito
>   sotto licenza CC0.

---

## Funzionalità principali

- 📥 Download partite del **mese corrente** dell'utente specificato
  (con bandiera del paese e Elo di entrambi i giocatori).
- 🐟 Analisi con **Stockfish locale** (depth configurabile, default 15).
- 🏷️ Tag per ogni mossa: *Brillante, Grande mossa, Migliore, Eccellente,
  Buona, Teoria, Imprecisione, Errore, Occasione persa, Grave errore*
  (modello *Expected Points* basato su Win Probability).
- 📊 Schermata di **riepilogo**: precisione %, *Performance Elo* per
  giocatore, grafico WP, fasi di partita (Apertura / Mediogioco / Finale).
- ▶️ **Revisione interattiva** mossa per mossa con commento tattico,
  freccia sulla mossa migliore, "linea di punizione" e *variation explorer*
  (puoi giocare mosse alternative trascinando i pezzi).
- 🧩 **Mini-puzzle** Lichess durante l'attesa dell'analisi.
- 🌍 UI bilingue **IT / EN**.
- 📱 Accessibile dal telefono sulla stessa rete Wi-Fi.

---

## Screenshot

> Aggiungi qui i tuoi screenshot e referenziali con percorsi tipo
> `docs/screenshots/dashboard.png`. Esempio:

```markdown
![Dashboard](docs/screenshots/dashboard.png)
![Riepilogo partita](docs/screenshots/summary.png)
![Revisione mossa per mossa](docs/screenshots/review.png)
```

---

## Setup (Windows)

### 1. Python e dipendenze

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> **PowerShell 5.1**: l'operatore `&&` non esiste. Esegui i comandi uno
> alla volta o concatena con `;` + `if ($?)`.
>
> Se PowerShell blocca l'attivazione del venv:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.

### 2. Stockfish

1. Scarica l'ultima build da
   <https://stockfishchess.org/download/> (Windows → variante
   `avx2`; se la tua CPU non la supporta, `popcnt`).
2. Estrai lo zip.
3. Copia l'eseguibile in `engine/`, rinominandolo in **`stockfish.exe`**:

   ```text
   engine/stockfish.exe
   ```

L'app cerca l'eseguibile in `engine/stockfish.exe` (Windows) oppure
`engine/stockfish` (Linux / macOS). Se manca, l'endpoint `/api/analyze`
risponde con un errore esplicito.

### 3. (Opzionale) Database puzzle Lichess

La schermata di caricamento mostra un mini-puzzle Lichess mentre Stockfish
analizza. Per attivarlo, scarica una sola volta il DB puzzle:

```powershell
python download_puzzles.py
```

Lo script scarica ~250 MB compressi da `database.lichess.org`, filtra in
streaming e produce `engine/puzzles.db` (~20 MiB con i cap di default).
Senza il DB il resto dell'app funziona comunque, semplicemente i mini-puzzle
non vengono mostrati.

Per ridurre un DB esistente senza ri-scaricarlo:

```powershell
python trim_puzzles.py --per-band 5000
```

### 4. (Opzionale) Profondità di analisi

Default = 15 (veloce). Per maggiore precisione (più lento):

```powershell
$env:REVIEW_DEPTH = "18"
python app.py
```

---

## Avvio

```powershell
.\.venv\Scripts\Activate.ps1
python app.py
```

In console verranno stampate gli URL su cui l'app è raggiungibile:

```text
 * Running on http://127.0.0.1:5000
 * Running on http://192.168.1.X:5000
```

### Dal PC

Apri <http://127.0.0.1:5000>.

### Dal telefono (stessa Wi-Fi)

1. Trova l'IP del PC: in PowerShell digita `ipconfig` e cerca
   "Indirizzo IPv4" della scheda Wi-Fi (es. `192.168.1.42`).
2. Sul telefono apri il browser e vai su `http://192.168.1.42:5000`.
3. **Firewall**: la prima volta Windows chiede se autorizzare Python ad
   accettare connessioni in ingresso → seleziona "Reti private". Se l'app
   non risponde, crea una regola in entrata su Windows Defender Firewall
   che apra la porta TCP 5000.

---

## Come si usa

1. **Dashboard** (`/`): scegli la lingua, inserisci uno username della tua
   piattaforma di scacchi (l'app usa l'API pubblica di lettura partite),
   premi *Cerca*. Le partite del mese corrente compaiono ordinate dalla più
   recente.
2. Clicca **Analizza**: parte l'analisi con Stockfish (1-3 minuti a
   depth 15). Durante l'attesa vedi un mini-puzzle e una progress bar.
3. Compare la **schermata di Riepilogo**: precisione %, Performance Elo,
   grafico Win Probability, fasi della partita.
4. Premi **▶ Inizia revisione** per la modalità mossa per mossa:
   - scacchiera centrata, orientata dal punto di vista del giocatore cercato;
   - eval bar a sinistra;
   - tag della mossa sulla casa di destinazione + freccia sulla mossa
     migliore in caso di errore;
   - lista delle mosse a destra;
   - tasti `←` / `→` o pulsanti `◀ ▶ ⏮ ⏭`;
   - **variation explorer**: trascina un pezzo per esplorare una mossa
     alternativa, eval bar e freccia migliore si aggiornano in tempo reale.

---

## Stack tecnologico

- **Backend**: Python 3, [Flask](https://flask.palletsprojects.com/),
  [python-chess](https://python-chess.readthedocs.io/),
  [Stockfish](https://stockfishchess.org/) (motore esterno).
- **Frontend**: Bootstrap 5, jQuery,
  [chessboard.js](https://chessboardjs.com/),
  [chess.js](https://github.com/jhlywa/chess.js),
  [Chart.js](https://www.chartjs.org/) — tutti tramite CDN.
- **DB locale (opzionale)**: SQLite + dataset puzzle Lichess.

---

## Struttura del progetto

```text
local-chess-review/
├── app.py                # server Flask + endpoints API
├── chess_api.py          # wrapper API pubblica (download partite)
├── analyzer.py           # engine + algoritmo di classificazione mosse
├── download_puzzles.py   # script one-shot: scarica + filtra puzzle Lichess
├── trim_puzzles.py       # script one-shot: riduce un puzzles.db esistente
├── requirements.txt
├── engine/
│   ├── README.txt        # istruzioni per scaricare Stockfish
│   ├── stockfish.exe     # (DA SCARICARE MANUALMENTE)
│   └── puzzles.db        # (CREATO DA download_puzzles.py)
├── templates/
│   ├── index.html        # dashboard: ricerca + lista partite
│   └── review.html       # pagina di revisione interattiva
└── static/
    ├── css/style.css
    └── js/
        ├── i18n.js       # dizionari IT/EN + helper t()
        ├── index.js
        └── review.js
```

---

## Personalizzazioni rapide

- **Più precisione → più lento**: aumenta `REVIEW_DEPTH` (env) o `depth=`
  in `GameAnalyzer`.
- **Soglie classificazione**: `WP_THRESHOLDS` in `analyzer.py` (in WP).
- **Brillante più / meno restrittivo**: `BRILLIANT_SEE_MAX`,
  `BRILLIANT_WP_FLOOR`, `BRILLIANT_WP_CEILING`, `BRILLIANT_2ND_GAP`.
- **Rilevamento teoria più lungo**: aumenta `BOOK_PLY` (default 16 plies).
- **Aggiungere una terza lingua**: aggiungi un sotto-dizionario in
  `static/js/i18n.js` e una `<option>` nel selettore in
  `templates/index.html`.

---

## Test rapidi senza UI

```powershell
# Lista partite del mese corrente per uno username
python chess_api.py <username>

# Analisi diretta di un PGN su disco
python analyzer.py engine\stockfish.exe game.pgn
```

---

## Risoluzione problemi

- **`Stockfish not found`** → controlla che esista `engine/stockfish.exe`.
- **Errore 403 / rate limit dall'API** → User-Agent troppo generico oppure
  troppe richieste. Aumenta il delay o personalizza l'header in
  `chess_api.py`.
- **Il telefono non vede il PC** → firewall Windows: consenti la porta
  5000 in ingresso per le reti private; PC e telefono devono essere sulla
  stessa Wi-Fi.
- **Analisi troppo lenta** → riduci `REVIEW_DEPTH` (es. 12) oppure scegli
  una partita più corta.
- **"DB puzzle non installato"** → esegui `python download_puzzles.py`
  una volta.

---

## Licenza

Codice sorgente: **MIT** (vedi `LICENSE` se presente, altrimenti scegline
una in fase di pubblicazione).

Componenti di terze parti — soggetti alle rispettive licenze:

- **Stockfish** — GPLv3 (eseguibile non incluso, da scaricare).
- **Lichess Puzzle Database** — CC0 (file `puzzles.db` non incluso, da
  scaricare).
- Tutte le librerie frontend sono caricate da CDN pubbliche e mantengono
  le proprie licenze originali.
