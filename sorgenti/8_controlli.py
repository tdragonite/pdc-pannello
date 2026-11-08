# ==========================================================================
# PEZZO 9 (v0.10): COMANDI (accendi / spegni riscaldamento, acqua calda, Smarther)
# ==========================================================================
# Servono permessi di SCRITTURA sui token: SmartThings x:devices:* e Netatmo write_smarther.
# Senza, il comando viene rifiutato e il pannello lo dice in italiano; la lettura continua a funzionare.

COMANDI_ST = {  # cosa -> (componente SmartThings, nome da mostrare)
    "riscaldamento": ("INDOOR", "Riscaldamento casa"),
    "acqua": ("main", "Acqua calda sanitaria"),
}
NOME_COMANDO = {"riscaldamento": "Riscaldamento casa", "acqua": "Acqua calda sanitaria", "smarther": "Termostato Smarther"}
_lock_comandi = threading.Lock()
_ultime_azioni = {}


def _troppo_presto(chiave, secondi):
    """Antirimbalzo: True se la stessa azione e' partita meno di `secondi` fa; altrimenti la segna come partita adesso."""
    with _lock_comandi:
        if time.time() - _ultime_azioni.get(chiave, 0) < secondi:
            return True
        _ultime_azioni[chiave] = time.time()
        return False


def _servizio(app, sigla):
    return next((s for s in app.servizi if s.sigla == sigla), None)


def _sveglia_tra(sv, *ritardi):
    """Fa rileggere il servizio dopo ciascun ritardo (secondi): SmartThings a volte e' lento ad aggiornare."""
    for ritardo in ritardi:
        t = threading.Timer(ritardo, sv.sveglia.set)
        t.daemon = True
        t.start()


def _rileggi_dopo(app, sigla):
    """Dopo un comando si rilegge lo stato dopo qualche secondo (e ancora un po' piu' tardi), cosi' la pagina si allinea."""
    sv = _servizio(app, sigla)
    if not sv:
        return
    sv.ult_info = 0  # Netatmo: rilegge anche la modalita' della casa (homesdata)
    _sveglia_tra(sv, 4, 20)


def _comando_st(app, cosa, acceso):
    comp = COMANDI_ST[cosa][0]
    cfg = app.cfg.leggi()["smartthings"]
    sv = _servizio(app, "st")
    dev = cfg.get("device_id") or (sv._trova_device() if sv else None)
    if not dev:
        raise ErroreAPI("richiesta", 0, "SmartThings: non trovo la pompa di calore.")
    corpo = json.dumps({"commands": [{"component": comp, "capability": "switch",
                                      "command": "on" if acceso else "off"}]}).encode()
    for tentativo in (1, 2):
        token = app.token_st.get()
        code, js = http_json("POST", f"{ST_API}/devices/{dev}/commands",
                             {"Authorization": "Bearer " + token, "Content-Type": "application/json",
                              "Accept": "application/json"}, corpo)
        if code == 401 and tentativo == 1 and not app.token_st.usa_pat():
            app.token_st.invalida()
            continue
        break
    if code in (401, 403):
        raise ErroreAPI("auth", code, "Il token SmartThings non ha il permesso di COMANDO (serve lo scope x:devices:*): "
                                      "rigeneralo come spiegato nel README, paragrafo sui comandi.")
    if code >= 400:
        raise classifica("SmartThings", code, js)
    esiti = [str(r.get("status", "")).upper() for r in (js.get("results") or []) if isinstance(r, dict)]
    if esiti and not any(e in ("ACCEPTED", "COMPLETED") for e in esiti):
        raise ErroreAPI("richiesta", code, "SmartThings ha rifiutato il comando (" + ", ".join(esiti) + ").")
    _rileggi_dopo(app, "st")


def _nt_chiama(app, percorso, dati, come_json=False):
    """Una chiamata di scrittura a Netatmo. Se non va, ErroreAPI con la risposta vera di Netatmo (non una mia ipotesi)."""
    for tentativo in (1, 2):
        token = app.token_nt.get()
        if come_json:
            h = {"Authorization": "Bearer " + token, "Accept": "application/json", "Content-Type": "application/json"}
            code, js = http_json("POST", NT_BASE + percorso, h, json.dumps(dati).encode())
        else:
            h = {"Authorization": "Bearer " + token, "Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"}
            code, js = http_json("POST", NT_BASE + percorso, h, dati)
        testo = json.dumps(js, ensure_ascii=False)[:200] if js else ""
        # il token si rinnova solo se Netatmo dice che e' il token il problema (un 403 puo' avere altri motivi)
        if tentativo == 1 and (code == 401 or (code == 403 and "token" in testo.lower())):
            app.token_nt.invalida()
            continue
        break
    if code >= 400 or (isinstance(js, dict) and "error" in js):
        raise ErroreAPI("richiesta", code, f"{percorso} -> HTTP {code} {testo}")


