# Strumenti di appoggio – primo passo

Questo piccolo script (`pdc_strumenti.py`) serve a **raccogliere i dati veri della tua pompa di calore e dello Smarther**, così il pannello definitivo parte giusto al primo colpo. Fa anche la procedura guidata per avere un token SmartThings che si rinnova da solo.

Non installa nulla e non ha dipendenze: serve solo Python 3.9 o più recente.

---

## 0. Preparazione (5 minuti)

1. Crea una cartella, per esempio `pdc`, e mettici dentro `pdc_strumenti.py`.
2. Apri un terminale **dentro quella cartella**:
   - **Windows**: apri la cartella in Esplora risorse, tieni premuto *Maiusc* e fai clic destro su uno spazio vuoto → *Apri finestra PowerShell qui*.
   - **Mac**: tasto destro sulla cartella → *Nuovo Terminale nella cartella*.
3. Controlla che Python ci sia: scrivi `python --version` (su Mac `python3 --version`). Deve comparire 3.9 o più.
   Nei comandi qui sotto uso `python`; su Mac scrivi `python3`.
4. Lancia `python pdc_strumenti.py st-devices`. La prima volta crea un file **`config.json`** vuoto e si ferma. Apri `config.json` con il Blocco note: è l'unico file dove metterai le tue chiavi.

> `config.json` contiene password e token. **Non mandarlo mai a nessuno**, nemmeno a me.

---

## 1. Dump SmartThings (obbligatorio)

Mi serve per sapere **esattamente come si chiamano** i valori della tua pdc (mandata, ritorno, portata, frequenza del compressore, curva climatica...). Lo script del repo GitHub ne mostra solo una parte.

1. Vai su <https://account.smartthings.com/tokens> → *Generate new token* → dagli un nome → spunta i permessi **Devices: List devices, See devices** → copia il token (si vede una sola volta).
2. In `config.json`, nella sezione `smartthings`, incolla il token tra le virgolette di `"pat"`. Salva.
3. Lancia:
   ```
   python pdc_strumenti.py st-devices
   python pdc_strumenti.py st-dump
   ```
   Il primo trova la pdc e salva il suo `device_id` nel config. Il secondo crea due file nella cartella **`diagnostica`** (accanto allo script):
   - `dump_smartthings_status.json`
   - `dump_smartthings_device.json`
4. **Allegali in chat.** Sono già ripuliti dai dati personali (id del proprietario e del luogo).

Il token personale (PAT) dura 24 ore, ma per il dump basta.

---

## 2. Token OAuth che si rinnova da solo (consigliato, ma si può fare dopo)

Con il PAT dovresti incollarne uno nuovo ogni giorno. Con OAuth il pannello si rinnova il token da solo.

**Due avvertenze oneste:**
- Samsung ha dichiarato che l'accesso OAuth alle API non sarà più gratuito. Non ho trovato conferma di cosa sia successo dopo ottobre 2026, quindi **potrebbe non funzionare o chiederti di pagare**. Se la creazione dell'app fallisce, non è colpa tua: resta il PAT (il pannello avrà una pagina dove incollarne uno nuovo e ti avviserà su Telegram prima della scadenza).
- Il *refresh token* è **monouso**: a ogni rinnovo ne arriva uno nuovo e il vecchio muore. Lo script lo riscrive da solo in `config.json`. Per questo non far girare due programmi con lo stesso `config.json` contemporaneamente.

### 2a. Installa la SmartThings CLI
- **Mac**: `brew install smartthingscommunity/smartthings/smartthings`
- **Windows / altri**: scarica l'installer dalla pagina *Releases* di <https://github.com/SmartThingsCommunity/smartthings-cli>, oppure, se hai Node.js, `npm install -g @smartthings/cli`.

### 2b. Crea l'app OAuth
Nel terminale lancia `smartthings apps:create`. Al primo uso si apre il browser per accedere con l'account Samsung. Poi risponde a una serie di domande (le parole esatte possono cambiare):
- tipo di app: **OAuth-In App**
- nome e descrizione: quello che vuoi (es. "Pannello PDC")
- permessi (*scopes*): seleziona **`r:devices:*`** (lettura) e, se vuoi accendere/spegnere dal pannello, anche **`x:devices:*`** (comando)
- Redirect URI: **`https://httpbin.org/get`** (scrivilo identico)
- il resto (icona, target URL...): lascia vuoto

