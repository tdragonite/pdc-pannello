# PIANO E STATO DEL PROGETTO – pannello pdc (leggere per primo se si riparte)

Obiettivo: clone locale di Dwellsee, in italiano, tema scuro, ottimizzato per iPhone, per la pdc Samsung EHS Mono (Gen 7, 8 kW, R32) di Alessandro. Un solo file `pdc_pannello.py` (solo libreria standard, Windows, porta 7000), tutto in `config.json`, dati in SQLite (`dati/pdc.db`, dalla 0.16 in sottocartelle: vedi 0.16).

## Decisioni già prese (dall'utente)
- Lettura, piu' (dalla 0.11) tre soli comandi on/off: riscaldamento, acqua calda, Smarther (vedi sotto). Nessuna impostazione della pdc viene modificata; niente resistenza elettrica (l'impianto non ce l'ha: nessuna riga e nessun allarme dedicati dalla 0.17). Login sempre (anche in LAN), sessione con cookie, blocco dopo tentativi sbagliati.
- Tempo reale con SSE. Sezioni: Dashboard, Analisi (6h/24h/personalizzato), Efficienza (3g/7g/28g). Curva climatica come Dwellsee (con punti reali sopra).
- Telegram: riusa le credenziali del suo bot (stanno in un .json). Anomalie: errori script, API SmartThings/Netatmo giù, nessun dato, token in scadenza/scaduto, pdc ferma con richiesta calore, casa fredda, cicli corti, portata bassa, mandata lontana dal setpoint, ACS sotto soglia, COP anomalo, riepilogo mattutino opzionale.
- Tunnel Cloudflare: assistente via API (token API Cloudflare incollato nella pagina Impostazioni), scarica `cloudflared.exe`.
- Auth SmartThings: PAT oggi, OAuth auspicato; il pannello avrà una pagina dove incollare un PAT nuovo + avviso Telegram prima della scadenza. Possibile piano a pagamento SmartThings dopo ottobre 2026 (non confermato).
- README a prova di principiante.
- **Lavorare a pezzi** (utente su piano free con risorse limitate): ogni pezzo consegnato come file con SendUserFile e testato.

## Fatti sui dati reali (dai dump dell'utente, 01/11/2026)
SmartThings, device id nella config. Due componenti: `main` (ACS/sistema) e `INDOOR` (circuito riscaldamento).
- `main.temperatureMeasurement.temperature` = temperatura bollitore ACS (47,1). `main.thermostatCoolingSetpoint` = setpoint ACS (50).
- `INDOOR.temperatureMeasurement.temperature` = temperatura acqua lato impianto (26 / 27,5), riferimento "water".
- `INDOOR.thermostatCoolingSetpoint` = 25 (corrisponde al "Target 25.0" di Dwellsee).
- `samsungce.ehsDiverterValve.position` = "room" | (probabile "tank") → riscaldamento casa o ACS.
- `samsungce.ehsBoosterHeater.status`, `INDOOR.samsungce.ehsDefrostMode.status`, `switch.switch` (main e INDOOR), `airConditionerMode` (main: eco/std/power/force; INDOOR: heat).
- `powerConsumptionReport.powerConsumption`: `power` in **kW** (0,206 = 206 W), `deltaEnergy` in **Wh** (60 Wh in 16 min, coerente), `energy` = Wh cumulativi (4.930.238 Wh), finestra start/end 16 min.
- FSV (`samsungce.ehsFsvSettings`): curva climatica 2011=-5 °C → 2021=37 °C; 2012=19 °C → 2022=26 °C. Zona 2: 2031=50, 2032=35 (non attiva). Altri id elencati nel dump.
- `samsungce.ehsThermostat.connectionState`: main = "disconnected", INDOOR = "connected" (normale).
- **`samsungce.ehsCycleData`** ha `outdoor` (28 byte) e `indoor` (31 byte): liste di 3 campioni, uno ogni 5 minuti, ciascuno con timestamp e stringa esadecimale. Contiene i valori che Dwellsee mostra (mandata, ritorno, esterna, portata, frequenza compressore, velocità pompa). Un thread OpenEnergyMonitor dice che si aggiorna ogni ~5 min **solo quando il sistema è in funzione**. Quindi: interrogare ogni 60 s va bene, ma **deduplicare per timestamp** e conservare tutti i campioni.

### Decodifica ehsCycleData – RISOLTA (guida pubblica Dwellsee + verifica sui campioni reali)
Fonte: www.dwellsee.com/knowledge-base/decoding-guide/ (URL con www; senza www non era raggiungibile).
Il layout si sceglie dalla LUNGHEZZA del blob (non dal binaryId: la pdc dichiara SAC_EHS_MONO ma manda indoor 31 B e outdoor 28 B). Temperature = byte − 55 °C; 0x05 e 0xFF = dato nullo.
- outdoor_28: byte 0-1 Hz compressore (u16 BE), 2-3 Hz target, 5 gas di scarico, 6 evaporazione, 7 aspirazione, 8 temperatura esterna (validata r=0,865 su stazione meteo).
- indoor_31: byte 2 ritorno, 3 mandata, 7 portata (/10 → L/min), 8 velocità pompa %, 27 temperatura bollitore.
- Riscontri sui campioni dell'utente (pdc ferma): esterna 7 °C (Dwellsee min 7), byte 27 → 48 °C vs bollitore reale 47,1–47,6, mandata 27–28 vs INDOOR.temperature 27,5, ritorno 13→11 che scende verso l'esterna a pompa ferma, Hz e pompa = 0.
- Le ipotesi del primo giro (offset −40, s16 ai byte 18–21) erano sbagliate: scartate.
- Byte non documentati: outdoor 4, 9–19, 22–27; indoor 0–1, 4–6, 9–26, 28–30. A pdc spenta/ferma i dati ciclo non si aggiornano: la portata va considerata 0 e l'esterna "vecchia".
- Il codice sceglie `mappa_ciclo` in config.json (campi attivabili/disattivabili) e `ridecodifica` rifà i dati salvati.

Netatmo (Smarther BNS, 1 stanza "Entrata", scope funzionanti): `homestatus.rooms[0]`: `therm_measured_temperature` (21,2), `therm_setpoint_temperature` (21), `humidity` (57), `heating_power_request` (0–100), `therm_setpoint_mode` ("home"), `open_window`, `reachable`; `modules[0]` (BNS): `boiler_status` (true/false = richiesta calore), `wifi_strength`. `homesdata` ha zone/programmi (Comfort +, Notte, Comfort, Economico). Rinnovo token: POST https://api.netatmo.com/oauth2/token, il refresh token ruota.

## Pezzi (tutti consegnati: storico, il lavoro successivo e' nelle Versioni)
- [x] **1 – Nucleo (CONSEGNATO: /home/claude/pdc-panel/pdc_pannello.py, 16 test ok)**: `pdc_pannello.py` con config.json, SQLite, collector SmartThings (PAT/OAuth) + Netatmo, decoder cycleData con mappa configurabile, `--cattura`, `--demo`, test. Niente interfaccia.
- [x] **2 – Web base**: server HTTP, login/sessione, API storico + SSE tempo reale, pagina Dashboard (card, barra modi, curva climatica), responsive iPhone.
- [x] **3 – Analisi + Efficienza**: grafici, intervalli 6h/24h/custom e 3g/7g/28g, stima COP, energia.
- [x] **4 – Telegram**: notifiche e regole di anomalia con anti-spam.
- [x] **5 – Cloudflare**: assistente tunnel nella pagina Impostazioni.
- [x] **6 – Rifinitura + README completo**.

Cartelle: strumenti già consegnati in `/home/claude/pdc-tools/` (pdc_strumenti.py, README_STRUMENTI.md). Il codice vero è in `/home/claude/pdc-panel/pdc_pannello.py` (cresce a ogni pezzo). La bozza v1 in `/home/claude/pdc-dashboard/` va scartata.

## Stato del codice (pezzo 1)
Serie nel DB (tabella `misure`, ts in epoch UTC): acs.temperatura/setpoint, risc.acqua/setpoint, pot.watt (kW×1000), en.totale_wh/delta_wh/finestra_s, ciclo.{freq_compressore,freq_target,scarico,evaporazione,aspirazione,esterna,ritorno,mandata,portata,vel_pompa,acs_ciclo}, casa.{temperatura,umidita,setpoint,richiesta_pct,richiesta_calore,wifi}. Stati testuali con storico dei cambi (tabella `stati`): pdc.acceso, risc.acceso, pdc.modo, risc.modo, pdc.valvola (room|tank), pdc.sbrinamento, pdc.resistenza, pdc.salute, casa.modo, fsv.NNNN. `ultimo` = ultimo valore di ogni serie. `ciclo` = blob grezzi (dedup per timestamp). `eventi` = anomalie aperte/chiuse; `Eventi.ascoltatori` = punto d'aggancio per Telegram e pagina web; `App.bus` = punto d'aggancio per il tempo reale (SSE).
Codici evento: st_conn_{rete,auth,pagamento,limite,server,richiesta}, nt_conn_*, st_config/nt_config, st_script/nt_script, st_pat (PAT >22 h), st_pdc_offline, st_ciclo_formato.
Da fare nei pezzi successivi: demo (dati finti per provare la UI), compattazione storico, web, login, Telegram, Cloudflare.


