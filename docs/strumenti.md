# Strumenti di appoggio (pdc_strumenti.py)

Questo piccolo script (`pdc_strumenti.py`) è l'attrezzo di appoggio del pannello. Serve a: **trovare l'ID della pompa** (`st-devices`), fare la **procedura guidata per il token SmartThings OAuth** che si rinnova da solo (`st-oauth`), **salvare i dati veri della pompa e dello Smarther** per diagnosticare problemi o capire cosa espongono i dispositivi (`st-dump`, `nt-dump`), vedere l'**elenco degli sbrinamenti** (`sbrinamenti`, capitolo 5), **provare i webhook di Netatmo** (`nt-webhook-prova`, capitolo 6), capire perché la **ripartenza prevista** c'è o non c'è (`rip-diag`, capitolo 7), **provare a leggere una stazione meteo vicina** (`meteo-prova`, capitolo 8), **raccogliere tutti i dati in un solo file per l'analisi** (`analisi`, capitolo 9), **vedere i comandi che la pompa espone** e **osservare cosa cambia mentre premi Boost o un programma** (`st-capacita` e `st-osserva`, capitolo 10) e fare qualche **esperimento** (`st-prova-refresh`, in fondo). Il pannello vero è `pdc_pannello.py`: guida completa in [README.md](../README.md) e nella cartella `docs/`.

Non installa nulla e non ha dipendenze: serve solo Python 3.10 o più recente.

---

## 0. Preparazione (5 minuti)

1. Crea una cartella, per esempio `pdc`, e mettici dentro `pdc_strumenti.py`.
2. Apri un terminale **dentro quella cartella**:
   - **Windows**: apri la cartella in Esplora risorse, tieni premuto *Maiusc* e fai clic destro su uno spazio vuoto → *Apri finestra PowerShell qui*.
   - **Mac**: tasto destro sulla cartella → *Nuovo Terminale nella cartella*.
3. Controlla che Python ci sia: scrivi `python --version` (su Mac `python3 --version`). Deve comparire 3.10 o più.
   Nei comandi qui sotto uso `python`; su Mac scrivi `python3`.
4. Lancia `python pdc_strumenti.py st-devices`. La prima volta crea un file **`config.json`** vuoto e si ferma. Apri `config.json` con il Blocco note: è l'unico file dove metterai le tue chiavi.

> `config.json` contiene password e token. **Non mandarlo mai a nessuno**, nemmeno a me.

---

## 1. Dump SmartThings (quando serve capire cosa espone la pompa)

Serve a sapere **esattamente come si chiamano** i valori della tua pdc (mandata, ritorno, portata, frequenza del compressore, curva climatica...). Lo script del repo GitHub ne mostra solo una parte.

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
4. **Allegali a chi ti aiuta.** Sono già ripuliti dai dati personali (id del proprietario e del luogo).

Il token personale (PAT) dura 24 ore, ma per il dump basta. Se usi già OAuth (capitolo 2) non serve il PAT: lo script usa i token di `config.json` e li rinnova da solo (con il pannello **fermo**, perché il refresh token è monouso).

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
Lancia `python pdc_strumenti.py st-oauth`, incolla Client ID e Secret quando li chiede (il secret non si vede mentre lo incolli: è normale), apri l'indirizzo che ti stampa (**usa Chrome**: con Edge a volte dà errore), accedi con l'account Samsung e consenti. Finisci su una pagina semplice di testo con una riga `"code": "...."`: incolla quel codice (o tutta la pagina) nel terminale **subito**, perché dura poco.

Se tutto va bene scrive i token in `config.json` e hai finito.

---

## 3. Dump Netatmo / Smarther (per diagnosticare temperatura, umidità e richiesta di calore)

1. Vai su <https://dev.netatmo.com> → *My apps* → la tua app → *Token generator*.
2. **Seleziona gli scope** (permessi) dello Smarther: **`read_smarther`** per leggere e, se vuoi accendere/spegnere dal pannello, anche **`write_smarther`**. Poi *Generate token*.
   > Rigenerare i token **invalida quelli vecchi**.
3. In `config.json`, sezione `netatmo`, compila: `client_id`, `client_secret`, `access_token`, `refresh_token`. Salva.
4. Lancia:
   ```
   python pdc_strumenti.py nt-dump
   ```
   Stampa gli scope che il token ha davvero, i valori di temperatura/setpoint/caldaia che trova e se trova l'**umidità**. Crea:
   - `diagnostica/dump_netatmo_homesdata.json`
   - `diagnostica/dump_netatmo_homestatus_1.json` (e `_2`, ... se hai più case)
