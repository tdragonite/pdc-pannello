# ==========================================================================
# PEZZO 6: ACCESSO DA INTERNET CON UN TUNNEL CLOUDFLARE (procedura guidata)
# ==========================================================================

import subprocess

CF_API = "https://api.cloudflare.com/client/v4"
CF_DOWNLOAD = "https://github.com/cloudflare/cloudflared/releases/latest/download/"
TITOLI["cf_tunnel"] = "Tunnel Cloudflare"
TITOLI["cf_esterno"] = "Pannello raggiungibile da internet"

CLOUDFLARE_DEFAULT = {
    "_nota": "Compilato dalla procedura guidata (Impostazioni > Accesso da internet). Il token del tunnel permette solo di "
             "far girare QUEL tunnel: tieni comunque config.json riservato.",
    "attivo": False,
    "avvio_automatico": True,
    "hostname": "", "nome_tunnel": "", "tunnel_id": "", "account_id": "", "zone_id": "",
    "tunnel_token": "", "porta_servizio": 0,
    "percorso_cloudflared": "",
    "protocollo": "auto",
}


class ErroreCF(Exception):
    pass


def cf_chiama(token, metodo, percorso, corpo=None, query=None):
    """Una chiamata all'API Cloudflare -> result. Solleva ErroreCF con un messaggio in italiano."""
    url = CF_API + percorso + (("?" + urlencode(query)) if query else "")
    dati = json.dumps(corpo).encode() if corpo is not None else None
    h = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if dati is not None:
        h["Content-Type"] = "application/json"
    try:
        code, js = http_json(metodo, url, h, dati, timeout=30)
    except ErroreAPI as e:
        raise ErroreCF(f"Non riesco a raggiungere Cloudflare ({e.messaggio}).")
    if js.get("success") is True:
        return js.get("result")
    errs = js.get("errors") or []
    testo = "; ".join(f"{e.get('message', '?')} (codice {e.get('code', '?')})" for e in errs) or f"errore HTTP {code}"
    if code in (401, 403) or any(e.get("code") in (10000, 9109, 9106, 9103) for e in errs):
        raise ErroreCF("Cloudflare rifiuta il token: scaduto, copiato male o senza tutti i permessi richiesti. "
                       f"Dettaglio: {testo}")
    if code == 429:
        raise ErroreCF("Cloudflare dice di rallentare (troppe richieste): riprova fra un minuto.")
    raise ErroreCF(f"Cloudflare: {testo}")


def _percorso_exe(app, cfg=None):
    c = (cfg or app.cfg.leggi())["cloudflare"]
    if c.get("percorso_cloudflared"):
        p = Path(c["percorso_cloudflared"])
        return p if p.is_absolute() else app.base / p
    nome = "cloudflared.exe" if os.name == "nt" else "cloudflared"
    locale = app.base / "cloudflared" / nome
    if locale.exists():
        return locale
    import shutil
    trovato = shutil.which("cloudflared")
    return Path(trovato) if trovato else locale


def _url_download():
    macchina = (os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or "").lower()
    import platform
    m = (platform.machine() or macchina).lower()
    if sys.platform.startswith("win"):
        return CF_DOWNLOAD + ("cloudflared-windows-386.exe" if m in ("x86", "i386", "i686") else "cloudflared-windows-amd64.exe")
    if sys.platform.startswith("linux"):
        return CF_DOWNLOAD + ("cloudflared-linux-arm64" if m in ("aarch64", "arm64") else "cloudflared-linux-amd64")
    return None


def scarica_cloudflared(app):
    url = _url_download()
    if not url:
        raise ErroreCF("Su questo sistema non so scaricare cloudflared da solo: installalo a mano e scrivi il percorso in "
                       "config.json (cloudflare -> percorso_cloudflared).")
    dest = app.base / "cloudflared" / ("cloudflared.exe" if os.name == "nt" else "cloudflared")
    dest.parent.mkdir(exist_ok=True)
    tmp = dest.with_name(dest.name + ".scarico")
    try:
        req = Request(url, headers={"User-Agent": "pdc-pannello"})
        with urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            tot = 0
            while True:
                blocco = r.read(1 << 16)
                if not blocco:
                    break
                tot += len(blocco)
                if tot > 150_000_000:
                    raise ErroreCF("Il file scaricato e' troppo grande: lo scarto.")
                f.write(blocco)
    except (URLError, OSError) as e:
        tmp.unlink(missing_ok=True)
        raise ErroreCF(f"Download non riuscito ({getattr(e, 'reason', e)}). Controlla internet e il firewall, oppure scarica "
                       "cloudflared a mano da github.com/cloudflare/cloudflared/releases e metti il percorso in config.json.")
    if tot < 5_000_000:
        tmp.unlink(missing_ok=True)
        raise ErroreCF("Il file scaricato e' troppo piccolo per essere cloudflared.")
    if os.name == "nt":
        with open(tmp, "rb") as f:
            if f.read(2) != b"MZ":
                tmp.unlink(missing_ok=True)
                raise ErroreCF("Il file scaricato non e' un programma Windows valido.")
    os.replace(tmp, dest)
    if os.name != "nt":
        dest.chmod(0o755)
    return versione_cloudflared(dest)


