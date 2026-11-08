# ==========================================================================
# PEZZO 5: PAGINA IMPOSTAZIONI (stato dei collegamenti + modifica di config.json dal pannello)
# ==========================================================================

AVVIO = time.time()

# Tipi di campo: testo, segreto (si scrive ma non si rilegge), bool, int, float, ora (HH:MM), lungo (testo su piu' righe),
# scelta (menu a tendina: opzioni = [[valore, etichetta], ...])
def _c(chiave, tipo, et, aiuto="", **kw):
    d = {"chiave": chiave, "tipo": tipo, "et": et, "aiuto": aiuto}
    d.update(kw)
    return d


ETICHETTE_ALLARME = {
    "minuti": ("Per quanti minuti di fila", "int", 1, 1440),
    "sotto_gradi": ("Avvisa sotto (°C)", "float", -30, 90),
    "sotto_lmin": ("Avvisa sotto (L/min)", "float", 0, 60),
    "scarto_sopra_gradi": ("Scarto sopra la curva (°C)", "float", 1, 40),
    "scarto_gradi": ("Scarto sotto la curva (°C)", "float", 1, 40),
    "durata_minima_min": ("Un ciclo è 'breve' se dura meno di (minuti)", "int", 1, 120),
    "quanti": ("Quanti eventi fanno scattare l'avviso", "int", 1, 100),
    "ore": ("Periodo controllato (ore)", "float", 0.5, 48),
    "sotto": ("Avvisa sotto (COP)", "float", 0.5, 10),
    "ore_minime": ("Ore minime di lavoro per valutarlo", "float", 0.5, 24),
    "ritardo_minuti": ("Aspetta prima di avvisare (minuti)", "int", 0, 1440),
}

DESCRIZIONI_ALLARME = {
    "nessun_dato": "Non arrivano dati dalla pompa di calore.",
    "pdc_ferma_con_richiesta": "Lo Smarther chiede calore ma la pompa è ferma.",
    "casa_fredda": "La temperatura in casa è troppo bassa.",
    "umidita_alta": "L'umidità in casa supera la soglia di guardia (giallo nella Dashboard).",
    "umidita_molto_alta": "L'umidità in casa è molto alta (rosso nella Dashboard).",
    "cicli_brevi": "La pompa parte e si ferma troppo spesso.",
    "portata_bassa": "Poca acqua nel circuito (filtro, aria, valvole).",
    "mandata_lontana": "La mandata è lontana da quella prevista dalla curva climatica, con una soglia per sopra e una per sotto.",
    "acs_bassa": "L'acqua calda sanitaria è troppo fredda.",
    "cop_basso": "L'efficienza stimata (COP) è bassa.",
    "sbrinamenti_frequenti": "Troppi sbrinamenti ravvicinati.",
    "sbrinamento_inizio": "Un messaggio, senza suoneria, quando la pompa entra in sbrinamento.",
    "sbrinamento_fine": "Un messaggio, senza suoneria, quando lo sbrinamento finisce (durata e consumo).",
}

AVVISI_INFORMATIVI = ("sbrinamento_inizio", "sbrinamento_fine")     # non sono anomalie: solo interruttore, niente soglie né ritardo


MODELLI_SMARTHER = [
    ["", "Non specificato"],
    ["XW8002", "XW8002 · da incasso"],
    ["XM8002", "XM8002 · da incasso"],
    ["XG8002", "XG8002 · da incasso"],
    ["XW8002W", "XW8002W · da parete (solo bianco)"],
]


