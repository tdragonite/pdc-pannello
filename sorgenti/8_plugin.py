
# ==========================================================================
# PLUGIN: integrazioni opzionali in file esterni (cartella «plugin», accanto a pdc_pannello.py)
# ==========================================================================
# Un plugin e' un file plugin/nome.py che il pannello carica all'avvio (e con «Ricarica i plugin» in Impostazioni > Integrazioni).
# Un errore dentro un plugin non ferma mai il pannello: lo segna e basta. Il plugin gira con gli stessi permessi del pannello,
# quindi nella cartella plugin ci va solo codice di cui ci si fida.
#
# Cosa deve contenere un plugin (INTERFACCIA 1):
#   INFO  = {"titolo": "Alexa", "versione": "1.0", "interfaccia": 1, "descrizione": "..."}
#   CAMPI = [{"chiave": "segreto", "tipo": "segreto", "et": "...", "aiuto": "...", "default": ...}, ...]   (facoltativo)
#   CHANGELOG = [{"v": "1.1", "data": "2026-11-04T18:00", "titolo": "...", "righe": [["n", "Titoletto", "Una frase."], ...]}, ...]   (facoltativo)
#       la cronologia del plugin, la piu' recente in alto: compare in Changelog > Plugin. Tipo riga: n = Novita', m = Migliorato, s = Sistemato.
#   def avvia(ctx): ...        (deve tornare subito; per lavori lunghi si usa un thread)
#   def ferma(ctx): ...        (facoltativa)
# Il campo «attivo» (interruttore) lo aggiunge il pannello: un plugin nuovo parte SPENTO.
# `ctx` (ContestoPlugin) e' tutto quello che il plugin puo' usare del pannello: config, dati, comandi, rotte pubbliche, registro.

PLUGIN_INTERFACCIA = 1
PLUGIN_MAX_FILE = 500_000       # byte
PLUGIN_MAX_CORPO = 65_536       # byte di una richiesta pubblica
PLUGIN_RICHIESTE_MIN = 60       # richieste al minuto, per plugin e per indirizzo
PLUGIN_ATTESA_AVVIO = 10        # secondi concessi a avvia()
_RX_NOME_PLUGIN = re.compile(r"[a-z][a-z0-9_]{0,23}")

PLUGIN = globals().get("PLUGIN") or {}               # nome -> Plug (sopravvive alla ricarica a caldo di questa parte)
PLUGIN_ROTTE = globals().get("PLUGIN_ROTTE") or {}   # "/plugin/nome/percorso" -> (nome, funzione, metodi)
_lock_plugin = globals().get("_lock_plugin") or threading.RLock()
_ritmo_plugin = globals().get("_ritmo_plugin") or {}  # (nome, ip) -> orari delle ultime richieste


class Plug:
    """Un plugin trovato nella cartella: stato = spento | attivo | errore."""
    def __init__(self, nome, file):
        self.nome, self.file = nome, file
        self.modulo = None
        self.info, self.campi = {}, []
        self.changelog = []
        self.sha = ""
        self.stato, self.errore = "errore", ""
        self.avviato = False
        self.ctx = None
        self.messaggio = ""            # una riga che il plugin puo' mostrare in Impostazioni (ctx.stato)
        self.richieste, self.ultima = 0, None
        self.ultimo_errore = ""


class Richiesta:
    """Cio' che una rotta pubblica di un plugin riceve."""
    def __init__(self, metodo, percorso, query, corpo, intestazioni, ip):
        self.metodo, self.percorso, self.query, self.corpo, self.intestazioni, self.ip = metodo, percorso, query, corpo, intestazioni, ip

    def json(self):
        """Il corpo come JSON (dict), oppure ValueError."""
        v = json.loads(self.corpo.decode("utf-8") or "{}")
        if not isinstance(v, dict):
            raise ValueError("serve un oggetto JSON")
        return v


