# Integrazioni (plugin), Alexa e widget

## Integrazioni (plugin)

Le integrazioni opzionali sono file nella cartella `plugin`, accanto a `pdc_pannello.py` (se non c'è, creala). In *Impostazioni → Integrazioni* vedi ogni plugin con il suo stato, l'interruttore (**un plugin nuovo parte spento**), i suoi campi e gli indirizzi pubblici che espone. Dopo aver aggiunto o cambiato un file premi *Ricarica i plugin*: non serve riavviare il pannello.

- Stato, numero di richieste e «ultima richiesta» di ogni plugin si aggiornano da soli ogni pochi secondi mentre guardi la scheda; quello che stai scrivendo nei campi non viene toccato.
- Gli indirizzi pubblici si mostrano già completi, con il tuo indirizzo Cloudflare (se l'hai configurato), pronti da copiare con il pulsante **Copia**.
- Un errore dentro un plugin non ferma mai il pannello: il plugin va in «errore» e il motivo è scritto lì (e nel registro).
- Un plugin gira con gli stessi permessi del pannello: metti nella cartella solo file di cui ti fidi.
- Un plugin può esporre indirizzi **senza login** (`/plugin/nome/…`) solo da acceso; il pannello limita dimensione e frequenza delle richieste, ma l'autenticazione è compito del plugin.
- `plugin_esempio/esempio.py` è un plugin di prova: copialo in `plugin` e provalo (`/plugin/esempio/ping`). `plugin_esempio/alexa.py` è il plugin Alexa (vedi sotto).


---

## Alexa (il plugin per le Routine)

Plugin `alexa.py`, versione 1.4.1. Con una **skill personale** di Alexa (visibile solo al tuo account Amazon) e le **Routine** puoi chiedere al pannello cose come «che temperatura c'è» o «il riscaldamento è acceso», e accendere o spegnere lo Smarther, senza pronunciare il nome della skill: è la Routine a dirlo per te. I file stanno nella cartella `plugin_esempio`: `alexa.py` (il plugin), `alexa_modello_it.json` (il modello della skill), `ALEXA_FRASI.md` (l'elenco completo di frasi e risposte) e `ALEXA_CHANGELOG.md` (la cronologia del plugin).

**Cosa serve**: il pannello raggiungibile da internet ([telegram-e-cloudflare.md](telegram-e-cloudflare.md)) e un account sviluppatore Amazon gratuito (developer.amazon.com; chiede la verifica dell'identità).

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
4. facoltativo: **Verifica firma Amazon**, con tre modi: *Spenta* (di partenza), *Solo avviso* (controlla e scrive l'esito nello stato, ma accetta comunque) e *Attiva* (rifiuta le richieste con firma non valida **o non verificabile**: se manca la libreria o i certificati di sistema, con «Attiva» Alexa non funziona finché non li sistemi). Consiglio: prova prima «Solo avviso» e passa ad «Attiva» quando lo stato dice «firma Amazon valida». Serve la libreria Python **cryptography** (`python -m pip install cryptography`); senza, il plugin continua a funzionare e lo stato dice «firma non verificata». Il controllo verifica che il certificato arrivi da `s3.amazonaws.com`, sia valido, sia intestato a `echo-api.amazon.com` e porti a una radice di fiducia del tuo sistema, poi controlla la firma sul contenuto. Il certificato si riscarica da solo in background ogni 50 minuti.

Il pannello limita comunque dimensione e frequenza delle richieste (vedi [manutenzione-e-problemi.md](manutenzione-e-problemi.md)).

### Se qualcosa non va

| Sintomo | Cosa fare |
|---|---|
| «Si è verificato un problema con la risposta della skill» | Guarda lo stato del plugin: se la richiesta non è arrivata, l'indirizzo nella console non è quello attuale (hai cambiato il segreto?) o il plugin è spento. |
| Alexa risponde con altro («il termostato rileva…») | Alexa sta usando un'altra skill o una funzione sua: controlla il nome di invocazione (`centrale termica`), che la skill sia in *Development* e prova senza Alexa+. |
| Non arriva niente a Alexa dopo il passaggio ad «Attiva» | Rimetti *Solo avviso* e leggi nello stato il motivo scritto fra parentesi dopo «firma NON valida». |
| Il contatore non sale | Ricarica la scheda Integrazioni; controlla il tunnel (vedi [telegram-e-cloudflare.md](telegram-e-cloudflare.md)) e che l'interruttore del plugin sia acceso. |


---

## Widget per iPhone (plugin e script Scriptable)

Il plugin `widget.py` (versione 1.1.0) crea un indirizzo pubblico, di sola lettura, con i pochi dati che servono al widget: temperatura e umidità di casa, termostato (chiama / riparte / non chiama), stato della pompa di calore e acqua calda, temperatura e umidità di fuori. Lo script `widget_scriptable.js` li disegna come widget medio nell'app gratuita **Scriptable**. Si installa come gli altri plugin (Impostazioni → Integrazioni), si copia l'indirizzo in «Indirizzi pubblici» e si incolla nello script.

**Sicurezza**: l'indirizzo contiene un segreto casuale; in più il campo **Token del widget** (da 24 a 64 caratteri) va scritto uguale anche nello script (costante `TOKEN`): senza il token l'indirizzo risponde 404, quindi aprirlo da un browser non mostra niente. Il widget può solo leggere. Per cambiare segreto o token basta scriverne uno nuovo, ricaricare i plugin e aggiornare lo script. La cronologia del plugin sta in `WIDGET_CHANGELOG.md`.

**Aggiornamento del widget**: iOS decide da solo quando ridisegnarlo (lo script gli chiede 15 minuti, ma ritardi di 20-30 minuti sono normali, di più con il Risparmio energetico o l'app Scriptable senza «Aggiorna app in background»). Toccare il widget apre il pannello; per forzare l'aggiornamento c'è la riga in basso a destra con l'ora: apre un Comando rapido chiamato **Aggiorna widget** (app Comandi → nuovo comando con l'azione di Scriptable «Refresh All Widgets» e, come ultima, «Vai alla schermata Home»). Il nome si cambia nella costante `COMANDO_AGGIORNA` all'inizio dello script. Per un attimo si vedono Comandi e Scriptable, poi si torna alla Home.