def schema_impostazioni():
    sezioni = [
        {"id": "telegram", "titolo": "Telegram", "nota": "Dove arrivano gli avvisi. Il token del bot si può inserire qui oppure in un "
         "tuo file .json (riquadro sotto): se il file è indicato ha la precedenza.", "prova": True, "campi": [
            _c("telegram.abilitato", "bool", "Invia gli avvisi su Telegram"),
            _c("telegram.bot_token", "segreto", "Token del bot", "Quello che ti ha dato BotFather."),
            _c("telegram.chat_id", "testo", "Chat ID", "Il tuo numero di chat con il bot."),
            _c("telegram.nome_impianto", "testo", "Nome mostrato nei messaggi"),
        ]},
        {"id": "telegram_file", "titolo": "Telegram: credenziali da un tuo file (facoltativo)", "nota": "Se il token e la chat sono già in un tuo "
         "file .json (per esempio quello di un altro bot), scrivi qui dove sta e come si chiamano le chiavi.", "campi": [
            _c("telegram.file_credenziali", "testo", "Percorso del file", "Per es. C:\\bot\\telegram.json"),
            _c("telegram.chiave_token", "testo", "Chiave del token nel file", "Per chiavi annidate: a.b"),
            _c("telegram.chiave_chat", "testo", "Chiave della chat nel file"),
        ]},
        {"id": "telegram_limiti", "titolo": "Telegram: promemoria e limiti", "campi": [
            _c("telegram.promemoria_ore", "float", "Promemoria ogni (ore)", "Se l'anomalia resta aperta. 0 = mai.", min=0, max=168),
            _c("telegram.max_messaggi_ora", "int", "Massimo messaggi all'ora", min=1, max=200),
        ]},
        {"id": "orari", "titolo": "Silenzio notturno", "nota": "Di notte i messaggi arrivano senza suoneria.", "campi": [
            _c("telegram.silenzio_notturno.attivo", "bool", "Silenzio notturno attivo"),
            _c("telegram.silenzio_notturno.da", "ora", "Dalle"),
            _c("telegram.silenzio_notturno.a", "ora", "Alle"),
            _c("telegram.silenzio_notturno.anche_errori", "bool", "Silenzia anche gli errori gravi"),
            _c("telegram.silenzio_notturno.oscillazione_gradi", "float", "Oscillazione notturna da segnalare (°C)",
               "Nel riepilogo del mattino (se attivo) aggiungo cosa è successo durante il silenzio: temperatura in casa variata di almeno questi gradi, umidità sopra la soglia gialla, avvisi scattati.", min=0.5, max=20),
        ]},
        {"id": "riepilogo", "titolo": "Riepilogo del mattino", "campi": [
            _c("telegram.riepilogo_mattina.attivo", "bool", "Riepilogo di ieri ogni mattina"),
            _c("telegram.riepilogo_mattina.ora", "ora", "Ora del riepilogo"),
        ]},
        {"id": "testi", "titolo": "Testi dei messaggi", "nota": "HTML semplice (<b>grassetto</b>, <i>corsivo</i>). Segnaposto: "
         "{impianto} {testo} {titolo} {ora} {data} {durata}. Il riepilogo ha i suoi: {energia} {cop} {ore_casa} "
         "{ore_acs} {cicli} {casa_min} {casa_max} {esterna} {anomalie}.", "campi": [
            _c("telegram.messaggi.apertura_errore", "lungo", "Problema grave"),
            _c("telegram.messaggi.apertura_avviso", "lungo", "Avviso"),
            _c("telegram.messaggi.ripristino", "lungo", "Tutto risolto"),
            _c("telegram.messaggi.promemoria", "lungo", "Promemoria"),
            _c("telegram.messaggi.riepilogo", "lungo", "Riepilogo del mattino"),
            _c("telegram.messaggi.sbrinamento_inizio", "lungo", "Sbrinamento iniziato", "Segnaposto: {impianto} {esterna} {ora}."),
            _c("telegram.messaggi.sbrinamento_fine", "lungo", "Sbrinamento finito", "Segnaposto: {impianto} {durata} {consumo}."),
        ]},
        {"id": "collegamenti", "titolo": "SmartThings (pompa di calore)", "nota": "I token che si rinnovano da soli (OAuth) non si modificano qui.",
         "campi": [
            _c("smartthings.device_id", "testo", "ID della pompa di calore"),
            _c("smartthings.pat", "segreto", "Token personale (PAT, dura 24 ore)", "Usato solo se l'OAuth non è configurato."),
            _c("generale.intervallo_pdc_secondi", "int", "Leggi la pompa ogni (secondi)", "Minimo 20.", min=20, max=3600),
        ]},
        {"id": "refresh", "titolo": "Dati più freschi (refresh)", "nota": "SmartThings consegna i dati del compressore con 4-5 minuti di ritardo. "
         "Con questa opzione, solo mentre la pompa lavora, il pannello le manda il comando «refresh» (non cambia nessuna impostazione) e a volte "
         "i dati arrivano molto prima. Serve il permesso di comando del token (x:devices:*). È una prova: migliora, non elimina il ritardo.",
         "campi": [
            _c("smartthings.refresh.attivo", "bool", "Chiedi alla pompa di aggiornare i dati mentre lavora"),
            _c("smartthings.refresh.ogni_secondi", "int", "Manda il refresh ogni (secondi)", "Minimo 30, consigliato 60.", min=30, max=600),
        ]},
        {"id": "netatmo", "titolo": "Netatmo (Smarther)", "campi": [
            _c("netatmo.home_id", "testo", "ID della casa"),
            _c("netatmo.room_id", "testo", "ID della stanza (Smarther)"),
            _c("generale.intervallo_netatmo_secondi", "int", "Leggi lo Smarther ogni (secondi)", min=30, max=3600),
        ]},
        {"id": "stazione", "titolo": "Stazione meteo vicina (umidità esterna)", "nota": "Facoltativo. Incolla l'indirizzo dei dati grezzi di una "
         "stazione WeatherLink vicina (non quello della pagina: lo trovi con «python pdc_strumenti.py meteo-prova», che lo stampa pronto da incollare; "
         "se la stazione cambia, rilancialo). Il pannello legge l'umidità ogni 10 minuti e la mostra nella card Esterna della Dashboard. "
         "Vuoto = funzione spenta.", "campi": [
            _c("stazione.url", "testo", "Indirizzo dei dati", "Deve iniziare con https:// e stare su weatherlink.com."),
        ]},
        {"id": "accessoweb", "titolo": "Login del pannello", "campi": [
            _c("generale.sessione_giorni", "int", "Giorni prima di rifare il login", min=1, max=365),
        ]},
        {"id": "umidita", "titolo": "Umidità in casa", "nota": "Soglie dell'umidità (dallo Smarther): sopra la prima il valore diventa giallo "
         "in Dashboard e parte l'avviso Telegram «Umidità alta»; sopra la seconda diventa rosso e parte «Umidità molto alta». "
         "Valgono per tutto: Dashboard, grafico e Telegram. Gli avvisi si possono spegnere in «Quando avvisarti».", "campi": [
            _c("umidita.giallo", "float", "Soglia gialla (%)", "Predefinita 60.", min=20, max=100),
            _c("umidita.rosso", "float", "Soglia rossa (%)", "Predefinita 70. Deve essere maggiore della gialla.", min=20, max=100),
            _c("umidita.minuti", "int", "Avvisa dopo (minuti di fila)", "Evita gli avvisi per un picco breve (una doccia).", min=1, max=1440),
            _c("umidita.rientro_punti", "float", "Rientro sotto la soglia di (punti)", "Evita messaggi continui se l'umidità oscilla attorno alla soglia.", min=0, max=20),
        ]},
        {"id": "tariffa", "titolo": "Costo dell'energia", "nota": "Se metti il prezzo, il pannello mostra quanto costa l'energia "
         "della pompa (stima: kWh della pompa per il prezzo, senza quota fissa).", "campi": [
            _c("tariffa.prezzo_kwh", "float", "Prezzo dell'energia (€ per kWh)", "Per esempio 0.28. Con 0 i costi non si vedono.", min=0, max=5),
        ]},
        {"id": "dispositivi", "titolo": "Dispositivi", "nota": "Per la pagina Dispositivi. Tutto facoltativo. I loghi non li fornisco io: "
         "metti i tuoi file nella cartella \"dati/loghi\" (smarther.svg, samsung.svg, netatmo.svg, bticino.svg; "
         "vanno bene anche .png).", "campi": [
            _c("dispositivi.ip_pdc", "testo", "IP della pompa di calore", "Per es. 192.168.1.50. SmartThings non lo dice: "
               "serve solo per mostrarlo e ricavare il MAC."),
            _c("dispositivi.ip_smarther", "testo", "IP dello Smarther", "Per es. 192.168.1.51."),
            _c("dispositivi.modello_smarther", "scelta", "Modello dello Smarther", "Netatmo dice solo \"BNS\": qui scegli "
               "l'articolo che hai (è scritto sulla scatola o sul retro).", opzioni=MODELLI_SMARTHER),
            _c("dispositivi.loghi_bianchi", "bool", "Mostra i loghi in bianco",
               "Utile se un logo è scuro e sul tema scuro non si legge."),
        ]},
        {"id": "bollitore", "titolo": "Bollitore", "nota": "Serve alla «Salute del bollitore» (pagina Efficienza) per trasformare "
         "il calo di temperatura di notte in watt.", "campi": [
            _c("bollitore.litri", "int", "Litri del bollitore", "Per es. 300.", min=50, max=2000),
        ]},
        {"id": "backup", "titolo": "Backup", "nota": "Ogni notte il pannello copia database e config.json nella cartella \"dati/backup\". "
         " Per rimettere una copia: python pdc_pannello.py ripristina (a pannello fermo).", "campi": [
            _c("backup.attivo", "bool", "Backup automatico notturno"),
            _c("backup.copie", "int", "Quante copie tenere", "Le più vecchie vengono cancellate.", min=1, max=60),
        ]},
        {"id": "storico", "titolo": "Storico dei dati", "nota": "Più indietro dei giorni indicati tengo una media ogni N "
         "minuti invece di un punto al minuto: i grafici vecchi restano ma il database non cresce all'infinito.", "campi": [
            _c("conservazione.dati_completi_giorni", "int", "Giorni con dati dettagliati", min=30, max=3650),
            _c("conservazione.media_minuti", "int", "Dopo, una media ogni (minuti)", min=5, max=240),
        ]},
    ]
    allarmi = []
    for codice, regola in ALLARMI_DEFAULT.items():
        if not isinstance(regola, dict):
            continue
        campi = [_c(f"allarmi.{codice}.attivo", "bool", "Attivo")]
        for k in regola:
            if k in ETICHETTE_ALLARME:
                et, tipo, mn, mx = ETICHETTE_ALLARME[k]
                campi.append(_c(f"allarmi.{codice}.{k}", tipo, et, min=mn, max=mx))
        if codice not in AVVISI_INFORMATIVI:
            et, tipo, mn, mx = ETICHETTE_ALLARME["ritardo_minuti"]
            campi.append(_c(f"allarmi.{codice}.ritardo_minuti", tipo, et, "Se si risolve prima, non ti scrivo.", min=mn, max=mx))
        allarmi.append({"id": codice, "titolo": TITOLI.get(codice, codice), "descrizione": DESCRIZIONI_ALLARME.get(codice, ""),
                        "campi": campi})
    for s in sezioni:  # in quale tab della pagina sta ogni riquadro
        s["tab"] = TAB_SEZIONI.get(s["id"], "dati")
    sezioni += sezioni_plugin()
    return sezioni, allarmi


