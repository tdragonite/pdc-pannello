# Pannello pompa di calore (Samsung EHS)

Un pannello web per la tua pompa di calore Samsung EHS e per lo Smarther 2, che gira sul tuo PC. **Legge e mostra** tutto (dati, storico, efficienza) e, se vuoi, ti lascia anche **comandare**: accendere/spegnere riscaldamento, acqua calda e Smarther, e (solo dalla rete di casa) cambiare le regolazioni della pompa e il boost dell'acqua calda, tutto dietro un lucchetto.

Ha le pagine **Dashboard**, **Analisi**, **Efficienza**, **Dispositivi** e **Impostazioni** (più il **Changelog**), funziona bene su iPhone, ti avvisa su Telegram se qualcosa non va e, se vuoi, lo raggiungi anche da fuori casa. Accetta integrazioni a plugin, come Alexa e il widget per iPhone.

Tutto sta in **un solo file Python** (`pdc_pannello.py`), senza installare nulla oltre a Python. Dati, impostazioni e log stanno nelle sottocartelle della cartella dello script.

## Per cominciare

1. Serve un PC che resta acceso (qui si parla di Windows) con **Python 3.10 o più recente** e un account SmartThings con la pompa dentro. Facoltativi: Netatmo (Smarther), un bot Telegram, un dominio Cloudflare.
2. Metti `pdc_pannello.py` e `pdc_strumenti.py` in una cartella, per esempio `C:\pdc`.
3. Per provarlo subito con dati finti: `python pdc_pannello.py --demo --porta 7010`, poi apri `http://localhost:7010`.
4. Per usarlo davvero, segui [docs/installazione.md](docs/installazione.md): collegare SmartThings e Netatmo, scegliere la password, avviarlo.

## La documentazione

| Documento | Che cosa trovi |
|---|---|
| [Installazione e primo avvio](docs/installazione.md) | Cosa serve, primo avvio, prova con i dati finti, SmartThings (PAT o OAuth), Netatmo, password, avvio senza finestra |
| [Le pagine del pannello](docs/pagine.md) | Come leggere Dashboard, Analisi, Efficienza, Dispositivi, Impostazioni e Changelog; usarlo su iPhone |
| [Comandi e regolazioni](docs/comandi-e-regolazioni.md) | Accendere e spegnere dal pannello, regolazioni della pompa (FSV, acqua calda, boost) |
| [Telegram e accesso da fuori casa](docs/telegram-e-cloudflare.md) | Gli avvisi su Telegram e il tunnel Cloudflare |
| [Comandi del programma e config.json](docs/configurazione.md) | Tutti i comandi da riga di comando e il file di configurazione spiegato |
| [Manutenzione, problemi e sicurezza](docs/manutenzione-e-problemi.md) | Cartelle, backup, aggiornamenti, se qualcosa non va, sicurezza |
| [Plugin, Alexa e widget](docs/plugin.md) | Integrazioni, Routine Alexa, widget per iPhone |
| [Strumenti di diagnostica](docs/strumenti.md) | `pdc_strumenti.py`: ID, OAuth, dump, analisi, prove |
| [Per chi modifica il codice](docs/sviluppo.md) | Come si costruisce il file dai sorgenti |
| [«Puoi chiudere»: come toglierlo](docs/ventilazione-rimozione.md) | Se la prova delle finestre aperte non ti serve, come toglierla |
| [Piano e stato del progetto](docs/PIANO.md) | Architettura, decisioni, cose aperte |

## Crediti

- **Idea, requisiti e prove sul campo**: Alessandro, sul suo impianto (Samsung EHS Mono, Smarther 2). **Scritto insieme a Claude** (Anthropic).
- **[Dwellsee](https://www.dwellsee.com)**: l'ispirazione per le pagine e le funzioni del pannello (Dashboard, Analisi, Efficienza, curva climatica, Boost) e la «Cycle Data Decoding Guide», da cui viene la lettura dei dati grezzi del compressore (`samsungce.ehsCycleData`).
- **[pookey/samsung-heatpump-smartthings](https://github.com/pookey/samsung-heatpump-smartthings)**: gli script da cui è partito il progetto.
- **[Open-Meteo](https://open-meteo.com)**: le previsioni del meteo di oggi (dati con licenza CC BY 4.0), con i modelli ICON-2I ed ECMWF.
- **WeatherLink**: la pagina pubblica della stazione meteo vicina da cui si legge l'umidità esterna.
- **Servizi e programmi usati**: SmartThings (Samsung), Netatmo e lo Smarther 2 (BTicino), Telegram, Cloudflare e il suo `cloudflared`, Alexa (Amazon) e l'app Scriptable per il widget.

I nomi e i marchi appartengono ai rispettivi proprietari; questo progetto non è affiliato a nessuno di loro.
