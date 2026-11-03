

# ==========================================================================
# PEZZO 2 - WEB: server, accesso con password, API, tempo reale, modo demo
# ==========================================================================

import bisect
import hmac
import math
import queue
import secrets
import struct
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

WEB_ASSETS = {}  # @@ASSETS@@  (riempito da costruisci.py: pagine, stile e script)
# impronta di pagina+stile+script: cambia a ogni modifica, anche senza cambiare numero di versione
BUILD = hashlib.sha256((VERSIONE + "".join(WEB_ASSETS[k] for k in sorted(WEB_ASSETS))).encode("utf-8")).hexdigest()[:8]


# --------------------------------------------------------------------------
# Password, sessioni, blocco anti-tentativi
# --------------------------------------------------------------------------

PBKDF2_ITER = 200_000


def hash_password(pw):
    sale = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), sale, PBKDF2_ITER)
    return f"pbkdf2${PBKDF2_ITER}${sale.hex()}${h.hex()}"


def verifica_password(pw, memorizzato):
    try:
        _, it, sale, h = memorizzato.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(sale), int(it))
        return hmac.compare_digest(calc.hex(), h)
    except (ValueError, AttributeError):
        return False


def applica_credenziali(app):
    """Se in config.json c'e' una password in chiaro la trasforma in hash e la cancella."""
    acc = app.cfg.leggi()["accesso"]
    if not acc.get("password"):
        return
    if len(acc["password"]) < 8:
        log.error("accesso.password troppo corta (minimo 8 caratteri): ignorata")
        return
    h = hash_password(acc["password"])

    def f(raw):
        a = raw.setdefault("accesso", {})
        a["password_hash"], a["password"] = h, ""
    app.cfg.aggiorna(f)
    log.info("Password dell'interfaccia web salvata (hash) e cancellata in chiaro da config.json")


def accesso_configurato(app):
    if app.demo:
        return True
    acc = app.cfg.leggi()["accesso"]
    return bool(acc.get("utente") and acc.get("password_hash"))