def _prova_in_ordine(tentativi, verifica=None):
    """Esegue i tentativi finche' uno riesce DAVVERO: una risposta "ok" di Netatmo non basta, se dopo qualche
    secondo lo stato non e' cambiato si passa al tentativo dopo. -> testo con il tentativo riuscito e gli scartati."""
    risposte = []
    for nome, f in tentativi:
        try:
            f()
        except ErroreAPI as e:
            risposte.append(f"{nome}: {e.messaggio}")
            continue
        if verifica:
            time.sleep(3)
            try:
                ok = verifica()
            except Exception:
                ok = None  # non riesco a controllare: mi fido della risposta
            if ok is False:
                risposte.append(f"{nome}: accettato da Netatmo ma lo stato non e' cambiato")
                continue
        return nome + (" (scartati: " + " | ".join(risposte) + ")" if risposte else "")
    raise ErroreAPI("richiesta", 0, "Netatmo non ha eseguito il comando. " + " | ".join(risposte)
                    + " (se compaiono 403/permessi: rigenera i token con write_smarther, vedi README)")


def _comando_nt(app, acceso):
    cfg = app.cfg.leggi()["netatmo"]
    home, room = cfg.get("home_id"), cfg.get("room_id")
    if not home or not room:  # li scrive la lettura quando trova la casa
        ult = app.db.ultimi_dict()
        home = home or (ult.get("info.nt.casa_id") or (0, 0, 0, ""))[3]
        room = room or (ult.get("info.nt.stanza_id") or (0, 0, 0, ""))[3]
    if not home or not room:
        raise ErroreAPI("richiesta", 0, "Netatmo: non conosco ancora la casa e la stanza. Aspetta una lettura e riprova, "
                                        "oppure scrivi home_id e room_id in config.json.")
    # Lo Smarther puo' risultare spento in due modi: dal pannello la CASA va in antigelo (hg) e la stanza resta in
    # "home"; dalla sua app invece e' la STANZA ad andare in hg con la casa in "schedule". Per accendere vanno
    # quindi sistemate tutte e due: casa in programma E stanza in "home".
    def stato_reale():
        sv = _servizio(app, "nt")
        homes = (sv._get("/api/homesdata").get("body") or {}).get("homes") or []
        casa = next((h.get("therm_mode") for h in homes if h.get("id") == home), None)
        stanze = ((sv._get("/api/homestatus", {"home_id": home}).get("body") or {}).get("home") or {}).get("rooms") or []
        st = next((r.get("therm_setpoint_mode") for r in stanze if r.get("id") == room), None)
        return casa, st

    def verifica():
        casa, st = stato_reale()
        if casa is None and st is None:
            return None
        if acceso:
            return casa == "schedule" and st not in ("hg", "off")
        return casa == "hg" or st in ("hg", "off")

    def stanza_home():
        _nt_chiama(app, "/api/setstate", {"home": {"id": home, "rooms": [
            {"id": room, "therm_setpoint_mode": "home"}]}}, come_json=True)

    def accendi():
        _nt_chiama(app, "/api/setthermmode", {"home_id": home, "mode": "schedule"})
        try:
            stanza_home()
        except ErroreAPI:
            pass  # se la stanza e' gia' in programma non serve; la verifica dira' se e' rimasta in antigelo

    if acceso:
        tentativi = [
            ("casa+stanza", accendi),
            ("setroomthermpoint", lambda: _nt_chiama(app, "/api/setroomthermpoint",
                                                      {"home_id": home, "room_id": room, "mode": "home"})),
            ("setstate-stanza", stanza_home),
        ]
    else:
        tentativi = [
            ("setthermmode", lambda: _nt_chiama(app, "/api/setthermmode", {"home_id": home, "mode": "hg"})),
            ("setroomthermpoint", lambda: _nt_chiama(app, "/api/setroomthermpoint",
                                                      {"home_id": home, "room_id": room, "mode": "off"})),
            ("setstate-stanza", lambda: _nt_chiama(app, "/api/setstate", {"home": {"id": home, "rooms": [
                {"id": room, "therm_setpoint_mode": "hg"}]}}, come_json=True)),
        ]
    metodo = _prova_in_ordine(tentativi, verifica)
    _rileggi_dopo(app, "nt")
    return metodo


