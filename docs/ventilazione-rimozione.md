# «Puoi chiudere» (0.25.5, corretto nella 0.25.7, messaggio Telegram dalla 0.25.9): come toglierlo

Prova introdotta l'11/11/2026. Tutto ciò che la riguarda è marcato con la parola **VENT** nei sorgenti. Per toglierla:

1. `sorgenti/2_web.py`: cancella il blocco tra `# VENT-INIZIO` e `# VENT-FINE` (la funzione `vent_stato` e le sue costanti `VENT_*`, compresa `VENT_CALDO`) e la riga `"vent": vent_stato(...)  # VENT` dentro il dizionario `"casa"`.
   `sorgenti/3_telegram.py` e `sorgenti/4_impostazioni.py` (messaggio Telegram, 0.25.9): cancella i blocchi o le righe marcati `VENT` (`avvisa_finestre`, la sua chiamata nella `Sentinella.run`, `vent_primo`, le voci `finestre_chiudi` nei titoli, nei testi, negli allarmi, nelle descrizioni, in `AVVISI_INFORMATIVI` e nei campi dei testi).
2. `sorgenti/web/app.js`: cancella il blocco tra `// VENT-INIZIO` e `// VENT-FINE` (`ventBox`), la riga `: c.vent ? ventBox(c.vent)   // VENT` nella catena di `html('c-n', ...)`, i blocchi `{v: '0.25.9' ...}` e `{v: '0.25.5' ...}` del changelog (marcati VENT) e le due righe marcate `// VENT` nel blocco `{v: '0.25.7' ...}`.
3. `sorgenti/web/app.css`: cancella la riga marcata `VENT` (le due regole `.ora-n .badge.acceso`, subito sotto `.ora-n .badge{...}`).
4. `sorgenti/1_nucleo.py`: la `VERSIONE` si lascia com'è (dalla 0.25.6 la versione contiene anche altro); se vuoi, alla prossima consegna sale di un ritocco.
5. Ricostruisci con `python costruisci.py`; per controllare che non resti niente: `grep -rn VENT sorgenti/`.
6. Cancella questo file e la sua riga in docs/PIANO.md.

Il pannello torna esattamente a prima: la ripartenza occupa di nuovo da sola il suo spazio. Non si tocca il database.