def versione_cloudflared(exe):
    try:
        r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or r.stderr).strip().splitlines()[0][:80]
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


class TunnelCF:
    """Tiene acceso cloudflared (processo figlio, senza finestra) e lo rilancia se si ferma."""

    def __init__(self, app):
        self.app = app
        self.proc = None
        self.voluto = False
        self.thread = None
        self.lock = threading.RLock()
        self.log_path = cartella_log(app.base) / "cloudflared.log"
        self.pid_path = app.base / "cloudflared" / "cloudflared.pid"
        try:
            self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.partito_il = 0
        self.fallimenti = 0
        self.fermo = threading.Event()
        self.ultimo_errore = None
        self.esterno_ts = 0       # ultimo controllo dall'esterno
        self.esterno_ko = 0       # controlli falliti di fila

    # ----- ciclo di vita -----
    def _cfg(self):
        return self.app.cfg.leggi()["cloudflare"]

    def pronto(self):
        c = self._cfg()
        return bool(c["tunnel_token"] and c["hostname"] and _percorso_exe(self.app).exists())

    def _pulisci_vecchio(self):
        """Se una copia precedente del pannello e' stata chiusa a forza, il suo cloudflared puo' essere rimasto."""
        try:
            pid = int(self.pid_path.read_text().strip())
        except (OSError, ValueError):
            return
        try:
            if os.name == "nt":
                # l'output di tasklist usa la tabella di caratteri di Windows (non UTF-8, ha le lettere accentate): lo leggo
                # come byte e cerco solo il nome del programma, cosi' nessun carattere strano puo' far fallire l'avvio
                r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                   timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if b"cloudflared" in (r.stdout or b"").lower():
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=15,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                if "cloudflared" in Path(f"/proc/{pid}/cmdline").read_text().lower():
                    os.kill(pid, 15)
        except Exception as e:      # ripulire un vecchio processo non deve mai impedire al pannello di partire
            log.warning("cloudflared: non riesco a controllare il processo vecchio (%s)", e)
        try:
            self.pid_path.unlink(missing_ok=True)
        except OSError:
            pass

    def avvia(self):
        with self.lock:
            if self.app.demo:
                raise ErroreCF("In modalita' demo il tunnel non parte.")
            c = self._cfg()
            exe = _percorso_exe(self.app)
            if not c["tunnel_token"]:
                raise ErroreCF("Il tunnel non e' ancora stato creato: usa la procedura guidata.")
            if not exe.exists():
                raise ErroreCF("Manca il programma cloudflared: scaricalo dalla procedura guidata.")
            self.voluto = True
            self.app.cfg.aggiorna(lambda r: r.setdefault("cloudflare", {}).update(attivo=True))
            if self.proc and self.proc.poll() is None:
                return
            self._lancia(exe, c)
            if not self.thread or not self.thread.is_alive():
                self.fermo.clear()
                self.thread = threading.Thread(target=self._guardia, daemon=True, name="tunnel-cf")
                self.thread.start()

    def _lancia(self, exe, c):
        self._pulisci_vecchio()
        env = dict(os.environ)
        env["TUNNEL_TOKEN"] = c["tunnel_token"]      # via ambiente: cosi' non compare nell'elenco dei processi
        args = [str(exe), "tunnel", "--no-autoupdate", "--loglevel", "info"]
        if c.get("protocollo") in ("http2", "quic"):
            args += ["--protocol", c["protocollo"]]
        args.append("run")
        try:
            if self.log_path.exists() and self.log_path.stat().st_size > 500_000:     # oltre 500 KB: si tiene la parte finale, da un inizio di avvio
                dati = self.log_path.read_bytes()[-400_000:]
                i = dati.find(b"\n--- avvio")
                self.log_path.write_bytes(dati[i + 1:] if i >= 0 else dati)
        except OSError:
            pass
        logf = open(self.log_path, "ab")
        logf.write(f"\n--- avvio {time.strftime('%d/%m/%Y %H:%M:%S')} ---\n".encode())
        logf.flush()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            self.proc = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                                         creationflags=flags, cwd=str(self.app.base))
        except OSError as e:
            logf.close()
            raise ErroreCF(f"Non riesco ad avviare cloudflared: {e}")
        logf.close()
        self.partito_il = time.time()
        try:
            self.pid_path.write_text(str(self.proc.pid))
        except OSError:
            pass
        log.info("cloudflared avviato (pid %s)", self.proc.pid)

    def ferma(self, spegni=True):
        with self.lock:
            self.voluto = False
            self.fermo.set()
            if spegni and not self.app.demo:
                try:
                    self.app.cfg.aggiorna(lambda r: r.setdefault("cloudflare", {}).update(attivo=False))
                except ErroreConfig:
                    pass
            p, self.proc = self.proc, None
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    p.kill()
            self.pid_path.unlink(missing_ok=True)
            self.app.eventi.chiudi("cf_tunnel")

    def chiudi_con_il_pannello(self):
        """All'uscita del pannello ferma il processo ma ricorda che il tunnel e' 'attivo'."""
        self.ferma(spegni=False)

    def avvia_se_serve(self):
        c = self._cfg()
        if c["attivo"] and c["avvio_automatico"] and self.pronto():
            try:
                self.avvia()
            except ErroreCF as e:
                log.warning("Tunnel Cloudflare: %s", e)
                self.ultimo_errore = str(e)

    def _guardia(self):
        """Controllo del tunnel: se per un imprevisto (per esempio database non scrivibile) si ferma, riparte."""
        while not self.fermo.is_set():
            try:
                return self._guardia_interna()
            except Exception:
                log.exception("errore nel controllo del tunnel Cloudflare; riprovo tra 30 secondi")
                self.fermo.wait(30)

    def _guardia_interna(self):
        attesa = 5
        while not self.fermo.wait(5):
            with self.lock:
                if not self.voluto:
                    return
                p = self.proc
                if p is None or p.poll() is not None:
                    self.fallimenti += 1
                    codice = None if p is None else p.returncode
                    self.ultimo_errore = f"cloudflared si e' fermato (codice {codice}). " + self._ultima_riga_problema()
                    log.warning("%s", self.ultimo_errore)
                    if self.fallimenti >= 3:
                        self.app.eventi.apri("cf_tunnel", f"Il tunnel Cloudflare continua a fermarsi: {self._ultima_riga_problema()}", "avviso")
                    try:
                        self._lancia(_percorso_exe(self.app), self._cfg())
                    except ErroreCF as e:
                        self.ultimo_errore = str(e)
                    self.fermo.wait(min(300, attesa))
                    attesa = min(300, attesa * 2)
                    continue
                if self.collegato():
                    self.fallimenti, attesa = 0, 5
                    self.app.eventi.chiudi("cf_tunnel")
                elif time.time() - self.partito_il > 180:
                    self.app.eventi.apri("cf_tunnel", "Il tunnel e' avviato ma non risulta collegato a Cloudflare da oltre 3 minuti. "
                                         + self._ultima_riga_problema(), "avviso")
            try:
                self._controllo_esterno()
            except Exception:
                log.exception("controllo del pannello da internet in errore")

    # ----- il pannello e' raggiungibile da internet? -----
    CONTROLLO_OGNI = 120        # secondi fra un controllo e l'altro
    CONTROLLO_FALLITI = 3       # controlli falliti di fila prima di avvisare (circa 6 minuti)

    def _controllo_esterno(self):
        """Prova il percorso intero, come lo farebbe il tuo iPhone da fuori casa: da qui a Cloudflare e di nuovo al pannello.
        Usa la pagina pubblica manifest.webmanifest (non serve la password). Avvisa solo dopo alcuni fallimenti di fila, per
        non disturbare con un singolo intoppo; quando torna tutto a posto l'avviso si chiude da solo."""
        if self.app.demo or not self.proc or self.proc.poll() is not None or time.time() - self.partito_il < 120:
            return
        if time.time() - self.esterno_ts < self.CONTROLLO_OGNI:
            return
        self.esterno_ts = time.time()
        host = self._cfg()["hostname"]
        if not host:
            return
        motivo = ""
        try:
            code, js = http_json("GET", f"https://{host}/manifest.webmanifest",
                                 {"User-Agent": "pdc-pannello", "Accept": "application/json"}, timeout=15)
            if code == 200 and isinstance(js, dict) and "start_url" in js:
                self.esterno_ko = 0
                self.app.eventi.chiudi("cf_esterno")
                return
            if code in (530, 1033):
                motivo = f"Cloudflare non trova il tunnel (codice {code})"
            elif code in (502, 504, 521, 522, 523, 524):
                motivo = f"Cloudflare risponde ma il pannello no (codice {code})"
            else:
                motivo = f"risposta inattesa (codice {code})"
        except ErroreAPI as e:
            motivo = str(e)
        self.esterno_ko += 1
        if self.esterno_ko >= self.CONTROLLO_FALLITI:
            self.app.eventi.apri("cf_esterno", f"Da internet il pannello non risponde ({host}): {motivo}.", "avviso")

    # ----- stato -----
    def _coda_log(self, n=12000):
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - n))
                testo = f.read().decode("utf-8", "replace")
            return testo[testo.rfind("--- avvio"):] if "--- avvio" in testo else testo
        except OSError:
            return ""

    def collegato(self):
        return "Registered tunnel connection" in self._coda_log()

    def _ultima_riga_problema(self):
        for riga in reversed(self._coda_log().splitlines()):
            if " ERR " in riga or "error" in riga.lower() or "failed" in riga.lower():
                return re.sub(r"^\S+\s+\w+\s+", "", riga).strip()[:220]
        return ""

    def stato(self):
        c = self._cfg()
        exe = _percorso_exe(self.app)
        in_esecuzione = bool(self.proc and self.proc.poll() is None)
        s = {"configurato": bool(c["tunnel_token"] and c["hostname"]), "hostname": c["hostname"],
             "indirizzo": f"https://{c['hostname']}" if c["hostname"] else "", "nome_tunnel": c["nome_tunnel"],
             "avvio_automatico": c["avvio_automatico"], "attivo": c["attivo"],
             "programma": exe.exists(), "in_esecuzione": in_esecuzione,
             "collegato": in_esecuzione and self.collegato(), "problema": self.ultimo_errore if not in_esecuzione else self._ultima_riga_problema() if not self.collegato() else "",
             "porta_diversa": bool(c["porta_servizio"] and int(c["porta_servizio"]) != int(self.app.cfg.leggi()["generale"]["porta"])),
             "porta_servizio": c["porta_servizio"], "demo": self.app.demo}
        return s


