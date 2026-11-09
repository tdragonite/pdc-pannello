# Installazione e primo avvio

Tutto quello che serve per far partire il pannello: Python, primo avvio, prova con i dati finti, collegamento a SmartThings e Netatmo, password e avvio vero.

## Cosa serve

- Un PC (qui si parla di **Windows**) che resta acceso: il pannello raccoglie i dati solo finché gira.
- **Python 3.10 o più recente**. Per controllare, apri PowerShell e scrivi `python --version`. Se non c'è, installalo da python.org **spuntando “Add python.exe to PATH”**.
- Un account SmartThings con la pompa di calore dentro (si usa l'API di SmartThings, non c'è nessun accesso diretto alla pompa).
- Facoltativi: l'app Netatmo (Smarther), un bot Telegram, un dominio su Cloudflare.

Nessuna libreria da installare: usa solo quello che c'è già in Python.


---

## Primo avvio

1. Crea una cartella, per esempio `C:\pdc`, e mettici dentro **`pdc_pannello.py`** (e `pdc_strumenti.py`, che serve per SmartThings OAuth e per gli ID, vedi sotto).
2. Apri PowerShell **in quella cartella**: da Esplora risorse tieni premuto *Maiusc*, clic destro su uno spazio vuoto → *Apri finestra PowerShell qui*.
3. Lancia:
   ```
   python pdc_pannello.py
   ```
   La prima volta crea il file **`config.json`** (con tutte le impostazioni e dei valori predefiniti) e si ferma.
4. Apri `config.json` con il Blocco note: è l'unico posto dove metti le tue chiavi. Quasi tutto si può poi cambiare anche dalla pagina **Impostazioni** del pannello.

> **`config.json` contiene token e password. Non mandarlo a nessuno.**
> Se lo modifichi a mano: ogni valore va tra `"virgolette"`, le righe di un blocco finiscono con la virgola tranne l'ultima, i numeri senza virgolette. Se sbagli, lo script ti dice che il JSON non è valido: controlla le virgole.

Ogni volta che aggiorni lo script, **le impostazioni nuove vengono aggiunte da sole** a `config.json` senza toccare quelle che hai già messo.


---

## Provalo subito con i dati finti

Prima di collegare qualsiasi cosa puoi vedere com'è fatto:

```
python pdc_pannello.py --demo --porta 7010
```

Apri `http://localhost:7010`: trovi 30 giorni di dati inventati, senza password. Premi `Ctrl+C` per chiudere. Non tocca niente di vero (usa un suo file, `dati\demo.db`).


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

1. Installa la SmartThings CLI (Windows: installer dalla pagina *Releases* di <https://github.com/SmartThingsCommunity/smartthings-cli>; oppure, se hai Node.js, `npm install -g @smartthings/cli`).
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

### Per provare (con la finestra visibile)

```
python pdc_pannello.py
```

Stampa gli indirizzi da aprire, per esempio:

```
http://localhost:7000
http://192.168.1.50:7000  (rete di casa, usa questo da iPhone)
```

Il primo è per il PC, il secondo per il telefono (stessa rete wifi). Alla **prima esecuzione Windows** può chiedere se consentire a Python di usare la rete: scegli *Reti private*, altrimenti il telefono non si collega. Per fermarlo: `Ctrl+C`.

Per vedere subito che i collegamenti funzionino senza avviare il pannello:

```
python pdc_pannello.py --una-volta
python pdc_pannello.py stato
```

### Senza finestra (con il tuo pannello di script)

Fai partire lo script con **`pythonw`** invece di `python`: è identico ma non apre nessuna finestra.

```
pythonw C:\pdc\pdc_pannello.py
```

Imposta come cartella di lavoro `C:\pdc` (non è obbligatorio: i file stanno comunque accanto allo script). Gli eventuali messaggi finiscono nel file **`log\pdc_pannello.log`** nella stessa cartella: è il primo posto dove guardare se qualcosa non va.

Una sola copia alla volta: se ne parte una seconda, si ferma con un messaggio (serve a non rovinare i token). La porta di controllo è `porta_lock` in `config.json`, di solito non devi toccarla.

Se il PC si riavvia, il pannello va fatto ripartire (da te o dal tuo gestore di script); lo storico che mancava nel frattempo purtroppo non si recupera: SmartThings non lo fornisce.
