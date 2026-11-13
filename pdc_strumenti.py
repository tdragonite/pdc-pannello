#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdc_strumenti.py - strumenti di appoggio per il progetto "pannello pompa di calore".

Fa queste cose, tutte con la sola libreria standard di Python:

  st-devices   elenca i dispositivi SmartThings e trova la pompa di calore
  st-oauth     procedura guidata per ottenere un token OAuth (con rinnovo automatico)
  st-osserva   guarda la pdc per N minuti e annota cosa cambia (per scoprire Boost/programmi; solo lettura)
  st-dump      salva lo stato COMPLETO della pdc in file JSON (da mandare a Claude)
  st-capacita  salva i comandi che SmartThings espone per la pdc (da mandare a Claude; non cambia nulla)
  nt-dump      salva i dati dello Smarther letti dalle API Netatmo (idem)
  st-prova-refresh  esperimento: il comando 'refresh' anticipa i dati del ciclo? (pannello fermo)
  rip-diag     diagnostica della «ripartenza prevista» dello Smarther: perche' c'e' o non c'e', quali pause usa
  nt-webhook-prova  prova i webhook Netatmo: registra un indirizzo temporaneo, ascolta gli eventi dello Smarther e li confronta con homestatus (pannello anche acceso)
  sbrinamenti  elenca gli sbrinamenti trovati nel database, uno per uno, con i cambi di stato grezzi (per controllare il conteggio)
  analisi      UN file con tutti i dati (impostazioni, cicli, sbrinamenti, serie nel tempo) per farli analizzare a Claude
  meteo-prova  prova a leggere umidita' e temperatura da una pagina WeatherLink "embeddable" (stazione meteo vicina), senza chiavi

