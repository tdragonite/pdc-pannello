# Plugin di esempio: non fa nulla di utile, serve a provare le Integrazioni.
# Copialo nella cartella "plugin" accanto a pdc_pannello.py, apri Impostazioni -> Integrazioni, premi "Ricarica i plugin"
# e accendi l'interruttore. Poi prova nel browser:  /plugin/esempio/ping  (risponde anche senza login: e' una rotta pubblica).
INFO = {
    "titolo": "Esempio",
    "versione": "1.0",
    "interfaccia": 1,
    "descrizione": "Plugin di prova: espone l’indirizzo pubblico /plugin/esempio/ping, che risponde con la temperatura in casa.",
}
CAMPI = [
    {"chiave": "saluto", "tipo": "testo", "et": "Saluto", "aiuto": "Il testo che compare nella risposta.", "default": "ciao"},
]
_ctx = {}


def avvia(ctx):
    _ctx["ctx"] = ctx
    ctx.rotta("ping", ping, ("GET",))
    ctx.stato("in ascolto su /plugin/esempio/ping")


def ping(richiesta):
    ctx = _ctx["ctx"]
    ultimi = ctx.ultimi()
    t = ultimi.get("casa.temperatura")
    return {"saluto": ctx.config()["saluto"], "temperatura_casa": t[1] if t else None}