class ContestoPlugin:
    """L'unica porta fra un plugin e il pannello. Piccola e stabile (PLUGIN_INTERFACCIA)."""
    def __init__(self, app, plug):
        self._app, self._plug = app, plug
        self.nome = plug.nome
        self.log = logging.getLogger("pdc.plugin." + plug.nome)
        self.demo = app.demo

    def config(self):
        """I valori del plugin (campi + «attivo»), con i predefiniti dove l'utente non ha scritto nulla."""
        sal = (self._app.cfg.leggi().get("plugin") or {}).get(self.nome) or {}
        out = {c["chiave"]: c.get("default") for c in self._plug.campi}
        out.update({k: v for k, v in sal.items() if k in out or k == "attivo"})
        return out

    def salva(self, chiave, valore):
        """Scrive un valore nel config.json, nella sezione del plugin (per esempio un segreto generato al primo avvio)."""
        def f(raw):
            raw.setdefault("plugin", {}).setdefault(self.nome, {})[chiave] = valore
        self._app.cfg.aggiorna(f)

    def ultimi(self):
        """{serie: (ts, numero, testo)}: gli ultimi valori letti dalla pompa e dallo Smarther."""
        return {s: (ts, num, txt) for s, ts, num, txt in self._app.db.ultimi()}

    def dashboard(self):
        """Lo stesso riepilogo che mostra la Dashboard (dict)."""
        return API["/api/dashboard"](self._app, {})

    def comando(self, cosa, acceso):
        """cosa = 'smarther' | 'riscaldamento' | 'acqua'. Stesse regole e stesso registro dei comandi del pannello."""
        return esegui_comando(self._app, cosa, acceso, "plugin " + self.nome)

    def rotta(self, percorso, funzione, metodi=("POST",)):
        """Una pagina PUBBLICA (senza login) su /plugin/<nome>/<percorso>. funzione(Richiesta) -> dict | (codice, dict) |
        (codice, testo_o_byte, tipo). Il pannello limita dimensione e frequenza delle richieste; l'autenticazione e' del plugin."""
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", percorso):
            raise ValueError("percorso non valido: solo lettere, numeri, - e _")
        PLUGIN_ROTTE[f"/plugin/{self.nome}/{percorso}"] = (self.nome, funzione, tuple(m.upper() for m in metodi))

    def stato(self, testo):
        """Una riga di stato mostrata in Impostazioni > Integrazioni."""
        self._plug.messaggio = str(testo)[:200]


def _riassunto(e):
    return f"{type(e).__name__}: {e}"[:300]


def _pulisci_changelog(grezzo):
    """La cronologia dichiarata da un plugin, ripulita (testi corti, limiti). Mai eccezioni: se e' sbagliata, resta vuota."""
    out = []
    try:
        for c in list(grezzo or [])[:60]:
            righe = [[str(x[0]) if str(x[0]) in ("n", "m", "s") else "m", str(x[1])[:80], str(x[2])[:600]]
                     for x in list(c.get("righe") or [])[:12] if isinstance(x, (list, tuple)) and len(x) >= 3]
            data = str(c.get("data", ""))[:16]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?", data):
                data = ""
            out.append({"v": str(c.get("v", ""))[:20], "data": data, "titolo": str(c.get("titolo", ""))[:80], "righe": righe})
    except Exception:
        return []
    return out


def _leggi_plugin(f):
    """Carica il file di un plugin SENZA avviarlo. Mai eccezioni: l'errore finisce nel Plug."""
    p = Plug(f.stem, f)
    try:
        if f.stat().st_size > PLUGIN_MAX_FILE:
            raise ValueError("file troppo grande")
        dati = f.read_bytes()
        p.sha = hashlib.sha256(dati).hexdigest()[:8]
        modulo = types.ModuleType("pdc_plugin_" + p.nome)
        modulo.__file__ = str(f)
        exec(compile(dati, str(f), "exec"), modulo.__dict__)
        info = getattr(modulo, "INFO", None)
        if not isinstance(info, dict):
            raise ValueError("manca INFO (un dizionario con titolo, versione, interfaccia)")
        p.info = {k: str(v)[:300] for k, v in info.items()}
        if info.get("interfaccia") != PLUGIN_INTERFACCIA:
            raise ValueError(f"pensato per l'interfaccia {info.get('interfaccia')!r}, questo pannello usa la {PLUGIN_INTERFACCIA}: "
                             "serve una versione del plugin adatta")
        if not callable(getattr(modulo, "avvia", None)):
            raise ValueError("manca la funzione avvia(ctx)")
        campi = []
        for c in getattr(modulo, "CAMPI", None) or []:
            if not isinstance(c, dict) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", str(c.get("chiave", ""))) or c.get("chiave") == "attivo":
                raise ValueError(f"campo non valido: {str(c)[:80]}")
            campi.append({"chiave": c["chiave"], "tipo": c.get("tipo", "testo"), "et": str(c.get("et", c["chiave"])),
                          "aiuto": str(c.get("aiuto", "")), "default": c.get("default"),
                          **{k: c[k] for k in ("min", "max", "opzioni") if k in c}})
        p.campi, p.modulo = campi, modulo
        p.changelog = _pulisci_changelog(getattr(modulo, "CHANGELOG", None))
        p.stato = "spento"
    except BaseException as e:            # anche SystemExit: un plugin non deve mai fermare il pannello
        if isinstance(e, KeyboardInterrupt):
            raise
        p.stato, p.errore = "errore", _riassunto(e)
        log.error("Plugin %s: non lo carico (%s)", p.nome, p.errore)
    return p