def tunnel(app):
    t = getattr(app, "tunnel", None)
    if t is None:
        t = app.tunnel = TunnelCF(app)
    return t


# --------------------------------------------------------------------------
# API della procedura guidata
# --------------------------------------------------------------------------

def _controllo_token(corpo):
    tok = str(corpo.get("token", "")).strip()
    if len(tok) < 20 or any(ch.isspace() for ch in tok):
        raise ErroreCF("Il token sembra incompleto: copialo per intero (senza spazi) dalla pagina di Cloudflare.")
    return tok


def _risposta_errore(e):
    return {"ok": False, "messaggio": str(e)}


@api("/api/cloudflare")
def api_cloudflare(app, q):
    return tunnel(app).stato()


@api_post("/api/cloudflare/verifica")
def cf_verifica(app, corpo, h):
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo la procedura non è disponibile."}
    try:
        tok = _controllo_token(corpo)
        zone = cf_chiama(tok, "GET", "/zones", query={"per_page": 50, "status": "active"}) or []
    except ErroreCF as e:
        return _risposta_errore(e)
    if not zone:
        return {"ok": False, "messaggio": "Il token funziona ma non vede nessun dominio. Nel token, alla voce 'Zone Resources' "
                                          "scegli il tuo dominio (o 'All zones') e aggiungi il permesso Zone > Zone > Read."}
    return {"ok": True, "zone": [{"id": z["id"], "nome": z["name"], "account_id": (z.get("account") or {}).get("id", ""),
                                  "account_nome": (z.get("account") or {}).get("name", "")} for z in zone]}