Usa un unico config.json nella stessa cartella dello script (lo stesso che
usera' poi il pannello vero). I token rinnovati vengono riscritti li' dentro.

Esempi:
    python pdc_strumenti.py st-devices
    python pdc_strumenti.py st-dump
    python pdc_strumenti.py nt-dump
    python pdc_strumenti.py rip-diag
    python pdc_strumenti.py analisi
    python pdc_strumenti.py nt-webhook-prova --minuti 30
    python pdc_strumenti.py nt-webhook-prova --metodo nuovo --minuti 15
    python pdc_strumenti.py sbrinamenti --giorni 28
    python pdc_strumenti.py meteo-prova "https://www.weatherlink.com/embeddablePage/show/<codice>/summary"
"""

import argparse
import atexit
import base64
import bisect
from datetime import datetime
import functools
import getpass
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

if sys.version_info < (3, 10):
    sys.exit(f"Serve Python 3.10 o piu' recente (hai la {sys.version.split()[0]}): scaricalo da python.org.")

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
    except OSError as e:                 # URLError, timeout, connessione caduta a meta' lettura
        die(f"Errore di rete: {getattr(e, 'reason', None) or e}")
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


def _device_id(cfg, args):
    """Il dispositivo su cui lavorare: --device, oppure quello in config.json, oppure l'unica pdc trovata sull'account."""
    device_id = args.device or cfg["smartthings"].get("device_id")
    if not device_id:
        _, ehs = find_ehs(cfg)
        if len(ehs) != 1:
            die("Non riesco a capire quale sia la pdc: lancia 'st-devices' oppure usa --device <id>.")
        device_id = ehs[0]["deviceId"]
    return device_id


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
    device_id = _device_id(cfg, args)
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


def cmd_st_capacita(cfg, args):
    """Chiede a SmartThings la definizione di ogni funzione della pdc (comandi e argomenti): serve a capire COME si
    comandano cose come i programmi dell'acqua calda, il boost o gli FSV. Non cambia niente sulla pompa."""
    device_id = _device_id(cfg, args)
    say(f"\nLeggo l'elenco delle funzioni del dispositivo {device_id} ...")
    device = st_get(cfg, f"/devices/{device_id}")
    trovate = {}
    for comp in device.get("components", []):
        for c in comp.get("capabilities", []):
            trovate.setdefault((c.get("id"), c.get("version", 1)), []).append(comp.get("id"))
    out = {}
    for (cid, ver), comps in sorted(trovate.items()):
        try:
            d = st_get(cfg, f"/capabilities/{cid}/{ver}")
        except SystemExit:
            raise
        except Exception as e:  # una funzione che non si lascia leggere non ferma le altre
            out[cid] = {"componenti": comps, "errore": str(e)[:200]}
            continue
        out[cid] = {"versione": ver, "componenti": comps,
                    "attributi": sorted((d.get("attributes") or {}).keys()),
                    "comandi": {n: [{"nome": a.get("name"), "schema": a.get("schema")} for a in (c.get("arguments") or [])]
                                for n, c in (d.get("commands") or {}).items()}}
        say(f"  {cid}: {len(out[cid]['comandi'])} comandi")
    say("")
    write_dump("dump_smartthings_capacita.json", out)
    say("\nFatto. Allega a Claude questo file (NON config.json):\n  dump_smartthings_capacita.json")


def cmd_st_osserva(cfg, args):
    """Guarda lo stato della pdc ogni pochi secondi e annota ogni attributo che CAMBIA, con l'ora: serve a scoprire
    cosa succede quando da Dwellsee / app Samsung si preme Boost o si crea un programma. Legge soltanto."""
    device_id = _device_id(cfg, args)
    rumore = () if args.tutto else ("samsungce.ehsCycleData", "powerConsumptionReport", "temperatureMeasurement")
    fine = time.time() + args.minuti * 60
    say(f"\nOsservo la pdc per {args.minuti} minuti (una lettura ogni {args.ogni} s). Ctrl+C per fermarmi prima.")
    say("Adesso fai le prove (Boost, programma...) e SEGNATI l'ora di ogni cosa che premi.\n")
    prima, cambi, errori, iniziale = None, [], 0, {}
    try:
        while time.time() < fine:
            try:
                st = st_get(cfg, f"/devices/{device_id}/status")
                errori = 0
            except SystemExit:
                errori += 1
                if errori >= 5:
                    say("Troppi errori di fila, mi fermo e salvo quello che ho.")
                    break
                time.sleep(args.ogni)
                continue
            ora = _appiattisci(st)
            adesso = datetime.now().strftime("%H:%M:%S")
            if prima is None:
                say(f"{adesso}  prima lettura: {len(ora)} attributi")
                iniziale = {k: v[0] for k, v in ora.items()}
            else:
                for k, (val, ts) in ora.items():
                    if any(r in k for r in rumore):
                        continue
                    vecchio = prima.get(k)
                    if vecchio is None or vecchio[0] != val:
                        cambi.append({"letto_alle": adesso, "attributo": k, "prima": vecchio[0] if vecchio else None,
                                      "dopo": val, "ts_pompa": ts})
                        say(f"{adesso}  {k}\n           {(vecchio[0] if vecchio else '-')[:120]}  ->  {val[:300]}")
            prima = ora
            time.sleep(args.ogni)
    except KeyboardInterrupt:
        say("\nFermato.")
    say(f"\n{len(cambi)} cambi annotati.")
    write_dump("dump_smartthings_osserva.json", {"device": device_id, "iniziale": iniziale, "cambi": cambi})
    say("\nFatto. Allega a Claude questo file (NON config.json):\n  dump_smartthings_osserva.json")


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
    device_id = _device_id(cfg, args)
    giri = max(2, args.giri + args.giri % 2)           # numero pari: meta' con refresh, meta' senza
    tipi = ["refresh"] * (giri // 2) + ["controllo"] * (giri // 2)
    random.shuffle(tipi)
    say("\nATTENZIONE: il pannello deve essere FERMO (usa lo stesso token).")
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


def _chiedi_segreto(nome):
    """Chiede un segreto senza mostrarlo sullo schermo (se il terminale non lo permette, lo chiede in chiaro)."""
    try:
        say(f"({nome}: mentre incolli non si vede nulla, e' normale. Poi premi Invio.)")
        return getpass.getpass(f"{nome}: ").strip()
    except (EOFError, KeyboardInterrupt):
        raise
    except Exception:                                            # noqa: BLE001
        return input(f"{nome}: ").strip()


def cmd_st_oauth(cfg, args):
    oa = cfg["smartthings"]["oauth"]
    say("\n=== Autorizzazione OAuth SmartThings ===")
    say("Prima devi aver creato l'app OAuth con la SmartThings CLI (vedi la guida, punto 2).")
    cid = oa.get("client_id") or input("Client ID: ").strip()
    secret = oa.get("client_secret") or _chiedi_segreto("Client Secret")
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


def _carica_pannello(obbligatorio=True):
    """Il modulo pdc_pannello.py che sta accanto a questo script (per usare i suoi stessi calcoli). Se manca: errore, oppure None."""
    sys.path.insert(0, str(HERE))
    try:
        import pdc_pannello as pp
    except Exception as e:                                   # noqa: BLE001
        if obbligatorio:
            die(f"Non riesco a caricare pdc_pannello.py (deve stare nella stessa cartella di questo script): {e}")
        return None
    return pp


class _DbSolaLettura:
    """Quanto basta del database del pannello per le sue funzioni di calcolo: solo _c(), aperto in sola lettura."""

    def __init__(self, path):
        self.path = Path(path)

    def _c(self):
        import sqlite3
        return sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)


def _db_leggibile(path):
    """Apre il database in sola lettura (anche con il pannello acceso); se non si riesce, lavora su una copia temporanea,
    che sparisce da sola alla fine del comando. Ritorna (database, cartella della copia oppure None)."""
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
        atexit.register(shutil.rmtree, tmp, True)                # la copia sparisce anche se il comando si ferma con un errore
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
    pp = _carica_pannello()
    if "diag" not in getattr(getattr(pp.stima_ripartenza, "__code__", None), "co_varnames", ()):
        die("Questo pdc_pannello.py e' troppo vecchio per la diagnostica: sostituiscilo con l'ultimo (versione 0.19 piu' recente).")
    db_path = _trova_db(args.db)
    db, _ = _db_leggibile(db_path)
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


def cmd_sbrinamenti(cfg, args):
    """Sbrinamenti nel database: i cambi di stato grezzi e l'elenco che ne ricava il pannello (stessa funzione del pannello)."""
    pp = _carica_pannello()
    if not hasattr(pp, "elenco_sbrinamenti"):
        die("Questo pdc_pannello.py e' troppo vecchio: sostituiscilo con l'ultimo (versione 0.19 piu' recente).")
    db_path = _trova_db(args.db)
    db, _ = _db_leggibile(db_path)
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

WH_MAX_EVENTI = 2000
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
                lungo = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                lungo = 0
            n = max(0, min(lungo, 262144))                  # mai negativo: read(-1) aspetterebbe la fine della connessione
            if lungo != n:
                self.close_connection = True                # corpo non letto per intero: la connessione non si riusa
            corpo = self.rfile.read(n) if n else b""
            firma = wh_firma_ok(corpo, segreto, self.headers.get("X-Netatmo-secret"))
            try:
                js = json.loads(corpo.decode("utf-8", "replace")) if corpo.strip() else {}
            except ValueError:
                js = {"raw": corpo[:300].decode("utf-8", "replace")}
            with lock:
                if len(eventi) < WH_MAX_EVENTI:             # l'indirizzo e' pubblico (anche se temporaneo): la memoria non cresce senza limite
                    eventi.append({"arrivo": time.time(), "firma": firma, "percorso": self.path, "corpo": js,
                                   "da": (self.headers.get("CF-Connecting-IP") or self.client_address[0]),
                                   "agente": (self.headers.get("User-Agent") or "")[:60]})
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


WH_SALTA = ("last_seen", "time_server", "wifi_strength", "rf_strength", "last_user_interaction", "last_bilan", "uptime", "last_activity")


def wh_stato_piatto(home):
    """Lo stato di casa come {campo: valore}, senza i campi che cambiano a ogni lettura."""
    return {k: v for k, v in walk(home) if k.split(".")[-1].split("[")[0] not in WH_SALTA}


def wh_sonda(token, home_id, prima, rapporto):
    """Legge homestatus e registra (con l'ora) cio' che e' cambiato dall'ultima volta. Restituisce il nuovo stato piatto."""
    home = (wh_leggi(token, "/api/homestatus", {"home_id": home_id}).get("body") or {}).get("home")
    if not isinstance(home, dict):
        return prima
    ora = datetime.now()
    nuovo = wh_stato_piatto(home)
    if prima is not None:
        for k in sorted(set(prima) | set(nuovo)):
            if prima.get(k) != nuovo.get(k):
                rapporto["cambiamenti_netatmo"].append({"arrivo": ora.timestamp(), "campo": k, "prima": prima.get(k), "dopo": nuovo.get(k)})
                say(f"  {ora.strftime('%H:%M:%S')}  Netatmo vede: {k}: {prima.get(k)} -> {nuovo.get(k)}")
    return nuovo


def wh_controlla_registro(token, url, rapporto):
    c, js = wh_chiama(token, "GET", "/webhooks/v1/")
    presente = bool(url and url in json.dumps(js))
    rapporto["controlli_registro"].append({"ora": datetime.now().isoformat(timespec="seconds"), "http": c, "presente": presente})
    if c >= 400 or not presente:
        say(f"  {datetime.now().strftime('%H:%M:%S')}  ATTENZIONE: il webhook non risulta piu' registrato (HTTP {c})")


def cmd_nt_webhook_prova(cfg, args):
    import threading
    nt = cfg["netatmo"]
    token = wh_token(cfg, args.minuti * 60, args.rinnova)
    segreto = nt.get("client_secret") or ""
    if not segreto:
        die("Manca netatmo.client_secret in config.json (serve a controllare la firma dei messaggi).")
    eventi, lock = [], threading.Lock()
    rapporto = {"avviato": datetime.now().isoformat(timespec="seconds"), "minuti": args.minuti, "registrazione": None,
                "eventi": [], "confronti": [], "elenco_prima": None, "elenco_dopo": None, "rimozione": None,
                "cambiamenti_netatmo": [], "controlli_registro": []}

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
    elif c0 < 400 and isinstance(js0, dict) and js0.get("body") not in ([], None) and not args.sostituisci:
        die("Sul tuo account c'e' gia' un webhook registrato (vedi l'elenco qui sopra): con il metodo 'vecchio' la prova lo SOSTITUIREBBE e alla fine "
            "lo toglierebbe. Se e' quello che serve a qualcosa (per esempio al pannello) non andare avanti. Se vuoi procedere lo stesso, "
            "rilancia aggiungendo --sostituisci.")
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
        sonda = int(getattr(args, "sonda", 20) or 0)
        stato_nt = None
        prox_sonda, prox_reg = time.time(), time.time() + 60
        try:
            while time.time() < fine:
                time.sleep(1)
                if sonda and home_id and time.time() >= prox_sonda:
                    stato_nt = wh_sonda(token, home_id, stato_nt, rapporto)
                    prox_sonda = time.time() + sonda
                if time.time() >= prox_reg:
                    wh_controlla_registro(token, url, rapporto)
                    prox_reg = time.time() + 120
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
    for ev in rapporto["eventi"] + rapporto["cambiamenti_netatmo"]:
        ev["arrivo"] = datetime.fromtimestamp(ev["arrivo"]).isoformat(timespec="seconds")
    write_dump("webhook_eventi.json", wh_pulisci(rapporto))
    say("\nFatto. Allega a Claude diagnostica/webhook_eventi.json (e controllalo prima con il Blocco note, NON config.json).")


def wh_riepilogo(r):
    ev = r["eventi"]
    say("\n=== RIEPILOGO ===")
    camb = r.get("cambiamenti_netatmo") or []
    gruppi = []
    for c in camb:                       # i cambiamenti letti a meno di 15 secondi l'uno dall'altro sono un solo gesto
        if not gruppi or c["arrivo"] - gruppi[-1][-1]["arrivo"] > 15:
            gruppi.append([])
        gruppi[-1].append(c)
    utili = [e for e in ev if isinstance(e["corpo"], dict) and e["corpo"].get("push_type") not in ("webhook_activation", "webhook_deletion")]
    abbinati = sum(1 for g in gruppi if any(0 <= e["arrivo"] - g[0]["arrivo"] <= 90 or 0 <= g[0]["arrivo"] - e["arrivo"] <= 30 for e in utili))
    say(f"Cambiamenti visti da Netatmo (homestatus): {len(gruppi)} ({len(camb)} campi). Eventi webhook oltre all'attivazione: {len(utili)}. "
        f"Cambiamenti con un evento vicino: {abbinati}.")
    reg = r.get("controlli_registro") or []
    if reg:
        say(f"Controlli del registro webhook: {sum(1 for x in reg if x['presente'])} su {len(reg)} con il webhook ancora registrato.")
    if gruppi and not utili:
        say("=> Netatmo ha visto i tuoi gesti ma non ha mandato nessun evento: guarda i controlli del registro; se era ancora registrato, scrivi a contact-api@netatmo.com.")
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
# meteo-prova: la stazione meteo vicina (WeatherLink, pagina "embeddable")
# --------------------------------------------------------------------------

METEO_UA = "Mozilla/5.0 (compatible; pdc-strumenti/1.0)"
METEO_CHIAVI = re.compile(r"hum|temp|dew|rain|wind|bar|press|time|date|updat|ts$|stamp|name|title", re.I)


def meteo_scarica(url, max_byte=2_000_000):
    """Scarica un indirizzo: (http, content-type, testo) oppure (None, errore, '')."""
    try:
        with urlopen(Request(url, headers={"User-Agent": METEO_UA, "Accept": "application/json, text/html, */*"}), timeout=15) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read(max_byte).decode("utf-8", "replace")
    except HTTPError as e:
        try:
            corpo = e.read(2000).decode("utf-8", "replace")
        except Exception:
            corpo = ""
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", corpo
    except (URLError, OSError, ValueError) as e:
        return None, f"{type(e).__name__}: {e}", ""


def meteo_valori(obj, percorso="", out=None):
    """Percorre un JSON e raccoglie i campi che sembrano temperatura, umidita', orario..."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            meteo_valori(v, f"{percorso}.{k}" if percorso else str(k), out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:40]):
            meteo_valori(v, f"{percorso}[{i}]", out)
    elif isinstance(obj, (int, float, str)) and METEO_CHIAVI.search(percorso.rsplit(".", 1)[-1]):
        out.append((percorso, obj))
    return out


def cmd_meteo_prova(cfg, args):
    url = args.url.strip()
    m = re.search(r"^(https?://[^/]+)/embeddablePage/show/([0-9a-fA-F]{32})", url)
    if not m:
        die("L'indirizzo non sembra una pagina WeatherLink 'embeddable'. Deve essere del tipo "
            "https://www.weatherlink.com/embeddablePage/show/<32 caratteri>/summary")
    base, codice = m.group(1), m.group(2)
    rapporto = {"url": url, "candidati": []}

    say(f"1) Leggo la pagina {url} ...")
    codice_http, tipo, html = meteo_scarica(url)
    say(f"   HTTP {codice_http}, {tipo}, {len(html)} caratteri")
    if codice_http is None:
        die("Non riesco a raggiungere la pagina (vedi sopra). Controlla la connessione e riprova.")
    titolo = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if titolo:
        say(f"   Titolo della pagina: {titolo.group(1).strip()}")
        rapporto["titolo"] = titolo.group(1).strip()

    # 2) indirizzi candidati: quello classico + qualunque riferimento a getData / api trovato nella pagina e nei suoi script
    cand = [f"{base}/embeddablePage/getData/{codice}"]
    script = re.findall(r'<script[^>]+src="([^"]+)"', html, re.I)
    testi = [html]
    for sc in script[:12]:
        if sc.startswith("//"):
            sc = "https:" + sc
        elif sc.startswith("/"):
            sc = base + sc
        if not sc.startswith("http") or "googletagmanager" in sc or "google-analytics" in sc:
            continue
        h, _, t = meteo_scarica(sc, 3_000_000)
        if h == 200:
            testi.append(t)
    trovati = set()
    for t in testi:
        for r in re.findall(r"""["'`]((?:https?:)?/{0,2}[\w./:-]*(?:getData|embeddablePage|/api/|/v1/|/v2/)[\w./:?=&%-]*)["'`]""", t):
            trovati.add(r)
    rapporto["riferimenti_trovati"] = sorted(trovati)[:40]
    say(f"2) Riferimenti a dati/API trovati nella pagina e negli script: {len(trovati)}")
    for r in sorted(trovati)[:25]:
        say("     " + r)
    for r in sorted(trovati):
        if "getData" in r or "embeddablePage" in r:
            u = r if r.startswith("http") else (base + r if r.startswith("/") else None)
            if u:
                u = u.replace("{token}", codice).replace("${token}", codice)
                if u.rstrip("/").endswith(("getData", "getdata")):
                    u = u.rstrip("/") + "/" + codice
                if u not in cand and "{" not in u and "$" not in u:
                    cand.append(u)

    say("3) Provo gli indirizzi candidati:")
    buono = None
    for u in cand[:8]:
        h, tipo, corpo = meteo_scarica(u)
        riga = {"url": u, "http": h, "tipo": tipo, "byte": len(corpo)}
        say(f"   {riga['url']}  ->  HTTP {h}, {tipo}, {len(corpo)} caratteri")
        js = None
        if h == 200:
            try:
                js = json.loads(corpo)
            except ValueError:
                say("      (non e' JSON; inizio: " + corpo[:120].replace("\n", " ") + ")")
        if js is not None:
            valori = meteo_valori(js)
            riga["valori"] = valori[:60]
            say(f"      JSON valido: {len(valori)} campi di interesse")
            if buono is None:
                buono = (u, js, valori)
        rapporto["candidati"].append(riga)

    if buono:
        u, js, valori = buono
        say("\n4) Campi che sembrano temperatura, umidita', orario (primi 40):")
        for k, v in valori[:40]:
            say(f"     {k} = {v}")
        hum = [(k, v) for k, v in valori if re.search(r"hum", k, re.I) and isinstance(v, (int, float, str))]
        say("\n=> Umidita' trovata: " + (", ".join(f"{k}={v}" for k, v in hum[:6]) if hum else "NESSUN campo con 'hum': guarda l'elenco qui sopra."))
        ult = js.get("lastReceived") if isinstance(js, dict) else None
        if isinstance(ult, (int, float)) and ult > 1e11:          # millisecondi dal 1970
            minuti = (time.time() - ult / 1000) / 60
            say(f"   Ultimo dato ricevuto dalla stazione: {datetime.fromtimestamp(ult / 1000):%d/%m/%Y %H:%M} ({minuti:.0f} minuti fa)")
        say("\n>>> INDIRIZZO DEI DATI da incollare in Impostazioni del pannello (se la stazione cambia, rilancia questo comando e incolla il nuovo):")
        say("\n    " + u + "\n")
        rapporto["json_completo"] = js if len(json.dumps(js)) < 200_000 else "troppo grande"
    else:
        say("\n=> Nessun indirizzo JSON leggibile: la stazione potrebbe non offrire i dati senza chiavi.")
    write_dump("meteo_prova.json", rapporto)
    say("\nFatto. Allega a Claude diagnostica/meteo_prova.json (non contiene password ne' token).")


# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Analisi completa: tutto quello che il database sa, in UN file (legge il database, non scrive niente nel pannello)
# --------------------------------------------------------------------------

ANALISI_VERSIONE = 2
# Dati dell'impianto che NON vanno nel file (identificativi, nomi, posti): servono solo a riconoscere i dispositivi.
ANALISI_ESCLUDI = ("info.pdc.id_ocf", "info.pdc.vid", "info.pdc.registrata", "info.pdc.nome", "info.pdc.descrizione",
                   "info.nt.id", "info.nt.casa", "info.nt.nome", "info.nt.stanza")
ANALISI_CONFIG_OK = {"intervallo_pdc_secondi", "intervallo_netatmo_secondi", "litri", "prezzo_kwh", "prezzo", "isteresi"}


def _arr(x, n=3):
    return None if x is None else round(x, n)


def _stat(vals):
    """Riassunto di una lista di numeri: quanti, minimo, percentili, massimo, media."""
    vs = sorted(v for v in vals if v is not None)
    n = len(vs)
    if not n:
        return None
    q = lambda p: vs[min(n - 1, int(p * (n - 1) + 0.5))]            # noqa: E731
    return {"n": n, "min": _arr(vs[0]), "p5": _arr(q(.05)), "p25": _arr(q(.25)), "mediana": _arr(q(.5)), "p75": _arr(q(.75)),
            "p95": _arr(q(.95)), "max": _arr(vs[-1]), "media": _arr(sum(vs) / n)}


@functools.lru_cache(maxsize=None)
def _giorno_blocco(blocco):
    return time.strftime("%Y-%m-%d", time.localtime(blocco * 900))


def _giorno(ts):
    """Il giorno locale 'AAAA-MM-GG' di un istante. Si calcola una volta per blocco di 15 minuti (tutti i fusi orari, ora legale compresa,
    cambiano giorno su un multiplo di 15 minuti): con centinaia di migliaia di misure fa la differenza."""
    return _giorno_blocco(int(ts) // 900)


def _ore(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _val_a(serie, ts, tempo_max=None):
    """Valore (ts, v) di una serie ordinata [(ts, v)] nell'ultimo istante <= ts (None se piu' vecchio di tempo_max secondi)."""
    i = bisect.bisect_right(serie, (ts, float("inf"))) - 1
    if i < 0:
        return None
    if tempo_max is not None and ts - serie[i][0] > tempo_max:
        return None
    return serie[i][1]


def _fra(serie, t0, t1):
    """I valori di una serie ordinata tra t0 e t1 (estremi compresi)."""
    i, j = bisect.bisect_left(serie, (t0, float("-inf"))), bisect.bisect_right(serie, (t1, float("inf")))
    return [v for _, v in serie[i:j]]


def _sezione(out, nome, fn):
    """Una sezione che sbaglia non rovina le altre: l'errore finisce nel file e il resto va avanti."""
    try:
        out[nome] = fn()
    except Exception as e:                                       # noqa: BLE001
        out.setdefault("errori", {})[nome] = f"{type(e).__name__}: {e}"
        say(f"   (sezione «{nome}» non riuscita: {e})")


def _buchi(serie):
    """Intervallo tipico fra due misure e i buchi nei dati (piu' di 5 volte l'intervallo tipico, minimo 10 minuti)."""
    if len(serie) < 2:
        return {"intervallo_s": None, "buchi": 0, "minuti_senza_dati": 0, "buchi_maggiori": []}
    d = [b[0] - a[0] for a, b in zip(serie, serie[1:], strict=False)]
    med = sorted(d)[len(d) // 2]
    lim = max(5 * med, 600)
    br = [(b[0] - a[0], a[0]) for a, b in zip(serie, serie[1:], strict=False) if b[0] - a[0] > lim]
    br.sort(reverse=True)
    return {"intervallo_s": med, "buchi": len(br), "minuti_senza_dati": round(sum(x for x, _ in br) / 60),
            "buchi_maggiori": [{"da": _ore(t), "minuti": round(x / 60)} for x, t in br[:10]]}


def _segmenti_on(serie, soglia=0.5, vuoto_max=900):
    """[(inizio, fine)] dei tratti in cui la serie e' >= soglia. Un buco nei dati piu' lungo di vuoto_max chiude il tratto."""
    seg, ini, ult = [], None, None
    for ts, v in serie:
        if ini is not None and ts - ult > vuoto_max:
            seg.append((ini, ult))
            ini = None
        if v >= soglia:
            if ini is None:
                ini = ts
        elif ini is not None:
            seg.append((ini, ts))
            ini = None
        ult = ts
    if ini is not None:
        seg.append((ini, ult))
    return seg


def _classe_esterna(t):
    if t is None:
        return "?"
    for lim, nome in ((-5, "< -5"), (0, "-5 .. 0"), (5, "0 .. 5"), (10, "5 .. 10"), (15, "10 .. 15"), (20, "15 .. 20")):
        if t < lim:
            return nome + " °C"
    return ">= 20 °C"


CLASSI_ORDINE = ["< -5 °C", "-5 .. 0 °C", "0 .. 5 °C", "5 .. 10 °C", "10 .. 15 °C", "15 .. 20 °C", ">= 20 °C", "?"]


def _cicli_smarther(S, ora):
    """Ogni periodo di richiesta calore dello Smarther con quello che e' successo in casa: da qui si vede l'isteresi vera."""
    ric, casa, est, setp = S.get("casa.richiesta_calore", []), S.get("casa.temperatura", []), S.get("ciclo.esterna", []), S.get("casa.setpoint", [])
    seg = _segmenti_on(ric)
    cicli = []
    for k, (t0, t1) in enumerate(seg):
        prossimo = seg[k + 1][0] if k + 1 < len(seg) else None
        chiuso = t1 < (ric[-1][0] - 60) if ric else False
        sp = _val_a(setp, t0, 7200)
        c0, c1 = _val_a(casa, t0, 900), _val_a(casa, t1, 900)
        finestra = _fra(casa, t1, min(t1 + 5400, prossimo if prossimo else t1 + 5400))
        picco = max(finestra) if finestra else None
        off_val = _fra(casa, t1, prossimo) if prossimo else []
        minimo = min(off_val) if off_val else None
        e = _fra(est, t0, prossimo or t1)
        cicli.append({"inizio": _ore(t0), "ts": t0, "on_min": round((t1 - t0) / 60, 1),
                      "off_min": round((prossimo - t1) / 60, 1) if prossimo else None,
                      "setpoint": sp, "casa_inizio": c0, "casa_fine_on": c1, "casa_picco_dopo": picco, "casa_minimo_off": minimo,
                      "sopra_setpoint": _arr(picco - sp, 2) if picco is not None and sp is not None else None,
                      "sotto_setpoint_alla_partenza": _arr(c0 - sp, 2) if c0 is not None and sp is not None else None,
                      "escursione": _arr(picco - minimo, 2) if picco is not None and minimo is not None else None,
                      "esterna_media": _arr(sum(e) / len(e), 1) if e else None, "chiuso": bool(prossimo) or chiuso})
    complet = [c for c in cicli if c["off_min"] is not None]
    giorni = {}
    for c in cicli:
        giorni.setdefault(_giorno(c["ts"]), []).append(c)
    classi = {}
    for c in complet:
        classi.setdefault(_classe_esterna(c["esterna_media"]), []).append(c)
    riassunto = {
        "n_periodi": len(cicli), "n_completi": len(complet),
        "durata_on_min": _stat([c["on_min"] for c in complet]), "pausa_min": _stat([c["off_min"] for c in complet]),
        "periodo_totale_min": _stat([c["on_min"] + c["off_min"] for c in complet]),
        "sopra_setpoint_dopo_lo_spegnimento": _stat([c["sopra_setpoint"] for c in complet]),
        "sotto_setpoint_alla_ripartenza": _stat([c["sotto_setpoint_alla_partenza"] for c in complet]),
        "escursione_casa": _stat([c["escursione"] for c in complet]),
        "per_giorno": {g: {"cicli": len(v), "on_min_totale": round(sum(c["on_min"] for c in v)), "on_min_mediano": _stat([c["on_min"] for c in v])["mediana"]}
                       for g, v in sorted(giorni.items())},
        "per_temperatura_esterna": {k: {"cicli": len(classi[k]), "on_min_mediano": _stat([c["on_min"] for c in classi[k]])["mediana"],
                                        "pausa_min_mediana": _stat([c["off_min"] for c in classi[k]])["mediana"],
                                        "sopra_setpoint_mediano": (_stat([c["sopra_setpoint"] for c in classi[k]]) or {}).get("mediana")}
                                    for k in CLASSI_ORDINE if k in classi},
    }
    return {"nota": "Un 'periodo' = lo Smarther chiede calore (casa.richiesta_calore). sopra_setpoint = quanto la casa sale oltre il target dopo che lo "
                    "Smarther smette (entro 90 min); sotto_setpoint_alla_partenza = quanto era scesa sotto il target quando ha richiesto di nuovo "
                    "(e' l'isteresi che si vede davvero). Il sensore dello Smarther va a passi di 0,1 °C.",
            "riassunto": riassunto, "cicli": cicli[-600:]}


def _cicli_compressore(S, ora):
    freq, est, man = S.get("ciclo.freq_compressore", []), S.get("ciclo.esterna", []), S.get("ciclo.mandata", [])
    seg = _segmenti_on(freq, 1, 600)
    cicli = []
    for k, (t0, t1) in enumerate(seg):
        prossimo = seg[k + 1][0] if k + 1 < len(seg) else None
        f = _fra(freq, t0, t1)
        e, m = _fra(est, t0, t1), _fra(man, t0, t1)
        cicli.append({"inizio": _ore(t0), "ts": t0, "on_min": round((t1 - t0) / 60, 1),
                      "off_min": round((prossimo - t1) / 60, 1) if prossimo else None,
                      "freq_media": _arr(sum(f) / len(f), 1) if f else None, "freq_max": max(f) if f else None,
                      "esterna_media": _arr(sum(e) / len(e), 1) if e else None, "mandata_media": _arr(sum(m) / len(m), 1) if m else None})
    giorni = {}
    for c in cicli:
        giorni.setdefault(_giorno(c["ts"]), []).append(c)
    completi = [c for c in cicli if c["off_min"] is not None]
    return {"nota": "Un 'avvio' = il compressore passa da 0 Hz a piu' di 0. Molti avvii corti (sotto i 10 minuti) = la PdC fa 'short cycling'.",
            "riassunto": {"avvii": len(cicli), "avvii_sotto_10_min": sum(1 for c in cicli if c["on_min"] < 10),
                          "durata_on_min": _stat([c["on_min"] for c in completi]), "pausa_min": _stat([c["off_min"] for c in completi]),
                          "per_giorno": {g: {"avvii": len(v), "on_min_totale": round(sum(c["on_min"] for c in v))} for g, v in sorted(giorni.items())}},
            "cicli": cicli[-600:]}


def _profilo_per_esterna(S, modo_a):
    """Per ogni fascia di temperatura esterna: com'e' la PdC mentre scalda la casa (solo i campioni del compressore acceso)."""
    freq, est, man, rit, por, pot, pom = (S.get(s, []) for s in ("ciclo.freq_compressore", "ciclo.esterna", "ciclo.mandata", "ciclo.ritorno",
                                                                   "ciclo.portata", "pot.watt", "ciclo.vel_pompa"))
    acc = {}
    for ts, f in freq:
        e = _val_a(est, ts, 300)
        k = _classe_esterna(e)
        d = acc.setdefault(k, {"tot": 0, "on": 0, "freq": [], "man": [], "rit": [], "dt": [], "por": [], "pot": [], "pom": [], "es": []})
        d["tot"] += 1
        if f > 0 and modo_a(ts) in (None, "casa"):
            d["on"] += 1
            d["freq"].append(f)
            d["es"].append(e)
            mm, rr = _val_a(man, ts, 300), _val_a(rit, ts, 300)
            if mm is not None:
                d["man"].append(mm)
            if rr is not None:
                d["rit"].append(rr)
            if mm is not None and rr is not None:
                d["dt"].append(mm - rr)
            for lista, serie in ((d["por"], por), (d["pot"], pot), (d["pom"], pom)):
                v = _val_a(serie, ts, 300)
                if v is not None:
                    lista.append(v)
    out = {}
    for k in CLASSI_ORDINE:
        d = acc.get(k)
        if not d:
            continue
        out[k] = {"campioni": d["tot"], "compressore_acceso_pct": round(100 * d["on"] / d["tot"], 1) if d["tot"] else None,
                  "freq_hz": _stat(d["freq"]), "mandata": _stat(d["man"]), "ritorno": _stat(d["rit"]), "salto_mandata_ritorno": _stat(d["dt"]),
                  "portata": _stat(d["por"]), "potenza_w": _stat(d["pot"]), "vel_pompa": _stat(d["pom"])}
    return {"nota": "Solo i campioni a compressore acceso in riscaldamento casa (valvola 'room'), divisi per temperatura esterna.", "fasce": out}


def _profilo_orario(S):
    """Media per ora del giorno (0-23): com'e' una giornata tipica."""
    out = {}
    for nome, serie in (("esterna", "ciclo.esterna"), ("casa", "casa.temperatura"), ("umidita_casa", "casa.umidita"), ("umidita_esterna", "esterna.umidita"), ("radiazione_w_m2", "meteo.radiazione"), ("potenza_w", "pot.watt"),
                        ("richiesta_calore_pct", "casa.richiesta_calore"), ("compressore_hz", "ciclo.freq_compressore"), ("mandata", "ciclo.mandata"),
                        ("acqua_calda", "acs.temperatura")):
        acc = {h: [] for h in range(24)}
        for ts, v in S.get(serie, []):
            acc[time.localtime(ts).tm_hour].append(100 * v if nome == "richiesta_calore_pct" else v)
        out[nome] = [_arr(sum(acc[h]) / len(acc[h]), 2) if acc[h] else None for h in range(24)]
    return {"nota": "Lista di 24 valori: ora 0, 1, ... 23 (ora locale).", **out}


def _acqua_aria(S, serie_t, serie_u):
    """{giorno: media dell'acqua nell'aria in g/m3}: per ogni misura di umidita' si usa la temperatura piu' vicina (entro 15 minuti)."""
    T = S.get(serie_t) or []
    tt = [x[0] for x in T]
    somma = {}
    for ts, u in S.get(serie_u) or []:
        k = bisect.bisect_left(tt, ts)
        vicini = [T[j] for j in (k - 1, k) if 0 <= j < len(T) and abs(T[j][0] - ts) <= 900]
        if not vicini:
            continue
        t = min(vicini, key=lambda x: abs(x[0] - ts))[1]
        somma.setdefault(_giorno(ts), []).append(6.112 * math.exp(17.62 * t / (243.12 + t)) * u * 2.1674 / (273.15 + t))
    return {g: round(sum(v) / len(v), 2) for g, v in somma.items()}


def _giornaliero(S, kwh_giorno):
    giorni = {}
    for nome, serie in (("esterna", "ciclo.esterna"), ("casa", "casa.temperatura"), ("umidita_casa", "casa.umidita"), ("umidita_esterna", "esterna.umidita"), ("setpoint", "casa.setpoint"),
                        ("mandata", "ciclo.mandata"), ("ritorno", "ciclo.ritorno"), ("acqua_calda", "acs.temperatura"), ("potenza_w", "pot.watt"),
                        ("freq_hz", "ciclo.freq_compressore")):
        for ts, v in S.get(serie, []):
            giorni.setdefault(_giorno(ts), {}).setdefault(nome, []).append(v)
    acqua = {nome: _acqua_aria(S, st, su) for nome, (st, su) in (("acqua_aria_casa_g_m3", ("casa.temperatura", "casa.umidita")),
                                                                    ("acqua_aria_esterna_g_m3", ("ciclo.esterna", "esterna.umidita")))}
    radiazione = {}
    for ts, v in S.get("meteo.radiazione", []):
        radiazione.setdefault(_giorno(ts), []).append(v)
    out = {}
    for g in sorted(giorni):
        d = {}
        for nome, vals in giorni[g].items():
            if nome == "freq_hz":
                att = [v for v in vals if v > 0]
                d["compressore_acceso_pct"] = round(100 * len(att) / len(vals), 1)
                d["freq_media_acceso"] = _arr(sum(att) / len(att), 1) if att else None
            else:
                d[nome] = {"min": _arr(min(vals), 1), "media": _arr(sum(vals) / len(vals), 2), "max": _arr(max(vals), 1)}
        for nome, per_giorno in acqua.items():
            if per_giorno.get(g) is not None:
                d[nome] = per_giorno[g]
        rad = radiazione.get(g, [])
        if len(rad) >= 20:      # giorno quasi completo (Open-Meteo, dato orario): somma W/m2 orari = Wh/m2
            d["radiazione_kwh_m2"] = round(sum(rad) / 1000, 2)
        d["energia_kwh_contatore"] = kwh_giorno.get(g)
        d["campioni"] = sum(len(v) for v in giorni[g].values())
        out[g] = d
    return out


def _griglia(S, t_fine, giorni, passo_fine=300, passo_lungo=1800, giorni_fine=14):
    """Tutte le serie ridotte a medie: passo 5 min negli ultimi 14 giorni, 30 min prima (come liste di numeri, null = nessun dato)."""
    out = {}
    for serie, dati in S.items():
        if not dati:
            continue
        pezzi = []
        limite = t_fine - giorni_fine * 86400
        for passo, a, b in ((passo_lungo, t_fine - giorni * 86400, limite), (passo_fine, limite, t_fine)):
            if b <= a:
                continue
            a0 = a - a % passo
            n = (b - a0) // passo + 1
            somma, cont = [0.0] * n, [0] * n
            for ts, v in dati:
                if a0 <= ts < a0 + n * passo:
                    i = (ts - a0) // passo
                    somma[i] += v
                    cont[i] += 1
            pezzi.append({"passo_s": passo, "da": a0, "da_testo": _ore(a0), "valori": [round(somma[i] / cont[i], 2) if cont[i] else None for i in range(n)]})
        out[serie] = pezzi
    return out


def _grezzi(S, t_fine, giorni):
    out = {}
    da = t_fine - giorni * 86400
    for serie, dati in S.items():
        r = [(t, v) for t, v in dati if t >= da]
        if r:
            out[serie] = {"t0": r[0][0], "dt": [0] + [y[0] - x[0] for x, y in zip(r, r[1:], strict=False)], "v": [round(v, 3) for _, v in r]}
    return {"nota": "Misure vere, non mediate: t0 = istante della prima misura (secondi Unix); dt[i] = secondi trascorsi dalla misura precedente (dt[0] = 0); "
                    "v[i] = valore. Il tempo della misura i e' t0 + somma(dt[0..i]).", "serie": out}


def _finestre_ricircolo(testo, dal_ts, ora):
    """[(inizio, fine)] delle finestre a ricircolo spento ('13:30-17:00,21:30-08:00'), giorno per giorno, da dal_ts a ora. Fine < inizio = passa la mezzanotte."""
    fasce = []
    for pezzo in str(testo).split(","):
        a, b = pezzo.strip().split("-")
        fasce.append(([int(x) for x in a.split(":")], [int(x) for x in b.split(":")]))
    lt = time.localtime(dal_ts)
    out = []
    for k in range(-1, int((ora - dal_ts) // 86400) + 2):
        for (h0, m0), (h1, m1) in fasce:
            w0 = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + k, h0, m0, 0, 0, 0, -1)))
            w1 = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + k, h1, m1, 0, 0, 0, -1)))
            if w1 <= w0:
                w1 += 86400
            if w1 > dal_ts and w0 < ora:                         # se il timer e' stato impostato a meta' finestra, si parte da li'
                out.append((max(w0, dal_ts), w1))
    return sorted(set(out))


def _raffreddamento_acs(S, stati, ora, finestre_testo, dal_ts):
    """Quanto scende la temperatura dell'acqua calda a ricircolo spento, finestra per finestra, e dopo quanto riparte la PdC."""
    temp = S.get("acs.temperatura", [])
    if len(temp) < 10:
        return None
    val = sorted((t, v) for sr, t, v in stati if sr == "pdc.valvola")
    cicli, ini = [], None
    for t, v in val:                                             # tratti in cui la valvola e' sul bollitore = ciclo acqua calda
        if v == "tank" and ini is None:
            ini = t
        elif v != "tank" and ini is not None:
            cicli.append((ini, t))
            ini = None
    if ini is not None:
        cicli.append((ini, ora))
    tts = [t for t, _ in temp]

    def t_at(t, tol=1800):
        i = bisect.bisect_right(tts, t) - 1
        return temp[i][1] if i >= 0 and t - tts[i] <= tol else None
    righe = []
    for w0, w1 in _finestre_ricircolo(finestre_testo, dal_ts, ora):
        w1c = min(w1, ora)
        g0 = w0
        for i, f in cicli:                                       # se un ciclo acqua calda finisce dentro la finestra: i primi 20 minuti l'acqua si rimescola
            if i <= w0 < f + 1200:
                g0 = max(g0, f + 1200)
        rip = next((i for i, f in cicli if g0 < i < w1c), None)  # prima ripartenza della PdC per l'acqua calda
        g1 = rip if rip else w1c
        riga = {"finestra": f"{_ore(w0)} -> {_ore(w1)}", "completa": w1 <= ora}
        if g1 - g0 < 1800:
            riga["nota"] = "troppo corta per misurare"
            righe.append(riga)
            continue
        v0, v1 = t_at(g0), t_at(g1)
        dentro = [(t, v) for t, v in temp if g0 <= t <= g1]
        if v0 is None or v1 is None or not dentro:
            riga["nota"] = "mancano i dati"
            righe.append(riga)
            continue
        ore = (g1 - g0) / 3600
        sale = max(v for _, v in dentro) - v0
        salti = sum(1 for (ta, va), (tb, vb) in zip(dentro, dentro[1:], strict=False) if va - vb >= 1.5 and tb - ta <= 900)
        riga.update({"da": _ore(g0), "a": _ore(g1), "ore_misurate": round(ore, 2), "temp_inizio": v0, "temp_fine": v1,
                     "gradi_persi": round(v0 - v1, 1), "gradi_ora": round((v0 - v1) / ore, 2),
                     "la_pdc_e_ripartita": bool(rip), "ore_prima_della_ripartenza": round((rip - w0) / 3600, 2) if rip else None,
                     "prelievo_probabile": salti > 0 or sale > 0.5,
                     "andamento_ogni_30_min": [[_ore(t)[-5:], t_at(t)] for t in range(g0, g1 + 1, 1800)]})
        righe.append(riga)
    buone = [r["gradi_ora"] for r in righe if "gradi_ora" in r and not r["prelievo_probabile"] and r["ore_misurate"] >= 1]
    lunghe = [r["gradi_ora"] for r in righe if "gradi_ora" in r and not r["prelievo_probabile"] and r["ore_misurate"] >= 5]
    return {"nota": "Finestre a ricircolo spento (timer Theben). Si misura dall'inizio della finestra fino a quando la PdC riparte per l'acqua calda (valvola sul bollitore) "
                    "o fino alla fine della finestra. 'prelievo_probabile' = salita della temperatura o crolli improvvisi (acqua fredda che entra): in quel caso il valore non e' pulito.",
            "finestre_usate": finestre_testo, "timer_attivo_dal": _ore(dal_ts),
            "mediana_gradi_ora_finestre_pulite": _arr(_stat(buone)["mediana"], 2) if buone else None,
            "mediana_gradi_ora_finestre_pulite_oltre_5_ore": _arr(_stat(lunghe)["mediana"], 2) if lunghe else None,
            "finestre": righe}


def cmd_analisi(cfg, args):
    """Un solo file con tutto quello che il database sa: impostazioni, statistiche, cicli, sbrinamenti, serie nel tempo. Per l'analisi di Claude."""
    import platform
    pp = _carica_pannello(obbligatorio=False)
    if pp is None:
        say("  (pdc_pannello.py non trovato accanto a questo script: salto le sezioni che usano i suoi calcoli)")
    db_path = _trova_db(args.db)
    db, _ = _db_leggibile(db_path)
    ora = int(time.time())
    giorni = max(1, min(120, int(args.giorni)))
    da = ora - giorni * 86400
    say(f"Analisi completa - {datetime.fromtimestamp(ora):%d/%m/%Y %H:%M}")
    say(f"Database: {db_path}  ({db_path.stat().st_size // (1024 * 1024)} MB), ultimi {giorni} giorni")

    with db._c() as c:
        nomi = [r[0] for r in c.execute("SELECT DISTINCT serie FROM misure").fetchall()]
        S = {n: c.execute("SELECT ts, valore FROM misure WHERE serie=? AND ts BETWEEN ? AND ? ORDER BY ts", (n, da, ora)).fetchall() for n in nomi}
        primo_ts = c.execute("SELECT MIN(ts) FROM misure").fetchone()[0]
        ultimo = c.execute("SELECT serie, ts, num, txt FROM ultimo ORDER BY serie").fetchall()
        stati = c.execute("SELECT serie, ts, valore FROM stati WHERE ts>=? ORDER BY serie, ts", (da - 86400,)).fetchall()
        try:
            eventi = c.execute("SELECT ts, codice, livello, testo, chiuso_ts FROM eventi WHERE ts>=? ORDER BY ts", (da,)).fetchall()
        except Exception:                                        # noqa: BLE001
            eventi = []
        try:
            ciclo_raw = c.execute("SELECT tipo, ts, hex FROM ciclo WHERE ts>=? ORDER BY ts", (ora - int(args.ciclo) * 86400,)).fetchall() if int(args.ciclo) > 0 else []
        except Exception:                                        # noqa: BLE001
            ciclo_raw = []
    S = {k: v for k, v in S.items() if v}
    if not S:
        die("Nel database non ci sono misure in questo periodo.")
    say(f"  {sum(len(v) for v in S.values())} misure in {len(S)} serie, dal {_ore(min(v[0][0] for v in S.values()))}")

    out = {}
    out["file"] = {"formato": "pdc-analisi", "versione_formato": ANALISI_VERSIONE, "creato": _ore(ora), "creato_ts": ora,
                   "fuso_orario_utc_offset_ore": time.localtime(ora).tm_gmtoff / 3600, "periodo_giorni": giorni,
                   "primo_dato_nel_database": _ore(primo_ts) if primo_ts else None,
                   "versione_pannello": getattr(pp, "VERSIONE", None) if pp else None, "python": platform.python_version(),
                   "come_leggerlo": "Tutte le ore sono locali (ore:minuti, giorno-mese-anno ISO). Temperature in °C, potenze in W, energia in kWh, "
                                    "frequenza compressore in Hz, portata in l/min. 'ts' = secondi Unix. Il calore prodotto e il COP sono STIME "
                                    "(portata x salto termico); l'energia elettrica viene dal contatore della PdC (a scatti). Nessun token, password, "
                                    "indirizzo o identificativo e' incluso."}
    mod = None
    if pp is not None:
        try:
            mod = pp.calcola_modi(db, da, ora, 150, ora)["segmenti"]
        except Exception as e:                                   # noqa: BLE001
            out.setdefault("errori", {})["calcola_modi"] = str(e)
    ini_mod = [s[0] for s in mod] if mod else []

    def modo_a(ts):
        if not mod:
            return "casa" if _val_a(S.get("ciclo.freq_compressore", []), ts, 600) else None
        i = bisect.bisect_right(ini_mod, ts) - 1
        return mod[i][2] if i >= 0 and mod[i][0] <= ts < mod[i][1] else None

    # --- impostazioni e stato attuale
    ult = {}
    fsv = {}
    for serie, ts, n, t in ultimo:
        if serie in ANALISI_ESCLUDI:
            continue
        if serie.startswith("fsv."):
            fsv[serie[4:]] = t if t is not None else n
        else:
            ult[serie] = {"ts": _ore(ts), "num": n, "txt": t}
    out["impianto"] = {"nota": "FSV = parametri installatore della PdC (letti da SmartThings). info.* = dati del dispositivo.", "fsv": fsv, "ultimo_valore_per_serie": ult}

    def configurazione():
        try:
            j = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None
        trovati = {}

        def giu(o, p=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ANALISI_CONFIG_OK and not isinstance(v, (dict, list)):
                        trovati[p + k] = v
                    else:
                        giu(v, p + k + ".")
        giu(j)
        return trovati
    _sezione(out, "configurazione_pannello", configurazione)

    # --- qualita' dei dati
    _sezione(out, "qualita_dati", lambda: {"nota": "Per ogni serie: quante misure, e i buchi (periodi senza dati piu' lunghi di 5 volte l'intervallo tipico, minimo 10 min).",
                                           "serie": {n: {"misure": len(v), "prima": _ore(v[0][0]), "ultima": _ore(v[-1][0]), "statistiche": _stat([x for _, x in v]), **_buchi(v)}
                                                     for n, v in sorted(S.items())}})
    # --- giornaliero
    en = S.get("en.totale_wh", [])
    kwh_g = {}
    for ts, v in en:
        d = kwh_g.setdefault(_giorno(ts), [v, v])
        d[0], d[1] = min(d[0], v), max(d[1], v)
    kwh_giorno = {g: round((b - a) / 1000, 3) for g, (a, b) in kwh_g.items()}
    _sezione(out, "giornaliero", lambda: {"nota": "Per ogni giorno locale: minimo/media/massimo delle misure principali, energia dal contatore, % di tempo con compressore acceso.",
                                          "giorni": _giornaliero(S, kwh_giorno)})
    _sezione(out, "profilo_orario", lambda: _profilo_orario(S))
    _sezione(out, "smarther_cicli", lambda: _cicli_smarther(S, ora))
    _sezione(out, "compressore_cicli", lambda: _cicli_compressore(S, ora))
    _sezione(out, "profilo_per_temperatura_esterna", lambda: _profilo_per_esterna(S, modo_a))

    def distribuzioni():
        casa = [v for _, v in S.get("casa.temperatura", [])]
        isto = {}
        for v in casa:
            k = f"{round(v, 1):.1f}"
            isto[k] = isto.get(k, 0) + 1
        fr = {}
        for _, v in S.get("ciclo.freq_compressore", []):
            k = int(v // 10 * 10)
            fr[str(k)] = fr.get(str(k), 0) + 1
        return {"temperatura_casa_campioni_per_valore": dict(sorted(isto.items())),
                "setpoint_smarther_valori": sorted({v for _, v in S.get("casa.setpoint", [])}),
                "freq_compressore_campioni_per_fascia_10hz": dict(sorted(fr.items(), key=lambda x: int(x[0])))}
    _sezione(out, "distribuzioni", distribuzioni)

    def curva():
        t1, t2, m1, m2 = (float(fsv[c]) for c in ("2011", "2012", "2021", "2022"))
        punti = []
        valv = [(t, v) for sr, t, v in stati if sr == "pdc.valvola"]
        valv_ts = [t for t, _ in valv]
        for ts, m in S.get("ciclo.mandata", []):
            e = _val_a(S.get("ciclo.esterna", []), ts, 300)
            f = _val_a(S.get("ciclo.freq_compressore", []), ts, 300)
            i = bisect.bisect_right(valv_ts, ts) - 1
            if i < 0 or valv[i][1] != "room" or ts - valv[i][0] < 600:        # solo riscaldamento casa, non subito dopo l'acqua calda
                continue
            if e is not None and f and f > 0:
                punti.append((e, m))
        if pp is not None:
            teor = [pp.mandata_calcolata((t1, m1, t2, m2), e) for e, _ in punti]
        else:
            teor = [m1 if e <= t1 else m2 if e >= t2 else m1 + (m2 - m1) * (e - t1) / (t2 - t1) for e, _ in punti]
        diff = [m - t for (e, m), t in zip(punti, teor, strict=True)]
        return {"nota": "Mandata misurata (compressore acceso, valvola sul riscaldamento casa, da almeno 10 minuti) meno la mandata che la curva climatica prevede per quella temperatura esterna.",
                "curva": {"esterna_bassa": t1, "mandata_bassa": m1, "esterna_alta": t2, "mandata_alta": m2},
                "differenza_misurata_meno_curva": _stat(diff)}
    _sezione(out, "curva_climatica", curva)

    # --- parti calcolate dal pannello
    if pp is not None:
        tol = 150

        def efficienza():
            r = pp.calcola_efficienza(db, da, ora, tol, ora)
            return json.loads(json.dumps(r, default=str))
        _sezione(out, "efficienza_pannello", efficienza)
        _sezione(out, "bollitore_pannello", lambda: pp.calcola_bollitore(db, da, ora, ora, 300))
        say(f"  Raffreddamento acqua calda: fasce a ricircolo spento «{args.ricircolo_off}», timer attivo dal {args.ricircolo_dal} "
            "(se il timer e' cambiato rilancia con --ricircolo-off e --ricircolo-dal)")
        _sezione(out, "raffreddamento_acs", lambda: _raffreddamento_acs(
            S, stati, ora, args.ricircolo_off, int(time.mktime(time.strptime(args.ricircolo_dal, "%Y-%m-%d %H:%M")))))
        _sezione(out, "sbrinamenti", lambda: json.loads(json.dumps(pp.elenco_sbrinamenti(db, da, ora, ora), default=str)))

        def ripartenza():
            num = lambda s: next((r[2] for r in ultimo if r[0] == s), None)         # noqa: E731
            tsx = lambda s: next((r[1] for r in ultimo if r[0] == s), None)         # noqa: E731
            diag = {}
            eta = pp.stima_ripartenza(type("A", (), {"db": db})(), ora, num("casa.temperatura"), tsx("casa.temperatura"),
                                      num("casa.richiesta_calore") == 1.0, num("ciclo.esterna"), num("casa.setpoint"), diag=diag)
            return json.loads(json.dumps({"stima": _ore(eta) if eta else None, "diag": diag}, default=str))
        _sezione(out, "ripartenza_prevista", ripartenza)

    # --- cambi di stato, eventi
    def cambi():
        per = {}
        for serie, ts, v in stati:
            if serie.startswith(("fsv.", "info.")):
                continue
            per.setdefault(serie, []).append([_ore(ts), v])
        return {"nota": "Ogni cambio di stato (stati testuali: modo, valvola, sbrinamento, acceso/spento...). Le ultime 400 righe per serie.",
                "serie": {k: v[-400:] for k, v in sorted(per.items())}}
    _sezione(out, "cambi_di_stato", cambi)
    _sezione(out, "eventi_e_allarmi", lambda: {"nota": "Allarmi/avvisi del pannello nel periodo (ultimi 300).",
                                               "per_codice": {k: sum(1 for e in eventi if e[1] == k) for k in sorted({e[1] for e in eventi})},
                                               "elenco": [{"quando": _ore(e[0]), "codice": e[1], "livello": e[2], "testo": e[3],
                                                           "chiuso": _ore(e[4]) if e[4] else None} for e in eventi[-300:]]})
    # --- serie nel tempo
    _sezione(out, "serie_mediate", lambda: {"nota": "Medie a passo fisso. Ogni serie e' una lista di pezzi {passo_s, da, valori[]}: il valore i e' la media tra da+i*passo_s e da+(i+1)*passo_s.",
                                             "serie": _griglia(S, ora, giorni)})
    if int(args.grezzi) > 0:
        _sezione(out, "misure_grezze", lambda: _grezzi(S, ora, int(args.grezzi)))
    if ciclo_raw:
        _sezione(out, "ciclo_grezzo", lambda: {
            "nota": "I campioni grezzi del ciclo (SmartThings ehsCycleData), uno ogni 5 minuti per tipo: tipo 'outdoor' (28 byte) e 'indoor' (31 byte), "
                    "ogni byte in esadecimale (2 caratteri). Servono per cercare byte non ancora decodificati (per esempio il target di mandata).",
            "campioni": [{"tipo": t, "ts": ts, "ora": _ore(ts), "hex": h} for t, ts, h in ciclo_raw]})
    out["sezioni"] = [k for k in out if k not in ("file", "errori")]

    nome = f"analisi_{datetime.fromtimestamp(ora):%Y%m%d_%H%M}.json"
    if args.uscita:                                              # lo usa il plugin «analisi»: scrive dove gli viene detto
        path, nome = Path(args.uscita), args.uscita
    else:
        cart = HERE / "diagnostica"
        cart.mkdir(exist_ok=True)
        path, nome = cart / nome, "diagnostica/" + nome
    testo = json.dumps(sanitize(out), ensure_ascii=False, separators=(",", ":"), default=str)
    path.write_text(testo, encoding="utf-8")
    kb = path.stat().st_size // 1024 + 1
    say(f"\nFatto. File salvato: {nome}  ({kb} KB)")
    if out.get("errori"):
        say("  Alcune sezioni non sono riuscite (l'errore e' scritto nel file): mandalo comunque.")
    if kb > 8000:
        say("  Il file e' grande: se la chat non lo accetta, rilancia con meno giorni, per esempio:  python pdc_strumenti.py analisi --giorni 14 --grezzi 0")


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
    d = sub.add_parser("st-capacita", help="salva i comandi che SmartThings espone per la pdc (non cambia nulla)")
    d.add_argument("--device", help="id del dispositivo (se non e' in config.json)")
    d = sub.add_parser("st-osserva", help="osserva i cambi di stato mentre premi Boost/programmi (solo lettura)")
    d.add_argument("--device", help="id del dispositivo (se non e' in config.json)")
    d.add_argument("--minuti", type=int, default=15, help="durata (default 15)")
    d.add_argument("--ogni", type=int, default=10, help="secondi tra una lettura e l'altra (default 10)")
    d.add_argument("--tutto", action="store_true", help="annota anche i dati che cambiano di continuo (ciclo, consumi, temperature)")
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
    w.add_argument("--sonda", type=int, default=20, help="ogni quanti secondi leggere anche homestatus per vedere i tuoi gesti (default 20, 0 = mai)")
    w.add_argument("--porta", type=int, default=0, help="porta locale del ricevitore (default: ne sceglie una libera da sola)")
    w.add_argument("--url", help="usa questo indirizzo pubblico (che deve arrivare alla --porta di questo PC) invece del tunnel temporaneo")
    w.add_argument("--metodo", choices=("vecchio", "nuovo"), default="vecchio",
                   help="vecchio = /api/addwebhook (default); nuovo = servizio /webhooks/v1/ (parte solo se l'elenco dei webhook e' vuoto)")
    w.add_argument("--sostituisci", action="store_true", help="con il metodo 'vecchio': procede anche se c'e' gia' un webhook registrato (verra' sostituito e poi tolto)")
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
    an = sub.add_parser("analisi", help="UN file con tutti i dati per l'analisi (legge il database, pannello anche acceso)")
    an.add_argument("--db", help="percorso del database (di solito lo trova da solo)")
    an.add_argument("--giorni", type=int, default=45, help="quanti giorni di storico includere (default 45, massimo 120)")
    an.add_argument("--ciclo", type=int, default=7, help="quanti giorni di campioni GREZZI del ciclo (byte del compressore) includere (default 7, 0 = nessuno)")
    an.add_argument("--grezzi", type=int, default=1, help="quanti giorni di misure GREZZE (non mediate) includere, gli ultimi (default 1, 0 = nessuna)")
    an.add_argument("--ricircolo-off", default="13:30-17:00,21:30-08:00", help="fasce a ricircolo SPENTO, tipo 13:30-17:00,21:30-08:00 "
                    "(default: quelle del timer del 5/11/2026; se lo cambi, indicale qui)")
    an.add_argument("--uscita", help="scrive il file in questo percorso invece che in diagnostica/ (lo usa il plugin «analisi»)")
    an.add_argument("--ricircolo-dal", default="2026-11-05 14:30", help="da quando valgono quelle fasce, 'AAAA-MM-GG HH:MM' (default 2026-11-05 14:30)")
    mp = sub.add_parser("meteo-prova", help="prova a leggere umidita' e temperatura da una pagina WeatherLink 'embeddable' (nessuna chiave, config.json non serve)")
    mp.add_argument("url", help="indirizzo della pagina, tipo https://www.weatherlink.com/embeddablePage/show/<codice>/summary")
    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return
    if args.cmd in ("rip-diag", "sbrinamenti", "meteo-prova", "analisi"):        # non serve config.json e non ne crea
        {"rip-diag": cmd_rip_diag, "sbrinamenti": cmd_sbrinamenti, "meteo-prova": cmd_meteo_prova, "analisi": cmd_analisi}[args.cmd](None, args)
        return
    cfg = load_cfg()
    if args.cmd == "nt-webhook-prova" and args.elimina:
        token = wh_token(cfg, 0, args.rinnova)
        if args.metodo == "nuovo":
            c, js = wh_chiama(token, "DELETE", "/webhooks/v1/")
            say(f"DELETE /webhooks/v1/: HTTP {c}: {json.dumps(js, ensure_ascii=False)[:300]}")
        else:
            c, js = wh_chiama(token, "POST", "/api/dropwebhook", {})
            say(f"dropwebhook: HTTP {c}: {json.dumps(js, ensure_ascii=False)[:300]}")
        c, js = wh_chiama(token, "GET", "/webhooks/v1/")
        say(f"webhook rimasti: HTTP {c}: {json.dumps(js, ensure_ascii=False)[:300]}")
        return
    {"st-devices": cmd_st_devices, "st-oauth": cmd_st_oauth,
     "st-dump": cmd_st_dump, "st-capacita": cmd_st_capacita, "st-osserva": cmd_st_osserva, "nt-dump": cmd_nt_dump,
     "st-prova-refresh": cmd_st_prova_refresh, "nt-webhook-prova": cmd_nt_webhook_prova}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