def _scrivi_stato(app, serie, valore):
    """Una riga nel registro dei cambi (tabella stati): e' li' che stanno comandi, modifiche FSV e regolazioni."""
    with closing(app.db._c()) as c, c:
        c.execute("INSERT OR REPLACE INTO stati VALUES(?,?,?)", (serie, int(time.time()), valore))


def _esito_txt(x, n=600):
    return str(x).replace("|", "/")[:n]      # il separatore del registro e' «|»


def _registra_comando(app, cosa, acceso, ip, esito):
    _scrivi_stato(app, f"cmd.{cosa}", f"{'acceso' if acceso else 'spento'}|{ip}|{_esito_txt(esito, 900)}")
    log.info("comando dal pannello: %s -> %s da %s (%s)", NOME_COMANDO.get(cosa, cosa),
             "acceso" if acceso else "spento", ip, esito)


def esegui_comando(app, cosa, acceso, ip):
    """Un comando alla pompa o allo Smarther, con le stesse regole per tutti (pagina, plugin): -> {'ok': bool, 'errore'?}.
    'ip' dice da dove e' partito e finisce nel registro dei comandi."""
    if cosa not in NOME_COMANDO or not isinstance(acceso, bool):
        return {"ok": False, "errore": "Comando non valido."}
    if app.demo:
        return {"ok": True, "demo": True}
    if _troppo_presto("cmd." + cosa, 2):
        return {"ok": False, "errore": "Un attimo: il comando precedente e' appena partito."}
    metodo = ""
    try:
        if cosa == "smarther":
            metodo = _comando_nt(app, acceso)
        else:
            _comando_st(app, cosa, acceso)
    except ErroreAPI as e:
        _registra_comando(app, cosa, acceso, ip, "non riuscito: " + e.messaggio)
        return {"ok": False, "errore": e.messaggio}
    except ErroreConfig as e:
        _registra_comando(app, cosa, acceso, ip, "non riuscito: " + str(e))
        return {"ok": False, "errore": str(e)}
    _registra_comando(app, cosa, acceso, ip, "inviato" + (f" ({metodo})" if metodo else ""))
    return {"ok": True}


@api_post("/api/controlli")
def api_controlli(app, corpo, gestore):
    return esegui_comando(app, corpo.get("cosa"), corpo.get("acceso"), ip_client(gestore))


def elenco_comandi(app, massimo=100):
    """Gli ultimi comandi dati dal pannello: [{ts, cosa, acceso, ip, esito}], dal piu' recente."""
    with closing(app.db._c()) as c:
        righe = c.execute("SELECT serie, ts, valore FROM stati WHERE serie LIKE 'cmd.%' ORDER BY ts DESC LIMIT ?",
                          (massimo,)).fetchall()
    out = []
    for serie, ts, v in righe:
        parti = (v.split("|") + ["", "", ""])[:3]
        if serie.startswith("cmd.fsv") or serie.startswith("cmd.reg"):   # modifica non riuscita: il testo dice cosa e valori
            out.append({"ts": ts, "cosa": "Impostazione installatore" if serie.startswith("cmd.fsv") else "Regolazione",
                        "acceso": False, "testo": parti[0],
                        "ip": parti[1], "esito": parti[2]})
            continue
        out.append({"ts": ts, "cosa": NOME_COMANDO.get(serie[4:], serie[4:]), "acceso": parti[0] == "acceso",
                    "ip": parti[1], "esito": parti[2]})
    return out


# --------------------------------------------------------------------------
# Modifica delle impostazioni installatore (FSV) dalla scheda Installatore
# --------------------------------------------------------------------------
# SmartThings espone gli FSV con la funzione samsungce.ehsFsvSettings. Il nome del comando e i suoi argomenti non sono
# scritti da nessuna parte nel pannello: si chiede la definizione a SmartThings (una volta al giorno) e si costruisce
# il comando da li'. Se la definizione non e' chiara, NON si scrive niente e si mostra cosa e' stato trovato.
FSV_CAPABILITY = "samsungce.ehsFsvSettings"


def _definizione_fsv(app, sv):
    """La definizione della funzione FSV secondo SmartThings: dict. Si tiene in cache 24 ore."""
    grezzo = app.db.meta_get("fsv_definizione")
    ts = float(app.db.meta_get("fsv_definizione_ts", 0) or 0)
    if grezzo and time.time() - ts < 86400:
        try:
            return json.loads(grezzo)
        except ValueError:
            pass
    d = sv._get(f"/capabilities/{FSV_CAPABILITY}/1")
    app.db.meta_set("fsv_definizione", json.dumps(d, ensure_ascii=False))
    app.db.meta_set("fsv_definizione_ts", int(time.time()))
    return d