TAB_SEZIONI = {"telegram": "avvisi", "telegram_file": "avvisi", "telegram_limiti": "avvisi", "orari": "avvisi", "riepilogo": "avvisi", "testi": "avvisi", "umidita": "avvisi", "collegamenti": "collegamenti", "refresh": "collegamenti", "netatmo": "collegamenti", "stazione": "collegamenti", "accessoweb": "collegamenti",
               "dispositivi": "collegamenti", "tariffa": "dati", "bollitore": "dati", "backup": "dati", "storico": "dati"}


def _tutti_i_campi():
    sezioni, allarmi = schema_impostazioni()
    return {c["chiave"]: c for s in sezioni + allarmi for c in s["campi"]}


def _leggi_percorso(cfg, chiave):
    cur = cfg
    for p in chiave.split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur


def _scrivi_percorso(raw, chiave, valore):
    parti = chiave.split(".")
    cur = raw
    for p in parti[:-1]:
        if not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    cur[parti[-1]] = valore


_cache_db = {"t": 0, "v": None}


def info_database(app):
    if time.time() - _cache_db["t"] < 300 and _cache_db["v"]:
        return _cache_db["v"]
    with closing(app.db._c()) as c:
        n, primo = c.execute("SELECT COUNT(*), MIN(ts) FROM misure").fetchone()
    try:
        peso = os.path.getsize(app.db.path) / 1e6
    except OSError:
        peso = None
    v = {"righe": n, "dal": primo, "mb": round(peso, 1) if peso is not None else None}
    _cache_db.update(t=time.time(), v=v)
    return v


