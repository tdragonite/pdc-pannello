# Pannello pompa di calore (Samsung EHS) – guida completa

Un pannello web per la tua pompa di calore Samsung EHS e per lo Smarther 2, che gira sul tuo PC. **Legge e mostra** tutto (dati, storico, efficienza). Se vuoi, ti lascia anche **comandare**: accendere/spegnere riscaldamento, acqua calda e Smarther, e (solo dalla rete di casa) cambiare le regolazioni della pompa e il boost dell'acqua calda. Tutto dietro un lucchetto: vedi «Accendere e spegnere dal pannello» e «Regolazioni della pompa».
Ha le pagine **Dashboard**, **Analisi**, **Efficienza**, **Dispositivi** e **Impostazioni** (più il **Changelog**), funziona bene su iPhone, ti avvisa su Telegram se qualcosa non va e, se vuoi, lo raggiungi anche da fuori casa. Accetta anche **integrazioni a plugin**, come Alexa e il widget per iPhone (capitoli 18, 19 e 20).

Tutto sta in **un solo file Python** (`pdc_pannello.py`), senza installare nulla oltre a Python. Dati, impostazioni e log stanno nelle sottocartelle della cartella dello script (vedi «Come è organizzata la cartella» nel capitolo 14).

---

## Indice

1. [Cosa serve](#1-cosa-serve)
2. [Primo avvio](#2-primo-avvio)
3. [Provalo subito con i dati finti](#3-provalo-subito-con-i-dati-finti)
4. [Collegare SmartThings (la pompa di calore)](#4-collegare-smartthings-la-pompa-di-calore)
5. [Collegare Netatmo (lo Smarther)](#5-collegare-netatmo-lo-smarther)
6. [Scegliere utente e password](#6-scegliere-utente-e-password)
7. [Avviarlo davvero](#7-avviarlo-davvero)
8. [Telegram: gli avvisi](#8-telegram-gli-avvisi)
9. [Accesso da fuori casa (Cloudflare)](#9-accesso-da-fuori-casa-cloudflare)
10. [Usarlo su iPhone](#10-usarlo-su-iphone)
11. [Come leggere le pagine](#11-come-leggere-le-pagine)
12. [Tutti i comandi](#12-tutti-i-comandi)
13. [Il file config.json spiegato](#13-il-file-configjson-spiegato)
14. [Manutenzione, backup, aggiornamenti](#14-manutenzione-backup-aggiornamenti)
15. [Se qualcosa non va](#15-se-qualcosa-non-va)
16. [Sicurezza: cosa è bene sapere](#16-sicurezza-cosa-è-bene-sapere)
17. [Per chi modifica il codice](#17-per-chi-modifica-il-codice)
18. [Integrazioni (plugin)](#18-integrazioni-plugin)
19. [Alexa (il plugin per le Routine)](#19-alexa-il-plugin-per-le-routine)
20. [Widget per iPhone (plugin e script Scriptable)](#20-widget-per-iphone-plugin-e-script-scriptable)
21. [Crediti](#21-crediti)

---

## 1. Cosa serve

- Un PC (qui si parla di **Windows**) che resta acceso: il pannello raccoglie i dati solo finché gira.
- **Python 3.9 o più recente**. Per controllare, apri PowerShell e scrivi `python --version`. Se non c'è, installalo da python.org **spuntando “Add python.exe to PATH”**.
- Un account SmartThings con la pompa di calore dentro (si usa l'API di SmartThings, non c'è nessun accesso diretto alla pompa).
- Facoltativi: l'app Netatmo (Smarther), un bot Telegram, un dominio su Cloudflare.

Nessuna libreria da installare: usa solo quello che c'è già in Python.

---

## 2. Primo avvio

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

## 3. Provalo subito con i dati finti

Prima di collegare qualsiasi cosa puoi vedere com'è fatto:

```
python pdc_pannello.py --demo --porta 7010
```

Apri `http://localhost:7010`: trovi 30 giorni di dati inventati, senza password. Premi `Ctrl+C` per chiudere. Non tocca niente di vero (usa un suo file, `dati\demo.db`).

---

## 4. Collegare SmartThings (la pompa di calore)

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
   Incolla Client ID e Secret, apri l'indirizzo che ti stampa (meglio con **Chrome**), accedi con l'account Samsung e consenti. Arrivi su una pagina di testo con una riga `"code": "…"`: incolla quel codice (o tutta la pagina) nel terminale **subito**, dura poco.
4. Se va bene scrive da solo i token in `config.json`. Fatto.

---

## 5. Collegare Netatmo (lo Smarther)

Serve per temperatura, umidità e richiesta di calore di casa.

1. Vai su <https://dev.netatmo.com> → *My apps* → la tua app → *Token generator*.
2. Spunta gli **scope** dello Smarther/BTicino: **`read_smarther`** per leggere e, se vuoi accendere/spegnere dal pannello, anche **`write_smarther`**. Genera il token. *Rigenerare i token invalida quelli vecchi.*
3. In `config.json`, sezione `netatmo`, compila `client_id`, `client_secret`, `access_token`, `refresh_token`. Lascia vuoti `home_id` e `room_id`: il pannello trova la casa da solo e, se `room_id` è vuoto usa la prima stanza che trova. (Con più stanze scrivi in `room_id` l'ID di quella dello Smarther: lo trovi con `python pdc_strumenti.py nt-dump`.)
4. Anche qui il *refresh token* ruota a ogni rinnovo e il pannello lo riscrive da solo: valgono le stesse avvertenze di sopra (una sola copia, file non aperto).

Se non usi lo Smarther lascia tutto vuoto: il pannello funziona lo stesso e ti segnala solo che Netatmo non è configurato.

---

## 6. Scegliere utente e password

**Il pannello chiede sempre il login, anche da casa.** Senza password non mostra nulla.

```
python pdc_pannello.py imposta-password
```

Scegli un utente e una password di **almeno 10 caratteri** (non si vede mentre la scrivi). Viene salvata solo come impronta (hash), mai in chiaro.

Alternativa: scrivi `utente` e `password` nella sezione `accesso` di `config.json`; al prossimo avvio la password viene trasformata in hash e cancellata dal file. Dopo, puoi cambiarla dalla pagina **Impostazioni**.

Dopo 5 tentativi sbagliati lo stesso indirizzo viene bloccato per 15 minuti.

---

## 7. Avviarlo davvero

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

---

## 8. Telegram: gli avvisi

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

**Le soglie sono un punto di partenza, non verità**: dopo qualche settimana guardale con i tuoi dati e ritoccale.

Per spegnere un allarme: `"attivo": false`. Per spegnere intere famiglie, in `allarmi` aggiungi una voce con asterisco, per esempio `"st_conn_*": {"attivo": false}`.

### Comportamento

- Un problema ti arriva **una volta** e poi, quando passa, un messaggio “tutto di nuovo regolare” con la durata. Se resta aperto, un promemoria ogni 6 ore (`promemoria_ore`, 0 = mai).
- Un'anomalia che si risolve da sola entro il ritardo che imposti (`ritardo_minuti`, di solito 0) non ti scrive.
- Massimo 20 messaggi all'ora (`max_messaggi_ora`): oltre, ne salta alcuni e te lo dice.
- **Silenzio notturno** (spento di default): di notte i messaggi arrivano senza suoneria.
- **Riepilogo del mattino** (spento di default): ogni giorno all'ora scelta, un messaggio con energia, COP, ore di funzionamento e temperature di ieri. Se il silenzio notturno è acceso, aggiunge una sezione **«Di notte»** solo quando è successo qualcosa durante il silenzio: temperatura in casa variata di almeno 2 °C (cambia in Impostazioni), umidità sopra la soglia gialla, avvisi scattati e già rientrati.
- I **testi** si personalizzano in Impostazioni → *Testi dei messaggi* (segnaposto come `{testo}`, `{ora}`, `{durata}`); per cambiare il testo di un solo allarme usa `per_codice` in `config.json`.

---

## 9. Accesso da fuori casa (Cloudflare)

Serve un **dominio che usa i DNS di Cloudflare** (anche gratuito). Non devi aprire porte sul router: il PC si collega a Cloudflare e Cloudflare porta qui chi visita il tuo indirizzo.

Tutto da **Impostazioni → Accesso da internet**, con la procedura guidata:

1. **Crea un token API su Cloudflare** (la guida è nella pagina): <https://dash.cloudflare.com/profile/api-tokens> → *Create Token* → *Create Custom Token*, nome a piacere, e tre permessi:
   - **Account · Cloudflare Tunnel · Edit**
   - **Zone · DNS · Edit**
   - **Zone · Zone · Read**

   In *Zone Resources* scegli *Include → Specific zone* e il tuo dominio. Copia il token.
2. **Incollalo** e premi *Verifica*: il pannello trova i tuoi domini.
3. **Scegli** dominio e sottodominio (per esempio `pdc` → `pdc.tuodominio.it`) e premi *Crea tunnel e collega*. Il pannello crea tunnel e record DNS, scarica `cloudflared` e lo avvia.

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

---

## 10. Usarlo su iPhone

- Da casa: apri su Safari l'indirizzo `http://192.168.…:7000` che il pannello ha stampato (il telefono deve essere sul wifi di casa). Da fuori: l'indirizzo Cloudflare.
- Per averlo come un'app: in Safari tocca *Condividi* → *Aggiungi alla schermata Home*. Si apre a schermo intero, con la sua icona e una schermata di avvio scura (con l'anello e il punto), così all'apertura non si vede il lampo bianco. iOS salva quell'immagine nel momento in cui aggiungi l'icona: se un aggiornamento la cambia, togli l'icona dalla Home e rimettila.
- Su telefono i comandi sono in basso (Dashboard, Analisi, Efficienza, Dispositivi, Impostazioni). Nei grafici di Analisi **tocca** per leggere i valori: sono sincronizzati.
- Aperto da internet, il pannello usa la connessione protetta (HTTPS) di Cloudflare; da casa è normale HTTP sulla tua rete.

---

## 11. Come leggere le pagine

**Dashboard** (intervalli 6h / 24h / personalizzato)
Riscaldamento (mandata, ritorno, ΔT), acqua calda sanitaria, temperatura esterna, energia (e, se hai messo il prezzo, il costo), casa (temperatura, umidità, richiesta di calore dello Smarther), la **linea del tempo** di cosa ha fatto la pompa (casa / acqua calda / riposo), lo stato del sistema con gli interruttori (vedi sotto), la scheda **Compressore e circuito** (quando il compressore è fermo mostra «Fermo» e i dati dell'ultimo ciclo: per cosa ha lavorato, quanto è durato, quanta energia ha usato) e la **curva climatica** con i tuoi punti reali.

> **Il compressore arriva in ritardo.** SmartThings consegna i dati del compressore (Hz, pompa, portata) a blocchi di 5 minuti, con almeno 4-5 minuti di ritardo. Nella scheda *Compressore e circuito* il pannello scrive «dato di N min fa» (in giallo se più di 20) e, sotto, il **ritardo tipico e massimo** misurato nelle ultime 24 ore.
> Per non dire «A riposo» mentre la pompa sta partendo, né «Riscaldamento» quando si è già fermata, l'indicatore e la linea del tempo guardano anche cose **in tempo reale**: la **temperatura dell'acqua dell'impianto** e la **richiesta di calore dello Smarther**.
> - *Parte*: l'acqua sale di almeno 1,5 °C in 7 minuti ed è vicina al suo massimo recente → la pompa sta scaldando verso dove punta la valvola (casa o bollitore).
> - *Si ferma*: l'acqua crolla di almeno 5 °C su due letture di fila, oppure scende di almeno 2 °C mentre lo Smarther ha smesso di chiedere calore. I crolli subito dopo un cambio di valvola (da acqua calda a casa, per esempio) sono normali e si ignorano. Con la pompa in marcia l'acqua oscilla di 2-3 °C: un calo piccolo non basta.
> - *Campioni «fantasma»*: ogni tanto SmartThings consegna un campione con il compressore acceso mentre l'acqua misurata nello stesso momento dice che è ferma da minuti (la mandata del campione è a più di 5 °C dall'acqua). Questi campioni vengono scartati dall'indicatore e dalla linea del tempo; nei grafici di Analisi restano, perché mostrano i dati così come arrivano.
> Resta comunque un ritardo di pochi minuti nei momenti di cambio, ed è normale.

**Dati più freschi (opzione).** In *Impostazioni → Collegamenti* c'è «SmartThings: chiedi alla pompa di aggiornare i dati mentre lavora» (spenta di default). Se la accendi, **solo mentre la pompa lavora** (compressore in marcia, Smarther che chiede calore o acqua dell'impianto che si muove) il pannello manda ogni 60 secondi (si cambia in *Ogni quanti secondi mandare il refresh*, minimo 30) il comando «refresh» e rilegge subito dopo. Non cambia nessuna impostazione della pompa. Dalle prove (pdc a riposo e in marcia) i dati nuovi arrivano dopo circa 10 secondi in più della metà dei tentativi: a volte freschi (meno di 2 minuti), a volte arretrati, e a volte non arriva niente. Quindi **migliora, ma non elimina il ritardo**. Serve il permesso di comando del token SmartThings (`x:devices:*`, come per gli interruttori): se manca, il pannello lo scrive nella scheda *Compressore e circuito* e riprova dopo un'ora.
Si aggiorna da sola mentre è aperta: il pallino in alto è verde se i dati sono freschi, giallo se il tempo reale non è collegato o i dati sono vecchi, rosso se c'è un problema.

**Ripartenza prevista**
Nella card dello Smarther, quando il riscaldamento è fermo, compare in un riquadro viola «Riparte alle 22:45» (o «Riparte domani…»): è l'ora in cui la casa dovrebbe scendere alla soglia di ripartenza dello Smarther. La stima nasce dai tuoi dati: il pannello cerca nello storico le pause del riscaldamento (almeno 15 minuti acceso e poi un'ora di pausa) e misura quanto in fretta scende la temperatura in casa; ne servono almeno 5 valide, quindi nei primi giorni non compare. Non compare nemmeno se sta già scaldando, se la ripartenza è oltre 12 ore o se il dato dello Smarther è vecchio. L'orario è arrotondato al quarto d'ora ed è la stima «in situazione ideale» (finestre aperte o sole la sfalsano). Il sensore dello Smarther misura a passi di 0,1 °C e con una casa inerte un solo passo vale anche mezz'ora: per questo l'orario mostrato **resta fermo** finché la nuova stima non si sposta di oltre una soglia (fino a 30 minuti da lontano, sempre meno avvicinandosi). Lo stesso orario lo leggono il widget e Alexa. Se non compare e vuoi sapere perché: `python pdc_strumenti.py rip-diag` (mostra anche la stima grezza).

**Analisi** (6h / 24h / personalizzato)
I grafici nel dettaglio: temperature, casa (con una striscia rossa quando lo Smarther chiede calore), umidità, potenza, gas di scarico e un unico grafico **Compressore, portata e pompa** (frequenza e target in Hz, pompa in %, portata in L/min su un secondo asse a destra). Le fasce colorate dietro le linee dicono quando scaldava la casa (rosso) o l'acqua (blu). Il grafico mostra i dati di SmartThings così come arrivano, senza correzioni. I periodi in cui non c'è nessun dato (pannello spento, collegamento caduto) sono segnati con righe oblique, così non sembrano valori a zero.
Da **computer**, con 6h o 24h, puoi **tornare indietro**: tieni premuto il mouse su un grafico (o sulla barra dei modi) e trascinalo verso destra per vedere le ore (o il giorno) prima, verso sinistra per tornare avanti; finché sei indietro il pannello non si aggiorna da solo, e «Torna ad adesso» rimette la diretta. Da **telefono** pizzica con due dita per ingrandire il tempo (tutti i grafici insieme), trascina con un dito per spostarti e fai doppio tocco per tornare alla vista intera.
Nel grafico della temperatura ambiente il pannello segna anche le **finestre aperte**: cerca i cali più rapidi di quelli naturali (guarda anche l'umidità assoluta, che con la finestra aperta scende) e li mostra con una fascia verde, anche sui giorni già registrati. La fascia piena è una finestra confermata da te, quella tenue con i bordi tratteggiati è ancora da confermare. Su computer c'è anche una iconcina (piena o vuota): toccala e rispondi Sì o No (i «No» spariscono, i «Sì» si possono togliere); il tasto **Segnala finestra** sotto il grafico ne segna una a mano, scegliendo da quando a quando. Dopo 10 risposte il pannello sceglie da solo le soglie (calo minimo e umidità) che sbagliano meno rispetto a quello che gli hai detto. Per ora le finestre sono solo mostrate: le statistiche non le escludono. Da telefono si vedono soltanto, senza icone.

**Efficienza** (1 / 3 / 7 / 28 giorni)
COP, energia elettrica, calore prodotto, ore di lavoro, energia e COP giorno per giorno, COP in base alla temperatura esterna e i cicli (numero, durata, quanti troppo brevi).
Il riquadro **Sbrinamenti** conta gli sbrinamenti del periodo; toccandolo si apre l'elenco (ultimi 50: data, durata, kWh consumati e temperatura esterna) con la media in cima. I tre grafici (energia, COP, COP per temperatura esterna) hanno il cursore: passa il mouse o tieni premuto il dito per leggere i valori.
Se in *Impostazioni → Costo dell'energia* scrivi il prezzo (€ per kWh, tasse comprese), compaiono anche il **costo stimato** del periodo e il costo medio di un ciclo di acqua calda. È una stima: kWh della pompa per il prezzo, senza quota fissa. Toccando «Costo stimato» compare la divisione tra riscaldamento, acqua calda e standby (la somma fa sempre il totale); lo stesso, con il mouse su «Costo», nella card Energia della Dashboard. In più c'è il **costo del mese in corso**: speso finora, stima a fine mese e confronto con il mese scorso (non dipende dal periodo scelto). In fondo alla pagina ci sono i link per **esportare in CSV** (riepilogo per giorno o dati grezzi degli ultimi 7 giorni), pensati per Excel italiano (punto e virgola, virgola decimale).

Il pulsante **Confronta** (spento quando apri la pagina) affianca i numeri del periodo scelto a quelli del periodo precedente della stessa lunghezza, con la differenza in percentuale. Se per il periodo precedente mancano dati, lo dice.
Con **Confronta** acceso, sulle barre dell'energia compare un trattino «tipico» (come si consuma di solito con quel freddo, cioè nei giorni con temperatura esterna entro ±2 °C) e un puntino se il giorno è oltre un quarto sopra il tipico; il COP ha una linea tratteggiata del suo valore tipico. Servono almeno 3 giorni simili: altrimenti non mostra niente, per non inventare valori.
La scheda **Salute del bollitore** stima quanto calore perde l'acqua calda di notte (in °C all'ora e in watt) e quanto ci mette a salire di 10 °C quando la pompa la scalda. Servono almeno due notti e due cicli completi; i litri del bollitore (300 di partenza) sono in `config.json` → `bollitore`. Accanto alla perdita di notte c'è quella **di giorno** (dalle 6 alle 23, solo nei tratti tranquilli di almeno un'ora, senza riscaldo né prelievi): serve a confrontare le ore con e senza ricircolo. Se la perdita aumenta nei mesi, può esserci un problema di isolamento o di ricircolo.

> **Il COP è una stima.** Il calore prodotto lo ricavo da portata × salto di temperatura tra mandata e ritorno × 0,0698 kW per (L/min·°C); l'energia elettrica viene dalla potenza che la pdc dichiara. Il COP conta solo i momenti di lavoro (casa + acqua calda), non lo standby. Può differire da quello di altri servizi: usalo per **confrontare nel tempo**, non come numero assoluto. Se mancano dati, la pagina ti mostra un avviso.

**Dispositivi**
Una scheda per la pompa di calore e una per lo Smarther: identità e modello, **versioni** (firmware del modulo Wi-Fi, software e firmware delle schede della pompa, firmware dello Smarther), identificativi con il pulsante **Copia**, **IP e MAC**, stato Online/Offline e qualità del Wi-Fi dello Smarther, e il **programma orario** che il termostato ha in memoria (a fasce oppure sempre fisso). In fondo c'è lo **storico delle versioni**: il pannello segna ogni volta che una versione cambia (per esempio se SmartThings aggiorna la pompa senza dirtelo). Le date partono da quando il pannello ha iniziato a guardare.
- **IP e MAC**: SmartThings non comunica l'IP della pompa. Scrivilo in *Impostazioni → Dispositivi* (insieme a quello dello Smarther): il pannello ricava il MAC leggendo la tabella di rete del PC e lo ricorda per una settimana (se non lo trova, riprova dopo 2 minuti). Per lo Smarther il MAC è già l'identificativo del modulo Netatmo. Se non lo trova, la pagina te lo dice.
- **Modello dello Smarther**: Netatmo dice solo "BNS". In *Impostazioni → Dispositivi* scegli l'articolo che hai (XW8002, XM8002, XG8002 o XW8002W): comparirà anche installazione e colorazione. I dati tecnici sono quelli del catalogo BTicino.
- **Installatore** (la scheda accanto a *Dispositivi*, indirizzo `#/dispositivi/installatore`): l'elenco delle impostazioni installatore (i codici FSV del manuale: curva climatica, acqua calda, riscaldamento…) che la pompa comunica tramite SmartThings, raggruppate per menu, ognuna con il suo nome in italiano. Per cambiarle vedi «Regolazioni della pompa» (capitolo 11). Sotto ogni valore c'è l'intervallo ammesso dal manuale Samsung EHS Mono (può cambiare da modello a modello); se un valore ne esce compare «fuori limiti». Se un valore cambia mentre il pannello è acceso, sotto compare quando e quanto valeva prima. *Copia elenco* mette tutto negli appunti, comodo da mandare all'installatore. I codici che la pompa manda e che non conosco compaiono in fondo come «Altri codici», col solo valore.
- **Loghi**: non li fornisce il pannello. Crea (se non c'è) la cartella **`dati\loghi`** accanto a `pdc_pannello.py` e mettici i tuoi file con questi nomi: `smarther.svg`, `samsung.svg`, `netatmo.svg`, `bticino.svg` (vanno bene anche `.png`, `.webp`, `.jpg`). Compaiono nelle schede e il logo Smarther anche sulla Dashboard. Se un file non c'è si vede il nome scritto. Se un logo è scuro e si perde sul tema scuro, attiva *Mostra i loghi in bianco* in *Impostazioni → Dispositivi*.

**Meteo di oggi**
Nella card Esterna c'è l'icona del tempo di oggi con la forchetta di temperatura (due modelli a confronto, ICON-2I ed ECMWF, da Open-Meteo): scrive «pioggia», «possibile pioggia» o «asciutto». Toccala per il dettaglio: 3 righe da 3 ore a partire da adesso, sempre con «Oggi»; dopo le 20 la riga può annunciare la pioggia di domani. La posizione sta in `config.json` → `meteo` (`latitudine` e `longitudine`; svuotale per spegnere il meteo). La previsione si rilegge ogni 30 minuti.

**Umidità esterna**
Facoltativa. Nella card Esterna della Dashboard, sotto Min/Max, compare «Umidità 78%», letta da una stazione meteo vicina (WeatherLink); se appare l'avviso «Dato delle… (PdC ferma)» prende il suo posto, senza cambiare le dimensioni della card. Per accenderla: lancia `python pdc_strumenti.py meteo-prova "<indirizzo della pagina della stazione>"` (capitolo 8 di `README_STRUMENTI.md`), copia l'**indirizzo dei dati** che stampa e incollalo in *Impostazioni → Collegamenti → Stazione meteo vicina*. Il pannello la rilegge ogni 10 minuti (insieme al meteo, in un solo lavoro di fondo che gira anche con la Dashboard chiusa, quindi Alexa e il widget la trovano sempre pronta); se la stazione non manda dati da più di un'ora, l'umidità sparisce. Con il campo vuoto la funzione è spenta e la Dashboard resta identica. Non viene salvata né disegnata in grafici: è solo il valore di adesso. La temperatura esterna resta quella della sonda della pompa.

**Changelog**
La cronologia delle versioni, scritta in italiano semplice, con la più recente in alto. Ha due tab: **Pannello** e **Plugin** (la cronologia di ogni plugin, tutti insieme o uno alla volta). Sul computer si apre dalla voce piccola «Changelog» sopra «Esci»; sul telefono, per non affollare la barra in basso, dal link «Changelog» accanto alla versione in *Impostazioni → Stato*.

**Impostazioni**
Divisa in sette tab, con i riquadri che si aprono e si chiudono con un tocco (il pannello ricorda come li hai lasciati; sugli schermi larghi i riquadri chiusi stanno su due colonne, quelli aperti più larghi passano a tutta riga):
- **Stato**: versione, collegamenti, token, database, aggiornamento a caldo.
- **Collegamenti**: SmartThings e Netatmo (ID e token), dati più freschi, stazione meteo vicina, login del pannello, intervalli di lettura, dispositivi (IP, modello, loghi).
- **Avvisi**: Telegram, silenzio notturno e riepilogo, testi dei messaggi, soglie di ogni allarme.
- **Integrazioni**: i plugin, come Alexa (capitoli 18 e 19).
- **Accesso**: password del pannello e accesso da internet (Cloudflare, con il pulsante che ti manda il link su Telegram).
- **Dati**: costo dell'energia, litri del bollitore, backup, storico, azzera il database.
- **Registro**: ultime righe del registro, dimensioni dei file e backup.
Le modifiche di tutti i tab si salvano insieme con il pulsante **Salva** in basso; i valori vengono controllati prima di salvare, e se uno è sbagliato il pannello apre il riquadro giusto.

---

### Accendere e spegnere dal pannello

Dalla **Dashboard**, nella scheda *Stato del sistema*, ci sono tre interruttori: **Riscaldamento casa**, **Acqua calda sanitaria** e **Termostato Smarther**.

- Sono **bloccati** finché non premi **Sblocca comandi** (si richiudono da soli dopo 30 secondi): serve a non spegnere il riscaldamento con un tocco sbagliato dal telefono.
- **Spegnere** chiede una conferma, accendere no.
- Dopo il tocco l'interruttore si sposta subito e compare il badge giallo **ATTENDI…** finché il comando non è finito (per lo Smarther fino a una decina di secondi, perché il pannello controlla che Netatmo abbia davvero cambiato stato). Poi il badge torna a ACCESO/SPENTO e il pannello rilegge lo stato.
- Lo sblocco dura 30 secondi **dall'ultima azione**: ogni comando lo fa ripartire.
- Ogni comando finisce nell'elenco **Comandi dati dal pannello** (pagina *Dispositivi*, a pagine da 5), con data, ora, indirizzo da cui è partito ed esito. Non c'è nessun avviso su Telegram.
- I comandi funzionano anche da fuori casa, con lo stesso login del pannello.
- **Lo Smarther si spegne in due modi diversi**, e il pannello li riconosce entrambi: dal pannello la *casa* va in antigelo (`therm_mode: hg`) e la stanza resta in «home» a 7 °C; dalla sua app invece è la *stanza* ad andare in antigelo (`therm_setpoint_mode: hg`) con la casa in «schedule». Per spegnere il pannello mette la casa in antigelo; per accendere rimette la casa in programma **e** la stanza in «home». Dopo ogni tentativo rilegge da Netatmo: se lo stato non è cambiato passa alla strada successiva, e nel registro vedi quale ha funzionato e le risposte di Netatmo per le altre.

**Servono permessi di comando sui token** (finché non li dai, il pannello legge e basta, e un comando riceve una risposta chiara: «il token non ha il permesso»).

*SmartThings con OAuth* (come nel punto 4c):
1. Vedi l'ID della tua app: `smartthings apps`.
2. Aggiungi il permesso di comando: `smartthings apps:oauth:update ID_DELLA_APP`; quando chiede gli *scopes* tieni `r:devices:*` e aggiungi **`x:devices:*`** (il Redirect URI resta `https://httpbin.org/get`). Se la CLI non te lo lascia fare, crea una nuova app con `smartthings apps:create` mettendo entrambi gli scopes e usa il nuovo Client ID e Secret.
3. **Ferma il pannello**, poi con il nuovo `pdc_strumenti.py` lancia di nuovo `python pdc_strumenti.py st-oauth` e rifai il consenso: i token nuovi hanno anche il permesso di comando e vengono scritti in `config.json`.
4. Riavvia il pannello.

*SmartThings con il token personale (PAT)*: alla creazione spunta anche **Devices → Control all devices**.

*Netatmo*: su <https://dev.netatmo.com> → *My apps* → la tua app → *Token generator*, spunta **`read_smarther`** e **`write_smarther`** (più quelli che già usavi), genera il token. **Ferma il pannello**, copia i due token nuovi (`access_token` e `refresh_token`) in `config.json`, sezione `netatmo`, e riavvia. Rigenerarli invalida i vecchi.

*Prova consigliata*: sblocca, e prova prima con **Acqua calda sanitaria** (spegni e riaccendi). Se qualcosa non va, il messaggio ti dice se manca un permesso o cosa ha risposto il servizio. Se Netatmo rifiuta il comando, il messaggio e l'ultima riga del registro in *Dispositivi* riportano la sua risposta: mandala a chi ti aiuta.

### Regolazioni della pompa

Nella pagina *Dispositivi*, scheda **Regolazioni** (indirizzo `#/dispositivi/installatore`):

- In alto: temperatura e modo dell'acqua calda, modo della pompa (Riscaldamento, Raffrescamento, Automatico) e il **Boost** dell'acqua calda (⚡ alza il setpoint all'obiettivo scelto e mette il modo «Forzato»; finisce quando l'acqua arriva all'obiettivo o dopo 2 ore, poi rimette modo e setpoint di prima, anche se nel frattempo il pannello si riavvia; ■ lo ferma a mano).
- Sotto: le impostazioni installatore (codici FSV). Si cambia un valore alla volta: **Sblocca modifiche** (30 secondi), la matita ✎ sulla riga, il nuovo valore e una conferma. Il pannello accetta solo codici e limiti del manuale; la riga resta «in attesa» finché la pompa non conferma il valore nuovo.
- In fondo, la **Cronologia delle modifiche**: ogni cambio visto, con il valore di prima e di dopo, e se l'ha fatto il pannello o è stato letto dalla pompa.
- **Solo dalla rete di casa**: da fuori (tunnel Cloudflare) il lucchetto non compare e le richieste vengono rifiutate.
- Servono gli stessi permessi di comando di SmartThings degli interruttori (vedi sopra). Cambiare un FSV a caso può far lavorare male la pompa: nel dubbio chiedi all'installatore.

## 12. Tutti i comandi

```
python pdc_pannello.py                      avvia tutto (raccolta + web + Telegram + tunnel)
python pdc_pannello.py --demo               prova con dati finti
python pdc_pannello.py --porta 7010         usa un'altra porta, solo per questa volta
python pdc_pannello.py --una-volta          una lettura e mostra i valori (non avvia il web)
python pdc_pannello.py stato                ultimi valori salvati e anomalie aperte
python pdc_pannello.py imposta-password     scegli utente e password
python pdc_pannello.py telegram-prova       manda un messaggio di prova
python pdc_pannello.py compatta             compatta ora lo storico vecchio
python pdc_pannello.py backup               copia ora database e config.json nella cartella dati\backup
python pdc_pannello.py ripristina           rimette una copia di backup o una fatta prima di un reset (a pannello fermo)
python pdc_pannello.py ridecodifica         rifà la lettura dei dati grezzi del ciclo
python pdc_pannello.py esporta-diagnostica  crea un file di diagnosi da mandare a chi ti aiuta
python pdc_pannello.py --versione
```

Nella stessa cartella c'è anche `pdc_strumenti.py` (vedi `README_STRUMENTI.md`): oltre ai dump, `rip-diag` e `sbrinamenti [--giorni 7] [--salva]` (elenco degli sbrinamenti letti dal database). Anche `nt-webhook-prova` (prova dei webhook Netatmo, capitolo 6 di `README_STRUMENTI.md`), `meteo-prova` (stazione meteo vicina, capitolo 8) e **`analisi`**: crea in un colpo solo un file `diagnostica/analisi_….json` con tutti i dati del database (impostazioni, cicli, sbrinamenti, serie nel tempo, senza token né indirizzi), da mandare a chi ti aiuta (capitolo 9). Ci sono anche `st-capacita` e `st-osserva` (i comandi della pompa e cosa cambia quando premi Boost, capitolo 10).

Per pythonw usa gli stessi argomenti, ma non vedrai l'output: i comandi “da guardare” (`stato`, `telegram-prova`…) lanciali con `python`.

---

## 13. Il file config.json spiegato

| Sezione | Cosa c'è |
|---|---|
| `generale` | `porta` (7000), `host` (`0.0.0.0` = raggiungibile dalla rete; `127.0.0.1` = solo dal PC), `intervallo_pdc_secondi` (60, minimo 20), `intervallo_netatmo_secondi` (120), `database`, `sessione_giorni` (30), `porta_lock` |
| `accesso` | utente e hash della password (non scrivere l'hash a mano) |
| `smartthings` | `device_id`, `pat`, `oauth` (compilato da `st-oauth`; i token ruotano da soli) e `refresh` (`attivo`, `ogni_secondi`: vedi «Dati più freschi») |
| `netatmo` | chiavi dell'app, token (ruotano da soli), `home_id`, `room_id` |
| `telegram` | credenziali, orari, testi, limiti |
| `allarmi` | soglie e interruttori di ogni allarme |
| `dispositivi` | `ip_pdc`, `ip_smarther` (facoltativi, per mostrare IP e MAC), `modello_smarther`, `loghi_bianchi` |
| `cloudflare` | compilata dalla procedura guidata |
| `meteo` | `latitudine` e `longitudine` per il meteo di oggi nella card Esterna (Open-Meteo); vuote = meteo spento |
| `stazione` | `url`: indirizzo dei dati della stazione WeatherLink vicina (umidità esterna); vuoto = spenta. Si cambia da *Impostazioni → Collegamenti* |
| `umidita` | `giallo` (60), `rosso` (70), `minuti` (30), `rientro_punti` (2): le soglie dell'umidità, uniche per Telegram, Dashboard e grafico |
| `tariffa` | `prezzo_kwh`: euro per kWh (0 = non mostrare i costi) |
| `bollitore` | `litri` (300): serve per la salute del bollitore |
| `backup` | `attivo`, `cartella` (`dati/backup`), `copie` (quante tenerne, 7) |
| `conservazione` | quanti giorni di dati dettagliati tenere (365) e ogni quanti minuti fare la media dopo (15); `elimina_dati_grezzi_ciclo` (sì) toglie i byte grezzi del ciclo quando compatta |
| `plugin` | un riquadro per plugin: `attivo` (spento di partenza) e i campi che il plugin dichiara. Per Alexa: `segreto`, `skill_id`, `verifica_firma` (capitolo 19). Si cambiano da *Impostazioni → Integrazioni* |
| `mappa_ciclo` | come si leggono i dati grezzi del compressore (non toccarla se non te lo chiede chi ti aiuta) |

Le modifiche valgono quasi tutte **subito**; fanno eccezione porta, host e intervalli di lettura, che valgono dal riavvio.

---

## 14. Manutenzione, backup, aggiornamenti

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
    plugin\                le integrazioni (alexa.py…): vedi capitolo 18
    docs\                  README.md, PIANO.md, README_STRUMENTI.md (se li vuoi tenere qui)
  ```

---

## 15. Se qualcosa non va

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
| «refresh fermo: manca il permesso di comando» (Dashboard) | Il token SmartThings non ha lo scope `x:devices:*`: vedi «Servono permessi di comando sui token». Oppure spegni l'opzione. |
| SmartThings 402/“a pagamento” | Il servizio potrebbe essere diventato a pagamento: guarda il messaggio nella Dashboard. |
| “Pompa offline” | La pdc non risulta connessa a SmartThings (wifi/modulo): controlla nell'app SmartThings. |
| Netatmo: token rifiutato | Rigenera i token dal portale con gli scope dello Smarther e incollali (i vecchi sono invalidati). |
| Il COP o le “ore di funzionamento” sono strani | Controlla l'avviso giallo in Efficienza: se mancano dati di potenza o portata, il COP è parziale. |
| Mancano i dati del ciclo (frequenza, portata…) | Si aggiornano solo mentre la pdc è in funzione: da ferma non ci sono punti nuovi. |
| Il tunnel dice “non collegato” | Guarda `log\cloudflared.log`; prova `protocollo` = `http2`; controlla che il PC abbia internet; verifica che l'antivirus non blocchi `cloudflared.exe`. |
| Telegram non arriva | `python pdc_pannello.py telegram-prova` dice il motivo. Hai scritto almeno un messaggio al bot? |
| Non so cosa succede | Guarda `log\pdc_pannello.log`; se devi chiedere aiuto, `python pdc_pannello.py esporta-diagnostica` crea `diagnostica\diagnostica.json`, senza password né token. |

---

## 16. Sicurezza: cosa è bene sapere

- Il pannello **legge** tutto e **comanda** solo quello che gli permetti (e solo se i token hanno il permesso di comando). Accendere/spegnere riscaldamento, acqua calda e Smarther si può anche da fuori casa; le **regolazioni della pompa** (codici FSV, temperatura e modo dell'acqua calda, modo della pompa, boost) solo dalla rete di casa, e il pannello rifiuta la richiesta se arriva dal tunnel. In ogni caso il login è sempre richiesto e tutto sta dietro un lucchetto.
- Il login è **sempre richiesto**. Le password sono salvate solo come impronta (PBKDF2), i tentativi sbagliati vengono limitati, i cookie di sessione sono protetti.
- In **rete di casa** il collegamento è HTTP normale: chi è sul tuo wifi potrebbe, in teoria, leggere la password mentre la digiti. Su una rete domestica fidata è ragionevole; se non lo è, usa l'indirizzo Cloudflare (HTTPS) anche da casa.
- **`config.json` è riservato**: contiene token SmartThings/Netatmo, il token del bot Telegram e quello del tunnel. Non condividerlo, non metterlo in cartelle sincronizzate pubblicamente.
- Se pubblichi il pannello su internet, scegli una password lunga e unica. Per una protezione in più puoi aggiungere **Cloudflare Access** (Zero Trust) davanti all'indirizzo: è gratuito per pochi utenti ed è un secondo lucchetto prima ancora di arrivare al login.
- Le **integrazioni a plugin** sono codice che gira con gli stessi permessi del pannello: copia nella cartella `plugin` solo file di cui ti fidi. Un plugin nuovo parte spento. Gli indirizzi pubblici dei plugin (`/plugin/…`) rispondono **senza login**: per questo ognuno deve difendersi da solo (Alexa usa un indirizzo segreto, riconosce solo la tua skill, scarta le richieste vecchie o ripetute e, se vuoi, controlla la firma di Amazon: capitolo 19) e il pannello limita comunque dimensione (64 KB) e frequenza (60 richieste al minuto per indirizzo).
- Il COP, le soglie degli allarmi e le fasce di funzionamento sono **stime ragionevoli, non misure certificate**: servono per capire le tendenze e accorgersi dei guasti, non per diagnosi tecniche.

---

## 17. Per chi modifica il codice

Il programma che avvii (`pdc_pannello.py`) è un file **generato**. I sorgenti veri stanno in `sorgenti/` (una parte per argomento: nucleo, web, Telegram, impostazioni, Cloudflare, manutenzione, dispositivi, controlli, aggiornamento, comandi; in `sorgenti/web/` pagine, stile e script). Per rigenerarlo:

```
python costruisci.py
```

Le pagine e i dati che il pannello manda al browser sono compressi (gzip), così su telefono pesano circa un terzo; `app.js` e `app.css` restano nella cache del browser (l'indirizzo cambia a ogni versione) e la prima Dashboard arriva già dentro la pagina. Le note tecniche (architettura, come è fatto il database, come si leggono i dati grezzi, decisioni) sono in `PIANO.md`; la cronologia delle versioni è nel Changelog del pannello. Gli strumenti di appoggio (dump SmartThings/Netatmo, OAuth, esperimenti) sono in `pdc_strumenti.py`, descritti in `README_STRUMENTI.md`.

## 18. Integrazioni (plugin)

Le integrazioni opzionali sono file nella cartella `plugin`, accanto a `pdc_pannello.py` (se non c'è, creala). In *Impostazioni → Integrazioni* vedi ogni plugin con il suo stato, l'interruttore (**un plugin nuovo parte spento**), i suoi campi e gli indirizzi pubblici che espone. Dopo aver aggiunto o cambiato un file premi *Ricarica i plugin*: non serve riavviare il pannello.

- Stato, numero di richieste e «ultima richiesta» di ogni plugin si aggiornano da soli ogni pochi secondi mentre guardi la scheda; quello che stai scrivendo nei campi non viene toccato.
- Gli indirizzi pubblici si mostrano già completi, con il tuo indirizzo Cloudflare (se l'hai configurato), pronti da copiare con il pulsante **Copia**.
- Un errore dentro un plugin non ferma mai il pannello: il plugin va in «errore» e il motivo è scritto lì (e nel registro).
- Un plugin gira con gli stessi permessi del pannello: metti nella cartella solo file di cui ti fidi.
- Un plugin può esporre indirizzi **senza login** (`/plugin/nome/…`) solo da acceso; il pannello limita dimensione e frequenza delle richieste, ma l'autenticazione è compito del plugin.
- `plugin_esempio/esempio.py` è un plugin di prova: copialo in `plugin` e provalo (`/plugin/esempio/ping`). `plugin_esempio/alexa.py` è il plugin Alexa (capitolo 19).

## 19. Alexa (il plugin per le Routine)

Plugin `alexa.py`, versione 1.4.0. Con una **skill personale** di Alexa (visibile solo al tuo account Amazon) e le **Routine** puoi chiedere al pannello cose come «che temperatura c'è» o «il riscaldamento è acceso», e accendere o spegnere lo Smarther, senza pronunciare il nome della skill: è la Routine a dirlo per te. I file stanno nella cartella `plugin_esempio`: `alexa.py` (il plugin), `alexa_modello_it.json` (il modello della skill), `ALEXA_FRASI.md` (l'elenco completo di frasi e risposte) e `ALEXA_CHANGELOG.md` (la cronologia del plugin).

**Cosa serve**: il pannello raggiungibile da internet (capitolo 9, Cloudflare) e un account sviluppatore Amazon gratuito (developer.amazon.com; chiede la verifica dell'identità).

### Installazione, passo per passo

1. Copia `alexa.py` nella cartella `plugin`. In *Impostazioni → Integrazioni* premi *Ricarica i plugin* e accendi l'interruttore di **Alexa**: il plugin genera da solo un **segreto** e lo mette nell'indirizzo.
2. Nello stesso riquadro, sotto *Indirizzi pubblici*, premi **Copia** accanto all'indirizzo (`https://tuo-dominio/plugin/alexa/…`). Non condividerlo: chi lo conosce può provare a chiamare il plugin.
3. Su <https://developer.amazon.com/alexa/console/ask> scegli *Create Skill*: nome a piacere, lingua **Italiano (Italia)**, tipo **Custom**, hosting **Provision your own**, template **Start from scratch**.
4. Vai in *Build → Interaction Model → JSON Editor*, cancella tutto e incolla il contenuto di `alexa_modello_it.json` (nome di invocazione: `centrale termica`, cambialo se vuoi). **Save** e **Build**.
5. In *Build → Endpoint* scegli **HTTPS**, incolla l'indirizzo copiato nel campo *Default Region* e, nel menu sotto, la voce che dice che l'endpoint ha un certificato di un'autorità riconosciuta (quello di Cloudflare lo è). **Save**.
6. Nella scheda *Test* abilita la skill in **Development** (se resta *Off* non risponde). Prova nel simulatore: «apri centrale termica» e poi «temperatura».
7. Nell'app **Alexa** crea una **Routine** per ogni frase che vuoi: *Quando* → «Voce» con la frase che dirai (per esempio «umidità»); *Azione* → **Personalizzata**, con il testo `chiedi a centrale termica umidità`. Ripeti per le altre (frasi in `ALEXA_FRASI.md`).

L'indirizzo, il segreto e l'ID della skill si vedono in *Impostazioni → Integrazioni*. Dopo la prima richiesta lo stato dice «ultima richiesta: …» e il contatore sale.

### Cosa fa e cosa no

- A voce si possono solo **leggere i dati** e **accendere/spegnere lo Smarther**. Riscaldamento e acqua calda della pompa non si comandano da Alexa.
- Le risposte usano gli stessi dati della Dashboard; se l'ultimo dato ha più di 30 minuti lo dice.
- **Alexa+**: con Alexa+ attiva le richieste «apri / chiedi a…» possono essere intercettate dalla Casa intelligente o abbinate a un'altra skill, e a volte Alexa risponde «non ho trovato il sensore»: la skill risponde bene quando viene raggiunta, ma l'esito è instabile. Amazon garantisce le skill personalizzate solo sull'Alexa classica, quindi, finché non lo sistema, il consiglio è l'Alexa classica («Alexa, esci da Alexa+»).

### Sicurezza dell'indirizzo

Chi arriva all'indirizzo non è passato dal login del pannello, quindi il plugin controlla da solo, nell'ordine:
1. l'**indirizzo contiene un segreto** (da 24 a 64 caratteri). Per cambiarlo scrivi un valore qualunque non valido (per esempio `x`) nel campo *Segreto dell'indirizzo*, salva e ricarica i plugin: ne genera uno nuovo; poi incolla il nuovo indirizzo nella console Amazon (*Endpoint*) e fai Save e Build;
2. la richiesta deve arrivare dalla **tua skill**: la prima volta il plugin ne memorizza l'ID (campo *ID della skill*, svuotalo per fargliela imparare di nuovo); le altre skill sono rifiutate;
3. la data della richiesta deve essere recente (al massimo 150 secondi) e ogni richiesta vale **una volta sola**, così una richiesta intercettata non si può ripetere;
4. facoltativo: **Verifica firma Amazon**, con tre modi: *Spenta* (di partenza), *Solo avviso* (controlla e scrive l'esito nello stato, ma accetta comunque) e *Attiva* (rifiuta le richieste con firma non valida). Consiglio: prova prima «Solo avviso» e passa ad «Attiva» quando lo stato dice «firma Amazon valida». Serve la libreria Python **cryptography** (`python -m pip install cryptography`); senza, il plugin continua a funzionare e lo stato dice «firma non verificata». Il controllo verifica che il certificato arrivi da `s3.amazonaws.com`, sia valido, sia intestato a `echo-api.amazon.com` e porti a una radice di fiducia del tuo sistema, poi controlla la firma sul contenuto. Il certificato si riscarica da solo in background ogni 50 minuti.

Il pannello limita comunque dimensione e frequenza delle richieste (capitolo 16).

### Se qualcosa non va

| Sintomo | Cosa fare |
|---|---|
| «Si è verificato un problema con la risposta della skill» | Guarda lo stato del plugin: se la richiesta non è arrivata, l'indirizzo nella console non è quello attuale (hai cambiato il segreto?) o il plugin è spento. |
| Alexa risponde con altro («il termostato rileva…») | Alexa sta usando un'altra skill o una funzione sua: controlla il nome di invocazione (`centrale termica`), che la skill sia in *Development* e prova senza Alexa+. |
| Non arriva niente a Alexa dopo il passaggio ad «Attiva» | Rimetti *Solo avviso* e leggi nello stato il motivo scritto fra parentesi dopo «firma NON valida». |
| Il contatore non sale | Ricarica la scheda Integrazioni; controlla il tunnel (capitolo 9) e che l'interruttore del plugin sia acceso. |

## 20. Widget per iPhone (plugin e script Scriptable)

Il plugin `widget.py` (versione 1.1.0) crea un indirizzo pubblico, di sola lettura, con i pochi dati che servono al widget: temperatura e umidità di casa, termostato (chiama / riparte / non chiama), stato della pompa di calore e acqua calda, temperatura e umidità di fuori. Lo script `widget_scriptable.js` li disegna come widget medio nell'app gratuita **Scriptable**. Si installa come gli altri plugin (Impostazioni → Integrazioni), si copia l'indirizzo in «Indirizzi pubblici» e si incolla nello script.

**Sicurezza**: l'indirizzo contiene un segreto casuale; in più il campo **Token del widget** (da 24 a 64 caratteri) va scritto uguale anche nello script (costante `TOKEN`): senza il token l'indirizzo risponde 404, quindi aprirlo da un browser non mostra niente. Il widget può solo leggere. Per cambiare segreto o token basta scriverne uno nuovo, ricaricare i plugin e aggiornare lo script. La cronologia del plugin sta in `WIDGET_CHANGELOG.md`.

**Aggiornamento del widget**: iOS decide da solo quando ridisegnarlo (lo script gli chiede 15 minuti, ma ritardi di 20-30 minuti sono normali, di più con il Risparmio energetico o l'app Scriptable senza «Aggiorna app in background»). Toccare il widget apre il pannello; per forzare l'aggiornamento c'è la riga in basso a destra con l'ora: apre un Comando rapido chiamato **Aggiorna widget** (app Comandi → nuovo comando con l'azione di Scriptable «Refresh All Widgets» e, come ultima, «Vai alla schermata Home»). Il nome si cambia nella costante `COMANDO_AGGIORNA` all'inizio dello script. Per un attimo si vedono Comandi e Scriptable, poi si torna alla Home.

---

## 21. Crediti

- **Idea, requisiti e prove sul campo**: Alessandro, sul suo impianto (Samsung EHS Mono, Smarther 2). **Scritto insieme a Claude** (Anthropic).
- **[Dwellsee](https://www.dwellsee.com)**: l'ispirazione per le pagine e le funzioni del pannello (Dashboard, Analisi, Efficienza, curva climatica, Boost) e la «Cycle Data Decoding Guide», da cui viene la lettura dei dati grezzi del compressore (`samsungce.ehsCycleData`).
- **[pookey/samsung-heatpump-smartthings](https://github.com/pookey/samsung-heatpump-smartthings)**: gli script da cui è partito il progetto.
- **[Open-Meteo](https://open-meteo.com)**: le previsioni del meteo di oggi (dati con licenza CC BY 4.0), con i modelli ICON-2I ed ECMWF.
- **WeatherLink**: la pagina pubblica della stazione meteo vicina da cui si legge l'umidità esterna.
- **Servizi e programmi usati**: SmartThings (Samsung), Netatmo e lo Smarther 2 (BTicino), Telegram, Cloudflare e il suo `cloudflared`, Alexa (Amazon) e l'app Scriptable per il widget.

I nomi e i marchi appartengono ai rispettivi proprietari; questo progetto non è affiliato a nessuno di loro.
