

# ==========================================================================
# PEZZO 2 - WEB: server, accesso con password, API, tempo reale, modo demo
# ==========================================================================

import bisect
import collections
import gzip
import hmac
import queue
import secrets
import struct
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

WEB_ASSETS = {}  # @@ASSETS@@  (riempito da costruisci.py: pagine, stile e script)
# impronta di pagina+stile+script: cambia a ogni modifica, anche senza cambiare numero di versione
def _calcola_build(versione, assets):
    """Impronta delle pagine: cambia con la versione e con ogni file web (le pagine aperte si ricaricano da sole)."""
    return hashlib.sha256((versione + "".join(assets[k] for k in sorted(assets))).encode("utf-8")).hexdigest()[:8]


BUILD = _calcola_build(VERSIONE, WEB_ASSETS)


# --------------------------------------------------------------------------
# Password, sessioni, blocco anti-tentativi
# --------------------------------------------------------------------------

PBKDF2_ITER = 600_000      # l'hash salva le iterazioni usate: le password gia' impostate (200k) restano valide


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
    if len(acc["password"]) < PASSWORD_MIN:
        log.error("accesso.password troppo corta (minimo %d caratteri): ignorata", PASSWORD_MIN)
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


def cookie_sessione(token, giorni, https):
    """Il cookie di accesso (unico punto: login e rinnovo)."""
    ck = f"pdc_sid={token}; Path=/; Max-Age={int(giorni) * 86400}; HttpOnly; SameSite=Lax"
    return ck + "; Secure" if https else ck


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
        v = min(vicini, key=lambda x: abs(x[0] - ts))[1]
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


def zona_curva(ult):
    """Quale curva climatica sta usando la pompa: (1 o 2, da dove lo so). Il codice 2041 la sceglie (1 = zona 1,
    pavimento; 2 = zona 2, ventilconvettori), ma SmartThings non lo comunica per tutte le pompe: senza, si assume
    la zona 1, che e' quella di default."""
    z = _fsv(ult, "2041")
    if z in (1.0, 2.0):
        return int(z), "2041"
    return 1, ("4061 = 0: la zona 2 non e' attiva" if _fsv(ult, "4061") == 0 else "ipotesi: 2041 non e' comunicato")


def dati_curva(db, ult, da, a, esterna_ora):
    zona, fonte = zona_curva(ult)
    c2, c3 = ("2021", "2022") if zona == 1 else ("2031", "2032")
    t1, t2, m1, m2 = (_fsv(ult, c) for c in ("2011", "2012", c2, c3))
    if None in (t1, t2, m1, m2) or t2 <= t1:
        return None
    es = dict(q_serie(db, "ciclo.esterna", da, a))
    ma = dict(q_serie(db, "ciclo.mandata", da, a))
    fr = dict(q_serie(db, "ciclo.freq_compressore", da, a))
    cambi = q_cambi(db, "pdc.valvola", da, a)
    punti = [[es[t], ma[t], t] for t in sorted(es)     # [esterna, mandata, istante]: l'istante serve al tooltip
             if t in ma and fr.get(t, 0) > 0 and stato_a(cambi, t) in (None, "room")]
    if len(punti) > 300:
        passo = len(punti) / 300
        punti = [punti[int(i * passo)] for i in range(300)]
    curva = (t1, m1, t2, m2)
    return {"basso": {"esterna": t1, "mandata": m1}, "alto": {"esterna": t2, "mandata": m2},
            "zona": zona, "zona_fonte": fonte, "punti": punti, "lungo": a - da > 86400,
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


# --------------------------------------------------------------------------
# Meteo del giorno (Open-Meteo, due modelli): una riga nella card «Esterna»
# --------------------------------------------------------------------------

METEO_PREDEFINITO = {"latitudine": 45.23, "longitudine": 7.60}         # Cirié; si cambia solo in config.json (sezione "meteo")
METEO_URL = "https://api.open-meteo.com/v1/forecast"
METEO_MODELLI = ("italia_meteo_arpae_icon_2i", "ecmwf_ifs025")         # ICON-2I (2 km, modello italiano) + ECMWF come seconda opinione
METEO_VARIABILI = ("temperature_2m", "precipitation", "cloud_cover", "weather_code", "is_day")
METEO_SOGLIA_MM = 0.2          # da questa pioggia oraria (mm) in su si considera che piova
METEO_OGNI = 1800              # secondi fra una lettura e l'altra
METEO_VECCHIO = 3 * 3600       # dati più vecchi di così non si mostrano più
_METEO_NEVE = {71, 73, 75, 77, 85, 86}
_METEO_TEMPORALE = {95, 96, 99}


def meteo_config(app):
    """(latitudine, longitudine) da config.json -> 'meteo'; None se svuotate (il meteo sparisce)."""
    c = dict(METEO_PREDEFINITO)
    try:
        c.update(app.cfg.leggi().get("meteo") or {})
        return float(c["latitudine"]), float(c["longitudine"])
    except (TypeError, ValueError, KeyError):
        return None


def meteo_scarica(lat, lon, modo=0):
    """Legge Open-Meteo. modo: 0 = due modelli, 1 = solo ICON-2I, 2 = modello automatico. -> (dati, modo_riuscito)
    Se Open-Meteo rifiuta la richiesta (per esempio il nome di un modello) si passa al modo successivo."""
    ultimo = None
    for m in range(modo, 3):
        modelli = METEO_MODELLI if m == 0 else METEO_MODELLI[:1] if m == 1 else ()
        par = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "hourly": ",".join(METEO_VARIABILI),
               "timezone": "Europe/Rome", "timeformat": "unixtime", "forecast_days": 2}
        if modelli:
            par["models"] = ",".join(modelli)
        # se la rete non c'è http_json solleva ErroreAPI: inutile provare gli altri modi, si esce
        code, js = http_json("GET", METEO_URL + "?" + urlencode(par), {"User-Agent": "pdc-pannello", "Accept": "application/json"}, timeout=15)
        if code != 200 or not isinstance(js, dict) or "hourly" not in js:
            ultimo = f"HTTP {code} {str(js)[:120]}"
            continue
        try:
            dati = meteo_leggi(js, modelli)
        except (KeyError, TypeError, ValueError) as e:
            ultimo = f"risposta inattesa ({e})"
            continue
        if dati["m"]:
            return dati, m
        ultimo = "nessun modello con dati"
    raise ErroreAPI("richiesta", 0, f"Open-Meteo: {ultimo}")


def meteo_leggi(js, modelli):
    """Risposta di Open-Meteo -> {'t': [epoch], 'off': secondi, 'm': {modello: {variabile: [valori]}}}. Con più modelli i nomi hanno il suffisso _<modello>."""
    h, tempi = js["hourly"], js["hourly"]["time"]
    out = {}
    for nome in (modelli or ("auto",)):
        d = {}
        for v in METEO_VARIABILI:
            chiave = next((k for k in ((f"{v}_{nome}", v) if modelli else (v,)) if k in h), None)
            d[v] = list(h[chiave]) if chiave else [None] * len(tempi)
        if any(x is not None for x in d["precipitation"]) and any(x is not None for x in d["temperature_2m"]):
            out[nome] = d
    return {"t": [int(t) for t in tempi], "off": int(js.get("utc_offset_seconds", 3600)), "m": out}


def _ore_intervalli(livelli):
    """[(inizio, fine_esclusa, livello_massimo)] dei gruppi di ore consecutive con livello > 0."""
    gr, cur = [], None
    for i, lv in enumerate(livelli):
        if lv > 0:
            if cur is None:
                cur = [i, i + 1, lv]
            else:
                cur[1], cur[2] = i + 1, max(cur[2], lv)
        elif cur is not None:
            gr.append(tuple(cur)); cur = None
    if cur is not None:
        gr.append(tuple(cur))
    return gr