def stato_stazione(app):
    """Riga di stato della stazione meteo vicina per Impostazioni: testo breve (vuoto se la funzione è spenta)."""
    i = stazione_info(app)
    if i is None:
        return ""
    if i["umidita"] is None and i["ts"] and not i["errore"]:
        return "La stazione non manda dati nuovi dalle " + datetime.fromtimestamp(i["ts"]).strftime("%H:%M")
    if i["umidita"] is None:
        return "In attesa del primo dato…" if not i["errore"] else "Non riesco a leggere la stazione: " + i["errore"]
    quando = datetime.fromtimestamp(i["ts"]).strftime("%H:%M") if i["ts"] else "?"
    return f"Umidità esterna {num_it(i['umidita'])} % · ultimo dato della stazione alle {quando}" + (" · ultima lettura non riuscita" if i["errore"] else "")


def stato_collegamenti(app, ora):
    cfg = app.cfg.leggi()
    srv = {s.sigla: s for s in app.servizi}

    def sv(sigla):
        s = srv.get(sigla)
        return {"ultimo_ok": s.stato["ultimo_ok"], "messaggio": s.stato["messaggio"]} if s else {"ultimo_ok": None, "messaggio": None}
    tg = getattr(app, "telegram", None) or Telegram(app)
    _, _, err = tg.credenziali()
    nt = cfg["netatmo"]
    return {
        "versione": VERSIONE, "python": sys.version.split()[0], "demo": app.demo,
        "avviato": int(AVVIO), "porta": cfg["generale"]["porta"], "cartella": str(app.base),
        "smartthings": {**sv("st"), "accesso": info_accesso_st(app)},
        "netatmo": {**sv("nt"), "token_scade": int(float(nt.get("expires_at") or 0)) or None,
                    "configurato": bool(nt.get("client_id") and nt.get("refresh_token"))},
        "telegram": {"abilitato": bool(cfg["telegram"]["abilitato"]), "attivo": tg.attivo(), "problema": err,
                     "ultimo_ok": tg.stato["ultimo_ok"], "messaggio": tg.stato["messaggio"]},
        "stazione": stato_stazione(app),
        "database": info_database(app),
        "eventi_aperti": sum(1 for e in app.db.eventi_recenti(50) if e[4] is None),
    }