5. **Allegali in chat.** Coordinate, città, email e indirizzi MAC sono già tolti, ma dai comunque un'occhiata con il Blocco note prima di mandarli.

---

## 4. Cosa mandare a chi ti aiuta

Tutti i file stanno nella cartella `diagnostica` accanto allo script.

| File | Quando |
|---|---|
| `dump_smartthings_status.json` e `dump_smartthings_device.json` | per capire cosa espone la pompa |
| `dump_netatmo_homesdata.json` e `dump_netatmo_homestatus_1.json` | per lo Smarther |
| `diagnostica.json` (lo crea il pannello con `python pdc_pannello.py esporta-diagnostica`) | per problemi sui dati o sull'indicatore |
| `analisi_AAAAMMGG_HHMM.json` | per far analizzare le impostazioni e i cicli (`analisi`, capitolo 9) |
| `dump_smartthings_capacita.json` · `dump_smartthings_osserva.json` | dopo `st-capacita` / `st-osserva` (capitolo 10) |
| `rip_diag.json` · `sbrinamenti.json` | con `--salva`, per la ripartenza prevista / gli sbrinamenti |
| `webhook_eventi.json` · `meteo_prova.json` | dopo `nt-webhook-prova` / `meteo-prova` |
| `prova_refresh.json` | dopo `st-prova-refresh` |
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

## 5. Sbrinamenti (dalla 0.19)

```
python pdc_strumenti.py sbrinamenti                 ultimi 7 giorni
python pdc_strumenti.py sbrinamenti --giorni 30     altro periodo
python pdc_strumenti.py sbrinamenti --salva         scrive anche diagnostica/sbrinamenti.json
```

Legge `dati/pdc.db` (serve `pdc_pannello.py` nella stessa cartella) e stampa data, durata, kWh ed esterna di ogni sbrinamento, con la media. Trova solo quelli registrati dal pannello da quando e' stato avviato: SmartThings non permette di tornare indietro nel tempo.

## 6. Prova dei webhook Netatmo (novembre 2026)

Netatmo ridurra' i limiti del polling dal 1 marzo 2027 e propone i webhook (il servizio ti avvisa quando cambia qualcosa). Prima di cambiare il pannello, questo comando guarda cosa arriva davvero dallo Smarther.

**Il pannello puo' restare acceso**: il comando non lo tocca. Apre un ricevitore sul tuo PC (su una porta libera scelta da solo), lo rende raggiungibile con un tunnel temporaneo di Cloudflare (indirizzo casuale `*.trycloudflare.com`, senza account, il tuo indirizzo Cloudflare non c'entra), registra quell'indirizzo come webhook su Netatmo e, alla fine, **lo rimuove**.

```
python pdc_strumenti.py nt-webhook-prova
```

