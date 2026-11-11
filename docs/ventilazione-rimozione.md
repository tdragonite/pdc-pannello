# «Puoi chiudere» (0.25.5): come toglierlo

Prova introdotta l'11/11/2026. Tutto ciò che la riguarda è marcato con la parola **VENT** nei sorgenti. Per toglierla:

1. `sorgenti/2_web.py`: cancella il blocco tra `# VENT-INIZIO` e `# VENT-FINE` (la funzione `vent_stato` e le sue costanti `VENT_*`) e la riga `"vent": vent_stato(...)  # VENT` dentro il dizionario `"casa"`.
2. `sorgenti/web/app.js`: cancella il blocco tra `// VENT-INIZIO` e `// VENT-FINE` (`ventBox`), la riga `: c.vent ? ventBox(c.vent)   // VENT` nella catena di `html('c-n', ...)`, e il blocco `{v: '0.25.5' ...}` del changelog (marcato VENT).
3. `sorgenti/web/app.css`: cancella la riga marcata `VENT` (le due regole `.ora-n .badge.acceso`, subito sotto `.ora-n .badge{...}`).
4. `sorgenti/1_nucleo.py`: la `VERSIONE` si lascia com'è (dalla 0.25.6 la versione contiene anche altro); se vuoi, alla prossima consegna sale di un ritocco.
5. Ricostruisci con `python costruisci.py`; per controllare che non resti niente: `grep -rn VENT sorgenti/`.
6. Cancella questo file e la sua riga in docs/PIANO.md.

Il pannello torna esattamente a prima: la ripartenza occupa di nuovo da sola il suo spazio. Non si tocca il database.
