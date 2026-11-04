

# ==========================================================================
# AGGIORNAMENTO A CALDO: ricarica le parti di interfaccia senza fermare la raccolta dei dati
# ==========================================================================
# Il file e' costruito a "parti" (segnalibri #@@PARTE nome@@). Le parti di interfaccia (pagine, API, Impostazioni, Dispositivi,
# comandi) si possono rieseguire dentro il programma gia' acceso; le altre (nucleo della raccolta, Telegram, tunnel, backup,
# avvio) no: se cambiano serve il riavvio completo. Il server web non si ferma: gli si cambia solo la classe che risponde.

RICARICABILI = ("web", "impostazioni", "dispositivi", "controlli", "plugin")
NOMI_PARTI = {"nucleo": "nucleo della raccolta dati", "web": "pagine e API", "telegram": "avvisi Telegram",
              "impostazioni": "Impostazioni", "cloudflare": "tunnel Cloudflare", "manutenzione": "backup e manutenzione",
              "dispositivi": "Dispositivi", "controlli": "comandi allo Smarther", "plugin": "plugin e integrazioni", "aggiorna": "aggiornamento a caldo",
              "comandi": "avvio e comandi"}
_RX_PARTE = re.compile(r"^#@@PARTE (\w+)@@\n", re.M)
_RX_VERSIONE = re.compile(r'^VERSIONE = "([^"]*)"$', re.M)
_lock_aggiorna = threading.Lock()


def _leggi_parti(testo):
    """Il file diviso nelle sue parti: {nome: (sorgente, righe_prima)} nell'ordine in cui compaiono."""
    parti, nome, inizio = {}, "nucleo", 0
    for m in _RX_PARTE.finditer(testo):
        parti[nome] = (testo[inizio:m.start()], testo.count("\n", 0, inizio))
        nome, inizio = m.group(1), m.end()
    parti[nome] = (testo[inizio:], testo.count("\n", 0, inizio))
    return parti


def _impronta(nome, sorgente):
    if nome == "nucleo":    # il numero di versione sta nel nucleo, ma cambiarlo non richiede il riavvio
        sorgente = _RX_VERSIONE.sub('VERSIONE = ""', sorgente, count=1)
    return hashlib.sha256(sorgente.encode("utf-8")).hexdigest()[:16]


def _parti_su_disco():
    testo = Path(__file__).resolve().read_text(encoding="utf-8")
    return testo, _leggi_parti(testo)


def _impronte_correnti():
    try:
        return {n: _impronta(n, s) for n, (s, _) in _parti_su_disco()[1].items()}
    except Exception:
        return {}


PARTI_CARICATE = _impronte_correnti()    # com'era il file quando e' partito il pannello


def stato_aggiornamento():
    """{'stato': aggiornato|applicabile|riavvio|errore, 'parti': [...], 'versione_disco', 'versione'}"""
    out = {"versione": VERSIONE, "versione_disco": VERSIONE, "parti": [], "stato": "aggiornato"}
    try:
        testo, parti = _parti_su_disco()
    except (OSError, UnicodeDecodeError) as e:
        return dict(out, stato="errore", messaggio=f"Non riesco a leggere il file del pannello ({e}).")
    m = _RX_VERSIONE.search(testo)
    out["versione_disco"] = m.group(1) if m else VERSIONE
    if not PARTI_CARICATE or "nucleo" not in parti:
        return dict(out, stato="riavvio", parti=[], messaggio="Non riesco a confrontare il file con quello in uso: riavvia il pannello.")
    cambiate = [n for n, (s, _) in parti.items() if _impronta(n, s) != PARTI_CARICATE.get(n)]
    out["parti"] = [{"nome": n, "titolo": NOMI_PARTI.get(n, n), "a_caldo": n in RICARICABILI} for n in cambiate]
    if any(n not in RICARICABILI for n in cambiate):
        out["stato"] = "riavvio"
    elif cambiate:
        out["stato"] = "applicabile"
    return out


