# Comandi del programma e config.json

## Tutti i comandi

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

Nella stessa cartella c'è anche `pdc_strumenti.py` (vedi [strumenti.md](strumenti.md)): oltre ai dump, `rip-diag` e `sbrinamenti [--giorni 7] [--salva]` (elenco degli sbrinamenti letti dal database). Anche `nt-webhook-prova` (prova dei webhook Netatmo, capitolo 6 di strumenti.md), `meteo-prova` (stazione meteo vicina, capitolo 8 di strumenti.md) e **`analisi`**: crea in un colpo solo un file `diagnostica/analisi_….json` con tutti i dati del database (impostazioni, cicli, sbrinamenti, serie nel tempo, senza token né indirizzi), da mandare a chi ti aiuta (capitolo 9 di strumenti.md). Ci sono anche `st-capacita` e `st-osserva` (i comandi della pompa e cosa cambia quando premi Boost, capitolo 10 di strumenti.md).

Per pythonw usa gli stessi argomenti, ma non vedrai l'output: i comandi “da guardare” (`stato`, `telegram-prova`…) lanciali con `python`.


---

## Il file config.json spiegato

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
| `plugin` | un riquadro per plugin: `attivo` (spento di partenza) e i campi che il plugin dichiara. Per Alexa: `segreto`, `skill_id`, `verifica_firma` (vedi [plugin.md](plugin.md)). Si cambiano da *Impostazioni → Integrazioni* |
| `mappa_ciclo` | come si leggono i dati grezzi del compressore (non toccarla se non te lo chiede chi ti aiuta) |

Le modifiche valgono quasi tutte **subito**; fanno eccezione porta, host e intervalli di lettura, che valgono dal riavvio.
