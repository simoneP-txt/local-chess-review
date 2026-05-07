Posiziona qui l'eseguibile di Stockfish.

Windows: scarica "Stockfish 16.x" da https://stockfishchess.org/download/
         (sezione "Windows", scegli la build avx2 o popcnt a seconda della CPU).
         Estrai lo .zip e copia "stockfish-windows-x86-64-avx2.exe" in questa cartella
         rinominandolo in:   stockfish.exe

Risultato finale atteso:
   engine/stockfish.exe

Linux/Mac (opzionale): metti l'eseguibile come "engine/stockfish".

Per cambiare la profondità di analisi (default 15) imposta la variabile d'ambiente:
   PowerShell:  $env:REVIEW_DEPTH = "18"; python app.py