def applica_aggiornamento(app):
    """Rilegge il file dal disco e ricarica a caldo le parti di interfaccia cambiate. Prima controlla tutto: se qualcosa non va,
    non cambia nulla e continua a girare il codice di prima. -> {'ok': bool, ...}"""
    if not _lock_aggiorna.acquire(blocking=False):
        return {"ok": False, "messaggio": "Un aggiornamento e' gia' in corso."}
    try:
        try:
            testo, parti = _parti_su_disco()
        except (OSError, UnicodeDecodeError) as e:
            return {"ok": False, "messaggio": f"Non riesco a leggere il file del pannello ({e})."}
        if not PARTI_CARICATE or "nucleo" not in parti:
            return {"ok": False, "messaggio": "Non riesco a confrontare il file con quello in uso: serve il riavvio completo."}
        cambiate = [n for n, (s, _) in parti.items() if _impronta(n, s) != PARTI_CARICATE.get(n)]
        fissi = [n for n in cambiate if n not in RICARICABILI]
        if fissi:
            return {"ok": False, "riavvio": True, "messaggio": "Per questa versione serve il riavvio completo (cambiato: "
                    + ", ".join(NOMI_PARTI.get(n, n) for n in fissi) + "). La raccolta dei dati continua finche' non lo fai."}
        if not cambiate:
            return {"ok": True, "gia": True, "messaggio": "Il pannello e' gia' aggiornato.", "versione": VERSIONE}
        codici = {}
        for n in cambiate:                      # 1) il codice nuovo deve almeno compilare
            sorg, righe = parti[n]
            try:
                codici[n] = compile("\n" * righe + sorg, str(Path(__file__).resolve()), "exec")
            except SyntaxError as e:
                return {"ok": False, "messaggio": f"Il file nuovo ha un errore (parte «{NOMI_PARTI.get(n, n)}», riga {e.lineno}): "
                        f"{e.msg}. Non ho cambiato nulla, il pannello continua con la versione di prima."}
        g = globals()
        prima = dict(g)                         # 2) per tornare indietro se l'esecuzione fallisce
        nuova = _RX_VERSIONE.search(testo)
        try:
            if nuova:
                g["VERSIONE"] = nuova.group(1)
            for n in [n for n in parti if n in codici]:
                exec(codici[n], g)
        except Exception as e:
            for k in set(g) | set(prima):
                if prima.get(k, g) is not g.get(k, prima):
                    if k in prima:
                        g[k] = prima[k]
                    else:
                        g.pop(k, None)
            log.exception("aggiornamento a caldo fallito")
            return {"ok": False, "messaggio": f"Il codice nuovo si e' fermato per un errore ({type(e).__name__}: {e}). "
                    "Ho rimesso quello di prima."}
        # l'impronta delle pagine (la stessa formula della parte web) dipende anche dal numero di versione
        g["BUILD"] = hashlib.sha256((g["VERSIONE"] + "".join(g["WEB_ASSETS"][k] for k in sorted(g["WEB_ASSETS"]))).encode("utf-8")).hexdigest()[:8]
        for n in cambiate:
            PARTI_CARICATE[n] = _impronta(n, parti[n][0])
        if "plugin" in cambiate and "plugin_ricarica" in g:      # il caricatore e' nuovo: rilegge la cartella plugin
            try:
                g["plugin_ricarica"](app)
            except Exception:
                log.exception("ricarica dei plugin fallita")
        srv = getattr(app, "server_web", None)
        if srv is not None:
            srv.RequestHandlerClass = g["Gestore"]   # le richieste nuove usano il codice nuovo; il server non si ferma
        titoli = ", ".join(NOMI_PARTI.get(n, n) for n in cambiate)
        log.info("Aggiornamento a caldo applicato: %s (versione %s)", titoli, VERSIONE)
        return {"ok": True, "versione": VERSIONE, "parti": titoli,
                "messaggio": f"Aggiornato alla versione {VERSIONE} senza fermare la raccolta dei dati."}
    finally:
        _lock_aggiorna.release()


@api("/api/aggiornamento")
def api_aggiornamento(app, q):
    return stato_aggiornamento()


@api_post("/api/aggiornamento/applica")
def api_applica_aggiornamento(app, corpo, h):
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo non si aggiorna a caldo."}
    return applica_aggiornamento(app)
