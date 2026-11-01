Posiziona qui l'eseguibile di Stockfish.

Windows: scarica "Stockfish 16.x" da https://stockfishchess.org/download/
         (sezione "Windows", scegli la build avx2 o popcnt a seconda della CPU).
         Estrai lo .zip e copia "stockfish-windows-x86-64-avx2.exe" in questa cartella
         rinominandolo in:   stockfish.exe

Risultato finale atteso:
   engine/stockfish.exe

Linux/Mac (opzionale): metti l'eseguibile come "engine/stockfish".

La profondità si sceglie dal pannello Engine della dashboard: default 15 (12 con 2 core o meno).
REVIEW_DEPTH cambia il default:
   PowerShell:  $env:REVIEW_DEPTH = "18"; python app.py
