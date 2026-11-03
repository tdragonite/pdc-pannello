#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pannello locale per pompa di calore Samsung EHS (SmartThings) + Smarther (Netatmo).

PEZZI FATTI: 1 nucleo, 2 web con Dashboard, 3 Analisi ed Efficienza, 4 Telegram, 5 Impostazioni modificabili, 6 tunnel Cloudflare, 7 storico compatto e README:
  - configurazione in config.json (stesso file di pdc_strumenti.py)
  - lettura periodica di SmartThings (pdc) e Netatmo (Smarther)
  - decodifica dei dati "ehsCycleData" (mandata, ritorno, esterna, portata, Hz, pompa)
  - database SQLite nella stessa cartella dello script
  - gestione errori/anomalie con eventi (a cui Telegram si agganciera' nel pezzo 4)

Solo libreria standard di Python (3.9 o piu' recente). Tutto vive nella cartella dello script.

Comandi:
    python pdc_pannello.py                      avvia raccolta dati + interfaccia web (porta 7000)
    python pdc_pannello.py imposta-password     sceglie utente e password per l'interfaccia web
    python pdc_pannello.py telegram-prova       manda un messaggio di prova su Telegram
    python pdc_pannello.py compatta             compatta subito lo storico vecchio
    python pdc_pannello.py --demo               prova l'interfaccia con dati finti, senza pdc
    python pdc_pannello.py --una-volta          una sola lettura, poi mostra i valori
    python pdc_pannello.py stato                mostra gli ultimi valori salvati
    python pdc_pannello.py ridecodifica         rifa' la decodifica dei dati ciclo gia' salvati
    python pdc_pannello.py esporta-diagnostica  crea diagnostica.json (da allegare in chat se serve)
"""

VERSIONE = "0.17"

import argparse
import base64
import copy
import hashlib
import ipaddress
import json
import logging
import logging.handlers
import os
import re
import socket
import sqlite3
import sys
import threading
import shutil
import time
import traceback
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

AQUI = Path(__file__).resolve().parent
ST_API = "https://api.smartthings.com/v1"
ST_BASE = "https://api.smartthings.com"
NT_BASE = "https://api.netatmo.com"

log = logging.getLogger("pdc")


# ==========================================================================
# DECODIFICA DEI DATI CICLO (samsungce.ehsCycleData)
# ==========================================================================
# Fonte: "Cycle Data Decoding Guide" di Dwellsee (ricavata per reverse engineering,
# Samsung non la documenta) + controllo sui dati reali di questa pdc.
# Il layout si sceglie dalla LUNGHEZZA del blob: per la tua pdc indoor = 31 byte, outdoor = 28.
# valore = (byte + offset) * scala.  Un byte uguale a uno dei "nulli" = dato non disponibile.
# Si puo' correggere tutto da config.json -> "mappa_ciclo" senza toccare il codice
# (poi:  python pdc_pannello.py ridecodifica).

def _t(byte, minimo=-20, massimo=100):
    """temperatura: byte - 55 gradi"""
    return {"byte": byte, "tipo": "u8", "offset": -55, "scala": 1, "min": minimo,
            "max": massimo, "nulli": [5, 255], "unita": "°C", "attivo": True}


def _u8(byte, unita, scala=1, minimo=0, massimo=100):
    return {"byte": byte, "tipo": "u8", "offset": 0, "scala": scala, "min": minimo,
            "max": massimo, "nulli": [255], "unita": unita, "attivo": True}


def _hz(byte):
    return {"byte": byte, "tipo": "u16", "offset": 0, "scala": 1, "min": 0, "max": 200,
            "nulli": [65535], "unita": "Hz", "attivo": True}


def _spegni(campo):
    c = dict(campo)
    c["attivo"] = False
    return c


_OUTDOOR = {
    "freq_compressore": _hz(0),
    "freq_target": _hz(2),
    "scarico": _t(5, -20, 150),
    "evaporazione": _t(6),
    "esterna": _t(8, -40, 60),
}

MAPPA_CICLO_DEFAULT = {
    "_nota": "valore = (byte + offset) * scala. Fonte: guida di decodifica Dwellsee. Per disattivare "
             "un campo metti \"attivo\": false. Dopo ogni modifica: python pdc_pannello.py ridecodifica",
    "outdoor_28": dict(_OUTDOOR, aspirazione=_t(7)),
    "outdoor_18": dict(_OUTDOOR),
    "indoor_31": {
        "ritorno": _t(2),
        "mandata": _t(3),
        "portata": _u8(7, "L/min", 0.1, 0, 60),
        "vel_pompa": _u8(8, "%"),
        "acs_ciclo": _t(27),
    },
    "indoor_36": {
        "ritorno": _t(2),
        "mandata": _t(3),
        "portata": _spegni(_u8(7, "L/min", 0.1, 0, 60)),  # inaffidabile su questo modello
        "vel_pompa": _u8(8, "%"),
    },
    "indoor_24": {
        "evap_ingresso": _t(0),
        "evap_uscita": _t(1),
        "ritorno": _t(2),
        "portata": _u8(7, "L/min", 1, 0, 60),
        "vel_pompa": _u8(8, "%"),
    },
}

_TIPI_CAMPO = {"u8": (1, False), "s8": (1, True), "u16": (2, False), "s16": (2, True)}


def decodifica_ciclo(tipo, hexstr, mappa):
    """tipo = 'indoor' | 'outdoor'.  Restituisce (layout, {nome: valore}, problema)."""
    try:
        b = bytes.fromhex(hexstr)
    except (ValueError, TypeError):
        return None, {}, "dati esadecimali non validi"
    chiave = f"{tipo}_{len(b)}"
    campi = mappa.get(chiave)
    if not isinstance(campi, dict):
        return None, {}, f"formato sconosciuto: {tipo} di {len(b)} byte"
    out = {}
    for nome, c in campi.items():
        if nome.startswith("_") or not isinstance(c, dict) or not c.get("attivo", True):
            continue
        try:
            n, con_segno = _TIPI_CAMPO[c.get("tipo", "u8")]
            i = int(c["byte"])
            if i + n > len(b):
                continue
            grezzo = int.from_bytes(b[i:i + n], "big", signed=con_segno)
            if grezzo in c.get("nulli", []):
                continue
            val = round((grezzo + c.get("offset", 0)) * c.get("scala", 1), 3)
            if c.get("min") is not None and val < c["min"]:
                continue
            if c.get("max") is not None and val > c["max"]:
                continue
            out[nome] = val
        except (KeyError, ValueError, TypeError):
            continue
    return chiave, out, None


# ==========================================================================
# ERRORI
# ==========================================================================

def riservato(percorso):
    """File leggibile solo dal proprietario (su Linux/Mac; su Windows non fa nulla): contiene password e token."""
    try:
        if os.name != "nt":
            os.chmod(percorso, 0o600)
    except OSError:
        pass


class ErroreConfig(Exception):
    """Manca qualcosa in config.json (l'utente lo puo' sistemare a programma acceso)."""


class ErroreAPI(Exception):
    """tipo: rete | auth | pagamento | limite | server | richiesta"""

    def __init__(self, tipo, http, messaggio):
        super().__init__(messaggio)
        self.tipo, self.http, self.messaggio = tipo, http, messaggio


def classifica(servizio, code, js):
    try:
        testo = json.dumps(js, ensure_ascii=False)[:300]
    except (TypeError, ValueError):
        testo = str(js)[:300]
    low = testo.lower()
    if code == 402 or (code in (401, 403) and any(w in low for w in
                                                  ("payment", "subscription", "billing", "quota"))):
        return ErroreAPI("pagamento", code, f"{servizio}: l'accesso alle API sembra richiedere un "
                                            f"abbonamento (HTTP {code}) {testo}")
    if code == 429 or "usage reached" in low or "too many" in low:
        return ErroreAPI("limite", code, f"{servizio}: troppe richieste (HTTP {code})")
    if code in (401, 403):
        return ErroreAPI("auth", code, f"{servizio}: accesso rifiutato (HTTP {code}): token scaduto "
                                       f"o permessi mancanti")
    if code >= 500:
        return ErroreAPI("server", code, f"{servizio}: errore del server (HTTP {code})")
    return ErroreAPI("richiesta", code, f"{servizio}: richiesta rifiutata (HTTP {code}) {testo}")


def http_json(metodo, url, headers=None, dati=None, timeout=30):
    """Unico punto di accesso alla rete (i test lo sostituiscono). -> (codice_http, json)"""
    body = urlencode(dati).encode() if isinstance(dati, dict) else dati
    req = Request(url, data=body, method=metodo, headers=headers or {})
    try:
        with urlopen(req, timeout=timeout) as r:
            raw, code = r.read().decode("utf-8", "replace"), r.status
    except HTTPError as e:
        raw, code = e.read().decode("utf-8", "replace"), e.code
    except (URLError, OSError) as e:  # include timeout e connessione rifiutata
        motivo = getattr(e, "reason", e)
        raise ErroreAPI("rete", 0, f"rete non raggiungibile: {motivo}")
    try:
        return code, (json.loads(raw) if raw.strip() else {})
    except ValueError:
        return code, {"raw": raw[:300]}


def iso_a_epoch(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp())


# ==========================================================================
# CONFIGURAZIONE
# ==========================================================================

TEMPLATE = {
    "_nota": "File di configurazione: modificalo con il Blocco note e salva; la maggior parte delle "
             "modifiche vale subito, senza riavviare. Non condividerlo mai (contiene password e token).",
    "generale": {
        "porta": 7000,
        "host": "0.0.0.0",
        "porta_lock": 7099,
        "intervallo_pdc_secondi": 60,
        "intervallo_netatmo_secondi": 120,
        "database": "dati/pdc.db",
        "sessione_giorni": 30,
    },
    "bollitore": {
        "_nota": "Litri del bollitore dell'acqua calda: servono solo a stimare in watt la perdita di calore (pagina Efficienza).",
        "litri": 300,
    },
    "umidita": {
        "_nota": "Umidita' in casa (dallo Smarther). Sopra 'giallo' compare il giallo in Dashboard e parte l'avviso Telegram 'Umidita' alta'; "
                 "sopra 'rosso' il rosso e l'avviso 'molto alta'. Telegram scrive solo se la soglia resta superata per 'minuti'. "
                 "L'avviso rientra quando scende di 'rientro_punti' sotto la soglia.",
        "giallo": 60.0, "rosso": 70.0, "minuti": 30, "rientro_punti": 2.0,
    },
    "tariffa": {
        "_nota": "Prezzo dell'energia elettrica in euro per kWh (con le tasse, per esempio 0.28). 0 = non mostrare i costi.",
        "prezzo_kwh": 0.0,
    },
    "accesso": {
        "_nota": "Scrivi utente e password in chiaro qui, al prossimo avvio vengono trasformati in un hash "
                 "e la password in chiaro viene cancellata. Oppure: python pdc_pannello.py imposta-password",
        "utente": "", "password": "", "password_hash": "",
    },
    "smartthings": {
        "device_id": "",
        "pat": "",
        "oauth": {"client_id": "", "client_secret": "", "access_token": "",
                  "refresh_token": "", "expires_at": 0},
    },
    "netatmo": {"client_id": "", "client_secret": "", "access_token": "", "refresh_token": "",
                "expires_at": 0, "home_id": "", "room_id": ""},
    "dispositivi": {
        "_nota": "Facoltativo. Gli IP servono solo per mostrare l'IP e ricavare il MAC nella pagina Dispositivi. "
                 "I loghi si mettono nella cartella 'dati/loghi' (smarther.svg, samsung.svg, ...).",
        "ip_pdc": "", "ip_smarther": "", "modello_smarther": "", "loghi_bianchi": False,
    },
    "telegram": {},        # riempito sotto da TELEGRAM_DEFAULT (parte Telegram)
    "allarmi": {},         # idem da ALLARMI_DEFAULT
    "cloudflare": {},      # idem da CLOUDFLARE_DEFAULT
    "mappa_ciclo": MAPPA_CICLO_DEFAULT,
}


def aggiungi_default_pezzi():
    """I valori predefiniti di Telegram e allarmi sono definiti nella loro parte del file."""
    TEMPLATE["telegram"] = copy.deepcopy(TELEGRAM_DEFAULT)
    TEMPLATE["allarmi"] = copy.deepcopy(ALLARMI_DEFAULT)
    TEMPLATE["cloudflare"] = copy.deepcopy(CLOUDFLARE_DEFAULT)


def unisci(base, extra):
    out = dict(base)
    for k, v in extra.items():
        out[k] = unisci(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


class Config:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def esiste(self):
        return self.path.exists()

    def _scrivi(self, raw):
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())  # se la luce va via subito dopo, il file nuovo e' davvero sul disco (contiene il refresh token)
        riservato(tmp)
        for tentativo in range(5):  # su Windows il file puo' essere momentaneamente occupato
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                time.sleep(0.3)
        raise ErroreConfig(f"Non riesco a scrivere {self.path.name}: e' aperto in un altro programma?")

    def completa(self):
        """Aggiunge al file le impostazioni nuove che mancano (coi valori predefiniti) senza toccare le tue."""
        def rec(raw, base):
            mod = False
            for k, v in base.items():
                if k not in raw:
                    raw[k] = copy.deepcopy(v)
                    mod = True
                elif isinstance(v, dict) and isinstance(raw[k], dict):
                    mod = rec(raw[k], v) or mod
            return mod
        with self.lock:
            raw = self._raw()
            if rec(raw, TEMPLATE):
                self._scrivi(raw)
                return True
        return False

    def crea_modello(self):
        with self.lock:
            self._scrivi(copy.deepcopy(TEMPLATE))

    def _raw(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            raise ErroreConfig(f"Manca {self.path.name}")
        except ValueError as e:
            raise ErroreConfig(f"{self.path.name} non e' un JSON valido ({e}). Controlla virgole e virgolette.")

    def leggi(self):
        with self.lock:
            return unisci(copy.deepcopy(TEMPLATE), self._raw())

    def aggiorna(self, fn):
        """Rilegge il file com'e' ORA (con le modifiche dell'utente), applica fn e riscrive."""
        with self.lock:
            raw = self._raw()
            fn(raw)
            self._scrivi(raw)


# ==========================================================================
# DATABASE
# ==========================================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS misure(serie TEXT NOT NULL, ts INTEGER NOT NULL, valore REAL NOT NULL,
    PRIMARY KEY(serie, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS stati(serie TEXT NOT NULL, ts INTEGER NOT NULL, valore TEXT NOT NULL,
    PRIMARY KEY(serie, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS ultimo(serie TEXT PRIMARY KEY, ts INTEGER NOT NULL, num REAL, txt TEXT);
CREATE TABLE IF NOT EXISTS ciclo(tipo TEXT NOT NULL, ts INTEGER NOT NULL, hex TEXT NOT NULL,
    PRIMARY KEY(tipo, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS eventi(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
    codice TEXT NOT NULL, livello TEXT NOT NULL, testo TEXT NOT NULL, chiuso_ts INTEGER);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS sessioni(h TEXT PRIMARY KEY, scade INTEGER NOT NULL);
"""


class Rec:
    """Cio' che una lettura ha prodotto, pronto da salvare."""

    def __init__(self):
        self.num = []      # (serie, ts, valore, lento)
        self.stati = {}    # serie -> testo
        self.ciclo = []    # (tipo, ts, hex)
        self.problemi = []  # problemi di decodifica


class ErroreDB(ErroreConfig):
    """Il file del database non si apre (danneggiato, non e' un database, disco con problemi)."""


class DB:
    HEARTBEAT = 900  # le grandezze "lente" (setpoint...) si risalvano se invariate dopo 15 min

    def __init__(self, path):
        self.path = str(path)
        self.lock = threading.RLock()
        try:
            with closing(self._c()) as c:
                c.execute("PRAGMA journal_mode=WAL")
                c.executescript(SCHEMA)
        except sqlite3.DatabaseError as e:
            raise ErroreDB(f"Il database ({self.path}) non si apre: {e}. Probabilmente e' danneggiato (blackout, disco pieno?). "
                           "Con il pannello fermo puoi rimettere l'ultima copia con:  python pdc_pannello.py ripristina") from e

    def _c(self):
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def _upsert_ultimo(c, serie, ts, num, txt):
        c.execute("INSERT INTO ultimo(serie,ts,num,txt) VALUES(?,?,?,?) "
                  "ON CONFLICT(serie) DO UPDATE SET ts=excluded.ts, num=excluded.num, txt=excluded.txt "
                  "WHERE excluded.ts >= ultimo.ts", (serie, ts, num, txt))

    def salva(self, rec, ts_lettura):
        """Salva una lettura. Restituisce l'elenco delle serie che hanno cambiato valore."""
        cambiati = []
        with self.lock, closing(self._c()) as c, c:
            for tipo, ts, hx in rec.ciclo:
                c.execute("INSERT OR IGNORE INTO ciclo VALUES(?,?,?)", (tipo, ts, hx))
            for serie, ts, val, lento in rec.num:
                if lento:
                    r = c.execute("SELECT ts, num FROM ultimo WHERE serie=?", (serie,)).fetchone()
                    if r and r[1] == val and ts - r[0] < self.HEARTBEAT:
                        continue
                c.execute("INSERT OR REPLACE INTO misure VALUES(?,?,?)", (serie, ts, val))
                self._upsert_ultimo(c, serie, ts, val, None)
                cambiati.append(serie)
            for serie, txt in rec.stati.items():
                r = c.execute("SELECT txt FROM ultimo WHERE serie=?", (serie,)).fetchone()
                if r is None or r[0] != txt:
                    c.execute("INSERT OR REPLACE INTO stati VALUES(?,?,?)", (serie, ts_lettura, txt))
                    cambiati.append(serie)
                c.execute("INSERT INTO ultimo(serie,ts,num,txt) VALUES(?,?,NULL,?) "
                          "ON CONFLICT(serie) DO UPDATE SET ts=excluded.ts, txt=excluded.txt",
                          (serie, ts_lettura, txt))
        return cambiati

    def ultimi(self):
        with closing(self._c()) as c:
            return c.execute("SELECT serie, ts, num, txt FROM ultimo ORDER BY serie").fetchall()

    def ridecodifica(self, mappa):
        with self.lock, closing(self._c()) as c, c:
            c.execute("DELETE FROM misure WHERE serie LIKE 'ciclo.%'")
            c.execute("DELETE FROM ultimo WHERE serie LIKE 'ciclo.%'")
            righe = c.execute("SELECT tipo, ts, hex FROM ciclo ORDER BY ts").fetchall()
            n = 0
            for tipo, ts, hx in righe:
                _, valori, _ = decodifica_ciclo(tipo, hx, mappa)
                for nome, v in valori.items():
                    c.execute("INSERT OR REPLACE INTO misure VALUES(?,?,?)", (f"ciclo.{nome}", ts, v))
                    self._upsert_ultimo(c, f"ciclo.{nome}", ts, v, None)
                    n += 1
            return len(righe), n

    # --- meta ---
    def meta_get(self, k, default=None):
        with closing(self._c()) as c:
            r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            return r[0] if r else default

    def meta_set(self, k, v):
        with self.lock, closing(self._c()) as c, c:
            c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, str(v)))

    # --- eventi ---
    def evento_aperto(self, codice):
        with closing(self._c()) as c:
            return c.execute("SELECT id FROM eventi WHERE codice=? AND chiuso_ts IS NULL",
                             (codice,)).fetchone() is not None

    def evento_inserisci(self, codice, livello, testo):
        with self.lock, closing(self._c()) as c, c:
            c.execute("INSERT INTO eventi(ts,codice,livello,testo) VALUES(?,?,?,?)",
                      (int(time.time()), codice, livello, testo))

    def eventi_chiudi(self, prefisso, esatto):
        """chiude gli eventi aperti; restituisce [(codice, testo)] di quelli chiusi"""
        with self.lock, closing(self._c()) as c, c:
            if esatto:
                righe = c.execute("SELECT id, codice, testo FROM eventi WHERE codice=? AND chiuso_ts IS NULL",
                                  (prefisso,)).fetchall()
            else:
                righe = c.execute("SELECT id, codice, testo FROM eventi WHERE codice LIKE ? AND chiuso_ts IS NULL",
                                  (prefisso + "%",)).fetchall()
            for i, _, _ in righe:
                c.execute("UPDATE eventi SET chiuso_ts=? WHERE id=?", (int(time.time()), i))
            return [(cod, t) for _, cod, t in righe]

    def eventi_recenti(self, n=50):
        with closing(self._c()) as c:
            return c.execute("SELECT ts, codice, livello, testo, chiuso_ts FROM eventi "
                             "ORDER BY id DESC LIMIT ?", (n,)).fetchall()


class Eventi:
    """Apre/chiude anomalie. Gli 'ascoltatori' (Telegram, pagina web) si registrano qui."""

    def __init__(self, db):
        self.db = db
        self.ascoltatori = []  # funzioni f(tipo, codice, livello, testo): tipo = 'apertura' | 'ripristino'

    def _emetti(self, tipo, codice, livello, testo):
        for f in list(self.ascoltatori):
            try:
                f(tipo, codice, livello, testo)
            except Exception:
                log.exception("ascoltatore eventi in errore")

    def apri(self, codice, testo, livello="errore"):
        if self.db.evento_aperto(codice):
            return
        self.db.evento_inserisci(codice, livello, testo)
        log.warning("ANOMALIA [%s] %s", codice, testo)
        self._emetti("apertura", codice, livello, testo)

    def chiudi(self, codice, esatto=True):
        for cod, testo in self.db.eventi_chiudi(codice, esatto):
            log.info("RIPRISTINATO [%s]", cod)
            self._emetti("ripristino", cod, "info", testo)

    def chiudi_prefisso(self, prefisso):
        self.chiudi(prefisso, esatto=False)


# ==========================================================================
# TOKEN
# ==========================================================================

def _basic(cid, secret):
    return "Basic " + base64.b64encode(f"{cid}:{secret}".encode()).decode()


class TokenST:
    """Token SmartThings: OAuth (si rinnova da solo) oppure PAT (24 ore, incollato a mano)."""

    def __init__(self, cfg, db):
        self.cfg, self.db = cfg, db
        self.lock = threading.Lock()
        self.forza = False

    def invalida(self):
        self.forza = True

    def usa_pat(self):
        oa = self.cfg.leggi()["smartthings"]["oauth"]
        return not (oa.get("client_id") and oa.get("client_secret") and oa.get("refresh_token"))

    def get(self):
        with self.lock:
            c = self.cfg.leggi()["smartthings"]
            oa = c["oauth"]
            if not self.usa_pat():
                if (not self.forza and oa.get("access_token")
                        and time.time() < float(oa.get("expires_at") or 0) - 300):
                    return oa["access_token"]
                return self._rinnova(oa)
            pat = (c.get("pat") or "").strip()
            if not pat or pat.startswith("your-"):
                raise ErroreConfig("SmartThings: manca il token. Incolla il token personale in config.json "
                                   "(smartthings -> pat) oppure configura OAuth.")
            h = hashlib.sha256(pat.encode()).hexdigest()[:16]
            if self.db.meta_get("st_pat_hash") != h:  # token nuovo: parte il conto delle 24 ore
                self.db.meta_set("st_pat_hash", h)
                self.db.meta_set("st_pat_ts", int(time.time()))
            return pat

    def eta_pat_ore(self):
        t = self.db.meta_get("st_pat_ts")
        return (time.time() - float(t)) / 3600 if t else None

    def _rinnova(self, oa):
        code, js = http_json(
            "POST", ST_BASE + "/oauth/token",
            {"Authorization": _basic(oa["client_id"], oa["client_secret"]),
             "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            {"grant_type": "refresh_token", "client_id": oa["client_id"],
             "refresh_token": oa["refresh_token"]})
        if code != 200 or "access_token" not in js:
            e = classifica("SmartThings", code, js)
            if e.tipo in ("richiesta", "auth"):
                raise ErroreAPI("auth", code, "SmartThings: il rinnovo del token OAuth e' stato rifiutato. "
                                              "Il refresh token e' scaduto o gia' usato: rifai "
                                              "'python pdc_strumenti.py st-oauth'.")
            raise e
        scad = int(time.time()) + int(js.get("expires_in", 86400))

        def salva(raw):  # SUBITO: il refresh token e' monouso, se si perde si ricomincia
            o = raw.setdefault("smartthings", {}).setdefault("oauth", {})
            o["access_token"] = js["access_token"]
            if js.get("refresh_token"):
                o["refresh_token"] = js["refresh_token"]
            o["expires_at"] = scad
        self.cfg.aggiorna(salva)
        self.forza = False
        return js["access_token"]


class TokenNT:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.forza = False

    def invalida(self):
        self.forza = True

    def get(self):
        with self.lock:
            n = self.cfg.leggi()["netatmo"]
            for k in ("client_id", "client_secret", "refresh_token"):
                if not n.get(k):
                    raise ErroreConfig(f"Netatmo: manca '{k}' in config.json (sezione netatmo).")
            if (not self.forza and n.get("access_token")
                    and time.time() < float(n.get("expires_at") or 0) - 120):
                return n["access_token"]
            code, js = http_json(
                "POST", NT_BASE + "/oauth2/token",
                {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                {"grant_type": "refresh_token", "refresh_token": n["refresh_token"],
                 "client_id": n["client_id"], "client_secret": n["client_secret"]})
            if code != 200 or "access_token" not in js:
                e = classifica("Netatmo", code, js)
                if e.tipo in ("richiesta", "auth"):
                    raise ErroreAPI("auth", code, "Netatmo: il rinnovo del token e' stato rifiutato. Se hai "
                                                  "rigenerato i token dal portale, copia in config.json quelli NUOVI.")
                raise e
            scad = int(time.time()) + int(js.get("expires_in", 10800))

            def salva(raw):
                o = raw.setdefault("netatmo", {})
                o["access_token"] = js["access_token"]
                if js.get("refresh_token"):
                    o["refresh_token"] = js["refresh_token"]
                o["expires_at"] = scad
            self.cfg.aggiorna(salva)
            self.forza = False
            return js["access_token"]


# ==========================================================================
# ESTRAZIONE DEI VALORI
# ==========================================================================

def _v(status, comp, cap, attr):
    try:
        return status["components"][comp][cap][attr].get("value")
    except (KeyError, TypeError, AttributeError):
        return None


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def estrai_st(status, mappa, ts):
    """Stato SmartThings -> Rec. Nomi delle serie in italiano, stabili nel tempo."""
    rec = Rec()

    def n(serie, val, lento=False, t=None):
        val = _num(val)
        if val is not None:
            rec.num.append((serie, t or ts, val, lento))

    def s(serie, val):
        if val is not None and val != "":
            rec.stati[serie] = str(val).lower() if isinstance(val, bool) else str(val)

    n("acs.temperatura", _v(status, "main", "temperatureMeasurement", "temperature"))
    n("acs.setpoint", _v(status, "main", "thermostatCoolingSetpoint", "coolingSetpoint"), lento=True)
    n("risc.acqua", _v(status, "INDOOR", "temperatureMeasurement", "temperature"))
    n("risc.setpoint", _v(status, "INDOOR", "thermostatCoolingSetpoint", "coolingSetpoint"), lento=True)

    pc = _v(status, "main", "powerConsumptionReport", "powerConsumption")
    if isinstance(pc, dict):
        fine = iso_a_epoch(pc.get("end")) or ts
        inizio = iso_a_epoch(pc.get("start"))
        kw = _num(pc.get("power"))
        if kw is not None:
            n("pot.watt", kw * 1000, t=fine)  # 'power' e' in kW
        n("en.totale_wh", pc.get("energy"), t=fine)
        n("en.delta_wh", pc.get("deltaEnergy"), t=fine)
        if inizio:
            n("en.finestra_s", fine - inizio, t=fine)

    s("pdc.acceso", _v(status, "main", "switch", "switch"))
    s("risc.acceso", _v(status, "INDOOR", "switch", "switch"))
    s("pdc.modo", _v(status, "main", "airConditionerMode", "airConditionerMode"))
    s("risc.modo", _v(status, "INDOOR", "airConditionerMode", "airConditionerMode"))
    s("pdc.valvola", _v(status, "main", "samsungce.ehsDiverterValve", "position"))
    s("pdc.sbrinamento", _v(status, "INDOOR", "samsungce.ehsDefrostMode", "status"))
    s("pdc.resistenza", _v(status, "main", "samsungce.ehsBoosterHeater", "status"))
    s("pdc.riferimento_temp", _v(status, "INDOOR", "samsungce.ehsTemperatureReference", "temperatureReference"))

    # identita' e versioni (pagina Dispositivi): cambiano di rado, la tabella "stati" ne tiene lo storico
    def ocf(k):
        return _v(status, "main", "ocf", k)

    def ident(k):
        return _v(status, "main", "samsungce.deviceIdentification", k)
    s("info.pdc.firmware", ocf("mnfv"))
    s("info.pdc.modello", ocf("mnmo"))
    s("info.pdc.nome", ocf("n"))
    s("info.pdc.id_ocf", ocf("di"))
    s("info.pdc.vid", ocf("vid"))
    s("info.pdc.piattaforma", " ".join(x for x in (ocf("mnos"), ocf("mnpv")) if x))
    s("info.pdc.binary", ident("binaryId"))
    s("info.pdc.descrizione", ident("description"))
    s("info.pdc.anno", ident("releaseYear"))
    s("info.pdc.seriale", ident("serialNumber"))
    s("info.pdc.driver", _v(status, "main", "samsungce.driverVersion", "versionNumber"))
    versioni = _v(status, "main", "samsungce.softwareVersion", "versions")
    if isinstance(versioni, list) and versioni:
        s("info.pdc.versioni", json.dumps([[x.get("swType"), x.get("versionNumber"), x.get("description")]
                                           for x in versioni if isinstance(x, dict)], ensure_ascii=False))
    s("info.pdc.aggiornamento", _v(status, "main", "samsungce.softwareUpdate", "newVersionAvailable"))

    fsv = _v(status, "main", "samsungce.ehsFsvSettings", "fsvSettings")
    if isinstance(fsv, list):
        for f in fsv:
            if isinstance(f, dict) and f.get("id") is not None and f.get("value") is not None:
                s(f"fsv.{f['id']}", f["value"])

    for tipo in ("outdoor", "indoor"):
        campioni = _v(status, "main", "samsungce.ehsCycleData", tipo)
        for cmp_ in campioni if isinstance(campioni, list) else []:
            t = iso_a_epoch(cmp_.get("timestamp")) if isinstance(cmp_, dict) else None
            hx = cmp_.get("data") if isinstance(cmp_, dict) else None
            if not t or not hx:
                continue
            rec.ciclo.append((tipo, t, hx))
            layout, valori, problema = decodifica_ciclo(tipo, hx, mappa)
            if problema:
                rec.problemi.append(problema)
            for nome, v in valori.items():
                rec.num.append((f"ciclo.{nome}", t, v, False))
    return rec


def estrai_netatmo(js, room_id, ts):
    rec = Rec()
    home = (js.get("body") or {}).get("home") or {}
    stanze, moduli = home.get("rooms") or [], home.get("modules") or []
    stanza = next((r for r in stanze if str(r.get("id")) == str(room_id)), stanze[0] if stanze else None)
    modulo = next((m for m in moduli if m.get("type") == "BNS"), moduli[0] if moduli else None)

    def n(serie, val, lento=False):
        val = _num(val)
        if val is not None:
            rec.num.append((serie, ts, val, lento))

    if stanza:
        n("casa.temperatura", stanza.get("therm_measured_temperature"))
        n("casa.umidita", stanza.get("humidity"))
        n("casa.setpoint", stanza.get("therm_setpoint_temperature"), lento=True)
        n("casa.richiesta_pct", stanza.get("heating_power_request"))
        if stanza.get("id"):
            rec.stati["info.nt.stanza_id"] = str(stanza["id"])
        if stanza.get("therm_setpoint_mode"):
            rec.stati["casa.modo"] = str(stanza["therm_setpoint_mode"])
        if "open_window" in stanza:
            rec.stati["casa.finestra_aperta"] = "si" if stanza["open_window"] else "no"
        if "reachable" in stanza:
            rec.stati["casa.raggiungibile"] = "si" if stanza["reachable"] else "no"
    if modulo:
        if "boiler_status" in modulo:
            n("casa.richiesta_calore", 1.0 if modulo["boiler_status"] else 0.0)
        n("casa.wifi", modulo.get("wifi_strength"), lento=True)
        if modulo.get("firmware_revision") is not None:
            rec.stati["info.nt.firmware"] = str(modulo["firmware_revision"])
        if modulo.get("id"):
            rec.stati["info.nt.id"] = str(modulo["id"])
        if modulo.get("type"):
            rec.stati["info.nt.tipo"] = str(modulo["type"])
    if home.get("id"):
        rec.stati["info.nt.casa_id"] = str(home["id"])
    return rec


def estrai_netatmo_casa(js):
    """Dati descrittivi (cambiano di rado) da /api/homesdata: nome del modulo, stanza, data di installazione."""
    rec = Rec()
    homes = (js.get("body") or {}).get("homes") or []
    if not homes:
        return rec
    home = homes[0]
    moduli = home.get("modules") or []
    mod = next((m for m in moduli if m.get("type") == "BNS"), moduli[0] if moduli else None)

    def s(serie, v):
        if v not in (None, ""):
            rec.stati[serie] = str(v)
    s("info.nt.casa", home.get("name"))
    s("info.nt.modo_termico", home.get("therm_mode"))
    s("info.nt.controllo", home.get("temperature_control_mode"))
    if mod:
        s("info.nt.nome", mod.get("name"))
        s("info.nt.installato", mod.get("setup_date"))
        stanza = next((r for r in home.get("rooms") or [] if str(r.get("id")) == str(mod.get("room_id"))), None)
        if stanza:
            s("info.nt.stanza", stanza.get("name"))
    return rec


# ==========================================================================
# RACCOLTA DATI (un thread per servizio: se uno si rompe l'altro continua)
# ==========================================================================

NOMI = {"st": "SmartThings", "nt": "Netatmo"}
SOGLIA_ERRORI = {"auth": 1, "pagamento": 1}  # gli altri errori: 3 di fila prima di segnalare


class Raccoglitore(threading.Thread):
    def __init__(self, app, sigla):
        super().__init__(daemon=True, name=f"raccolta-{sigla}")
        self.app, self.sigla = app, sigla
        self.fermo = threading.Event()
        self.sveglia = threading.Event()  # "rileggi subito" (dopo un comando)
        self.errori = 0
        self.stato = {"ultimo_ok": None, "ultimo_errore": None, "messaggio": None}

    def intervallo(self):
        raise NotImplementedError

    def passo(self):
        raise NotImplementedError

    def esegui_una_volta(self):
        """Una lettura con gestione errori. Restituisce i secondi da aspettare."""
        ev, s = self.app.eventi, self.sigla
        try:
            cambiati = self.passo()
            self.errori = 0
            self.stato.update(ultimo_ok=int(time.time()), messaggio=None)
            ev.chiudi_prefisso(f"{s}_conn_")
            ev.chiudi(f"{s}_config")
            ev.chiudi(f"{s}_script")
            for cb in list(self.app.bus):
                try:
                    cb(cambiati)
                except Exception:
                    log.exception("errore in un ascoltatore dati")
            return self.intervallo()
        except ErroreConfig as e:
            self.stato.update(ultimo_errore=int(time.time()), messaggio=str(e))
            ev.apri(f"{s}_config", str(e), "errore")
            return 60
        except ErroreAPI as e:
            self.errori += 1
            self.stato.update(ultimo_errore=int(time.time()), messaggio=e.messaggio)
            log.warning("%s: %s", NOMI[s], e.messaggio)
            if self.errori >= SOGLIA_ERRORI.get(e.tipo, 3):
                ev.apri(f"{s}_conn_{e.tipo}", e.messaggio, "errore")
            base = self.intervallo()
            if e.tipo in ("auth", "pagamento", "limite"):
                return max(300, base)
            return min(600, base * 2 ** min(self.errori, 3))
        except Exception:
            tb = traceback.format_exc()
            log.error("errore interno in %s:\n%s", NOMI[s], tb)
            riga = tb.strip().splitlines()[-1]
            self.stato.update(ultimo_errore=int(time.time()), messaggio=riga)
            ev.apri(f"{s}_script", f"Errore interno dello script ({NOMI[s]}): {riga}", "errore")
            return 120

    def run(self):
        while not self.fermo.is_set():
            try:
                attesa = self.esegui_una_volta()
            except Exception:
                # per esempio disco pieno: perfino segnalare l'errore nel database fallisce. Il thread non deve morire.
                log.exception("errore grave in %s (il database e' scrivibile?); riprovo tra poco", NOMI[self.sigla])
                self.stato.update(ultimo_errore=int(time.time()), messaggio="Errore grave: controlla spazio su disco e registro")
                attesa = 120
            fine = time.time() + attesa
            while not self.fermo.is_set() and not self.sveglia.is_set() and time.time() < fine:
                self.fermo.wait(1)
            self.sveglia.clear()


class ServizioST(Raccoglitore):
    def __init__(self, app):
        super().__init__(app, "st")
        self.passi = 0
        self.offline = 0

    def intervallo(self):
        # SmartThings concede circa 12 richieste/minuto: sotto i 20 secondi non scendiamo
        return max(20, int(self.app.cfg.leggi()["generale"]["intervallo_pdc_secondi"]))

    def _get(self, path):
        for tentativo in (1, 2):
            token = self.app.token_st.get()
            code, js = http_json("GET", ST_API + path,
                                 {"Authorization": "Bearer " + token, "Accept": "application/json"})
            if code == 401 and tentativo == 1 and not self.app.token_st.usa_pat():
                self.app.token_st.invalida()
                continue
            if code >= 400:
                raise classifica("SmartThings", code, js)
            return js

    def _trova_device(self):
        items = self._get("/devices").get("items", [])
        ehs = [d for d in items if any(
            c.get("id", "").startswith("samsungce.ehs")
            for comp in d.get("components", []) for c in comp.get("capabilities", []))]
        if len(ehs) != 1:
            elenco = ", ".join(f"{d.get('label') or d.get('name')} ({d['deviceId']})" for d in items)
            raise ErroreConfig("SmartThings: non riesco a capire quale sia la pdc. Scrivi il device_id in "
                               "config.json (smartthings -> device_id). Dispositivi: " + elenco)
        dev = ehs[0]["deviceId"]
        self.app.cfg.aggiorna(lambda raw: raw.setdefault("smartthings", {}).update(device_id=dev))
        log.info("pdc trovata e salvata in config.json")
        return dev

    def passo(self):
        cfg = self.app.cfg.leggi()
        dev = cfg["smartthings"]["device_id"] or self._trova_device()
        status = self._get(f"/devices/{dev}/status")
        ts = int(time.time())
        rec = estrai_st(status, cfg["mappa_ciclo"], ts)
        cambiati = self.app.db.salva(rec, ts)
        self._misura_ritardo(rec, ts)
        if rec.problemi:
            self.app.eventi.apri("st_ciclo_formato", "Formato dei dati ciclo non riconosciuto "
                                 f"({rec.problemi[0]}): forse un aggiornamento della pdc. Fammi vedere "
                                 "diagnostica.json.", "avviso")
        elif rec.ciclo:
            self.app.eventi.chiudi("st_ciclo_formato")
        if self.passi % 5 == 0:
            self._salute(dev, ts)
        if time.time() - getattr(self, "ult_info", 0) > 3600:
            self.ult_info = time.time()
            self._info_dispositivo(dev, ts)
        self.passi += 1
        self._controlla_pat()
        return cambiati

    def _misura_ritardo(self, rec, ts):
        """Diagnostica: quanto e' vecchio il campione del ciclo quando ARRIVA (serie diag.ritardo_ciclo, in secondi)."""
        try:
            if not rec.ciclo:
                return
            camp = max(t for _, t, _ in rec.ciclo)
            ultimo = float(self.app.db.meta_get("ciclo_ultimo_ts", 0) or 0)
            if camp > ultimo:
                r = Rec()
                r.num.append(("diag.ritardo_ciclo", ts, float(max(0, ts - camp)), False))
                self.app.db.salva(r, ts)
                self.app.db.meta_set("ciclo_ultimo_ts", int(camp))
        except Exception:
            log.exception("misura del ritardo non riuscita")

    def _info_dispositivo(self, dev, ts):
        """Dati di registrazione della pompa su SmartThings (non stanno nello 'status'). Sono accessori: se falliscono
        non e' un problema."""
        try:
            d = self._get(f"/devices/{dev}")
        except ErroreAPI:
            return
        rec = Rec()
        reg = iso_a_epoch(d.get("createTime")) if isinstance(d, dict) else None
        if reg:
            rec.stati["info.pdc.registrata"] = str(reg)
        if isinstance(d, dict) and d.get("deviceTypeName"):
            rec.stati["info.pdc.tipo"] = str(d["deviceTypeName"])
        if rec.stati:
            self.app.db.salva(rec, ts)

    def _salute(self, dev, ts):
        try:
            stato = str(self._get(f"/devices/{dev}/health").get("state", "")).upper()
        except ErroreAPI:
            return  # se e' un problema vero lo vede gia' la lettura principale
        rec = Rec()
        rec.stati["pdc.salute"] = stato.lower()
        self.app.db.salva(rec, ts)
        self.offline = 0 if stato == "ONLINE" else self.offline + 1
        if self.offline >= 2:
            self.app.eventi.apri("st_pdc_offline", "SmartThings dice che la pompa di calore e' OFFLINE "
                                 "(wifi, router o modulo di comunicazione).", "errore")
        elif self.offline == 0:
            self.app.eventi.chiudi("st_pdc_offline")

    def _controlla_pat(self):
        if not self.app.token_st.usa_pat():
            self.app.eventi.chiudi("st_pat")
            return
        eta = self.app.token_st.eta_pat_ore()
        if eta is not None and eta > 22:
            self.app.eventi.apri("st_pat", "Il token personale SmartThings ha oltre 22 ore (dura 24): sta per "
                                 "scadere. Generane uno nuovo su account.smartthings.com/tokens e incollalo in "
                                 "config.json.", "avviso")
        else:
            self.app.eventi.chiudi("st_pat")


class ServizioNT(Raccoglitore):
    def __init__(self, app):
        super().__init__(app, "nt")

    def intervallo(self):
        return max(60, int(self.app.cfg.leggi()["generale"]["intervallo_netatmo_secondi"]))

    def _get(self, path, params=None):
        url = NT_BASE + path + ("?" + urlencode(params) if params else "")
        for tentativo in (1, 2):
            token = self.app.token_nt.get()
            code, js = http_json("GET", url, {"Authorization": "Bearer " + token, "Accept": "application/json"})
            if code in (401, 403) and tentativo == 1:
                self.app.token_nt.invalida()
                continue
            if code >= 400 or (isinstance(js, dict) and "error" in js):
                raise classifica("Netatmo", code if code >= 400 else 400, js)
            return js

    def passo(self):
        cfg = self.app.cfg.leggi()["netatmo"]
        home = cfg.get("home_id")
        if not home:
            homes = (self._get("/api/homesdata").get("body") or {}).get("homes") or []
            if not homes:
                raise ErroreConfig("Netatmo: nessuna casa trovata: al token mancano gli scope dello Smarther.")
            home = homes[0]["id"]
            self.app.cfg.aggiorna(lambda raw: raw.setdefault("netatmo", {}).update(home_id=home))
        js = self._get("/api/homestatus", {"home_id": home})
        ts = int(time.time())
        if ts - getattr(self, "ult_info", 0) > 120:  # la modalita' della casa (hg = spento) sta solo in homesdata
            self.ult_info = ts
            try:
                self.app.db.salva(estrai_netatmo_casa(self._get("/api/homesdata")), ts)
            except ErroreAPI:
                pass  # accessorio: se fallisce non e' un problema
        rec = estrai_netatmo(js, cfg.get("room_id"), ts)
        if not rec.num and not rec.stati:
            raise ErroreAPI("richiesta", 200, "Netatmo: risposta senza dati dello Smarther")
        return self.app.db.salva(rec, ts)


def cartelle_loghi(base):
    """Dove cercare i loghi: dati/loghi (nuova) e, per abitudine, anche la vecchia 'loghi' accanto allo script."""
    base = Path(base)
    return [base / "dati" / "loghi", base / "loghi"]


def cartella_log(base):
    p = Path(base) / "log"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def _sposta(src, dst):
    """Sposta un file o una cartella senza mai sovrascrivere nulla. Una cartella gia' presente nella destinazione
    non si cancella: ci si aggiungono solo i file che mancano. -> True se qualcosa e' stato spostato."""
    src, dst = Path(src), Path(dst)
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir() and dst.is_dir():
        fatto = False
        for f in list(src.iterdir()):
            if not (dst / f.name).exists():
                os.replace(f, dst / f.name)
                fatto = True
        try:
            src.rmdir()
        except OSError:
            pass
        return fatto
    if dst.exists():
        return False
    os.replace(src, dst)
    return True


def riordina_cartelle(base, cfg):
    """Dalla vecchia disposizione (tutto accanto allo script) alle sottocartelle dati/, log/, cloudflared/.
    Sposta e basta (stesso disco: un rinomina, non una copia), mai sovrascrive; il database si sposta con i suoi -wal/-shm
    tutto insieme o per niente. Si fa solo con il pannello fermo.
    -> (codice, righe): codice 0 = si puo' proseguire, altrimenti il programma deve fermarsi (con le righe da mostrare)."""
    base = Path(base)
    righe = []
    try:
        g = cfg.leggi()
    except ErroreConfig:
        return 0, []                       # il config rotto lo segnala chi lo usa dopo
    gen, bk = g.get("generale", {}), g.get("backup") or {}
    db_vecchio = str(gen.get("database", "")) == "pdc.db"
    bk_vecchio = str(bk.get("cartella", "")) == "backup"
    lavori = []                            # (descrizione, src, dst)
    db_files = [(base / n, base / "dati" / n) for n in ("pdc.db", "pdc.db-wal", "pdc.db-shm")] if db_vecchio else []
    if db_vecchio and (base / "pdc.db").exists() and (base / "dati" / "pdc.db").exists():
        return 1, ["Trovo due database: pdc.db (vecchio) e dati/pdc.db (nuovo). Non decido io quale sia quello giusto.",
                   "Tieni quello con i dati e sposta l'altro altrove (per esempio rinominandolo), poi rilancia."]
    for s, d in db_files:
        if s.exists():
            lavori.append(("database", s, d))
    if bk_vecchio and (base / "backup").exists():
        lavori.append(("backup", base / "backup", base / "dati" / "backup"))
    if (base / "loghi").is_dir():
        lavori.append(("loghi", base / "loghi", base / "dati" / "loghi"))
    for nome in ("pdc_pannello.log", "pdc_pannello.log.1", "pdc_pannello.log.2", "pdc_pannello.log.3"):
        if (base / nome).exists() and not (base / "log" / nome).exists():
            lavori.append(("log", base / nome, base / "log" / nome))
    for f in sorted(base.glob("dump_*.json")):          # i file di diagnosi (strumenti e esporta-diagnostica) hanno una cartella a parte
        if not (base / "diagnostica" / f.name).exists():
            lavori.append(("diagnostica", f, base / "diagnostica" / f.name))
    if (base / "dati" / "diagnostica.json").exists() and not (base / "diagnostica" / "diagnostica.json").exists():
        lavori.append(("diagnostica", base / "dati" / "diagnostica.json", base / "diagnostica" / "diagnostica.json"))
    if (base / "cloudflared.log").exists() and not (base / "log" / "cloudflared.log").exists():
        lavori.append(("log", base / "cloudflared.log", base / "log" / "cloudflared.log"))
    if (base / "cloudflared.pid").exists() and not (base / "cloudflared" / "cloudflared.pid").exists():
        lavori.append(("tunnel", base / "cloudflared.pid", base / "cloudflared" / "cloudflared.pid"))
    if not lavori and not db_vecchio and not bk_vecchio:
        return 0, []
    if lavori:
        # sposto file che potrebbero essere in uso: serve la certezza che il pannello non sia acceso
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", int(gen.get("porta_lock", 7099))))
        except OSError:
            return 1, ["Un'altra copia del pannello e' accesa: prima di aggiornare le cartelle chiudila, poi rilancia."]
        finally:
            s.close()
    fatti = []
    try:
        # il database va spostato intero, o per niente
        for desc, s, d in lavori:
            if desc != "database":
                continue
            d.parent.mkdir(parents=True, exist_ok=True)
            if d.exists():
                raise OSError(f"{d.name} esiste gia' in dati/")
            os.replace(s, d)
            fatti.append((s, d))
    except OSError as e:
        for s, d in reversed(fatti):
            try:
                os.replace(d, s)
            except OSError:
                pass
        return 1, [f"Non riesco a spostare il database in dati/ ({e}). Non ho toccato nulla: e' aperto in un altro programma?"]
    if fatti:
        righe.append("database spostato in dati/")
    for desc, s, d in lavori:
        if desc == "database":
            continue
        try:
            if _sposta(s, d):
                righe.append(f"{s.name} -> {d.parent.relative_to(base)}/")
        except OSError as e:
            righe.append(f"{s.name} lasciato dov'era ({e})")
    try:
        def agg(raw):
            if db_vecchio:
                raw.setdefault("generale", {})["database"] = "dati/pdc.db"
            if bk_vecchio and isinstance(raw.get("backup"), dict):
                raw["backup"]["cartella"] = "dati/backup"
        cfg.aggiorna(agg)
    except (ErroreConfig, OSError) as e:
        for s, d in reversed(fatti):
            try:
                os.replace(d, s)
            except OSError:
                pass
        return 1, [f"Non riesco ad aggiornare config.json ({e}): ho rimesso il database dov'era."]
    return 0, ([("Cartelle riordinate: " + "; ".join(righe))] if righe else [])


class App:
    def __init__(self, base=AQUI, demo=False, tollera_db=False):
        self.base = Path(base)
        self.demo = demo
        self.cfg = Config(self.base / "config.json")
        c = self.cfg.leggi()
        dbp = Path("dati/demo.db" if demo else c["generale"]["database"])
        dbp = dbp if dbp.is_absolute() else self.base / dbp
        try:
            dbp.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._lock_sock = None
        try:
            self.db = DB(dbp)
        except ErroreDB:
            if not tollera_db:
                raise
            # solo per il comando 'ripristina': serve proprio quando il database non si apre
            self.db = type("DBGuasto", (), {"path": str(dbp)})()
            self.servizi = []
            return
        self.diffusione = Diffusione()  # tempo reale verso le pagine aperte (definita nella parte web)
        self.eventi = Eventi(self.db)
        self.bus = []  # funzioni f(serie_cambiate): il tempo reale del pezzo 2 si aggancia qui
        self.token_st = TokenST(self.cfg, self.db)
        self.token_nt = TokenNT(self.cfg)
        self.servizi = [] if demo else [ServizioST(self), ServizioNT(self)]
        self._lock_sock = None

    def intervallo_pdc(self):
        if self.demo:
            return 300
        return max(20, int(self.cfg.leggi()["generale"]["intervallo_pdc_secondi"]))

    def blocca_istanza(self):
        """Una sola copia alla volta: il refresh token e' monouso, due copie lo brucerebbero."""
        porta = int(self.cfg.leggi()["generale"]["porta_lock"])
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", porta))
            s.listen(1)
        except OSError:
            s.close()
            return False
        self._lock_sock = s
        return True

    def sblocca_istanza(self):
        if self._lock_sock is not None:
            self._lock_sock.close()
            self._lock_sock = None

    def avvia(self):
        for s in self.servizi:
            s.start()

    def ferma(self):
        for s in self.servizi:
            s.fermo.set()