def _togli_rotte(nome):
    for k in [k for k, v in PLUGIN_ROTTE.items() if v[0] == nome]:
        del PLUGIN_ROTTE[k]


def _avvia_plugin(app, p):
    """avvia(ctx) con un limite di tempo. Se fallisce il plugin va in «errore» e le sue rotte vengono tolte."""
    ctx = ContestoPlugin(app, p)
    esito = {}

    def lavoro():
        try:
            p.modulo.avvia(ctx)
        except BaseException as e:
            esito["errore"] = _riassunto(e)
            log.exception("Plugin %s: errore in avvia()", p.nome)
    th = threading.Thread(target=lavoro, daemon=True, name="plugin-" + p.nome)
    th.start()
    th.join(PLUGIN_ATTESA_AVVIO)
    if th.is_alive():
        esito["errore"] = f"avvia() non è tornata entro {PLUGIN_ATTESA_AVVIO} secondi (per i lavori lunghi serve un thread)"
    if esito.get("errore"):
        _togli_rotte(p.nome)
        p.stato, p.errore, p.avviato = "errore", esito["errore"], False
        return
    p.ctx, p.avviato, p.stato, p.errore = ctx, True, "attivo", ""
    log.info("Plugin %s avviato", p.nome)


def _ferma_plugin(p):
    _togli_rotte(p.nome)
    f = getattr(p.modulo, "ferma", None)
    if p.avviato and callable(f):
        try:
            f(p.ctx)
        except BaseException as e:
            if isinstance(e, KeyboardInterrupt):
                raise
            log.warning("Plugin %s: errore in ferma() (%s)", p.nome, _riassunto(e))
    if p.avviato:
        log.info("Plugin %s fermato", p.nome)
    p.avviato = False
    if p.stato == "attivo":
        p.stato = "spento"


def plugin_sincronizza(app):
    """Accende i plugin con l'interruttore su «attivo», spegne gli altri."""
    with _lock_plugin:
        sal = app.cfg.leggi().get("plugin") or {}
        for p in PLUGIN.values():
            if p.modulo is None:
                continue
            voglio = bool((sal.get(p.nome) or {}).get("attivo"))
            if voglio and not p.avviato:
                _avvia_plugin(app, p)
            elif not voglio and p.avviato:
                _ferma_plugin(p)
            elif not voglio and p.stato == "errore" and p.modulo is not None:
                p.stato, p.errore = "spento", ""


def plugin_ferma_tutti():
    with _lock_plugin:
        for p in PLUGIN.values():
            if p.avviato:
                _ferma_plugin(p)


def plugin_ricarica(app):
    """Rilegge la cartella plugin e riavvia quelli accesi. -> numero di plugin trovati."""
    with _lock_plugin:
        plugin_ferma_tutti()
        PLUGIN.clear()
        PLUGIN_ROTTE.clear()
        cart = app.base / "plugin"
        if cart.is_dir():
            for f in sorted(cart.glob("*.py"))[:20]:
                if f.name.startswith(("_", ".")) or f.is_symlink() or not _RX_NOME_PLUGIN.fullmatch(f.stem):
                    continue
                PLUGIN[f.stem] = _leggi_plugin(f)
        plugin_sincronizza(app)
        return len(PLUGIN)


@api("/api/plugin/changelog")
def api_plugin_changelog(app, q):
    """Le cronologie dei plugin, per la tab Changelog > Plugin."""
    with _lock_plugin:
        return {"plugin": [{"nome": p.nome, "titolo": p.info.get("titolo") or p.nome, "versione": p.info.get("versione", ""),
                            "changelog": p.changelog} for p in PLUGIN.values() if p.changelog]}


