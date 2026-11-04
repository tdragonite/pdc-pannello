# ==========================================================================
# COMANDI
# ==========================================================================

def imposta_log(base):
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%d/%m %H:%M:%S")
    fh = logging.handlers.RotatingFileHandler(cartella_log(base) / "pdc_pannello.log", maxBytes=1_000_000,
                                              backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if sys.stderr is not None:  # con pythonw (senza terminale) non c'e'
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        log.addHandler(sh)


def fmt_val(num, txt):
    if num is not None:
        return f"{num:g}"
    return txt if txt is not None else "-"


def cmd_stato(app):
    righe = app.db.ultimi()
    if not righe:
        print("Nessun dato salvato ancora. Avvia prima la raccolta.")
        return
    adesso = time.time()
    print(f"{'serie':26} {'valore':>12}   ultimo aggiornamento")
    for serie, ts, num, txt in righe:
        if serie.startswith("fsv."):
            continue
        eta = int(adesso - ts)
        quando = f"{eta} s fa" if eta < 120 else (f"{eta // 60} min fa" if eta < 7200 else f"{eta // 3600} h fa")
        print(f"{serie:26} {fmt_val(num, txt):>12}   {datetime.fromtimestamp(ts):%d/%m %H:%M:%S} ({quando})")
    nf = sum(1 for r in righe if r[0].startswith("fsv."))
    print(f"\n(+ {nf} impostazioni FSV della PdC salvate)")
    ac = info_accesso_st(app)
    print("Accesso SmartThings:", {"oauth": "OAuth (si rinnova da solo)", "pat": "token personale PAT (24 ore)"}.get(ac["tipo"], ac["tipo"]))
    aperti = [e for e in app.db.eventi_recenti(30) if e[4] is None]
    for _, cod, liv, testo, _ in aperti:
        print(f"ANOMALIA APERTA [{cod}]: {testo}")


def cmd_diagnostica(app):
    cfg = app.cfg.leggi()
    da = int(time.time()) - 72 * 3600
    out = {"versione": VERSIONE, "generato": datetime.now().isoformat(timespec="seconds"),
           "mappa_ciclo": cfg["mappa_ciclo"], "campioni_ciclo": [], "serie": {}, "stati": {}}
    with closing(app.db._c()) as c:
        for tipo, ts, hx in c.execute("SELECT tipo, ts, hex FROM ciclo WHERE ts>=? ORDER BY ts", (da,)):
            _, valori, problema = decodifica_ciclo(tipo, hx, cfg["mappa_ciclo"])
            out["campioni_ciclo"].append({"ts": ts, "ora_utc": datetime.fromtimestamp(ts, timezone.utc)
                                          .strftime("%H:%M:%S"), "tipo": tipo, "hex": hx,
                                          "decodificato": valori, "problema": problema})
        for serie, ts, v in c.execute("SELECT serie, ts, valore FROM misure WHERE ts>=? AND serie NOT LIKE 'ciclo.%' "
                                      "ORDER BY serie, ts", (da,)):
            out["serie"].setdefault(serie, []).append([ts, v])
        for serie, ts, v in c.execute("SELECT serie, ts, valore FROM stati WHERE ts>=? AND serie NOT LIKE 'fsv.%' "
                                      "ORDER BY serie, ts", (da,)):
            out["stati"].setdefault(serie, []).append([ts, v])
    out["eventi"] = [list(e) for e in app.db.eventi_recenti(30)]
    p = app.base / "diagnostica" / "diagnostica.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Creato diagnostica/{p.name}: {len(out['campioni_ciclo'])} campioni ciclo, {len(out['serie'])} serie. "
          f"Non contiene password ne' token.")


def cmd_imposta_password(app):
    import getpass
    acc = app.cfg.leggi()["accesso"]
    predefinito = acc.get("utente") or "admin"
    utente = input(f"Nome utente [{predefinito}]: ").strip() or predefinito
    while True:
        p1 = getpass.getpass("Password (minimo 10 caratteri, non si vede mentre scrivi): ")
        p2 = getpass.getpass("Ripeti la password: ")
        if p1 != p2:
            print("Le due password non coincidono, riprova.")
        elif len(p1) < 10:
            print("Troppo corta: servono almeno 10 caratteri (il pannello potra' essere visibile da internet).")
        else:
            break
    h = hash_password(p1)
    app.cfg.aggiorna(lambda raw: raw.setdefault("accesso", {}).update(utente=utente, password_hash=h, password=""))
    print(f"Fatto: utente '{utente}' e password salvati (la password e' conservata solo come hash).")


def indirizzi(host, porta, app=None):
    """Righe da mostrare all'avvio: la rete di casa (quella da usare con l'iPhone) e, se c'e', l'indirizzo Cloudflare.
    L'IP 'di rete' si ricava dalla route verso internet (niente pacchetti inviati davvero), cosi' si evitano
    gli adattatori virtuali di WSL/Hyper-V/VPN."""
    ip = None
    if host not in ("127.0.0.1", "localhost", "::1"):  # in ascolto solo su questo computer: la rete di casa non arriva
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            s.close()
        except OSError:
            pass
        if ip and ip.startswith(("127.", "169.254.")):
            ip = None
    out = [f"Rete di casa:    http://{ip}:{porta}" if ip else f"Su questo computer: http://localhost:{porta}"]
    try:
        cf = app.cfg.leggi().get("cloudflare") or {} if app else {}
        if cf.get("tunnel_token") and cf.get("hostname"):
            out.append(f"Da fuori casa:   https://{cf['hostname']}")
    except Exception:
        pass
    return out


