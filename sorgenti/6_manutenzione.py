# ==========================================================================
# PEZZO 7: MANUTENZIONE DELLO STORICO (il database non cresce all'infinito)
# ==========================================================================

CONSERVAZIONE_DEFAULT = {
    "_nota": "Gli ultimi 'dati_completi_giorni' giorni restano dettagliati (un punto al minuto circa). Più indietro "
             "il pannello tiene una media ogni 'media_minuti' minuti: i grafici vecchi restano, il file non cresce. "
             "Lo fa da solo, di notte. Su richiesta: python pdc_pannello.py compatta",
    "dati_completi_giorni": 365,
    "media_minuti": 15,
    "elimina_dati_grezzi_ciclo": True,
}
BACKUP_DEFAULT = {
    "_nota": "Ogni notte il pannello fa un pacchetto .tar.xz (database, config.json, plugin e loghi) nella cartella indicata (di solito dati/backup). "
             "Senza 'bucket' resta li' (sullo stesso computer: copialo ogni tanto altrove) e si tengono le ultime 'copie'. Con 'bucket' "
             "(destinazione di rclone, per es. 'backup:nome-bucket') il pacchetto va in giornalieri/ (il primo del mese anche in mensili/) e la copia "
             "locale si cancella; la scadenza la decide il bucket. Se il caricamento non riesce la copia resta (al massimo 'copie') e si riprova. "
             "'rclone_config': file di configurazione di rclone, se non e' quello predefinito. Su richiesta: python pdc_pannello.py backup",
    "attivo": True,
    "cartella": "dati/backup",
    "copie": 7,
    "bucket": "",
    "rclone_config": "",
}
SERIE_CUMULATIVE = ("en.totale_wh",)     # contatori che crescono: si tiene l'ultimo valore, non la media


