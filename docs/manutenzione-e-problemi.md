# Manutenzione, problemi e sicurezza

Backup, aggiornamenti, cosa fare se qualcosa non va e cosa è bene sapere sulla sicurezza.

## Manutenzione, backup, aggiornamenti

- **Storico:** tengo i dati dettagliati per 365 giorni; più indietro una media ogni 15 minuti, così il database non cresce all'infinito. Lo fa da solo, di notte. Per farlo subito: `python pdc_pannello.py compatta`.
- **Backup automatico:** ogni notte (tra le 3 e le 6; se il pannello era spento, al primo avvio utile) il pannello copia il database e `config.json` nella cartella **`backup`** accanto allo script, con la data nel nome, e tiene le ultime 7 copie. La copia è coerente anche mentre il pannello lavora. Per farla subito: `python pdc_pannello.py backup`. **Attenzione:** la copia sta sullo stesso computer (e `config.json` contiene password e token): ogni tanto copia la cartella su un altro disco o nel cloud, e non condividerla. **Per ripristinare:** ferma il pannello e lancia `python pdc_pannello.py ripristina`: elenca le copie (con data e dimensione), scegli il numero e scrivi SI. Il database attuale viene prima messo da parte nella cartella `backup` (`prima-del-ripristino-…`). Se c'è anche il `config.json` di quel giorno, ti chiede se rimetterlo: di solito conviene di no, perché i token rinnovabili di una copia vecchia possono essere già scaduti.
- **Azzerare il database:** *Impostazioni → Dati → Azzera il database…*. Cancella tutto lo storico (misure, grafici, anomalie) e riparte da zero; restano impostazioni, password e collegamenti. Chiede di scrivere **RESETTA** e la tua password, e **prima** fa una copia di sicurezza nella cartella dei backup (`prima-del-reset-AAAAMMGG-OOMMSS.db`). Se la copia non riesce, non cancella niente. Per tornare indietro: ferma il pannello e lancia `python pdc_pannello.py ripristina`, che elenca anche queste copie.
- **Registro:** in *Impostazioni → Registro* vedi le ultime righe del registro del pannello (password e token sono nascosti), le dimensioni di database e log e quando è stato fatto l'ultimo backup.
- **Backup a mano:** copia, **a pannello fermo**, `config.json` e `dati\pdc.db` (e le loro copie `pdc.db-wal`/`pdc.db-shm` se esistono). Basta questo per spostare tutto su un altro PC.
- **Aggiornare lo script:** ferma il pannello, sostituisci `pdc_pannello.py`, riavvia. `config.json` e lo storico restano; le nuove impostazioni vengono aggiunte da sole. (C'è anche l'aggiornamento senza fermare la raccolta, qui sotto.)
- **Spostare su un altro PC:** copia l'intera cartella; se usi Cloudflare ricontrolla il tunnel dalle Impostazioni.
- **Log:** `log\pdc_pannello.log` (si ruota da solo) e `log\cloudflared.log`.
- **Aggiornare senza fermare la raccolta:** sostituisci `pdc_pannello.py` **senza riavviare**, poi vai in *Impostazioni → Stato → Aggiornamento*. Il pannello confronta il file nuovo con quello in uso e ti dice cosa è cambiato:
  - *«È pronta la versione X»* → premi **Applica aggiornamento**: pagine, grafici, API, Impostazioni, Dispositivi, comandi allo Smarther e plugin vengono ricaricati a caldo e la lettura dei dati non si ferma mai (il server web non si riavvia, cambia solo il codice che risponde). Le pagine aperte si ricaricano da sole.
  - *«Serve il riavvio completo»* → è cambiato il nucleo (lettura e decodifica dei dati, database), Telegram, il tunnel o il backup: chiudi il programma Python e rilancialo, come sempre. Il solo numero di versione non richiede riavvio.
  - Se il file nuovo ha un errore (di scrittura o durante il caricamento) non cambia niente: continua il codice di prima e il motivo è scritto nel messaggio. Dopo un aggiornamento a caldo, la riga «Aggiornamento a caldo applicato» compare nel registro.
  - Limiti: la prima volta (per far entrare la funzione) serve un riavvio completo; funziona solo in modalità normale, non nella demo.
- **Come è organizzata la cartella:**
  ```
  pdc\
    pdc_pannello.py        il programma
    pdc_strumenti.py       lo strumento per SmartThings/ID
    config.json            le tue impostazioni (l'unico file che modifichi a mano)
    dati\                  database (pdc.db), backup\ (copie notturne), loghi\ (i tuoi loghi)
    log\                   pdc_pannello.log, cloudflared.log
    diagnostica\           i file da mandare a chi ti aiuta: diagnostica.json e i dump di pdc_strumenti.py
    cloudflared\           il programma del tunnel e il suo pid
    plugin\                le integrazioni (alexa.py…): vedi [plugin.md](plugin.md)
    docs\                  la documentazione (se la vuoi tenere qui)
  ```


---

## Se qualcosa non va