@api_post("/api/cloudflare/crea")
def cf_crea(app, corpo, h):
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo la procedura non è disponibile."}
    passi = []

    def ok(t):
        passi.append({"t": t, "ok": True})
    tunnel_id = account = None
    tok = None
    try:
        tok = _controllo_token(corpo)
        sub = str(corpo.get("sottodominio", "")).strip().lower()
        nome = str(corpo.get("nome_tunnel", "")).strip() or "pannello-pdc"
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?", sub):
            raise ErroreCF("Il nome del sottodominio può contenere solo lettere minuscole, numeri e trattini (es. pdc).")
        if not re.fullmatch(r"[A-Za-z0-9 _.-]{1,60}", nome):
            raise ErroreCF("Il nome del tunnel può contenere solo lettere, numeri, spazi, trattino, punto e underscore.")
        zona = cf_chiama(tok, "GET", f"/zones/{corpo.get('zone_id', '')}")
        account = (zona.get("account") or {}).get("id")
        if not account:
            raise ErroreCF("Non riesco a capire a quale account appartiene il dominio.")
        host = f"{sub}.{zona['name']}"
        ok(f"Dominio verificato: {zona['name']}")
        esistenti = cf_chiama(tok, "GET", f"/zones/{zona['id']}/dns_records", query={"name": host}) or []
        record = None
        for r in esistenti:
            if r.get("type") == "CNAME" and str(r.get("content", "")).endswith(".cfargotunnel.com"):
                record = r
            else:
                raise ErroreCF(f"Esiste già un record DNS '{r.get('type')}' per {host}: non lo tocco. Scegli un altro sottodominio "
                               "oppure cancella quel record dal pannello DNS di Cloudflare.")
        doppi = cf_chiama(tok, "GET", f"/accounts/{account}/cfd_tunnel", query={"name": nome, "is_deleted": "false"}) or []
        if doppi:
            raise ErroreCF(f"Esiste già un tunnel chiamato '{nome}' nel tuo account: scegli un altro nome.")
        ok("Nome e sottodominio liberi")
        t = cf_chiama(tok, "POST", f"/accounts/{account}/cfd_tunnel", {"name": nome, "config_src": "cloudflare"})
        tunnel_id = t["id"]
        tunnel_token = t.get("token") or cf_chiama(tok, "GET", f"/accounts/{account}/cfd_tunnel/{tunnel_id}/token")
        ok(f"Tunnel '{nome}' creato")
        porta = int(app.cfg.leggi()["generale"]["porta"])
        cf_chiama(tok, "PUT", f"/accounts/{account}/cfd_tunnel/{tunnel_id}/configurations",
                  {"config": {"ingress": [{"hostname": host, "service": f"http://127.0.0.1:{porta}", "originRequest": {}},
                                          {"service": "http_status:404"}]}})
        ok(f"Il tunnel porta {host} al pannello (porta {porta})")
        contenuto = f"{tunnel_id}.cfargotunnel.com"
        dati_dns = {"type": "CNAME", "name": host, "content": contenuto, "proxied": True}
        if record:
            cf_chiama(tok, "PUT", f"/zones/{zona['id']}/dns_records/{record['id']}", dati_dns)
        else:
            cf_chiama(tok, "POST", f"/zones/{zona['id']}/dns_records", dati_dns)
        ok("Record DNS creato")
    except ErroreCF as e:
        if tunnel_id and account and tok:               # a meta' strada: riporto tutto com'era
            try:
                cf_chiama(tok, "DELETE", f"/accounts/{account}/cfd_tunnel/{tunnel_id}")
                passi.append({"t": "Ho annullato il tunnel creato a metà", "ok": True})
            except ErroreCF:
                passi.append({"t": f"Attenzione: il tunnel '{nome}' è rimasto nel tuo account Cloudflare, cancellalo a mano", "ok": False})
        passi.append({"t": str(e), "ok": False})
        return {"ok": False, "messaggio": str(e), "passi": passi}

    def salva(raw):
        raw.setdefault("cloudflare", {}).update(
            hostname=host, nome_tunnel=nome, tunnel_id=tunnel_id, account_id=account, zone_id=zona["id"],
            tunnel_token=tunnel_token, porta_servizio=porta, attivo=False)
    app.cfg.aggiorna(salva)
    ok("Impostazioni salvate (il token di Cloudflare NON è stato conservato)")
    log.info("Tunnel Cloudflare creato per %s", host)
    return {"ok": True, "passi": passi, "indirizzo": f"https://{host}"}


