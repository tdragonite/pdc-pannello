# Plugin Widget: un indirizzo pubblico, in SOLA LETTURA, con i pochi dati che servono al widget dell'iPhone (app Scriptable).
# Installazione: copia questo file nella cartella "plugin" accanto a pdc_pannello.py, apri Impostazioni -> Integrazioni,
# premi "Ricarica i plugin" e accendi l'interruttore. L'indirizzo da incollare nello script di Scriptable compare in «Indirizzi pubblici».
#
# SICUREZZA:
#  1. l'indirizzo contiene un segreto casuale, generato al primo avvio: senza non si arriva a nulla (404);
#  2. risponde solo a GET e non puo' comandare niente: niente storico, niente impostazioni, nessun comando;
#  3. se l'indirizzo finisce in mano a qualcuno, scrivi un segreto nuovo (o svuota il campo), ricarica i plugin e aggiorna lo script:
#     il vecchio indirizzo smette di funzionare;
#  4. il pannello limita comunque la frequenza delle richieste;
#  5. in piu' c'e' un TOKEN (campo «Token del widget»): se e' impostato, chi non lo manda nell'intestazione «X-Widget-Token» riceve
#     lo stesso 404 di un indirizzo sbagliato, quindi aprire l'indirizzo da un browser non mostra niente. Il token si scrive anche
#     nello script di Scriptable. (Se il campo e' vuoto il controllo e' spento, come nella 1.0.)
import hmac
import re
import secrets
import time

INFO = {
    "titolo": "Widget iPhone",
    "versione": "1.1.0",
    "interfaccia": 1,
    "descrizione": "Indirizzo di sola lettura per il widget dell'iPhone (app Scriptable): casa, termostato, pompa di calore, acqua calda e fuori, "
                   "già pronti da mostrare. Non può comandare niente.",
}
CHANGELOG = [
    {"v": "1.1.0", "data": "2026-11-04T18:00", "titolo": "Token del widget", "righe": [
        ["n", "Token", "Campo nuovo in Impostazioni: se lo imposti, senza il token l’indirizzo risponde 404 anche a chi conosce il segreto. Aprirlo da un browser non mostra niente."]]},
    {"v": "1.0.0", "data": "2026-11-04T16:00", "titolo": "Primo rilascio", "righe": [
        ["n", "Widget per Scriptable", "Indirizzo di sola lettura con casa, termostato, pompa di calore, acqua calda e fuori, già pronti da disegnare. Solo GET, nessun comando."]]},
]

CAMPI = [
    {"chiave": "segreto", "tipo": "segreto", "et": "Segreto dell’indirizzo",
     "aiuto": "Generato da solo al primo avvio. Per cambiarlo scrivine uno nuovo (da 24 a 64 caratteri tra lettere, numeri, trattino e "
              "underscore), oppure svuota il campo: ne esce uno nuovo. Poi ricarica i plugin e aggiorna l’indirizzo nello script di Scriptable."},
    {"chiave": "token", "tipo": "segreto", "et": "Token del widget (consigliato)",
     "aiuto": "Scrivine uno tuo (da 24 a 64 caratteri tra lettere, numeri, trattino e underscore) e incollalo uguale nello script di Scriptable. "
              "Se è impostato, senza il token l’indirizzo risponde 404 anche a chi conosce il segreto: aprirlo da un browser non mostra niente. "
              "Campo vuoto = controllo spento."},
]

_ctx = {}
CASA_VECCHIO = 900        # secondi: oltre, il dato dello Smarther e' «vecchio» (come nella Dashboard)
ESTERNA_VECCHIA = 7200    # secondi: oltre, la sonda della pompa e' ferma (come nella Dashboard)


def n(v, dec=1):
    """Numero all'italiana: 20,5 - e senza ',0' se e' tondo."""
    s = f"{float(v):.{dec}f}".replace(".", ",")
    return s[:-2] if s.endswith(",0") else s


def hhmm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def gradi(v):
    return f"{n(v)}°" if v is not None else "—"


def _token():
    """Il token configurato (valido), o '' se il controllo e' spento."""
    t = str(_ctx["ctx"].config().get("token") or "").strip()
    return t if re.fullmatch(r"[A-Za-z0-9_\-]{24,64}", t) else ""


