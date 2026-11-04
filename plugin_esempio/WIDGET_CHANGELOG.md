# Plugin Widget iPhone — changelog

## 1.1.0 — 4 novembre 2026
- **Token del widget** (campo nuovo in Impostazioni → Integrazioni): se è impostato, chi non manda l'intestazione `X-Widget-Token` riceve lo stesso 404 di un indirizzo sbagliato, quindi aprire l'indirizzo da un browser non mostra niente. Campo vuoto = controllo spento, come nella 1.0.
- Lo script di Scriptable ha la costante `TOKEN` (si ricorda nel Portachiavi, come l'indirizzo) e manda l'intestazione.
- Script: sfondo bordeaux scuro, riquadro in basso a sinistra con il testo allineato agli altri e sulla stessa linea di «Aggiornato…».

## 1.0.0 — 4 novembre 2026
- Primo rilascio: indirizzo pubblico di sola lettura (con segreto casuale) che dà casa, termostato, pompa di calore, acqua calda e fuori già pronti da mostrare nel widget medio di Scriptable. Solo GET, nessun comando.
