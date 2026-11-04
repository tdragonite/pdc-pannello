# Pannello pompa di calore (Samsung EHS + Smarther 2)

Pannello web locale, in italiano, per una pompa di calore Samsung EHS Mono (SmartThings) e un termostato BTicino/Netatmo Smarther 2.
Un solo file Python, nessuna dipendenza oltre a Python 3.9+.

- `pdc_pannello.py` – il pannello (**file generato**, è quello da avviare)
- `sorgenti/` – i sorgenti veri; `python costruisci.py` li riunisce in `pdc_pannello.py`
- `pdc_strumenti.py` – strumenti di appoggio (dump SmartThings/Netatmo, OAuth, `st-prova-refresh`, `rip-diag`, `sbrinamenti`)
- `config.example.json` – modello vuoto di configurazione: copialo come `config.json` e compilalo
- `docs/` – guida completa (`README.md`), note tecniche per versione (`PIANO.md`), guida degli strumenti
- `extra/` – script di prova vari

## Avvio

```
python pdc_pannello.py
```

La prima volta crea `config.json`; compila le sezioni `smartthings` (e `netatmo`), poi rilancia. Guida passo passo in `docs/README.md`.

## Attenzione

`config.json`, `dati/` (database), `log/`, `cloudflared/` e `diagnostica/` contengono password, token e dati personali:
sono nel `.gitignore` e **non vanno caricati**, nemmeno in un repository privato.
