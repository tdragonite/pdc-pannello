# Per chi modifica il codice

Il programma che avvii (`pdc_pannello.py`) è un file **generato**. I sorgenti veri stanno in `sorgenti/` (una parte per argomento: nucleo, web, Telegram, impostazioni, Cloudflare, manutenzione, dispositivi, controlli, aggiornamento, comandi; in `sorgenti/web/` pagine, stile e script). Per rigenerarlo:

```
python costruisci.py
```

Le pagine e i dati che il pannello manda al browser sono compressi (gzip), così su telefono pesano circa un terzo; `app.js` e `app.css` restano nella cache del browser (l'indirizzo cambia a ogni versione) e la prima Dashboard arriva già dentro la pagina. Le note tecniche (architettura, come è fatto il database, come si leggono i dati grezzi, decisioni) sono in [PIANO.md](PIANO.md); la cronologia delle versioni è nel Changelog del pannello. Gli strumenti di appoggio (dump SmartThings/Netatmo, OAuth, esperimenti) sono in `pdc_strumenti.py`, descritti in [strumenti.md](strumenti.md).