def compatta_storico(db, giorni, minuti, ora=None, elimina_blob=True, blocco_giorni=5, progresso=None):
    """Sostituisce i punti piu' vecchi di 'giorni' con una media ogni 'minuti' minuti. Si puo' rilanciare: non fa danni."""
    ora = int(ora or time.time())
    passo = max(60, int(minuti) * 60)
    limite = ora - int(giorni) * 86400
    limite -= limite % passo
    prima = dopo_cancellati = 0
    with closing(db._c()) as c:
        series = [r[0] for r in c.execute("SELECT DISTINCT serie FROM ultimo")]
        primo = c.execute("SELECT MIN(ts) FROM misure").fetchone()[0]
    if primo is None or primo >= limite:
        return {"prima": 0, "tolti": 0, "blob": 0, "limite": limite}
    for serie in series:
        t0 = primo - primo % passo
        while t0 < limite:
            t1 = min(limite, t0 + blocco_giorni * 86400 // passo * passo)   # fine blocco sempre su un confine di media: altrimenti una media verrebbe tagliata a meta'
            with db.lock, closing(db._c()) as c, c:
                gruppi = c.execute(
                    "SELECT ts/?1 AS b, COUNT(*), AVG(valore), MAX(ts) FROM misure WHERE serie=?2 AND ts>=?3 AND ts<?4 "
                    "GROUP BY b HAVING COUNT(*)>1", (passo, serie, t0, t1)).fetchall()
                for b, n, media, ultimo_ts in gruppi:
                    valore = media
                    if serie in SERIE_CUMULATIVE:
                        valore = c.execute("SELECT valore FROM misure WHERE serie=? AND ts=?", (serie, ultimo_ts)).fetchone()[0]
                    c.execute("DELETE FROM misure WHERE serie=? AND ts>=? AND ts<?", (serie, b * passo, (b + 1) * passo))
                    c.execute("INSERT OR REPLACE INTO misure VALUES(?,?,?)", (serie, b * passo + passo // 2, round(valore, 3)))
                    prima += n
                    dopo_cancellati += n - 1
            t0 = t1
        if progresso:
            progresso(serie)
    blob = 0
    if elimina_blob:
        with db.lock, closing(db._c()) as c, c:
            blob = c.execute("DELETE FROM ciclo WHERE ts<?", (limite,)).rowcount
    with db.lock, closing(db._c()) as c, c:
        c.execute("DELETE FROM sessioni WHERE scade<?", (ora,))
        c.execute("DELETE FROM eventi WHERE chiuso_ts IS NOT NULL AND chiuso_ts<?", (ora - 730 * 86400,))
    return {"prima": prima, "tolti": dopo_cancellati, "blob": blob, "limite": limite}


def cartella_backup(app):
    """La cartella dei backup (config backup.cartella, di solito dati/backup), come percorso assoluto."""
    cart = Path((app.cfg.leggi().get("backup") or BACKUP_DEFAULT).get("cartella") or "dati/backup")
    return cart if cart.is_absolute() else app.base / cart


class ErroreBackup(Exception):
    pass


def _rclone(c, *argomenti):
    """Lancia rclone e restituisce quello che scrive; ErroreBackup se non riesce."""
    cmd = ["rclone"] + (["--config", c["rclone_config"]] if c.get("rclone_config") else []) + ["--retries", "2"] + [str(a) for a in argomenti]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ErroreBackup(f"rclone non eseguito ({e})")
    if r.returncode:
        righe = [x for x in r.stderr.splitlines() if x.strip()]
        errore = next((x for x in righe if "ERROR" in x or "Error" in x), righe[-1] if righe else f"codice {r.returncode}")
        raise ErroreBackup(errore[:300])
    return r.stdout


def _crea_pacchetto(app, cart):
    """pdc-GG-MM-AAAA-HHMMSS.tar.xz con pdc.db (copia coerente, controllata), config.json, plugin e loghi."""
    dest = cart / f"pdc-{time.strftime('%d-%m-%Y-%H%M%S')}.tar.xz"
    tmp = dest.with_name(dest.name + ".tmp")
    senza_cache = lambda i: None if "__pycache__" in i.name else i
    try:
        with tempfile.TemporaryDirectory(dir=cart) as lavoro:
            copia = Path(lavoro) / "pdc.db"
            with closing(app.db._c()) as src, closing(sqlite3.connect(str(copia))) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ErroreBackup("la copia del database non e' integra")
            tmp.touch()
            riservato(tmp)
            with tarfile.open(tmp, "w:xz") as tar:     # xz livello 6 (il predefinito)
                tar.add(copia, "pdc.db")
                tar.add(app.cfg.path, "config.json")
                for cartella, nome in ((app.base / "plugin", "plugin"), (app.base / "dati" / "loghi", "loghi")):
                    if cartella.is_dir():
                        tar.add(cartella, nome, filter=senza_cache)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def pacchetti(cart):
    """I pacchetti presenti nella cartella, dal piu' vecchio al piu' nuovo."""
    return sorted(cart.glob("pdc-*.tar.xz"), key=lambda f: (f.stat().st_mtime, f.name))


def _carica(c, cart):
    """Carica sul bucket tutti i pacchetti in attesa e cancella ognuno solo a caricamento riuscito. -> righe per il registro"""
    base = str(c["bucket"]).rstrip("/")
    fatti = []
    for f in pacchetti(cart):
        mese = f.name[7:14]                      # MM-AAAA
        peso = f.stat().st_size / 1e6
        _rclone(c, "copyto", f, f"{base}/giornalieri/{f.name}")     # rclone controlla l'impronta di quello che ha caricato
        dove = "giornalieri/"
        mensili = _rclone(c, "lsf", "--files-only", f"{base}/mensili/").split()
        if not any(n[7:14] == mese for n in mensili):
            _rclone(c, "copyto", f, f"{base}/mensili/{f.name}")
            dove += " e mensili/"
        f.unlink()
        fatti.append(f"{f.name} ({peso:.1f} MB) caricato nel bucket in {dove}, copia locale cancellata")
    return fatti


def fai_backup(app):
    """Fa il pacchetto e, se c'e' un bucket, lo carica. -> righe che raccontano cosa e' successo; ErroreBackup se non riesce."""
    c = app.cfg.leggi().get("backup") or BACKUP_DEFAULT
    cart = cartella_backup(app)
    cart.mkdir(parents=True, exist_ok=True)
    nuovo = _crea_pacchetto(app, cart)
    peso = nuovo.stat().st_size / 1e6
    # tetto alle copie sul disco: senza bucket sono le copie da tenere, col bucket quelle rimaste in attesa
    for vecchio in pacchetti(cart)[:-max(1, int(c.get("copie") or 7))]:
        vecchio.unlink(missing_ok=True)
    if not c.get("bucket"):
        return [f"{nuovo.name} ({peso:.1f} MB) in {cart}"]
    return _carica(c, cart)


def esegui_backup(app):
    """fai_backup + memoria dell'esito (la pagina Registro lo mostra). -> (riuscito, righe)"""
    try:
        righe = fai_backup(app)
    except (ErroreBackup, OSError, sqlite3.Error, tarfile.TarError) as e:
        app.db.meta_set("backup_errore", int(time.time()))
        return False, [str(e)]
    app.db.meta_set("backup_ts", int(time.time()))
    app.db.meta_set("backup_errore", 0)
    return True, righe


def vacuum(db):
    with db.lock, closing(db._c()) as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.isolation_level = None
        c.execute("VACUUM")


class Manutenzione(threading.Thread):
    """Ogni notte (tra le 3 e le 6) compatta lo storico; se il pannello era spento, recupera al primo avvio utile."""

    def __init__(self, app):
        super().__init__(daemon=True, name="manutenzione")
        self.app = app
        self.fermo = threading.Event()

    def run(self):
        self.fermo.wait(300)
        while not self.fermo.is_set():
            try:
                self.controlla()
            except Exception:
                log.exception("errore nella manutenzione dello storico")
            try:
                self.controlla_backup()
            except Exception:
                log.exception("errore nel backup")
            self.fermo.wait(3600)

    def controlla_backup(self):
        db = self.app.db
        c = self.app.cfg.leggi().get("backup") or {}
        if not c.get("attivo", True):
            return
        ultimo = float(db.meta_get("backup_ts", 0) or 0)
        notte = 3 <= time.localtime().tm_hour < 6
        if time.time() - ultimo > 20 * 3600 and (notte or time.time() - ultimo > 3 * 86400):
            riuscito, righe = esegui_backup(self.app)
            if riuscito:
                for r in righe:
                    log.info("Backup: %s", r)
            else:
                log.error("Backup NON riuscito: %s. Le copie non caricate restano in %s; riprovo piu' tardi.", righe[0], cartella_backup(self.app))

    def controlla(self):
        db = self.app.db
        ultimo = float(db.meta_get("manutenzione_ts", 0) or 0)
        lt = time.localtime()
        scaduta = time.time() - ultimo > 36 * 3600
        notte = 3 <= lt.tm_hour < 6
        if not (scaduta and (notte or time.time() - ultimo > 5 * 86400)):
            return
        c = self.app.cfg.leggi()["conservazione"]
        esito = compatta_storico(db, max(30, int(c["dati_completi_giorni"])), max(5, int(c["media_minuti"])),
                                 elimina_blob=bool(c["elimina_dati_grezzi_ciclo"]))
        db.meta_set("manutenzione_ts", int(time.time()))
        if esito["tolti"] or esito["blob"]:
            log.info("Manutenzione storico: tolti %d punti vecchi (sostituiti da medie) e %d dati grezzi", esito["tolti"], esito["blob"])
        with closing(db._c()) as cx:
            cx.execute("PRAGMA wal_checkpoint(PASSIVE)")


def cmd_backup(app):
    riuscito, righe = esegui_backup(app)
    if not riuscito:
        print(f"Backup NON riuscito: {righe[0]}")
        print(f"Le copie non caricate restano in {cartella_backup(app)}.")
        return 1
    for r in righe:
        print("Backup:", r)
    if not (app.cfg.leggi().get("backup") or {}).get("bucket"):
        print("Copia questa cartella anche su un altro disco: se il computer si rompe, la copia qui con lui.")
    return 0


def comprimi_db(copia):
    """copia.db -> copia.db.xz (le copie di sicurezza fatte prima di un reset o di un ripristino)."""
    dest = copia.with_name(copia.name + ".xz")
    dest.touch()
    riservato(dest)
    with open(copia, "rb") as f, lzma.open(dest, "wb") as g:
        shutil.copyfileobj(f, g)
    copia.unlink()
    return dest


def _estrai_copia(percorso, lavoro):
    """Da un pacchetto .tar.xz o da una copia .db.xz -> (database, config.json o None) dentro la cartella 'lavoro'."""
    db, cfg = Path(lavoro) / "pdc.db", None
    if percorso.name.endswith(".tar.xz"):
        with tarfile.open(percorso, "r:xz") as tar:
            for nome in ("pdc.db", "config.json"):      # si leggono solo questi due, per nome
                try:
                    sorgente = tar.extractfile(nome)
                except KeyError:
                    continue
                if sorgente is None:
                    continue
                dest = Path(lavoro) / nome
                with sorgente, open(dest, "wb") as g:
                    shutil.copyfileobj(sorgente, g)
                if nome == "config.json":
                    cfg = dest
    else:
        with lzma.open(percorso, "rb") as f, open(db, "wb") as g:
            shutil.copyfileobj(f, g)
    return db, cfg


def _info_copia(percorso):
    """(righe di misure, ultimo dato) di una copia del database, aprendola in sola lettura; None se non e' valida."""
    try:
        with closing(sqlite3.connect(Path(percorso).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as c:
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return None
            nomi = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"misure", "stati", "ultimo"} <= nomi:
                return None
            n, ultimo = c.execute("SELECT COUNT(*), MAX(ts) FROM misure").fetchone()
            return n, ultimo
    except sqlite3.Error:
        return None


def cmd_ripristina(app, leggi=input):
    """Rimette al suo posto una copia del database (e, se vuoi, di config.json). Solo a pannello fermo."""
    if not app.blocca_istanza():
        print("Il pannello e' acceso (o c'e' un'altra copia in esecuzione): fermalo, poi rilancia questo comando.")
        return 1
    try:
        cart = cartella_backup(app)
        copie = (sorted(list(cart.glob("pdc-*.tar.xz")) + list(cart.glob("prima-del-*.db.xz")),
                        key=lambda f: f.stat().st_mtime, reverse=True) if cart.is_dir() else [])
        if not copie:
            print(f"Non trovo nessuna copia in {cart}. Il backup si fa con:  python pdc_pannello.py backup")
            if (app.cfg.leggi().get("backup") or {}).get("bucket"):
                print("Le copie caricate stanno nel bucket: scaricane una (.tar.xz), mettila in quella cartella e rilancia.")
            return 1
        print(f"Copie trovate in {cart}:\n")
        valide = []
        for i, f in enumerate(copie, 1):
            try:
                with tempfile.TemporaryDirectory() as lavoro:
                    info = _info_copia(_estrai_copia(f, lavoro)[0])
            except (OSError, EOFError, lzma.LZMAError, tarfile.TarError):
                info = None
            if info:
                n, ultimo = info
                fino = time.strftime("%d/%m/%Y %H:%M", time.localtime(ultimo)) if ultimo else "nessun dato"
                print(f"  {i}. {f.name}   {f.stat().st_size / 1e6:.1f} MB   dati fino al {fino}")
            else:
                print(f"  {i}. {f.name}   NON VALIDA (danneggiata o incompleta)")
            valide.append(info is not None)
        scelta = leggi("\nQuale copia vuoi rimettere? (numero, invio per annullare): ").strip()
        if not scelta:
            print("Annullato, non ho toccato niente.")
            return 0
        if not scelta.isdigit() or not (1 <= int(scelta) <= len(copie)):
            print("Numero non valido: non ho toccato niente.")
            return 1
        k = int(scelta) - 1
        if not valide[k]:
            print("Quella copia non e' valida: scegline un'altra. Non ho toccato niente.")
            return 1
        scelta_f = copie[k]
        print(f"\nSto per sostituire il database ATTUALE con {scelta_f.name}.")
        print("I dati arrivati dopo quella copia andranno persi (una copia di sicurezza di quello attuale la faccio prima).")
        if leggi("Scrivi SI per confermare: ").strip().upper() != "SI":
            print("Annullato, non ho toccato niente.")
            return 0
        stamp = time.strftime("%d-%m-%Y-%H%M%S")
        da_parte = None
        cart.mkdir(parents=True, exist_ok=True)
        dbp = Path(app.db.path)
        if dbp.exists():
            dest = cart / f"prima-del-ripristino-{stamp}.db"
            try:
                with closing(sqlite3.connect(str(dbp))) as sr, closing(sqlite3.connect(str(dest))) as ds:
                    sr.backup(ds)  # include anche i dati ancora nel file -wal
            except sqlite3.Error:  # database guasto: si tiene com'e', per eventuali recuperi a mano
                dest.unlink(missing_ok=True)
                shutil.copy2(dbp, dest)
            da_parte = dest
            print(f"Database attuale messo da parte in {dest}.xz")
        for estr in ("-wal", "-shm"):
            try:
                Path(str(dbp) + estr).unlink()
            except OSError:
                pass
        with tempfile.TemporaryDirectory() as lavoro:
            db_c, cfg_c = _estrai_copia(scelta_f, lavoro)
            tmp = dbp.with_name(dbp.name + ".tmp")
            shutil.copy2(db_c, tmp)
            riservato(tmp)
            os.replace(tmp, dbp)
            print(f"Fatto: il database e' tornato a {scelta_f.name}.")
            if cfg_c:
                print("\nNella copia c'e' anche config.json. ATTENZIONE: i token rinnovabili (Netatmo, SmartThings OAuth) in una copia vecchia "
                      "possono essere scaduti o gia' usati, e il pannello poi non si collega piu'.")
                if leggi("Rimetto anche config.json? (s/N): ").strip().lower() in ("s", "si", "y", "yes"):
                    shutil.copy2(app.cfg.path, cart / f"prima-del-ripristino-{stamp}-config.json")
                    riservato(cart / f"prima-del-ripristino-{stamp}-config.json")
                    shutil.copy2(cfg_c, app.cfg.path)
                    riservato(app.cfg.path)
                    print("config.json ripristinato (quello di prima e' nella cartella backup).")
        if da_parte:
            comprimi_db(da_parte)
        print("\nOra puoi riavviare il pannello.")
        return 0
    finally:
        app.sblocca_istanza()


def cmd_compatta(app):
    c = app.cfg.leggi()["conservazione"]
    giorni, minuti = max(30, int(c["dati_completi_giorni"])), max(5, int(c["media_minuti"]))
    try:
        peso0 = os.path.getsize(app.db.path) / 1e6
    except OSError:
        peso0 = 0
    print(f"Compatto lo storico oltre {giorni} giorni in medie da {minuti} minuti. Può volerci qualche minuto...")
    n = [0]

    def prog(serie):
        n[0] += 1
        print(f"  {n[0]:2d}. {serie}", end="\r")
    e = compatta_storico(app.db, giorni, minuti, elimina_blob=bool(c["elimina_dati_grezzi_ciclo"]), progresso=prog)
    print(f"\nPunti sostituiti: {e['prima']} -> {e['prima'] - e['tolti']}. Dati grezzi del ciclo tolti: {e['blob']}.")
    if e["tolti"] or e["blob"] or peso0 > 50:
        print("Recupero lo spazio sul disco (VACUUM)...")
        vacuum(app.db)
    app.db.meta_set("manutenzione_ts", int(time.time()))
    print(f"Fatto. Il file del database pesa {os.path.getsize(app.db.path) / 1e6:.1f} MB (prima {peso0:.1f} MB).")


def avvia_manutenzione(app):
    app.manutenzione = Manutenzione(app)
    app.manutenzione.start()


TEMPLATE["conservazione"] = copy.deepcopy(CONSERVAZIONE_DEFAULT)
TEMPLATE["backup"] = copy.deepcopy(BACKUP_DEFAULT)


aggiungi_default_pezzi()     # Telegram e allarmi: ora che le loro parti sono definite