@api("/api/impostazioni")
def api_impostazioni(app, q):
    ora = int(time.time())
    cfg = app.cfg.leggi()
    sezioni, allarmi = schema_impostazioni()
    for s in sezioni + allarmi:
        for c in s["campi"]:
            v = _leggi_percorso(cfg, c["chiave"])
            if v is None and c.get("default") is not None:
                v = c["default"]
            if c["tipo"] == "segreto":
                c["valore"], c["impostato"] = "", bool(v)
            else:
                c["valore"] = v if v is not None else ("" if c["tipo"] in ("testo", "lungo", "ora", "scelta") else (False if c["tipo"] == "bool" else 0))
    cf = cfg.get("cloudflare") or {}
    base = f"https://{cf['hostname']}" if cf.get("tunnel_token") and cf.get("hostname") else ""      # indirizzo da fuori casa, per i plugin
    for s in sezioni:
        if s.get("plugin"):
            s["plugin"]["base"] = base
    out = cornice(app, ora)
    out.update({"stato": stato_collegamenti(app, ora), "sezioni": sezioni, "allarmi": allarmi,
                "utente": cfg["accesso"].get("utente") or ""})
    return out


def _valida(c, v):
    """-> (valore_pulito, errore)"""
    t = c["tipo"]
    if t == "bool":
        return (v, None) if isinstance(v, bool) else (None, "Valore non valido")
    if t in ("int", "float"):
        try:
            x = float(str(v).replace(",", "."))
        except ValueError:
            return None, "Scrivi un numero"
        if not math.isfinite(x) or x < c.get("min", -1e9) or x > c.get("max", 1e9):
            return None, f"Deve stare tra {c.get('min')} e {c.get('max')}"
        return (int(round(x)) if t == "int" else x), None
    if t == "ora":
        try:
            m = _minuti(str(v))
        except ValueError:
            return None, "Scrivi l'ora come HH:MM"
        if not (0 <= m < 1440) or len(str(v)) != 5:
            return None, "Scrivi l'ora come HH:MM"
        return str(v), None
    if t == "scelta":
        return (v, None) if any(v == o[0] for o in c.get("opzioni", [])) else (None, "Scelta non valida")
    s = str(v if v is not None else "")
    if len(s) > 4000:
        return None, "Testo troppo lungo"
    if t == "lungo":
        try:
            s.format_map(_Sicuro({}))
        except (ValueError, IndexError):
            return None, "Le parentesi graffe non tornano: i segnaposto vanno scritti come {testo}"
        return s, None
    s = s.strip()
    if c["chiave"] == "stazione.url" and s and not stazione_url_valido(s):
        return None, "Scrivi un indirizzo https:// di weatherlink.com"
    if c["chiave"] in ("dispositivi.ip_pdc", "dispositivi.ip_smarther") and s:
        try:
            ipaddress.IPv4Address(s)
        except ValueError:
            return None, "Scrivi un indirizzo IP, per es. 192.168.1.50"
    return s, None