def avvia(ctx):
    _ctx["ctx"] = ctx
    seg = str(ctx.config().get("segreto") or "")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{24,64}", seg):
        seg = secrets.token_urlsafe(24)
        ctx.salva("segreto", seg)
    ctx.rotta(seg, richiesta, ("GET",))
    ctx.stato("in attesa della prima richiesta" + ("" if _token() else " (senza token: consigliato impostarlo)"))


def ferma(ctx):
    _ctx.clear()


def riepilogo(d, ora=None):
    """Dalla Dashboard al riepilogo per il widget: testi gia' pronti, lo script di Scriptable deve solo disegnarli."""
    ora = ora or time.time()
    c, e, a, s = d["casa"], d["esterna"], d["acs"], d["sistema"]
    modo = d.get("modo")

    # --- Casa: il riquadro in basso mostra UNA cosa (come la riga di avviso della Dashboard)
    if c["temp"] is None:
        chip = {"testo": "Smarther non collegato", "tipo": "bad"}
    elif c.get("ts") and ora - c["ts"] > CASA_VECCHIO:
        chip = {"testo": "Dato delle " + hhmm(c["ts"]), "tipo": "warn"}
    elif c.get("richiesta"):
        chip = {"testo": "Il termostato chiama", "tipo": "chiama"}
    elif c.get("riparte"):
        t = time.localtime(c["riparte"])
        oggi = time.localtime(ora)
        domani = (t.tm_year, t.tm_yday) != (oggi.tm_year, oggi.tm_yday)
        chip = {"testo": ("Riparte domani " if domani else "Riparte alle ") + hhmm(c["riparte"]), "tipo": "riparte"}
    else:
        chip = {"testo": "Non chiama", "tipo": "fermo"}

    # --- Pompa di calore
    if not s.get("pdc_accesa"):
        stato, testo = "spenta", "Spenta"
    elif s.get("sbrinamento") or d["riscaldamento"]["stato"] == "sbrinamento":
        stato, testo = "sbrinamento", "Sbrinamento"
    elif modo == "casa":
        stato, testo = "riscaldamento", "Riscaldamento"
    elif modo == "acs":
        stato, testo = "acqua", "Acqua calda"
    elif modo == "riposo":
        stato, testo = "riposo", "A riposo"
    else:
        stato, testo = "nodati", "Nessun dato"
    if a["temp"] is None:
        dettaglio = ""
    elif stato == "acqua" and a.get("target") is not None:
        dettaglio = f"Acqua {gradi(a['temp'])} → {gradi(a['target'])}"
    else:
        dettaglio = f"Acqua calda {gradi(a['temp'])}"

    # --- In fondo a destra: ora dell'ultimo aggiornamento, o l'avviso se la sonda della pompa e' ferma
    if e.get("ts") and ora - e["ts"] > ESTERNA_VECCHIA:
        piede = {"testo": "Dato PdC delle " + hhmm(e["ts"]), "tipo": "warn"}
    else:
        ultimo = d.get("ultimo_ok") or c.get("ts")
        piede = {"testo": "Aggiornato alle " + hhmm(ultimo or ora), "tipo": "normale"}

    return {
        "versione": 1, "ora": int(ora),
        "casa": {"temp": gradi(c["temp"]), "target": gradi(c.get("target")),
                 "umidita": f"{n(c['umidita'], 0)}%" if c.get("umidita") is not None else "—", "chip": chip},
        "pdc": {"stato": stato, "testo": testo, "dettaglio": dettaglio},
        "fuori": {"temp": gradi(e["temp"]), "umidita": f"{n(e['umidita'], 0)}%" if e.get("umidita") is not None else None},
        "piede": piede,
    }


def richiesta(rich):
    ctx = _ctx["ctx"]
    tok = _token()
    if tok:
        dato = str(rich.intestazioni.get("x-widget-token", ""))
        if not hmac.compare_digest(dato.encode(), tok.encode()):      # stessa risposta di un indirizzo sbagliato
            return 404, "Non trovato", "text/plain; charset=utf-8"
    try:
        r = riepilogo(ctx.dashboard())
    except Exception:
        ctx.log.exception("widget: errore nel riepilogo")
        return 500, {"errore": "errore interno"}
    ctx.stato("ultima richiesta alle " + time.strftime("%H:%M"))
    return r
