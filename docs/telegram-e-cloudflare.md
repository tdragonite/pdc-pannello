# Telegram e accesso da fuori casa

Gli avvisi su Telegram e il tunnel Cloudflare per raggiungere il pannello da internet.

## Telegram: gli avvisi

Il pannello ti scrive su Telegram quando succede qualcosa di strano e quando poi torna tutto a posto.

### Collegamento

Hai già un bot? Serve il **token** (da BotFather) e il **chat ID** (il numero della tua chat con il bot; per ottenerlo scrivi al bot e poi guarda `https://api.telegram.org/bot<TOKEN>/getUpdates`: cerca `"chat":{"id": …`).

Due modi, in `config.json` → `telegram` (o dalla pagina Impostazioni):

- **A, direttamente:** scrivi `bot_token` e `chat_id`.
- **B, da un tuo file `.json`:** metti il percorso in `file_credenziali` e i nomi delle chiavi in `chiave_token` e `chiave_chat`. Le chiavi dentro altre chiavi si scrivono col punto: `telegram.token`. Se sbagli, il messaggio d'errore elenca i nomi che ha trovato nel file (senza mostrare i valori).

Prova:

```
python pdc_pannello.py telegram-prova
```

Se arriva “Prova riuscita!” hai finito. Se no, ti dice perché (token sbagliato, chat sbagliata, bot bloccato, niente rete…).

In *Impostazioni → Avvisi* i riquadri sono, nell'ordine: *Telegram*, *credenziali da un tuo file*, *promemoria e limiti*, *silenzio notturno*, *riepilogo del mattino*, *testi dei messaggi*, *umidità in casa* e *quando avvisarti* (l'elenco degli allarmi, uno per uno).

### Cosa ti avvisa

Tutte le anomalie compaiono sempre anche nel pannello (banner in alto); qui decidi cosa mandare anche a Telegram. Gli allarmi con le soglie di partenza (modificabili in Impostazioni → *Quando avvisarti*, o in `config.json` → `allarmi`):

| Allarme | Scatta quando |
|---|---|
| Collegamenti e token | SmartThings o Netatmo non rispondono, token scaduto o in scadenza, pompa offline, errori dello script, tunnel Cloudflare che da internet non risponde per qualche minuto (si chiude da solo quando torna) |
| `nessun_dato` | nessun dato nuovo dalla pdc da 20 minuti |
| `pdc_ferma_con_richiesta` | lo Smarther chiede calore da 60 minuti ma la pompa è ferma |
| `casa_fredda` | meno di 17 °C in casa per 30 minuti |
| `umidita_alta` / `umidita_molto_alta` | umidità dello Smarther sopra 60 % / 70 % per 30 minuti (le soglie, i minuti e il rientro si cambiano in Impostazioni → Avvisi → *Umidità in casa*; valgono anche per i colori della Dashboard e le linee del grafico) |
| `cicli_brevi` | 4 o più cicli sotto i 10 minuti in 3 ore |
| `portata_bassa` | portata sotto 6 L/min per 15 minuti con il compressore acceso |
| `mandata_lontana` | mandata più di 12 °C sopra, o più di 8 °C sotto, la curva climatica per 40 minuti (sopra la curva la PdC sta spesso: compressore a bassa frequenza) |
| `acs_bassa` | acqua calda sotto 35 °C per 30 minuti |
| `cop_basso` | COP stimato delle ultime 24 ore sotto 2 |
| `sbrinamenti_frequenti` | 6 o più sbrinamenti in 3 ore |
| `sbrinamento_inizio` / `sbrinamento_fine` | **avvisi informativi**: la pompa inizia / finisce uno sbrinamento (alla fine: durata e consumo). Arrivano **sempre in silenzio** (senza suono), non si ripetono e non hanno “rientro”. Hanno lo stile degli altri messaggi (emoji e «Pompa di calore» in grassetto sulla prima riga). Si spengono o si riscrivono in *Impostazioni → Avvisi* (segnaposto `{impianto} {esterna} {ora}` / `{impianto} {durata} {consumo}`) |
| `finestre_chiudi` | **avviso informativo** (0.25.9): dopo una finestra aperta, quando nella Dashboard compare «Puoi chiudere» arriva un messaggio (calo di umidità e umidità di adesso). **Con suoneria** (di notte vale il silenzio notturno), una sola volta per apertura; dopo un riavvio del pannello un «Puoi chiudere» già comparso non si riannuncia. Si spegne o si riscrive in *Impostazioni → Avvisi* (segnaposto `{impianto} {calo} {um}`). Fa parte della prova marcata VENT: vedi [ventilazione-rimozione.md](ventilazione-rimozione.md). |