def _tipo(schema):
    return (schema or {}).get("type") if isinstance(schema, dict) else None


def _numero_per(schema, n):
    return int(n) if _tipo(schema) == "integer" else n


def costruisci_comando_fsv(defn, codice, valore):
    """-> {'component','capability','command','arguments'}. Alza ErroreAPI (con la definizione) se non si capisce."""
    cmds = defn.get("commands") if isinstance(defn, dict) else None
    riassunto = json.dumps(cmds, ensure_ascii=False)[:700] if cmds else "nessun comando"

    def non_capisco(perche):
        return ErroreAPI("richiesta", 0, f"{perche} Non scrivo niente. Definizione trovata: {riassunto}")

    if not isinstance(cmds, dict) or not cmds:
        raise non_capisco("SmartThings non indica nessun comando per le impostazioni installatore.")
    scelto = next((n for n in ("setFsvSettings", "setFsvSetting", "setFsvValue", "setValue") if n in cmds), None)
    if not scelto:
        c = [n for n in cmds if "fsv" in n.lower()] or [n for n in cmds if n.lower().startswith("set")]
        if len(c) == 1:
            scelto = c[0]
    if not scelto:
        raise non_capisco("Non so quale comando serve (ce ne sono diversi).")
    args = cmds[scelto].get("arguments") or []
    if len(args) == 1:
        schema = args[0].get("schema") or {}
        t = _tipo(schema)
        elemento = schema.get("items") if t == "array" else schema if t == "object" else None
        props = (elemento or {}).get("properties") if isinstance(elemento, dict) else None
        if not isinstance(props, dict) or not props:
            raise non_capisco("Il formato dell'argomento non e' quello che mi aspetto.")
        oggetto = {}
        for nome, sch in props.items():
            low = nome.lower()
            if low == "id" or low.endswith("id"):
                oggetto[nome] = int(codice) if _tipo(sch) == "integer" else str(codice)
            elif "value" in low and "min" not in low and "max" not in low:
                oggetto[nome] = _numero_per(sch, valore)
            elif nome in (elemento.get("required") or []):
                raise non_capisco(f"Il comando richiede anche '{nome}', che non so cosa sia.")
        if len(oggetto) < 2:
            raise non_capisco("Non trovo dove mettere codice e valore.")
        argomenti = [[oggetto] if t == "array" else oggetto]
    elif len(args) == 2:
        argomenti = []
        for a in args:
            low = str(a.get("name", "")).lower()
            if low == "id" or low.endswith("id"):
                argomenti.append(int(codice) if _tipo(a.get("schema")) == "integer" else str(codice))
            elif "value" in low:
                argomenti.append(_numero_per(a.get("schema"), valore))
            else:
                raise non_capisco(f"Argomento '{a.get('name')}' sconosciuto.")
    else:
        raise non_capisco("Il numero di argomenti non e' quello che mi aspetto.")
    return {"component": "main", "capability": FSV_CAPABILITY, "command": scelto, "arguments": argomenti}


def _registra_fsv(app, codice, prima, dopo, ip, esito):
    _scrivi_stato(app, f"fsvmod.{codice}", f"{prima}|{dopo}|{ip}|{_esito_txt(esito)}")
    log.info("FSV dal pannello: %s %s -> %s da %s (%s)", codice, prima, dopo, ip, esito)


def _posta_st(app, dev, comando):
    corpo = json.dumps({"commands": [comando]}).encode()
    for tentativo in (1, 2):
        token = app.token_st.get()
        code, js = http_json("POST", f"{ST_API}/devices/{dev}/commands",
                             {"Authorization": "Bearer " + token, "Content-Type": "application/json",
                              "Accept": "application/json"}, corpo)
        if code == 401 and tentativo == 1 and not app.token_st.usa_pat():
            app.token_st.invalida()
            continue
        break
    if code in (401, 403):
        raise ErroreAPI("auth", code, "Il token SmartThings non ha il permesso di COMANDO (serve lo scope x:devices:*).")
    if code >= 400:
        raise classifica("SmartThings", code, js)
    esiti = [str(r.get("status", "")).upper() for r in (js.get("results") or []) if isinstance(r, dict)]
    if esiti and not any(e in ("ACCEPTED", "COMPLETED") for e in esiti):
        raise ErroreAPI("richiesta", code, "SmartThings ha rifiutato il comando (" + ", ".join(esiti) + ").")


