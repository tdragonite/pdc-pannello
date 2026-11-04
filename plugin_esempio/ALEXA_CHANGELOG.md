# Plugin Alexa — changelog

## 1.4.0 — 4 novembre 2026
- **Due nuove domande «fuori»**, come quelle della casa con l'ordine invertito: `TemperaturaFuoriIntent` («Fuori ci sono 8,8 gradi e l'87 per cento di umidità.») e `UmidFuoriIntent` («Fuori c'è l'87 per cento di umidità e ci sono 8,8 gradi.»). La temperatura è quella della sonda della pompa, l'umidità viene dalla stazione meteo vicina (Impostazioni → Collegamenti).
- Senza stazione, o con dato più vecchio di un'ora, dice solo i gradi (con «Non ho l'umidità della stazione» se la stazione è configurata). Se la sonda è ferma da più di 2 ore aggiunge l'avviso.
- La risposta sulla pompa di calore ora dice anche l'umidità di fuori: «Fuori ci sono 8,4 gradi e l'87 per cento di umidità.»
- «Aiuto» e l'apertura della skill nominano anche «com'è fuori».
- **Da rifare nella console Amazon**: reimportare `alexa_modello_it.json` (frase nuova).

## 1.3.1 — 4 novembre 2026
- Spariscono dalla console gli avvisi «CryptographyDeprecationWarning: Parsed a serial number…»: venivano da una radice vecchia nell’archivio di Windows e erano innocui. Se una versione futura della libreria la rifiutasse, la radice viene saltata e le altre restano valide.

## 1.3 — 4 novembre 2026
- L'esito della firma (valida / non valida) nello stato ora appartiene alla singola richiesta: con richieste ravvicinate non si può più mescolare.
- Il download del certificato di Amazon aspetta al massimo 3 secondi (prima 6), per non consumare gli 8 secondi concessi da Alexa.
- Pulizia: il file dei certificati di sistema si chiude sempre.

## 1.2 — 4 novembre 2026
- Il certificato di Amazon si riscarica da solo in background ogni 50 minuti (se fallisce, riprova dopo 5), così nessuna richiesta di Alexa aspetta il download. Solo la prima richiesta dopo un riavvio o una ricarica dei plugin lo scarica al momento. Il thread si ferma quando il plugin viene spento o ricaricato.

## 1.1 — 4 novembre 2026
- **Verifica della firma di Amazon** (campo «Verifica firma Amazon»: *Spenta* / *Solo avviso* / *Attiva*). Serve la libreria Python `cryptography`; senza, il plugin lo scrive nello stato e accetta come prima.
  - Controlla l'indirizzo del certificato (solo `https://s3.amazonaws.com/echo.api/…`), la catena fino a una radice di fiducia del sistema, il nome `echo-api.amazon.com`, le date e la firma RSA-SHA256 del contenuto.
  - Il certificato scaricato resta in memoria per un'ora.
  - Validata con una richiesta reale di Amazon («firma Amazon valida»).
- Il modello vocale (`alexa_modello_it.json`) ora usa il nome di invocazione **centrale termica**.
- Nel pannello, stato e contatore richieste del plugin si aggiornano da soli ogni 5 secondi (solo con la scheda Integrazioni aperta; i campi che stai scrivendo non vengono toccati).

## 1.0 — novembre 2026
Prima versione, skill personalizzata (italiano) con Routine.

**Intenti**: temperatura, umidità, riscaldamento acceso, ripartenza, pompa di calore, acqua calda, mandata, accendi/spegni Smarther.

**Risposte** concordate:
- Temperatura con umidità e impostazione dello Smarther; umidità con temperatura (senza Smarther).
- «Il riscaldamento è acceso» distingue acceso, a riposo (con ora di ripartenza stimata), acqua calda, sbrinamento, disattivato e nessun dato; niente più «sì» solo perché è abilitato.
- Ora di ripartenza detta in italiano («verso le 17 e 15»).
- Testi in SSML semplice (niente tag fonetici).

**Sicurezza**
- Indirizzo con segreto casuale (da 24 a 64 caratteri), generato al primo avvio e rigenerabile scrivendo un valore non valido (per esempio «x»).
- Skill riconosciuta al primo uso (ID memorizzato); le altre vengono rifiutate. L'ID utente Amazon non si controlla: cambia ogni volta che la skill viene disattivata e riattivata.
- Richieste con data oltre 150 secondi vecchie (o oltre 60 nel futuro) rifiutate.
- Ogni richiesta (`requestId`) vale una volta sola; se ne ricordano al massimo 500.
- A voce solo Smarther acceso/spento; pompa di calore in sola lettura.
- Limiti del pannello: 60 richieste al minuto per indirizzo IP, corpo massimo 64 KB.

**Plugin**
- Stato visibile in Impostazioni → Integrazioni, con indirizzo pubblico da copiare, contatore e ultima richiesta.
- Si ricarica senza riavviare il pannello («Ricarica i plugin»).

## Cose note, non risolte
- **Alexa+**: con Alexa+ attiva, «apri / avvia / chiedi a *nome*» viene interpretato come comando di Casa intelligente («non riesco ad accedere alla skill SmartHomeDashboard-prod») e i nomi vengono associati per significato a skill e dispositivi simili. Per ora la skill funziona uscendo da Alexa+ («Alexa, esci da Alexa+»). Da provare: Routine con Alexa+ attiva.
- La skill è in modalità **Sviluppo**: il test deve restare su *Development* nella scheda Test della console.
