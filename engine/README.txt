Posiziona qui l'eseguibile di Stockfish.

Windows: scarica l'ultima versione da https://stockfishchess.org/download/
         (sezione "Windows", link "Download (x86-64)": il binario "universal"
         sceglie da solo le istruzioni adatte alla CPU).
         Estrai lo .zip e copia l'eseguibile .exe che contiene in questa cartella
         rinominandolo in:   stockfish.exe

Risultato finale atteso:
   engine/stockfish.exe

Linux/Mac (opzionale): metti l'eseguibile come "engine/stockfish".

La profondità si sceglie dal pannello Engine della dashboard: default 15 (12 con 2 core o meno).
REVIEW_DEPTH cambia il default:
   PowerShell:  $env:REVIEW_DEPTH = "18"; python app.py