def modifica_fsv(app, codice, valore, ip):
    """Cambia UNA impostazione installatore: -> {'ok': bool, 'errore'?, 'prima'?, 'dopo'?}. Il valore va letto di nuovo
    dalla pompa: SmartThings puo' metterci un po' ad aggiornarlo, e finche' non lo fa la scheda dice 'in attesa'."""
    codice = str(codice or "").strip()
    n, err = fsv_valore_valido(codice, valore)
    if err:
        return {"ok": False, "errore": err}
    ult = app.db.ultimi_dict()
    r = ult.get("fsv." + codice)
    if not r:
        return {"ok": False, "errore": "La pompa non ha comunicato questo codice: non lo modifico."}
    prima = r[3] if r[3] not in (None, "") else (_num_it(r[2]) if r[2] is not None else "")
    if _fsv_numero(prima) == n:
        return {"ok": False, "errore": "E' gia' a questo valore."}
    if app.demo:   # nella demo il valore cambia davvero nel database finto, cosi' si vede tutto il giro
        t = int(time.time())
        with closing(app.db._c()) as c, c:
            c.execute("INSERT OR REPLACE INTO stati VALUES(?,?,?)", (f"fsv.{codice}", t, str(n)))
            c.execute("INSERT INTO ultimo(serie,ts,num,txt) VALUES(?,?,NULL,?) ON CONFLICT(serie) DO UPDATE SET "
                      "ts=excluded.ts, txt=excluded.txt", (f"fsv.{codice}", t, str(n)))
        _registra_fsv(app, codice, prima, str(n), ip, "inviato (demo)")
        return {"ok": True, "demo": True, "prima": prima, "dopo": str(n)}
    if _troppo_presto("fsv", 5):
        return {"ok": False, "errore": "Un attimo: la modifica precedente e' appena partita."}
    try:
        sv, dev = _dispositivo_st(app)
        comando = costruisci_comando_fsv(_definizione_fsv(app, sv), codice, n)
        _posta_st(app, dev, comando)
    except ErroreAPI as e:
        _registra_fsv_rifiutata(app, codice, prima, n, ip, e.messaggio)
        return {"ok": False, "errore": e.messaggio}
    except ErroreConfig as e:
        _registra_fsv_rifiutata(app, codice, prima, n, ip, str(e))
        return {"ok": False, "errore": str(e)}
    _registra_fsv(app, codice, prima, str(n), ip, f"inviato ({comando['command']})")
    sv.ult_info = 0
    _sveglia_tra(sv, 4, 20, 60, 180, 600)
    return {"ok": True, "prima": prima, "dopo": str(n)}


def _registra_fsv_rifiutata(app, codice, prima, n, ip, motivo):
    """Una modifica non riuscita non e' una modifica: va solo nel registro dei comandi, come gli altri tentativi."""
    _scrivi_stato(app, f"cmd.fsv{codice}", f"{codice}: {prima} -> {n}|{ip}|non riuscito: {_esito_txt(motivo)}")
    log.info("FSV dal pannello non riuscito: %s %s -> %s (%s)", codice, prima, n, motivo)


SOLO_CASA = "Questa modifica si fa solo da casa, collegato alla rete locale (non da fuori)."


def _fuori_casa(gestore, cosa):
    """True (e lo scrive nel log) se la richiesta non viene dalla rete di casa."""
    if gestore is not None and not da_casa(gestore):
        log.warning("modifica '%s' rifiutata: richiesta da fuori casa (%s)", cosa, ip_client(gestore))
        return True
    return False


@api_post("/api/fsv")
def api_fsv(app, corpo, gestore):
    if _fuori_casa(gestore, "fsv"):
        return {"ok": False, "errore": SOLO_CASA}
    return modifica_fsv(app, corpo.get("codice"), corpo.get("valore"), ip_client(gestore))


# --------------------------------------------------------------------------
# Modifica di temperatura/modo dell'acqua calda e del modo della pompa
# --------------------------------------------------------------------------


