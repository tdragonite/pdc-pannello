# Comandi e regolazioni

Accendere e spegnere dal pannello, e cambiare le regolazioni della pompa (FSV, acqua calda, boost). Tutto dietro un lucchetto.

## Accendere e spegnere dal pannello

Dalla **Dashboard**, nella scheda *Stato del sistema*, ci sono tre interruttori: **Riscaldamento casa**, **Acqua calda sanitaria** e **Termostato Smarther**.

- Sono **bloccati** finché non premi **Sblocca comandi** (si richiudono da soli dopo 30 secondi): serve a non spegnere il riscaldamento con un tocco sbagliato dal telefono.
- **Spegnere** chiede una conferma, accendere no.
- Dopo il tocco l'interruttore si sposta subito e compare il badge giallo **ATTENDI…** finché il comando non è finito (per lo Smarther fino a una decina di secondi, perché il pannello controlla che Netatmo abbia davvero cambiato stato). Poi il badge torna a ACCESO/SPENTO e il pannello rilegge lo stato.
- Lo sblocco dura 30 secondi **dall'ultima azione**: ogni comando lo fa ripartire.
- Ogni comando finisce nell'elenco **Comandi dati dal pannello** (pagina *Dispositivi*, a pagine da 5), con data, ora, indirizzo da cui è partito ed esito. Non c'è nessun avviso su Telegram.
- I comandi funzionano anche da fuori casa, con lo stesso login del pannello.
- **Lo Smarther si spegne in due modi diversi**, e il pannello li riconosce entrambi: dal pannello la *casa* va in antigelo (`therm_mode: hg`) e la stanza resta in «home» a 7 °C; dalla sua app invece è la *stanza* ad andare in antigelo (`therm_setpoint_mode: hg`) con la casa in «schedule». Per spegnere il pannello mette la casa in antigelo; per accendere rimette la casa in programma **e** la stanza in «home». Dopo ogni tentativo rilegge da Netatmo: se lo stato non è cambiato passa alla strada successiva, e nel registro vedi quale ha funzionato e le risposte di Netatmo per le altre.

**Servono permessi di comando sui token** (finché non li dai, il pannello legge e basta, e un comando riceve una risposta chiara: «il token non ha il permesso»).

*SmartThings con OAuth* (come nel punto 4c di [installazione.md](installazione.md)):
1. Vedi l'ID della tua app: `smartthings apps`.
2. Aggiungi il permesso di comando: `smartthings apps:oauth:update ID_DELLA_APP`; quando chiede gli *scopes* tieni `r:devices:*` e aggiungi **`x:devices:*`** (il Redirect URI resta `https://httpbin.org/get`). Se la CLI non te lo lascia fare, crea una nuova app con `smartthings apps:create` mettendo entrambi gli scopes e usa il nuovo Client ID e Secret.
3. **Ferma il pannello**, poi con il nuovo `pdc_strumenti.py` lancia di nuovo `python pdc_strumenti.py st-oauth` e rifai il consenso: i token nuovi hanno anche il permesso di comando e vengono scritti in `config.json`.
4. Riavvia il pannello.

*SmartThings con il token personale (PAT)*: alla creazione spunta anche **Devices → Control all devices**.

*Netatmo*: su <https://dev.netatmo.com> → *My apps* → la tua app → *Token generator*, spunta **`read_smarther`** e **`write_smarther`** (più quelli che già usavi), genera il token. **Ferma il pannello**, copia i due token nuovi (`access_token` e `refresh_token`) in `config.json`, sezione `netatmo`, e riavvia. Rigenerarli invalida i vecchi.

*Prova consigliata*: sblocca, e prova prima con **Acqua calda sanitaria** (spegni e riaccendi). Se qualcosa non va, il messaggio ti dice se manca un permesso o cosa ha risposto il servizio. Se Netatmo rifiuta il comando, il messaggio e l'ultima riga del registro in *Dispositivi* riportano la sua risposta: mandala a chi ti aiuta.

## Regolazioni della pompa

Nella pagina *Dispositivi*, scheda **Regolazioni** (indirizzo `#/dispositivi/installatore`):

- In alto: temperatura e modo dell'acqua calda, modo della pompa (Riscaldamento, Raffrescamento, Automatico) e il **Boost** dell'acqua calda (⚡ alza il setpoint all'obiettivo scelto e mette il modo «Forzato»; finisce quando l'acqua arriva all'obiettivo o dopo 2 ore, poi rimette modo e setpoint di prima, anche se nel frattempo il pannello si riavvia; ■ lo ferma a mano).
- Sotto: le impostazioni installatore (codici FSV). Si cambia un valore alla volta: **Sblocca modifiche** (30 secondi), la matita ✎ sulla riga, il nuovo valore e una conferma. Il pannello accetta solo codici e limiti del manuale; la riga resta «in attesa» finché la pompa non conferma il valore nuovo.
- In fondo, la **Cronologia delle modifiche**: ogni cambio visto, con il valore di prima e di dopo, e se l'ha fatto il pannello o è stato letto dalla pompa.
- **Solo dalla rete di casa**: da fuori (tunnel Cloudflare) il lucchetto non compare e le richieste vengono rifiutate.
- Servono gli stessi permessi di comando di SmartThings degli interruttori (vedi [Accendere e spegnere dal pannello](#accendere-e-spegnere-dal-pannello)). Cambiare un FSV a caso può far lavorare male la pompa: nel dubbio chiedi all'installatore.
