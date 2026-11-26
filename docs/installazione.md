# Installazione e primo avvio

Tutto quello che serve per far partire il pannello: Python, primo avvio, prova con i dati finti, collegamento a SmartThings e Netatmo, password e avvio vero.

## Cosa serve

- Una macchina **sempre accesa** (qui si parla di un **server Linux**; per provare va bene anche un Mac): il pannello raccoglie i dati solo finché gira.
- **Python 3.10 o più recente** (`python3 --version`).
- Un account SmartThings con la pompa di calore dentro (si usa l'API di SmartThings, non c'è nessun accesso diretto alla pompa).
- Facoltativi: l'app Netatmo (Smarther), un bot Telegram, un reverse proxy per usarlo da fuori.

Nessuna libreria da installare per il pannello: usa solo quello che c'è già in Python. (Solo il plugin Alexa, per la «Verifica firma Amazon», vuole `cryptography`: su Debian `apt install python3-cryptography`.)


---

## Primo avvio

1. Crea un utente senza accesso interattivo e la sua cartella, e mettici **`pdc_pannello.py`** e `pdc_strumenti.py`:
   ```
   sudo useradd --system --user-group --home-dir /opt/pdc --shell /usr/sbin/nologin pdc
   sudo install -d -m 700 -o pdc -g pdc /opt/pdc
   sudo install -m 600 -o pdc -g pdc pdc_pannello.py pdc_strumenti.py /opt/pdc/
   ```
2. Lancia il pannello una volta:
   ```
   sudo -u pdc python3 /opt/pdc/pdc_pannello.py
   ```
   La prima volta crea il file **`config.json`** (con tutte le impostazioni e dei valori predefiniti) e si ferma.
3. Apri `config.json` con un editor di testo (`sudo -u pdc nano /opt/pdc/config.json`): è l'unico posto dove metti le tue chiavi. Quasi tutto si può poi cambiare anche dalla pagina **Impostazioni** del pannello.

Nei comandi di queste pagine `python pdc_pannello.py ...` sta per `sudo -u pdc python3 /opt/pdc/pdc_pannello.py ...` (vale anche per `pdc_strumenti.py`). **Usa sempre l'utente del pannello**: un file creato da un altro utente, poi il pannello non riesce a leggerlo.

> **`config.json` contiene token e password. Non mandarlo a nessuno.**
> Se lo modifichi a mano: ogni valore va tra `"virgolette"`, le righe di un blocco finiscono con la virgola tranne l'ultima, i numeri senza virgolette. Se sbagli, lo script ti dice che il JSON non è valido: controlla le virgole.

Ogni volta che aggiorni lo script, **le impostazioni nuove vengono aggiunte da sole** a `config.json` senza toccare quelle che hai già messo.


---

## Provalo subito con i dati finti

Prima di collegare qualsiasi cosa puoi vedere com'è fatto:

```
python pdc_pannello.py --demo --porta 7010
```

Apri `http://localhost:7010`: trovi 30 giorni di dati inventati, senza password. Premi `Ctrl+C` per chiudere. Non tocca niente di vero (usa un suo file, `dati/demo.db`).


---

## Collegare SmartThings (la pompa di calore)

Ci sono due modi per dare accesso al pannello. Puoi preparare tutti e due: **se l'OAuth è completo viene usato quello, altrimenti il token personale.** Nella pagina Impostazioni → *Stato* vedi quale dei due sta usando.

### 4a. Trovare l'ID della pompa

1. Vai su <https://account.smartthings.com/tokens> → *Generate new token*. Dagli un nome e spunta i permessi **Devices → List all devices** e **See all devices** (se vuoi anche i comandi dal pannello, spunta pure **Control all devices**). Copia il token (si vede una volta sola).
2. Aprilo in `config.json`, sezione `smartthings`, e incollalo in `"pat"`.
3. Lancia:
   ```
   python pdc_strumenti.py st-devices
   ```
   Trova la pompa e scrive da solo il suo `device_id` in `config.json`.

### 4b. Metodo semplice: il token personale (PAT)

È quello che hai appena creato. **Ha un difetto: dura 24 ore.** Il pannello conta le ore e ti avvisa su Telegram quando sta per scadere; dopo basta generarne uno nuovo e incollarlo (anche dalla pagina Impostazioni → *Collegamenti*).

### 4c. Metodo comodo: OAuth (si rinnova da solo)

Più lungo da preparare, ma poi non ci pensi più.

> **Due avvertenze oneste.** Samsung ha detto che l'accesso OAuth alle API non sarà più gratuito; non so cosa succeda in concreto: potrebbe non funzionare o chiederti di pagare. Se la creazione dell'app fallisce, resta il PAT. E il *refresh token* è **monouso**: a ogni rinnovo ne arriva uno nuovo e il vecchio muore. Il pannello lo riscrive subito in `config.json`: per questo **non far girare due copie del pannello con lo stesso `config.json`** e non tenere il file aperto nel Blocco note mentre il pannello lavora.