def _icona_blocco(prim, idx, p):
    """Icona di un blocco di ore (p: 0 asciutto, 1 pioggia possibile, 2 pioggia certa)."""
    cod = [prim["weather_code"][i] for i in idx if prim["weather_code"][i] is not None]
    notte = prim["is_day"][idx[len(idx) // 2]] == 0
    temp = [prim["temperature_2m"][i] for i in idx if prim["temperature_2m"][i] is not None]
    if p and any(c in _METEO_TEMPORALE for c in cod):
        return "temporale"
    if p and (any(c in _METEO_NEVE for c in cod) or (temp and min(temp) <= 0.5)):
        return "neve"
    if p == 2:
        return "pioggia"
    if p == 1:
        return "luna-pioggia" if notte else "sole-pioggia"
    if cod and sum(1 for c in cod if c in (45, 48)) * 2 > len(cod):
        return "nebbia"
    nuv = [prim["cloud_cover"][i] for i in idx if prim["cloud_cover"][i] is not None]
    media = sum(nuv) / len(nuv) if nuv else 50
    if media < 20:
        return "luna" if notte else "sole"
    if media < 60:
        return "luna-nuvola" if notte else "sole-nuvola"
    return "nuvola"


def meteo_riassumi(raw, ora):
    """Dati grezzi -> {giorno, tmin, tmax, icona, testo, livello, ore[...]}. Funzione pura (si prova con dati finti).
    Pioggia 'certa' = tutti i modelli la prevedono nell'ora; 'possibile' = solo alcuni. Con un modello solo, se la prevede è 'certa'."""
    tempi, off, modelli = raw["t"], raw["off"], raw["m"]
    if not tempi or not modelli:
        return None
    nomi = list(modelli)
    prim = modelli[nomi[0]]
    loc = [t + off for t in tempi]
    adesso = ora + off
    oggi = adesso // 86400
    giorno_idx = oggi + 1 if (adesso % 86400) // 3600 >= 20 else oggi          # dalle 20 la forchetta e la pioggia guardano al giorno dopo (ma la scritta resta «Oggi»)
    del_giorno = [i for i, l in enumerate(loc) if l // 86400 == giorno_idx]
    if not del_giorno:
        return None
    finestra = [i for i in del_giorno if giorno_idx != oggi or loc[i] // 3600 >= adesso // 3600]
    temps = [prim["temperature_2m"][i] for i in del_giorno if prim["temperature_2m"][i] is not None]
    if not temps:
        return None

    def mm(nome, i):
        v = modelli[nome]["precipitation"][i]
        return v if v is not None else 0.0

    n = len(nomi)
    def livello(i):
        k = sum(1 for nome in nomi if mm(nome, i) >= METEO_SOGLIA_MM)
        return 0 if k == 0 else 2 if k == n else 1

    liv = [livello(i) for i in finestra]
    gruppi = _ore_intervalli(liv)
    ora_h = lambda i: (loc[i] % 86400) // 3600
    notte = giorno_idx == oggi and prim["is_day"][finestra[0]] == 0 if finestra else False
    testo, lvl, codici = "asciutto", "nessuna", []
    if gruppi:
        a, b, mx = gruppi[0]
        lvl = "certa" if mx == 2 else "possibile"
        ia, ib = ora_h(finestra[a]), ora_h(finestra[b - 1]) + 1
        in_corso = giorno_idx == oggi and a == 0
        fino_sera = ib >= 24
        if in_corso:
            quando = "per il resto della giornata" if fino_sera else f"fino alle {ib}"
        else:
            quando = f"dalle {ia}" if fino_sera else f"alle {ia}" if ib - ia <= 1 else f"{ia}–{ib}"
        testo = ("pioggia " if lvl == "certa" else "possibile pioggia ") + quando
        codici = [prim["weather_code"][finestra[j]] for j in range(a, b) if prim["weather_code"][finestra[j]] is not None]
        freddo = any((prim["temperature_2m"][finestra[j]] or 9) <= 0.5 for j in range(a, b))
    # icona
    if gruppi:
        if any(c in _METEO_TEMPORALE for c in codici):
            icona = "temporale"
        elif any(c in _METEO_NEVE for c in codici) or freddo:
            icona = "neve"
        elif lvl == "certa":
            icona = "pioggia"
        else:
            icona = "luna-pioggia" if notte else "sole-pioggia"
    else:
        diurne = [i for i in (finestra or del_giorno) if 8 <= ora_h(i) <= 19] or (finestra or del_giorno)
        nuv = [prim["cloud_cover"][i] for i in diurne if prim["cloud_cover"][i] is not None]
        cod = [prim["weather_code"][i] for i in diurne if prim["weather_code"][i] is not None]
        media = sum(nuv) / len(nuv) if nuv else 50
        if cod and sum(1 for c in cod if c in (45, 48)) * 2 > len(cod):
            icona = "nebbia"
        elif media < 20:
            icona = "luna" if notte else "sole"
        elif media < 60:
            icona = "luna-nuvola" if notte else "sole-nuvola"
        else:
            icona = "nuvola"
    # dettaglio a blocchi di 3 ore (per il riquadro al tocco): ora d'inizio, temperatura, pioggia del blocco e icona
    nomi_liv = ("nessuna", "possibile", "certa")
    dettaglio = []
    # il dettaglio sono sempre 3 righe da 3 ore a partire da adesso (a sera tarda le ultime due cadono dopo mezzanotte), sotto la scritta «Oggi»
    fin_det = [i for i in range(len(loc)) if loc[i] // 3600 >= adesso // 3600][:9]
    liv_det = [livello(i) for i in fin_det]
    for j in range(0, len(fin_det), 3):
        blocco = list(range(j, min(j + 3, len(fin_det))))
        idx = [fin_det[k] for k in blocco]
        p = max(liv_det[k] for k in blocco)
        mm_blocco = sum(max(mm(nome, i) for nome in nomi) for i in idx)
        dettaglio.append({"h": ora_h(idx[0]), "t": prim["temperature_2m"][idx[0]], "mm": round(mm_blocco, 1),
                          "p": nomi_liv[p], "i": _icona_blocco(prim, idx, p)})
        if len(dettaglio) >= 3:
            break
    return {"giorno": "Oggi", "tmin": min(temps), "tmax": max(temps),
            "icona": icona, "testo": testo, "livello": lvl, "ore": dettaglio, "modelli": n}


def _meteo_stato(app):
    return app.__dict__.setdefault("meteo_st", {"raw": None, "ts": 0, "prossimo": 0, "modo": 0, "falliti": 0})


def _meteo_lavoro(app, lat, lon):
    """Una lettura di Open-Meteo (la chiama il thread di fondo «esterni»)."""
    st = _meteo_stato(app)
    try:
        dati, modo = meteo_scarica(lat, lon, st["modo"])
        st.update(raw=dati, ts=int(time.time()), modo=modo, falliti=0, prossimo=int(time.time()) + METEO_OGNI)
        if modo:
            log.info("Meteo: Open-Meteo non ha dato due modelli, uso la modalità %d", modo)
    except Exception as e:
        st["falliti"] += 1
        st["prossimo"] = int(time.time()) + min(300 * st["falliti"], METEO_OGNI)
        (log.warning if st["falliti"] == 1 else log.debug)("Meteo: lettura non riuscita (%s)", e)


def meteo_demo(ora):
    """Previsione finta per la modalità demo: dalle 17 pioggia (i due modelli concordano), poi asciutto."""
    off = 3600
    inizio = (ora + off) // 86400 * 86400 - off
    tempi = [inizio + h * 3600 for h in range(48)]
    def serie(f):
        return [f(h % 24) for h in range(48)]
    base = {"temperature_2m": serie(lambda h: round(9 + 8 * max(0.0, 1 - abs(h - 15) / 9), 1)),
            "cloud_cover": serie(lambda h: 30 if h < 14 else 85), "weather_code": serie(lambda h: 3 if h < 17 else 63),
            "is_day": serie(lambda h: 1 if 7 <= h <= 18 else 0)}
    a = dict(base, precipitation=serie(lambda h: 0.0 if h < 17 or h > 20 else 1.2))
    b = dict(base, precipitation=serie(lambda h: 0.0 if h < 18 or h > 21 else 0.8))
    return {"t": tempi, "off": off, "m": {METEO_MODELLI[0]: a, METEO_MODELLI[1]: b}}


def meteo_info(app):
    """Riassunto del meteo per la Dashboard, o None. Legge solo l'ultima lettura di Open-Meteo, fatta dal thread di fondo."""
    cfg = meteo_config(app)
    if not cfg:
        return None
    ora = int(time.time())
    if app.demo:
        r = meteo_riassumi(meteo_demo(ora), ora)
        return dict(r, agg=ora, modelli_nomi=list(METEO_MODELLI)) if r else None
    st = _meteo_stato(app)
    esterni_assicura(app)
    if not st["raw"] or ora - st["ts"] > METEO_VECCHIO:
        return None
    r = meteo_riassumi(st["raw"], ora)
    return dict(r, agg=st["ts"], modelli_nomi=list(st["raw"]["m"])) if r else None


# --------------------------------------------------------------------------
# Umidità esterna da una stazione meteo vicina (WeatherLink, dati grezzi): un solo valore nella card «Esterna»
# --------------------------------------------------------------------------

STAZIONE_OGNI = 600            # secondi fra una lettura e l'altra
STAZIONE_VECCHIO = 3600        # se la stazione non manda dati da più di così il valore non si mostra
STAZIONE_ATTESA = 5            # secondi di attesa massima della rete


def stazione_url_valido(u):
    """Solo https verso weatherlink.com (o un suo sottodominio), senza utente né password: l'indirizzo lo scrive una persona."""
    try:
        p = urlparse(u)
        h = (p.hostname or "").lower()
    except ValueError:
        return False
    return p.scheme == "https" and not p.username and not p.password and (h == "weatherlink.com" or h.endswith(".weatherlink.com"))


def stazione_url(app):
    """Indirizzo dei dati da config.json -> 'stazione'; '' se vuoto o non valido (la funzione resta spenta)."""
    u = str((app.cfg.leggi().get("stazione") or {}).get("url") or "").strip()
    return u if stazione_url_valido(u) else ""


def stazione_leggi(js):
    """JSON della stazione -> (umidità %, epoch dell'ultimo dato o None). ValueError se manca o non ha senso."""
    if not isinstance(js, dict):
        raise ValueError("risposta inattesa")
    um = float(str(js.get("humidity")).replace(",", "."))
    if not 0 <= um <= 100:
        raise ValueError("umidità fuori scala")
    ult = js.get("lastReceived")
    ts = int(ult / 1000) if isinstance(ult, (int, float)) and not isinstance(ult, bool) and ult > 1e11 else None   # millisecondi
    return round(um, 1), ts


def _stazione_stato(app, url):
    st = app.__dict__.setdefault("stazione_st", {"url": "", "um": None, "ts": 0, "errore": None, "falliti": 0, "prossimo": 0, "vecchio": False})
    if st["url"] != url:
        st.update(url=url, um=None, ts=0, errore=None, falliti=0, prossimo=0, vecchio=False)
    return st


def _stazione_lavoro(app, url):
    """Una lettura della stazione (la chiama il thread di fondo «esterni»). Il «dato vecchio» si giudica sul dato appena letto."""
    st = _stazione_stato(app, url)
    try:
        code, js = http_json("GET", url, {"User-Agent": "pdc-pannello", "Accept": "application/json"}, timeout=STAZIONE_ATTESA)
        if code != 200:
            raise ValueError(f"HTTP {code}")
        um, ts = stazione_leggi(js)
        ora = int(time.time())
        if st["url"] == url:                                  # nel frattempo l'indirizzo e' cambiato: scarto
            vecchio = ora - (ts or ora) > STAZIONE_VECCHIO
            if vecchio and not st["vecchio"]:
                log.warning("Stazione meteo: il sito risponde ma l'ultimo dato della stazione è delle %s, non lo mostro", time.strftime("%H:%M", time.localtime(ts)))
            elif not vecchio and (st["falliti"] or st["vecchio"]):
                log.info("Stazione meteo: di nuovo disponibile")
            st.update(um=um, ts=ts or ora, errore=None, falliti=0, vecchio=vecchio, prossimo=ora + STAZIONE_OGNI)
    except Exception as e:
        st["falliti"] += 1
        st["errore"] = str(e)[:120]
        st["prossimo"] = int(time.time()) + min(300 * st["falliti"], STAZIONE_OGNI)
        (log.warning if st["falliti"] == 1 else log.debug)("Stazione meteo: lettura non riuscita (%s)", e)


def stazione_info(app):
    """None se la funzione è spenta, altrimenti {'umidita': % o None, 'ts': ultimo dato della stazione, 'errore': testo o None}.
    Legge solo l'ultima lettura, fatta dal thread di fondo; il valore più vecchio di un'ora non si mostra."""
    ora = int(time.time())
    if app.demo:                                              # nella demo si vede sempre, come il meteo
        return {"umidita": 78.0, "ts": ora - 120, "errore": None}
    url = stazione_url(app)
    if not url:
        return None
    st = _stazione_stato(app, url)
    esterni_assicura(app)
    vecchio = st["um"] is not None and ora - st["ts"] > STAZIONE_VECCHIO
    um = None if vecchio else st["um"]
    return {"umidita": um, "ts": st["ts"] or None, "errore": st["errore"]}


# --------------------------------------------------------------------------
# Un solo thread di fondo per i dati esterni: stazione meteo vicina e previsione Open-Meteo restano sempre freschi,
# anche quando nessuno ha la pagina aperta (prima la lettura partiva solo con la Dashboard, e Alexa o il widget trovavano il buco)
# --------------------------------------------------------------------------

_lock_esterni = threading.Lock()


def esterni_giro(app):
    """Un giro: fa le letture scadute. -> fra quanti secondi guardare di nuovo (tra 15 e 60)."""
    if app.demo:
        return 60
    prossimi = []
    url = stazione_url(app)
    if url:
        st = _stazione_stato(app, url)
        if time.time() >= st["prossimo"]:
            _stazione_lavoro(app, url)
        prossimi.append(st["prossimo"])
    cfg = meteo_config(app)
    if cfg:
        st = _meteo_stato(app)
        if time.time() >= st["prossimo"]:
            _meteo_lavoro(app, *cfg)
        prossimi.append(st["prossimo"])
    return max(15, min(60, min(prossimi) - int(time.time()))) if prossimi else 60


class Esterni(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True, name="esterni")
        self.app, self.fermo = app, threading.Event()

    def run(self):
        self.fermo.wait(3)                # lascia partire il resto
        while not self.fermo.is_set():
            try:
                attesa = esterni_giro(self.app)
            except Exception:
                log.exception("errore nella lettura dei dati esterni (stazione, meteo)")
                attesa = 60
            self.fermo.wait(attesa)


def esterni_assicura(app):
    """Avvia il thread se non c'e'. All'avvio lo fa il programma; dopo un aggiornamento a caldo (che non riavvia i thread) basta la prima richiesta."""
    if app.demo:
        return
    t = app.__dict__.get("esterni")
    if t is not None and t.is_alive():
        return
    with _lock_esterni:
        t = app.__dict__.get("esterni")
        if t is None or not t.is_alive():
            t = app.__dict__["esterni"] = Esterni(app)
            t.start()


# --------------------------------------------------------------------------
# Stima: quando riparte il riscaldamento (dalla discesa della temperatura in casa)
# --------------------------------------------------------------------------
# Idea: a riscaldamento fermo la casa si raffredda come un corpo caldo in una stanza fredda, T(t) = E + (T0 - E) * exp(-k t),
# con E la temperatura esterna. Dallo storico ricavo k (quanto "tiene" la casa), di quanto sale ancora la temperatura dopo che lo
# Smarther smette di chiedere (inerzia del pavimento) e a quanti gradi sotto il target riparte. Poi dalla temperatura di adesso
# calcolo quando arrivera' a quella soglia. Si ricalcola dallo storico (ultimi 45 giorni, pesando di piu' i piu' recenti).
RIP_PASSO = 300           # secondi per cella
RIP_GIORNI = 45           # finestra dello storico
RIP_MEZZAVITA = 14.0      # giorni: dopo tanto un campione pesa la meta'
RIP_MIN_CAMPIONI = 5      # senza almeno cosi' tante discese vere la stima non compare
RIP_TRATTO = 18           # celle (90 minuti) dell'ultimo tratto su cui si misura k
RIP_MAX_ORE = 12          # oltre questo orizzonte la stima non e' credibile


def _med_pesata(vals, pesi):
    """Mediana pesata: robusta, non si fa spostare da una discesa strana."""
    coppie = sorted(zip(vals, pesi, strict=True))
    meta = sum(p for _, p in coppie) / 2.0
    acc = 0.0
    for v, p in coppie:
        acc += p
        if acc >= meta:
            return v
    return coppie[-1][0]


def ripartenza_campioni(T, R, E, S, ok, fine, diag=None):
    """Cerca nelle celle (dict cella -> valore) le discese a riscaldamento fermo e ne ricava i campioni.
    T temperatura casa, R richiesta calore (0/1), E esterna, S target, ok(cella) -> True se lo Smarther era in programma.
    Un campione = una pausa della richiesta, tra due accensioni, di almeno un'ora, con dati completi.
    diag (opzionale, per 'pdc_strumenti.py rip-diag'): lista in cui annotare ogni pausa trovata e perche' e' valida o scartata."""
    campioni = []
    celle = sorted(R)
    if not celle:
        return campioni
    i = 0
    n = len(celle)
    while i < n:
        b = celle[i]
        # acceso da almeno 3 celle consecutive, poi spento: e' la fine di un ciclo di riscaldamento
        if R[b] >= 0.5:
            j = i
            while j + 1 < n and celle[j + 1] == celle[j] + 1 and R[celle[j + 1]] >= 0.5:
                j += 1
            if j - i + 1 >= 3 and j + 1 < n and celle[j + 1] == celle[j] + 1 and R[celle[j + 1]] < 0.5:
                s = celle[j + 1]                     # prima cella spenta
                k = j + 1
                while k + 1 < n and celle[k + 1] == celle[k] + 1 and R[celle[k + 1]] < 0.5:
                    k += 1
                # k = ultima cella spenta; la pausa e' valida solo se poi lo Smarther RIPARTE (cella successiva accesa)
                riparte = k + 1 < n and celle[k + 1] == celle[k] + 1 and R[celle[k + 1]] >= 0.5
                if riparte and celle[k] - s + 1 >= 12:
                    e = celle[k + 1]
                    mot = []
                    c = _campione(T, E, S, ok, s, e, mot)
                    if c:
                        c["cella"] = e
                        campioni.append(c)
                    if diag is not None:
                        diag.append({"da": s * RIP_PASSO, "a": e * RIP_PASSO, "valida": bool(c), "motivo": None if c else (mot[0] if mot else "scartata"),
                                     "k": c["k"] if c else None, "sovra": c["sovra"] if c else None, "scarto": c["scarto"] if c else None})
                elif diag is not None:
                    if not riparte:
                        mot = "la pausa non e' finita (lo Smarther non e' ancora ripartito) o ci sono buchi nei dati"
                    else:
                        mot = "pausa troppo corta (%d min, ne servono almeno 60)" % ((celle[k] - s + 1) * RIP_PASSO // 60)
                    diag.append({"da": s * RIP_PASSO, "a": (celle[k] + 1) * RIP_PASSO, "valida": False, "motivo": mot,
                                 "k": None, "sovra": None, "scarto": None})
                i = k + 1
                continue
            i = j + 1
            continue
        i += 1
    return campioni


def _campione(T, E, S, ok, s, e, motivo=None):
    """Una pausa [s, e): s prima cella spenta, e cella in cui lo Smarther riparte.
    motivo (opzionale): lista in cui annotare perche' la pausa viene scartata (per la diagnostica)."""
    def no(m):
        if motivo is not None:
            motivo.append(m)
        return None
    tutte = range(s, e + 1)
    pres = sum(1 for x in tutte if x in T)
    if pres < 0.85 * len(tutte):
        return no("dati di temperatura incompleti (%d%% delle celle, ne servono almeno 85%%)" % (100 * pres // len(tutte)))
    if not all(ok(x) for x in (s, e)):
        return no("lo Smarther non era in modo programma/casa all'inizio o alla fine della pausa")
    t0 = T.get(s - 1, T.get(s))
    if t0 is None:
        return no("manca la temperatura prima dello stop")
    # picco: il massimo nella prima ora e mezza (il pavimento continua a cedere calore dopo lo stop)
    fin = min(e - 1, s + 17)
    cand = [(T[x], x) for x in range(s, fin + 1) if x in T]
    if not cand:
        return no("nessun dato di temperatura nella prima ora e mezza dopo lo stop")
    tpk, pk = max(cand)
    dur_h = (e - pk) * RIP_PASSO / 3600.0
    te = T.get(e)
    if te is None:
        return no("manca la temperatura nel momento della ripartenza")
    if dur_h < 0.75:
        return no("discesa troppo breve (%.1f h dal picco, ne servono 0,75)" % dur_h)
    # k si misura sull'ultimo tratto della discesa (al massimo 90 minuti prima della ripartenza): e' quello che conta per
    # prevedere quando riparte, e li' la curva e' piu' vicina a un'esponenziale che subito dopo il picco
    q = max(pk, e - RIP_TRATTO)
    tq = T.get(q)
    if tq is None:
        return no("manca la temperatura all'inizio dell'ultimo tratto di discesa")
    dq_h = (e - q) * RIP_PASSO / 3600.0
    es = [E[x] for x in range(q, e + 1) if x in E]
    if dq_h < 0.5:
        return no("ultimo tratto di discesa troppo corto (%d min, ne servono 30)" % (dq_h * 60))
    if len(es) < 0.7 * (e - q + 1):
        return no("dati della temperatura esterna incompleti nell'ultimo tratto")
    em = sum(es) / len(es)
    if tq - em < 3 or te - em < 3:
        return no("troppo poca differenza tra casa e esterno (casa %.1f / %.1f, esterna %.1f: servono almeno 3 gradi)" % (tq, te, em))
    if tq <= te + 0.05:
        return no("la temperatura non scende nell'ultimo tratto (da %.1f a %.1f)" % (tq, te))
    k = math.log((tq - em) / (te - em)) / dq_h
    if not (0.003 <= k <= 1.0):
        return no("coefficiente di raffreddamento fuori dai limiti (k=%.3f, ammessi 0,003-1,0)" % k)
    bers = S.get(e)
    return {"k": k, "sovra": max(0.0, tpk - t0), "coda_min": (pk - s) * RIP_PASSO / 60.0,
            "scarto": (bers - te) if bers is not None else None, "te": te}


def ripartenza_fit(campioni, ora, diag=None):
    """Dai campioni ai parametri: k (1/ora), sovraelongazione, durata della coda, soglia di ripartenza."""
    if len(campioni) < RIP_MIN_CAMPIONI:
        if diag is not None:
            diag.setdefault("motivi", []).append("storico insufficiente: %d pause valide su %d necessarie" % (len(campioni), RIP_MIN_CAMPIONI))
        return None
    pesi = [0.5 ** (max(0.0, ora - c["cella"] * RIP_PASSO) / 86400.0 / RIP_MEZZAVITA) for c in campioni]
    if diag is not None:
        diag["peso"] = sum(pesi)
    if sum(pesi) < 3.0:
        if diag is not None:
            diag.setdefault("motivi", []).append("pause valide troppo vecchie (peso %.1f, ne servono almeno 3,0): servono dati piu' recenti" % sum(pesi))
        return None
    scarti = [(c["scarto"], p) for c, p in zip(campioni, pesi, strict=True) if c["scarto"] is not None]
    assoluti = [(c["te"], p) for c, p in zip(campioni, pesi, strict=True)]
    return {"k": _med_pesata([c["k"] for c in campioni], pesi),
            "sovra": _med_pesata([c["sovra"] for c in campioni], pesi),
            "coda_min": _med_pesata([c["coda_min"] for c in campioni], pesi),
            "scarto": _med_pesata([v for v, _ in scarti], [p for _, p in scarti]) if scarti else None,
            "soglia": _med_pesata([v for v, _ in assoluti], [p for _, p in assoluti]),
            "n": len(campioni)}


def ripartenza_eta(fit, ora, t_casa, esterna, target, spenta_da, t_alla_fine, diag=None):
    """Quando la temperatura in casa arrivera' alla soglia di ripartenza: ora in secondi, oppure None.
    spenta_da = secondi da quando lo Smarther ha smesso di chiedere; t_alla_fine = temperatura in quel momento."""
    if not fit or t_casa is None or esterna is None:
        return None
    soglia = (target - fit["scarto"]) if (fit["scarto"] is not None and target is not None) else fit["soglia"]
    t_rif, t_ora = t_casa, ora
    if spenta_da is not None and t_alla_fine is not None and spenta_da < fit["coda_min"] * 60:
        # il pavimento sta ancora cedendo calore: la temperatura salira' ancora fino al picco tipico
        t_rif = max(t_casa, t_alla_fine + fit["sovra"])
        t_ora = ora + (fit["coda_min"] * 60 - spenta_da)
    if diag is not None:
        diag.update({"soglia": soglia, "t_rif": t_rif})
    if soglia - esterna < 1 or t_rif - esterna < 1:
        if diag is not None:
            diag["motivo"] = "casa o soglia troppo vicine alla temperatura esterna (%.1f / %.1f / esterna %.1f)" % (t_rif, soglia, esterna)
        return None
    if t_rif <= soglia:
        if diag is not None:
            diag["motivo"] = "la casa e' gia' sotto la soglia di ripartenza (%.1f contro %.1f): lo Smarther dovrebbe ripartire subito" % (t_rif, soglia)
        return None
    ore = math.log((t_rif - esterna) / (soglia - esterna)) / fit["k"]
    eta = t_ora + ore * 3600
    if eta - ora > RIP_MAX_ORE * 3600:
        if diag is not None:
            diag["motivo"] = "la ripartenza e' troppo lontana (%.1f ore, oltre il limite di %d)" % ((eta - ora) / 3600.0, RIP_MAX_ORE)
        return None
    return max(eta, ora)


def _celle_sql(db, serie, da, a, funz):
    """{cella: valore} con una sola query: media (o massimo) per cella di 5 minuti."""
    with closing(db._c()) as c:
        righe = c.execute(f"SELECT ts/{RIP_PASSO} AS b, {funz}(valore) FROM misure WHERE serie=? AND ts BETWEEN ? AND ? "
                          "GROUP BY b", (serie, int(da), int(a))).fetchall()
    return {b: v for b, v in righe if v is not None}


def stima_ripartenza(app, ora, t_casa, ts_casa, richiesta, esterna, target, diag=None):
    """Orario (in secondi, arrotondato al quarto d'ora) in cui il riscaldamento dovrebbe ripartire, oppure None.
    Si calcola solo a richiesta spenta, con dato fresco e con abbastanza discese vere nello storico.
    diag (opzionale, un dict): se c'e', si riempie con le pause trovate, i parametri e il primo motivo per cui la stima manca;
    in quel caso non si usa la cache. Serve a 'pdc_strumenti.py rip-diag'."""
    def no(m):
        if diag is not None:
            diag.setdefault("motivi", []).append(m)
            diag.setdefault("motivo", m)
        return None
    blocco = None
    if richiesta:
        blocco = "lo Smarther sta chiedendo calore adesso: la stima si fa solo a richiesta spenta"
    elif t_casa is None:
        blocco = "manca la temperatura della casa"
    elif esterna is None:
        blocco = "manca la temperatura esterna"
    elif ts_casa is None or ora - ts_casa > 900:
        blocco = "il dato della casa e' vecchio (%s): serve piu' recente di 15 minuti" % ("mai ricevuto" if ts_casa is None else "%d min" % ((ora - ts_casa) // 60))
    if blocco:
        no(blocco)
        if diag is None:
            return None
    db = app.db
    cache = None if diag is not None else app.__dict__.get("rip_cache")
    if not cache or ora - cache["ts"] > 600:
        da = ora - RIP_GIORNI * 86400
        T = _celle_sql(db, "casa.temperatura", da, ora, "AVG")
        R = _celle_sql(db, "casa.richiesta_calore", da, ora, "MAX")
        E = _celle_sql(db, "ciclo.esterna", da, ora, "AVG")
        S = _celle_sql(db, "casa.setpoint", da, ora, "AVG")
        cambi = q_cambi(db, "casa.modo", da, ora)

        def ok(b):
            m = stato_a(cambi, b * RIP_PASSO)
            return m in (None, "schedule", "home")
        pause = [] if diag is not None else None
        campioni = ripartenza_campioni(T, R, E, S, ok, ora // RIP_PASSO, pause)
        fit = ripartenza_fit(campioni, ora, diag)
        if diag is not None:
            diag.update({"pause": pause, "fit": fit, "celle": {"temperatura": len(T), "richiesta": len(R), "esterna": len(E), "target": len(S)}})
        else:
            cache = app.__dict__["rip_cache"] = {"ts": ora, "fit": fit}
    fit = diag["fit"] if diag is not None and "fit" in diag else cache["fit"]
    if not fit:
        return None
    if blocco:
        return None
    # da quando lo Smarther ha smesso di chiedere, e a che temperatura eravamo
    with closing(db._c()) as c:
        r = c.execute("SELECT MAX(ts) FROM misure WHERE serie='casa.richiesta_calore' AND valore>=0.5 AND ts>?",
                      (int(ora - 6 * 3600),)).fetchone()
    spenta_da = t_fine = None
    if r and r[0] is not None:
        spenta_da = max(0, ora - r[0])
        t_fine = q_valore_a(db, "casa.temperatura", r[0])
    ed = {} if diag is not None else None
    eta = ripartenza_eta(fit, ora, t_casa, esterna, target, spenta_da, t_fine, ed)
    if diag is not None:
        diag["eta_dettagli"] = {**ed, "spenta_da": spenta_da, "t_fine": t_fine}
    if eta is None:
        return no((ed or {}).get("motivo") or "stima non calcolabile")
    q15 = 900
    nuova = int(round(eta / q15) * q15)
    if diag is not None:
        return nuova
    # Orario stabile: il sensore da' la casa a passi di 0.1 gradi e, con la casa che scende piano, un passo vale anche mezz'ora o piu'.
    # Si tiene l'orario gia' mostrato finche' la nuova stima non si sposta di piu' di una soglia (il 40% del tempo che manca, tra 5 e 30 minuti:
    # piu' la ripartenza e' vicina, piu' l'orario segue la realta'). Si riparte da zero a ogni nuova pausa o dopo 30 minuti senza stima.
    ciclo = r[0] if r else None
    m = app.__dict__.get("rip_mostrata")
    if m and m["ciclo"] == ciclo and ora - m["ts"] <= 1800 and m["eta"] > ora - 600:
        soglia = min(1800, max(300, 0.4 * (m["eta"] - ora)))
        if abs(nuova - m["eta"]) <= soglia:
            nuova = m["eta"]
    app.rip_mostrata = {"ciclo": ciclo, "eta": nuova, "ts": ora}
    return nuova


@api("/api/dashboard")
def api_dashboard(app, q):
    ora = int(time.time())
    da, a = finestra(q, ora)
    db = app.db
    ult = db.ultimi_dict()
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
    stz = stazione_info(app)
    kwh_per = energia_kwh(db, da, a)
    servizi = [{"nome": NOMI[s.sigla], "ultimo_ok": s.stato["ultimo_ok"], "messaggio": s.stato["messaggio"]}
               for s in app.servizi]
    return {
        "ora": ora, "da": da, "a": a, "demo": app.demo, "intervallo": app.intervallo_pdc(),
        "ultimo_ok": max([s["ultimo_ok"] or 0 for s in servizi] + [ts_("acs.temperatura") or 0]),
        "modo": modo, "servizi": servizi, "accesso_st": info_accesso_st(app),
        "refresh": info_refresh(app),
        "meteo": meteo_info(app),
        "riscaldamento": {
            "stato": stato_risc, "temp": acqua, "target": num("risc.setpoint"), "ritorno": ritorno,
            "delta": round(acqua - ritorno, 1) if acqua is not None and ritorno is not None else None,
            "spark": q_griglia(db, "risc.acqua", da, a, 60)[0]},
        "acs": {
            "stato": stato_acs, "temp": num("acs.temperatura"), "target": num("acs.setpoint"),
            "modo": NOMI_MODO_ACS.get(txt("pdc.modo"), txt("pdc.modo")),
            "spark": q_griglia(db, "acs.temperatura", da, a, 60)[0]},
        "esterna": {"temp": num("ciclo.esterna"), "ts": ts_("ciclo.esterna"), "min": es_min, "max": es_max,
                    "umidita": (stz or {}).get("umidita"), "umidita_attiva": stz is not None,
                    "spark": q_griglia(db, "ciclo.esterna", da, a, 60)[0]},
        "energia": {"kwh": kwh_per, "potenza_w": num("pot.watt"), "prezzo_kwh": prezzo_kwh(app),
                    "dettaglio": dettaglio_costo(app, da, a, kwh_per, prezzo_kwh(app), ora),
                    "spark": q_griglia(db, "pot.watt", da, a, 60)[0]},
        "casa": {"temp": num("casa.temperatura"), "umidita": num("casa.umidita"), "soglie_umidita": soglie_umidita(app), "target": num("casa.setpoint"),
                 "riparte": stima_ripartenza(app, ora, num("casa.temperatura"), ts_("casa.temperatura"),
                                             num("casa.richiesta_calore") == 1.0, num("ciclo.esterna"), num("casa.setpoint")),
                 "richiesta": num("casa.richiesta_calore") == 1.0, "richiesta_pct": num("casa.richiesta_pct"),
                 "ts": ts_("casa.temperatura"), "spark": q_griglia(db, "casa.temperatura", da, a, 60)[0],
                 "spark_um": q_griglia(db, "casa.umidita", da, a, 60)[0]},
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


def info_refresh(app):
    """Stato del 'refresh' dei dati del compressore (smartthings.refresh): {attivo, stato, ogni, inviati, ultimo, messaggio}."""
    try:
        cfg = (app.cfg.leggi()["smartthings"].get("refresh")) or {}
        sv = next((x for x in app.servizi if x.sigla == "st"), None)
        i = getattr(sv, "refresh_info", None) or {}
        return {"attivo": bool(cfg.get("attivo")), "stato": i.get("stato", "spento"), "ogni": max(30, int(cfg.get("ogni_secondi") or 60)),
                "inviati": i.get("inviati", 0), "ultimo": i.get("ultimo"), "messaggio": i.get("messaggio")}
    except Exception:
        return {"attivo": False, "stato": "spento"}


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


def dettaglio_costo(app, da, a, kwh, prezzo, ora):
    """Costo del periodo diviso in riscaldamento, acqua calda e standby (euro), stesse proporzioni di Efficienza applicate
    al totale del contatore. None se manca il prezzo o i dati. Calcolo tenuto in memoria 5 minuti per periodo."""
    if prezzo <= 0 or not kwh or kwh <= 0:
        return None
    cache = app.__dict__.setdefault("det_cache", {})
    voce = cache.get(round(da))
    if voce and ora - voce["ts"] < 300:
        tot = voce["tot"]
    else:
        try:
            tol = max(150, int(app.intervallo_pdc() * 1.5))
            tot = calcola_efficienza(app.db, da, a, tol, ora)["tot"]
        except Exception:
            tot = None
        if len(cache) > 8:
            cache.clear()
        cache[round(da)] = {"ts": ora, "tot": tot}
    if not tot:
        return None
    parti = {k: max(0.0, tot.get("el_" + k) or 0.0) for k in ("casa", "acs", "riposo")}
    somma = sum(parti.values())
    if somma <= 0:
        return None
    return {k: round(kwh * prezzo * v / somma, 2) for k, v in parti.items()}


def costo_mese(app, ora):
    """Costo del mese in corso, stima a fine mese e confronto col mese scorso (None se manca il prezzo o i dati).
    La stima e' lineare (consumo medio per giorno trascorso x giorni del mese): vale solo come ordine di grandezza."""
    prezzo = prezzo_kwh(app)
    if prezzo <= 0:
        return None
    db = app.db
    adesso = datetime.fromtimestamp(ora)
    inizio = datetime(adesso.year, adesso.month, 1)
    prec = datetime(adesso.year - (adesso.month == 1), 12 if adesso.month == 1 else adesso.month - 1, 1)
    fine = datetime(adesso.year + (adesso.month == 12), 1 if adesso.month == 12 else adesso.month + 1, 1)
    with closing(db._c()) as c:
        r = c.execute("SELECT MIN(ts) FROM misure WHERE serie='en.totale_wh'").fetchone()
    primo = r[0] if r and r[0] is not None else None
    if primo is None:
        return None
    t_in, t_prec = inizio.timestamp(), prec.timestamp()
    kwh = energia_kwh(db, max(t_in, primo), ora)
    if kwh is None:
        return None
    trascorso = (ora - max(t_in, primo)) / 86400
    durata = (fine - inizio).total_seconds() / 86400
    stima = None
    if primo <= t_in and trascorso >= 1.0:
        stima = kwh / trascorso * durata
    scorso = energia_kwh(db, t_prec, t_in) if primo <= t_prec + 3600 else None
    # divisione tra riscaldamento, acqua calda e standby: stesse proporzioni della stima di Efficienza, applicate al
    # totale del contatore, cosi' la somma torna sempre con il costo del mese. Calcolo tenuto in memoria 5 minuti.
    det = None
    cache = app.__dict__.get("mese_cache")
    if cache and cache["mese"] == t_in and ora - cache["ts"] < 300:
        det = cache["tot"]
    else:
        try:
            tol = max(150, int(app.intervallo_pdc() * 1.5))
            det = calcola_efficienza(db, max(t_in, primo), ora, tol, ora)["tot"]
            app.__dict__["mese_cache"] = {"mese": t_in, "ts": ora, "tot": det}
        except Exception:
            det = None
    dettaglio = None
    if det:
        parti = {k: max(0.0, det.get("el_" + k) or 0.0) for k in ("casa", "acs", "riposo")}
        somma = sum(parti.values())
        if somma > 0:
            dettaglio = {k: round(kwh * prezzo * v / somma, 2) for k, v in parti.items()}
    return {"kwh": kwh, "euro": round(kwh * prezzo, 2), "dettaglio": dettaglio,
            "stima_euro": None if stima is None else round(stima * prezzo, 2),
            "scorso_euro": None if scorso is None else round(scorso * prezzo, 2),
            "mese": inizio.month, "mese_scorso": prec.month}


def cornice(app, ora):
    """Parti comuni a tutte le risposte: stato collegamenti e avvisi (per il pallino in alto e il banner)."""
    ult = app.db.ultimi_dict()
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


def dirada_richiesta(punti, passo_max=600):
    """Tiene solo i campioni che servono al grafico: quelli dove la richiesta cambia (e quello subito prima), e uno ogni 10 minuti
    dentro un periodo uguale (la pagina ne ha bisogno per non spezzare le fasce). Su 30 giorni passa da ~800 KB a poche decine."""
    out, ult = [], None
    for i, (t, v) in enumerate(punti):
        cambia = i == 0 or i == len(punti) - 1 or v != punti[i - 1][1] or v != punti[i + 1][1]
        if cambia or t - ult >= passo_max:
            out.append([t, v]); ult = t
    return out


# ---------- Finestre aperte (riconosciute dai dati) ----------
FIN_BASE = {"calo": 0.5, "ah": None}     # calo minimo in gradi; variazione minima (negativa) dell'umidita' assoluta, None = non richiesta


def fin_tabella(db):
    """La tabella nasce all'avvio; se il pannello e' stato aggiornato a caldo si crea qui, una volta sola."""
    if not getattr(db, "_fin_ok", False):
        with db.lock, closing(db._c()) as c, c:
            c.execute("CREATE TABLE IF NOT EXISTS finestre(da INTEGER PRIMARY KEY, a INTEGER NOT NULL, calo REAL, ah REAL,"
                      " stato TEXT NOT NULL, manuale INTEGER NOT NULL DEFAULT 0)")
        db._fin_ok = True


def fin_soglie(db):
    try:
        v = json.loads(db.meta_get("fin_soglie") or "{}")
    except ValueError:
        v = {}
    return {"calo": float(v.get("calo", FIN_BASE["calo"])), "ah": v.get("ah", FIN_BASE["ah"]), "n": int(v.get("n", 0))}


def umidita_assoluta(t, u):
    """g/m3 a partire da temperatura (gradi) e umidita' relativa (%)."""
    return 6.112 * math.exp(17.62 * t / (243.12 + t)) * u * 2.1674 / (273.15 + t)


def _ah_vicina(U, Uts, tt, ini_ts):
    """Variazione dell'umidita' assoluta tra l'inizio e il punto piu' freddo, o None se mancano i dati."""
    def umid(x):
        k = bisect.bisect_left(Uts, x - 150)
        return U[k][1] if k < len(U) else None
    u0, u1 = umid(ini_ts[0]), umid(tt[0])
    return None if u0 is None or u1 is None else round(umidita_assoluta(tt[1], u1) - umidita_assoluta(ini_ts[1], u0), 1)


def fin_rileva(db, da, a, soglie=None):
    """Finestre aperte probabili: la temperatura di casa scende piu' in fretta e piu' in basso di come farebbe da sola.
    Lavora su tutto lo storico (e' retroattivo): un solo passaggio sui campioni."""
    s = soglie or fin_soglie(db)
    T = q_serie(db, "casa.temperatura", da - 3600, a)
    U = q_serie(db, "casa.umidita", da - 3600, a)
    Uts = [x[0] for x in U]
    n, ev, i, p, coda = len(T), [], 0, 0, collections.deque()     # coda: massimi degli ultimi 25 minuti, dal piu' vecchio
    inneschi = max(0.3, s["calo"] * 0.8)
    while i < n:
        while p <= i:
            while coda and coda[-1][1] < T[p][1]:
                coda.pop()
            coda.append(T[p])
            p += 1
        t, v = T[i]
        while coda[0][0] < t - 1500:
            coda.popleft()
        pic = coda[0]
        if pic[1] - v >= inneschi:
            vmin, tmin, j = v, t, i
            while j + 1 < n:
                j += 1
                tj, vj = T[j]
                if vj < vmin:
                    vmin, tmin = vj, tj
                if vj - vmin >= 0.25 or tj - pic[0] > 10800:
                    break
            calo = pic[1] - vmin
            if calo >= s["calo"] and tmin - pic[0] <= 10800:
                dah = _ah_vicina(U, Uts, (tmin, vmin), pic)
                if s["ah"] is None or dah is None or dah <= s["ah"]:
                    ev.append({"da": pic[0], "a": tmin + 300, "calo": round(calo, 1), "ah": dah})
            i = j + 1
        else:
            i += 1
    return [e for e in ev if e["a"] > da and e["da"] < a]


def fin_eventi(db, da, a):
    """Quello che il grafico mostra: le finestre confermate (anche a mano) e quelle ancora da confermare.
    Le risposte «no» spariscono, e con loro i rilevamenti che si sovrappongono."""
    fin_tabella(db)
    with closing(db._c()) as c:
        reg = c.execute("SELECT da, a, calo, ah, stato, manuale FROM finestre WHERE a > ? AND da < ? ORDER BY da", (int(da), int(a))).fetchall()
        tot = c.execute("SELECT COUNT(*) FROM finestre WHERE manuale=0").fetchone()[0]
    out = [{"da": r[0], "a": r[1], "calo": r[2], "ah": r[3], "stato": "si", "manuale": r[5]} for r in reg if r[4] == "si"]
    for e in fin_rileva(db, da, a):
        if not any(r[0] < e["a"] and r[1] > e["da"] for r in reg):
            out.append({**e, "stato": "da confermare", "manuale": 0})
    out.sort(key=lambda e: e["da"])
    s = fin_soglie(db)
    return out, {"risposte": tot, "tarato": s["n"] >= 10, "n": s["n"]}


def fin_calibra(db):
    """Dopo almeno 10 risposte (con almeno 2 si' e 2 no) sceglie le soglie che sbagliano meno sulle risposte date."""
    with closing(db._c()) as c:
        righe = c.execute("SELECT calo, ah, stato FROM finestre WHERE calo IS NOT NULL").fetchall()
    si = [r for r in righe if r[2] == "si"]
    no = [r for r in righe if r[2] == "no"]
    if len(righe) < 10 or len(si) < 2 or len(no) < 2:
        return
    def passa(r, c, h):
        return r[0] >= c - 1e-9 and (h is None or r[1] is None or r[1] <= h)
    migliori = None
    for c in [round(0.3 + 0.1 * k, 1) for k in range(13)]:
        for h in (None, 0.0, -0.3, -0.6, -1.0, -1.5):
            giusti = sum(passa(r, c, h) for r in si) + sum(not passa(r, c, h) for r in no)
            chiave = (-giusti, abs(c - FIN_BASE["calo"]), 0 if h is None else 1)
            if migliori is None or chiave < migliori[0]:
                migliori = (chiave, c, h)
    db.meta_set("fin_soglie", json.dumps({"calo": migliori[1], "ah": migliori[2], "n": len(righe)}))


def _num_fin(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


@api_post("/api/finestre/giudica")
def api_fin_giudica(app, corpo, h):
    fin_tabella(app.db)
    da, a, calo, ah = _num_fin(corpo.get("da")), _num_fin(corpo.get("a")), _num_fin(corpo.get("calo")), _num_fin(corpo.get("ah"))
    stato = corpo.get("stato")
    if da is None or a is None or a <= da or a - da > 86400 or stato not in ("si", "no"):
        return {"ok": False, "errore": "Dati non validi."}
    with app.db.lock, closing(app.db._c()) as c, c:
        c.execute("DELETE FROM finestre WHERE a > ? AND da < ?", (int(da), int(a)))
        c.execute("INSERT INTO finestre(da, a, calo, ah, stato, manuale) VALUES(?,?,?,?,?,0)", (int(da), int(a), calo, ah, stato))
    fin_calibra(app.db)
    return {"ok": True}


@api_post("/api/finestre/manuale")
def api_fin_manuale(app, corpo, h):
    fin_tabella(app.db)
    da, a = _num_fin(corpo.get("da")), _num_fin(corpo.get("a"))
    if da is None or a is None or a <= da or a - da > 86400:
        return {"ok": False, "errore": "L'intervallo non è valido (max 24 ore)."}
    T = q_serie(app.db, "casa.temperatura", da - 1500, a)
    U = q_serie(app.db, "casa.umidita", da - 1500, a)
    calo = ah = None
    if len(T) >= 2:
        dentro = [x for x in T if x[0] >= da] or T
        pic = max((x for x in T if x[0] <= da + 1500), key=lambda x: (x[1], -x[0]), default=T[0])
        minimo = min(dentro, key=lambda x: x[1])
        calo = round(max(0.0, pic[1] - minimo[1]), 1)
        ah = _ah_vicina(U, [x[0] for x in U], minimo, pic)
    with app.db.lock, closing(app.db._c()) as c, c:
        c.execute("DELETE FROM finestre WHERE a > ? AND da < ?", (int(da), int(a)))
        c.execute("INSERT INTO finestre(da, a, calo, ah, stato, manuale) VALUES(?,?,?,?, 'si', 1)", (int(da), int(a), calo, ah))
    fin_calibra(app.db)
    return {"ok": True}


@api_post("/api/finestre/annulla")
def api_fin_annulla(app, corpo, h):
    fin_tabella(app.db)
    da = _num_fin(corpo.get("da"))
    if da is None:
        return {"ok": False, "errore": "Dati non validi."}
    with app.db.lock, closing(app.db._c()) as c, c:
        c.execute("DELETE FROM finestre WHERE da=?", (int(da),))
    fin_calibra(app.db)
    return {"ok": True}


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
    richiesta = dirada_richiesta(q_serie(app.db, "casa.richiesta_calore", da, a))
    out.update({"da": da, "a": a, "passo": dim, "grafici": grafici, "richiesta": richiesta, "soglie_umidita": soglie_umidita(app),
                "modi": calcola_modi(app.db, da, a, tol, ora)})
    out["finestre"], out["finestre_info"] = fin_eventi(app.db, da, a)
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
    n = len(v)
    return None if not n else (v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0)


def _tratti_calmi(temp, tts, cicli, w0, w1):
    """[(inizio, fine)] dei tratti tranquilli tra w0 e w1, lunghi almeno 1 ora: senza riscaldo dell'acqua (piu' 20 minuti di rimescolamento dopo)
    e senza prelievi (calo di 1 grado o piu' in 15 minuti) ne' rimescolamenti (salita di 0,3 gradi o piu'). Dopo un evento si aspettano 10 minuti."""
    occ = sorted((i, f + 1200) for i, f in cicli if f + 1200 > w0 and i < w1)
    liberi, cur = [], w0
    for i, f in occ:
        if i > cur:
            liberi.append((cur, i))
        cur = max(cur, f)
    if cur < w1:
        liberi.append((cur, w1))
    out = []
    for g0, g1 in liberi:
        pts = temp[bisect.bisect_left(tts, g0):bisect.bisect_right(tts, g1)]
        ini = g0
        for (ta, va), (tb, vb) in zip(pts, pts[1:], strict=False):
            if tb - ta <= 900 and (va - vb >= 1.0 or vb - va >= 0.3):
                if ta - ini >= 3600:
                    out.append((ini, ta))
                ini = tb + 600
        if g1 - ini >= 3600:
            out.append((ini, g1))
    return out


def perdita_giorno(temp, tts, cicli, t_at, da, a, litri, ore_da=6, ore_a=23):
    """Perdita a riposo di giorno (6-23): i gradi persi per ora nei tratti tranquilli, tutti insieme (somma dei gradi / somma delle ore).
    None se in tutto ci sono meno di 2 ore tranquille."""
    lt = time.localtime(da)
    gradi = ore = 0.0
    n = 0
    for k in range(0, (a - da) // 86400 + 3):
        w0 = max(da, _ore_locali(lt, ore=ore_da, giorni=k - 1))
        w1 = min(a, _ore_locali(lt, ore=ore_a, giorni=k - 1))
        if w1 - w0 < 3600:
            continue
        for g0, g1 in _tratti_calmi(temp, tts, cicli, w0, w1):
            v0, v1 = t_at(g0, 900), t_at(g1, 900)
            if v0 is None or v1 is None:
                continue
            dentro = temp[bisect.bisect_left(tts, g0):bisect.bisect_right(tts, g1)]
            h = (g1 - g0) / 3600.0
            if (dentro and max(v for _, v in dentro) > v0 + 0.5) or not 0 <= (v0 - v1) / h <= 3:
                continue
            gradi += v0 - v1
            ore += h
            n += 1
    if ore < 2:
        return None
    r = gradi / ore
    return {"gradi_ora": round(r, 2), "watt": round(r * litri * 1.163), "ore": round(ore, 1), "tratti": n}


def calcola_bollitore(db, da, a, ora, litri=300):
    """Salute del bollitore (stime dalla sonda dell'acqua calda):
    - perdita a riposo: di notte (23-06), nel tratto piu' lungo (>=3 h) senza riscaldo dell'acqua, di quanto scende la temperatura (gradi/ora);
    - perdita di giorno (6-23): come sopra ma sui tratti tranquilli di almeno 1 ora, senza prelievi (vedi perdita_giorno);
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
        w0 = _ore_locali(lt, ore=23, giorni=k - 1)
        w1 = _ore_locali(lt, ore=6, giorni=k)
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
    out = {"perdita": None, "salita": None, "giorno": perdita_giorno(temp, tts, cicli, t_at, da, a, litri), "litri": litri}
    if len(perd) >= 2:
        r = _mediana(perd)
        out["perdita"] = {"gradi_ora": round(r, 2), "watt": round(r * litri * 1.163), "notti": len(perd)}
    if len(sal) >= 2:
        rate = _mediana([g / (d / 3600.0) for d, g in sal])
        out["salita"] = {"gradi_ora": round(rate, 1), "min_per_10": round(600.0 / rate) if rate > 0 else None,
                         "minuti": round(_mediana([d for d, _ in sal]) / 60), "gradi": round(_mediana([g for _, g in sal]), 1),
                         "cicli": len(sal)}
    return out if (out["perdita"] or out["salita"] or out["giorno"]) else None


SBR_MAX_ELENCO = 50


def elenco_sbrinamenti(db, da, a, ora):
    """Sbrinamenti nel periodo, dalle transizioni on/off di pdc.sbrinamento.
    Inizio = passaggio a "on" dopo 'da'; fine = il "off" successivo (None se ancora in corso).
    kWh = differenza del contatore tra inizio e fine (approssimata: il contatore e' a scatti); None se non leggibile.
    Esterna = media dei campioni di ciclo.esterna attorno allo sbrinamento. Piu' recenti per primi."""
    da, a = int(da), min(int(a), int(ora))
    cambi = q_cambi(db, "pdc.sbrinamento", da, a)
    lista, inizio = [], None
    for ts, v in cambi[1 if cambi and cambi[0][0] == da else 0:]:      # il primo e' lo stato che c'era a 'da' (se c'era)
        if v == "on" and inizio is None:
            inizio = ts
        elif v != "on" and inizio is not None:
            lista.append((inizio, ts))
            inizio = None
    if inizio is not None:
        lista.append((inizio, None))
    out = []
    for i, f in lista:
        fine = f if f is not None else int(ora)
        e0, e1 = q_valore_a(db, "en.totale_wh", i), q_valore_a(db, "en.totale_wh", fine)
        kwh = round(max(0.0, e1 - e0) / 1000, 2) if e0 is not None and e1 is not None else None
        est = [v for _, v in q_serie(db, "ciclo.esterna", i - 900, fine + 900)]
        out.append({"ts": i, "fine": f, "min": round((fine - i) / 60), "kwh": kwh,
                    "esterna": round(sum(est) / len(est), 1) if est else None, "incorso": f is None})
    out.reverse()
    chiusi = [s for s in out if not s["incorso"]]
    kw = [s["kwh"] for s in out if s["kwh"] is not None]
    est = [s["esterna"] for s in out if s["esterna"] is not None]
    return {"n": len(out), "kwh": round(sum(kw), 2) if kw else None,
            "media_min": round(sum(s["min"] for s in chiusi) / len(chiusi), 1) if chiusi else None,
            "esterna": round(sum(est) / len(est), 1) if est else None,
            "elenco": out[:SBR_MAX_ELENCO]}


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
            "giorno": chiave, "ts": _ore_locali(lt),
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
    sbr = elenco_sbrinamenti(db, da, a, ora)
    sbrinamenti = sbr["n"]
    curva_cop = {}
    for m in ("casa", "acs"):
        curva_cop[m] = [{"da": b, "a": b + 2, "cop": round(d[0] / d[1], 2), "ore": round(d[2] * ore_h, 1)}
                        for b, d in sorted(bins[m].items()) if d[2] >= 6 and d[1] > 0]
    return {
        "giorni": righe, "bins": curva_cop, "cicli": cicli, "sbrinamenti": sbrinamenti, "sbr": sbr,
        "tot": {"el_casa": round(tot["el"]["casa"], 1), "el_acs": round(tot["el"]["acs"], 1),
                "el_riposo": round(tot["el"]["riposo"], 1),
                "calore_casa": round(tot["calore"]["casa"], 1), "calore_acs": round(tot["calore"]["acs"], 1),
                "cop_casa": cop("casa"), "cop_acs": cop("acs"),
                "cop": round(cal_c / el_c, 2) if el_c > 0.1 else None,
                "ore_casa": round(modi["minuti"]["casa"] / 60, 1), "ore_acs": round(modi["minuti"]["acs"] / 60, 1)},
        "copertura": {m: round(100.0 * coperte[m] / totali_celle[m]) if totali_celle[m] else None
                      for m in coperte},
    }


# Giorni "tipici": con il pulsante Confronta, per ogni giorno completo si cerca nello storico (ultimi 90 giorni) un gruppo di giorni
# con la stessa temperatura esterna media (entro 2 gradi) e se ne prende la mediana: e' il consumo / il COP "normale" per
# quel freddo. Un giorno molto sopra il normale e' segnalato. Servono almeno 3 giorni simili, altrimenti niente (nessun valore inventato).
TIP_STORICO_GIORNI = 90
TIP_DELTA_GRADI = 2.0
TIP_MIN_SIMILI = 3
TIP_SOPRA = 1.25          # oltre il 25% sopra il tipico ...
TIP_SOPRA_MIN_KWH = 1.5   # ... e almeno cosi' tanti kWh in piu': segnala un giorno anomalo


def giorni_tipici(app, giorni_sel, ora, oggi0, tol):
    """{giorno: {el, cop_casa, cop_acs, n, sopra}} per i giorni del periodo che hanno abbastanza giorni simili nello storico."""
    cache = app.__dict__.get("tip_cache")
    if not cache or cache["giorno"] != oggi0 or ora - cache["ts"] > 1800:
        da = oggi0 - TIP_STORICO_GIORNI * 86400
        rif = calcola_efficienza(app.db, da, ora, tol, ora)["giorni"]
        cache = app.__dict__["tip_cache"] = {"giorno": oggi0, "ts": ora, "rif": rif}
    tot = lambda g: g["el_casa"] + g["el_acs"] + g["el_riposo"]
    pieni = lambda g: g["esterna"] is not None and g["nodati_min"] <= 120 and g["ts"] < oggi0 and tot(g) > 0.5
    rif = [g for g in cache["rif"] if pieni(g)]
    out = {}
    for g in giorni_sel:
        if not pieni(g):
            continue
        simili = [r for r in rif if r["giorno"] != g["giorno"] and abs(r["esterna"] - g["esterna"]) <= TIP_DELTA_GRADI]
        if len(simili) < TIP_MIN_SIMILI:
            continue
        el = _mediana([tot(r) for r in simili])
        cc = [r["cop_casa"] for r in simili if r["cop_casa"] is not None]
        ca = [r["cop_acs"] for r in simili if r["cop_acs"] is not None]
        out[g["giorno"]] = {"el": round(el, 2), "n": len(simili), "esterna": g["esterna"],
                            "cop_casa": round(_mediana(cc), 2) if len(cc) >= TIP_MIN_SIMILI else None,
                            "cop_acs": round(_mediana(ca), 2) if len(ca) >= TIP_MIN_SIMILI else None,
                            "sopra": bool(tot(g) > el * TIP_SOPRA and tot(g) - el >= TIP_SOPRA_MIN_KWH)}
    return out


@api("/api/efficienza")
def api_efficienza(app, q):
    ora = int(time.time())
    try:
        giorni = int(q.get("giorni", ["7"])[0])
    except ValueError:
        giorni = 7
    giorni = max(1, min(90, giorni))
    lt = time.localtime(ora)
    oggi0 = _ore_locali(lt)
    da = oggi0 - (giorni - 1) * 86400
    # i confini dei giorni cadono su mezzanotte locale: la griglia da 5 minuti parte da li'
    tol = max(150, int(app.intervallo_pdc() * 1.5))
    out = cornice(app, ora)
    out.update(calcola_efficienza(app.db, da, ora, tol, ora))
    out.update({"da": da, "a": ora, "n_giorni": giorni, "prezzo_kwh": prezzo_kwh(app),
                "energia_contatore": energia_kwh(app.db, da, ora), "mese": costo_mese(app, ora)})
    litri = int((app.cfg.leggi().get("bollitore") or {}).get("litri") or 300)
    out["bollitore"] = calcola_bollitore(app.db, da, ora, ora, litri)
    if q.get("confronta", ["0"])[0] == "1":
        out["tipici"] = giorni_tipici(app, out["giorni"], ora, oggi0, tol)
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
    oggi0 = _ore_locali(lt)
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

def _liscio(d, bordo):
    """1 dentro, 0 fuori, con bordo morbido (d = distanza dal bordo in pixel)."""
    return max(0.0, min(1.0, 0.5 - d / bordo))


def _png(n, tipo_colore, righe, alto=None):
    """PNG n x alto (quadrato se manca alto) a 8 bit da righe gia' pronte (ciascuna col byte di filtro iniziale); tipo_colore 2 = RGB, 6 = RGBA."""
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, alto or n, 8, tipo_colore, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(righe), 9)) + chunk(b"IEND", b""))


def png_icona(n=180):
    righe = []
    for y in range(n):
        r = bytearray([0])
        for x in range(n):
            dx, dy = x + .5 - n / 2, y + .5 - n / 2
            d = math.hypot(dx, dy)
            t = y / n
            col = [11 + 12 * t, 15 + 14 * t, 23 + 30 * t]
            anello = _liscio(abs(d - n * .27) - n * .055, 1.4)
            if anello:
                k = (dx / n + .5)
                c2 = [255 - 60 * k, 107 + 90 * k, 87 - 20 * k]
                col = [col[i] * (1 - anello) + c2[i] * anello for i in range(3)]
            punto = _liscio(d - n * .095, 1.4)
            if punto:
                c3 = [52, 211, 176]
                col = [col[i] * (1 - punto) + c3[i] * punto for i in range(3)]
            r += bytes(int(max(0, min(255, v))) for v in col)
        righe.append(bytes(r))
    return _png(n, 2, righe)


def png_favicon(n=64):
    """Icona della scheda del browser: solo il simbolo (anello e punto), sfondo trasparente e piu' grande di quella della Home."""
    righe = []
    for y in range(n):
        r = bytearray([0])
        for x in range(n):
            dx, dy = x + .5 - n / 2, y + .5 - n / 2
            d = math.hypot(dx, dy)
            a = _liscio(abs(d - n * .37) - n * .075, 1.1)
            k = dx / n + .5
            col = [255 - 60 * k, 107 + 90 * k, 87 - 20 * k]
            p = _liscio(d - n * .13, 1.1)
            if p:
                col = [col[i] * (1 - p) + (52, 211, 176)[i] * p for i in range(3)]
                a = max(a, p)
            r += bytes(int(max(0, min(255, v))) for v in col) + bytes([int(a * 255)])
        righe.append(bytes(r))
    return _png(n, 6, righe)


# Schermate di avvio per l'iPhone (apple-touch-startup-image): senza, all'apertura dalla Home si vede un lampo bianco.
# Fondo scuro come la pagina e solo anello e punto al centro; i formati sono i pixel reali dei vari iPhone.
AVVIO_FORMATI = [  # (larghezza pt, altezza pt, densita')
    (440, 956, 3), (430, 932, 3), (428, 926, 3), (420, 912, 3), (414, 896, 3), (414, 896, 2), (414, 736, 3),
    (402, 874, 3), (393, 852, 3), (390, 844, 3), (375, 812, 3), (375, 667, 2)]
AVVIO_PIXEL = {f"{w * d}x{h * d}": (w * d, h * d) for w, h, d in AVVIO_FORMATI}


def png_avvio(w, h):
    fondo = bytes((11, 15, 23))
    vuota = b"\x00" + fondo * w
    n = round(w * .28)          # lato del riquadro con anello e punto
    x0, y0 = (w - n) // 2, (h - n) // 2
    righe = [vuota] * h
    for y in range(n):
        r = bytearray(b"\x00" + fondo * x0)
        for x in range(n):
            dx, dy = x + .5 - n / 2, y + .5 - n / 2
            d = math.hypot(dx, dy)
            a = _liscio(abs(d - n * .37) - n * .075, 1.1)
            k = dx / n + .5
            col = [255 - 60 * k, 107 + 90 * k, 87 - 20 * k]
            pt = _liscio(d - n * .13, 1.1)
            if pt:
                col = [col[i] * (1 - pt) + (52, 211, 176)[i] * pt for i in range(3)]
                a = max(a, pt)
            r += bytes(int(max(0, min(255, fondo[i] * (1 - a) + col[i] * a))) for i in range(3))
        r += fondo * (w - x0 - n)
        righe[y0 + y] = bytes(r)
    return _png(w, 2, righe, h)


_AVVIO = {}


def avvio(chiave):
    if chiave not in _AVVIO:
        _AVVIO[chiave] = png_avvio(*AVVIO_PIXEL[chiave])
    return _AVVIO[chiave]


_ICONA = []
_FAVICON = []


def icona():
    if not _ICONA:
        _ICONA.append(png_icona())
    return _ICONA[0]


def favicon():
    if not _FAVICON:
        _FAVICON.append(png_favicon())
    return _FAVICON[0]


MANIFEST = {"name": "Pompa di calore", "short_name": "PDC", "start_url": "/", "display": "standalone",
            "background_color": "#0b0f17", "theme_color": "#0b0f17", "lang": "it",
            "icons": [{"src": "/icona.png", "sizes": "180x180", "type": "image/png"}]}

_GZ_CACHE = {}
TIPI_COMPRIMIBILI = {"text/html", "text/css", "text/javascript", "text/csv", "text/plain", "application/json",
                     "application/manifest+json", "image/svg+xml"}
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


def da_casa(h):
    """True solo se la richiesta arriva dalla rete di casa: niente intestazioni del tunnel Cloudflare (le mette sempre
    lui) e indirizzo privato o locale. Serve per le modifiche delicate, che da fuori non si fanno."""
    if any(h.headers.get(x) for x in ("CF-Connecting-IP", "CF-Ray", "X-Forwarded-For", "Cf-Visitor")):
        return False
    try:
        ip = ipaddress.ip_address(h.client_address[0].split("%")[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def e_https(h):
    return (h.headers.get("X-Forwarded-Proto", "") == "https"
            or '"https"' in h.headers.get("Cf-Visitor", ""))


class Gestore(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 60

    def log_message(self, fmt, *args):
        log.debug("web %s - %s", self.address_string(), fmt % args)

    # ---- risposte ----
    def _invia(self, codice, corpo, tipo="text/html; charset=utf-8", extra=None, gz_chiave=None):
        dati = corpo if isinstance(corpo, bytes) else corpo.encode("utf-8")
        compresso = False
        # testo, JSON, CSV e SVG si comprimono (da 5 a 10 volte più piccoli): conta soprattutto via tunnel, dove passano dalla tua linea in upload
        if (len(dati) > 1024 and tipo.split(";")[0] in TIPI_COMPRIMIBILI and "gzip" in self.headers.get("Accept-Encoding", "")
                and codice == 200):
            if gz_chiave:       # file fissi (app.js, app.css): si comprimono una volta per versione, non a ogni richiesta
                k = (gz_chiave, BUILD)
                if k not in _GZ_CACHE:
                    for vecchia in [x for x in _GZ_CACHE if x[1] != BUILD]:      # le versioni vecchie non servono piu'
                        del _GZ_CACHE[vecchia]
                    _GZ_CACHE[k] = gzip.compress(dati, 6, mtime=0)
                dati, compresso = _GZ_CACHE[k], True
            else:
                dati, compresso = gzip.compress(dati, 5, mtime=0), True
        self.send_response(codice)
        self.send_header("Content-Type", tipo)
        if compresso:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
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
        if getattr(self, "_chiudi", False):      # corpo della richiesta non letto: la connessione non si riusa
            self.send_header("Connection", "close")
        for k, v in extra:
            if k not in sost:
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(dati)

    def _rifiuta_json(self, obj, codice):
        """Errore dato prima di leggere il corpo della richiesta: poi si chiude la connessione, altrimenti il corpo
        rimasto in coda sarebbe letto come richiesta successiva (HTTP/1.1 keep-alive)."""
        self._chiudi = True
        self._json(obj, codice)

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

    def _dati_iniziali(self, app):
        """La Dashboard gia' pronta dentro la pagina: il browser la disegna subito, senza il giro in piu' per i dati.
        Vale per la finestra salvata dal browser (cookie pdc_r: 6 o 24 ore); se il browser ne usa un'altra, la ignora."""
        if self.command == "HEAD":
            return "null"
        r = "6h" if self._cookie("pdc_r") == "6h" else "24h"
        ora = int(time.time())
        try:
            q = {"da": [str(ora - (6 if r == "6h" else 24) * 3600)], "a": [str(ora)], "_casa": ["1" if da_casa(self) else "0"]}
            dati = API["/api/dashboard"](app, q)
        except Exception:
            log.exception("dati iniziali della Dashboard")
            return "null"
        return json.dumps({"r": r, "dati": dati}, separators=(",", ":"), ensure_ascii=False).replace("<", "\\u003c")

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
                etag = f'"{BUILD}-{p[1:]}"'     # il browser chiede «e' cambiato?» e di solito riceve solo un 304 (conta via tunnel)
                # con ?v= uguale alla versione in corso il file non cambia mai: il browser lo tiene senza nemmeno chiedere (un giro in meno)
                fisso = q.get("v") == [BUILD]
                extra = [("Cache-Control", "public, max-age=31536000, immutable" if fisso else "no-cache"), ("ETag", etag)]
                if self.headers.get("If-None-Match") == etag:
                    return self._invia(304, b"", pubbliche[p], extra)
                return self._invia(200, WEB_ASSETS[p[1:]], pubbliche[p], extra, gz_chiave=p)
            if p == "/icona.png":
                return self._invia(200, icona(), "image/png", [("Cache-Control", "max-age=86400")])
            if p.startswith("/avvio/") and p.endswith(".png") and p[7:-4] in AVVIO_PIXEL:
                return self._invia(200, avvio(p[7:-4]), "image/png", [("Cache-Control", "max-age=86400")])
            if p == "/favicon.png":
                return self._invia(200, favicon(), "image/png", [("Cache-Control", "max-age=86400")])
            if p == "/manifest.webmanifest":
                return self._invia(200, json.dumps(MANIFEST), "application/manifest+json")
            if p == "/login":
                if self.command == "POST":
                    return self._login(app)
                if not app.demo and app.sessioni.valida(self._cookie("pdc_sid")):
                    return self._vai("/")   # gia' dentro (es. icona della Home salvata sulla pagina di login)
                return self._invia(200, self._pagina("login.html", errore=""))
            if p.startswith("/plugin/"):      # rotte pubbliche dei plugin (l'autenticazione e' del plugin)
                return plugin_pubblico(app, self, p, q)
            autenticato = app.demo or app.sessioni.valida(self._cookie("pdc_sid"))
            if p == "/logout" and self.command == "POST":
                app.sessioni.elimina(self._cookie("pdc_sid"))
                return self._vai("/login", [("Set-Cookie", "pdc_sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")])
            if not autenticato:
                if p.startswith("/api/"):
                    return self._json({"errore": "non autenticato"}, 401)
                return self._vai("/login")
            if p == "/":
                return self._invia(200, self._pagina("index.html", dati0=self._dati_iniziali(app)))
            if p.startswith("/loghi/") and self.command in ("GET", "HEAD"):
                return self._logo(app, p[7:])
            if p == "/api/flusso":
                return self._flusso(app)
            if p == "/api/esporta.csv" and self.command in ("GET", "HEAD"):
                dati, nome = csv_esporta(app, q)
                return self._invia(200, dati, "text/csv; charset=utf-8",
                                   [("Content-Disposition", f'attachment; filename="{nome}"')])
            if p in API and self.command in ("GET", "HEAD"):
                q["_casa"] = ["1" if da_casa(self) else "0"]
                return self._json(API[p](app, q))
            if p in API_POST and self.command == "POST":
                return self._post_api(app, p)
            self._invia(404, "Non trovato", "text/plain; charset=utf-8")
        except (ConnectionError, TimeoutError):     # il browser se n'e' andato a meta' (pipe rotta, reset, abort 10053 di Windows)
            pass
        except Exception:
            log.exception("errore nella richiesta web %s", self.path)
            try:
                self._json({"errore": "errore interno"}, 500)
            except Exception:
                pass

    def _logo(self, app, nome):
        """File dalla cartella dati/loghi. Solo nomi e estensioni previsti: niente percorsi liberi."""
        m = re.fullmatch(r"(smarther|samsung|netatmo|bticino)\.(svg|png|webp|jpe?g)", nome)
        f = app.base / "dati" / "loghi" / nome
        if not m or not f.is_file() or f.stat().st_size > 2_000_000:
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
            return self._rifiuta_json({"errore": "richiesta non consentita"}, 403)
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if n < 0 or n > 200_000:
            return self._rifiuta_json({"errore": "richiesta non valida" if n < 0 else "richiesta troppo grande"}, 400 if n < 0 else 413)
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
        self._chiudi = True      # in tutti i casi di rifiuto (e se il corpo supera il limite) il corpo non viene letto per intero
        if origine and origine != "null" and urlparse(origine).netloc != self.headers.get("Host", ""):
            return self._invia(403, "Richiesta non consentita", "text/plain; charset=utf-8")
        if app.limite.bloccato(ip):
            return self._invia(429, self._pagina("login.html", errore="Troppi tentativi. Riprova tra 15 minuti."))
        try:
            n = max(0, min(int(self.headers.get("Content-Length", "0")), 4096))
        except ValueError:
            n = 0
        lungh = self.headers.get("Content-Length", "0").strip()
        self._chiudi = not lungh.isdigit() or int(lungh) > n        # corpo piu' lungo del limite: resta in coda, quindi si chiude
        dati = parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
        utente, pw = dati.get("utente", [""])[0], dati.get("password", [""])[0]
        acc = app.cfg.leggi()["accesso"]
        ok_u = hmac.compare_digest(utente.encode(), (acc.get("utente") or "").encode())
        ok_p = verifica_password(pw, acc.get("password_hash") or "")  # sempre calcolata: stesso tempo
        if app.demo or (ok_u and ok_p):
            app.limite.ok(ip)
            giorni = int(app.cfg.leggi()["generale"]["sessione_giorni"])
            tok = app.sessioni.crea(giorni)
            ck = cookie_sessione(tok, giorni, e_https(self))
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
                          "info.nt.installato": "1759245643", "info.nt.modo_termico": "schedule",
                          "info.nt.programma": '{"nome": "Casa", "fasce": [[0, 20.0]]}'})
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