1. Lancia il comando (di default resta in ascolto 30 minuti; `--minuti 15` basta per la prova).
2. Quando compare "Ascolto per ...", muovi lo Smarther: cambia la temperatura impostata dall'app BTicino, accendi e spegni, aspetta una partenza o uno stop del riscaldamento. Vedrai comparire gli eventi riga per riga.
3. A fine tempo c'e' un riepilogo e il file `diagnostica/webhook_eventi.json` (user_id, home_id e indirizzi sono gia' omessi): controllalo con il Blocco note e mandalo a Claude.

**Dal 4 novembre** il comando guarda anche *dalla parte di Netatmo*: ogni 20 secondi (`--sonda 30` per cambiare, `--sonda 0` per spegnere) legge `homestatus` e scrive con l'ora ogni cosa che cambia («Netatmo vede: rooms[0].therm_setpoint_temperature: 21 -> 22»), e ogni 2 minuti controlla con `GET /webhooks/v1/` che il webhook sia ancora registrato. Nel riepilogo finale confronta i due lati: quanti tuoi gesti ha visto Netatmo e quanti eventi sono arrivati (il file `webhook_eventi.json` ha le sezioni `cambiamenti_netatmo` e `controlli_registro`). Cosi' si capisce se un evento manca perche' Netatmo non lo manda o perche' non arriva a noi.

Cosa guarda: quali tipi di evento arrivano, con che ritardo, quali campi portano (temperatura, setpoint, umidita', richiesta di calore) e, dopo ogni evento, li confronta con quello che dice `homestatus` (al massimo 12 confronti, per non consumare richieste).

Note:
- Se Ctrl+C o un errore lo interrompe, il comando prova comunque a rimuovere il webhook. Se restasse registrato: `python pdc_strumenti.py nt-webhook-prova --elimina`.
- Usa il token Netatmo del pannello **senza rinnovarlo** (Netatmo cambia il refresh token a ogni rinnovo: farlo a pannello acceso lo romperebbe). Se il token sta per scadere ti dice di riprovare tra qualche minuto; con il pannello FERMO puoi usare `--rinnova`.
- **`--metodo nuovo`**: registra con il servizio nuovo di Netatmo (`POST /webhooks/v1/`) invece di `/api/addwebhook`, e alla fine toglie con `DELETE /webhooks/v1/`. Parte solo se l'elenco dei webhook dell'account e' vuoto (il DELETE li toglie *tutti*). Se resta registrato: `python pdc_strumenti.py nt-webhook-prova --metodo nuovo --elimina`.
- **`--sostituisci`**: con il metodo di partenza («vecchio»), se sull'account c'e' gia' un webhook registrato il comando si ferma per non toccarlo; con `--sostituisci` procede, lo sostituisce e poi lo toglie.
- Servono `cloudflared` nella cartella `cloudflared` accanto allo script (c'e' gia' se il pannello usa il tunnel) e il `client_secret` Netatmo in `config.json`.
- Se il tunnel temporaneo e' bloccato o Netatmo lo rifiuta, il comando lo dice: manda l'output a Claude.

## 7. Ripartenza prevista (rip-diag, dalla 0.19)

Se nella card dello Smarther non compare «Ripartenza prevista», questo comando dice perché.

```
python pdc_strumenti.py rip-diag                 ultime 15 pause del riscaldamento
python pdc_strumenti.py rip-diag --ultime 30     piu' pause
python pdc_strumenti.py rip-diag --salva         scrive anche diagnostica/rip_diag.json
```

Legge `dati/pdc.db` (come `sbrinamenti`, anche a pannello acceso; serve `pdc_pannello.py` nella stessa cartella) e usa le stesse funzioni del pannello. Stampa i dati dello Smarther che ci sono nel database, poi le pause del riscaldamento trovate nello storico, una per riga: «OK» con la velocita' di discesa della temperatura (`k`) oppure «scartata» con il motivo (pausa troppo corta, la temperatura non scende, la pausa non e' finita, buchi nei dati). Servono almeno 5 pause valide per una stima. Con `--salva` scrive il rapporto in `diagnostica/rip_diag.json`: mandalo a chi ti aiuta.

L'«orario di adesso» che stampa in fondo è la stima **grezza**; dalla 0.21.3 l'orario mostrato nel pannello è la stessa stima resa stabile (cambia solo se si sposta di oltre una soglia), perché il sensore dello Smarther va a passi di 0,1 °C e con una casa inerte un passo vale anche mezz'ora.

## 8. Stazione meteo vicina (meteo-prova, novembre 2026)

Prova a leggere umidità (e temperatura) da una pagina **WeatherLink** pubblica di una stazione meteo vicina, senza chiavi. Serve per capire se i dati della pagina sono leggibili da un programma, prima di aggiungere l'«umidità esterna» al pannello.

```
python pdc_strumenti.py meteo-prova "https://www.weatherlink.com/embeddablePage/show/<codice>/summary"
```

Non usa `config.json` e non tocca il pannello. Fa tre cose: legge la pagina (titolo e script), cerca al suo interno gli indirizzi da cui prende i dati, poi prova gli indirizzi trovati (compreso quello classico `.../embeddablePage/getData/<codice>`). Se uno risponde con un JSON, stampa i campi che sembrano temperatura, umidità e orario. Se trova i dati, stampa in fondo l'**indirizzo dei dati** (quello grezzo, non quello della pagina) con l'ora dell'ultimo dato ricevuto dalla stazione: è quello da incollare nelle impostazioni del pannello; se la stazione cambia, rilancia il comando e incolla il nuovo. Alla fine scrive `diagnostica/meteo_prova.json`: mandalo a chi ti aiuta (non contiene password né token, solo dati pubblici della stazione).

## 9. Analisi completa (analisi, dalla 0.21.3)

Un solo file con tutto quello che il database sa, da mandare a Claude per farlo analizzare. Legge il database in sola lettura (il pannello puo' restare acceso) e non contiene token, password, indirizzi o identificativi.

```
python pdc_strumenti.py analisi                      ultimi 45 giorni, 1 giorno di misure grezze
python pdc_strumenti.py analisi --giorni 14          meno storico (file piu' piccolo)
python pdc_strumenti.py analisi --grezzi 3           3 giorni di misure non mediate
python pdc_strumenti.py analisi --ciclo 0            senza i campioni grezzi del ciclo (file piu' piccolo)
python pdc_strumenti.py analisi --uscita C:\altro\dati.json   scrive il file dove dici tu invece che in diagnostica (lo usa il plugin «analisi»)
python pdc_strumenti.py analisi --ricircolo-off "13:30-17:00,21:30-08:00" --ricircolo-dal "2026-11-05 14:30"   fasce a ricircolo spento e da quando valgono (all'avvio le stampa)
```

Crea `diagnostica/analisi_AAAAMMGG_HHMM.json`. Dentro: impostazioni (FSV, dispositivi), qualita' dei dati e buchi, riepilogo per giorno (con l'acqua nell'aria in g/m³ di casa e, se c'è la stazione meteo, di fuori), giornata tipica per ora, ogni ciclo dello Smarther (durata, quanto la casa sale oltre il target, isteresi vera), ogni avvio del compressore, profilo della PdC per temperatura esterna, curva climatica contro mandata misurata, efficienza, bollitore, sbrinamenti, ripartenza prevista, cambi di stato, allarmi, e tutte le serie mediate nel tempo (5 minuti gli ultimi 14 giorni, 30 minuti prima), più i byte grezzi del ciclo del compressore degli ultimi 7 giorni (`--ciclo`), che servono a cercare valori non ancora decodificati.

Gli stessi dati, senza esportare il file a mano, li dà anche il plugin `analisi` (solo dalla rete di casa, con token: vedi plugin.md).

Come si usa: lancialo da PowerShell nella cartella del pannello (anche con il pannello acceso), poi allega in chat il file nuovo di `diagnostica`. Se il file è troppo grande per la chat: `--giorni 14`. Il valore dell'isteresi impostato nello Smarther **non** c'è nel database: scrivilo tu insieme al file. Serve `pdc_pannello.py` nella stessa cartella (per i calcoli di efficienza, sbrinamenti e ripartenza; senza, le altre sezioni ci sono lo stesso).


## 10. Comandi della pompa e osservazione (st-capacita, st-osserva)

Due strumenti di **sola lettura** per capire come si comanda la pompa, usati per costruire Boost, programmi e FSV.

```
python pdc_strumenti.py st-capacita                  salva i comandi che SmartThings espone per la pompa
python pdc_strumenti.py st-osserva                   guarda la pompa per 15 minuti e annota cosa cambia
python pdc_strumenti.py st-osserva --minuti 30 --ogni 5
```

- **`st-capacita`**: chiede a SmartThings la definizione di ogni funzione della pompa (nomi dei comandi e dei loro argomenti) e scrive `diagnostica/dump_smartthings_capacita.json`. Non cambia niente sulla pompa. Se non sa quale sia, usa `--device <id>` (o lancia prima `st-devices`).
- **`st-osserva`**: legge lo stato della pompa ogni 10 secondi (`--ogni`) per 15 minuti (`--minuti`) e annota, con l'ora, ogni valore che cambia. Mentre gira premi Boost o crei un programma dall'app e **segnati l'ora di ogni cosa che fai**: il risultato è `diagnostica/dump_smartthings_osserva.json`. Se usi OAuth, come per `st-dump` lancialo a pannello **fermo**: il refresh token è monouso e in due insieme si pestano i piedi (capitolo 1).

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

**Risultato del 3/11/2026** (24 giri complessivi, ordine casuale): con il refresh dati nuovi in 7 giri su 12, quasi sempre dopo 10 secondi; senza refresh in 1 su 12. Dati freschi (meno di 3 minuti) solo con il refresh, ma in circa metà dei tentativi non arriva niente. Il pannello 0.18 ha l'opzione per usarlo (Impostazioni → Collegamenti).

---

## Crediti

Idea, requisiti e prove sul campo: Alessandro; scritto insieme a Claude (Anthropic). Ispirazione e decodifica dei dati del compressore: [Dwellsee](https://www.dwellsee.com) («Cycle Data Decoding Guide»). Script di partenza: [pookey/samsung-heatpump-smartthings](https://github.com/pookey/samsung-heatpump-smartthings). Meteo: [Open-Meteo](https://open-meteo.com) (CC BY 4.0). I nomi e i marchi (Samsung, SmartThings, Netatmo, BTicino, Cloudflare, Amazon) appartengono ai rispettivi proprietari; il progetto non è affiliato a nessuno di loro.