1. Installa la SmartThings CLI (sul tuo computer: dalla pagina *Releases* di <https://github.com/SmartThingsCommunity/smartthings-cli>, oppure, se hai Node.js, `npm install -g @smartthings/cli`).
2. Lancia `smartthings apps:create` e rispondi: tipo **OAuth-In App**, nome a piacere, permessi (*scopes*) **`r:devices:*`** (lettura) e, se vuoi accendere/spegnere dal pannello, anche **`x:devices:*`** (comandi), Redirect URI **`https://httpbin.org/get`** (identico), il resto vuoto. Alla fine ti dà **Client ID** e **Client Secret**: il secret si vede **una volta sola**, copialo subito.
3. Lancia:
   ```
   python pdc_strumenti.py st-oauth
   ```
   Incolla Client ID e Secret (il secret non si vede mentre lo incolli: è normale), apri l'indirizzo che ti stampa (meglio con **Chrome**), accedi con l'account Samsung e consenti. Arrivi su una pagina di testo con una riga `"code": "…"`: incolla quel codice (o tutta la pagina) nel terminale **subito**, dura poco.
4. Se va bene scrive da solo i token in `config.json`. Fatto.


---

## Collegare Netatmo (lo Smarther)

Serve per temperatura, umidità e richiesta di calore di casa.

1. Vai su <https://dev.netatmo.com> → *My apps* → la tua app → *Token generator*.
2. Spunta gli **scope** dello Smarther/BTicino: **`read_smarther`** per leggere e, se vuoi accendere/spegnere dal pannello, anche **`write_smarther`**. Genera il token. *Rigenerare i token invalida quelli vecchi.*
3. In `config.json`, sezione `netatmo`, compila `client_id`, `client_secret`, `access_token`, `refresh_token`. Lascia vuoti `home_id` e `room_id`: il pannello trova la casa da solo e, se `room_id` è vuoto usa la prima stanza che trova. (Con più stanze scrivi in `room_id` l'ID di quella dello Smarther: lo trovi con `python pdc_strumenti.py nt-dump`.)
4. Anche qui il *refresh token* ruota a ogni rinnovo e il pannello lo riscrive da solo: valgono le stesse avvertenze di sopra (una sola copia, file non aperto).

Se non usi lo Smarther lascia tutto vuoto: il pannello funziona lo stesso e ti segnala solo che Netatmo non è configurato.


---

## Scegliere utente e password

**Il pannello chiede sempre il login, anche da casa.** Senza password non mostra nulla.

```
python pdc_pannello.py imposta-password
```

Scegli un utente e una password di **almeno 10 caratteri** (non si vede mentre la scrivi). Viene salvata solo come impronta (hash), mai in chiaro.

Alternativa: scrivi `utente` e `password` nella sezione `accesso` di `config.json`; al prossimo avvio la password viene trasformata in hash e cancellata dal file. Dopo, puoi cambiarla dalla pagina **Impostazioni**.

Dopo 5 tentativi sbagliati lo stesso indirizzo viene bloccato per 15 minuti.


---

## Avviarlo davvero

### Per provare

```
sudo -u pdc python3 /opt/pdc/pdc_pannello.py --host 127.0.0.1
```

Per fermarlo: `Ctrl+C`. Per controllare i collegamenti senza avviare il pannello:

```
python pdc_pannello.py --una-volta
python pdc_pannello.py stato
```

### Come servizio (systemd)

Crea `/etc/systemd/system/pdc.service`:

```ini
[Unit]
Description=Pannello pompa di calore
After=network.target

[Service]
User=pdc
WorkingDirectory=/opt/pdc
ExecStart=/usr/bin/python3 /opt/pdc/pdc_pannello.py --host 127.0.0.1 --porta 7000
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
ProtectSystem=strict
ReadWritePaths=/opt/pdc
ProtectHome=yes
PrivateTmp=yes
NoNewPrivileges=yes
UMask=0077

[Install]
WantedBy=multi-user.target
```

Poi `sudo systemctl daemon-reload && sudo systemctl enable --now pdc`. I messaggi si leggono con `journalctl -u pdc` e nel file **`/opt/pdc/log/pdc_pannello.log`**: è il primo posto dove guardare se qualcosa non va. `systemctl stop` lo chiude in modo pulito.

Con `--host 127.0.0.1` il pannello si raggiunge solo da quel computer: da un altro si usa un **tunnel SSH** (`ssh -L 7000:127.0.0.1:7000 utente@server`, poi `http://localhost:7000`) oppure un reverse proxy con HTTPS (vedi [telegram-e-accesso.md](telegram-e-accesso.md)).

Una sola copia alla volta, **anche su macchine diverse**: due copie con le stesse chiavi si invalidano i token. Sulla stessa macchina la seconda si ferma con un messaggio (porta di controllo `porta_lock`). I dati del tempo in cui il pannello è fermo non si recuperano: SmartThings non li fornisce.
