# ==========================================================================
# PEZZO 4: TELEGRAM E SENTINELLA (controlli automatici)
# ==========================================================================

import fnmatch

TITOLI = {
    "st_conn_rete": "Collegamento a SmartThings (internet)",
    "st_conn_auth": "Accesso a SmartThings (token)",
    "st_conn_pagamento": "Servizio SmartThings (piano a pagamento?)",
    "st_conn_limite": "Limite di richieste SmartThings",
    "st_conn_server": "Server SmartThings",
    "st_conn_richiesta": "Richiesta a SmartThings",
    "nt_conn_rete": "Collegamento a Netatmo (internet)",
    "nt_conn_auth": "Accesso a Netatmo (token)",
    "nt_conn_pagamento": "Servizio Netatmo",
    "nt_conn_limite": "Limite di richieste Netatmo",
    "nt_conn_server": "Server Netatmo",
    "nt_conn_richiesta": "Richiesta a Netatmo",
    "st_config": "Configurazione SmartThings",
    "nt_config": "Configurazione Netatmo",
    "st_script": "Script (lettura SmartThings)",
    "nt_script": "Script (lettura Netatmo)",
    "pannello_script": "Controlli automatici del pannello",
    "st_pat": "Token personale SmartThings",
    "st_pdc_offline": "Pompa di calore offline",
    "st_ciclo_formato": "Formato dei dati ciclo",
    "nessun_dato": "Dati dalla PdC",
    "pdc_ferma_con_richiesta": "Pompa ferma con richiesta di calore",
    "casa_fredda": "Temperatura in casa",
    "umidita_alta": "Umidità alta",
    "umidita_molto_alta": "Umidità molto alta",
    "cicli_brevi": "Cicli troppo brevi",
    "portata_bassa": "Portata dell'acqua",
    "mandata_lontana": "Mandata lontana dalla curva",
    "acs_bassa": "Acqua calda sanitaria",
    "cop_basso": "Efficienza (COP)",
    "sbrinamenti_frequenti": "Sbrinamenti frequenti",
    "sbrinamento_inizio": "Sbrinamento iniziato",
    "sbrinamento_fine": "Sbrinamento finito",
    "finestre_chiudi": "Puoi chiudere le finestre",   # VENT
}

MESSAGGI_DEFAULT = {
    "_nota": "Testi dei messaggi Telegram (HTML semplice: <b>grassetto</b>, <i>corsivo</i>). Segnaposto: "
             "{impianto} {testo} {titolo} {codice} {ora} {data} {durata}. Per cambiare il testo di UN solo "
             "allarme usa per_codice, es.: \"per_codice\": {\"casa_fredda\": {\"apertura\": \"Brrr! {testo}\"}}",
    "apertura_errore": "🚨 <b>{impianto}</b>\n{testo}\n<i>dalle {ora}</i>",
    "apertura_avviso": "⚠️ <b>{impianto}</b>\n{testo}\n<i>dalle {ora}</i>",
    "ripristino": "✅ <b>{impianto}</b>\n{titolo}: tutto di nuovo regolare.\n<i>il problema è durato {durata}</i>",
    "promemoria": "⏰ <b>{impianto}</b>\nCi siamo ancora: {testo}\n<i>da {durata}</i>",
    "riepilogo": "☀️ <b>{impianto} · ieri, {data}</b>\n"
                 "⚡ Energia: <b>{energia} kWh</b> · COP <b>{cop}</b>\n"
                 "🔥 Riscaldamento {ore_casa} h · 🚿 Acqua calda {ore_acs} h ({cicli} cicli)\n"
                 "🏠 In casa {casa_min}–{casa_max} °C · fuori {esterna} °C\n{anomalie}",
    "prova": "👋 <b>{impianto}</b>\nProva riuscita! Da qui arriveranno gli avvisi del pannello.",
    "sbrinamento_inizio": "🧊 <b>{impianto}</b>\nSbrinamento iniziato: esterna {esterna}\n<i>dalle {ora}</i>",
    "sbrinamento_fine": "✅ <b>{impianto}</b>\nSbrinamento finito\n<i>dopo {durata}, {consumo}</i>",
    "finestre_chiudi": "🪟 <b>{impianto}</b>\nPuoi chiudere le finestre\n<i>umidità scesa di {calo} punti, ora {um}</i>",   # VENT
    "troppi": "🤐 <b>{impianto}</b>\nStanno arrivando troppi messaggi: per un po' ne salto alcuni (li trovi sempre nel pannello).",
    "per_codice": {},
}