def valida_regolazione(app, cosa, valore, ult):
    """-> (valore pulito, errore)"""
    d = REGOLAZIONI.get(cosa)
    if not d:
        return None, "Regolazione sconosciuta."
    if cosa in REG_MODI:
        v = str(valore or "").strip()
        if v not in [k for k, _, _ in REG_MODI[cosa]]:
            return None, "Modo non previsto per questa pompa."
        return v, None
    try:
        n = float(str(valore).replace(",", "."))
    except (TypeError, ValueError):
        return None, "Il valore non e' un numero."
    lo, hi = reg_limiti_acs(ult)
    if n != int(n):
        return None, "La temperatura va a gradi interi."
    n = int(n)
    if not (lo <= n <= hi):
        return None, f"Fuori dai limiti della pompa (da {lo} a {hi} °C)."
    return n, None


def _registra_reg(app, cosa, prima, dopo, ip, esito):
    _scrivi_stato(app, f"regmod.{cosa}", f"{prima}|{dopo}|{ip}|{_esito_txt(esito)}")
    log.info("regolazione dal pannello: %s %s -> %s da %s (%s)", cosa, prima, dopo, ip, esito)


def _registra_reg_rifiutata(app, cosa, prima, dopo, ip, motivo):
    nome = REGOLAZIONI[cosa]["nome"]
    _scrivi_stato(app, f"cmd.reg{cosa}", f"{nome}: {reg_testo(cosa, prima)} -> {reg_testo(cosa, dopo)}|{ip}|non riuscito: {_esito_txt(motivo)}")
    log.info("regolazione dal pannello non riuscita: %s %s -> %s (%s)", cosa, prima, dopo, motivo)


def modifica_regolazione(app, cosa, valore, ip):
    """Cambia UNA regolazione: -> {'ok': bool, 'errore'?}. Come per gli FSV, il valore va riletto dalla pompa."""
    ult = app.db.ultimi_dict()
    v, err = valida_regolazione(app, cosa, valore, ult)
    if err:
        return {"ok": False, "errore": err}
    d = REGOLAZIONI[cosa]
    r = ult.get(d["serie"])
    prima = (r[2] if d["tipo"] == "num" else r[3]) if r else None
    if prima is None:
        return {"ok": False, "errore": "La pompa non ha ancora comunicato il valore di adesso: non modifico."}
    if _reg_uguali(cosa, prima, v):
        return {"ok": False, "errore": "E' gia' a questo valore."}
    if app.demo:
        t = int(time.time())
        rec = Rec()
        if d["tipo"] == "num":
            rec.num.append((d["serie"], t, float(v), True))
        else:
            rec.stati[d["serie"]] = str(v)
        app.db.salva(rec, t)
        _registra_reg(app, cosa, prima, v, ip, "inviato (demo)")
        return {"ok": True, "demo": True}
    if _troppo_presto("reg", 3):
        return {"ok": False, "errore": "Un attimo: la modifica precedente e' appena partita."}
    try:
        sv, dev = _dispositivo_st(app)
        _posta_st(app, dev, {"component": d["comp"], "capability": d["cap"], "command": d["cmd"],
                             "arguments": [v]})
    except ErroreAPI as e:
        _registra_reg_rifiutata(app, cosa, prima, v, ip, e.messaggio)
        return {"ok": False, "errore": e.messaggio}
    except ErroreConfig as e:
        _registra_reg_rifiutata(app, cosa, prima, v, ip, str(e))
        return {"ok": False, "errore": str(e)}
    _registra_reg(app, cosa, prima, v, ip, f"inviato ({d['cmd']})")
    sv.ult_info = 0
    _sveglia_tra(sv, 4, 20, 60, 180)
    return {"ok": True}


@api_post("/api/regolazioni")
def api_regolazioni(app, corpo, gestore):
    if _fuori_casa(gestore, str(corpo.get("cosa"))):
        return {"ok": False, "errore": SOLO_CASA}
    return modifica_regolazione(app, corpo.get("cosa"), corpo.get("valore"), ip_client(gestore))


# --------------------------------------------------------------------------
# Boost acqua calda: come Dwellsee. Setpoint piu' alto, POI modo "force"; alla fine modo e setpoint di prima.
# Lo stato sta nel database (stati 'boost.attivo'), cosi' il ripristino riparte anche dopo un riavvio.
# --------------------------------------------------------------------------
BOOST_MAX_S = 7200          # dopo 2 ore si ripristina comunque
BOOST_PREDEFINITO = 50
BOOST_PAUSA_S = 20          # ogni quanto il pannello controlla se il boost e' finito
_boost_thread = [None]
_boost_lock = threading.Lock()


def _boost_leggi(app):
    with closing(app.db._c()) as c:
        r = c.execute("SELECT valore FROM stati WHERE serie='boost.attivo' ORDER BY ts DESC LIMIT 1").fetchone()
    try:
        d = json.loads(r[0]) if r and r[0] else None
    except ValueError:
        d = None
    return d or None