@api_post("/api/cloudflare/scarica")
def cf_scarica(app, corpo, h):
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo non scarico nulla."}
    try:
        v = scarica_cloudflared(app)
    except ErroreCF as e:
        return _risposta_errore(e)
    return {"ok": True, "versione": v or "installato"}


@api_post("/api/cloudflare/avvia")
def cf_avvia(app, corpo, h):
    try:
        t = tunnel(app)
        t.ultimo_errore = None
        t.avvia()
    except ErroreCF as e:
        return _risposta_errore(e)
    return {"ok": True}


@api_post("/api/cloudflare/ferma")
def cf_ferma(app, corpo, h):
    tunnel(app).ferma()
    return {"ok": True}


@api_post("/api/cloudflare/telegram")
def cf_telegram(app, corpo, h):
    """Manda su Telegram l'indirizzo per raggiungere il pannello da fuori casa."""
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo non si invia nulla."}
    c = app.cfg.leggi().get("cloudflare") or {}
    if not c.get("hostname"):
        return {"ok": False, "messaggio": "L'accesso da internet non è ancora configurato."}
    tg = getattr(app, "telegram", None) or Telegram(app)
    _, _, err = tg.credenziali()
    if err:
        return {"ok": False, "messaggio": err}
    nome = esc_html(tg.cfg().get("nome_impianto") or "Pompa di calore")
    ok, msg = tg.invia_ora(f"🔗 <b>{nome}</b>\nPannello da fuori casa:\nhttps://{esc_html(c['hostname'])}")
    return {"ok": ok, "messaggio": "Inviato: guarda Telegram." if ok else msg}