ALLARMI_DEFAULT = {
    "_nota": "Ogni allarme si spegne con \"attivo\": false. I minuti sono 'da quanto tempo la condizione deve durare'. "
             "Per spegnere anche gli allarmi di collegamento aggiungi una voce col codice (o con * : \"st_conn_*\"): "
             "{\"attivo\": false}. Gli allarmi compaiono sempre anche nel pannello; qui decidi solo cosa va su Telegram.",
    "nessun_dato": {"attivo": True, "minuti": 20},
    "pdc_ferma_con_richiesta": {"attivo": True, "minuti": 60},
    "casa_fredda": {"attivo": True, "sotto_gradi": 17.0, "minuti": 30},
    "umidita_alta": {"attivo": True},
    "umidita_molto_alta": {"attivo": True},
    "cicli_brevi": {"attivo": True, "durata_minima_min": 10, "quanti": 4, "ore": 3},
    "portata_bassa": {"attivo": True, "sotto_lmin": 6.0, "minuti": 15},
    # sopra la curva la PdC ci sta spesso (il compressore resta a bassa frequenza: mediana +8 °C sui dati reali), sotto e' raro e vero
    "mandata_lontana": {"attivo": True, "scarto_sopra_gradi": 12.0, "scarto_gradi": 8.0, "minuti": 40},
    "acs_bassa": {"attivo": True, "sotto_gradi": 35.0, "minuti": 30},
    "cop_basso": {"attivo": True, "sotto": 2.0, "ore_minime": 3},
    "sbrinamenti_frequenti": {"attivo": True, "quanti": 6, "ore": 3},
    "sbrinamento_inizio": {"attivo": True},     # non sono anomalie: avvisi informativi, sempre senza suoneria
    "sbrinamento_fine": {"attivo": True},
    "finestre_chiudi": {"attivo": True},        # VENT: informativo, ma con suoneria (serve proprio quando non guardi il telefono)
}

TELEGRAM_DEFAULT = {
    "_nota": "Puoi scrivere bot_token e chat_id qui, OPPURE indicare un tuo file .json che li contiene "
             "(file_credenziali, con i nomi delle chiavi). Prova: python pdc_pannello.py telegram-prova",
    "abilitato": True,
    "bot_token": "", "chat_id": "",
    "file_credenziali": "", "chiave_token": "bot_token", "chiave_chat": "chat_id",
    "nome_impianto": "Pompa di calore",
    "promemoria_ore": 6,
    "max_messaggi_ora": 20,
    "silenzio_notturno": {"attivo": False, "da": "23:00", "a": "07:00", "anche_errori": False, "oscillazione_gradi": 2.0},
    "riepilogo_mattina": {"attivo": False, "ora": "08:00"},
    "messaggi": MESSAGGI_DEFAULT,
}


def _cerca_chiave(dati, percorso):
    cur = dati
    for p in str(percorso).split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur if isinstance(cur, (str, int)) and str(cur).strip() else None


def _chiavi_file(dati, prefisso=""):
    out = []
    if isinstance(dati, dict):
        for k, v in dati.items():
            out.append(prefisso + str(k))
            if isinstance(v, dict):
                out += _chiavi_file(v, prefisso + str(k) + ".")
    return out


