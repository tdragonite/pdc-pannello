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
_ultimo_comando = {}
_lock_comandi = threading.Lock()


def _servizio(app, sigla):
    return next((s for s in app.servizi if s.sigla == sigla), None)


def _rileggi_dopo(app, sigla):
    """Dopo un comando si rilegge lo stato dopo qualche secondo (e ancora un po' piu' tardi), cosi' la pagina si allinea."""
    sv = _servizio(app, sigla)
    if not sv:
        return
    sv.ult_info = 0  # Netatmo: rilegge anche la modalita' della casa (homesdata)
    for ritardo in (4, 20):
        t = threading.Timer(ritardo, sv.sveglia.set)
        t.daemon = True
        t.start()


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
        ult = {r[0]: r for r in app.db.ultimi()}
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


def _registra_comando(app, cosa, acceso, ip, esito):
    t = int(time.time())
    voce = f"{'acceso' if acceso else 'spento'}|{ip}|{str(esito).replace('|', '/')[:900]}"
    with closing(app.db._c()) as c, c:
        c.execute("INSERT OR REPLACE INTO stati VALUES(?,?,?)", (f"cmd.{cosa}", t, voce))
    log.info("comando dal pannello: %s -> %s da %s (%s)", NOME_COMANDO.get(cosa, cosa),
             "acceso" if acceso else "spento", ip, esito)


def esegui_comando(app, cosa, acceso, ip):
    """Un comando alla pompa o allo Smarther, con le stesse regole per tutti (pagina, plugin): -> {'ok': bool, 'errore'?}.
    'ip' dice da dove e' partito e finisce nel registro dei comandi."""
    if cosa not in NOME_COMANDO or not isinstance(acceso, bool):
        return {"ok": False, "errore": "Comando non valido."}
    if app.demo:
        return {"ok": True, "demo": True}
    with _lock_comandi:
        if time.time() - _ultimo_comando.get(cosa, 0) < 2:
            return {"ok": False, "errore": "Un attimo: il comando precedente e' appena partito."}
        _ultimo_comando[cosa] = time.time()
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
        out.append({"ts": ts, "cosa": NOME_COMANDO.get(serie[4:], serie[4:]), "acceso": parti[0] == "acceso",
                    "ip": parti[1], "esito": parti[2]})
    return out
