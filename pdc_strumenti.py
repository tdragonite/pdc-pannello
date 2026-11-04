#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdc_strumenti.py - strumenti di appoggio per il progetto "pannello pompa di calore".

Fa queste cose, tutte con la sola libreria standard di Python:

  st-devices   elenca i dispositivi SmartThings e trova la pompa di calore
  st-oauth     procedura guidata per ottenere un token OAuth (con rinnovo automatico)
  st-dump      salva lo stato COMPLETO della pdc in file JSON (da mandare a Claude)
  nt-dump      salva i dati dello Smarther letti dalle API Netatmo (idem)
  st-prova-refresh  esperimento: il comando 'refresh' anticipa i dati del ciclo? (pannello fermo)
  rip-diag     diagnostica della «ripartenza prevista» dello Smarther: perche' c'e' o non c'e', quali pause usa
  nt-webhook-prova  prova i webhook Netatmo: registra un indirizzo temporaneo, ascolta gli eventi dello Smarther e li confronta con homestatus (pannello anche acceso)
  sbrinamenti  elenca gli sbrinamenti trovati nel database, uno per uno, con i cambi di stato grezzi (per controllare il conteggio)

Usa un unico config.json nella stessa cartella dello script (lo stesso che
usera' poi il pannello vero). I token rinnovati vengono riscritti li' dentro.

Esempi:
    python pdc_strumenti.py st-devices
    python pdc_strumenti.py st-dump
    python pdc_strumenti.py nt-dump
    python pdc_strumenti.py rip-diag
    python pdc_strumenti.py nt-webhook-prova --minuti 30
    python pdc_strumenti.py nt-webhook-prova --metodo nuovo --minuti 20
    python pdc_strumenti.py sbrinamenti --giorni 28
"""

import argparse
import base64
from datetime import datetime
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"

ST_API = "https://api.smartthings.com/v1"
ST_BASE = "https://api.smartthings.com"
NT_BASE = "https://api.netatmo.com"
REDIRECT_URI = "https://httpbin.org/get"

TEMPLATE = {
    "_nota": "File di configurazione: modificalo con il Blocco note. Non condividerlo mai con nessuno (contiene password e token).",
    "smartthings": {
        "device_id": "",
        "pat": "",
        "oauth": {
            "client_id": "",
            "client_secret": "",
            "access_token": "",
            "refresh_token": "",
            "expires_at": 0,
        },
    },
    "netatmo": {
        "client_id": "",
        "client_secret": "",
        "access_token": "",
        "refresh_token": "",
        "expires_at": 0,
    },
    "telegram": {"bot_token": "", "chat_id": ""},
}

# chiavi che non voglio nei dump (dati personali o inutili per l'analisi)
REDACT_KEYS = {
    "coordinates", "altitude", "city", "country", "zip", "address", "street",
    "email", "mail", "pseudo", "user", "lat", "lon", "latitude", "longitude",
    "ownerId", "locationId", "roomId", "installedAppId",
}
MAC_RE = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")


# --------------------------------------------------------------------------
# utilita'
# --------------------------------------------------------------------------

def say(*a):
    print(*a, flush=True)


def die(msg):
    print("\nERRORE: " + msg, file=sys.stderr, flush=True)
    sys.exit(1)


def merge(base, extra):
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            out[k] = merge(base[k], v)
        else:
            out[k] = v
    return out


def save_cfg(cfg):
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def load_cfg():
    template = json.loads(json.dumps(TEMPLATE))
    if not CONFIG_PATH.exists():
        save_cfg(template)
        say(f"Ho creato il file {CONFIG_PATH.name} (vuoto) in:\n  {CONFIG_PATH.parent}")
        say("Aprilo con il Blocco note, compila i campi che ti servono, poi rilancia lo stesso comando.")
        sys.exit(0)
    try:
        user = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        die(f"{CONFIG_PATH.name} non e' un JSON valido ({e}). Controlla virgole e virgolette.")
    return merge(template, user)


def http(method, url, headers=None, data=None, timeout=30):
    """Restituisce (codice_http, json). Gli errori HTTP non sollevano eccezioni."""
    body = urlencode(data).encode() if isinstance(data, dict) else data
    req = Request(url, data=body, method=method, headers=headers or {})
    try:
        with urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            code = r.status
    except HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        code = e.code
    except URLError as e:
        die(f"Errore di rete: {e.reason}")
    try:
        return code, (json.loads(raw) if raw.strip() else {})
    except ValueError:
        return code, {"raw": raw[:500]}


def basic(client_id, client_secret):
    return "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


def sanitize(obj, macs=None):
    """Toglie dati personali e sostituisce i MAC address con nomi neutri."""
    macs = {} if macs is None else macs
    if isinstance(obj, dict):
        return {k: ("<omesso>" if k in REDACT_KEYS else sanitize(v, macs)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v, macs) for v in obj]
    if isinstance(obj, str) and MAC_RE.match(obj):
        return macs.setdefault(obj.lower(), f"mac-{len(macs) + 1}")
    return obj


def write_dump(name, obj):
    cartella = HERE / "diagnostica"      # i file da mandare a Claude stanno in una cartella a parte, non in mezzo al resto
    cartella.mkdir(exist_ok=True)
    path = cartella / name
    path.write_text(json.dumps(sanitize(obj), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    say(f"  salvato: diagnostica/{path.name}  ({path.stat().st_size // 1024 + 1} KB)")
    return path


# --------------------------------------------------------------------------
# SmartThings
# --------------------------------------------------------------------------

def st_store_tokens(cfg, js):
    oa = cfg["smartthings"]["oauth"]
    oa["access_token"] = js["access_token"]
    if js.get("refresh_token"):
        oa["refresh_token"] = js["refresh_token"]  # e' monouso: il nuovo sostituisce il vecchio
    oa["expires_at"] = int(time.time()) + int(js.get("expires_in", 86400))
    save_cfg(cfg)  # subito, prima di fare altro: se si perde il nuovo refresh token si ricomincia


def st_access_token(cfg):
    st = cfg["smartthings"]
    oa = st["oauth"]
    if oa.get("refresh_token") and oa.get("client_id"):
        if oa.get("access_token") and time.time() < oa.get("expires_at", 0) - 300:
            return oa["access_token"]
        code, js = http(
            "POST", f"{ST_BASE}/oauth/token",
            {"Authorization": basic(oa["client_id"], oa["client_secret"]),
             "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            {"grant_type": "refresh_token", "client_id": oa["client_id"], "refresh_token": oa["refresh_token"]},
        )
        if code != 200 or "access_token" not in js:
            die(f"SmartThings: rinnovo del token OAuth fallito (HTTP {code}): {js}\n"
                "Se il refresh token e' scaduto o gia' usato rilancia:  python pdc_strumenti.py st-oauth")
        st_store_tokens(cfg, js)
        return js["access_token"]
    if st.get("pat"):
        return st["pat"]
    die("SmartThings: nessun token. Metti il PAT in config.json (smartthings -> pat) "
        "oppure lancia:  python pdc_strumenti.py st-oauth")


def st_get(cfg, path):
    token = st_access_token(cfg)
    code, js = http("GET", ST_API + path, {"Authorization": f"Bearer {token}", "Accept": "application/json"})
    if code == 401:
        die("SmartThings: 401 non autorizzato. Il token e' scaduto o sbagliato "
            "(i token personali PAT durano solo 24 ore: generane uno nuovo).")
    if code == 403:
        die("SmartThings: 403 vietato. Al token mancano i permessi (devono esserci almeno 'Devices: List, See').")
    if code == 429:
        die("SmartThings: 429 troppe richieste. Aspetta un minuto e riprova.")
    if code >= 400:
        die(f"SmartThings: errore HTTP {code}: {js}")
    return js


def find_ehs(cfg):
    items = st_get(cfg, "/devices").get("items", [])
    ehs = []
    for d in items:
        caps = {c.get("id", "") for comp in d.get("components", []) for c in comp.get("capabilities", [])}
        if any(c.startswith("samsungce.ehs") for c in caps):
            ehs.append(d)
    return items, ehs


def cmd_st_devices(cfg, args):
    items, ehs = find_ehs(cfg)
    say(f"\nDispositivi trovati: {len(items)}")
    for d in items:
        mark = "  <-- pompa di calore Samsung EHS" if d in ehs else ""
        say(f"  {d.get('deviceId')}   {d.get('label') or d.get('name')}{mark}")
    if len(ehs) == 1 and not cfg["smartthings"].get("device_id"):
        cfg["smartthings"]["device_id"] = ehs[0]["deviceId"]
        save_cfg(cfg)
        say(f"\nHo salvato il device_id della pdc in {CONFIG_PATH.name}.")
    elif not ehs:
        say("\nNessuna pdc riconosciuta automaticamente: copia a mano il deviceId giusto in config.json.")


def cmd_st_dump(cfg, args):
    device_id = args.device or cfg["smartthings"].get("device_id")
    if not device_id:
        _, ehs = find_ehs(cfg)
        if len(ehs) != 1:
            die("Non riesco a capire quale sia la pdc: lancia 'st-devices' oppure usa --device <id>.")
        device_id = ehs[0]["deviceId"]
    say(f"\nLeggo lo stato completo del dispositivo {device_id} ...")
    status = st_get(cfg, f"/devices/{device_id}/status")
    device = st_get(cfg, f"/devices/{device_id}")
    say("")
    write_dump("dump_smartthings_status.json", status)
    write_dump("dump_smartthings_device.json", device)
    comps = status.get("components", {})
    say("\nRiepilogo di quello che espone la tua pdc:")
    for comp, caps in comps.items():
        n = sum(len(a) for a in caps.values())
        say(f"  componente '{comp}': {len(caps)} capability, {n} valori")
    say("\nFatto. Allega a Claude questi due file (NON config.json):")
    say("  dump_smartthings_status.json\n  dump_smartthings_device.json")


def _appiattisci(status):
    """{percorso: (valore_testo, timestamp_iso)} per ogni attributo dello stato SmartThings."""
    out = {}
    for comp, caps in (status.get("components") or {}).items():
        for cap, attrs in caps.items():
            for att, info in attrs.items():
                if isinstance(info, dict) and "value" in info:
                    out[f"{comp}/{cap}/{att}"] = (json.dumps(info.get("value"), ensure_ascii=False, sort_keys=True),
                                                  info.get("timestamp") or "")
    return out


def _ts_locale(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
    except Exception:
        return iso or "-"


def _campioni_ciclo(piatto):
    """Timestamp dei campioni del ciclo (indoor/outdoor) presenti adesso."""
    out = {}
    for tipo in ("indoor", "outdoor"):
        v = piatto.get(f"main/samsungce.ehsCycleData/{tipo}")
        try:
            out[tipo] = sorted(x["timestamp"] for x in json.loads(v[0]))
        except Exception:
            out[tipo] = []
    return out


def cmd_st_prova_refresh(cfg, args):
    """Esperimento: il comando 'refresh' fa arrivare prima i dati del ciclo? Da lanciare col pannello FERMO.
    Fa tanti giri in ordine casuale: alcuni con il refresh, altri SENZA (controllo). Se i dati nuovi arrivano
    quasi sempre e subito solo nei giri col refresh, e' il refresh; se arrivano uguale anche senza, e' il caso."""
    import random
    device_id = args.device or cfg["smartthings"].get("device_id")
    if not device_id:
        _, ehs = find_ehs(cfg)
        if len(ehs) != 1:
            die("Non riesco a capire quale sia la pdc: lancia 'st-devices' oppure usa --device <id>.")
        device_id = ehs[0]["deviceId"]
    giri = max(2, args.giri + args.giri % 2)           # numero pari: meta' con refresh, meta' senza
    tipi = ["refresh"] * (giri // 2) + ["controllo"] * (giri // 2)
    random.shuffle(tipi)
    say(f"\nATTENZIONE: il pannello deve essere FERMO (usa lo stesso token).")
    say(f"Faro' {giri} giri da {args.finestra} s ciascuno (circa {giri * (args.finestra + args.pausa) // 60} minuti in tutto, pause comprese), "
        f"in ordine casuale: meta' con il refresh, meta' senza.")
    say("L'ordine e' mescolato a caso apposta, cosi' non si confonde il refresh con un arrivo casuale.\n")

    def campioni(stato):
        out = set()
        for tipo, lista in _campioni_ciclo(_appiattisci(stato)).items():
            out.update((tipo, t) for t in lista)
        return out

    visti = campioni(st_get(cfg, f"/devices/{device_id}/status"))
    risultati, log = [], {"avvio": datetime.now().isoformat(timespec="seconds"), "giri": []}
    for n, tipo in enumerate(tipi, 1):
        codice = ""
        t_inizio = time.time()
        if tipo == "refresh":
            token = st_access_token(cfg)
            cod, js = http("POST", f"{ST_API}/devices/{device_id}/commands",
                           {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                            "Accept": "application/json"},
                           json.dumps({"commands": [{"component": "main", "capability": "refresh",
                                                     "command": "refresh"}]}).encode())
            codice = f"HTTP {cod}"
        arrivo, ritardo = None, None
        while time.time() - t_inizio < args.finestra:
            time.sleep(args.ogni)
            try:
                nuovi = campioni(st_get(cfg, f"/devices/{device_id}/status")) - visti
            except SystemExit:
                continue
            if nuovi and arrivo is None:
                arrivo = int(time.time() - t_inizio)
                ritardo = max((time.time() - datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()) / 60
                              for _, t in nuovi)
            visti |= nuovi
        # pausa "di decantazione": i dati in ritardo di questo giro non devono finire nel giro dopo
        t_pausa = time.time()
        tardivi = 0
        while time.time() - t_pausa < args.pausa and n < giri:
            time.sleep(args.ogni)
            try:
                nuovi = campioni(st_get(cfg, f"/devices/{device_id}/status")) - visti
            except SystemExit:
                continue
            tardivi += len(nuovi)
            visti |= nuovi
        r = {"giro": n, "tipo": tipo, "comando": codice, "dati_nuovi_dopo_s": arrivo, "arrivati_in_pausa": tardivi,
             "ritardo_campione_min": round(ritardo, 1) if ritardo is not None else None}
        log["giri"].append(r)
        risultati.append(r)
        esito = f"dati nuovi dopo {arrivo} s (campione vecchio di {ritardo:.1f} min)" if arrivo is not None else "nessun dato nuovo"
        extra = f"  (+{tardivi} arrivati dopo, nella pausa)" if tardivi else ""
        say(f"  giro {n:>2}/{giri}  [{'REFRESH  ' if tipo == 'refresh' else 'controllo'}] {codice:9} -> {esito}{extra}")

    say("\n--- Riepilogo ---")
    for tipo, nome in (("refresh", "con refresh"), ("controllo", "senza (controllo)")):
        gruppo = [r for r in risultati if r["tipo"] == tipo]
        ok = [r for r in gruppo if r["dati_nuovi_dopo_s"] is not None]
        tempi = sorted(r["dati_nuovi_dopo_s"] for r in ok)
        med = f", mediana {tempi[len(tempi) // 2]} s dopo l'inizio del giro" if tempi else ""
        say(f"  {nome:18}: dati nuovi in {len(ok)} giri su {len(gruppo)}{med}")
    rf = [r for r in risultati if r["tipo"] == "refresh"]
    ct = [r for r in risultati if r["tipo"] == "controllo"]
    pf = sum(r["dati_nuovi_dopo_s"] is not None for r in rf) / max(1, len(rf))
    pc = sum(r["dati_nuovi_dopo_s"] is not None for r in ct) / max(1, len(ct))
    log["riepilogo"] = {"con_refresh": pf, "controllo": pc}
    say("")
    if pf >= pc + 0.4 and pf >= 0.7:
        say("Sembra che il refresh FUNZIONI: i dati nuovi arrivano molto piu' spesso quando lo mando.")
    elif pf <= pc + 0.15:
        say("Il refresh NON sembra servire: i dati arrivano allo stesso modo anche senza.")
    else:
        say("Risultato dubbio: la differenza e' piccola. Rilancia con piu' giri (--giri 16) prima di decidere.")
    say("(Con pochi giri il caso conta ancora: fidati di una differenza netta, non di una piccola.)")
    write_dump("prova_refresh.json", log)


def extract_code(text):
    text = text.strip()
    # pagina httpbin incollata per intero:  "code": "XXXX"
    m = re.search(r'"code"\s*:\s*"([^"]+)"', text)
    if m:
        return m.group(1)
    # indirizzo di ritorno:  ...?code=XXXX&...
    m = re.search(r"[?&]code=([^&\s\"']+)", text)
    if m:
        return m.group(1)
    return text.strip('"\' ')


def cmd_st_oauth(cfg, args):
    oa = cfg["smartthings"]["oauth"]
    say("\n=== Autorizzazione OAuth SmartThings ===")
    say("Prima devi aver creato l'app OAuth con la SmartThings CLI (vedi la guida, punto 2).")
    cid = oa.get("client_id") or input("Client ID: ").strip()
    secret = oa.get("client_secret") or input("Client Secret: ").strip()
    if not cid or not secret:
        die("Servono Client ID e Client Secret.")
    url = f"{ST_BASE}/oauth/authorize?" + urlencode({
        "client_id": cid, "response_type": "code",
        "redirect_uri": REDIRECT_URI, "scope": "r:devices:* x:devices:*"})
    say("\n1) Apri questo indirizzo nel browser (meglio Chrome), accedi con l'account Samsung e consenti:\n")
    say(url)
    say("\n2) Ti ritrovi su una pagina bianca con del testo (httpbin.org). Cerca la riga  \"code\": \"XXXXXX\".")
    say("   Chiedo lettura (r:devices:*) e comando (x:devices:*, serve per accendere/spegnere dal pannello).")
    say("   Se compare un errore sui permessi, l'app OAuth non ha ancora lo scope di comando: vedi la guida del pannello,")
    say("   paragrafo 'Accendere e spegnere dal pannello'. Per la sola lettura puoi togliere ' x:devices:*' dall'indirizzo.")
    raw = input("\n3) Incolla qui il codice (o tutto il testo/indirizzo della pagina): ")
    code_value = extract_code(raw)
    if not code_value:
        die("Non ho trovato nessun codice.")
    code, js = http(
        "POST", f"{ST_BASE}/oauth/token",
        {"Authorization": basic(cid, secret), "Content-Type": "application/x-www-form-urlencoded",
         "Accept": "application/json"},
        {"grant_type": "authorization_code", "code": code_value,
         "redirect_uri": REDIRECT_URI, "client_id": cid},
    )
    if code != 200 or "access_token" not in js:
        die(f"Scambio del codice fallito (HTTP {code}): {js}\n"
            "Il codice dura pochissimo e si usa una volta sola: rifai il passo 1 e incolla subito.")
    oa["client_id"], oa["client_secret"] = cid, secret
    st_store_tokens(cfg, js)
    say("\nFatto! Token OAuth salvati in config.json.")
    say(f"  scadenza access token: tra circa {int(js.get('expires_in', 0)) // 3600} ore (si rinnova da solo)")
    say("  Il refresh token e' monouso: non lanciare due programmi con lo stesso config.json contemporaneamente.")


# --------------------------------------------------------------------------
# Diagnostica degli algoritmi del pannello (legge il database, non scrive niente)
# --------------------------------------------------------------------------

def _trova_db(esplicito=None):
    """Il database del pannello: --db, oppure quello indicato in config.json, oppure i posti soliti."""
    if esplicito:
        p = Path(esplicito).expanduser()
        return p if p.exists() else die(f"Non trovo il database: {p}")
    nome = "dati/pdc.db"
    try:
        nome = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")).get("generale", {}).get("database") or nome
    except (OSError, ValueError):
        pass
    for c in (HERE / nome, HERE / "dati" / "pdc.db", HERE / "pdc.db"):
        if c.exists():
            return c
    return die("Non trovo il database del pannello. Lancia il comando dalla cartella del pannello, oppure indicalo con:\n"
               "  python pdc_strumenti.py rip-diag --db C:\\pdc\\dati\\pdc.db")


class _DbSolaLettura:
    """Quanto basta del database del pannello per le sue funzioni di calcolo: solo _c(), aperto in sola lettura."""

    def __init__(self, path):
        self.path = Path(path)

    def _c(self):
        import sqlite3
        return sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)


def _db_leggibile(path):
    """Apre il database in sola lettura (anche con il pannello acceso); se non si riesce, lavora su una copia temporanea."""
    import shutil
    import sqlite3
    import tempfile
    db = _DbSolaLettura(path)
    try:
        with db._c() as c:
            c.execute("SELECT 1 FROM misure LIMIT 1").fetchall()
        return db, None
    except sqlite3.Error:
        tmp = Path(tempfile.mkdtemp(prefix="pdc_diag_"))
        for suff in ("", "-wal", "-shm"):
            src = Path(str(path) + suff)
            if src.exists():
                shutil.copy2(src, tmp / (Path(path).name + suff))
        say("  (il database e' in uso: lavoro su una copia temporanea)")
        return _DbSolaLettura(tmp / Path(path).name), tmp


def _fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M") if ts else "—"


def _fmt_gg(s):
    return f"{s / 86400:.1f} giorni" if s >= 86400 else f"{s / 3600:.1f} ore"


def cmd_rip_diag(cfg, args):
    """Perche' la «ripartenza prevista» c'e' o non c'e': usa le STESSE funzioni del pannello (pdc_pannello.py, stessa cartella)."""
    sys.path.insert(0, str(HERE))
    try:
        import pdc_pannello as pp
    except Exception as e:                                   # noqa: BLE001
        die(f"Non riesco a caricare pdc_pannello.py (deve stare nella stessa cartella di questo script): {e}")
    if "diag" not in getattr(pp.stima_ripartenza, "__code__", None).co_varnames:
        die("Questo pdc_pannello.py e' troppo vecchio per la diagnostica: sostituiscilo con l'ultimo (versione 0.19 piu' recente).")
    db_path = _trova_db(args.db)
    db, tmp = _db_leggibile(db_path)
    ora = int(time.time())
    say(f"Diagnostica ripartenza prevista - {datetime.fromtimestamp(ora):%d/%m/%Y %H:%M}")
    say(f"Database: {db_path}  ({db_path.stat().st_size // (1024 * 1024)} MB)")

    with db._c() as c:
        ult = {r[0]: r for r in c.execute("SELECT serie, ts, num, txt FROM ultimo").fetchall()}
        primo = c.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM misure WHERE serie='casa.temperatura'").fetchone()
    num = lambda s: ult[s][2] if s in ult else None            # noqa: E731
    tsx = lambda s: ult[s][1] if s in ult else None            # noqa: E731
    t_casa, ts_casa, esterna, target = num("casa.temperatura"), tsx("casa.temperatura"), num("ciclo.esterna"), num("casa.setpoint")
    richiesta = num("casa.richiesta_calore") == 1.0

    say("\n1) Dati dello Smarther nel database")
    if primo and primo[0]:
        say(f"   temperatura casa: {primo[2]} misure, dal {_fmt_ts(primo[0])} ({_fmt_gg(ora - primo[0])} di storico)")
    else:
        say("   nessuna misura di temperatura casa: lo Smarther non e' mai stato letto.")
    say(f"   adesso: casa {t_casa if t_casa is not None else '—'} °C (dato delle {_fmt_ts(ts_casa)}), target {target if target is not None else '—'}, "
        f"esterna {esterna if esterna is not None else '—'} °C, richiesta calore: {'SI' if richiesta else 'no'}")

    diag = {}
    try:
        eta = pp.stima_ripartenza(type("A", (), {"db": db})(), ora, t_casa, ts_casa, richiesta, esterna, target, diag=diag)
    except Exception as e:                                   # noqa: BLE001
        die(f"Errore nel calcolo: {e}")
    pause = diag.get("pause")
    if pause is not None:
        buone = [p for p in pause if p["valida"]]
        say(f"\n2) Pause trovate nello storico (ultimi {pp.RIP_GIORNI} giorni): {len(pause)}, valide {len(buone)} "
            f"(ne servono {pp.RIP_MIN_CAMPIONI})")
        if diag.get("celle"):
            ce = diag["celle"]
            say(f"   celle da 5 minuti con dati: temperatura {ce['temperatura']}, richiesta {ce['richiesta']}, esterna {ce['esterna']}, target {ce['target']}")
        for p in pause[-int(args.ultime):]:
            if p["valida"]:
                say(f"   {_fmt_ts(p['da'])} -> {_fmt_ts(p['a'])}   OK   k={p['k']:.3f}/h  sovraelongazione {p['sovra']:.2f} °C")
            else:
                say(f"   {_fmt_ts(p['da'])} -> {_fmt_ts(p['a'])}   scartata: {p['motivo']}")
        if len(pause) > int(args.ultime):
            say(f"   (le ultime {args.ultime} su {len(pause)}; con --ultime N ne vedi di piu')")
        if not pause:
            say("   nessuna: servono cicli di riscaldamento di almeno 15 minuti seguiti da una pausa di almeno un'ora e dalla ripartenza.")
    fit = diag.get("fit")
    if fit:
        say("\n3) Parametri stimati")
        say(f"   k (raffreddamento): {fit['k']:.3f} /ora   sovraelongazione: {fit['sovra']:.2f} °C   coda: {fit['coda_min']:.0f} min")
        say(f"   soglia di ripartenza: {fit['soglia']:.2f} °C" + (f" (target - {fit['scarto']:.2f})" if fit.get("scarto") is not None else ""))
        say(f"   basati su {fit['n']} pause, peso complessivo {diag.get('peso', 0):.1f}")
    elif pause is not None:
        fm = [m for m in diag.get("motivi", []) if m.startswith(("storico", "pause valide"))]
        say("\n3) Parametri stimati: non calcolabili (" + (fm[0] if fm else "storico insufficiente") + ")")
    say("\n4) Stima di adesso")
    if eta:
        d = diag.get("eta_dettagli", {})
        say(f"   ripartenza prevista: {datetime.fromtimestamp(eta):%H:%M} (tra {_fmt_gg(eta - ora)})")
        if d.get("soglia") is not None:
            say(f"   (casa di riferimento {d['t_rif']:.2f} °C, soglia {d['soglia']:.2f} °C)")
    else:
        motivi = diag.get("motivi") or ["motivo sconosciuto"]
        say("   nessuna stima, perche':" if len(motivi) > 1 else f"   nessuna stima: {motivi[0]}")
        if len(motivi) > 1:
            for m in motivi:
                say(f"    - {m}")
    if args.salva:
        write_dump("rip_diag.json", {"ora": ora, "db": db_path.name, "t_casa": t_casa, "ts_casa": ts_casa, "esterna": esterna, "target": target,
                                     "richiesta": richiesta, "stima": eta, "diag": diag})
    if tmp:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_sbrinamenti(cfg, args):
    """Sbrinamenti nel database: i cambi di stato grezzi e l'elenco che ne ricava il pannello (stessa funzione del pannello)."""
    sys.path.insert(0, str(HERE))
    try:
        import pdc_pannello as pp
    except Exception as e:                                   # noqa: BLE001
        die(f"Non riesco a caricare pdc_pannello.py (deve stare nella stessa cartella di questo script): {e}")
    if not hasattr(pp, "elenco_sbrinamenti"):
        die("Questo pdc_pannello.py e' troppo vecchio: sostituiscilo con l'ultimo (versione 0.19 piu' recente).")
    db_path = _trova_db(args.db)
    db, tmp = _db_leggibile(db_path)
    ora = int(time.time())
    da = ora - int(args.giorni) * 86400
    say(f"Sbrinamenti - ultimi {args.giorni} giorni - {datetime.fromtimestamp(ora):%d/%m/%Y %H:%M}")
    say(f"Database: {db_path}  ({db_path.stat().st_size // (1024 * 1024)} MB)")
    with db._c() as c:
        cambi = c.execute("SELECT ts, valore FROM stati WHERE serie='pdc.sbrinamento' AND ts>? AND ts<=? ORDER BY ts", (da, ora)).fetchall()
        prima = c.execute("SELECT ts, valore FROM stati WHERE serie='pdc.sbrinamento' AND ts<=? ORDER BY ts DESC LIMIT 1", (da,)).fetchone()
        primo = c.execute("SELECT MIN(ts) FROM stati WHERE serie='pdc.sbrinamento'").fetchone()[0]
        ult = c.execute("SELECT ts, txt FROM ultimo WHERE serie='pdc.sbrinamento'").fetchone()
        wh = c.execute("SELECT COUNT(*), MIN(ts) FROM misure WHERE serie='en.totale_wh' AND ts>?", (da,)).fetchone()
    say("\n1) Cambi di stato grezzi (serie pdc.sbrinamento)")
    say(f"   stato prima del periodo: {prima[1] if prima else '— (nessun dato precedente)'}   primo dato in assoluto: {_fmt_ts(primo)}")
    say(f"   stato adesso: {ult[1] if ult else '—'} (letto alle {_fmt_ts(ult[0]) if ult else '—'})")
    prec = prima[0] if prima else None
    for ts, v in cambi:
        say(f"   {_fmt_ts(ts)}  ->  {v:<4}" + (f"  (dopo {round((ts - prec) / 60)} min)" if prec else ""))
        prec = ts
    if not cambi:
        say("   nessun cambio nel periodo.")
    r = pp.elenco_sbrinamenti(db, da, ora, ora)
    say(f"\n2) Sbrinamenti ricavati dal pannello: {r['n']}")
    for x in r["elenco"][::-1]:
        fine = "IN CORSO" if x["incorso"] else _fmt_ts(x["fine"])
        say(f"   {_fmt_ts(x['ts'])} -> {fine:<12} {x['min']:>3} min   "
            f"{('~' + format(x['kwh'], '.2f') + ' kWh') if x['kwh'] is not None else 'kWh n.d.':<12} "
            f"esterna {x['esterna'] if x['esterna'] is not None else '—'} °C")
    say(f"   totale {r['kwh'] if r['kwh'] is not None else '—'} kWh, durata media {r['media_min'] if r['media_min'] is not None else '—'} min, "
        f"esterna media {r['esterna'] if r['esterna'] is not None else '—'} °C")
    say("\n3) Cose da controllare")
    dubbi = []
    if len(r["elenco"]) >= pp.SBR_MAX_ELENCO:
        dubbi.append(f"l'elenco e' limitato agli ultimi {pp.SBR_MAX_ELENCO}: il conteggio resta giusto")
    brevi = [x for x in r["elenco"] if not x["incorso"] and x["min"] < 2]
    lunghi = [x for x in r["elenco"] if not x["incorso"] and x["min"] > 20]
    if brevi:
        dubbi.append(f"{len(brevi)} sbrinamenti sotto i 2 minuti: durano davvero cosi' poco o sono un dato isolato?")
    if lunghi:
        dubbi.append(f"{len(lunghi)} sbrinamenti sopra i 20 minuti: insolito, controlla le righe sopra")
    vuoti = [x for x in r["elenco"] if x["kwh"] is None]
    if vuoti:
        dubbi.append(f"{len(vuoti)} senza kWh: manca il contatore dell'energia in quel momento")
    zero = [x for x in r["elenco"] if x["kwh"] == 0]
    if zero:
        dubbi.append(f"{len(zero)} con 0,00 kWh: il contatore e' a scatti, uno sbrinamento corto puo' cadere tra due scatti (e' normale)")
    if wh and wh[0]:
        dubbi.append(f"contatore dell'energia: {wh[0]} letture nel periodo")
    if cambi and not any(v == "on" for _, v in cambi):
        dubbi.append("ci sono cambi di stato ma nessuno e' 'on': guarda i valori della colonna sopra")
    say("\n".join("   - " + d for d in dubbi) if dubbi else "   niente di strano.")
    if args.salva:
        write_dump("sbrinamenti.json", {"ora": ora, "db": db_path.name, "giorni": args.giorni, "cambi": cambi, "elenco": r})
    if tmp:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# Netatmo (Smarther)
# --------------------------------------------------------------------------

def nt_access_token(cfg, force=False):
    nt = cfg["netatmo"]
    for k in ("client_id", "client_secret", "refresh_token"):
        if not nt.get(k):
            die(f"Netatmo: manca '{k}' in config.json (sezione netatmo).")
    if not force and nt.get("access_token") and time.time() < nt.get("expires_at", 0) - 120:
        return nt["access_token"]
    code, js = http(
        "POST", f"{NT_BASE}/oauth2/token",
        {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        {"grant_type": "refresh_token", "refresh_token": nt["refresh_token"],
         "client_id": nt["client_id"], "client_secret": nt["client_secret"]},
    )
    if code != 200 or "access_token" not in js:
        die(f"Netatmo: rinnovo del token fallito (HTTP {code}): {js}\n"
            "Se hai rigenerato i token nel portale, copia in config.json i NUOVI (i vecchi non valgono piu').")
    nt["access_token"] = js["access_token"]
    if js.get("refresh_token"):
        nt["refresh_token"] = js["refresh_token"]
    nt["expires_at"] = int(time.time()) + int(js.get("expires_in", 10800))
    save_cfg(cfg)
    scope = js.get("scope")
    if scope:
        say("Scope concessi al token Netatmo: " + (", ".join(scope) if isinstance(scope, list) else str(scope)))
    return nt["access_token"]


def nt_get(cfg, path, params=None):
    url = NT_BASE + path + ("?" + urlencode(params) if params else "")
    for attempt in (1, 2):
        token = nt_access_token(cfg, force=(attempt == 2))
        code, js = http("GET", url, {"Authorization": f"Bearer {token}", "Accept": "application/json"})
        if code in (401, 403) and attempt == 1:
            continue
        break
    if code >= 400 or (isinstance(js, dict) and "error" in js):
        err = js.get("error") if isinstance(js, dict) else js
        die(f"Netatmo {path}: HTTP {code}: {err}\n"
            "Possibili cause: scope mancante nel token (rigeneralo dal portale selezionando quelli dello Smarther/BTicino), "
            "oppure token scaduto.")
    return js


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, obj


def cmd_nt_dump(cfg, args):
    say("\nLeggo le case Netatmo ...")
    homesdata = nt_get(cfg, "/api/homesdata")
    homes = homesdata.get("body", {}).get("homes", [])
    say("")
    write_dump("dump_netatmo_homesdata.json", homesdata)
    if not homes:
        die("Nessuna casa trovata: il token probabilmente non ha lo scope dello Smarther.")
    for i, home in enumerate(homes, 1):
        types = sorted({m.get("type", "?") for m in home.get("modules", [])})
        say(f"\nCasa {i}: {len(home.get('rooms', []))} stanze, tipi di modulo: {', '.join(types) or 'nessuno'}")
        status = nt_get(cfg, "/api/homestatus", {"home_id": home["id"]})
        write_dump(f"dump_netatmo_homestatus_{i}.json", status)
        found = [(p, v) for p, v in walk(sanitize(status.get("body", {})))
                 if re.search(r"hum|temp|heat|boiler|therm|setpoint", p, re.I)]
        if found:
            say("  Valori interessanti trovati:")
            for p, v in found[:25]:
                say(f"    {p} = {v}")
        humidity = any(re.search(r"hum", p, re.I) for p, _ in found)
        say("  Umidita': " + ("TROVATA nei dati." if humidity else "NON trovata nei dati di questa casa."))
    say("\nFatto. Allega a Claude questi file (controllali prima con il Blocco note, NON config.json):")
    say("  dump_netatmo_homesdata.json\n  dump_netatmo_homestatus_*.json")



# --------------------------------------------------------------------------
# Prova dei webhook Netatmo (sola osservazione: il pannello non viene toccato)
# --------------------------------------------------------------------------

WH_SEGRETI = ("user_id", "home_id", "home_name", "url")    # tolti dal file da mandare (i MAC li neutralizza sanitize)


def wh_firma_ok(corpo, segreto, intestazione):
    """HMAC-SHA256 del corpo grezzo con il client_secret, in esadecimale minuscolo (come da documentazione Netatmo)."""
    import hashlib
    import hmac
    if not intestazione:
        return None
    atteso = hmac.new(segreto.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(atteso, intestazione.strip().lower())


def wh_ricevitore(porta, segreto, eventi, lock):
    """Piccolo server HTTP: registra ogni POST (con esito della firma) e risponde sempre 200 entro pochi ms."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_POST(self):
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 262144)
            except ValueError:
                n = 0
            corpo = self.rfile.read(n) if n else b""
            firma = wh_firma_ok(corpo, segreto, self.headers.get("X-Netatmo-secret"))
            try:
                js = json.loads(corpo.decode("utf-8", "replace")) if corpo.strip() else {}
            except ValueError:
                js = {"raw": corpo[:300].decode("utf-8", "replace")}
            with lock:
                eventi.append({"arrivo": time.time(), "firma": firma, "percorso": self.path, "corpo": js})
            risp = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(risp)))
            self.end_headers()
            self.wfile.write(risp)

        do_GET = do_POST

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", porta), H)
    except OSError as e:     # su Windows alcune porte sono riservate o occupate (WinError 10013/10048): ne scelgo una libera
        if not porta:
            raise
        say(f"  (la porta {porta} non e' utilizzabile: {e.strerror or e}; ne scelgo una libera)")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def wh_trova_cloudflared(cfg):
    import shutil
    c = (cfg.get("cloudflare") or {}).get("percorso_cloudflared") or ""
    if c:
        p = Path(c)
        return p if p.is_absolute() else HERE / p
    nome = "cloudflared.exe" if os.name == "nt" else "cloudflared"
    loc = HERE / "cloudflared" / nome
    if loc.exists():
        return loc
    t = shutil.which("cloudflared")
    return Path(t) if t else None


def wh_tunnel(exe, porta, attesa=60):
    """Tunnel temporaneo di Cloudflare (nessun account, indirizzo casuale *.trycloudflare.com). Restituisce (processo, url)."""
    import subprocess
    import threading
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    pr = subprocess.Popen([str(exe), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{porta}"],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                          creationflags=flags)
    trovato, righe = [], []

    def leggi():
        for riga in pr.stdout:
            righe.append(riga.rstrip())
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", riga)
            if m and not trovato:
                trovato.append(m.group(0))
    threading.Thread(target=leggi, daemon=True).start()
    t0 = time.time()
    while time.time() - t0 < attesa and not trovato and pr.poll() is None:
        time.sleep(0.3)
    if not trovato:
        pr.terminate()
        die("Il tunnel temporaneo di Cloudflare non e' partito (nessun indirizzo trycloudflare.com ricevuto).\n"
            "Ultime righe di cloudflared:\n  " + "\n  ".join(righe[-8:]))
    time.sleep(3)    # il nome impiega qualche secondo a essere raggiungibile
    return pr, trovato[0]


def wh_piatto(obj, prefisso=""):
    return {p: v for p, v in walk(obj, prefisso)}


def wh_token(cfg, durata_s, rinnova):
    """Usa il token del pannello SENZA rinnovarlo (Netatmo cambia il refresh token a ogni rinnovo: farlo mentre il pannello gira lo romperebbe)."""
    nt = cfg["netatmo"]
    resta = float(nt.get("expires_at") or 0) - time.time()
    if rinnova:
        return nt_access_token(cfg, force=True)
    if nt.get("access_token") and resta > durata_s + 300:
        return nt["access_token"]
    die("Il token Netatmo e' in scadenza (o manca), e rinnovarlo da qui mentre il pannello e' acceso lo metterebbe nei guai.\n"
        "  - Aspetta qualche minuto e rilancia: il pannello lo rinnova da solo e il nuovo dura circa 3 ore.\n"
        "  - Oppure FERMA il pannello e rilancia con --rinnova.")


def wh_pulisci(obj):
    if isinstance(obj, dict):
        return {k: ("<omesso>" if k in WH_SEGRETI else wh_pulisci(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [wh_pulisci(v) for v in obj]
    return obj


def wh_leggi(token, path, params=None):
    """GET che NON rinnova mai il token (a differenza di nt_get)."""
    c, js = wh_chiama(token, "GET", path + ("?" + urlencode(params) if params else ""))
    if c >= 400:
        say(f"  (Netatmo {path}: HTTP {c}: {json.dumps(js, ensure_ascii=False)[:200]})")
        return {}
    return js


def wh_chiama(token, metodo, path, dati=None, json_body=None):
    h = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    corpo = None
    if json_body is not None:
        h["Content-Type"] = "application/json"
        corpo = json.dumps(json_body).encode()
        dati = None
    elif dati is not None:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    return http(metodo, NT_BASE + path, h, corpo if corpo is not None else dati)


def wh_confronta(evento_mod, stato_mod):
    """Campi dell'evento uguali / diversi / presenti solo in homestatus."""
    e, s = wh_piatto(evento_mod), wh_piatto(stato_mod)
    uguali = sorted(k for k in e if k in s and e[k] == s[k])
    diversi = sorted(k for k in e if k in s and e[k] != s[k])
    solo_ev = sorted(k for k in e if k not in s)
    solo_st = sorted(k for k in s if k not in e)
    return {"uguali": uguali, "diversi": diversi, "solo_nel_webhook": solo_ev, "solo_in_homestatus": solo_st}


def cmd_nt_webhook_prova(cfg, args):
    import threading
    nt = cfg["netatmo"]
    token = wh_token(cfg, args.minuti * 60, args.rinnova)
    segreto = nt.get("client_secret") or ""
    if not segreto:
        die("Manca netatmo.client_secret in config.json (serve a controllare la firma dei messaggi).")
    eventi, lock = [], threading.Lock()
    rapporto = {"avviato": datetime.now().isoformat(timespec="seconds"), "minuti": args.minuti, "registrazione": None,
                "eventi": [], "confronti": [], "elenco_prima": None, "elenco_dopo": None, "rimozione": None}

    say("\nElenco dei webhook gia' presenti sul tuo account ...")
    c0, js0 = wh_chiama(token, "GET", "/webhooks/v1/")
    rapporto["elenco_prima"] = {"http": c0, "risposta": js0}
    say(f"  HTTP {c0}: {json.dumps(js0, ensure_ascii=False)[:300]}")

    nuovo = args.metodo == "nuovo"
    if nuovo:
        elenco = js0.get("body") if isinstance(js0, dict) else None
        if c0 >= 400 or elenco != []:
            die("Con --metodo nuovo parto solo se l'elenco dei webhook e' certamente vuoto (la cancellazione finale con DELETE /webhooks/v1/ "
                "toglie TUTTI i webhook dell'account). Non lo e' (o non riesco a leggerlo): guarda l'elenco qui sopra.")
    srv = pr = None
    registrato = False
    url = args.url
    try:
        srv = wh_ricevitore(args.porta, segreto, eventi, lock)
        porta = srv.server_address[1]
        if not url:
            exe = wh_trova_cloudflared(cfg)
            if not exe or not Path(exe).exists():
                die("Non trovo cloudflared. Mettilo nella cartella 'cloudflared' accanto a questo script, oppure scrivi il percorso in "
                    "config.json (cloudflare -> percorso_cloudflared), oppure passa un indirizzo pubblico tuo con --url.")
            say(f"\nAvvio il tunnel temporaneo (cloudflared: {exe}) ...")
            pr, url = wh_tunnel(exe, porta)
        say(f"  indirizzo pubblico: {url}")

        if nuovo:
            say("\nRegistro il webhook su Netatmo (POST /webhooks/v1/, servizio nuovo) ...")
            c1, js1 = wh_chiama(token, "POST", "/webhooks/v1/", json_body={"url": url})
            rapporto["registrazione"] = {"http": c1, "risposta": js1, "metodo": "/webhooks/v1/"}
            say(f"  HTTP {c1}: {json.dumps(js1, ensure_ascii=False)[:300]}")
            if c1 >= 400:
                die("Netatmo ha rifiutato la registrazione con il servizio nuovo (vedi sopra). Manda l'output a Claude.")
        else:
            say("\nRegistro il webhook su Netatmo (POST /api/addwebhook) ...")
            c1, js1 = wh_chiama(token, "POST", "/api/addwebhook", {"url": url})
            rapporto["registrazione"] = {"http": c1, "risposta": js1, "metodo": "/api/addwebhook"}
            say(f"  HTTP {c1}: {json.dumps(js1, ensure_ascii=False)[:300]}")
        if not nuovo and c1 >= 400:
            say("  Riprovo con POST /webhooks/v1/ ...")
            c2, js2 = wh_chiama(token, "POST", "/webhooks/v1/", json_body={"url": url})
            rapporto["registrazione"] = {"http": c2, "risposta": js2, "metodo": "/webhooks/v1/", "primo_tentativo": {"http": c1, "risposta": js1}}
            say(f"  HTTP {c2}: {json.dumps(js2, ensure_ascii=False)[:300]}")
            if c2 >= 400:
                die("Netatmo ha rifiutato la registrazione in entrambi i modi (vedi sopra). Il tuo account sviluppatore potrebbe non avere ancora "
                    "i webhook abilitati: scrivi a contact-api@netatmo.com oppure manda questo output a Claude.")
        registrato = True

        say(f"\nAscolto per {args.minuti} minuti. ORA muovi lo Smarther: cambia la temperatura impostata dall'app, accendi e spegni,")
        say("aspetta che parta o si fermi il riscaldamento. Ctrl+C per finire prima.\n")
        home_id = None
        homes = (wh_leggi(token, "/api/homesdata").get("body") or {}).get("homes") or []
        if homes:
            home_id = homes[0]["id"]
        visti = ultimo_conf = n_conf = 0
        fine = time.time() + args.minuti * 60
        try:
            while time.time() < fine:
                time.sleep(1)
                with lock:
                    nuovi = eventi[visti:]
                    visti = len(eventi)
                for ev in nuovi:
                    corpo = ev["corpo"] if isinstance(ev["corpo"], dict) else {}
                    mod = (corpo.get("extra_params") or {}).get("modules") or []
                    tipi = ",".join(sorted({str(m.get("type", "?")) for m in mod if isinstance(m, dict)})) or "-"
                    f = {True: "firma OK", False: "FIRMA NON VALIDA", None: "senza firma"}[ev["firma"]]
                    say(f"  {datetime.fromtimestamp(ev['arrivo']).strftime('%H:%M:%S')}  {corpo.get('push_type', '?')}  moduli: {tipi}  [{f}]")
                    rapporto["eventi"].append(ev)
                    if corpo.get("push_type") == "device_event" and home_id and n_conf < 12 and time.time() - ultimo_conf > 30:
                        ultimo_conf, n_conf = time.time(), n_conf + 1
                        st = (wh_leggi(token, "/api/homestatus", {"home_id": home_id}).get("body") or {}).get("home") or {}
                        by_id = {m.get("id"): m for m in st.get("modules") or [] if isinstance(m, dict)}
                        for m in mod:
                            if isinstance(m, dict) and m.get("id") in by_id:
                                rapporto["confronti"].append({"tipo": m.get("type"), **wh_confronta(m, by_id[m["id"]])})
        except KeyboardInterrupt:
            say("\nInterrotto: faccio pulizia ...")
    finally:
        if registrato:
            say("\nRimuovo il webhook da Netatmo (" + ("DELETE /webhooks/v1/" if nuovo else "POST /api/dropwebhook") + ") ...")
            try:
                c3, js3 = wh_chiama(token, "DELETE", "/webhooks/v1/") if nuovo else wh_chiama(token, "POST", "/api/dropwebhook", {})
                rapporto["rimozione"] = {"http": c3, "risposta": js3}
                say(f"  HTTP {c3}: {json.dumps(js3, ensure_ascii=False)[:300]}")
                c4, js4 = wh_chiama(token, "GET", "/webhooks/v1/")
                rapporto["elenco_dopo"] = {"http": c4, "risposta": js4}
                if url and url in json.dumps(js4):
                    say("  ATTENZIONE: il nostro indirizzo risulta ancora registrato. Rilancia con --elimina per riprovare.")
            except SystemExit:
                say("  Non sono riuscito a rimuoverlo: rilancia 'nt-webhook-prova --elimina'.")
        if srv:
            srv.shutdown()
        if pr and pr.poll() is None:
            pr.terminate()

    wh_riepilogo(rapporto)
    for ev in rapporto["eventi"]:
        ev["arrivo"] = datetime.fromtimestamp(ev["arrivo"]).isoformat(timespec="seconds")
    write_dump("webhook_eventi.json", wh_pulisci(rapporto))
    say("\nFatto. Allega a Claude diagnostica/webhook_eventi.json (e controllalo prima con il Blocco note, NON config.json).")


def wh_riepilogo(r):
    ev = r["eventi"]
    say("\n=== RIEPILOGO ===")
    say(f"Eventi ricevuti: {len(ev)}")
    if not ev:
        say("Nessuno. Possibili cause: il tunnel temporaneo non e' raggiungibile da Netatmo, la registrazione e' stata ignorata,")
        say("oppure lo Smarther non ha generato eventi in questi minuti (riprova muovendo la temperatura impostata).")
        return
    tipi = {}
    for e in ev:
        c = e["corpo"] if isinstance(e["corpo"], dict) else {}
        tipi[c.get("push_type", "?")] = tipi.get(c.get("push_type", "?"), 0) + 1
    say("Tipi di evento: " + ", ".join(f"{k} x{v}" for k, v in sorted(tipi.items())))
    firme = [e["firma"] for e in ev]
    say(f"Firma: {firme.count(True)} valide, {firme.count(False)} NON valide, {firme.count(None)} assenti")
    campi = set()
    ritardi = []
    for e in ev:
        c = e["corpo"] if isinstance(e["corpo"], dict) else {}
        for m in (c.get("extra_params") or {}).get("modules") or []:
            if isinstance(m, dict):
                campi.update(m.keys())
                if isinstance(m.get("last_seen"), (int, float)):
                    ritardi.append(e["arrivo"] - m["last_seen"])
    say("Campi presenti nei moduli: " + (", ".join(sorted(campi)) or "nessuno"))
    if ritardi:
        ritardi.sort()
        say(f"Ritardo fra 'last_seen' del modulo e l'arrivo: da {ritardi[0]:.0f} a {ritardi[-1]:.0f} secondi (mediana {ritardi[len(ritardi) // 2]:.0f})")
    if r["confronti"]:
        mancanti = set.intersection(*[set(c["solo_in_homestatus"]) for c in r["confronti"]])
        diversi = set().union(*[set(c["diversi"]) for c in r["confronti"]])
        say("Campi di homestatus che il webhook NON porta: " + (", ".join(sorted(mancanti)) or "nessuno"))
        say("Campi che nel webhook erano diversi da homestatus: " + (", ".join(sorted(diversi)) or "nessuno"))
        if mancanti:
            say("=> Il webhook da solo non basta: serve ancora leggere homestatus (la sola notifica fa da campanello).")
        else:
            say("=> Il webhook porta tutti i campi di homestatus: si potrebbe costruire lo stato dagli eventi.")


# --------------------------------------------------------------------------

def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Strumenti di appoggio per il pannello pdc")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("st-devices", help="elenca i dispositivi SmartThings e trova la pdc")
    sub.add_parser("st-oauth", help="procedura guidata per il token OAuth")
    d = sub.add_parser("st-dump", help="salva lo stato completo della pdc")
    d.add_argument("--device", help="device id (altrimenti da config.json o auto)")
    sub.add_parser("nt-dump", help="salva i dati Netatmo/Smarther")
    r = sub.add_parser("st-prova-refresh", help="esperimento: il comando refresh anticipa i dati del ciclo? (pannello FERMO)")
    r.add_argument("--device", help="device id (altrimenti da config.json o auto)")
    r.add_argument("--giri", type=int, default=8, help="numero di giri (pari; meta' con refresh, meta' senza). Default 8")
    r.add_argument("--finestra", type=int, default=120, help="secondi per ogni giro (default 120)")
    r.add_argument("--pausa", type=int, default=45, help="pausa tra un giro e l'altro, non conteggiata (default 45)")
    r.add_argument("--ogni", type=int, default=10, help="secondi tra una lettura e l'altra (default 10)")
    w = sub.add_parser("nt-webhook-prova", help="prova i webhook Netatmo con un tunnel temporaneo (il pannello puo' restare acceso)")
    w.add_argument("--minuti", type=int, default=30, help="quanti minuti restare in ascolto (default 30)")
    w.add_argument("--porta", type=int, default=0, help="porta locale del ricevitore (default: ne sceglie una libera da sola)")
    w.add_argument("--url", help="usa questo indirizzo pubblico (che deve arrivare alla --porta di questo PC) invece del tunnel temporaneo")
    w.add_argument("--metodo", choices=("vecchio", "nuovo"), default="vecchio",
                   help="vecchio = /api/addwebhook (default); nuovo = servizio /webhooks/v1/ (parte solo se l'elenco dei webhook e' vuoto)")
    w.add_argument("--rinnova", action="store_true", help="rinnova il token Netatmo: SOLO a pannello fermo")
    w.add_argument("--elimina", action="store_true", help="non ascolta: toglie da Netatmo un webhook rimasto registrato")
    g = sub.add_parser("rip-diag", help="diagnostica della ripartenza prevista dello Smarther (legge il database, pannello anche acceso)")
    g.add_argument("--db", help="percorso del database (di solito lo trova da solo)")
    g.add_argument("--ultime", type=int, default=15, help="quante pause mostrare, le ultime (default 15)")
    g.add_argument("--salva", action="store_true", help="salva anche il rapporto in diagnostica/rip_diag.json")
    b = sub.add_parser("sbrinamenti", help="elenca gli sbrinamenti trovati nel database (legge il database, pannello anche acceso)")
    b.add_argument("--db", help="percorso del database (di solito lo trova da solo)")
    b.add_argument("--giorni", type=int, default=7, help="quanti giorni guardare (default 7)")
    b.add_argument("--salva", action="store_true", help="salva anche il rapporto in diagnostica/sbrinamenti.json")
    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return
    if args.cmd in ("rip-diag", "sbrinamenti"):        # non serve config.json e non ne crea
        (cmd_rip_diag if args.cmd == "rip-diag" else cmd_sbrinamenti)(None, args)
        return
    cfg = load_cfg()
    if args.cmd == "nt-webhook-prova" and args.elimina:
        if args.metodo == "nuovo":
            c, js = wh_chiama(wh_token(cfg, 0, args.rinnova), "DELETE", "/webhooks/v1/")
            say(f"DELETE /webhooks/v1/: HTTP {c}: {json.dumps(js, ensure_ascii=False)[:300]}")
        else:
            c, js = wh_chiama(wh_token(cfg, 0, args.rinnova), "POST", "/api/dropwebhook", {})
            say(f"dropwebhook: HTTP {c}: {json.dumps(js, ensure_ascii=False)[:300]}")
        c, js = wh_chiama(wh_token(cfg, 0, args.rinnova), "GET", "/webhooks/v1/")
        say(f"webhook rimasti: HTTP {c}: {json.dumps(js, ensure_ascii=False)[:300]}")
        return
    {"st-devices": cmd_st_devices, "st-oauth": cmd_st_oauth,
     "st-dump": cmd_st_dump, "nt-dump": cmd_nt_dump,
     "st-prova-refresh": cmd_st_prova_refresh, "nt-webhook-prova": cmd_nt_webhook_prova}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
