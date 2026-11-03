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
    "_nota": "Ogni notte il pannello fa una copia del database (e di config.json) nella cartella indicata (di solito dati/backup) e tiene le ultime 'copie'. La copia e' sullo stesso computer: per essere al sicuro copia ogni tanto "
             "la cartella su un altro disco o nel cloud. Su richiesta: python pdc_pannello.py backup",
    "attivo": True,
    "cartella": "dati/backup",
    "copie": 7,
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
            t1 = min(limite, t0 + blocco_giorni * 86400)
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


def fai_backup(app):
    """Copia coerente del database (anche mentre il pannello lavora) + config.json. -> (cartella, [file creati])."""
    c = app.cfg.leggi().get("backup") or BACKUP_DEFAULT
    cart = Path(c.get("cartella") or "dati/backup")
    cart = cart if cart.is_absolute() else app.base / cart
    cart.mkdir(parents=True, exist_ok=True)
    giorno = time.strftime("%Y-%m-%d")
    creati = []
    dest = cart / f"pdc-{giorno}.db"
    tmp = cart / f"pdc-{giorno}.db.tmp"
    if tmp.exists():
        tmp.unlink()
    with closing(app.db._c()) as src, closing(sqlite3.connect(str(tmp))) as dst:
        src.backup(dst)
    os.replace(tmp, dest)
    creati.append(dest.name)
    try:
        shutil.copy2(app.cfg.path, cart / f"config-{giorno}.json")
        riservato(cart / f"config-{giorno}.json")
        creati.append(f"config-{giorno}.json")
    except OSError:
        pass
    n = max(1, int(c.get("copie") or 7))
    for modello in ("pdc-*.db", "config-*.json"):
        for vecchio in sorted(cart.glob(modello))[:-n]:
            try:
                vecchio.unlink()
            except OSError:
                pass
    return cart, creati


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
            cart, creati = fai_backup(self.app)
            db.meta_set("backup_ts", int(time.time()))
            log.info("Backup fatto in %s: %s", cart, ", ".join(creati))

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
    cart, creati = fai_backup(app)
    app.db.meta_set("backup_ts", int(time.time()))
    print(f"Backup fatto in {cart}:")
    for f in creati:
        print("  ", f)
    print("Copia questa cartella anche su un altro disco: se il computer si rompe, la copia qui con lui.")


def _info_copia(percorso):
    """(righe di misure, ultimo dato) di una copia del database, aprendola in sola lettura; None se non e' valida."""
    try:
        with closing(sqlite3.connect("file:" + str(percorso).replace("\\", "/") + "?mode=ro&immutable=1", uri=True)) as c:
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
        c = app.cfg.leggi().get("backup") or BACKUP_DEFAULT
        cart = Path(c.get("cartella") or "dati/backup")
        cart = cart if cart.is_absolute() else app.base / cart
        copie = sorted(cart.glob("pdc-*.db"), reverse=True) if cart.is_dir() else []
        if not copie:
            print(f"Non trovo nessuna copia in {cart}. Il backup si fa con:  python pdc_pannello.py backup")
            return 1
        print(f"Copie trovate in {cart}:\n")
        valide = []
        for i, f in enumerate(copie, 1):
            info = _info_copia(f)
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
        stamp = time.strftime("%Y%m%d-%H%M%S")
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
            print(f"Database attuale messo da parte in {cart / ('prima-del-ripristino-' + stamp + '.db')}")
        for estr in ("-wal", "-shm"):
            try:
                Path(str(dbp) + estr).unlink()
            except OSError:
                pass
        tmp = dbp.with_name(dbp.name + ".tmp")
        shutil.copy2(scelta_f, tmp)
        os.replace(tmp, dbp)
        print(f"Fatto: il database e' tornato a {scelta_f.name}.")
        cfg_c = cart / scelta_f.name.replace("pdc-", "config-").replace(".db", ".json")
        if cfg_c.is_file():
            print(f"\nC'e' anche {cfg_c.name}. ATTENZIONE: i token rinnovabili (Netatmo, SmartThings OAuth) in una copia vecchia "
                  "possono essere scaduti o gia' usati, e il pannello poi non si collega piu'.")
            if leggi("Rimetto anche config.json? (s/N): ").strip().lower() in ("s", "si", "y", "yes"):
                shutil.copy2(app.cfg.path, cart / f"prima-del-ripristino-{stamp}-config.json")
                riservato(cart / f"prima-del-ripristino-{stamp}-config.json")
                shutil.copy2(cfg_c, app.cfg.path)
                riservato(app.cfg.path)
                print("config.json ripristinato (quello di prima e' nella cartella backup).")
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