## Stato dopo pezzo 2
- Fatto: web server (porta 7000), login/sessioni/lockout, SSE realtime, Dashboard con curva climatica, `--demo`, `imposta-password`. Build: `python costruisci.py` unisce sorgenti/*.py + sorgenti/web/* in pdc_pannello.py.
- Analisi/Efficienza sono segnaposto in app.js. Nuovi endpoint: usare `@api("/api/...")` in 2_web.py.
- URL guida Dwellsee: usare solo www.dwellsee.com/knowledge-base/decoding-guide/
- Prossimo: pezzo 3 (Analisi + Efficienza).

## Stato dopo pezzo 3
- Fatto: pagine Analisi (grafici sincronizzati con cursore, fasce casa/acqua, 6h/24h/custom) ed Efficienza (3g/7g/28g: COP stimato, energia al giorno, COP per temperatura esterna, cicli). API: /api/analisi, /api/efficienza (in 2_web.py). Il calore e' una STIMA (portata x salto x 0,0698); COP escluso lo standby.
- Prossimo: pezzo 4 (Telegram).

## Stato dopo pezzo 4
- Fatto: Telegram (3_telegram.py): credenziali da config.json o da file .json dell'utente (file_credenziali + chiave_token/chiave_chat, anche annidate 'a.b'), messaggi personalizzabili (telegram.messaggi, per_codice), limite orario, silenzio notturno, promemoria, riepilogo mattina, comando `telegram-prova`. Sentinella (regole in config.json -> allarmi): nessun_dato, pdc_ferma_con_richiesta, casa_fredda, cicli_brevi, portata_bassa, mandata_lontana, acs_bassa, cop_basso, sbrinamenti_frequenti (resistenza_accesa c era all inizio, rimosso nella 0.17). Tutti gli eventi (anche collegamento/token/script) vanno su Telegram; si spengono con allarmi.<codice>.attivo=false (anche con * es. st_conn_*). `Config.completa()` aggiunge al file le chiavi nuove all'avvio senza toccare i valori dell'utente.
- cicli_compressore(): cicli letti dai campioni (non dalla griglia 5 min), usati anche in Efficienza.
- DA FARE nel pezzo 5 (richiesta utente): la riga 'Accesso SmartThings' (OAuth/PAT) va tolta dalla Dashboard e messa SOLO nella pagina Impostazioni.
- Attenzione test: config.json/db/log stanno nella cartella dello SCRIPT (non nel cwd): non lanciare lo script reale dalla cartella di lavoro.
- Prossimo: pezzo 5 (Impostazioni + tunnel Cloudflare).

## Stato dopo pezzo 5
- Fatto: pagina Impostazioni (4_impostazioni.py + app.js): stato collegamenti (SmartThings OAuth/PAT, Netatmo, Telegram, database, versione), modifica di config.json dal pannello con validazione (schema_impostazioni), segreti write-only, sezioni Telegram/orari/testi/collegamenti/allarmi, messaggio di prova, cambio password (chiude le altre sessioni). POST protetti da header X-Pdc + controllo Origin (`@api_post`). Riga OAuth tolta dalla Dashboard.
- La card 'Accesso da internet (Cloudflare)' e' un segnaposto.
- Prossimo: pezzo 6 = procedura guidata Cloudflare (token API, tunnel con nome, DNS, download cloudflared.exe, avvio/stop come processo figlio), poi pezzo 7 = README a prova di scemo + compattazione storico + rifinitura.

## Stato dopo pezzo 6
- Fatto: procedura guidata Cloudflare (5_cloudflare.py + card in Impostazioni): token API usato una volta e NON conservato; crea tunnel (config_src cloudflare), ingress -> http://127.0.0.1:<porta>, CNAME proxied verso <id>.cfargotunnel.com, rollback se fallisce, rifiuta record DNS/tunnel omonimi esistenti; scarica cloudflared da GitHub (win amd64/linux), lo avvia come processo figlio senza finestra con TUNNEL_TOKEN in ambiente, guardia con riavvio e backoff, evento cf_tunnel, pid file per pulire orfani, log cloudflared.log. Config: sezione `cloudflare` (attivo, avvio_automatico, protocollo auto|http2|quic, percorso_cloudflared). Testato solo con finto Cloudflare e finto cloudflared (Linux): NON provato su Windows ne' con Cloudflare vero.
- Permessi token usati nella guida: Account>Cloudflare Tunnel>Edit, Zone>DNS>Edit, Zone>Zone>Read.
- Prossimo: pezzo 7 = README a prova di scemo (setup SmartThings OAuth, Netatmo, Telegram, Cloudflare, avvio da PowerShell/pannello script, risoluzione problemi), compattazione storico (dati completi 12 mesi poi medie a 15 min), rifinitura generale; poi correzioni dopo i test dell'utente sui dati veri.

## Stato dopo pezzo 7 (progetto COMPLETO nelle funzioni previste)
- Fatto: 6_manutenzione.py (compattazione storico: ultimi 365 gg dettagliati, poi medie a 15 min; contatore en.totale_wh = ultimo valore; blob ciclo vecchi eliminati; thread notturno + comando `compatta`; sezione `conservazione` in config e in Impostazioni) e README.md completo (setup SmartThings PAT/OAuth, Netatmo, password, avvio con pythonw, Telegram, Cloudflare, iPhone, comandi, config, manutenzione, troubleshooting, sicurezza).
- Stato file: pdc_pannello.py 0.7 e' il file unico; si ricostruisce con `python costruisci.py` da sorgenti/ (1_nucleo, 2_web, 3_telegram, 4_impostazioni, 5_cloudflare, 6_manutenzione, 9_comandi + web/*).
- Da fare solo su richiesta: correzioni dopo i test sui dati veri (soglie allarmi, COP, decodifica ciclo), eventuali aggiunte richieste dall'utente.
- Limiti noti: Cloudflare/cloudflared non provati su Windows dal sistema di sviluppo (utente dice che funziona); test di crash-restart del tunnel flaky nel test ma il riavvio funziona; nessun test di carico con molti mesi di dati reali.

## Versioni
- Numerazione dalla 0.8: 0.8 = base dopo i test; 0.8.1, 0.8.2... = correzioni; 0.9 = nuove funzioni; 1.0 = prossima versione maggiore.
- La versione sta in `VERSIONE` (sorgenti/1_nucleo.py). Ricordati di cambiarla a ogni consegna.
- 0.8: separatori nello Stato; badge di stato a colori pieni (verde acceso, arancio richiesta calore, giallo sbrinamento, blu fermo/attesa, rosso spento) con suggerimento al passaggio; testi dei badge invariati rispetto alla 0.7.
- 0.8.1: verde `--ok` più chiaro (#62eba6); le etichette `.pill.si` sono ora piene con testo scuro (leggibili); stesso verde nel badge 'acceso'.
- 0.8.1 (bug): la regola `.riga span:first-child` colorava di grigio le etichette (pill) prime nella riga; ora `.riga>span:first-child`, quindi 'Problema', 'da configurare' ecc. tornano rossi e 'OK' verde.
- 0.8.2: le etichette di stato nelle schede vanno a capo sotto il titolo a qualsiasi larghezza (prima solo sotto 720px: su Safari desktop con 5 schede strette sbordavano).
- 0.8.3: nelle schede della dashboard l'etichetta di stato sta SEMPRE sotto il titolo (classe ch-st; spazio riservato se vuota). 'Spento'/'Problema' sono rossi pieni ovunque (.pill.off/.bad = stesso rosso del badge), 'IN USO'/'IN CORSO' (resistenza, sbrinamento) gialli come il badge sbrinamento.
- 0.8.4 (bug): l'ultimo intervallo di 5 min (senza ancora il campione del compressore, che SmartThings consegna in ritardo) veniva classificato 'riposo' => Acqua calda 'Standby' durante un ciclo ACS. Ora gli ultimi 15 min senza campione ereditano lo stato dell'ultimo campione noto (calcola_modi); modo_corrente guarda 20 min indietro.
- 0.9: pagina **Dispositivi** (sorgenti/7_dispositivi.py): schede PdC (da SmartThings: ocf, deviceIdentification, softwareVersion, driver, softwareUpdate; una volta l'ora GET /devices/{id} per data di registrazione) e Smarther (Netatmo homestatus + homesdata orario: firmware_revision, id modulo = MAC, wifi_strength, setup_date); serie `info.pdc.*` e `info.nt.*` nella tabella `stati` (lo storico delle versioni e' la sua cronologia dei cambi); IP/MAC opzionali da config (`dispositivi.ip_*`, MAC via ARP, cache 10 min); modello Smarther dal catalogo BTicino (menu); loghi dalla cartella `loghi/` accanto allo script (NON forniti: i marchi sono di terzi), route `/loghi/<nome>` con whitelist di nomi/estensioni e CSP sandbox per gli SVG; badge Online/Offline in 'Stato del sistema' (+ riga Smarther); campo di tipo `scelta` nelle Impostazioni. Test: /tmp/t/d9 (t1 dati, t2 web/sicurezza, t3 screenshot).

## 0.9.1
- Stato "In funzione/A riposo": se il campione del compressore e' in ritardo (SmartThings), si tiene l'ultimo campione vero fino a 30 min (prima 15, e partiva da celle gia' dedotte). Se il compressore e' davvero fermo (0 Hz) torna subito a riposo.

## 0.9.2
- Logo Smarther nel titolo della card Casa: altezza 17px -> 12px, allineato al testo.

## 0.9.3
- Dispositivi: tolti i loghi piccoli bticino/netatmo sotto il titolo; il logo BTicino sta nella riga "Produttore" (con la nota "con Netatmo"). Logo Smarther a 26px.
- Cache: indirizzi di stile/script con impronta del contenuto (app.js?v=...) e ricarica automatica delle pagine rimaste aperte quando lo script viene aggiornato (intestazione X-Pdc-Build).

## 0.10
- Nuova scheda "Installatore" dentro Dispositivi (#/dispositivi/installatore): impostazioni FSV lette dalla pompa, in sola lettura, per menu, con nome italiano, intervallo del manuale, "fuori limiti", ultimo cambio e Copia elenco. Tolto il badge "modificato" (rumore: la curva climatica e' sempre regolata dall'installatore). Nuove sotto-pagine: percorso #/pagina/sotto.
- Etichette dei codici scritte da me; significati dal manuale EHS Mono (guida FSV di Dwellsee). Default indicativi (variano per modello).
- Installatore: etichette con "temperatura" esplicita; significati dei valori (2091/2092/2093, 3011, 4011, 4021, 4022, 4041, 4051, 3071) verificati sul manuale Samsung del telecomando cablato MWR-WW10N (modalita' Field Setting).

## 0.11
- Comandi: interruttori accendi/spegni per Riscaldamento casa (SmartThings, componente INDOOR), Acqua calda (componente main) e Termostato Smarther (Netatmo: setroomthermpoint off/home, ripiego setthermmode hg/schedule) nella card "Stato del sistema". Lucchetto da sbloccare (30 s), conferma allo spegnimento, nessuno stato "in attesa" (l'interruttore resta dove lo metti, il pannello rilegge dopo 4 e 20 s). Registro in Dispositivi (non Telegram). Niente interruttore generale, accessibile anche da fuori.
- Richiede token con permesso di comando: SmartThings x:devices:*, Netatmo write_smarther. pdc_strumenti.py st-oauth ora chiede entrambi gli scope di SmartThings.


## 0.11.1 – 0.11.2 (correzioni dopo le prove sui dispositivi veri, 02/11/2026; la 0.11.1 era il primo tentativo sullo Smarther, la 0.11.2 contiene tutto il resto)
- **Modo casa/acs con compressore in ritardo**: dal dump reale, i campioni ciclo (ogni 5 min) arrivano con 1-2 min di ritardo e il compressore a 0 Hz del campione vecchio faceva dire "Standby" mentre la pompa partiva (valvola passata a "tank" alle 21:43, Hz=50 solo nel campione delle 21:47). Ora, se i campioni dicono riposo ma `risc.acqua` (INDOOR, in tempo reale) sale di >=3 C in 15 min, il modo e' quello della valvola (room=casa, tank=acs). Tenuta dell'ultimo stato noto ridotta da 30 a 10 min. Tolte le regole resistenza/potenza (la pompa non ha resistenza sull'impianto; non c'e' modo di saperlo dall'API).
- Scheda "Compressore e circuito": "dato di N min fa".
- **Smarther: due modi di essere spento** (dump reale): dal pannello casa `therm_mode=hg` + stanza `home` a 7 C; dall'app stanza `therm_setpoint_mode=hg` + casa `schedule`. Lettura: spento se stanza off/hg O casa hg. `homesdata` riletto ogni 2 min (prima 1 h) e subito dopo ogni comando. NON si usa piu' il setpoint <= 9 (insensato per un manuale basso).
- **Comandi Smarther**: `setstate` con `therm_mode` nella casa NON esiste (errore 21 "should NOT have additional properties"). `setroomthermpoint` accetta solo manual/max/home. Strada giusta: spegni = `setthermmode hg`; accendi = `setthermmode schedule` + `setstate` stanza `therm_setpoint_mode: home`. Ogni tentativo e' seguito da una verifica (homesdata+homestatus dopo 3 s): una risposta "ok" non basta (setstate stanza rispondeva ok senza fare nulla). Il registro riporta strada riuscita e scartate.
- UI comandi: badge giallo ATTENDI... durante il comando (richiesta dell'utente, rispetto alla scelta iniziale di non avere uno stato di attesa); lo sblocco da 30 s riparte a ogni azione; registro a pagine da 5 (fino a 100 comandi in memoria).
- Costo energia: `tariffa.prezzo_kwh` (Impostazioni); costo nell'energia della Dashboard, costo stimato nell'Efficienza e costo medio di un ciclo acqua calda.
- Esporta CSV (`/api/esporta.csv?tipo=giorni|misure`), formato per Excel italiano.
- Backup notturno (`backup.*`, sorgenti/6_manutenzione.py: `fai_backup`, API di backup di sqlite3) e comando `backup`; rotazione delle ultime N copie.
- Idee scartate dall'utente: modalita'/temperatura ACS dal pannello, Smarther manuale/assente, nuovi allarmi Telegram. Badge Energia: resta "Consumo".
- Test: /tmp/t/d9 (t9 UI comandi, t10 comandi con rete simulata), /tmp/t/tg (regressione 4,5,7,9).
- Changelog: pagina #/changelog (fuori dalla barra del telefono: voce piccola sopra 'Esci' sul desktop, link in Impostazioni > Stato). I testi stanno in `CHANGELOG` in sorgenti/web/app.js: **aggiungere una voce a ogni consegna** (la piu' recente in cima, italiano semplice). Prima della 0.9 e' ricostruita dai 'pezzi' 1-7 e dalle note 0.8.x del piano, senza date.
- Diagnostica ritardo ciclo: `ServizioST._misura_ritardo` salva `diag.ritardo_ciclo` (s tra l'ora del campione e quella di arrivo, solo per campioni nuovi, meta `ciclo_ultimo_ts`); la Dashboard mostra tipico/massimo 24 h (`ritardo_ciclo` in /api/dashboard, serve >= 5 misure). Motivo: 'dato di 17 min fa' a pompa ferma, che dopo il riavvio del pannello risultava subito 'aggiornato' => sospetto che /status restituisca dati vecchi per un po' (cache SmartThings?) o che il pannello perda campioni. Da verificare con i numeri veri; se il massimo e' alto valutare il comando `refresh` della capability.


## 0.12 (03/11/2026)
- **Confronta** (Efficienza): `/api/efficienza?confronta=1` aggiunge `prec` (stessa durata, finestra da-durata..da: tot, copertura, cicli, bollitore, nodati_min, dati_da, completo). Pulsante `.conf-btn`, stato `S.confronto` non salvato (spento a ogni apertura).
- **Salute del bollitore**: `calcola_bollitore` in 2_web.py. Perdita notturna = °C/h nella finestra 23-06 piu' lunga (>=3 h) senza cicli ACS, W = °C/h * litri * 1.163; salita = mediana °C/h dai cicli ACS e minuti per 10 C. None se <2 notti/cicli. Config `bollitore.litri`.
- **Registro** (Impostazioni, tab `#/impostazioni/registro`): `/api/registro` (4_impostazioni.py) con ultime righe del log ripulite da segreti (`pulisci_riga`), pesi di db/log, stato backup.
- **ripristina** (`cmd_ripristina`, 6_manutenzione.py): richiede il blocco istanza (rifiuta se il pannello e' acceso); copie validate (integrity_check + tabelle) aprendole `mode=ro&immutable=1`; copia di sicurezza del db attuale con l'API backup di sqlite3 (include il -wal), poi cancella -wal/-shm e sostituisce con `os.replace`; config.json opzionale (default no) con copia di sicurezza.
- Versione 0.12 scelta perche' ci sono funzioni nuove; l'utente puo' cambiarla.

## 0.12.1 (03/11/2026, robustezza)
- Audit con pyflakes/bandit (solo falsi positivi) e misure su db da 2 M righe: pagine <0,3 s, niente da ottimizzare. Gzip non aggiunto (lo fa Cloudflare).
- Sicurezza: `riservato()` chmod 600 su config.json (e tmp, copie di backup); Content-Length negativo respinto (login e API); controllo Origin nel login; HSTS se https; `Diffusione.MAX_FLUSSI=20` (503); BUILD con sha256.
- Stabilita': `Raccoglitore.run` non muore piu' se perfino `eventi.apri` fallisce (disco pieno); stessa protezione per `Sentinella` e `_guardia` del tunnel (supervisore + `_guardia_interna`); `ErroreDB` con messaggio che rimanda a `ripristina`, `App(tollera_db=True)` per il comando ripristina; `ripristina` copia il db guasto com'e' se l'API di backup fallisce; `Config._scrivi` con fsync.
- SSE: `collega()` riapre il canale (Safari non riprova da solo); il server manda `event: ping` ogni 15 s, il client riapre dopo 45 s di silenzio, al ritorno sulla scheda, su pageshow/online.
- Non provato: database bloccato da un altro processo per >30 s.

## 0.13 (03/11/2026, Impostazioni in tab)
- Tab: Stato, Collegamenti (collegamenti + dispositivi), Avvisi (telegram, orari, testi, soglie allarmi), Accesso (password + Cloudflare), Dati (tariffa, bollitore, backup, storico), Registro. Sottopagine via hash `#/impostazioni/<tab>`; il server assegna `tab` a ogni sezione (`TAB_SEZIONI` in 4_impostazioni.py), nuove sezioni `bollitore.litri` e `backup.attivo/copie`.
- Riquadri = `<details class="card imp riq" data-id>`, stato aperto/chiuso in localStorage (`pdc_imp_aperti`); di default chiusi, tranne il primo di ogni tab. Una sola barra Salva per tutti i tab; `mostraCampo` apre i riquadri e cambia tab per un errore di validazione.
- Cloudflare: `POST /api/cloudflare/telegram` + pulsante (il bot Telegram resta solo in invio, niente comandi in arrivo).
- Avvio: `indirizzi()` mostra solo rete di casa e, se configurato, l'indirizzo Cloudflare. UI: `daQuanto()` per «acceso da».

## 0.13.1 (03/11/2026, rifiniture)
- Dispositivi su telefono (<=560 px): `.dev-head` a griglia (logo | stato sulla stessa riga, titolo/sottotitolo sotto), logo in casella fissa 28 px di altezza con object-fit (un SVG senza dimensioni intrinseche si sovrapponeva al testo con `height:auto`); `Wi-Fi` con trattino non separabile nelle etichette.
- Analisi: gruppi `casa` = «Temperatura ambiente · Smarther», `umid` = «Umidità · Smarther». Dashboard: la scheda resta «Casa · logo Smarther» (il titolo lungo mandava a capo il logo).

## 0.14 (03/11/2026, umidita')
- Config `umidita` {giallo 60, rosso 70, minuti 30, rientro_punti 2}: unica fonte per Telegram, Dashboard (`#c-um.um-giallo/.um-rosso`) e grafico Analisi (linee tratteggiate, `soglie_umidita` in /api/dashboard casa e /api/analisi, `soglie_umidita(app)` in 2_web.py). Impostazioni: riquadro «Umidità in casa» nel tab Avvisi, con controllo rosso > giallo in `salva_impostazioni`.
- Allarmi `umidita_alta` (avviso) e `umidita_molto_alta` (errore) in `Sentinella.valuta`, solo `attivo` e ritardo Telegram nella loro scheda; isteresi: restano aperti finche' umidita' > soglia - rientro_punti, poi i soliti 10 min di calma. Dato: `casa.umidita` (max 30 min di eta').
- Le linee nel grafico si disegnano solo se i dati arrivano a meno di 10 punti dalla soglia (per non schiacciare la scala).

## 0.14.1 (03/11/2026, riepilogo con la notte)
- `Sentinella._finestra_notte/_notte_righe`: nel riepilogo (solo li', niente messaggio a parte) la sezione «Di notte» con: temperatura casa (min/max se escursione >= `telegram.silenzio_notturno.oscillazione_gradi`, 2 C), umidita' massima se sopra la soglia gialla, eventi aperti nella finestra (anche gia' chiusi). Finestra = ultimo silenzio notturno finito; se il riepilogo e' prima della fine del silenzio, la notte in corso fino a ora. Con silenzio notturno spento non c'e' sezione.
- Nota: i messaggi di notte arrivano comunque, senza suoneria (`disable_notification`); gli errori gravi suonano a meno di `anche_errori`.

## 0.14.2 (03/11/2026, grafici)
- Potenza (Analisi, <= 3 giorni): `q_potenza_a_gradini` usa `en.finestra_s` per disegnare ogni rapporto come tratto [fine - finestra, fine] a valore costante (continuo a gradini); senza finestra resta il punto. Causa dello spezzettamento: un punto ogni ~15 min con tolleranza `buco` di ~1000 s, quindi la linea si interrompeva fra quasi tutti i punti (per 'pot' ora 3600 s).
- Puntini al passaggio del mouse: `.cur-d` per linea, `valoreInterp` (interpolazione fra punti vicini, nessun puntino nei buchi), scala letta da `data-lo/hi/t0/ph/buco` dell'SVG.
- Riepilogo «Modo di funzionamento»: «casa» -> «riscaldamento» (la legenda resta «Riscaldamento casa»).

## 0.15 (03/11/2026, grafico del circuito)
- `GRUPPI_ANALISI`: i gruppi `comp`, `portata`, `pompa` sono sostituiti da `circuito` ("Compressore, portata e pompa"). Le serie possono avere 3 campi extra `(unita, asse, area)`; `/api/analisi` li espone per linea (`u`, `asse`, `area`) e per gruppo (`gradini`).
- `graficoLineare`: se una linea ha `asse: 1` il grafico ha due scale (destra con numeri colorati come la linea, senza griglia). `percorsoGradini`/`segmentiGradini`: gradini centrati a meta' strada fra due punti, pezzi staccati dove il buco supera `buco`; l'area (compressore) e' chiusa sulla linea dello zero.
- Puntini al passaggio del mouse: per i gradini `valoreGradino` (valore del punto piu' vicino, se non c'e' un buco), `data-mezzo` sull'svg; ogni puntino ha `data-a` per scegliere la scala. La legenda usa l'unita' di ogni linea (`l.u`).
- Il target mandata resta com'e' (linea morbida), da rivedere.
- Dashboard, scheda "Compressore e circuito": con compressore a 0 Hz mostra lo stato "Fermo" e tre riquadri (`.fermo-w`: per cosa, durata, energia) dell'ultimo ciclo concluso; l'API espone `ultimo_ciclo` {inizio, fine, modo, kwh} (da `cicli_compressore`, ultimi 3 giorni; kWh dal contatore `en.totale_wh`, omessi se non leggibili). Le tessere e la nota sul ritardo restano solo con compressore attivo; l'etichetta "dato di N min fa" a compressore fermo compare solo oltre 20 min.
- La tessera "Gas di scarico" si mostra solo se `prestazioni.scarico` ha un valore (sulla pdc dell'utente e' sempre assente).
- Grafico "Temperatura ambiente · Smarther": `fasceRichiesta` fonde i campioni consecutivi in blocchi; oltre alla tinta leggera c'e' una striscia piena (7 px) sul fondo del riquadro. La legenda ha `richiestaLegenda` ("Richiesta caldo si/no" all'ora del cursore, o ultimo valore).
- Nota sul ritardo (`#prest-rit`): ora HTML con `.nw` (nowrap) su ogni gruppo, titolo su una riga a parte.

## 0.16 (03/11/2026, cartelle)
- Nuova disposizione: radice = `pdc_pannello.py`, `pdc_strumenti.py`, `config.json`; `dati/` (pdc.db, backup/, loghi/, demo.db; i file da mandare in chat stanno in `diagnostica/`, vedi 0.17), `log/` (pdc_pannello.log, cloudflared.log), `cloudflared/` (exe + cloudflared.pid). Default: `generale.database = "dati/pdc.db"`, `backup.cartella = "dati/backup"`.
- `riordina_cartelle(base, cfg)` (1_nucleo.py), chiamata da `main()` prima di `imposta_log` (non in demo): sposta con `os.replace` (stesso disco = rinomina), mai sovrascrive; `_sposta` per cartelle unisce solo i file mancanti. Il database (+ -wal/-shm) e' tutto o niente con rollback. Aggiorna config.json solo se i valori sono i vecchi predefiniti (`pdc.db`, `backup`). Prima di spostare prova a prendere `porta_lock`: se occupata esce con codice 1. Se esistono sia `pdc.db` sia `dati/pdc.db` esce con codice 1 senza toccare nulla. Un file che non si lascia spostare (es. log aperto da un cloudflared rimasto vivo, su Windows) resta dov'era e viene segnalato nel log.
- `cartelle_loghi(base)`: `dati/loghi` poi la vecchia `loghi` (stessa logica in `elenco_loghi` e `_logo`). `cartella_log(base)` crea `log/`.
- Provato (`/tmp/t/mig/mig.py`, 21 controlli): layout vecchio completo con WAL aperto, copia accesa, due database, percorso personalizzato, installazione nuova, idempotenza, `backup` e `ripristina` nei nuovi percorsi.
- Correzione (stessa 0.16): `_pulisci_vecchio` (tunnel) leggeva l'output di `tasklist` come UTF-8; su Windows e' nella tabella OEM e un byte 0x85 faceva fallire la lettura (`stdout` = None, `AttributeError`) e il pannello non partiva. Non emergeva prima perche' il file `cloudflared.pid` veniva sempre rimosso a pannello chiuso; la migrazione l'ha spostato e il controllo e' partito. Ora si legge in byte, ogni errore di questo controllo e' solo un avviso nel registro, e anche `cloudflared --version` e `arp` leggono con `errors="replace"`.

## 0.17 (03/11/2026, aggiornamento a caldo)
- `costruisci.py` mette un segnalibro `#@@PARTE nome@@` davanti a ogni parte tranne la prima (nucleo = intestazione + 1_nucleo). Nuovo `9_aggiorna.py` (prima di `9_comandi.py`): `_leggi_parti` divide il file, `_impronta` fa lo sha256 di ogni parte (nel nucleo la riga `VERSIONE = "..."` e' normalizzata, cosi' cambiare solo versione non richiede riavvio), `PARTI_CARICATE` = com'era il file all'avvio.
- Ricaricabili a caldo (`RICARICABILI`): web, impostazioni, dispositivi, controlli. Il resto (nucleo, telegram, cloudflare, manutenzione, aggiorna, comandi) e' "fisso": se cambia, `applica_aggiornamento` rifiuta e dice di riavviare.
- `applica_aggiornamento(app)`: 1) rilegge il file; 2) rifiuta se e' cambiata una parte fissa; 3) compila ogni parte cambiata (riempita di righe vuote davanti, cosi' i numeri di riga dei messaggi coincidono col file) e se non compila non tocca nulla; 4) imposta `VERSIONE`, esegue le parti con `exec` nei globali del modulo, con istantanea dei globali per tornare indietro se un'esecuzione lancia un'eccezione; 5) ricalcola `BUILD` (le pagine aperte si ricaricano da sole) e mette `app.server_web.RequestHandlerClass = Gestore`: il server non si ferma, ne' si riapre la porta, ne' si chiudono gli SSE.
- `API` / `API_POST` ora sono `globals().get("API") or {}`: lo stesso dizionario sopravvive alla ricarica (nessuna finestra con rotte vuote) e le rotte delle parti fisse (tunnel, aggiornamento) non si perdono. `avvia_web` salva `app.server_web`; il `finally` di `main` chiude quello. Il flusso SSE esce se riceve l'evento "chiudi" (non ancora usato).
- Limiti noti: gli oggetti gia' creati restano col codice vecchio (`Sessioni`, `LimiteLogin`, `Diffusione`, `ServerWeb`: se la loro classe cambia serve il riavvio); lo stato di modulo delle parti ricaricate si azzera (`_cache_*`, `_ultimo_comando`); un thread che sta eseguendo una funzione vecchia la finisce col codice vecchio.
- API: `GET /api/aggiornamento` ({stato: aggiornato|applicabile|riavvio|errore, parti, versione, versione_disco}), `POST /api/aggiornamento/applica`. UI: riquadro «Aggiornamento» in Impostazioni → Stato (controllo ogni 20 s).
- Prova (`/tmp/t/hot/hot.py`, 17 controlli): modifica a caldo con rotta nuova, nuova versione e BUILD cambiato; errore di sintassi (riga giusta, nulla cambia); eccezione a meta' (ripristino dei globali); cambio nel nucleo (rifiutato); solo numero di versione (nessuna parte cambiata); thread di raccolta finto che scrive ogni 50 ms durante tutto il test: buco massimo 60 ms.
- Correzione (stessa 0.16/0.17): i file da mandare in chat hanno una cartella a parte, `diagnostica/`: `esporta-diagnostica` scrive `diagnostica/diagnostica.json` e `pdc_strumenti.py` i `dump_*.json`. `riordina_cartelle` sposta li' quelli gia' creati (`dump_*.json` in radice e `dati/diagnostica.json`).
- Indicatore Dashboard (`modo_istantaneo`, parte web): la regola "l'acqua sta salendo" guarda l'acqua in questo momento (+1,5 gradi in 7 minuti e vicina al massimo), non il minimo degli ultimi 15 minuti. Vedi sotto («Indicatore: causa e correzione») per le regole di stop e i campioni fantasma.

### Indicatore: causa e correzione (03/11/2026, senza cambio di versione)
Dai dati reali (`diagnostica.json` + dump) lo stop del riscaldamento e' stato alle 11:16:48-11:17:54 (`casa.richiesta_calore` 1 -> 0; `risc.acqua` 36,7 -> 27,7), ma l'indicatore e' passato a «A riposo» solo verso le 11:36 (Dwellsee alle 11:33).
- Fatti: i campioni del ciclo (`samsungce.ehsCycleData`) arrivano a blocchi di 3 campioni a 5 minuti l'uno dall'altro, scritti almeno 4 minuti dopo il loro orario; ci sono due fasi di timestamp (:29 e :30). `risc.acqua` (componente INDOOR `temperatureMeasurement`) si aggiorna circa ogni minuto, in tempo reale. Nello stato SmartThings non c'e' nessun attributo di «stato operativo» (`reportStateRealtime` e' `disabled`; `powerConsumptionReport` e' una media su finestra, vecchia di ~14 minuti; la valvola cambia solo posizione).
- Campioni «fantasma»: in 3 giorni, 7 campioni con Hz > 0 la cui `mandata` (tipo indoor) si discosta di piu' di 4-5 gradi dalla `risc.acqua` misurata nello stesso istante (es. 11:17:29: 45 Hz, mandata 38 contro acqua 29,0; 11:22:29: 37 Hz, mandata 40 contro 28,3). Scartarli e' sicuro: la mediana della differenza fra mandata decodificata e acqua misurata sugli altri campioni e' 0.
- `campioni_freq(db, da, a, soglia=5.0)` (2_web.py): `{ts: Hz}` con i fantasma azzerati. Usata da `calcola_modi` (linea del tempo/Dashboard) e dalla regola di stop; NON dai grafici di Analisi, che mostrano i dati grezzi (scelta dell'utente).
- Regola di stop in `modo_istantaneo` (solo se la modalita' corrente e' casa/acs, non ci sono campioni recenti con compressore in marcia, la valvola non e' cambiata negli ultimi 10 minuti): `crollo` = massimo degli ultimi 10 min meno il massimo delle ultime due letture >= 5 gradi, oppure `spento` = valvola `room` + ultimo `casa.richiesta_calore` = 0 + calo >= 2 gradi dal massimo. La vecchia regola (calo di 1 grado) era sbagliata: con la pdc in marcia l'acqua fa un «dente di sega» di 2-3 gradi (37-40) e dopo un cambio di valvola l'acqua crolla di 25 gradi senza che la pdc si fermi.
- Prova (`/tmp/t/gh/`, dati reali): stop visto alle 11:18; nessun falso «riposo» durante il riscaldamento. Nota: la linea del tempo per cella usa solo il primo campione della cella, quindi non era la causa del ritardo mostrato in dashboard; quella era la regola di stop troppo lenta/mancante piu' i fantasma nella finestra `marcia` di 4 minuti.

### Strumenti e esperimenti (03/11/2026)
- `pdc_strumenti.py st-prova-refresh`: test del comando SmartThings `refresh` (capability `refresh`, componente `main`) per anticipare i campioni del ciclo. Fa N giri casuali con/senza refresh, pausa di decantazione fra i giri, riepilogo con verdetto. Primo risultato (8 giri, pdc a riposo): con refresh nuovi campioni in 3 giri su 4 dopo ~10 s, campioni vecchi di 0,4-2,5 minuti (invece di >= 4); senza refresh 0 su 4, ma i giri non erano mescolati (4 refresh poi 4 controlli), quindi da confermare con `--giri 16` a pdc in marcia. Se conferma: mandare `refresh` a ogni lettura (intervallo_pdc_secondi, ora 30 s sul PC dell'utente) o ogni ~60 s solo quando la pdc lavora.
- Rimosso l'allarme `resistenza_accesa` (e la sua voce in Impostazioni): l'impianto non ha la resistenza.

### Da fare (richieste dell'utente, per dopo)
- Tasto «Resetta database» in Impostazioni, con avvisi: proposta = backup automatico prima, conferma in due passaggi (scrivere una parola).
- Eventuale uso del `refresh` nel pannello (vedi sopra), se il test lo conferma.

## Stato dopo pezzo 7 (progetto COMPLETO nelle funzioni previste)
- Fatto: 6_manutenzione.py (compattazione storico: ultimi 365 gg dettagliati, poi medie a 15 min; contatore en.totale_wh = ultimo valore; blob ciclo vecchi eliminati; thread notturno + comando `compatta`; sezione `conservazione` in config e in Impostazioni) e README.md completo (setup SmartThings PAT/OAuth, Netatmo, password, avvio con pythonw, Telegram, Cloudflare, iPhone, comandi, config, manutenzione, troubleshooting, sicurezza).
- Stato file: pdc_pannello.py 0.7 e' il file unico; si ricostruisce con `python costruisci.py` da sorgenti/ (1_nucleo, 2_web, 3_telegram, 4_impostazioni, 5_cloudflare, 6_manutenzione, 9_comandi + web/*).
- Da fare solo su richiesta: correzioni dopo i test sui dati veri (soglie allarmi, COP, decodifica ciclo), eventuali aggiunte richieste dall'utente.
- Limiti noti: Cloudflare/cloudflared non provati su Windows dal sistema di sviluppo (utente dice che funziona); test di crash-restart del tunnel flaky nel test ma il riavvio funziona; nessun test di carico con molti mesi di dati reali.

## Versioni
- Numerazione dalla 0.8: 0.8 = base dopo i test; 0.8.1, 0.8.2... = correzioni; 0.9 = nuove funzioni; 1.0 = prossima versione maggiore.
- La versione sta in `VERSIONE` (sorgenti/1_nucleo.py). Ricordati di cambiarla a ogni consegna.
- 0.8: separatori nello Stato; badge di stato a colori pieni (verde acceso, arancio richiesta calore, giallo sbrinamento, blu fermo/attesa, rosso spento) con suggerimento al passaggio; testi dei badge invariati rispetto alla 0.7.
- 0.8.1: verde `--ok` più chiaro (#62eba6); le etichette `.pill.si` sono ora piene con testo scuro (leggibili); stesso verde nel badge 'acceso'.
- 0.8.1 (bug): la regola `.riga span:first-child` colorava di grigio le etichette (pill) prime nella riga; ora `.riga>span:first-child`, quindi 'Problema', 'da configurare' ecc. tornano rossi e 'OK' verde.
- 0.8.2: le etichette di stato nelle schede vanno a capo sotto il titolo a qualsiasi larghezza (prima solo sotto 720px: su Safari desktop con 5 schede strette sbordavano).
- 0.8.3: nelle schede della dashboard l'etichetta di stato sta SEMPRE sotto il titolo (classe ch-st; spazio riservato se vuota). 'Spento'/'Problema' sono rossi pieni ovunque (.pill.off/.bad = stesso rosso del badge), 'IN USO'/'IN CORSO' (resistenza, sbrinamento) gialli come il badge sbrinamento.
- 0.8.4 (bug): l'ultimo intervallo di 5 min (senza ancora il campione del compressore, che SmartThings consegna in ritardo) veniva classificato 'riposo' => Acqua calda 'Standby' durante un ciclo ACS. Ora gli ultimi 15 min senza campione ereditano lo stato dell'ultimo campione noto (calcola_modi); modo_corrente guarda 20 min indietro.
- 0.9: pagina **Dispositivi** (sorgenti/7_dispositivi.py): schede PdC (da SmartThings: ocf, deviceIdentification, softwareVersion, driver, softwareUpdate; una volta l'ora GET /devices/{id} per data di registrazione) e Smarther (Netatmo homestatus + homesdata orario: firmware_revision, id modulo = MAC, wifi_strength, setup_date); serie `info.pdc.*` e `info.nt.*` nella tabella `stati` (lo storico delle versioni e' la sua cronologia dei cambi); IP/MAC opzionali da config (`dispositivi.ip_*`, MAC via ARP, cache 10 min); modello Smarther dal catalogo BTicino (menu); loghi dalla cartella `loghi/` accanto allo script (NON forniti: i marchi sono di terzi), route `/loghi/<nome>` con whitelist di nomi/estensioni e CSP sandbox per gli SVG; badge Online/Offline in 'Stato del sistema' (+ riga Smarther); campo di tipo `scelta` nelle Impostazioni. Test: /tmp/t/d9 (t1 dati, t2 web/sicurezza, t3 screenshot).

## 0.9.1
- Stato "In funzione/A riposo": se il campione del compressore e' in ritardo (SmartThings), si tiene l'ultimo campione vero fino a 30 min (prima 15, e partiva da celle gia' dedotte). Se il compressore e' davvero fermo (0 Hz) torna subito a riposo.

## 0.9.2
- Logo Smarther nel titolo della card Casa: altezza 17px -> 12px, allineato al testo.

## 0.9.3
- Dispositivi: tolti i loghi piccoli bticino/netatmo sotto il titolo; il logo BTicino sta nella riga "Produttore" (con la nota "con Netatmo"). Logo Smarther a 26px.
- Cache: indirizzi di stile/script con impronta del contenuto (app.js?v=...) e ricarica automatica delle pagine rimaste aperte quando lo script viene aggiornato (intestazione X-Pdc-Build).

## 0.10
- Nuova scheda "Installatore" dentro Dispositivi (#/dispositivi/installatore): impostazioni FSV lette dalla pompa, in sola lettura, per menu, con nome italiano, intervallo del manuale, "fuori limiti", ultimo cambio e Copia elenco. Tolto il badge "modificato" (rumore: la curva climatica e' sempre regolata dall'installatore). Nuove sotto-pagine: percorso #/pagina/sotto.
- Etichette dei codici scritte da me; significati dal manuale EHS Mono (guida FSV di Dwellsee). Default indicativi (variano per modello).
- Installatore: etichette con "temperatura" esplicita; significati dei valori (2091/2092/2093, 3011, 4011, 4021, 4022, 4041, 4051, 3071) verificati sul manuale Samsung del telecomando cablato MWR-WW10N (modalita' Field Setting).

## 0.11
- Comandi: interruttori accendi/spegni per Riscaldamento casa (SmartThings, componente INDOOR), Acqua calda (componente main) e Termostato Smarther (Netatmo: setroomthermpoint off/home, ripiego setthermmode hg/schedule) nella card "Stato del sistema". Lucchetto da sbloccare (30 s), conferma allo spegnimento, nessuno stato "in attesa" (l'interruttore resta dove lo metti, il pannello rilegge dopo 4 e 20 s). Registro in Dispositivi (non Telegram). Niente interruttore generale, accessibile anche da fuori.
- Richiede token con permesso di comando: SmartThings x:devices:*, Netatmo write_smarther. pdc_strumenti.py st-oauth ora chiede entrambi gli scope di SmartThings.


## 0.11.1 – 0.11.2 (correzioni dopo le prove sui dispositivi veri, 02/11/2026; la 0.11.1 era il primo tentativo sullo Smarther, la 0.11.2 contiene tutto il resto)
- **Modo casa/acs con compressore in ritardo**: dal dump reale, i campioni ciclo (ogni 5 min) arrivano con 1-2 min di ritardo e il compressore a 0 Hz del campione vecchio faceva dire "Standby" mentre la pompa partiva (valvola passata a "tank" alle 21:43, Hz=50 solo nel campione delle 21:47). Ora, se i campioni dicono riposo ma `risc.acqua` (INDOOR, in tempo reale) sale di >=3 C in 15 min, il modo e' quello della valvola (room=casa, tank=acs). Tenuta dell'ultimo stato noto ridotta da 30 a 10 min. Tolte le regole resistenza/potenza (la pompa non ha resistenza sull'impianto; non c'e' modo di saperlo dall'API).
- Scheda "Compressore e circuito": "dato di N min fa".
- **Smarther: due modi di essere spento** (dump reale): dal pannello casa `therm_mode=hg` + stanza `home` a 7 C; dall'app stanza `therm_setpoint_mode=hg` + casa `schedule`. Lettura: spento se stanza off/hg O casa hg. `homesdata` riletto ogni 2 min (prima 1 h) e subito dopo ogni comando. NON si usa piu' il setpoint <= 9 (insensato per un manuale basso).
- **Comandi Smarther**: `setstate` con `therm_mode` nella casa NON esiste (errore 21 "should NOT have additional properties"). `setroomthermpoint` accetta solo manual/max/home. Strada giusta: spegni = `setthermmode hg`; accendi = `setthermmode schedule` + `setstate` stanza `therm_setpoint_mode: home`. Ogni tentativo e' seguito da una verifica (homesdata+homestatus dopo 3 s): una risposta "ok" non basta (setstate stanza rispondeva ok senza fare nulla). Il registro riporta strada riuscita e scartate.
- UI comandi: badge giallo ATTENDI... durante il comando (richiesta dell'utente, rispetto alla scelta iniziale di non avere uno stato di attesa); lo sblocco da 30 s riparte a ogni azione; registro a pagine da 5 (fino a 100 comandi in memoria).
- Costo energia: `tariffa.prezzo_kwh` (Impostazioni); costo nell'energia della Dashboard, costo stimato nell'Efficienza e costo medio di un ciclo acqua calda.
- Esporta CSV (`/api/esporta.csv?tipo=giorni|misure`), formato per Excel italiano.
- Backup notturno (`backup.*`, sorgenti/6_manutenzione.py: `fai_backup`, API di backup di sqlite3) e comando `backup`; rotazione delle ultime N copie.
- Idee scartate dall'utente: modalita'/temperatura ACS dal pannello, Smarther manuale/assente, nuovi allarmi Telegram. Badge Energia: resta "Consumo".
- Test: /tmp/t/d9 (t9 UI comandi, t10 comandi con rete simulata), /tmp/t/tg (regressione 4,5,7,9).
- Changelog: pagina #/changelog (fuori dalla barra del telefono: voce piccola sopra 'Esci' sul desktop, link in Impostazioni > Stato). I testi stanno in `CHANGELOG` in sorgenti/web/app.js: **aggiungere una voce a ogni consegna** (la piu' recente in cima, italiano semplice). Prima della 0.9 e' ricostruita dai 'pezzi' 1-7 e dalle note 0.8.x del piano, senza date.
- Diagnostica ritardo ciclo: `ServizioST._misura_ritardo` salva `diag.ritardo_ciclo` (s tra l'ora del campione e quella di arrivo, solo per campioni nuovi, meta `ciclo_ultimo_ts`); la Dashboard mostra tipico/massimo 24 h (`ritardo_ciclo` in /api/dashboard, serve >= 5 misure). Motivo: 'dato di 17 min fa' a pompa ferma, che dopo il riavvio del pannello risultava subito 'aggiornato' => sospetto che /status restituisca dati vecchi per un po' (cache SmartThings?) o che il pannello perda campioni. Da verificare con i numeri veri; se il massimo e' alto valutare il comando `refresh` della capability.


## 0.12 (03/11/2026)
- **Confronta** (Efficienza): `/api/efficienza?confronta=1` aggiunge `prec` (stessa durata, finestra da-durata..da: tot, copertura, cicli, bollitore, nodati_min, dati_da, completo). Pulsante `.conf-btn`, stato `S.confronto` non salvato (spento a ogni apertura).
- **Salute del bollitore**: `calcola_bollitore` in 2_web.py. Perdita notturna = °C/h nella finestra 23-06 piu' lunga (>=3 h) senza cicli ACS, W = °C/h * litri * 1.163; salita = mediana °C/h dai cicli ACS e minuti per 10 C. None se <2 notti/cicli. Config `bollitore.litri`.
- **Registro** (Impostazioni, tab `#/impostazioni/registro`): `/api/registro` (4_impostazioni.py) con ultime righe del log ripulite da segreti (`pulisci_riga`), pesi di db/log, stato backup.
- **ripristina** (`cmd_ripristina`, 6_manutenzione.py): richiede il blocco istanza (rifiuta se il pannello e' acceso); copie validate (integrity_check + tabelle) aprendole `mode=ro&immutable=1`; copia di sicurezza del db attuale con l'API backup di sqlite3 (include il -wal), poi cancella -wal/-shm e sostituisce con `os.replace`; config.json opzionale (default no) con copia di sicurezza.
- Versione 0.12 scelta perche' ci sono funzioni nuove; l'utente puo' cambiarla.

## 0.12.1 (03/11/2026, robustezza)
- Audit con pyflakes/bandit (solo falsi positivi) e misure su db da 2 M righe: pagine <0,3 s, niente da ottimizzare. Gzip non aggiunto (lo fa Cloudflare).
- Sicurezza: `riservato()` chmod 600 su config.json (e tmp, copie di backup); Content-Length negativo respinto (login e API); controllo Origin nel login; HSTS se https; `Diffusione.MAX_FLUSSI=20` (503); BUILD con sha256.
- Stabilita': `Raccoglitore.run` non muore piu' se perfino `eventi.apri` fallisce (disco pieno); stessa protezione per `Sentinella` e `_guardia` del tunnel (supervisore + `_guardia_interna`); `ErroreDB` con messaggio che rimanda a `ripristina`, `App(tollera_db=True)` per il comando ripristina; `ripristina` copia il db guasto com'e' se l'API di backup fallisce; `Config._scrivi` con fsync.
- SSE: `collega()` riapre il canale (Safari non riprova da solo); il server manda `event: ping` ogni 15 s, il client riapre dopo 45 s di silenzio, al ritorno sulla scheda, su pageshow/online.
- Non provato: database bloccato da un altro processo per >30 s.

## 0.13 (03/11/2026, Impostazioni in tab)
- Tab: Stato, Collegamenti (collegamenti + dispositivi), Avvisi (telegram, orari, testi, soglie allarmi), Accesso (password + Cloudflare), Dati (tariffa, bollitore, backup, storico), Registro. Sottopagine via hash `#/impostazioni/<tab>`; il server assegna `tab` a ogni sezione (`TAB_SEZIONI` in 4_impostazioni.py), nuove sezioni `bollitore.litri` e `backup.attivo/copie`.
- Riquadri = `<details class="card imp riq" data-id>`, stato aperto/chiuso in localStorage (`pdc_imp_aperti`); di default chiusi, tranne il primo di ogni tab. Una sola barra Salva per tutti i tab; `mostraCampo` apre i riquadri e cambia tab per un errore di validazione.
- Cloudflare: `POST /api/cloudflare/telegram` + pulsante (il bot Telegram resta solo in invio, niente comandi in arrivo).
- Avvio: `indirizzi()` mostra solo rete di casa e, se configurato, l'indirizzo Cloudflare. UI: `daQuanto()` per «acceso da».

## 0.13.1 (03/11/2026, rifiniture)
- Dispositivi su telefono (<=560 px): `.dev-head` a griglia (logo | stato sulla stessa riga, titolo/sottotitolo sotto), logo in casella fissa 28 px di altezza con object-fit (un SVG senza dimensioni intrinseche si sovrapponeva al testo con `height:auto`); `Wi-Fi` con trattino non separabile nelle etichette.
- Analisi: gruppi `casa` = «Temperatura ambiente · Smarther», `umid` = «Umidità · Smarther». Dashboard: la scheda resta «Casa · logo Smarther» (il titolo lungo mandava a capo il logo).

## 0.14 (03/11/2026, umidita')
- Config `umidita` {giallo 60, rosso 70, minuti 30, rientro_punti 2}: unica fonte per Telegram, Dashboard (`#c-um.um-giallo/.um-rosso`) e grafico Analisi (linee tratteggiate, `soglie_umidita` in /api/dashboard casa e /api/analisi, `soglie_umidita(app)` in 2_web.py). Impostazioni: riquadro «Umidità in casa» nel tab Avvisi, con controllo rosso > giallo in `salva_impostazioni`.
- Allarmi `umidita_alta` (avviso) e `umidita_molto_alta` (errore) in `Sentinella.valuta`, solo `attivo` e ritardo Telegram nella loro scheda; isteresi: restano aperti finche' umidita' > soglia - rientro_punti, poi i soliti 10 min di calma. Dato: `casa.umidita` (max 30 min di eta').
- Le linee nel grafico si disegnano solo se i dati arrivano a meno di 10 punti dalla soglia (per non schiacciare la scala).

## 0.14.1 (03/11/2026, riepilogo con la notte)
- `Sentinella._finestra_notte/_notte_righe`: nel riepilogo (solo li', niente messaggio a parte) la sezione «Di notte» con: temperatura casa (min/max se escursione >= `telegram.silenzio_notturno.oscillazione_gradi`, 2 C), umidita' massima se sopra la soglia gialla, eventi aperti nella finestra (anche gia' chiusi). Finestra = ultimo silenzio notturno finito; se il riepilogo e' prima della fine del silenzio, la notte in corso fino a ora. Con silenzio notturno spento non c'e' sezione.
- Nota: i messaggi di notte arrivano comunque, senza suoneria (`disable_notification`); gli errori gravi suonano a meno di `anche_errori`.

## 0.14.2 (03/11/2026, grafici)
- Potenza (Analisi, <= 3 giorni): `q_potenza_a_gradini` usa `en.finestra_s` per disegnare ogni rapporto come tratto [fine - finestra, fine] a valore costante (continuo a gradini); senza finestra resta il punto. Causa dello spezzettamento: un punto ogni ~15 min con tolleranza `buco` di ~1000 s, quindi la linea si interrompeva fra quasi tutti i punti (per 'pot' ora 3600 s).
- Puntini al passaggio del mouse: `.cur-d` per linea, `valoreInterp` (interpolazione fra punti vicini, nessun puntino nei buchi), scala letta da `data-lo/hi/t0/ph/buco` dell'SVG.
- Riepilogo «Modo di funzionamento»: «casa» -> «riscaldamento» (la legenda resta «Riscaldamento casa»).

## 0.15 (03/11/2026, grafico del circuito)
- `GRUPPI_ANALISI`: i gruppi `comp`, `portata`, `pompa` sono sostituiti da `circuito` ("Compressore, portata e pompa"). Le serie possono avere 3 campi extra `(unita, asse, area)`; `/api/analisi` li espone per linea (`u`, `asse`, `area`) e per gruppo (`gradini`).
- `graficoLineare`: se una linea ha `asse: 1` il grafico ha due scale (destra con numeri colorati come la linea, senza griglia). `percorsoGradini`/`segmentiGradini`: gradini centrati a meta' strada fra due punti, pezzi staccati dove il buco supera `buco`; l'area (compressore) e' chiusa sulla linea dello zero.
- Puntini al passaggio del mouse: per i gradini `valoreGradino` (valore del punto piu' vicino, se non c'e' un buco), `data-mezzo` sull'svg; ogni puntino ha `data-a` per scegliere la scala. La legenda usa l'unita' di ogni linea (`l.u`).
- Il target mandata resta com'e' (linea morbida), da rivedere.
- Dashboard, scheda "Compressore e circuito": con compressore a 0 Hz mostra lo stato "Fermo" e tre riquadri (`.fermo-w`: per cosa, durata, energia) dell'ultimo ciclo concluso; l'API espone `ultimo_ciclo` {inizio, fine, modo, kwh} (da `cicli_compressore`, ultimi 3 giorni; kWh dal contatore `en.totale_wh`, omessi se non leggibili). Le tessere e la nota sul ritardo restano solo con compressore attivo; l'etichetta "dato di N min fa" a compressore fermo compare solo oltre 20 min.
- La tessera "Gas di scarico" si mostra solo se `prestazioni.scarico` ha un valore (sulla pdc dell'utente e' sempre assente).
- Grafico "Temperatura ambiente · Smarther": `fasceRichiesta` fonde i campioni consecutivi in blocchi; oltre alla tinta leggera c'e' una striscia piena (7 px) sul fondo del riquadro. La legenda ha `richiestaLegenda` ("Richiesta caldo si/no" all'ora del cursore, o ultimo valore).
- Nota sul ritardo (`#prest-rit`): ora HTML con `.nw` (nowrap) su ogni gruppo, titolo su una riga a parte.

## 0.16 (03/11/2026, cartelle)
- Nuova disposizione: radice = `pdc_pannello.py`, `pdc_strumenti.py`, `config.json`; `dati/` (pdc.db, backup/, loghi/, demo.db; i file da mandare in chat stanno in `diagnostica/`, vedi 0.17), `log/` (pdc_pannello.log, cloudflared.log), `cloudflared/` (exe + cloudflared.pid). Default: `generale.database = "dati/pdc.db"`, `backup.cartella = "dati/backup"`.
- `riordina_cartelle(base, cfg)` (1_nucleo.py), chiamata da `main()` prima di `imposta_log` (non in demo): sposta con `os.replace` (stesso disco = rinomina), mai sovrascrive; `_sposta` per cartelle unisce solo i file mancanti. Il database (+ -wal/-shm) e' tutto o niente con rollback. Aggiorna config.json solo se i valori sono i vecchi predefiniti (`pdc.db`, `backup`). Prima di spostare prova a prendere `porta_lock`: se occupata esce con codice 1. Se esistono sia `pdc.db` sia `dati/pdc.db` esce con codice 1 senza toccare nulla. Un file che non si lascia spostare (es. log aperto da un cloudflared rimasto vivo, su Windows) resta dov'era e viene segnalato nel log.
- `cartelle_loghi(base)`: `dati/loghi` poi la vecchia `loghi` (stessa logica in `elenco_loghi` e `_logo`). `cartella_log(base)` crea `log/`.
- Provato (`/tmp/t/mig/mig.py`, 21 controlli): layout vecchio completo con WAL aperto, copia accesa, due database, percorso personalizzato, installazione nuova, idempotenza, `backup` e `ripristina` nei nuovi percorsi.
- Correzione (stessa 0.16): `_pulisci_vecchio` (tunnel) leggeva l'output di `tasklist` come UTF-8; su Windows e' nella tabella OEM e un byte 0x85 faceva fallire la lettura (`stdout` = None, `AttributeError`) e il pannello non partiva. Non emergeva prima perche' il file `cloudflared.pid` veniva sempre rimosso a pannello chiuso; la migrazione l'ha spostato e il controllo e' partito. Ora si legge in byte, ogni errore di questo controllo e' solo un avviso nel registro, e anche `cloudflared --version` e `arp` leggono con `errors="replace"`.

## 0.17 (03/11/2026, aggiornamento a caldo)
- `costruisci.py` mette un segnalibro `#@@PARTE nome@@` davanti a ogni parte tranne la prima (nucleo = intestazione + 1_nucleo). Nuovo `9_aggiorna.py` (prima di `9_comandi.py`): `_leggi_parti` divide il file, `_impronta` fa lo sha256 di ogni parte (nel nucleo la riga `VERSIONE = "..."` e' normalizzata, cosi' cambiare solo versione non richiede riavvio), `PARTI_CARICATE` = com'era il file all'avvio.
- Ricaricabili a caldo (`RICARICABILI`): web, impostazioni, dispositivi, controlli. Il resto (nucleo, telegram, cloudflare, manutenzione, aggiorna, comandi) e' "fisso": se cambia, `applica_aggiornamento` rifiuta e dice di riavviare.
- `applica_aggiornamento(app)`: 1) rilegge il file; 2) rifiuta se e' cambiata una parte fissa; 3) compila ogni parte cambiata (riempita di righe vuote davanti, cosi' i numeri di riga dei messaggi coincidono col file) e se non compila non tocca nulla; 4) imposta `VERSIONE`, esegue le parti con `exec` nei globali del modulo, con istantanea dei globali per tornare indietro se un'esecuzione lancia un'eccezione; 5) ricalcola `BUILD` (le pagine aperte si ricaricano da sole) e mette `app.server_web.RequestHandlerClass = Gestore`: il server non si ferma, ne' si riapre la porta, ne' si chiudono gli SSE.
- `API` / `API_POST` ora sono `globals().get("API") or {}`: lo stesso dizionario sopravvive alla ricarica (nessuna finestra con rotte vuote) e le rotte delle parti fisse (tunnel, aggiornamento) non si perdono. `avvia_web` salva `app.server_web`; il `finally` di `main` chiude quello. Il flusso SSE esce se riceve l'evento "chiudi" (non ancora usato).
- Limiti noti: gli oggetti gia' creati restano col codice vecchio (`Sessioni`, `LimiteLogin`, `Diffusione`, `ServerWeb`: se la loro classe cambia serve il riavvio); lo stato di modulo delle parti ricaricate si azzera (`_cache_*`, `_ultimo_comando`); un thread che sta eseguendo una funzione vecchia la finisce col codice vecchio.
- API: `GET /api/aggiornamento` ({stato: aggiornato|applicabile|riavvio|errore, parti, versione, versione_disco}), `POST /api/aggiornamento/applica`. UI: riquadro «Aggiornamento» in Impostazioni → Stato (controllo ogni 20 s).
- Prova (`/tmp/t/hot/hot.py`, 17 controlli): modifica a caldo con rotta nuova, nuova versione e BUILD cambiato; errore di sintassi (riga giusta, nulla cambia); eccezione a meta' (ripristino dei globali); cambio nel nucleo (rifiutato); solo numero di versione (nessuna parte cambiata); thread di raccolta finto che scrive ogni 50 ms durante tutto il test: buco massimo 60 ms.
- Correzione (stessa 0.16/0.17): i file da mandare in chat hanno una cartella a parte, `diagnostica/`: `esporta-diagnostica` scrive `diagnostica/diagnostica.json` e `pdc_strumenti.py` i `dump_*.json`. `riordina_cartelle` sposta li' quelli gia' creati (`dump_*.json` in radice e `dati/diagnostica.json`).
- Indicatore Dashboard (`modo_istantaneo`, parte web): la regola "l'acqua sta salendo" ora guarda l'acqua in questo momento (+1,5 gradi in 7 minuti e vicina al massimo), non il minimo degli ultimi 15 minuti; nuova regola di stop (acqua scesa di 1 grado dal massimo recente e nessun campione recente con compressore in marcia). La causa principale del ritardo resta da confermare con diagnostica.json (arrivo dei campioni dopo lo stop) e un nuovo st-dump.