**Le soglie sono un punto di partenza, non verità**: dopo qualche settimana guardale con i tuoi dati e ritoccale.

Per spegnere un allarme: `"attivo": false`. Per spegnere intere famiglie, in `allarmi` aggiungi una voce con asterisco, per esempio `"st_conn_*": {"attivo": false}`.

### Comportamento

- Un problema ti arriva **una volta** e poi, quando passa, un messaggio “tutto di nuovo regolare” con la durata. Se resta aperto, un promemoria ogni 6 ore (`promemoria_ore`, 0 = mai).
- Un'anomalia che si risolve da sola entro il ritardo che imposti (`ritardo_minuti`, di solito 0) non ti scrive.
- Massimo 20 messaggi all'ora (`max_messaggi_ora`): oltre, ne salta alcuni e te lo dice.
- **Silenzio notturno** (spento di default): di notte i messaggi arrivano senza suoneria.
- **Riepilogo del mattino** (spento di default): ogni giorno all'ora scelta, un messaggio con energia, COP, ore di funzionamento e temperature di ieri. Se il silenzio notturno è acceso, aggiunge una sezione **«Di notte»** solo quando è successo qualcosa durante il silenzio: temperatura in casa variata di almeno 2 °C (cambia in Impostazioni), umidità sopra la soglia gialla, avvisi scattati e già rientrati.
- I **testi** si personalizzano in Impostazioni → *Testi dei messaggi* (segnaposto come `{testo}`, `{ora}`, `{durata}`; un modello scritto male, per esempio con `{testo.x}`, viene rifiutato quando salvi, con il motivo); per cambiare il testo di un solo allarme usa `per_codice` in `config.json`.


---

## Accesso da fuori casa (Cloudflare)

Serve un **dominio che usa i DNS di Cloudflare** (anche gratuito). Non devi aprire porte sul router: il PC si collega a Cloudflare e Cloudflare porta qui chi visita il tuo indirizzo.

Tutto da **Impostazioni → Accesso da internet**, con la procedura guidata:

1. **Crea un token API su Cloudflare** (la guida è nella pagina): <https://dash.cloudflare.com/profile/api-tokens> → *Create Token* → *Create Custom Token*, nome a piacere, e tre permessi:
   - **Account · Cloudflare Tunnel · Edit**
   - **Zone · DNS · Edit**
   - **Zone · Zone · Read**

   In *Zone Resources* scegli *Include → Specific zone* e il tuo dominio. Copia il token.
2. **Incollalo** e premi *Verifica*: il pannello trova i tuoi domini.
3. **Scegli** dominio e sottodominio (per esempio `pdc` → `pdc.tuodominio.it`) e premi *Crea tunnel e collega*. Il pannello crea tunnel e record DNS, scarica `cloudflared` (controllando che il file corrisponda all'impronta SHA-256 pubblicata da GitHub: se non coincide lo scarta) e lo avvia.

Il **token API non viene conservato**: lo usa e lo butta. In `config.json` resta solo il token del tunnel, che serve a far girare quel tunnel e nient'altro.

Poi apri semplicemente `https://pdc.tuodominio.it` — **niente `:7000`** (la porta è solo un dettaglio interno). Chiede comunque utente e password.

Cose da sapere:

- L'indirizzo funziona **solo mentre il pannello è acceso** sul tuo PC.
- Con l'interruttore *Avvia il tunnel insieme al pannello* parte da solo; se cade, il pannello lo riavvia, e dopo tre cadute di fila ti avvisa.
- Se il tuo antivirus blocca `cloudflared.exe` (è il programma ufficiale Cloudflare, scaricato da GitHub), scaricalo a mano da <https://github.com/cloudflare/cloudflared/releases> e scrivi il percorso in `config.json` → `cloudflare` → `percorso_cloudflared`.
- Se il tunnel non si collega perché la tua rete blocca il traffico UDP: in `config.json` → `cloudflare` → `protocollo` cambia `"auto"` in `"http2"`.
- Se cambi la porta del pannello (`generale.porta`), rifai la procedura (*Rimuovi*, poi di nuovo).
- Per smontare tutto: *Rimuovi* nella stessa pagina (se dai di nuovo un token cancella anche tunnel e DNS su Cloudflare, altrimenti li cancelli tu dal loro sito).
- Il log del tunnel è in `log\cloudflared.log`.