class Telegram:
    """Invia i messaggi in un thread a parte: se Telegram non risponde il pannello non si blocca."""

    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.coda = []                       # elementi: dict(due, tipo, codice, livello, testo, tentativi)
        self.ult_invio = []                  # timestamp degli ultimi invii (limite orario)
        self.troppi_ts = 0
        self.stato = {"ultimo_ok": None, "ultimo_errore": None, "messaggio": None}
        self.promemoria = {}                 # codice -> ts dell'ultimo messaggio (apertura o promemoria)
        try:
            self.notificati = set(json.loads(app.db.meta_get("tg_notificati", "[]")))
        except ValueError:
            self.notificati = set()
        self.fermo = threading.Event()
        self.thread = threading.Thread(target=self._lavora, daemon=True, name="telegram")
        self._cache_file = (None, None, None)

    # ----- configurazione -----
    def cfg(self):
        return self.app.cfg.leggi()["telegram"]

    def credenziali(self):
        """-> (token, chat, errore). Il file dell'utente ha la precedenza se indicato."""
        c = self.cfg()
        f = (c.get("file_credenziali") or "").strip()
        if f:
            p = Path(f)
            p = p if p.is_absolute() else self.app.base / p
            try:
                mt = p.stat().st_mtime
                if self._cache_file[0] != (str(p), mt):
                    self._cache_file = ((str(p), mt), json.loads(p.read_text(encoding="utf-8-sig")), None)
                dati = self._cache_file[1]
            except OSError:
                return None, None, f"Non trovo il file delle credenziali Telegram: {p}"
            except ValueError:
                return None, None, f"Il file {p.name} non e' un JSON valido."
            tok = _cerca_chiave(dati, c.get("chiave_token") or "bot_token")
            chat = _cerca_chiave(dati, c.get("chiave_chat") or "chat_id")
            if tok is None or chat is None:
                return None, None, (f"In {p.name} non trovo le chiavi '{c.get('chiave_token')}' / "
                                    f"'{c.get('chiave_chat')}'. Chiavi presenti: {', '.join(_chiavi_file(dati)) or 'nessuna'}. "
                                    "Correggi chiave_token e chiave_chat in config.json.")
            return str(tok).strip(), str(chat).strip(), None
        tok, chat = (c.get("bot_token") or "").strip(), str(c.get("chat_id") or "").strip()
        if not tok or not chat:
            return None, None, "Telegram non e' configurato (bot_token/chat_id vuoti)."
        return tok, chat, None

    def attivo(self):
        c = self.cfg()
        if not c.get("abilitato", True):
            return False
        return self.credenziali()[0] is not None

    def allarme_attivo(self, codice):
        al = self.app.cfg.leggi()["allarmi"]
        if codice in al and isinstance(al[codice], dict):
            return bool(al[codice].get("attivo", True))
        for k, v in al.items():
            if "*" in k and isinstance(v, dict) and fnmatch.fnmatch(codice, k):
                return bool(v.get("attivo", True))
        return True

    # ----- testi -----
    def formatta(self, tipo, codice, livello, testo, durata_s=0, extra=None):
        c = self.cfg()
        m = c["messaggi"]
        chiave = {"apertura": "apertura_errore" if livello == "errore" else "apertura_avviso"}.get(tipo, tipo)
        sp = (m.get("per_codice") or {}).get(codice, {})
        modello = sp.get(tipo) or sp.get(chiave) or m.get(chiave) or MESSAGGI_DEFAULT.get(chiave, "{testo}")
        titoli = dict(TITOLI)
        titoli.update(m.get("titoli", {}) if isinstance(m.get("titoli"), dict) else {})
        ora = time.time()
        valori = {"impianto": esc_html(c["nome_impianto"]), "testo": esc_html(testo), "codice": codice,
                  "titolo": esc_html(titoli.get(codice, codice)), "ora": time.strftime("%H:%M", time.localtime(ora)),
                  "data": time.strftime("%d/%m/%Y", time.localtime(ora)), "durata": durata_it(durata_s)}
        if extra:
            valori.update(extra)
        try:
            return modello.format_map(_Sicuro(valori))
        except (ValueError, IndexError, AttributeError, KeyError, TypeError):     # modello scritto male in config.json: il messaggio parte lo stesso
            return f"{valori['impianto']}: {valori['testo']}"

    # ----- coda -----
    def accoda(self, tipo, codice, livello, testo, ritardo=0, durata_s=0, extra=None, forza=False, muto=False):
        with self.lock:
            self.coda.append({"due": time.time() + ritardo, "tipo": tipo, "codice": codice, "livello": livello,
                              "testo": testo, "durata_s": durata_s, "extra": extra, "tentativi": 0, "forza": forza,
                              "muto": muto})

    def ascolta(self, tipo, codice, livello, testo):
        """Registrato in Eventi.ascoltatori: arriva da tutte le anomalie (script, collegamenti, sentinella)."""
        if not self.allarme_attivo(codice):
            return
        if tipo == "apertura":
            r = self.app.cfg.leggi()["allarmi"].get(codice, {})
            ritardo = int(r.get("ritardo_minuti", 0) or 0) * 60 if isinstance(r, dict) else 0
            self.accoda("apertura", codice, livello, testo, ritardo)
        elif tipo == "ripristino":
            with self.lock:
                in_attesa = [e for e in self.coda if e["codice"] == codice and e["tipo"] == "apertura"]
                for e in in_attesa:      # risolto prima ancora di avvisare: niente messaggi
                    self.coda.remove(e)
            if codice in self.notificati and not in_attesa:
                self.accoda("ripristino", codice, "info", testo, 0, self._durata_evento(codice))

    def _durata_evento(self, codice):
        with closing(self.app.db._c()) as c:
            r = c.execute("SELECT ts, chiuso_ts FROM eventi WHERE codice=? ORDER BY id DESC LIMIT 1", (codice,)).fetchone()
        return max(0, (r[1] or int(time.time())) - r[0]) if r else 0

    def _segna(self, codice, si):
        if si:
            self.notificati.add(codice)
        else:
            self.notificati.discard(codice)
        self.app.db.meta_set("tg_notificati", json.dumps(sorted(self.notificati)))

    def silenzioso(self, livello):
        s = self.cfg()["silenzio_notturno"]
        if not s.get("attivo"):
            return False
        if livello == "errore" and not s.get("anche_errori"):
            return False
        try:
            da = _minuti(s["da"])
            a = _minuti(s["a"])
        except (ValueError, KeyError):
            return False
        lt = time.localtime()
        m = lt.tm_hour * 60 + lt.tm_min
        return (da <= m < a) if da < a else (m >= da or m < a)

    # ----- invio -----
    def _http(self, token, chat, testo, silenzioso, html=True):
        dati = {"chat_id": chat, "text": testo, "disable_web_page_preview": "true"}
        if html:
            dati["parse_mode"] = "HTML"
        if silenzioso:
            dati["disable_notification"] = "true"
        return http_json("POST", f"{TG_BASE}{token}/sendMessage",
                         {"Content-Type": "application/x-www-form-urlencoded"}, dati, timeout=20)

    def invia_ora(self, testo, silenzioso=False):
        """Invio immediato. -> (ok, messaggio). Mai il token nei messaggi di errore."""
        tok, chat, err = self.credenziali()
        if err:
            return False, err
        try:
            code, js = self._http(tok, chat, testo, silenzioso)
            if code == 400 and "parse" in str(js.get("description", "")).lower():
                code, js = self._http(tok, chat, re.sub(r"<[^>]+>", "", testo), silenzioso, html=False)
        except ErroreAPI as e:
            return False, e.messaggio
        if code == 200 and js.get("ok"):
            self.stato.update(ultimo_ok=int(time.time()), messaggio=None)
            return True, "inviato"
        d = js.get("description") or js.get("raw") or f"errore {code}"
        if code in (401, 404):
            d += " (token del bot sbagliato?)"
        elif code == 400 and "chat" in d.lower():
            d += " (chat_id sbagliato, oppure non hai ancora scritto al bot?)"
        elif code == 403:
            d += " (il bot e' stato bloccato o non ha il permesso di scriverti)"
        elif code == 429:
            d += f" (riprovo tra {js.get('parameters', {}).get('retry_after', '?')} s)"
        return False, f"Telegram: {d}"

    def _limite_ok(self, forza):
        c = self.cfg()
        adesso = time.time()
        self.ult_invio = [t for t in self.ult_invio if adesso - t < 3600]
        if forza or len(self.ult_invio) < int(c["max_messaggi_ora"]):
            return True
        if adesso - self.troppi_ts > 3600:
            self.troppi_ts = adesso
            self.invia_ora(self.formatta("troppi", "troppi", "avviso", ""))
        return False

    def _lavora(self):
        while not self.fermo.wait(2):
            try:
                self._giro()
            except Exception:
                log.exception("errore nel thread Telegram")

    def _giro(self):
        adesso = time.time()
        with self.lock:
            pronti = [e for e in self.coda if e["due"] <= adesso]
        for e in pronti:
            if e["tipo"] == "apertura" and not self.app.db.evento_aperto(e["codice"]):
                self._togli(e)          # si e' risolto da solo durante il ritardo: nessun messaggio
                continue
            if not self.attivo():
                if e["tentativi"] > 0 or adesso - e["due"] > 3600:     # a Telegram spento la coda non si accumula per sempre
                    self._togli(e)
                continue
            if not self._limite_ok(e["forza"]):
                self._togli(e)
                continue
            testo = self.formatta(e["tipo"], e["codice"], e["livello"], e["testo"], e["durata_s"], e["extra"])
            ok, msg = self.invia_ora(testo, e.get("muto") or self.silenzioso(e["livello"]))
            if ok:
                self.ult_invio.append(time.time())
                self._togli(e)
                if e["tipo"] == "apertura":
                    self._segna(e["codice"], True)
                    self.promemoria[e["codice"]] = time.time()
                elif e["tipo"] == "ripristino":
                    self._segna(e["codice"], False)
                    self.promemoria.pop(e["codice"], None)
            else:
                e["tentativi"] += 1
                self.stato.update(ultimo_errore=int(time.time()), messaggio=msg)
                log.warning("Telegram: invio non riuscito (%d): %s", e["tentativi"], msg)
                if e["tentativi"] >= 6:
                    self._togli(e)
                else:
                    e["due"] = time.time() + min(900, 30 * 2 ** e["tentativi"])

    def _togli(self, e):
        with self.lock:
            if e in self.coda:
                self.coda.remove(e)

    def promemoria_aperti(self):
        """Se un'anomalia resta aperta, ogni N ore un promemoria (0 = mai)."""
        ore = float(self.cfg().get("promemoria_ore") or 0)
        if ore <= 0:
            return
        adesso = time.time()
        for ts, codice, livello, testo, chiuso in self.app.db.eventi_recenti(50):
            if chiuso is not None or codice not in self.notificati or not self.allarme_attivo(codice):
                continue
            ultimo = self.promemoria.get(codice, ts)
            if adesso - ultimo >= ore * 3600 and not any(e["codice"] == codice for e in self.coda):
                self.promemoria[codice] = adesso
                self.accoda("promemoria", codice, livello, testo, 0, int(adesso - ts))

    def collega(self):
        self.app.eventi.ascoltatori.append(self.ascolta)
        self.thread.start()

    def ferma(self):
        self.fermo.set()