@api_post("/api/impostazioni/salva")
def salva_impostazioni(app, corpo, h):
    if app.demo:
        return {"ok": False, "errore": "In modalità demo non si salva nulla."}
    mod = corpo.get("modifiche")
    campi = _tutti_i_campi()
    if not isinstance(mod, dict) or not mod:
        return {"ok": True, "salvati": 0}
    puliti, errori = {}, {}
    for chiave, v in mod.items():
        c = campi.get(chiave)
        if not c:
            errori[chiave] = "Impostazione sconosciuta"
            continue
        if c["tipo"] == "segreto" and not str(v).strip():
            continue                      # vuoto = non cambiare
        val, err = _valida(c, v)
        if err:
            errori[chiave] = err
        else:
            puliti[chiave] = val
    if "umidita.giallo" in puliti or "umidita.rosso" in puliti:
        cu = app.cfg.leggi().get("umidita") or {}
        g = puliti.get("umidita.giallo", cu.get("giallo", 60))
        r = puliti.get("umidita.rosso", cu.get("rosso", 70))
        if float(r) <= float(g) and "umidita.rosso" not in errori and "umidita.giallo" not in errori:
            errori["umidita.rosso"] = "Deve essere maggiore della soglia gialla."
    if errori:
        return {"ok": False, "errori": errori}

    def scrivi(raw):
        for k, v in puliti.items():
            _scrivi_percorso(raw, k, v)
    app.cfg.aggiorna(scrivi)
    log.info("Impostazioni modificate dal pannello: %s", ", ".join(sorted(k for k in puliti if campi[k]["tipo"] != "segreto"))
             + (" (+ segreti)" if any(campi[k]["tipo"] == "segreto" for k in puliti) else ""))
    if any(k.startswith("plugin.") for k in puliti):
        plugin_sincronizza(app)
    riavvio = [k for k in puliti if k.startswith("generale.")]
    return {"ok": True, "salvati": len(puliti), "riavvio": bool(riavvio)}


@api_post("/api/impostazioni/telegram-prova")
def prova_telegram(app, corpo, h):
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo non si invia nulla."}
    tg = getattr(app, "telegram", None) or Telegram(app)
    _, _, err = tg.credenziali()
    if err:
        return {"ok": False, "messaggio": err}
    ok, msg = tg.invia_ora(tg.formatta("prova", "prova", "info", ""))
    return {"ok": ok, "messaggio": "Messaggio inviato: guarda Telegram." if ok else msg}


@api_post("/api/impostazioni/password")
def cambia_password(app, corpo, h):
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo non si cambia la password."}
    ip = ip_client(h)
    if app.limite.bloccato(ip):
        return {"ok": False, "messaggio": "Troppi tentativi. Riprova più tardi."}
    acc = app.cfg.leggi()["accesso"]
    if not verifica_password(str(corpo.get("attuale", "")), acc.get("password_hash") or ""):
        app.limite.fallito(ip)
        time.sleep(1.0)
        return {"ok": False, "messaggio": "La password attuale non è corretta."}
    nuova = str(corpo.get("nuova", ""))
    if len(nuova) < PASSWORD_MIN:
        return {"ok": False, "messaggio": f"La nuova password deve avere almeno {PASSWORD_MIN} caratteri."}
    nuovo_hash = hash_password(nuova)
    utente = str(corpo.get("utente", "")).strip() or acc.get("utente")
    app.cfg.aggiorna(lambda raw: raw.setdefault("accesso", {}).update(password_hash=nuovo_hash, utente=utente, password=""))
    app.sessioni.elimina_altre(h._cookie("pdc_sid"))
    app.limite.ok(ip)
    log.info("Password del pannello cambiata dal pannello (le altre sessioni sono state chiuse)")
    return {"ok": True, "messaggio": "Fatto. Gli altri dispositivi dovranno rifare il login."}


# --------------------------------------------------------------------------
# Registro del pannello (Impostazioni > Registro): ultime righe del log, senza segreti
# --------------------------------------------------------------------------

_SEGRETI = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=\-]{8,}"), r"\1***"),
    (re.compile(r"(?i)((?:access_token|refresh_token|client_secret|secret|password|passwd|pat|token|api_key|apikey)[\"']?\s*[:=]\s*[\"']?)[^\s,\"'}&]+"), r"\1***"),
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_\-]{30,}\b"), "***"),
    (re.compile(r"[A-Za-z0-9_\-]{40,}"), "***"),
]