def sezioni_plugin():
    """I riquadri di Impostazioni > Integrazioni: uno per plugin, con interruttore e campi (stessa forma delle altre sezioni)."""
    out = []
    with _lock_plugin:
        for p in PLUGIN.values():
            campi = [] if p.modulo is None else (
                [{"chiave": f"plugin.{p.nome}.attivo", "tipo": "bool", "et": "Attivo", "aiuto": "Accende o spegne il plugin.", "default": False}]
                + [{**c, "chiave": f"plugin.{p.nome}.{c['chiave']}"} for c in p.campi])
            out.append({"id": "plugin_" + p.nome, "tab": "integrazioni", "campi": campi,
                        "titolo": p.info.get("titolo") or p.nome, "nota": p.info.get("descrizione", ""),
                        "plugin": {"nome": p.nome, "file": p.file.name, "sha": p.sha, "versione": p.info.get("versione", ""),
                                   "stato": p.stato, "errore": p.errore or p.ultimo_errore, "messaggio": p.messaggio,
                                   "richieste": p.richieste, "ultima": p.ultima,
                                   "rotte": sorted(k for k, v in PLUGIN_ROTTE.items() if v[0] == p.nome)}})
    return out


def _troppo_veloce(nome, ip):
    ora = time.time()
    q = _ritmo_plugin.setdefault((nome, ip), [])
    while q and q[0] < ora - 60:
        q.pop(0)
    if len(q) >= PLUGIN_RICHIESTE_MIN:
        return True
    q.append(ora)
    if len(_ritmo_plugin) > 2000:       # non cresce senza limite
        for k in [k for k, v in _ritmo_plugin.items() if not v or v[-1] < ora - 60]:
            del _ritmo_plugin[k]
    return False


def plugin_pubblico(app, h, p, q):
    """Risponde alle rotte pubbliche dei plugin (chiamata dal server web PRIMA del controllo del login)."""
    voce = PLUGIN_ROTTE.get(p)
    plug = PLUGIN.get(voce[0]) if voce else None
    if not voce or not plug or not plug.avviato:
        if _troppo_veloce("-", ip_client(h)):      # anche gli indirizzi sbagliati hanno un limite: niente tentativi a raffica
            return h._json({"errore": "troppe richieste"}, 429)
        return h._invia(404, "Non trovato", "text/plain; charset=utf-8")
    _, funzione, metodi = voce
    if h.command not in metodi:
        return h._invia(405, "Metodo non consentito", "text/plain; charset=utf-8")
    ip = ip_client(h)
    if _troppo_veloce(plug.nome, ip):
        return h._json({"errore": "troppe richieste"}, 429)
    try:
        n = int(h.headers.get("Content-Length", "0"))
    except ValueError:
        n = -1
    if n < 0 or n > PLUGIN_MAX_CORPO:
        return h._json({"errore": "richiesta non valida" if n < 0 else "richiesta troppo grande"}, 400 if n < 0 else 413)
    corpo = h.rfile.read(n) if n else b""
    plug.richieste, plug.ultima = plug.richieste + 1, int(time.time())
    req = Richiesta(h.command, p, q, corpo, {k.lower(): v for k, v in h.headers.items()}, ip)
    try:
        r = funzione(req)
    except Exception as e:
        plug.ultimo_errore = _riassunto(e)
        log.exception("Plugin %s: errore nella rotta %s", plug.nome, p)
        return h._json({"errore": "errore interno"}, 500)
    plug.ultimo_errore = ""
    if isinstance(r, dict):
        return h._json(r)
    if isinstance(r, tuple) and len(r) == 2 and isinstance(r[1], dict):
        return h._json(r[1], int(r[0]))
    if isinstance(r, tuple) and len(r) == 3:
        return h._invia(int(r[0]), r[1], str(r[2]))
    plug.ultimo_errore = "la rotta ha risposto con un tipo non previsto"
    return h._json({"errore": "errore interno"}, 500)


@api_post("/api/plugin/ricarica")
def api_plugin_ricarica(app, corpo, h):
    n = plugin_ricarica(app)
    return {"ok": True, "plugin": n}