def _boost_scrivi(app, d):
    with closing(app.db._c()) as c, c:
        c.execute("DELETE FROM stati WHERE serie='boost.attivo'")      # la chiave e' (serie, ts): si tiene una riga sola
        c.execute("INSERT OR REPLACE INTO stati VALUES(?,?,?)", ("boost.attivo", int(time.time()), json.dumps(d) if d else ""))


def _boost_manda(app, sv, dev, cosa, v):
    """Un comando del boost (setpoint o modo). In demo scrive il valore nel database."""
    d = REGOLAZIONI[cosa]
    if app.demo:
        t = int(time.time())
        rec = Rec()
        if d["tipo"] == "num":
            rec.num.append((d["serie"], t, float(v), True))
        else:
            rec.stati[d["serie"]] = str(v)
        app.db.salva(rec, t)
        return
    _posta_st(app, dev, {"component": d["comp"], "capability": d["cap"], "command": d["cmd"], "arguments": [v]})


def _dispositivo_st(app):
    if app.demo:
        return None, None
    sv = _servizio(app, "st")
    dev = app.cfg.leggi()["smartthings"].get("device_id") or (sv._trova_device() if sv else None)
    if not dev or not sv:
        raise ErroreAPI("richiesta", 0, "SmartThings: non trovo la pompa di calore.")
    return sv, dev


def _boost_rileggi(app, sv):
    if sv is None:
        return
    sv.ult_info = 0
    _sveglia_tra(sv, 4, 20, 60)


def _boost_ripristina(app, motivo, ip="pannello"):
    """Rimette modo e setpoint di prima (nell'ordine di Dwellsee: modo, poi setpoint). -> (ok, errore)"""
    with _boost_lock:
        b = _boost_leggi(app)
        if not b:
            return True, None
        try:
            sv, dev = _dispositivo_st(app)
            ora_sp = app.db.ultimi_dict().get("acs.setpoint")
            if b.get("modo_prima"):
                _boost_manda(app, sv, dev, "acs_modo", b["modo_prima"])
                _registra_reg(app, "acs_modo", "force", b["modo_prima"], ip, f"inviato (fine boost: {motivo})")
            if b.get("sp_prima") is not None:
                _boost_manda(app, sv, dev, "acs_setpoint", int(b["sp_prima"]))
                _registra_reg(app, "acs_setpoint", ora_sp[2] if ora_sp else "", int(b["sp_prima"]), ip,
                              f"inviato (fine boost: {motivo})")
        except (ErroreAPI, ErroreConfig) as e:
            msg = getattr(e, "messaggio", None) or str(e)
            b["errore"] = f"Ripristino non riuscito ({msg}). Riprovo da solo."
            b["tentativi"] = int(b.get("tentativi", 0)) + 1
            _boost_scrivi(app, b)
            log.warning("boost: ripristino fallito: %s", msg)
            return False, msg
        _boost_scrivi(app, None)
        log.info("boost finito (%s): modo %s, setpoint %s", motivo, b.get("modo_prima"), b.get("sp_prima"))
        _boost_rileggi(app, sv)
        return True, None


def _boost_thread_finito(app):
    """True (e libera il posto) solo se non c'e' piu' nessun boost: cosi' un boost nuovo non resta senza controllo."""
    with _boost_lock:
        if _boost_leggi(app) is None:
            _boost_thread[0] = None
            return True
    return False


def _boost_vigila(app):
    while True:
        time.sleep(BOOST_PAUSA_S)
        b = _boost_leggi(app)
        if not b:
            if _boost_thread_finito(app):
                return
            continue
        ora = time.time()
        t = app.db.ultimi_dict().get("acs.temperatura")
        motivo = None
        if ora >= b["scade"]:
            motivo = "tempo massimo"
        elif t and t[2] is not None and ora - b["inizio"] > 120 and ora - t[1] < 900 and t[2] >= b["obiettivo"]:
            motivo = f"acqua a {t[2]:g} °C"
        if motivo:
            ok, _ = _boost_ripristina(app, motivo)
            if not ok and int(b.get("tentativi", 0)) >= 30:
                _boost_scrivi(app, None)          # dopo troppi tentativi si rinuncia (resta il registro dei comandi)
            if _boost_thread_finito(app):
                return


def boost_riprendi(app):
    """Se c'e' un boost in corso e nessuno lo controlla (dopo un riavvio), riprende a controllarlo."""
    if not _boost_leggi(app):
        return
    with _boost_lock:
        if _boost_thread[0] and _boost_thread[0].is_alive():
            return
        th = threading.Thread(target=_boost_vigila, args=(app,), daemon=True, name="boost")
        _boost_thread[0] = th
        th.start()