def pulisci_riga(t):
    for rx, sost in _SEGRETI:
        t = rx.sub(sost, t)
    return t


def leggi_coda_log(percorso, n=120, max_byte=300_000):
    """Ultime n righe del log: [{livello, testo}]. Le righe senza data (traceback) ereditano il livello della precedente."""
    try:
        with open(percorso, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - max_byte))
            testo = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    righe = testo.splitlines()[-n:]
    rx, liv, out = re.compile(r"^\d\d/\d\d \d\d:\d\d:\d\d (\w+) "), "INFO", []
    for r in righe:
        m = rx.match(r)
        if m:
            liv = m.group(1)
        out.append({"livello": liv, "testo": pulisci_riga(r)})
    return out


@api("/api/registro")
def api_registro(app, q):
    try:
        n = max(20, min(400, int(q.get("righe", ["150"])[0])))
    except ValueError:
        n = 150
    base = app.base

    def peso(p):
        try:
            return os.path.getsize(p)
        except OSError:
            return 0
    dbp = str(app.db.path)
    cfg_b = app.cfg.leggi().get("backup") or {}
    cart = cartella_backup(app)
    copie = sorted(cart.glob("pdc-*.db")) if cart.is_dir() else []
    return {
        "righe": leggi_coda_log(base / "log" / "pdc_pannello.log", n),
        "db_byte": peso(dbp) + peso(dbp + "-wal"), "log_byte": peso(base / "log" / "pdc_pannello.log"),
        "backup_ts": float(app.db.meta_get("backup_ts", 0) or 0) or None,
        "backup_copie": len(copie), "backup_attivo": bool(cfg_b.get("attivo", True)),
        "versione": VERSIONE, "avviato": int(AVVIO), "ora": int(time.time()),
    }


@api_post("/api/database/reset")
def reset_database(app, corpo, h):
    """Azzera i dati raccolti (misure, stati, cicli, anomalie). Restano config.json, la password e i login.
    Prima di toccare qualcosa fa una copia del database; se la copia non riesce non cancella niente."""
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo non si azzera niente."}
    ip = ip_client(h)
    if app.limite.bloccato(ip):
        return {"ok": False, "messaggio": "Troppi tentativi. Riprova più tardi."}
    if str(corpo.get("conferma", "")).strip() != "RESETTA":
        return {"ok": False, "messaggio": "Per confermare scrivi esattamente RESETTA (tutto maiuscolo)."}
    acc = app.cfg.leggi()["accesso"]
    if not verifica_password(str(corpo.get("password", "")), acc.get("password_hash") or ""):
        app.limite.fallito(ip)
        time.sleep(1.0)
        return {"ok": False, "messaggio": "La password non è corretta: non ho cancellato niente."}
    cart = cartella_backup(app)
    copia = cart / f"prima-del-reset-{time.strftime('%Y%m%d-%H%M%S')}.db"
    try:
        cart.mkdir(parents=True, exist_ok=True)
        with closing(app.db._c()) as src, closing(sqlite3.connect(str(copia))) as dst:
            src.backup(dst)
        if not copia.exists() or copia.stat().st_size < 4096:
            raise OSError("la copia è vuota")
    except Exception as e:
        copia.unlink(missing_ok=True)
        log.error("Reset database annullato: copia non riuscita (%s)", e)
        return {"ok": False, "messaggio": f"Non sono riuscito a fare la copia di sicurezza ({e}): non ho cancellato niente."}
    with app.db.lock, closing(app.db._c()) as c, c:
        for tab in ("misure", "stati", "ultimo", "ciclo", "eventi", "finestre"):
            c.execute(f"DELETE FROM {tab}")
        c.execute("DELETE FROM meta WHERE k IN ('ciclo_ultimo_ts', 'tg_notificati', 'fin_soglie')")
    try:
        vacuum(app.db)
    except Exception:
        log.exception("reset: compattazione non riuscita (non è grave)")
    log.warning("DATABASE AZZERATO dal pannello (da %s). Copia di sicurezza: %s", ip, copia.name)
    for s in app.servizi:
        s.sveglia.set()     # rileggi subito: i primi dati ricompaiono in pochi secondi
    return {"ok": True, "messaggio": "Database azzerato. I dati ricominciano da adesso.", "copia": copia.name}