class Sessioni:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def _h(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def crea(self, giorni):
        token = secrets.token_urlsafe(32)
        with self.db.lock, closing(self.db._c()) as c, c:
            c.execute("DELETE FROM sessioni WHERE scade < ?", (int(time.time()),))
            c.execute("INSERT INTO sessioni VALUES(?,?)", (self._h(token), int(time.time() + giorni * 86400)))
        return token

    def valida(self, token):
        if not token:
            return False
        with closing(self.db._c()) as c:
            r = c.execute("SELECT scade FROM sessioni WHERE h=?", (self._h(token),)).fetchone()
        return bool(r and r[0] > time.time())

    def elimina_altre(self, token):
        with self.db.lock, closing(self.db._c()) as c, c:
            c.execute("DELETE FROM sessioni WHERE h<>?", (self._h(token or ""),))

    def elimina(self, token):
        with self.db.lock, closing(self.db._c()) as c, c:
            c.execute("DELETE FROM sessioni WHERE h=?", (self._h(token),))


class LimiteLogin:
    """Dopo 5 errori in 15 minuti da uno stesso indirizzo (o 40 in totale) blocca i tentativi."""
    FINESTRA, MAX_IP, MAX_TOT = 900, 5, 40

    def __init__(self):
        self.falliti = {}
        self.lock = threading.Lock()

    def _pulisci(self, ip=None):
        lim = time.time() - self.FINESTRA
        for k in ([ip] if ip else list(self.falliti)):
            self.falliti[k] = [t for t in self.falliti.get(k, []) if t > lim]
            if not self.falliti[k]:
                del self.falliti[k]

    def bloccato(self, ip):
        with self.lock:
            self._pulisci()
            return (len(self.falliti.get(ip, [])) >= self.MAX_IP
                    or sum(len(v) for v in self.falliti.values()) >= self.MAX_TOT)

    def fallito(self, ip):
        with self.lock:
            self.falliti.setdefault(ip, []).append(time.time())

    def ok(self, ip):
        with self.lock:
            self.falliti.pop(ip, None)


class Diffusione:
    """Manda un avviso a tutte le pagine aperte (Server-Sent Events)."""

    def __init__(self):
        self.code = set()
        self.lock = threading.Lock()

    MAX_FLUSSI = 20  # ogni pagina aperta tiene occupato un thread: oltre questo numero si rifiuta

    def iscrivi(self):
        q = queue.Queue(maxsize=50)
        with self.lock:
            if len(self.code) >= self.MAX_FLUSSI:
                return None
            self.code.add(q)
        return q

    def rimuovi(self, q):
        with self.lock:
            self.code.discard(q)

    def invia(self, evento, dati):
        with self.lock:
            code = list(self.code)
        for q in code:
            try:
                q.put_nowait((evento, dati))
            except queue.Full:
                pass


# --------------------------------------------------------------------------
# Interrogazioni sui dati
# --------------------------------------------------------------------------

def soglie_umidita(app):
    """[giallo, rosso]: le soglie dell'umidita' (Impostazioni > Avvisi > Umidita'), uniche per Telegram, Dashboard e grafico."""
    c = app.cfg.leggi().get("umidita") or {}
    try:
        g, r = float(c.get("giallo", 60)), float(c.get("rosso", 70))
    except (TypeError, ValueError):
        g, r = 60.0, 70.0
    return [g, max(g, r)]


def q_serie(db, serie, da, a):
    with closing(db._c()) as c:
        return c.execute("SELECT ts, valore FROM misure WHERE serie=? AND ts BETWEEN ? AND ? ORDER BY ts",
                         (serie, int(da), int(a))).fetchall()


def q_primo_ts(db):
    """Quando e' il primo dato di pompa che il pannello ha (per sapere se il periodo di confronto e' coperto)."""
    with closing(db._c()) as c:
        r = c.execute("SELECT MIN(ts) FROM misure WHERE serie='ciclo.freq_compressore'").fetchone()
    return r[0] if r and r[0] is not None else None


def q_medie(db, serie, da, a, n=60):
    """Serie ridotta a ~n punti (media per intervallo): per i grafici piccoli."""
    da, a = int(da), int(a)
    dim = max(1, (a - da) // n)
    with closing(db._c()) as c:
        righe = c.execute("SELECT (ts-?)/? AS b, AVG(valore) FROM misure WHERE serie=? AND ts>=? AND ts<=? "
                          "GROUP BY b ORDER BY b", (da, dim, serie, da, a)).fetchall()
    return [[da + b * dim + dim // 2, round(v, 2)] for b, v in righe]


def q_valore_a(db, serie, t):
    with closing(db._c()) as c:
        r = c.execute("SELECT valore FROM misure WHERE serie=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                      (serie, int(t))).fetchone()
    return r[0] if r else None


def q_cambi(db, serie, da, a):
    """[(ts, stato)]: lo stato che c'era a 'da' e poi tutti i cambi fino ad 'a'."""
    with closing(db._c()) as c:
        prima = c.execute("SELECT valore FROM stati WHERE serie=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                          (serie, int(da))).fetchone()
        dopo = c.execute("SELECT ts, valore FROM stati WHERE serie=? AND ts>? AND ts<=? ORDER BY ts",
                         (serie, int(da), int(a))).fetchall()
    return ([(int(da), prima[0])] if prima else []) + list(dopo)


def stato_a(cambi, t):
    ts = [c[0] for c in cambi]
    i = bisect.bisect_right(ts, t) - 1
    return cambi[i][1] if i >= 0 else None


MODI = ("casa", "acs", "riposo", "nodati")


def campioni_freq(db, da, a, soglia=5.0):
    """{ts: Hz} del compressore, senza i campioni 'fantasma': ogni tanto SmartThings consegna un campione di ciclo con Hz>0
    e mandata alta mentre l'acqua dell'impianto (INDOOR, in tempo reale) dice che la pdc e' ferma da minuti. Se la mandata del
    campione e' lontana piu' di 'soglia' gradi dall'acqua misurata nello stesso momento, il campione con Hz>0 si scarta."""
    freq = dict(q_serie(db, "ciclo.freq_compressore", da, a))
    if not any(v > 0 for v in freq.values()):
        return freq
    mandata = dict(q_serie(db, "ciclo.mandata", da, a))
    acqua = q_serie(db, "risc.acqua", da - 180, a + 180)
    if not acqua or not mandata:
        return freq
    at = [t for t, _ in acqua]
    for ts, hz in list(freq.items()):
        if hz <= 0 or ts not in mandata:
            continue
        i = bisect.bisect_left(at, ts)
        vicini = [acqua[j] for j in (i - 1, i) if 0 <= j < len(acqua) and abs(acqua[j][0] - ts) <= 120]
        if not vicini:
            continue
        t_v, v = min(vicini, key=lambda x: abs(x[0] - ts))
        if abs(mandata[ts] - v) > soglia:
            freq[ts] = 0.0
    return freq


def calcola_modi(db, da, a, tolleranza=150, ora=None, passo=300):
    """Cosa stava facendo la pdc, a intervalli di 5 minuti.
    casa = riscaldamento casa, acs = acqua calda, riposo, nodati.
    Il compressore (Hz) dice SE lavora, la valvola deviatrice dice PER COSA."""
    ora = int(ora or time.time())
    a = min(int(a), ora)
    da = int(da)
    if a <= da:
        return {"segmenti": [], "minuti": {m: 0 for m in MODI}}
    inizio = da - da % passo
    freq = campioni_freq(db, inizio, a)
    freq_ts = sorted(freq)
    battiti = [ts for ts, _ in q_serie(db, "acs.temperatura", inizio - tolleranza, a)]
    cambi = q_cambi(db, "pdc.valvola", inizio, a)
    cella, celle = inizio, []
    noto = None  # (fine cella, modo) dell'ultimo campione vero del compressore
    while cella < a:
        fine = cella + passo
        j = bisect.bisect_left(freq_ts, cella)
        campione = freq_ts[j] if j < len(freq_ts) and freq_ts[j] < fine else None
        k = bisect.bisect_left(battiti, cella - tolleranza)
        presente = campione is not None or (k < len(battiti) and battiti[k] < fine)
        if not presente:
            modo = "nodati"
        elif campione is not None and freq[campione] > 0:
            modo = "casa" if stato_a(cambi, campione) in (None, "room") else "acs"
        elif (campione is None and cella >= ora - 600 and noto and noto[1] in ("casa", "acs")
              and cella - noto[0] <= 600):
            # ultimi minuti: il campione del compressore arriva da SmartThings con parecchi minuti di
            # ritardo, quindi non e' ancora presente. Si tiene lo stato dell'ultimo campione VERO
            # (non quello di celle gia' dedotte), purche' recente.
            modo = noto[1]
        else:
            modo = "riposo"
        if campione is not None:
            noto = (min(fine, a), modo)
        celle.append((cella, min(fine, a), modo))
        cella = fine
    segmenti = []
    for c0, c1, m in celle:
        if segmenti and segmenti[-1][2] == m:
            segmenti[-1][1] = c1
        else:
            segmenti.append([max(c0, da), c1, m])
    minuti = {m: 0 for m in MODI}
    for s0, s1, m in segmenti:
        minuti[m] += round((s1 - s0) / 60)
    return {"segmenti": segmenti, "minuti": minuti}


def modo_corrente(db, ora, tolleranza=150):
    r = calcola_modi(db, ora - 3600, ora, tolleranza, ora)["segmenti"]
    return r[-1][2] if r else "nodati"


def energia_kwh(db, da, a):
    e1 = q_valore_a(db, "en.totale_wh", a)
    if e1 is None:
        return None
    e0 = q_valore_a(db, "en.totale_wh", da)
    if e0 is None:
        with closing(db._c()) as c:
            r = c.execute("SELECT valore FROM misure WHERE serie='en.totale_wh' ORDER BY ts LIMIT 1").fetchone()
        e0 = r[0] if r else e1
    return round(max(0.0, e1 - e0) / 1000, 3)


def mandata_calcolata(curva, esterna):
    """Curva climatica della pdc (FSV 2011/2012/2021/2022): retta tra due punti, piatta fuori."""
    t1, m1, t2, m2 = curva
    if esterna <= t1:
        return m1
    if esterna >= t2:
        return m2
    return round(m1 + (m2 - m1) * (esterna - t1) / (t2 - t1), 1)


def _fsv(ult, codice):
    r = ult.get(f"fsv.{codice}")
    try:
        return float(r[3]) if r and r[3] is not None else None
    except ValueError:
        return None


def dati_curva(db, ult, da, a, esterna_ora):
    t1, t2, m1, m2 = (_fsv(ult, c) for c in ("2011", "2012", "2021", "2022"))
    if None in (t1, t2, m1, m2) or t2 <= t1:
        return None
    es = dict(q_serie(db, "ciclo.esterna", da, a))
    ma = dict(q_serie(db, "ciclo.mandata", da, a))
    fr = dict(q_serie(db, "ciclo.freq_compressore", da, a))
    cambi = q_cambi(db, "pdc.valvola", da, a)
    punti = [[es[t], ma[t]] for t in sorted(es)
             if t in ma and fr.get(t, 0) > 0 and stato_a(cambi, t) in (None, "room")]
    if len(punti) > 300:
        passo = len(punti) / 300
        punti = [punti[int(i * passo)] for i in range(300)]
    curva = (t1, m1, t2, m2)
    return {"basso": {"esterna": t1, "mandata": m1}, "alto": {"esterna": t2, "mandata": m2},
            "punti": punti,
            "ora": {"esterna": esterna_ora,
                    "mandata": mandata_calcolata(curva, esterna_ora) if esterna_ora is not None else None}}


# --------------------------------------------------------------------------
# API (le parti successive aggiungono qui le loro funzioni con @api)
# --------------------------------------------------------------------------

API = globals().get("API") or {}   # stesso dizionario anche se la parte viene ricaricata a caldo


def api(percorso):
    def deco(f):
        API[percorso] = f
        return f
    return deco


API_POST = globals().get("API_POST") or {}


def api_post(percorso):
    def deco(f):
        API_POST[percorso] = f
        return f
    return deco


def finestra(q, ora):
    try:
        a = int(float(q.get("a", [ora])[0]))
        da = int(float(q.get("da", [a - 86400])[0]))
    except ValueError:
        a, da = ora, ora - 86400
    a = min(a, ora)
    da = max(da, a - 90 * 86400)
    if da >= a:
        da = a - 3600
    return da, a


NOMI_MODO_ACS = {"std": "Standard", "eco": "Eco", "power": "Potenza", "force": "Forzato"}


def modo_istantaneo(db, ora, tol, valvola):
    """Cosa fa la pdc ADESSO. I campioni del compressore arrivano da SmartThings con minuti di ritardo (e piu' di rado quando
    la pdc e' ferma), l'acqua dell'impianto (INDOOR) invece e' in tempo reale: la si usa per anticipare i cambi di stato,
    ma solo guardando cosa fa l'acqua IN QUESTO MOMENTO (sale o scende), mai cosa e' successo 15 minuti fa."""
    modo = modo_corrente(db, ora, tol)
    if valvola is None:
        return modo
    acqua = q_serie(db, "risc.acqua", ora - 900, ora)
    if len(acqua) < 3:
        return modo
    if modo in ("riposo", "nodati"):
        # parte un ciclo che il compressore non mostra ancora: l'acqua sta salendo e non ha ancora toccato il massimo
        rec = [v for t, v in acqua if t >= ora - 420]
        if len(rec) >= 2 and rec[-1] - rec[0] >= 1.5 and rec[-1] >= max(v for _, v in acqua) - 0.3:
            return "casa" if valvola == "room" else "acs"
    elif modo in ("casa", "acs"):
        # il compressore si e' fermato ma il campione di stop non e' ancora arrivato: l'acqua scende dal suo massimo recente.
        # Se pero' c'e' un campione recente con il compressore in marcia, vale quello.
        # Attenzione: con la pdc in marcia l'acqua fa un "dente di sega" di 2-3 gradi (si vede nel grafico), quindi un calo
        # piccolo NON basta. Servono un crollo netto (5+ gradi, su due letture di fila) oppure un calo di 2+ gradi
        # insieme al termostato Smarther che ha smesso di chiedere calore (con la valvola sul riscaldamento).
        marcia = any(v > 0 for v in campioni_freq(db, ora - 240, ora).values())
        rec = [v for t, v in acqua if t >= ora - 600]
        cambio_valvola = len(q_cambi(db, "pdc.valvola", ora - 600, ora)) > 1   # il crollo dopo un cambio acs->casa e' normale
        if not marcia and not cambio_valvola and len(rec) >= 3:
            crollo = max(rec) - max(rec[-2:]) >= 5.0
            rich = [v for t, v in q_serie(db, "casa.richiesta_calore", ora - 300, ora)]
            spento = valvola == "room" and bool(rich) and rich[-1] == 0 and max(rec) - rec[-1] >= 2.0
            if crollo or spento:
                return "riposo"
    return modo


@api("/api/dashboard")
def api_dashboard(app, q):
    ora = int(time.time())
    da, a = finestra(q, ora)
    db = app.db
    ult = {r[0]: r for r in db.ultimi()}
    tol = max(150, int(app.intervallo_pdc() * 1.5))

    def num(s):
        r = ult.get(s)
        return r[2] if r else None

    def txt(s):
        r = ult.get(s)
        return r[3] if r else None

    def ts_(s):
        r = ult.get(s)
        return r[1] if r else None

    valvola = txt("pdc.valvola")
    modo = modo_istantaneo(db, ora, tol, valvola)
    sbrina = txt("pdc.sbrinamento") == "on"
    es_min = es_max = None
    es_serie = q_serie(db, "ciclo.esterna", da, a)
    if es_serie:
        vals = [v for _, v in es_serie]
        es_min, es_max = min(vals), max(vals)
    ritorno_fresco = ts_("ciclo.ritorno") is not None and ora - ts_("ciclo.ritorno") < 1200
    acqua = num("risc.acqua")
    ritorno = num("ciclo.ritorno") if ritorno_fresco else None
    pdc_acc, risc_acc = txt("pdc.acceso") == "on", txt("risc.acceso") == "on"
    stato_risc = ("spento" if not risc_acc else "sbrinamento" if sbrina else "attivo" if modo == "casa" else "riposo")
    stato_acs = "spento" if not pdc_acc else ("attivo" if modo == "acs" else "standby")
    curva = dati_curva(db, ult, da, a, num("ciclo.esterna"))
    servizi = [{"nome": NOMI[s.sigla], "ultimo_ok": s.stato["ultimo_ok"], "messaggio": s.stato["messaggio"]}
               for s in app.servizi]
    return {
        "ora": ora, "da": da, "a": a, "demo": app.demo, "intervallo": app.intervallo_pdc(),
        "ultimo_ok": max([s["ultimo_ok"] or 0 for s in servizi] + [ts_("acs.temperatura") or 0]),
        "modo": modo, "servizi": servizi, "accesso_st": info_accesso_st(app),
        "riscaldamento": {
            "stato": stato_risc, "temp": acqua, "target": num("risc.setpoint"), "ritorno": ritorno,
            "delta": round(acqua - ritorno, 1) if acqua is not None and ritorno is not None else None,
            "spark": q_medie(db, "risc.acqua", da, a)},
        "acs": {
            "stato": stato_acs, "temp": num("acs.temperatura"), "target": num("acs.setpoint"),
            "modo": NOMI_MODO_ACS.get(txt("pdc.modo"), txt("pdc.modo")),
            "spark": q_medie(db, "acs.temperatura", da, a)},
        "esterna": {"temp": num("ciclo.esterna"), "ts": ts_("ciclo.esterna"), "min": es_min, "max": es_max,
                    "spark": q_medie(db, "ciclo.esterna", da, a)},
        "energia": {"kwh": energia_kwh(db, da, a), "potenza_w": num("pot.watt"), "prezzo_kwh": prezzo_kwh(app),
                    "spark": q_medie(db, "pot.watt", da, a)},
        "casa": {"temp": num("casa.temperatura"), "umidita": num("casa.umidita"), "soglie_umidita": soglie_umidita(app), "target": num("casa.setpoint"),
                 "richiesta": num("casa.richiesta_calore") == 1.0, "richiesta_pct": num("casa.richiesta_pct"),
                 "ts": ts_("casa.temperatura"), "spark": q_medie(db, "casa.temperatura", da, a),
                 "spark_um": q_medie(db, "casa.umidita", da, a)},
        "prestazioni": {k: num(f"ciclo.{k}") for k in ("freq_compressore", "freq_target", "vel_pompa", "portata",
                                                       "scarico")},
        "prestazioni_ts": ts_("ciclo.freq_compressore"),
        "ultimo_ciclo": ultimo_ciclo(db, ora),
        "ritardo_ciclo": ritardo_ciclo(db, ora),
        "sistema": {"riscaldamento_acceso": risc_acc, "pdc_accesa": pdc_acc, "valvola": txt("pdc.valvola"),
                    "resistenza": txt("pdc.resistenza"), "sbrinamento": sbrina, "salute": txt("pdc.salute"),
                    "riferimento": txt("pdc.riferimento_temp"), "smarther": txt("casa.raggiungibile"),
                    "smarther_modo": txt("casa.modo"), "smarther_setpoint": num("casa.setpoint"),
                    "smarther_modo_casa": txt("info.nt.modo_termico")},
        "loghi": elenco_loghi(app), "loghi_bianchi": bool(app.cfg.leggi().get("dispositivi", {}).get("loghi_bianchi")),
        "modi": calcola_modi(db, da, a, tol, ora),
        "curva": curva,
        "eventi": [{"ts": e[0], "codice": e[1], "livello": e[2], "testo": e[3]}
                   for e in db.eventi_recenti(30) if e[4] is None],
    }


# --------------------------------------------------------------------------
# Pezzo 3: Analisi (grafici storici) ed Efficienza (COP, energia, cicli)
# --------------------------------------------------------------------------

def info_accesso_st(app):
    """Come si autentica SmartThings: OAuth (si rinnova da solo) o token personale (24 ore)."""
    if app.demo:
        return {"tipo": "demo"}
    if not app.token_st.usa_pat():
        oa = app.cfg.leggi()["smartthings"]["oauth"]
        return {"tipo": "oauth", "scade": int(float(oa.get("expires_at") or 0)) or None}
    eta = app.token_st.eta_pat_ore()
    return {"tipo": "pat", "eta_ore": round(eta, 1) if eta is not None else None}


def ultimo_ciclo(db, ora):
    """Ultimo ciclo del compressore concluso (ultimi 3 giorni): {inizio, fine, modo, kwh} oppure None.
    I kWh vengono dal contatore (en.totale_wh) e si danno solo se la differenza e' leggibile."""
    try:
        cicli = cicli_compressore(db, ora - 3 * 86400, ora, ora)
    except Exception:
        return None
    if not cicli:
        return None
    i, f, m = cicli[-1]
    kwh = energia_kwh(db, i, f)
    return {"inizio": i, "fine": f, "modo": m, "kwh": kwh if kwh and kwh > 0 else None}


def ritardo_ciclo(db, ora):
    """Ritardo con cui arrivano i campioni del compressore, ultime 24 h: {n, tipico_min, max_min} (None se pochi dati)."""
    v = sorted(x for _, x in q_serie(db, "diag.ritardo_ciclo", ora - 86400, ora))
    if len(v) < 5:
        return None
    return {"n": len(v), "tipico_min": round(v[len(v) // 2] / 60, 1), "max_min": round(v[-1] / 60, 1)}


def prezzo_kwh(app):
    """Prezzo dell'energia (euro/kWh) da config.json; 0 se non impostato."""
    try:
        return max(0.0, float(app.cfg.leggi().get("tariffa", {}).get("prezzo_kwh") or 0))
    except (TypeError, ValueError):
        return 0.0


def cornice(app, ora):
    """Parti comuni a tutte le risposte: stato collegamenti e avvisi (per il pallino in alto e il banner)."""
    ult = {r[0]: r for r in app.db.ultimi()}
    servizi = [{"nome": NOMI[s.sigla], "ultimo_ok": s.stato["ultimo_ok"], "messaggio": s.stato["messaggio"]}
               for s in app.servizi]
    r = ult.get("acs.temperatura")
    return {"ora": ora, "demo": app.demo, "intervallo": app.intervallo_pdc(), "servizi": servizi,
            "ultimo_ok": max([s["ultimo_ok"] or 0 for s in servizi] + [r[1] if r else 0]),
            "eventi": [{"ts": e[0], "codice": e[1], "livello": e[2], "testo": e[3]}
                       for e in app.db.eventi_recenti(30) if e[4] is None]}


# gruppi di grafici della pagina Analisi: (id, titolo, unita, [(serie, etichetta, colore, tratteggio)])
GRUPPI_ANALISI = [
    ("temp", "Temperature", "°C", [
        ("ciclo.esterna", "Esterna", "est", 0), ("risc.acqua", "Mandata", "casa", 0),
        ("ciclo.ritorno", "Ritorno", "ritorno", 0), ("acs.temperatura", "Acqua calda", "acs", 0),
        ("risc.setpoint", "Target mandata", "casa", 1)]),
    ("casa", "Temperatura ambiente · Smarther", "°C", [
        ("casa.temperatura", "Temperatura", "amb", 0), ("casa.setpoint", "Target", "amb", 1)]),
    ("umid", "Umidità · Smarther", "%", [("casa.umidita", "Umidità", "umid", 0)]),
    # un solo grafico a gradini per le tre grandezze del circuito: compressore (area) e pompa a sinistra, portata a destra
    ("circuito", "Compressore, portata e pompa", "", [
        ("ciclo.freq_compressore", "Compressore", "amb", 0, "Hz", 0, 1),
        ("ciclo.freq_target", "Target", "amb", 1, "Hz", 0, 0),
        ("ciclo.vel_pompa", "Pompa", "en", 0, "%", 0, 0),
        ("ciclo.portata", "Portata", "est", 0, "L/min", 1, 0)]),
    ("pot", "Potenza elettrica", "W", [("pot.watt", "Potenza", "en", 0)]),
    ("gas", "Gas refrigerante", "°C", [
        ("ciclo.scarico", "Scarico", "casa", 0), ("ciclo.evaporazione", "Evaporazione", "acs", 0),
        ("ciclo.aspirazione", "Aspirazione", "est", 0)]),
]


def q_griglia(db, serie, da, a, n):
    """Media per intervalli uguali: [[ts_centro, valore]]. Massimo ~n punti."""
    da, a = int(da), int(a)
    dim = max(1, (a - da) // n)
    with closing(db._c()) as c:
        righe = c.execute("SELECT (ts-?)/? AS b, AVG(valore) FROM misure WHERE serie=? AND ts>=? AND ts<=? "
                          "GROUP BY b ORDER BY b", (da, dim, serie, da, a)).fetchall()
    return [[da + b * dim + dim // 2, round(v, 2)] for b, v in righe], dim


def q_potenza_a_gradini(db, da, a):
    """Potenza elettrica come 'gradini': ogni rapporto di SmartThings vale per la sua finestra (inizio-fine) ed e' una media,
    non un istante. Cosi' la linea e' continua e dice la verita' (prima: un punto ogni ~15 min, spezzato in tanti pezzetti)."""
    with closing(db._c()) as c:
        righe = c.execute("SELECT p.ts, p.valore, f.valore FROM misure p LEFT JOIN misure f ON f.serie='en.finestra_s' AND f.ts=p.ts "
                          "WHERE p.serie='pot.watt' AND p.ts BETWEEN ? AND ? ORDER BY p.ts", (int(da), int(a) + 3 * 3600)).fetchall()
    pts, ult = [], int(da)
    for ts, w, fin in righe:
        t0 = ts - int(fin) if fin and 60 <= fin <= 3 * 3600 else ts
        t0 = max(t0, ult, int(da))
        t1 = min(ts, int(a))
        if t1 < t0:
            continue
        w = round(w, 1)
        pts.append([t0, w])
        if t1 > t0:
            pts.append([t1, w])
        ult = t1
    return pts


@api("/api/analisi")
def api_analisi(app, q):
    ora = int(time.time())
    da, a = finestra(q, ora)
    try:
        n = max(60, min(600, int(q.get("n", ["240"])[0])))
    except ValueError:
        n = 240
    tol = max(150, int(app.intervallo_pdc() * 1.5))
    out = cornice(app, ora)
    grafici, dim = [], max(1, (a - da) // n)
    for gid, titolo, unita, serie in GRUPPI_ANALISI:
        linee = []
        for nome, et, col, tr, *extra in serie:
            u, asse, area = extra if extra else (unita, 0, 0)
            pts, dim = q_griglia(app.db, nome, da, a, n)
            if nome == "pot.watt" and (a - da) <= 3 * 86400:
                gr = q_potenza_a_gradini(app.db, da, a)
                if gr:
                    pts = gr
            if pts:
                linee.append({"id": nome, "nome": et, "col": col, "tratti": tr, "punti": pts, "u": u, "asse": asse, "area": area})
        grafici.append({"id": gid, "titolo": titolo, "unita": unita, "gradini": gid == "circuito", "linee": linee})
    # richiesta di calore della casa: intervalli in cui lo Smarther chiede caldo (fascia sul grafico casa)
    richiesta = [[t, v] for t, v in q_serie(app.db, "casa.richiesta_calore", da, a)]
    out.update({"da": da, "a": a, "passo": dim, "grafici": grafici, "richiesta": richiesta, "soglie_umidita": soglie_umidita(app),
                "modi": calcola_modi(app.db, da, a, tol, ora)})
    return out


def _celle(db, serie, da, a, passo):
    with closing(db._c()) as c:
        righe = c.execute("SELECT (ts-?)/? AS b, AVG(valore) FROM misure WHERE serie=? AND ts>=? AND ts<?"
                          " GROUP BY b", (da, passo, serie, da, a)).fetchall()
    return dict(righe)


KW_PER_LMIN_K = 0.0698      # 4,186 kJ/(kg*K) / 60 : acqua, 1 L ~ 1 kg


def cicli_compressore(db, da, a, ora, gap=900):
    """Cicli del compressore letti direttamente dai campioni (risoluzione ~2 minuti, non 5):
    [(inizio, fine, modo)] con modo casa/acs secondo la valvola all'inizio. Esclude il ciclo ancora in corso."""
    da, a = int(da), min(int(a), int(ora))
    camp = q_serie(db, "ciclo.freq_compressore", da - gap, a)
    cambi = q_cambi(db, "pdc.valvola", da - gap, a)
    cicli, inizio, ultimo = [], None, None

    def chiudi(fine):
        if inizio >= da:
            cicli.append((inizio, fine, "casa" if stato_a(cambi, inizio) in (None, "room") else "acs"))
    for ts, hz in camp:
        if hz > 0:
            if inizio is not None and ts - ultimo > gap:      # buco nei dati: il ciclo era finito
                chiudi(ultimo + 120)
                inizio = None
            if inizio is not None and (stato_a(cambi, ts) in (None, "room")) != (stato_a(cambi, inizio) in (None, "room")):
                chiudi(ts)                                   # la valvola ha cambiato destinazione: altro ciclo
                inizio = None
            if inizio is None:
                inizio = ts
            ultimo = ts
        elif inizio is not None:
            chiudi(ts)
            inizio = None
    if inizio is not None and int(ora) - ultimo > gap:
        chiudi(ultimo + 120)
    return cicli


def _mediana(v):
    v = sorted(v)
    return v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2


def calcola_bollitore(db, da, a, ora, litri=300):
    """Salute del bollitore (stime dalla sonda dell'acqua calda):
    - perdita a riposo: di notte (23-06), nel tratto piu' lungo (>=3 h) senza riscaldo dell'acqua, di quanto scende la temperatura (gradi/ora);
    - salita: nei cicli di riscaldo dell'acqua, di quanto sale la temperatura e in quanto tempo.
    None se i dati non bastano."""
    da, a = int(da), min(int(a), int(ora))
    temp = q_serie(db, "acs.temperatura", da - 7200, a)
    if len(temp) < 10:
        return None
    tts = [t for t, _ in temp]

    def t_at(t, tol=1800):
        i = bisect.bisect_right(tts, t) - 1
        return temp[i][1] if i >= 0 and t - tts[i] <= tol else None
    cicli = [(i, f) for i, f, m in cicli_compressore(db, da - 86400, a, ora) if m == "acs"]
    sal = []
    for i, f in cicli:
        if i < da or f - i < 600:
            continue
        t0 = t_at(i)
        vals = [v for ts_, v in temp if i <= ts_ <= f + 300]
        if t0 is None or not vals or max(vals) - t0 < 2:
            continue
        sal.append((f - i, max(vals) - t0))
    perd = []
    lt = time.localtime(da)
    for k in range(0, (a - da) // 86400 + 3):
        w0 = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + k - 1, 23, 0, 0, 0, 0, -1)))
        w1 = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + k, 6, 0, 0, 0, 0, -1)))
        if w0 < da or w1 > a:
            continue
        occ = sorted((max(i, w0), min(f + 300, w1)) for i, f in cicli if f + 300 > w0 and i < w1)
        liberi, cur = [], w0
        for i, f in occ:
            if i > cur:
                liberi.append((cur, i))
            cur = max(cur, f)
        if cur < w1:
            liberi.append((cur, w1))
        if not liberi:
            continue
        g0, g1 = max(liberi, key=lambda x: x[1] - x[0])
        g0 += 1200          # i primi 20 minuti dopo il riscaldo l'acqua si rimescola
        if g1 - g0 < 2.5 * 3600:
            continue
        v0, v1 = t_at(g0), t_at(g1)
        dentro = [v for ts_, v in temp if g0 <= ts_ <= g1]
        if v0 is None or v1 is None or (dentro and max(dentro) > v0 + 0.5):
            continue
        r = (v0 - v1) / ((g1 - g0) / 3600.0)
        if 0 <= r <= 3:
            perd.append(r)
    out = {"perdita": None, "salita": None, "litri": litri}
    if len(perd) >= 2:
        r = _mediana(perd)
        out["perdita"] = {"gradi_ora": round(r, 2), "watt": round(r * litri * 1.163), "notti": len(perd)}
    if len(sal) >= 2:
        rate = _mediana([g / (d / 3600.0) for d, g in sal])
        out["salita"] = {"gradi_ora": round(rate, 1), "min_per_10": round(600.0 / rate) if rate > 0 else None,
                         "minuti": round(_mediana([d for d, _ in sal]) / 60), "gradi": round(_mediana([g for _, g in sal]), 1),
                         "cicli": len(sal)}
    return out if (out["perdita"] or out["salita"]) else None


def calcola_efficienza(db, da, a, tol, ora, passo=300):
    """Per ogni giorno locale: energia elettrica e calore prodotto stimato (portata x salto termico)
    separati tra riscaldamento casa e acqua calda, COP, cicli. Il calore e' una STIMA."""
    da = int(da)
    a = min(int(a), int(ora))
    modi = calcola_modi(db, da, a, tol, ora, passo)
    modo_cella = {}
    for s0, s1, m in modi["segmenti"]:
        t = s0 - (s0 - da) % passo
        while t < s1:
            modo_cella[(t - da) // passo] = m
            t += passo
    freq, portata = _celle(db, "ciclo.freq_compressore", da, a, passo), _celle(db, "ciclo.portata", da, a, passo)
    mand, rit = _celle(db, "ciclo.mandata", da, a, passo), _celle(db, "ciclo.ritorno", da, a, passo)
    pot, est = _celle(db, "pot.watt", da, a, passo), _celle(db, "ciclo.esterna", da, a, passo)
    giorni, bins = {}, {"casa": {}, "acs": {}}
    coperte = {"casa": 0, "acs": 0}
    totali_celle = {"casa": 0, "acs": 0}
    ore_h = passo / 3600.0
    for i in sorted(modo_cella):
        m = modo_cella[i]
        t = da + i * passo
        lt = time.localtime(t)
        chiave = time.strftime("%Y-%m-%d", lt)
        g = giorni.setdefault(chiave, {
            "giorno": chiave, "ts": int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))),
            "el": {"casa": 0.0, "acs": 0.0, "riposo": 0.0}, "calore": {"casa": 0.0, "acs": 0.0},
            "el_cop": {"casa": 0.0, "acs": 0.0}, "min": {"casa": 0, "acs": 0}, "est": [], "nodati_min": 0})
        if m == "nodati":
            g["nodati_min"] += passo // 60
            continue
        if i in est:
            g["est"].append(est[i])
        w = pot.get(i)
        if w is not None:
            g["el"][m] += w / 1000.0 * ore_h
        if m in ("casa", "acs"):
            g["min"][m] += passo // 60
            totali_celle[m] += 1
            if w is not None and i in portata and i in mand and i in rit and freq.get(i, 0) > 0:
                kw = max(0.0, KW_PER_LMIN_K * portata[i] * (mand[i] - rit[i]))
                kwh_el, kwh_th = w / 1000.0 * ore_h, kw * ore_h
                g["calore"][m] += kwh_th
                g["el_cop"][m] += kwh_el
                coperte[m] += 1
                if i in est and kwh_el > 0:
                    b = int(math.floor(est[i] / 2.0)) * 2
                    d = bins[m].setdefault(b, [0.0, 0.0, 0])
                    d[0] += kwh_th
                    d[1] += kwh_el
                    d[2] += 1
    righe = []
    for k in sorted(giorni):
        g = giorni[k]
        el_cop = g["el_cop"]["casa"] + g["el_cop"]["acs"]
        cal = g["calore"]["casa"] + g["calore"]["acs"]
        righe.append({
            "giorno": k, "ts": g["ts"],
            "el_casa": round(g["el"]["casa"], 2), "el_acs": round(g["el"]["acs"], 2),
            "el_riposo": round(g["el"]["riposo"], 2),
            "calore_casa": round(g["calore"]["casa"], 2), "calore_acs": round(g["calore"]["acs"], 2),
            "cop_casa": round(g["calore"]["casa"] / g["el_cop"]["casa"], 2) if g["el_cop"]["casa"] > 0.05 else None,
            "cop_acs": round(g["calore"]["acs"] / g["el_cop"]["acs"], 2) if g["el_cop"]["acs"] > 0.05 else None,
            "cop": round(cal / el_cop, 2) if el_cop > 0.1 else None,
            "min_casa": g["min"]["casa"], "min_acs": g["min"]["acs"],
            "esterna": round(sum(g["est"]) / len(g["est"]), 1) if g["est"] else None,
            "nodati_min": g["nodati_min"]})
    tot = {"el": {"casa": 0.0, "acs": 0.0, "riposo": 0.0}, "calore": {"casa": 0.0, "acs": 0.0},
           "el_cop": {"casa": 0.0, "acs": 0.0}}
    for g in giorni.values():
        for k in ("el", "calore", "el_cop"):
            for m in tot[k]:
                tot[k][m] += g[k].get(m, 0.0)
    cop = lambda m: round(tot["calore"][m] / tot["el_cop"][m], 2) if tot["el_cop"][m] > 0.05 else None
    el_c = tot["el_cop"]["casa"] + tot["el_cop"]["acs"]
    cal_c = tot["calore"]["casa"] + tot["calore"]["acs"]
    # cicli: un "ciclo" e' un tratto continuo in un modo
    cicli = {}
    runs = cicli_compressore(db, da, a, ora)
    for m in ("casa", "acs"):
        durate = [(f - i) / 60 for i, f, mm in runs if mm == m]
        cicli[m] = {"n": len(durate), "media_min": round(sum(durate) / len(durate), 1) if durate else None,
                    "brevi": sum(1 for d in durate if d < 10), "max_min": round(max(durate), 1) if durate else None}
    sbrinamenti = sum(1 for _, v in q_cambi(db, "pdc.sbrinamento", da, a)[1:] if v == "on") \
        if q_cambi(db, "pdc.sbrinamento", da, a) else 0
    curva_cop = {}
    for m in ("casa", "acs"):
        curva_cop[m] = [{"da": b, "a": b + 2, "cop": round(d[0] / d[1], 2), "ore": round(d[2] * ore_h, 1)}
                        for b, d in sorted(bins[m].items()) if d[2] >= 6 and d[1] > 0]
    return {
        "giorni": righe, "bins": curva_cop, "cicli": cicli, "sbrinamenti": sbrinamenti,
        "tot": {"el_casa": round(tot["el"]["casa"], 1), "el_acs": round(tot["el"]["acs"], 1),
                "el_riposo": round(tot["el"]["riposo"], 1),
                "calore_casa": round(tot["calore"]["casa"], 1), "calore_acs": round(tot["calore"]["acs"], 1),
                "cop_casa": cop("casa"), "cop_acs": cop("acs"),
                "cop": round(cal_c / el_c, 2) if el_c > 0.1 else None,
                "ore_casa": round(modi["minuti"]["casa"] / 60, 1), "ore_acs": round(modi["minuti"]["acs"] / 60, 1)},
        "copertura": {m: round(100.0 * coperte[m] / totali_celle[m]) if totali_celle[m] else None
                      for m in coperte},
    }


@api("/api/efficienza")
def api_efficienza(app, q):
    ora = int(time.time())
    try:
        giorni = int(q.get("giorni", ["7"])[0])
    except ValueError:
        giorni = 7
    giorni = max(1, min(90, giorni))
    lt = time.localtime(ora)
    oggi0 = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
    da = oggi0 - (giorni - 1) * 86400
    # i confini dei giorni cadono su mezzanotte locale: la griglia da 5 minuti parte da li'
    tol = max(150, int(app.intervallo_pdc() * 1.5))
    out = cornice(app, ora)
    out.update(calcola_efficienza(app.db, da, ora, tol, ora))
    out.update({"da": da, "a": ora, "n_giorni": giorni, "prezzo_kwh": prezzo_kwh(app),
                "energia_contatore": energia_kwh(app.db, da, ora)})
    litri = int((app.cfg.leggi().get("bollitore") or {}).get("litri") or 300)
    out["bollitore"] = calcola_bollitore(app.db, da, ora, ora, litri)
    if q.get("confronta", ["0"])[0] == "1":
        # periodo di pari durata subito prima: da - (ora - da) ... da
        durata = ora - da
        prec = calcola_efficienza(app.db, da - durata, da, tol, ora)
        primo = q_primo_ts(app.db)
        out["prec"] = {"da": da - durata, "a": da, "tot": prec["tot"], "copertura": prec["copertura"],
                       "cicli": prec["cicli"], "bollitore": calcola_bollitore(app.db, da - durata, da, ora, litri),
                       "nodati_min": sum(g["nodati_min"] for g in prec["giorni"]),
                       "dati_da": primo,
                       "completo": primo is not None and primo <= da - durata + 3600}
    return out


def csv_esporta(app, q):
    """CSV per Excel italiano (punto e virgola, virgola decimale, UTF-8 con BOM). tipo=giorni (riepilogo) o misure (dati grezzi)."""
    import io, csv
    ora = int(time.time())
    tipo = (q.get("tipo", ["giorni"])[0] or "giorni")
    try:
        giorni = int(q.get("giorni", ["28"])[0])
    except ValueError:
        giorni = 28
    num_it = lambda v: "" if v is None else (str(round(v, 3)).replace(".", ",") if isinstance(v, float) else str(v))
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    lt = time.localtime(ora)
    oggi0 = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
    if tipo == "misure":
        giorni = max(1, min(7, giorni))
        da = oggi0 - (giorni - 1) * 86400
        w.writerow(["serie", "data_ora", "valore"])
        with closing(app.db._c()) as c:
            for serie, ts, val in c.execute("SELECT serie, ts, valore FROM misure WHERE ts BETWEEN ? AND ? "
                                            "ORDER BY serie, ts", (da, ora)):
                w.writerow([serie, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)), num_it(float(val))])
        nome = f"pdc-misure-{time.strftime('%Y%m%d', lt)}.csv"
    else:
        giorni = max(1, min(90, giorni))
        da = oggi0 - (giorni - 1) * 86400
        tol = max(150, int(app.intervallo_pdc() * 1.5))
        e = calcola_efficienza(app.db, da, ora, tol, ora)
        prezzo = prezzo_kwh(app)
        w.writerow(["giorno", "energia_casa_kWh", "energia_acqua_kWh", "energia_standby_kWh", "calore_casa_kWh",
                    "calore_acqua_kWh", "COP", "COP_casa", "COP_acqua", "ore_casa", "ore_acqua", "esterna_media_C"]
                   + (["costo_euro"] if prezzo > 0 else []))
        for g in e["giorni"]:
            riga = [g["giorno"], num_it(g["el_casa"]), num_it(g["el_acs"]), num_it(g["el_riposo"]),
                    num_it(g["calore_casa"]), num_it(g["calore_acs"]), num_it(g["cop"]), num_it(g["cop_casa"]),
                    num_it(g["cop_acs"]), num_it(round(g["min_casa"] / 60, 2)), num_it(round(g["min_acs"] / 60, 2)),
                    num_it(g["esterna"])]
            if prezzo > 0:
                riga.append(num_it(round((g["el_casa"] + g["el_acs"] + g["el_riposo"]) * prezzo, 2)))
            w.writerow(riga)
        nome = f"pdc-giorni-{time.strftime('%Y%m%d', lt)}.csv"
    return ("\ufeff" + out.getvalue()).encode("utf-8"), nome


# --------------------------------------------------------------------------
# Icona dell'app (PNG disegnato a mano: serve a iPhone per la schermata Home)
# --------------------------------------------------------------------------

def png_icona(n=180):
    def liscio(d, bordo):  # 1 dentro, 0 fuori, con bordo morbido
        return max(0.0, min(1.0, 0.5 - d / bordo))
    righe = []
    for y in range(n):
        r = bytearray([0])
        for x in range(n):
            dx, dy = x + .5 - n / 2, y + .5 - n / 2
            d = math.hypot(dx, dy)
            t = y / n
            col = [11 + 12 * t, 15 + 14 * t, 23 + 30 * t]
            anello = liscio(abs(d - n * .27) - n * .055, 1.4)
            if anello:
                k = (dx / n + .5)
                c2 = [255 - 60 * k, 107 + 90 * k, 87 - 20 * k]
                col = [col[i] * (1 - anello) + c2[i] * anello for i in range(3)]
            punto = liscio(d - n * .095, 1.4)
            if punto:
                c3 = [52, 211, 176]
                col = [col[i] * (1 - punto) + c3[i] * punto for i in range(3)]
            r += bytes(int(max(0, min(255, v))) for v in col)
        righe.append(bytes(r))
    raw = zlib.compress(b"".join(righe), 9)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


_ICONA = []


def icona():
    if not _ICONA:
        _ICONA.append(png_icona())
    return _ICONA[0]


MANIFEST = {"name": "Pompa di calore", "short_name": "PDC", "start_url": "/", "display": "standalone",
            "background_color": "#0b0f17", "theme_color": "#0b0f17", "lang": "it",
            "icons": [{"src": "/icona.png", "sizes": "180x180", "type": "image/png"}]}

CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


# --------------------------------------------------------------------------
# Server HTTP
# --------------------------------------------------------------------------

def ip_client(h):
    ip = h.client_address[0]
    if ip in ("127.0.0.1", "::1"):  # dietro cloudflared: l'indirizzo vero sta nell'intestazione
        return h.headers.get("CF-Connecting-IP") or ip
    return ip


def e_https(h):
    return (h.headers.get("X-Forwarded-Proto", "") == "https"
            or '"https"' in h.headers.get("Cf-Visitor", ""))


class Gestore(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 60

    def log_message(self, fmt, *args):
        log.debug("web %s - %s", self.address_string(), fmt % args)

    # ---- risposte ----
    def _invia(self, codice, corpo, tipo="text/html; charset=utf-8", extra=None):
        dati = corpo if isinstance(corpo, bytes) else corpo.encode("utf-8")
        self.send_response(codice)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dati)))
        extra = list(extra or [])
        sost = {k: v for k, v in extra if k in ("Cache-Control", "Content-Security-Policy")}
        self.send_header("Cache-Control", sost.get("Cache-Control", "no-store"))
        self.send_header("X-Pdc-Build", BUILD)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if e_https(self):
            self.send_header("Strict-Transport-Security", "max-age=15552000")
        self.send_header("Content-Security-Policy", sost.get("Content-Security-Policy", CSP))
        for k, v in extra:
            if k not in sost:
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(dati)

    def _json(self, obj, codice=200):
        self._invia(codice, json.dumps(obj, separators=(",", ":"), ensure_ascii=False),
                    "application/json; charset=utf-8")

    def _vai(self, dove, extra=None):
        self._invia(303, b"", extra=[("Location", dove)] + list(extra or []))

    def _pagina(self, nome, **sost):
        t = WEB_ASSETS[nome]
        for k, v in sost.items():
            t = t.replace("{{" + k + "}}", v)
        return t.replace("{{versione}}", VERSIONE.split()[0]).replace("{{build}}", BUILD)

    def _cookie(self, nome):
        for parte in self.headers.get("Cookie", "").split(";"):
            k, _, v = parte.strip().partition("=")
            if k == nome:
                return v
        return None

    # ---- smistamento ----
    def do_HEAD(self):
        self._smista()

    def do_GET(self):
        self._smista()

    def do_POST(self):
        self._smista()

    def _smista(self):
        app = self.server.app
        try:
            u = urlparse(self.path)
            p, q = u.path, parse_qs(u.query)
            if not accesso_configurato(app):
                return self._invia(503, self._pagina("setup.html"))
            pubbliche = {"/app.css": "text/css; charset=utf-8", "/app.js": "text/javascript; charset=utf-8"}
            if p in pubbliche and p[1:] in WEB_ASSETS:
                return self._invia(200, WEB_ASSETS[p[1:]], pubbliche[p],
                                   [("Cache-Control", "no-cache")])
            if p == "/icona.png":
                return self._invia(200, icona(), "image/png", [("Cache-Control", "max-age=86400")])
            if p == "/manifest.webmanifest":
                return self._invia(200, json.dumps(MANIFEST), "application/manifest+json")
            if p == "/login":
                return self._login(app) if self.command == "POST" else self._invia(200, self._pagina("login.html", errore=""))
            autenticato = app.demo or app.sessioni.valida(self._cookie("pdc_sid"))
            if p == "/logout" and self.command == "POST":
                app.sessioni.elimina(self._cookie("pdc_sid"))
                return self._vai("/login", [("Set-Cookie", "pdc_sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")])
            if not autenticato:
                if p.startswith("/api/"):
                    return self._json({"errore": "non autenticato"}, 401)
                return self._vai("/login")
            if p == "/":
                return self._invia(200, self._pagina("index.html"))
            if p.startswith("/loghi/") and self.command in ("GET", "HEAD"):
                return self._logo(app, p[7:])
            if p == "/api/flusso":
                return self._flusso(app)
            if p == "/api/esporta.csv" and self.command in ("GET", "HEAD"):
                dati, nome = csv_esporta(app, q)
                return self._invia(200, dati, "text/csv; charset=utf-8",
                                   [("Content-Disposition", f'attachment; filename="{nome}"')])
            if p in API and self.command in ("GET", "HEAD"):
                return self._json(API[p](app, q))
            if p in API_POST and self.command == "POST":
                return self._post_api(app, p)
            self._invia(404, "Non trovato", "text/plain; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            log.exception("errore nella richiesta web %s", self.path)
            try:
                self._json({"errore": "errore interno"}, 500)
            except Exception:
                pass

    def _logo(self, app, nome):
        """File dalla cartella dati/loghi (o dalla vecchia 'loghi'). Solo nomi e estensioni previsti: niente percorsi liberi."""
        m = re.fullmatch(r"(smarther|samsung|netatmo|bticino)\.(svg|png|webp|jpe?g)", nome)
        f = next((c / nome for c in cartelle_loghi(app.base) if m and (c / nome).is_file()), None)
        if not m or f is None or f.stat().st_size > 2_000_000:
            return self._invia(404, "Non trovato", "text/plain; charset=utf-8")
        tipo = {"svg": "image/svg+xml", "png": "image/png", "webp": "image/webp", "jpg": "image/jpeg",
                "jpeg": "image/jpeg"}[m.group(2)]
        # un SVG aperto da solo potrebbe eseguire script: lo si blocca (come immagine in pagina non succede comunque)
        extra = [("Cache-Control", "max-age=3600")]
        if m.group(2) == "svg":
            extra.append(("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; sandbox"))
        self._invia(200, f.read_bytes(), tipo, extra)

    def _post_api(self, app, p):
        # difesa da richieste fatte da altri siti: intestazione speciale + stessa origine
        origine = self.headers.get("Origin")
        if self.headers.get("X-Pdc") != "1" or (origine and urlparse(origine).netloc != self.headers.get("Host", "")):
            return self._json({"errore": "richiesta non consentita"}, 403)
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if n < 0 or n > 200_000:
            return self._json({"errore": "richiesta non valida" if n < 0 else "richiesta troppo grande"}, 400 if n < 0 else 413)
        try:
            corpo = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            if not isinstance(corpo, dict):
                raise ValueError
        except ValueError:
            return self._json({"errore": "JSON non valido"}, 400)
        return self._json(API_POST[p](app, corpo, self))

    def _login(self, app):
        ip = ip_client(self)
        origine = self.headers.get("Origin")
        if origine and origine != "null" and urlparse(origine).netloc != self.headers.get("Host", ""):
            return self._invia(403, "Richiesta non consentita", "text/plain; charset=utf-8")
        if app.limite.bloccato(ip):
            return self._invia(429, self._pagina("login.html", errore="Troppi tentativi. Riprova tra 15 minuti."))
        try:
            n = max(0, min(int(self.headers.get("Content-Length", "0")), 4096))
        except ValueError:
            n = 0
        dati = parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
        utente, pw = dati.get("utente", [""])[0], dati.get("password", [""])[0]
        acc = app.cfg.leggi()["accesso"]
        ok_u = hmac.compare_digest(utente.encode(), (acc.get("utente") or "").encode())
        ok_p = verifica_password(pw, acc.get("password_hash") or "")  # sempre calcolata: stesso tempo
        if app.demo or (ok_u and ok_p):
            app.limite.ok(ip)
            giorni = int(app.cfg.leggi()["generale"]["sessione_giorni"])
            tok = app.sessioni.crea(giorni)
            ck = f"pdc_sid={tok}; Path=/; Max-Age={giorni * 86400}; HttpOnly; SameSite=Lax"
            if e_https(self):
                ck += "; Secure"
            log.info("accesso web eseguito da %s", ip)
            return self._vai("/", [("Set-Cookie", ck)])
        app.limite.fallito(ip)
        log.warning("accesso web FALLITO da %s", ip)
        time.sleep(1.0)
        self._invia(401, self._pagina("login.html", errore="Utente o password non corretti."))

    def _flusso(self, app):
        coda = app.diffusione.iscrivi()
        if coda is None:
            return self._invia(503, "Troppe pagine aperte", "text/plain; charset=utf-8", [("Retry-After", "30")])
        self.send_response(200)
        for k, v in (("Content-Type", "text/event-stream; charset=utf-8"), ("Cache-Control", "no-cache"),
                     ("Connection", "keep-alive"), ("X-Accel-Buffering", "no")):
            self.send_header(k, v)
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(b"retry: 4000\n\nevent: ciao\ndata: {}\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev, dati = coda.get(timeout=15)
                    if ev == "chiudi":
                        break
                    msg = f"event: {ev}\ndata: {json.dumps(dati)}\n\n"
                except queue.Empty:
                    msg = "event: ping\ndata: {}\n\n"
                self.wfile.write(msg.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            app.diffusione.rimuovi(coda)


class ServerWeb(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"  # su Windows consentirebbe a un altro programma di rubare la porta

    def __init__(self, indirizzo, app):
        super().__init__(indirizzo, Gestore)
        self.app = app

    def handle_error(self, request, client_address):
        """Connessioni chiuse a meta' (browser che chiude la scheda, telefono che cambia rete) non sono errori."""
        e = sys.exc_info()[1]
        if isinstance(e, (OSError, TimeoutError)):
            log.debug("connessione web interrotta da %s: %s", client_address[0], e)
        else:
            log.exception("errore nel server web (%s)", client_address[0])


def avvia_web(app, host=None, porta=None):
    """Avvia il server web in un thread. Restituisce (server, thread)."""
    applica_credenziali(app)
    app.sessioni = Sessioni(app.db)
    app.limite = LimiteLogin()
    g = app.cfg.leggi()["generale"]
    srv = ServerWeb((host or g["host"], int(porta if porta is not None else g["porta"])), app)
    app.bus.append(lambda cambiati: app.diffusione.invia("dati", {"n": len(cambiati), "ts": int(time.time())}))
    app.eventi.ascoltatori.append(lambda tipo, cod, liv, testo: app.diffusione.invia(
        "anomalia", {"tipo": tipo, "codice": cod}))
    app.server_web = srv
    th = threading.Thread(target=srv.serve_forever, daemon=True, name="web")
    th.start()
    return srv, th


# --------------------------------------------------------------------------
# Modo DEMO: dati finti ma realistici, per provare l'interfaccia senza la pdc
# --------------------------------------------------------------------------

FSV_DEMO = {"1031": 70, "1032": 25, "1051": 55, "1052": 40, "2011": -5, "2012": 19, "2021": 37, "2022": 26,
            "2031": 50, "2032": 35, "2091": 1, "2092": 1, "2093": 4, "3011": 1, "3071": 0, "4011": 0,
            "4012": 0, "4021": 0, "4042": 10, "4061": 0}


class Simulatore:
    def __init__(self, ts0):
        self.T, self.Td, self.dem, self.dhw = 21.0, 48.0, False, False
        self.mandata, self.ritorno, self.wh, self.ts = 27.0, 20.0, 4_930_000.0, ts0

    @staticmethod
    def esterna(ts):
        giorno = ts / 86400
        ora = (ts % 86400) / 3600
        return 5.5 + 3.5 * math.sin(giorno * 0.9) + 4.0 * math.sin(2 * math.pi * (ora - 9) / 24) \
            + 0.4 * math.sin(ts / 997.0)

    def passo(self, ts):
        dt = max(1, ts - self.ts)
        self.ts = ts
        out = self.esterna(ts)
        ora = (ts % 86400) / 3600
        prelievo = 1.6 if (6 <= ora < 8 or 18 <= ora < 21) else 0.1
        self.Td -= (0.08 + prelievo) * dt / 3600
        if self.Td < 44:
            self.dhw = True
        if self.Td >= 50:
            self.dhw = False
        if self.T < 20.9:
            self.dem = True
        if self.T >= 21.1:
            self.dem = False
        modo = "acs" if self.dhw else ("casa" if self.dem else "riposo")
        curva = mandata_calcolata((-5, 37, 19, 26), out)
        if modo == "acs":
            hz, pot, portata, pompa = 52 + 6 * math.sin(ts / 300), 0.7 + 0.02 * 55, 17.0, 100
            bersaglio_m, bersaglio_r = min(58, self.Td + 9), min(58, self.Td + 9) - 5
            self.Td += 8.5 * dt / 3600
        elif modo == "casa":
            hz = max(25, min(60, 28 + (21 - out) * 1.6))
            pot, portata, pompa = 0.45 + 0.016 * hz, 14.0, 70
            bersaglio_m, bersaglio_r = curva + 1, curva - 3.5
        else:
            hz, pot, portata, pompa = 0, 0.03 + 0.02 * abs(math.sin(ts / 700)), 0.0, 0
            bersaglio_m, bersaglio_r = self.T + 4, out + 4
        k = min(1.0, dt / (240 if modo != "riposo" else 900))
        self.mandata += (bersaglio_m - self.mandata) * k
        self.ritorno += (bersaglio_r - self.ritorno) * k
        self.T += (-(self.T - out) * 5.0e-6 + (2.8e-4 if modo == "casa" else 0)) * dt
        self.wh += pot * 1000 * dt / 3600
        return {"modo": modo, "out": out, "hz": round(hz), "pot_w": pot * 1000, "portata": portata,
                "pompa": pompa}

    def record(self, ts, s, cicli=True):
        rec = Rec()
        n = lambda serie, v, lento=False, t=None: rec.num.append((serie, t or ts, round(float(v), 3), lento))
        n("acs.temperatura", self.Td)
        n("acs.setpoint", 50, True)
        n("risc.acqua", self.mandata + (0 if s["modo"] != "riposo" else -1))
        n("risc.setpoint", 25, True)
        n("pot.watt", s["pot_w"])
        n("en.totale_wh", self.wh)
        n("casa.temperatura", self.T + 0.15 * math.sin(ts / 3000))
        n("casa.umidita", 52 + 6 * math.sin(ts / 7000) + 0.4 * (10 - s["out"]))
        n("casa.setpoint", 21, True)
        n("casa.richiesta_calore", 1.0 if self.dem else 0.0)
        n("casa.richiesta_pct", 100 if self.dem else 0)
        n("casa.wifi", 51, True)
        if cicli:
            n("ciclo.esterna", s["out"])
            n("ciclo.freq_compressore", s["hz"])
            n("ciclo.freq_target", s["hz"])
            n("ciclo.mandata", self.mandata)
            n("ciclo.ritorno", self.ritorno)
            n("ciclo.portata", s["portata"])
            n("ciclo.vel_pompa", s["pompa"])
            if s["hz"]:
                n("ciclo.scarico", 55 + s["hz"] * 0.6)
        rec.stati.update({"pdc.acceso": "on", "risc.acceso": "on", "pdc.modo": "std", "risc.modo": "heat",
                          "pdc.valvola": "tank" if s["modo"] == "acs" else "room", "pdc.sbrinamento": "off",
                          "pdc.resistenza": "off", "pdc.salute": "online", "pdc.riferimento_temp": "water",
                          "casa.modo": "home", "casa.raggiungibile": "si",
                          "info.pdc.firmware": "20260320.1", "info.pdc.modello": "SAC_EHS_MONO|220614|61007400001600000400000000000000",
                          "info.pdc.nome": "Heat Pump", "info.pdc.binary": "SAC_EHS_MONO", "info.pdc.descrizione": "EHS",
                          "info.pdc.anno": "22", "info.pdc.piattaforma": "Tizen 4.0", "info.pdc.driver": "26050101",
                          "info.pdc.vid": "DA-SAC-EHS-000001-SUB", "info.pdc.aggiornamento": "false",
                          "info.pdc.id_ocf": "00000000-0000-0000-0000-000000000000",
                          "info.pdc.tipo": "Samsung OCF Air Conditioner", "info.pdc.registrata": "1766153559",
                          "info.pdc.versioni": '[["Software","DB91-02102A 2026-03-20","Version"],["Firmware","DB91-02100A 2020-07-10","Version"],["Firmware","DB91-02103B 2022-06-14",""],["Firmware","DB91-02450A 2022-07-06","EHS MONO LOWTEMP"]]',
                          "info.nt.firmware": "40", "info.nt.tipo": "BNS", "info.nt.id": "70:ee:50:00:00:01",
                          "info.nt.nome": "Smarther Thermostat", "info.nt.stanza": "Entrata", "info.nt.casa": "La mia abitazione",
                          "info.nt.installato": "1759245643", "info.nt.modo_termico": "schedule"})
        return rec


def riempi_demo(db, giorni=30, passo=300):
    ora = int(time.time())
    inizio = ora - giorni * 86400
    sim = Simulatore(inizio)
    righe, stati, prec = [], [], {}
    for ts in range(inizio - inizio % passo, ora - passo, passo):
        rec = sim.record(ts, sim.passo(ts))
        righe += [(s, t, v) for s, t, v, _ in rec.num]
        for s, v in rec.stati.items():
            if prec.get(s) != v:
                stati.append((s, ts, v))
                prec[s] = v
    with db.lock, closing(db._c()) as c, c:
        c.executemany("INSERT OR REPLACE INTO misure VALUES(?,?,?)", righe)
        c.executemany("INSERT OR REPLACE INTO stati VALUES(?,?,?)", stati)
    fine = Rec()
    fine.stati = {f"fsv.{k}": str(v) for k, v in FSV_DEMO.items()}
    db.salva(fine, inizio)
    # l'ultimo valore di ogni serie
    ultimo = sim.record(ora - passo, sim.passo(ora - passo))
    db.salva(ultimo, ora - passo)
    return sim


class RaccoltaDemo(threading.Thread):
    """Ogni 20 secondi un nuovo punto finto, per vedere il tempo reale in azione."""

    def __init__(self, app, sim):
        super().__init__(daemon=True, name="demo")
        self.app, self.sim, self.fermo = app, sim, threading.Event()

    def run(self):
        while not self.fermo.wait(20):
            ts = int(time.time())
            rec = self.sim.record(ts, self.sim.passo(ts))
            cambiati = self.app.db.salva(rec, ts)
            for cb in list(self.app.bus):
                cb(cambiati)