Alla fine ti mostra **Client ID** e **Client Secret**. Il secret **si vede una volta sola**: copialo subito.

### 2c. Autorizza
Lancia `python pdc_strumenti.py st-oauth`, incolla Client ID e Secret quando li chiede, apri l'indirizzo che ti stampa (**usa Chrome**: con Edge a volte dà errore), accedi con l'account Samsung e consenti. Finisci su una pagina semplice di testo con una riga `"code": "...."`: incolla quel codice (o tutta la pagina) nel terminale **subito**, perché dura poco.

Se tutto va bene scrive i token in `config.json` e hai finito.

---

## 3. Dump Netatmo / Smarther (obbligatorio per vedere temperatura e umidità di casa)

1. Vai su <https://dev.netatmo.com> → *My apps* → la tua app → *Token generator*.
2. **Seleziona gli scope** (permessi) che hanno a che fare con lo **Smarther / BTicino / thermostat**. Non so con certezza il nome esatto (forse qualcosa come `read_smarther`): se non li trovi, spunta tutti quelli di lettura. Poi *Generate token*.
   > Rigenerare i token **invalida quelli vecchi**.
3. In `config.json`, sezione `netatmo`, compila: `client_id`, `client_secret`, `access_token`, `refresh_token`. Salva.
4. Lancia:
   ```
   python pdc_strumenti.py nt-dump
   ```
   Stampa gli scope che il token ha davvero, i valori di temperatura/setpoint/caldaia che trova e se trova l'**umidità**. Crea:
   - `dump_netatmo_homesdata.json`
   - `dump_netatmo_homestatus_1.json` (e `_2`, ... se hai più case)
5. **Allegali in chat.** Coordinate, città, email e indirizzi MAC sono già tolti, ma dai comunque un'occhiata con il Blocco note prima di mandarli.

---

## 4. Cosa mandarmi

| File | Obbligatorio |
|---|---|
| `dump_smartthings_status.json` | sì |
| `dump_smartthings_device.json` | sì |
| `dump_netatmo_homesdata.json` | sì (se vuoi lo Smarther) |
| `dump_netatmo_homestatus_1.json` | sì (se vuoi lo Smarther) |
| `config.json` | **MAI** |

---

## Se qualcosa non va

| Messaggio | Cosa significa |
|---|---|
| `python non e' riconosciuto` | Python non è installato o non è nel PATH. Installalo da python.org spuntando *Add to PATH*. |
| `config.json non e' un JSON valido` | Hai tolto o aggiunto una virgola/virgoletta. Ogni valore va tra `"virgolette"`, e tutte le righe tranne l'ultima di un blocco finiscono con la virgola. |
| `SmartThings: 401` | Token scaduto o sbagliato (il PAT dura 24 ore). Generane uno nuovo. |
| `SmartThings: 403` | Al token mancano i permessi. Rigeneralo spuntando *List* e *See devices*. |
| `SmartThings: 429` | Troppe richieste. Aspetta un minuto. |
| `Netatmo ... scope mancante` | Rigenera il token dal portale con gli scope dello Smarther. |
| `Nessuna casa trovata` (Netatmo) | Il token non vede lo Smarther: stesso rimedio, scope. |

---

## Esperimento: il comando "refresh" anticipa i dati del ciclo?

I dati del compressore arrivano da SmartThings con 4-5 minuti di ritardo, a pacchetti. Questo test prova se il comando `refresh` li fa arrivare prima.
Per non scambiare il caso per un effetto, fa 8 giri da 2 minuti in ordine casuale: 4 CON il refresh e 4 SENZA (controllo), poi confronta quante volte arrivano dati nuovi nei due casi (circa 22 minuti con le pause).

1. **Ferma il pannello** (usa lo stesso token OAuth: in due insieme si pestano i piedi).
2. Dalla cartella dello script:
   ```
   python pdc_strumenti.py st-prova-refresh
   ```
3. Aspetta la fine (compare il "Riepilogo" con il verdetto). Riavvia il pannello e allega `diagnostica/prova_refresh.json`.

Tra un giro e l'altro c'e' una pausa di 45 secondi (non conteggiata) cosi' i dati in ritardo non sporcano il giro dopo.

Opzioni: `--giri 16` (piu' giri = risultato piu' affidabile, sempre un numero pari), `--finestra 180` (secondi per giro), `--ogni 15` (secondi tra le letture).
Non modifica nessuna impostazione della pdc: manda solo `refresh`, come il pulsante di aggiornamento dell'app.