| Sintomo | Cosa fare |
|---|---|
| `python non è riconosciuto` | Python non è installato o non è nel PATH: reinstallalo da python.org spuntando *Add to PATH*. |
| “config.json non è un JSON valido” | Una virgola o una virgoletta di troppo/in meno. Annulla l'ultima modifica o controlla le righe vicine a quelle che hai toccato. |
| Non apro la pagina dal telefono | Stessa rete wifi? Hai consentito Python nel firewall (reti private)? Usa l'indirizzo `192.168.…` stampato all'avvio, non `localhost`. |
| “Un'altra copia del pannello è già attiva” | Ne hai già una avviata (anche senza finestra): chiudila da Gestione attività (cerca `python`/`pythonw`). |
| “Non riesco ad aprire la porta web” | Un altro programma usa la 7000: cambia `generale.porta` in `config.json`. |
| Non ricordo la password | `python pdc_pannello.py imposta-password` (a pannello fermo). |
| SmartThings 401 | Token personale scaduto (dura 24 ore): generane uno nuovo, oppure configura OAuth. |
| SmartThings 403 | Al token mancano i permessi (devices: list e see; per i comandi anche control / `x:devices:*`). |
| SmartThings 429 | Troppe richieste: alza `intervallo_pdc_secondi` (e, se l'hai acceso, `refresh.ogni_secondi`). |
| «refresh fermo: manca il permesso di comando» (Dashboard) | Il token SmartThings non ha lo scope `x:devices:*`: vedi «Servono permessi di comando sui token» in [comandi-e-regolazioni.md](comandi-e-regolazioni.md). Oppure spegni l'opzione. |
| SmartThings 402/“a pagamento” | Il servizio potrebbe essere diventato a pagamento: guarda il messaggio nella Dashboard. |
| “Pompa offline” | La pdc non risulta connessa a SmartThings (wifi/modulo): controlla nell'app SmartThings. |
| Netatmo: token rifiutato | Rigenera i token dal portale con gli scope dello Smarther e incollali (i vecchi sono invalidati). |
| Il COP o le “ore di funzionamento” sono strani | Controlla l'avviso giallo in Efficienza: se mancano dati di potenza o portata, il COP è parziale. |
| Mancano i dati del ciclo (frequenza, portata…) | Si aggiornano solo mentre la pdc è in funzione: da ferma non ci sono punti nuovi. |
| Il tunnel dice “non collegato” | Guarda `log\cloudflared.log`; prova `protocollo` = `http2`; controlla che il PC abbia internet; verifica che l'antivirus non blocchi `cloudflared.exe`. |
| Telegram non arriva | `python pdc_pannello.py telegram-prova` dice il motivo. Hai scritto almeno un messaggio al bot? |
| Non so cosa succede | Guarda `log\pdc_pannello.log`; se devi chiedere aiuto, `python pdc_pannello.py esporta-diagnostica` crea `diagnostica\diagnostica.json`, senza password né token. |


---

## Sicurezza: cosa è bene sapere

- Il pannello **legge** tutto e **comanda** solo quello che gli permetti (e solo se i token hanno il permesso di comando). Accendere/spegnere riscaldamento, acqua calda e Smarther si può anche da fuori casa; le **regolazioni della pompa** (codici FSV, temperatura e modo dell'acqua calda, modo della pompa, boost) solo dalla rete di casa, e il pannello rifiuta la richiesta se arriva dal tunnel. In ogni caso il login è sempre richiesto e tutto sta dietro un lucchetto.
- Il login è **sempre richiesto**. Le password sono salvate solo come impronta (PBKDF2), i tentativi sbagliati vengono limitati, i cookie di sessione sono protetti.
- In **rete di casa** il collegamento è HTTP normale: chi è sul tuo wifi potrebbe, in teoria, leggere la password mentre la digiti. Su una rete domestica fidata è ragionevole; se non lo è, usa l'indirizzo Cloudflare (HTTPS) anche da casa.
- **`config.json` è riservato**: contiene token SmartThings/Netatmo, il token del bot Telegram e quello del tunnel. Non condividerlo, non metterlo in cartelle sincronizzate pubblicamente.
- Se pubblichi il pannello su internet, scegli una password lunga e unica. Per una protezione in più puoi aggiungere **Cloudflare Access** (Zero Trust) davanti all'indirizzo: è gratuito per pochi utenti ed è un secondo lucchetto prima ancora di arrivare al login.
- Le **integrazioni a plugin** sono codice che gira con gli stessi permessi del pannello: copia nella cartella `plugin` solo file di cui ti fidi. Un plugin nuovo parte spento. Gli indirizzi pubblici dei plugin (`/plugin/…`) rispondono **senza login**: per questo ognuno deve difendersi da solo (Alexa usa un indirizzo segreto, riconosce solo la tua skill, scarta le richieste vecchie o ripetute e, se vuoi, controlla la firma di Amazon: vedi [plugin.md](plugin.md)) e il pannello limita comunque dimensione (64 KB) e frequenza (60 richieste al minuto per indirizzo).
- Il COP, le soglie degli allarmi e le fasce di funzionamento sono **stime ragionevoli, non misure certificate**: servono per capire le tendenze e accorgersi dei guasti, non per diagnosi tecniche.