def boost_dati(app, ult, adesso):
    b = _boost_leggi(app)
    if not b:
        return {"attivo": False, "massimo_min": BOOST_MAX_S // 60, "predefinito": BOOST_PREDEFINITO}
    t = ult.get("acs.temperatura")
    return {"attivo": True, "inizio": b["inizio"], "scade": b["scade"], "obiettivo": b["obiettivo"],
            "sp_prima": b.get("sp_prima"), "modo_prima": b.get("modo_prima"), "errore": b.get("errore"),
            "temperatura": t[2] if t else None, "massimo_min": BOOST_MAX_S // 60, "predefinito": BOOST_PREDEFINITO}


def boost_avvia(app, obiettivo, ip):
    ult = app.db.ultimi_dict()
    if _boost_leggi(app):
        return {"ok": False, "errore": "C'e' gia' un boost in corso."}
    try:
        n = float(str(obiettivo).replace(",", "."))
    except (TypeError, ValueError):
        return {"ok": False, "errore": "La temperatura non e' un numero."}
    lo, hi = reg_limiti_acs(ult)
    if n != int(n) or not (lo <= n <= hi):
        return {"ok": False, "errore": f"La temperatura va a gradi interi, da {lo} a {hi} °C."}
    n = int(n)
    sp, modo, temp = ult.get("acs.setpoint"), ult.get("pdc.modo"), ult.get("acs.temperatura")
    if not sp or sp[2] is None or not modo or not modo[3]:
        return {"ok": False, "errore": "La pompa non ha ancora comunicato setpoint e modo di adesso: non parto."}
    if temp and temp[2] is not None and temp[2] >= n:
        return {"ok": False, "errore": f"L'acqua e' gia' a {temp[2]:g} °C: il boost non serve."}
    if _troppo_presto("reg", 3):
        return {"ok": False, "errore": "Un attimo: la modifica precedente e' appena partita."}
    prima_sp, prima_modo = int(sp[2]), str(modo[3])
    try:
        sv, dev = _dispositivo_st(app)
        if n != prima_sp:
            _boost_manda(app, sv, dev, "acs_setpoint", n)       # prima il setpoint...
            _registra_reg(app, "acs_setpoint", prima_sp, n, ip, "inviato (boost)")
        try:
            _boost_manda(app, sv, dev, "acs_modo", "force")     # ...poi il modo, come Dwellsee
        except (ErroreAPI, ErroreConfig):
            if n != prima_sp:                                    # il boost non e' partito: rimette il setpoint
                try:
                    _boost_manda(app, sv, dev, "acs_setpoint", prima_sp)
                except (ErroreAPI, ErroreConfig):
                    log.warning("boost: setpoint non rimesso dopo il rifiuto del modo")
            raise
    except ErroreAPI as e:
        _registra_reg_rifiutata(app, "acs_modo", prima_modo, "force", ip, e.messaggio)
        return {"ok": False, "errore": e.messaggio}
    except ErroreConfig as e:
        _registra_reg_rifiutata(app, "acs_modo", prima_modo, "force", ip, str(e))
        return {"ok": False, "errore": str(e)}
    _registra_reg(app, "acs_modo", prima_modo, "force", ip, "inviato (boost)")
    t = int(time.time())
    _boost_scrivi(app, {"inizio": t, "scade": t + BOOST_MAX_S, "obiettivo": n, "sp_prima": prima_sp,
                        "modo_prima": prima_modo, "ip": ip})
    log.info("boost avviato da %s: obiettivo %s, prima modo %s setpoint %s", ip, n, prima_modo, prima_sp)
    _boost_rileggi(app, sv)
    boost_riprendi(app)
    return {"ok": True}


@api_post("/api/boost")
def api_boost(app, corpo, gestore):
    if _fuori_casa(gestore, "boost"):
        return {"ok": False, "errore": SOLO_CASA}
    ip = ip_client(gestore)
    if corpo.get("azione") == "ferma":
        if not _boost_leggi(app):
            return {"ok": False, "errore": "Non c'e' nessun boost in corso."}
        ok, err = _boost_ripristina(app, "fermato a mano", ip)
        return {"ok": ok, "errore": None if ok else f"Ripristino non riuscito: {err}. Il pannello riprova da solo."}
    return boost_avvia(app, corpo.get("obiettivo"), ip)
