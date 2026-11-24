# Plugin Widget iPhone — changelog

## 1.3.0 — 14 novembre 2026
- Con `?storico=6` (da 1 a 24 ore) la risposta contiene anche temperatura di casa, umidità e target a passi di 5 minuti, per il grafico dell'app dell'orologio. Senza il parametro la risposta è identica a prima: il widget dell'iPhone non cambia.

## 1.2.0 — 13 novembre 2026
- Il riquadro in basso mostra anche «Finestre aperte» (tinta viola) e «Puoi chiudere» (verde), con la stessa priorità della Dashboard: sta prima della ripartenza. Lo script di Scriptable ha i due colori nuovi; senza aggiornarlo i riquadri appaiono in grigio.

## 1.1.1 — 11 novembre 2026
- Il riquadro della ripartenza dice sempre «Riparte alle 00:15», anche quando è dopo mezzanotte: con «domani» la scritta non entrava e l'ora veniva tagliata. Lo script di Scriptable non cambia.

## Script Scriptable (senza cambio di versione del plugin) — 4 novembre 2026
- Toccando la riga in basso a destra (l'ora di aggiornamento) parte il Comando rapido **Aggiorna widget** (costante `COMANDO_AGGIORNA`: nell'app Comandi, azione Scriptable «Refresh All Widgets» + «Vai alla schermata Home»); il resto del widget continua ad aprire il pannello. iOS decide da solo quando aggiornare davvero (la richiesta dello script è ogni 15 minuti).

## 1.1.0 — 4 novembre 2026
- **Token del widget** (campo nuovo in Impostazioni → Integrazioni): se è impostato, chi non manda l'intestazione `X-Widget-Token` riceve lo stesso 404 di un indirizzo sbagliato, quindi aprire l'indirizzo da un browser non mostra niente. Campo vuoto = controllo spento, come nella 1.0.
- Lo script di Scriptable ha la costante `TOKEN` (si ricorda nel Portachiavi, come l'indirizzo) e manda l'intestazione.
- Script: sfondo bordeaux scuro, riquadro in basso a sinistra con il testo allineato agli altri e sulla stessa linea di «Aggiornato…».

## 1.0.0 — 4 novembre 2026
- Primo rilascio: indirizzo pubblico di sola lettura (con segreto casuale) che dà casa, termostato, pompa di calore, acqua calda e fuori già pronti da mostrare nel widget medio di Scriptable. Solo GET, nessun comando.