def main(argv=None, base=AQUI):
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Pannello locale pompa di calore Samsung EHS")
    ap.add_argument("comando", nargs="?", default="avvia",
                    choices=["avvia", "stato", "ridecodifica", "esporta-diagnostica", "imposta-password", "telegram-prova", "compatta", "backup", "ripristina"])
    ap.add_argument("--una-volta", action="store_true", help="una sola lettura e poi mostra i valori")
    ap.add_argument("--demo", action="store_true", help="dati finti per provare l'interfaccia (nessun login)")
    ap.add_argument("--porta", type=int, help="porta web (altrimenti quella di config.json, 7000)")
    ap.add_argument("--host", help="indirizzo di ascolto (altrimenti quello di config.json)")
    ap.add_argument("--versione", action="store_true")
    a = ap.parse_args(argv)
    if a.versione:
        print(VERSIONE)
        return 0

    base = Path(base)
    cfg = Config(base / "config.json")
    if not cfg.esiste():
        cfg.crea_modello()
        print(f"Ho creato config.json (vuoto) in:\n  {base}\n"
              "Aprilo con il Blocco note, compila almeno la sezione smartthings (e netatmo), poi rilancia.")
        if not a.demo:
            return 0
    avvisi_riordino = []
    if not a.demo:
        try:
            rc, avvisi_riordino = riordina_cartelle(base, cfg)
        except Exception as e:      # il riordino non deve mai impedire di partire per un imprevisto
            rc, avvisi_riordino = 0, [f"Riordino cartelle saltato: {e}"]
        if rc:
            for r in avvisi_riordino:
                print("ATTENZIONE:", r)
            return 1
    imposta_log(base)
    for r in avvisi_riordino:
        log.info(r)
    if not a.demo:
        try:
            if cfg.completa():
                print("config.json aggiornato: ho aggiunto le impostazioni nuove (le tue non sono state toccate).")
        except ErroreConfig as e:
            print("ERRORE:", e)
            return 1
    try:
        if a.demo:
            for n in ("dati/demo.db", "dati/demo.db-wal", "dati/demo.db-shm", "demo.db"):
                (base / n).unlink(missing_ok=True)
        app = App(base, demo=a.demo, tollera_db=(a.comando == "ripristina"))
    except ErroreConfig as e:
        print("ERRORE:", e)
        return 1

    if a.comando == "stato":
        cmd_stato(app)
        return 0
    if a.comando == "esporta-diagnostica":
        cmd_diagnostica(app)
        return 0
    if a.comando == "ridecodifica":
        righe, valori = app.db.ridecodifica(app.cfg.leggi()["mappa_ciclo"])
        print(f"Rifatta la decodifica di {righe} campioni ({valori} valori).")
        return 0
    if a.comando == "imposta-password":
        cmd_imposta_password(app)
        return 0
    if a.comando == "compatta":
        cmd_compatta(app)
        return 0
    if a.comando == "backup":
        cmd_backup(app)
        return 0
    if a.comando == "ripristina":
        return cmd_ripristina(app)
    if a.comando == "telegram-prova":
        tg = Telegram(app)
        _, _, err = tg.credenziali()
        if err:
            print("PROBLEMA:", err)
            return 1
        ok, msg = tg.invia_ora(tg.formatta("prova", "prova", "info", ""))
        print("Messaggio di prova inviato: controlla Telegram." if ok else f"NON inviato -> {msg}")
        return 0 if ok else 1

    if not a.demo and not app.blocca_istanza():
        print("Un'altra copia del pannello e' gia' attiva (o la porta_lock e' occupata). Chiudila prima.")
        return 1
    log.info("Pannello %s avviato", VERSIONE)
    if not a.demo:
        log.info("SmartThings: accesso con %s", "OAuth" if not app.token_st.usa_pat() else "token personale (PAT, 24 ore)")
    srv = None
    try:
        if a.una_volta:
            for s in app.servizi:
                s.esegui_una_volta()
                if s.stato["messaggio"]:
                    print(f"{NOMI[s.sigla]}: PROBLEMA: {s.stato['messaggio']}")
            cmd_stato(app)
            return 0
        if a.demo:
            print("MODO DEMO: dati finti (30 giorni generati adesso), nessuna password.")
            sim = riempi_demo(app.db)
            RaccoltaDemo(app, sim).start()
        else:
            avvia_telegram_e_sentinella(app)
            avvia_manutenzione(app)
            app.avvia()
        try:
            srv, _ = avvia_web(app, a.host, a.porta)
        except OSError as e:
            msg = f"Non riesco ad aprire la porta web ({e}). Un altro programma la usa? Cambia generale.porta in config.json."
            print(msg)
            log.error(msg)
            app.ferma()
            return 1
        avvia_tunnel_se_serve(app)
        porta = srv.server_address[1]
        log.info("Interfaccia web attiva sulla porta %s", porta)
        print("Interfaccia web pronta:\n  " + "\n  ".join(indirizzi(srv.server_address[0], porta, app)))
        if not a.demo and not accesso_configurato(app):
            print("ATTENZIONE: manca la password. Lancia:  python pdc_pannello.py imposta-password")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Fermato.")
        app.ferma()
        if getattr(app, "tunnel", None):
            app.tunnel.chiudi_con_il_pannello()
        if getattr(app, "telegram", None):
            app.telegram.ferma()
            app.sentinella.fermo.set()
        if getattr(app, "manutenzione", None):
            app.manutenzione.fermo.set()
        return 0
    finally:
        srv = getattr(app, "server_web", None) or srv
        if srv is not None:
            srv.shutdown()
        app.sblocca_istanza()


if __name__ == "__main__":
    sys.exit(main())