@api_post("/api/cloudflare/riavvia")
def cf_riavvia(app, corpo, h):
    try:
        t = tunnel(app)
        t.ferma(spegni=False)
        t.ultimo_errore = None
        t.avvia()
    except ErroreCF as e:
        return _risposta_errore(e)
    return {"ok": True}


@api_post("/api/cloudflare/opzioni")
def cf_opzioni(app, corpo, h):
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo non si salva nulla."}
    val = bool(corpo.get("avvio_automatico"))
    app.cfg.aggiorna(lambda raw: raw.setdefault("cloudflare", {}).update(avvio_automatico=val))
    return {"ok": True}


@api_post("/api/cloudflare/rimuovi")
def cf_rimuovi(app, corpo, h):
    """Ferma il tunnel e lo toglie dal pannello. Con un token Cloudflare cancella anche DNS e tunnel online."""
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo non si cambia nulla."}
    c = app.cfg.leggi()["cloudflare"]
    tunnel(app).ferma()
    passi, avviso = [], None
    tok = str(corpo.get("token", "")).strip()
    if tok and c["tunnel_id"]:
        try:
            tok = _controllo_token(corpo)
            for r in cf_chiama(tok, "GET", f"/zones/{c['zone_id']}/dns_records", query={"name": c["hostname"]}) or []:
                if r.get("type") == "CNAME" and str(r.get("content", "")).endswith(".cfargotunnel.com"):
                    cf_chiama(tok, "DELETE", f"/zones/{c['zone_id']}/dns_records/{r['id']}")
            passi.append("Record DNS cancellato")
            try:    # le connessioni appena chiuse possono risultare ancora attive per qualche secondo
                cf_chiama(tok, "DELETE", f"/accounts/{c['account_id']}/cfd_tunnel/{c['tunnel_id']}/connections")
            except ErroreCF:
                pass
            cf_chiama(tok, "DELETE", f"/accounts/{c['account_id']}/cfd_tunnel/{c['tunnel_id']}")
            passi.append("Tunnel cancellato da Cloudflare")
        except ErroreCF as e:
            avviso = f"Online non sono riuscito a cancellare tutto ({e}). Puoi farlo a mano dal sito di Cloudflare."
    elif c["tunnel_id"]:
        avviso = ("Ho spento il tunnel qui, ma su Cloudflare restano il tunnel e il record DNS: cancellali dal loro sito "
                  "(Zero Trust > Networks > Tunnels, e DNS del dominio) oppure rifai questo passaggio dando un token.")
    app.cfg.aggiorna(lambda raw: raw.setdefault("cloudflare", {}).update(
        hostname="", nome_tunnel="", tunnel_id="", account_id="", zone_id="", tunnel_token="", porta_servizio=0, attivo=False))
    log.info("Tunnel Cloudflare rimosso dal pannello")
    return {"ok": True, "passi": passi, "avviso": avviso}


def avvia_tunnel_se_serve(app):
    if not app.demo:
        tunnel(app).avvia_se_serve()


aggiungi_default_pezzi()