class _Sicuro(dict):
    def __missing__(self, k):
        return ""


def esc_html(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def durata_it(s):
    s = int(s)
    if s < 90:
        return f"{s} secondi"
    if s < 5400:
        return f"{round(s / 60)} minuti"
    if s < 172800:
        return f"{s / 3600:.1f} ore".replace(".", ",")
    return f"{round(s / 86400)} giorni"


def _minuti(hhmm):
    h, m = str(hhmm).split(":")
    return int(h) * 60 + int(m)


TG_BASE = "https://api.telegram.org/bot"


# --------------------------------------------------------------------------
# Sentinella: controlla i dati ogni minuto e apre/chiude le anomalie
# --------------------------------------------------------------------------

class Sentinella(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True, name="sentinella")
        self.app = app
        self.fermo = threading.Event()
        self.da = {}          # nome regola -> da quando la condizione e' vera (ts) o None
        self.falso_da = {}    # codice -> da quando la condizione e' falsa
        self.ult_cop = 0
        self.cop_cond = False
        self.sbr_primo = True   # primo controllo dopo l'avvio: uno sbrinamento gia' in corso non si riannuncia
        self.vent_primo = True  # VENT: idem per un «puoi chiudere» gia' comparso prima dell'avvio

    def run(self):
        self.fermo.wait(45)    # lascia partire i raccoglitori
        while not self.fermo.is_set():
            try:
                self.valuta()
                self.avvisa_sbrinamento()
                self.avvisa_finestre()   # VENT
                self.app.eventi.chiudi("pannello_script")
                if getattr(self.app, "telegram", None):
                    self.app.telegram.promemoria_aperti()
                    self.riepilogo()
            except Exception:
                tb = traceback.format_exc()
                log.error("errore nella sentinella:\n%s", tb)
                try:
                    self.app.eventi.apri("pannello_script", "Errore nei controlli automatici: " + tb.strip().splitlines()[-1])
                except Exception:
                    log.exception("non riesco nemmeno a segnalare l'errore (database non scrivibile?)")
            self.fermo.wait(60)

    # ----- utilita' -----
    def durata(self, nome, cond, ora):
        """Minuti da cui 'cond' e' continuamente vera (0 se falsa)."""
        if not cond:
            self.da[nome] = None
            return 0
        if self.da.get(nome) is None:
            self.da[nome] = ora
        return (ora - self.da[nome]) / 60.0

    def imposta(self, codice, cond, testo, livello="avviso"):
        ev = self.app.eventi
        if cond:
            self.falso_da.pop(codice, None)
            ev.apri(codice, testo, livello)
        elif self.app.db.evento_aperto(codice):
            t0 = self.falso_da.setdefault(codice, time.time())
            if time.time() - t0 >= 600:      # 10 minuti di calma prima di dichiarare risolto
                self.falso_da.pop(codice, None)
                ev.chiudi(codice)

    # ----- le regole -----
    def valuta(self):
        app, ora = self.app, int(time.time())
        al = app.cfg.leggi()["allarmi"]
        db = app.db
        ult = db.ultimi_dict()

        def num(s, max_eta=900):
            r = ult.get(s)
            return r[2] if r and r[2] is not None and ora - r[1] <= max_eta else None

        def txt(s):
            r = ult.get(s)
            return r[3] if r else None

        def on(nome):
            r = al.get(nome)
            return isinstance(r, dict) and r.get("attivo", True)

        tol = tolleranza(app)
        modo = modo_corrente(db, ora, tol)
        pdc_on, risc_on = txt("pdc.acceso") == "on", txt("risc.acceso") == "on"

        if on("nessun_dato"):
            r = al["nessun_dato"]
            ts = ult.get("acs.temperatura", (None, 0))[1] or 0
            causa_nota = any(db.evento_aperto(c) for c in ("st_pdc_offline", "st_config")) or \
                any(e[1].startswith("st_conn_") and e[4] is None for e in db.eventi_recenti(30))
            vecchio = ora - ts > float(r["minuti"]) * 60
            if ts and vecchio and not causa_nota:
                m = round((ora - ts) / 60)
                self.imposta("nessun_dato", True, f"Non arrivano dati nuovi dalla pompa di calore da {m} minuti.", "errore")
            else:
                self.imposta("nessun_dato", False, "")

        if on("pdc_ferma_con_richiesta") and risc_on and pdc_on:
            r = al["pdc_ferma_con_richiesta"]
            richiesta = num("casa.richiesta_calore", 1200) == 1.0
            m = self.durata("ferma", richiesta and modo == "riposo", ora)
            self.imposta("pdc_ferma_con_richiesta", m >= float(r["minuti"]),
                         f"Lo Smarther chiede calore da {round(m)} minuti ma la pompa di calore è ferma.")

        if on("casa_fredda"):
            r = al["casa_fredda"]
            t = num("casa.temperatura", 1800)
            m = self.durata("fredda", t is not None and t < float(r["sotto_gradi"]), ora)
            self.imposta("casa_fredda", m >= float(r["minuti"]),
                         f"In casa ci sono {num_it(t)} °C (soglia {num_it(r['sotto_gradi'])} °C).")

        # umidita' (Smarther): scatta sopra la soglia per N minuti, rientra 2 punti sotto la soglia (isteresi)
        umid = num("casa.umidita", 1800)
        cu = app.cfg.leggi().get("umidita") or {}
        for (cod, livello), soglia in zip((("umidita_alta", "avviso"), ("umidita_molto_alta", "errore")), soglie_umidita(app), strict=True):
            if on(cod):
                aperto = db.evento_aperto(cod)
                cond = umid is not None and (umid > soglia or (aperto and umid > soglia - float(cu.get("rientro_punti", 2))))
                m = self.durata(cod, cond, ora)
                self.imposta(cod, m >= float(cu.get("minuti", 30)),
                             f"L'umidità in casa è {num_it(umid, 0)} % (soglia {num_it(soglia, 0)} %).", livello)

        if on("cicli_brevi"):
            r = al["cicli_brevi"]
            da = ora - int(float(r["ore"]) * 3600)
            brevi = [c for c in cicli_compressore(db, da, ora, ora)
                     if c[2] == "casa" and (c[1] - c[0]) < float(r["durata_minima_min"]) * 60]
            self.imposta("cicli_brevi", len(brevi) >= int(r["quanti"]),
                         f"{len(brevi)} cicli di riscaldamento sotto i {r['durata_minima_min']} minuti nelle ultime "
                         f"{num_it(r['ore'], 0)} ore: la pompa parte e si ferma troppo spesso.")

        if on("portata_bassa"):
            r = al["portata_bassa"]
            p, hz = num("ciclo.portata", 900), num("ciclo.freq_compressore", 900)
            m = self.durata("portata", p is not None and (hz or 0) > 0 and p < float(r["sotto_lmin"]), ora)
            self.imposta("portata_bassa", m >= float(r["minuti"]),
                         f"La portata dell'acqua è {num_it(p)} L/min (soglia {num_it(r['sotto_lmin'])}): "
                         "filtro sporco, aria nel circuito o valvole chiuse?")

        if on("mandata_lontana"):
            r = al["mandata_lontana"]
            curva = dati_curva(db, ult, ora - 3600, ora, num("ciclo.esterna", 1800))
            att = curva["ora"]["mandata"] if curva else None
            man = num("ciclo.mandata", 900)
            in_casa = self.durata("casa_run", modo == "casa", ora) >= 20
            sotto = float(r["scarto_gradi"])
            sopra = float(r.get("scarto_sopra_gradi", sotto))
            lim = sopra if (man is not None and att is not None and man > att) else sotto
            fuori = in_casa and att is not None and man is not None and abs(man - att) > lim
            m = self.durata("mandata", fuori, ora)
            self.imposta("mandata_lontana", m >= float(r["minuti"]),
                         f"La mandata è {num_it(man)} °C, la curva prevede {num_it(att)} °C (scarto oltre "
                         f"{num_it(lim)} °C).")

        if on("acs_bassa") and pdc_on:
            r = al["acs_bassa"]
            t = num("acs.temperatura", 1800)
            m = self.durata("acs", t is not None and t < float(r["sotto_gradi"]), ora)
            self.imposta("acs_bassa", m >= float(r["minuti"]),
                         f"L'acqua calda è a {num_it(t)} °C (soglia {num_it(r['sotto_gradi'])} °C).")

        if on("sbrinamenti_frequenti"):
            r = al["sbrinamenti_frequenti"]
            da = ora - int(float(r["ore"]) * 3600)
            n = sum(1 for _, v in q_cambi(db, "pdc.sbrinamento", da, ora)[1:] if v == "on")
            self.imposta("sbrinamenti_frequenti", n >= int(r["quanti"]),
                         f"{n} sbrinamenti nelle ultime {num_it(r['ore'], 0)} ore.")

        if on("cop_basso") and ora - self.ult_cop > 1800:
            self.ult_cop = ora
            r = al["cop_basso"]
            da = ora - 86400
            da -= da % 300
            e = calcola_efficienza(db, da, ora, tol, ora)
            t, cop = e["tot"], e["tot"]["cop"]
            ore_lav = t["ore_casa"] + t["ore_acs"]
            cov = [v for v in e["copertura"].values() if v is not None]
            valido = cop is not None and ore_lav >= float(r["ore_minime"]) and (not cov or min(cov) >= 60)
            if valido:
                self.cop_cond = cop < float(r["sotto"]) if not self.cop_cond else cop < float(r["sotto"]) + 0.3
            self.imposta("cop_basso", valido and self.cop_cond,
                         f"Negli ultimi giorni il COP stimato è {num_it(cop, 2)} (soglia {num_it(r['sotto'])}): "
                         "la pompa rende meno del solito.")

    # ----- sbrinamento: due avvisi informativi (inizio e fine), sempre senza suoneria -----
    def avvisa_sbrinamento(self):
        """Segue lo stato pdc.sbrinamento. L'inizio dello sbrinamento annunciato resta in 'tg_sbr_inizio': dopo un riavvio
        non si riannuncia l'inizio, ma la fine si'. Non sono anomalie: niente eventi, niente promemoria."""
        db, tg = self.app.db, self.app.telegram
        ora = int(time.time())
        r = db.ultimi_dict().get("pdc.sbrinamento")
        primo, self.sbr_primo = self.sbr_primo, False
        if not r or r[3] not in ("on", "off") or ora - r[1] > 900:
            return
        try:
            inizio = int(db.meta_get("tg_sbr_inizio", "0") or 0)
        except ValueError:
            inizio = 0
        if inizio and ora - inizio > 3 * 3600:      # dimenticato da ore (pannello spento): non si racconta
            inizio = 0
            db.meta_set("tg_sbr_inizio", "0")
        if r[3] == "on" and not inizio:
            on_ts = [t for t, v in q_cambi(db, "pdc.sbrinamento", ora - 6 * 3600, ora) if v == "on"]
            inizio = on_ts[-1] if on_ts else ora
            db.meta_set("tg_sbr_inizio", str(inizio))
            if not primo and tg.allarme_attivo("sbrinamento_inizio"):
                est = db.ultimi_dict().get("ciclo.esterna")
                est_t = f"{num_it(est[2], 0)} °C" if est and est[2] is not None and ora - est[1] <= 1800 else "n.d."
                tg.accoda("sbrinamento_inizio", "sbrinamento_inizio", "info", "", extra={"esterna": est_t,
                          "ora": time.strftime("%H:%M", time.localtime(inizio))}, muto=True)
        elif r[3] == "off" and inizio:
            db.meta_set("tg_sbr_inizio", "0")
            if tg.allarme_attivo("sbrinamento_fine"):
                x = [e for e in elenco_sbrinamenti(db, inizio - 1, ora, ora)["elenco"] if not e["incorso"]]
                minuti = x[0]["min"] if x else max(1, round((ora - inizio) / 60))
                kwh = x[0]["kwh"] if x else None
                tg.accoda("sbrinamento_fine", "sbrinamento_fine", "info", "", extra={
                    "durata": f"{minuti} min",
                    "consumo": f"consumo circa {num_it(kwh, 2)} kWh" if kwh is not None else "consumo non disponibile"}, muto=True)

    # VENT-INIZIO  (messaggio «puoi chiudere»: si toglie con tutto cio' che e' marcato VENT)
    def avvisa_finestre(self):
        """Quando l'indicazione della Dashboard passa a «Puoi chiudere», un solo messaggio per ogni apertura (si riconosce dall'istante
        in cui e' iniziata). Dopo un riavvio un «puoi chiudere» gia' comparso non si riannuncia. Non e' un'anomalia: niente eventi."""
        db, tg = self.app.db, self.app.telegram
        ora = int(time.time())
        primo, self.vent_primo = self.vent_primo, False
        ult = db.ultimi_dict()
        stz = stazione_info(self.app)
        num = lambda k: (ult.get(k) or (None, None, None))[2]
        v = vent_stato(db, ora, (stz or {}).get("umidita"), num("ciclo.esterna"), num("casa.temperatura"))
        if not v or v["s"] != "chiudi":
            return
        if str(db.meta_get("tg_vent_da", "")) == str(v["da"]):
            return
        db.meta_set("tg_vent_da", str(v["da"]))
        if primo or not tg.allarme_attivo("finestre_chiudi"):
            return
        tg.accoda("finestre_chiudi", "finestre_chiudi", "info", "", extra={"calo": str(v["calo"]), "um": f"{num_it(v['um'], 0)}%"})
    # VENT-FINE

    # ----- riepilogo del mattino: cosa e' successo durante il silenzio notturno -----
    def _finestra_notte(self, ora):
        """(inizio, fine) dell'ultima notte silenziosa gia' finita, oppure None."""
        s = self.app.telegram.cfg()["silenzio_notturno"]
        if not s.get("attivo"):
            return None
        try:
            da, a = _minuti(s["da"]), _minuti(s["a"])
        except (ValueError, KeyError):
            return None
        lt = time.localtime(ora)
        g0 = _ore_locali(lt)
        fine = g0 + a * 60
        inizio = (g0 if da < a else g0 - 86400) + da * 60
        if ora < fine:  # riepilogo prima della fine del silenzio: si racconta la notte fino a ora
            return (inizio, ora) if inizio < ora else None
        return inizio, fine

    def _notte_righe(self, ora):
        """Righe (testo HTML) sulle grandi oscillazioni avvenute nel silenzio notturno; vuoto se e' stata una notte tranquilla."""
        w = self._finestra_notte(ora)
        if not w:
            return ""
        ini, fin = w
        db, cfg = self.app.db, self.app.cfg.leggi()
        soglia = float((cfg["telegram"]["silenzio_notturno"].get("oscillazione_gradi") or 2.0))
        righe = []
        t = q_serie(db, "casa.temperatura", ini, fin)
        if t:
            lo, hi = min(v for _, v in t), max(v for _, v in t)
            if hi - lo >= soglia:
                righe.append(f"🏠 In casa tra {num_it(lo)} e {num_it(hi)} °C")
        u = q_serie(db, "casa.umidita", ini, fin)
        if u:
            ts_max, v_max = max(u, key=lambda x: x[1])
            if v_max > soglie_umidita(self.app)[0]:
                righe.append(f"💧 Umidità fino a {num_it(v_max, 0)} % (alle {time.strftime('%H:%M', time.localtime(ts_max))})")
        hm = lambda x: time.strftime("%H:%M", time.localtime(x))
        for ts, cod, _liv, _testo, chiuso in reversed(db.eventi_recenti(100)):
            if ini <= ts < fin:
                fino = f"alle {hm(chiuso)}" if chiuso else "ancora aperto"
                righe.append(f"⚠️ {esc_html(TITOLI.get(cod, cod))}: dalle {hm(ts)} {fino}")
        return "\n".join(righe)

    def riepilogo(self):
        tg = self.app.telegram
        c = tg.cfg()["riepilogo_mattina"]
        if not c.get("attivo") or not tg.attivo():
            return
        lt = time.localtime()
        try:
            if lt.tm_hour * 60 + lt.tm_min < _minuti(c["ora"]):
                return
        except (ValueError, KeyError):
            return
        oggi = time.strftime("%Y-%m-%d", lt)
        if self.app.db.meta_get("tg_riepilogo_giorno") == oggi:
            return
        self.app.db.meta_set("tg_riepilogo_giorno", oggi)
        oggi0 = _ore_locali(lt)
        ieri0 = oggi0 - 86400
        ora = int(time.time())
        tol = tolleranza(self.app)
        e = calcola_efficienza(self.app.db, ieri0, oggi0, tol, ora)
        g = e["giorni"][0] if e["giorni"] else None
        if not g:
            return
        casa = [v for _, v in q_serie(self.app.db, "casa.temperatura", ieri0, oggi0)]
        est = [v for _, v in q_serie(self.app.db, "ciclo.esterna", ieri0, oggi0)]
        aperti = [x for x in self.app.db.eventi_recenti(30) if x[4] is None]
        anom = ("\n⚠️ Anomalie aperte: " + ", ".join(esc_html(TITOLI.get(x[1], x[1])) for x in aperti)) if aperti else "\n✅ Nessuna anomalia aperta."
        righe_notte = self._notte_righe(ora)
        if righe_notte:
            anom += "\n\n🌙 <b>Di notte</b>\n" + righe_notte
        extra = {"energia": num_it(g["el_casa"] + g["el_acs"] + g["el_riposo"]), "cop": num_it(g["cop"], 2) if g["cop"] else "—",
                 "ore_casa": num_it(g["min_casa"] / 60), "ore_acs": num_it(g["min_acs"] / 60), "cicli": e["cicli"]["casa"]["n"],
                 "casa_min": num_it(min(casa)) if casa else "—", "casa_max": num_it(max(casa)) if casa else "—",
                 "esterna": num_it(sum(est) / len(est)) if est else "—", "anomalie": anom,
                 "data": time.strftime("%d/%m", time.localtime(ieri0 + 3600))}
        tg.accoda("riepilogo", "riepilogo", "info", "", 0, 0, extra, forza=True)


def num_it(v, d=1):
    return "—" if v is None else f"{float(v):.{d}f}".replace(".", ",")


def avvia_telegram_e_sentinella(app):
    app.telegram = Telegram(app)
    app.telegram.collega()
    app.sentinella = Sentinella(app)
    app.sentinella.start()
    ok, err = (app.telegram.attivo(), app.telegram.credenziali()[2])
    if app.cfg.leggi()["telegram"]["abilitato"] and not ok:
        log.info("Telegram non attivo: %s", err)
    elif ok:
        log.info("Telegram attivo.")

