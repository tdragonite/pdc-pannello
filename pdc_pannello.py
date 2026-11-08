#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pannello locale per pompa di calore Samsung EHS (SmartThings) + Smarther (Netatmo).

Cosa fa:
  - legge a intervalli SmartThings (pompa) e Netatmo (Smarther) e salva tutto in SQLite (dati/pdc.db)
  - decodifica i dati "ehsCycleData" (mandata, ritorno, esterna, portata, Hz, pompa)
  - serve il pannello web (Dashboard, Analisi, Efficienza, Dispositivi, Impostazioni, Changelog)
  - avvisi Telegram, tunnel Cloudflare, plugin (Alexa, widget), backup e compattazione notturni
  - configurazione in config.json (stesso file di pdc_strumenti.py)

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

VERSIONE = "0.25.1"
PASSWORD_MIN = 10        # lunghezza minima della password del pannello

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
import types
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
    "outdoor_28": dict(_OUTDOOR, aspirazione=_t(7), scarico=_t(4, -20, 150)),     # su questa pdc lo scarico e' nel byte 4 (il 5 e' sempre 255)
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
    "meteo": {
        "_nota": "Posizione per il meteo del giorno nella Dashboard (card Esterna), da Open-Meteo. Ora c'è Cirié. Per toglierlo svuota le due coordinate ('').",
        "latitudine": 45.23,
        "longitudine": 7.60,
    },
    "stazione": {
        "_nota": "Facoltativo. Indirizzo dei DATI di una stazione meteo WeatherLink vicina (non quello della pagina: lo stampa 'python pdc_strumenti.py meteo-prova'). "
                 "Il pannello ne legge l'umidita' esterna ogni 10 minuti e la mostra nella card Esterna. Vuoto = funzione spenta.",
        "url": "",
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
        "refresh": {
            "_nota": "Facoltativo. SmartThings consegna i dati del compressore con 4-5 minuti di ritardo. Con 'attivo' il pannello, "
                     "SOLO mentre la pompa lavora, le chiede di aggiornarsi (comando 'refresh') ogni 'ogni_secondi' secondi. "
                     "Non cambia nessuna impostazione della pompa; serve il permesso di comando del token (x:devices:*).",
            "attivo": False, "ogni_secondi": 60,
        },
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
    "plugin": {},          # una sottosezione per plugin (cartella plugin): {"nome": {"attivo": false, ...}}
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


# Testi predefiniti che una versione ha cambiato: (percorso nel config.json, testo della versione precedente). Vedi Config.completa().
TESTI_PREDEFINITI_VECCHI = [
    (("telegram", "messaggi", "sbrinamento_inizio"), "🧊 <b>PdC in sbrinamento</b> — esterna {esterna}, dalle {ora}."),
    (("telegram", "messaggi", "sbrinamento_fine"), "✅ <b>Sbrinamento finito</b> dopo {durata}, {consumo}."),
]


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
        def rinnova(raw):
            """Un testo predefinito cambiato in una versione nuova: si aggiorna SOLO se nel file c'e' ancora identico quello vecchio
            (se l'hai personalizzato non si tocca)."""
            mod = False
            for percorso, vecchio in TESTI_PREDEFINITI_VECCHI:
                dove, nuovo = raw, TEMPLATE
                for k in percorso[:-1]:
                    dove, nuovo = dove.get(k) if isinstance(dove, dict) else None, nuovo.get(k) if isinstance(nuovo, dict) else None
                if isinstance(dove, dict) and isinstance(nuovo, dict) and dove.get(percorso[-1]) == vecchio and nuovo.get(percorso[-1]) not in (None, vecchio):
                    dove[percorso[-1]] = nuovo[percorso[-1]]
                    mod = True
            return mod
        with self.lock:
            raw = self._raw()
            mod = rec(raw, TEMPLATE)
            mod = rinnova(raw) or mod
            if mod:
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
CREATE TABLE IF NOT EXISTS finestre(da INTEGER PRIMARY KEY, a INTEGER NOT NULL, calo REAL, ah REAL,
    stato TEXT NOT NULL, manuale INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessioni(h TEXT PRIMARY KEY, scade INTEGER NOT NULL);
"""


class Rec:
    """Cio' che una lettura ha prodotto, pronto da salvare."""

    def __init__(self):
        self.num = []      # (serie, ts, valore, lento)
        self.stati = {}    # serie -> testo
        self.ciclo = []    # (tipo, ts, hex)
        self.problemi = []  # problemi di decodifica

    def agg_num(self, serie, ts, val, lento=False):
        """Aggiunge un valore numerico (se lo e': i dati mancanti o strani si saltano)."""
        val = _num(val)
        if val is not None:
            self.num.append((serie, ts, val, lento))


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

    def ultimi_dict(self):
        """{serie: (serie, ts, num, txt)}: l'ultimo valore di ogni serie."""
        return {r[0]: r for r in self.ultimi()}

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

def _ore_locali(lt, giorni=0, ore=0):
    """Epoch dell'ora locale `ore` del giorno di `lt` (un time.struct_time locale), spostato di `giorni` giorni: con l'ora legale si
    lascia fare a mktime (giorni=-1, ore=23 vale «ieri alle 23»)."""
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + giorni, ore, 0, 0, 0, 0, -1)))


def _basic(cid, secret):
    return "Basic " + base64.b64encode(f"{cid}:{secret}".encode()).decode()


class _TokenOAuth:
    """Parte comune dei token: un rinnovo OAuth con refresh token. Il refresh token e' MONOUSO: la risposta si salva SUBITO
    in config.json, altrimenti se si perde bisogna rifare l'autorizzazione."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.forza = False

    def invalida(self):
        self.forza = True

    def _rinnova_oauth(self, nome, url, intestazioni, corpo, rifiuto, scadenza_def, percorso):
        """POST al servizio, poi salva access/refresh token e scadenza nella sezione `percorso` di config.json."""
        code, js = http_json("POST", url, intestazioni, corpo)
        if code != 200 or "access_token" not in js:
            e = classifica(nome, code, js)
            if e.tipo in ("richiesta", "auth"):
                raise ErroreAPI("auth", code, rifiuto)
            raise e
        scad = int(time.time()) + int(js.get("expires_in", scadenza_def))

        def salva(raw):
            o = raw
            for k in percorso:
                o = o.setdefault(k, {})
            o["access_token"] = js["access_token"]
            if js.get("refresh_token"):
                o["refresh_token"] = js["refresh_token"]
            o["expires_at"] = scad
        self.cfg.aggiorna(salva)
        self.forza = False
        return js["access_token"]


class TokenST(_TokenOAuth):
    """Token SmartThings: OAuth (si rinnova da solo) oppure PAT (24 ore, incollato a mano)."""

    def __init__(self, cfg, db):
        super().__init__(cfg)
        self.db = db

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
        return self._rinnova_oauth(
            "SmartThings", ST_BASE + "/oauth/token",
            {"Authorization": _basic(oa["client_id"], oa["client_secret"]),
             "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            {"grant_type": "refresh_token", "client_id": oa["client_id"], "refresh_token": oa["refresh_token"]},
            "SmartThings: il rinnovo del token OAuth e' stato rifiutato. Il refresh token e' scaduto o gia' usato: "
            "rifai 'python pdc_strumenti.py st-oauth'.", 86400, ("smartthings", "oauth"))


class TokenNT(_TokenOAuth):
    def get(self):
        with self.lock:
            n = self.cfg.leggi()["netatmo"]
            for k in ("client_id", "client_secret", "refresh_token"):
                if not n.get(k):
                    raise ErroreConfig(f"Netatmo: manca '{k}' in config.json (sezione netatmo).")
            if (not self.forza and n.get("access_token")
                    and time.time() < float(n.get("expires_at") or 0) - 120):
                return n["access_token"]
            return self._rinnova_oauth(
                "Netatmo", NT_BASE + "/oauth2/token",
                {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                {"grant_type": "refresh_token", "refresh_token": n["refresh_token"],
                 "client_id": n["client_id"], "client_secret": n["client_secret"]},
                "Netatmo: il rinnovo del token e' stato rifiutato. Se hai rigenerato i token dal portale, "
                "copia in config.json quelli NUOVI.", 10800, ("netatmo",))


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
        rec.agg_num(serie, t or ts, val, lento)

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
            _, valori, problema = decodifica_ciclo(tipo, hx, mappa)
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
        rec.agg_num(serie, ts, val, lento)

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


def _programma_smarther(home, mod):
    """Programma orario dello Smarther da /api/homesdata: {"nome", "fasce": [[minuti da lunedi' 00:00, temperatura], ...]}.
    Senza programma (o in un formato che non conosco) 'fasce' e' vuoto."""
    scheda = None
    for sc in home.get("schedules") or []:
        if sc.get("type", "therm") == "therm" and (sc.get("selected") or scheda is None):
            scheda = sc
            if sc.get("selected"):
                break
    if not scheda:
        return {"nome": None, "fasce": []}
    stanza = str(mod.get("room_id")) if mod else None
    zone = {}
    for z in scheda.get("zones") or []:
        temp = None
        rt = z.get("rooms_temp") or []
        r = next((x for x in rt if str(x.get("room_id")) == stanza), rt[0] if rt else None)
        if r is not None:
            temp = r.get("temp")
        if temp is None:
            rr = z.get("rooms") or []
            r2 = next((x for x in rr if str(x.get("id")) == stanza), rr[0] if rr else None)
            if r2 is not None:
                temp = r2.get("therm_setpoint_temperature")
        zone[z.get("id")] = temp
    fasce = []
    for t in sorted(scheda.get("timetable") or [], key=lambda x: x.get("m_offset", 0)):
        temp = zone.get(t.get("zone_id"))
        if temp is not None:
            fasce.append([int(t.get("m_offset", 0)), float(temp)])
    return {"nome": scheda.get("name"), "fasce": fasce}


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
    try:
        s("info.nt.programma", json.dumps(_programma_smarther(home, mod), ensure_ascii=False))
    except Exception:
        s("info.nt.programma", '{"errore": true}')   # accessorio: la riga dice 'formato non riconosciuto'
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
        # stato del "refresh" (vedi _refresh): lo legge la Dashboard
        self.refresh_info = {"stato": "spento", "inviati": 0, "ultimo": None, "messaggio": None, "bloccato_fino": 0}

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
            raise ErroreConfig("SmartThings: non riesco a capire quale sia la PdC. Scrivi il device_id in "
                               "config.json (smartthings -> device_id). Dispositivi: " + elenco)
        dev = ehs[0]["deviceId"]
        self.app.cfg.aggiorna(lambda raw: raw.setdefault("smartthings", {}).update(device_id=dev))
        log.info("PdC trovata e salvata in config.json")
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
                                 f"({rec.problemi[0]}): forse un aggiornamento della PdC. Fammi vedere "
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
        try:
            self._refresh(dev, ts)
        except Exception:   # e' un extra: non deve mai rovinare la lettura normale
            log.exception("refresh: errore inatteso (ignorato)")
        return cambiati

    def _pdc_lavora(self, ts):
        """La pompa sta lavorando (o sta per partire / appena finito)? Si guarda solo cio' che e' gia' nel database:
        compressore in marcia in un campione recente, Smarther che chiede calore, acqua dell'impianto che si muove."""
        with closing(self.app.db._c()) as c:
            def ultimo(serie, da):
                r = c.execute("SELECT valore FROM misure WHERE serie=? AND ts>=? ORDER BY ts DESC LIMIT 1", (serie, da)).fetchone()
                return r[0] if r else None
            hz = ultimo("ciclo.freq_compressore", ts - 900)
            if hz is not None and hz > 0:
                return True
            if ultimo("casa.richiesta_calore", ts - 300) == 1.0:
                return True
            acqua = [v for (v,) in c.execute("SELECT valore FROM misure WHERE serie='risc.acqua' AND ts>=?", (ts - 420,))]
        return len(acqua) >= 3 and max(acqua) - min(acqua) >= 1.5

    def _refresh(self, dev, ts):
        """Se attivato in config.json (smartthings.refresh), mentre la pompa lavora manda ogni N secondi il comando 'refresh':
        la pdc consegna subito i campioni del ciclo che ha (dalle prove: 4 volte su 7 entro 10 secondi, a volte freschi, a volte
        arretrati). Subito dopo si rilegge. Se il token non ha il permesso di comando, o SmartThings protesta, si smette per un po'."""
        info = self.refresh_info
        cfg = (self.app.cfg.leggi()["smartthings"].get("refresh")) or {}
        if not cfg.get("attivo"):
            info.update(stato="spento", messaggio=None)
            return
        if info["bloccato_fino"] > ts:
            return
        ogni = max(30, int(cfg.get("ogni_secondi") or 60))
        if info["ultimo"] and ts - info["ultimo"] < ogni - 5:
            return
        if not self._pdc_lavora(ts):
            info.update(stato="a riposo", messaggio=None)
            return
        corpo = json.dumps({"commands": [{"component": "main", "capability": "refresh", "command": "refresh"}]}).encode()
        try:
            token = self.app.token_st.get()
            code, js = http_json("POST", f"{ST_API}/devices/{dev}/commands",
                                 {"Authorization": "Bearer " + token, "Content-Type": "application/json",
                                  "Accept": "application/json"}, corpo)
        except Exception as e:
            info.update(stato="errore", messaggio=str(e)[:120], bloccato_fino=ts + 300)
            return
        if code == 401 and not self.app.token_st.usa_pat():
            self.app.token_st.invalida()
            info.update(stato="errore", bloccato_fino=ts + 120, messaggio="token SmartThings da rinnovare, riprovo")
        elif code in (401, 403):
            info.update(stato="permesso", bloccato_fino=ts + 3600,
                        messaggio="il token SmartThings non ha il permesso di comando (x:devices:*)")
            log.warning("refresh: %s; riprovo tra un'ora", info["messaggio"])
        elif code == 429:
            info.update(stato="errore", bloccato_fino=ts + 300, messaggio="troppe richieste a SmartThings")
        elif code >= 400:
            info.update(stato="errore", bloccato_fino=ts + 300, messaggio=f"SmartThings ha risposto {code}")
        else:
            info.update(stato="attivo", ultimo=ts, messaggio=None, inviati=info["inviati"] + 1)
            t = threading.Timer(12, self.sveglia.set)   # i dati arrivano in ~10 secondi: rileggi subito dopo
            t.daemon = True
            t.start()

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


def cartella_log(base):
    p = Path(base) / "log"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


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


#@@PARTE web@@


# ==========================================================================
# PEZZO 2 - WEB: server, accesso con password, API, tempo reale, modo demo
# ==========================================================================

import bisect
import collections
import gzip
import hmac
import math
import queue
import secrets
import struct
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

WEB_ASSETS = {
    'app.css': r''':root{
  --bg:#0b0f17; --bg2:#0f1521; --card:#121a28; --card2:#172033; --linea:rgba(255,255,255,.07);
  --txt:#eef2f8; --mut:#a9b4c7; --mut2:#8491a8;
  --casa:#ff6b57; --acs:#4da3ff; --est:#34d3b0; --en:#ffb547; --amb:#a78bfa; --ok:#62eba6; --warn:#ffb547; --bad:#ff5d6c;
  --c-casa:255,107,87; --c-acs:77,163,255; --c-est:52,211,176; --c-en:255,181,71; --c-bad:255,93,108; --c-ok:98,235,166;
  --r:16px; --sab:env(safe-area-inset-bottom,0px); --sat:env(safe-area-inset-top,0px);
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html{background:var(--bg);color-scheme:dark}
body{margin:0;background:radial-gradient(1200px 600px at 80% -10%,#16223a 0,transparent 60%),var(--bg);color:var(--txt);
  font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;min-height:100vh;
  -webkit-font-smoothing:antialiased}
b{font-weight:650} .mut{color:var(--mut)}
code{background:var(--card2);padding:2px 7px;border-radius:6px;font-size:13px}
button{font:inherit;color:inherit}

/* ---------- accesso ---------- */
body.solo{display:grid;place-items:center;padding:24px}
.accesso{width:min(380px,100%);background:var(--card);border:1px solid var(--linea);border-radius:22px;padding:28px 24px;
  display:grid;gap:14px;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.45)}
.accesso img{margin:0 auto;border-radius:18px}
.accesso h1{margin:0;font-size:22px}
.accesso p{margin:0}
.accesso label{display:grid;gap:6px;text-align:left;color:var(--mut);font-size:13px}
.accesso input{font-size:16px;padding:13px 14px;border-radius:12px;border:1px solid var(--linea);background:var(--bg2);color:var(--txt);outline:none}
.accesso input:focus{border-color:var(--acs);box-shadow:0 0 0 3px rgba(77,163,255,.2)}
.accesso button{padding:14px;border:0;border-radius:12px;background:linear-gradient(135deg,var(--casa),var(--en));color:#1a0d08;font-weight:700;font-size:16px;cursor:pointer}
.errore{color:var(--bad);min-height:20px;font-size:14px}

/* ---------- struttura ---------- */
.shell{display:grid;grid-template-columns:230px 1fr;min-height:100vh}
.nav{position:sticky;top:0;height:100vh;padding:22px 14px;display:flex;flex-direction:column;gap:6px;
  border-right:1px solid var(--linea);background:rgba(11,15,23,.6);backdrop-filter:blur(12px)}
.brand{display:flex;align-items:center;gap:10px;padding:4px 10px 18px;font-weight:700;font-size:16px}
.brand img{width:30px;height:30px;border-radius:9px}
.nav a,.nav button.esci{display:flex;align-items:center;gap:12px;padding:11px 12px;border-radius:12px;color:var(--mut);text-decoration:none;
  cursor:pointer;border:0;background:none;text-align:left;width:100%;font-size:15px}
.nav a svg,.nav button svg{width:20px;height:20px;flex:none}
.nav a:hover,.nav button.esci:hover{background:rgba(255,255,255,.04);color:var(--txt)}
.nav a.on{background:rgba(255,255,255,.07);color:var(--txt)}
.nav .sp{flex:1}
.nav form{margin:0}
main{min-width:0;padding:22px 26px 40px}
.top{display:flex;flex-wrap:wrap;align-items:center;gap:12px 18px;margin-bottom:18px}
.top h1{margin:0;font-size:24px;letter-spacing:-.3px;flex:1 1 auto}
.stato{display:flex;align-items:center;gap:8px;color:var(--mut);font-size:13px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--mut2);flex:none}
.dot.ok{background:var(--ok);animation:pulse 2.2s infinite}.dot.warn{background:var(--warn)}.dot.bad{background:var(--bad)}
.chips{display:flex;gap:6px;background:var(--card);padding:4px;border-radius:12px;border:1px solid var(--linea)}
.chips button{border:0;background:none;padding:8px 14px;border-radius:9px;color:var(--mut);cursor:pointer;font-size:14px;min-height:36px}
.chips button.on{background:var(--card2);color:var(--txt);box-shadow:0 1px 0 rgba(255,255,255,.06) inset}
[hidden]{display:none!important}
.custom{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;background:var(--card);border:1px solid var(--linea);border-radius:var(--r);padding:8px 16px;margin-bottom:16px}
.custom label{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--mut)}
.custom input{font:inherit;font-size:14px;height:32px;box-sizing:border-box;background:var(--bg2);border:1px solid var(--linea);color:var(--txt);border-radius:8px;padding:0 10px;color-scheme:dark}
.custom button{height:32px;border:0;border-radius:8px;padding:0 14px;font-size:13px;background:var(--acs);color:#06101d;font-weight:650;cursor:pointer}
#i-t{flex:1;font-size:13px;color:var(--mut)}#i-t b{color:var(--txt);font-weight:600;font-variant-numeric:tabular-nums}
@media (max-width:513px){.custom label{display:grid;grid-template-columns:2.2em 1fr;width:100%}.custom label input{min-width:0;font-size:16px;height:36px}.custom button{width:100%;height:36px}#i-t{flex-basis:100%}}
.avviso{--c:var(--c-bad);display:flex;gap:10px;align-items:flex-start;padding:11px 14px;border-radius:12px;margin-bottom:10px;font-size:14px;
  background:rgba(var(--c),.09);border:1px solid rgba(var(--c),.3)}
.avviso.warn{--c:var(--c-en)}
.avviso small{display:block;color:var(--mut)}

/* ---------- schede ---------- */
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:14px;margin-bottom:16px}
.cards.c5{grid-template-columns:repeat(auto-fit,minmax(180px,1fr))}
.card{background:linear-gradient(180deg,var(--card2),var(--card) 55%);border:1px solid var(--linea);border-radius:var(--r);padding:15px 16px 9px;
  position:relative;overflow:hidden;min-width:0}
.card.acc::before{content:"";position:absolute;left:0;right:0;top:0;height:3px;background:var(--acc)}
.ch{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}
.et{font-size:11.5px;letter-spacing:.9px;text-transform:uppercase;color:var(--mut);font-weight:600}
.badge{font-size:11.5px;flex:none;padding:3px 9px;border-radius:99px;background:rgba(255,255,255,.06);color:var(--mut);white-space:nowrap}
.badge.on{background:color-mix(in srgb,var(--acc) 22%,transparent);color:var(--acc)}
/* stato: colori pieni, leggibili a colpo d'occhio */
.badge.acceso,.badge.riposo,.badge.spento,.badge.caldo,.badge.warn{font-weight:700;font-size:12px;padding:4px 11px;color:#0b0f17}
.badge.acceso{background:#62eba6}          /* verde: sta lavorando */
.badge.caldo{background:#ff7a45}           /* arancio: richiesta di calore */
.badge.warn{background:#ffc83d}            /* giallo: sbrinamento / attenzione */
.badge.riposo{background:#5aa9ff}          /* blu: acceso ma fermo / in attesa */
.badge.spento{background:#ff4d5e;color:#fff} /* rosso: spento */
.badge.tema{background:var(--acc);color:#0b0f17;font-weight:700;font-size:12px;padding:4px 11px} /* etichetta di categoria, nel colore della scheda */
.badge.acceso::before,.badge.riposo::before,.badge.spento::before,.badge.caldo::before,.badge.warn::before{content:'';display:inline-block;width:6px;height:6px;border-radius:50%;background:currentColor;opacity:.55;margin-right:6px;vertical-align:1px}
.val{display:flex;align-items:flex-end;justify-content:space-between;gap:10px;min-height:48px}
.val .n{font-size:34px;font-weight:700;letter-spacing:-1px;line-height:1;font-variant-numeric:tabular-nums;white-space:nowrap}
.val .n i{font-style:normal;font-size:16px;font-weight:500;color:var(--mut);margin-left:3px;letter-spacing:0}
.spark{flex:1;min-width:50px;max-width:130px;height:40px;color:var(--acc)}
.spark svg{width:100%;height:100%;display:block;overflow:visible}
.sub{display:flex;flex-wrap:wrap;gap:3px 14px;margin-top:10px;color:var(--mut);font-size:12.5px}
.sub b{color:var(--txt);font-variant-numeric:tabular-nums}
.flash{animation:flash .9s ease-out}
.card .nota{margin-top:8px;color:var(--mut2);font-size:12px}
.card .nota .um-n{display:block;color:var(--mut);font-size:12.5px;line-height:15px}
.card .nota .um-n b{color:var(--txt);font-variant-numeric:tabular-nums}
.card .nota:empty{display:none}
.card .nota:has(.ora-n,.um-n){margin-top:6px}
@media (min-width:761px){#c-n,#e-n{display:block;min-height:15px;margin-top:6px}} /* PC: riga sempre riservata, la card non cambia altezza */
.ora-n{display:flex;align-items:center;gap:7px;line-height:1.3}
.ora-n .ck{flex:none;box-sizing:border-box;width:11px;height:11px;border-radius:50%;border:1.5px solid #fff;position:relative}
.ora-n .ck:before,.ora-n .ck:after{content:"";position:absolute;background:#fff}
.ora-n .badge{margin:-3px 0;min-width:0;overflow:hidden;text-overflow:ellipsis}
.ora-n .ck:before{left:50%;top:1.5px;width:1.5px;height:3.5px;margin-left:-.75px}
.ora-n .ck:after{left:50%;top:50%;width:3px;height:1.5px;margin-top:-.75px}

/* meteo nella scheda Esterna; il dettaglio e' un pannello sospeso */
/* nell'angolo in alto a destra della scheda (spazio vuoto): non allunga mai la scheda; si adatta alla larghezza */
.card:has(> .mt-bl){container-type:inline-size}
.mt-bl{position:absolute;right:6px;top:9px;display:flex;flex-direction:column;align-items:flex-end;gap:1px;padding:2px 5px;background:none;border:0;border-radius:10px;color:inherit;cursor:pointer;text-align:right;line-height:1.2;white-space:nowrap}
.mt-bl[hidden]{display:none}
.mt-bl:hover{background:rgba(255,255,255,.05)}
.mt-g{font-size:13px;color:var(--txt);font-variant-numeric:tabular-nums}
/* il blocco e' alto e stretto; sotto certe larghezze lascia cadere prima la frase di pioggia, poi la forchetta, per non toccare il badge */
@container (max-width:178px){ .mt-pi{display:none} }
@container (max-width:146px){ .mt-g{display:none} }
.mt-i{flex:none;display:block}
.mt-n{stroke:#b3bfd6;fill:var(--card)}.mt-s{stroke:var(--en)}.mt-l{stroke:#d6dcff}.mt-p{stroke:#4da3ff}.mt-f{stroke:var(--en)}.mt-v{stroke:#dff0ff;stroke-width:3.2}
.mt-pi{font-size:11px;font-weight:600}
.mt-pi.certa{color:#6cb4ff}.mt-pi.possibile{color:var(--warn)}.mt-pi.nessuna{color:var(--mut2);font-weight:500}
.mt-pan{position:fixed;z-index:60;padding:12px 14px;border-radius:14px;background:#0a0f19;border:1px solid rgba(255,255,255,.12);box-shadow:0 14px 40px rgba(0,0,0,.6);font-size:12.5px;font-variant-numeric:tabular-nums}
.mt-pan .eti{margin-bottom:4px}
.mt-pan .riga{padding:3px 0;border:0;font-size:inherit}
.mt-r{display:grid;grid-template-columns:20px 46px 34px 1fr;gap:10px;align-items:center;padding:3px 0;color:var(--mut)}
.mt-r b{color:var(--txt);text-align:right}
.mt-mm{white-space:nowrap;text-align:right}
.mt-r.certa .mt-mm{color:#6cb4ff}.mt-r.possibile .mt-mm{color:var(--warn)}.mt-r.nessuna .mt-mm{color:var(--mut2)}
.mt-fo{margin-top:8px;padding-top:8px;border-top:1px solid var(--linea);color:var(--mut2);font-size:11.5px;line-height:1.55;display:flex;flex-direction:column}
.mt-fo span{white-space:nowrap}
.mt-fo a{color:var(--mut);text-decoration:underline}

.grid2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);gap:14px;margin-top:16px;align-items:start}
.colonna{display:grid;gap:14px;min-width:0}
.card h2{margin:0;font-size:15px;font-weight:650}
.righe{display:grid;margin-top:6px}
.riga{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-top:1px solid var(--linea);font-size:14px}
.riga:first-child{border-top:0}
.riga>span:first-child{color:var(--mut)}
.riga b{font-variant-numeric:tabular-nums;text-align:right}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;font-weight:650;background:rgba(255,255,255,.07)}
.pill.si{background:#62eba6;color:#08140e;font-weight:700}.pill.no{background:rgba(255,255,255,.06);color:var(--mut)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin-top:10px}
.tile{background:var(--bg2);border:1px solid var(--linea);border-radius:12px;padding:10px 12px}
.tile small{display:block;color:var(--mut);font-size:11.5px}
.tile b{font-size:19px;font-variant-numeric:tabular-nums}.tile i{font-style:normal;color:var(--mut);font-size:12px;margin-left:2px}
.tile.vecchio b{color:var(--mut)}
.card:has(#cicli){container-type:inline-size}
.cg{margin-top:10px;display:grid;grid-template-columns:122px 1fr;align-items:center;gap:0 10px}@container (max-width:400px){.cg{display:block}}.cg h3{margin:0;line-height:1.3;display:flex;align-items:center;gap:7px}
.cg h3 i{width:8px;height:8px;border-radius:50%;background:rgb(var(--c))}
.cg .tiles{margin-top:0}@container (max-width:400px){.cg .tiles{margin-top:6px}}

/* ---------- timeline ---------- */
.tlw{position:relative;margin-top:6px;touch-action:pan-y}
.tlw svg{width:100%;height:46px;display:block;border-radius:10px;background:var(--bg2)}
.assi{display:flex;justify-content:space-between;color:var(--mut2);font-size:11.5px;margin-top:5px;font-variant-numeric:tabular-nums}
@media (max-width:513px){.tlw,.assi{margin-left:-6px;margin-right:-6px}}   /* telefono: barra e orari a 8 px dal bordo, uguali a destra e a sinistra */
.legenda{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:10px;font-size:12.5px;color:var(--mut)}
.legenda i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.riep{font-size:12.5px;color:var(--mut);display:flex;gap:4px 12px;flex-wrap:wrap}
.riep b{font-variant-numeric:tabular-nums}
@media (max-width:760px){.riep{font-size:12px;gap:4px 9px}}
.tip{position:absolute;top:-4px;transform:translate(-50%,-100%);background:#05080d;border:1px solid var(--linea);border-radius:9px;
  padding:6px 10px;font-size:12px;white-space:nowrap;pointer-events:none;z-index:4;box-shadow:0 6px 20px rgba(0,0,0,.5)}

/* ---------- grafici ---------- */
.gr{margin-top:14px}
.grw{overflow:hidden}.grw,#g-en,#g-cop,#g-bin{touch-action:pan-y}
.grw svg,#g-en svg,#g-cop svg,#g-bin svg{display:block;max-width:100%}
.gr text,#eff svg text{fill:var(--mut2);font-size:11px;font-family:inherit}
.gr .cur-t,#eff .cur-t{pointer-events:none}
.gr .cur-bg,#eff .cur-bg{fill:var(--bg2);stroke:rgba(255,255,255,.16);stroke-width:1}
.gr .cur-tx,#eff .cur-tx{fill:var(--txt);font-size:11.5px;font-variant-numeric:tabular-nums}
.gr .cur-tx .nd{fill:var(--mut)}
.bordo-vuoto{stroke:#8793a8;stroke-opacity:.4;stroke-width:1;stroke-dasharray:3 3}
.bordo-vuoto.fin{stroke:#23a876;stroke-opacity:1}[data-fin]{cursor:pointer}
.gl{stroke:rgba(255,255,255,.06)}.gv{stroke:rgba(255,255,255,.035)}
.cur{stroke:rgba(255,255,255,.32);stroke-width:1}
.legg{display:flex;flex-wrap:wrap;gap:4px 14px;justify-content:flex-end;font-size:12.5px;color:var(--mut)}
.legg i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}
.legg b{color:var(--txt);font-variant-numeric:tabular-nums}
.legg i.tp{width:11px;height:2px;border-radius:1px;background:#fff;opacity:.85;vertical-align:3px}
.legg i.tp2{width:12px;height:0;border-radius:0;border-top:2px dashed var(--mut);background:none;vertical-align:3px}
.legg i.an{background:var(--warn)}
.tip-l{stroke:rgba(255,255,255,.9);stroke-width:2;stroke-linecap:round}
.tip-a{fill:var(--warn)}
#eff svg .vt{fill:var(--txt);font-size:10.5px}
#eff svg .ass{fill:var(--mut)}
.nota.f{color:var(--mut2);font-size:12px;line-height:1.5;margin:14px 2px 4px}
.nota.f span{display:block}
@media (max-width:760px){.nota.f span{text-wrap:pretty}.nota.f span+span{margin-top:4px}}
.grid2.uguali{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
#eff .grid2,#eff>.avviso,#eff>.card{margin-top:14px}
@media (max-width:900px){.grid2.uguali{grid-template-columns:1fr}}

/* ---------- impostazioni ---------- */
#imp .card{margin-bottom:14px}
.sez-t{margin:22px 2px 10px;font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut)}
.nota-sez,.fsv-edit small{text-wrap:pretty}
.nota-sez{color:var(--mut);font-size:13px;margin:6px 0 4px}
.campo input[type=text],.campo input[type=password],.campo input[type=time],.campo select,.campo textarea{width:100%;font:inherit;font-size:16px;background:var(--bg2);
  border:1px solid var(--linea);color:var(--txt);border-radius:10px;padding:10px 12px;color-scheme:dark}
.campo textarea{display:block;resize:vertical;font-family:ui-monospace,Consolas,monospace;font-size:13.5px;line-height:1.45}
.campo input:focus,.campo textarea:focus{outline:2px solid color-mix(in srgb,var(--acs) 60%,transparent);outline-offset:0}
.sw{position:relative;display:inline-block;width:46px;height:28px}
.sw input{opacity:0;width:0;height:0;position:absolute}
.sw span{position:absolute;inset:0;background:#2a3547;border-radius:99px;transition:.2s;cursor:pointer}
.sw span::after{content:"";position:absolute;left:3px;top:3px;width:22px;height:22px;border-radius:50%;background:#cfd8e6;transition:.2s}
.sw input:checked+span{background:var(--est)}
.sw input:checked+span::after{transform:translateX(18px);background:#fff}
.sw input:focus-visible+span{outline:2px solid var(--acs);outline-offset:2px}
button.sec,.azioni button,.barra-salva button{font:inherit;font-weight:650;border:0;border-radius:10px;padding:10px 16px;background:var(--acs);color:#06101d;cursor:pointer}
button.sec{background:rgba(255,255,255,.08);color:var(--txt)}
button.piccolo{padding:6px 12px;font-size:13px}
button:disabled{opacity:.5;cursor:default}
.azioni{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:center;margin-top:14px}
.msg.ok{color:var(--ok)}.msg.bad{color:var(--bad)}
.allarme summary{list-style:none;cursor:pointer;display:grid;gap:2px}
.allarme summary::-webkit-details-marker{display:none}
.allarme summary .t{font-weight:650}
.allarme summary .d{color:var(--mut);font-size:13px}
.allarme[open] summary{padding-bottom:4px;border-bottom:1px solid var(--linea)}
.allarme summary::after{content:"›";position:absolute;right:16px;top:14px;color:var(--mut);transition:.2s;font-size:20px}
.allarme[open] summary::after{transform:rotate(90deg)}
.barra-salva{position:sticky;bottom:calc(14px + var(--sab));z-index:6;display:flex;align-items:center;gap:10px;justify-content:flex-end;
  background:rgba(18,26,40,.94);backdrop-filter:blur(10px);border:1px solid var(--linea);border-radius:14px;padding:10px 14px;margin-top:8px;
  box-shadow:0 8px 30px rgba(0,0,0,.5)}
.barra-salva span{flex:1;color:var(--warn);font-size:14px}
.pill.bad,.pill.off{background:#ff4d5e;color:#fff;font-weight:700}
.pill.warn{background:#ffc83d;color:#1a1200;font-weight:700}
.pill.en{background:var(--en);color:#0b0f17;font-weight:700}
.pill.tg{background:rgba(var(--c),.16);color:rgb(var(--c));font-size:11px;font-weight:700}
.pill.up{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:3px 9px;background:rgba(138,147,166,.18);color:var(--mut)}
.cl-h .pill{align-self:center}.cl-v li .pill{display:block;text-align:center;padding:1px 0}
.sep{color:var(--mut2);margin:0 1px}
#toast{position:fixed;left:50%;bottom:calc(90px + var(--sab));transform:translate(-50%,20px);background:#05080d;border:1px solid var(--linea);border-radius:12px;
  padding:11px 16px;font-size:14px;opacity:0;pointer-events:none;transition:.25s;z-index:30;max-width:90vw;box-shadow:0 8px 30px rgba(0,0,0,.6)}
#toast.on{opacity:1;transform:translate(-50%,0)}#toast.bad{border-color:var(--bad)}

details.guida{margin:12px 0;background:var(--bg2);border:1px solid var(--linea);border-radius:12px;padding:10px 14px}
details.guida summary{cursor:pointer;font-weight:650}
details.guida ol{margin:10px 0 4px;padding-left:20px;display:grid;gap:8px;color:var(--mut);font-size:14px}
details.guida b{color:var(--txt)}
ul.passi{list-style:none;margin:12px 0 0;padding:0;display:grid;gap:6px;font-size:14px}
ul.passi li.ok{color:var(--ok)}ul.passi li.bad{color:var(--bad)}
a{color:var(--acs)}

/* ---------- curva ---------- */
.curva svg{width:100%;height:auto;display:block;margin-top:6px}
.curva text{fill:var(--mut);font-size:11px;font-family:inherit}
.curva .ass{fill:var(--mut2);font-size:11px}
.box2,.exp{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}
.bx{border-radius:12px;border:1px solid var(--linea);min-width:0}
.tn{background:rgba(var(--c),.09)}
.c-casa{--c:var(--c-casa)}.c-acs{--c:var(--c-acs)}.c-est{--c:var(--c-est)}.c-en{--c:var(--c-en)}.c-bad{--c:var(--c-bad)}.c-ok{--c:var(--c-ok)}
.eti{font-size:11px;letter-spacing:.8px;text-transform:uppercase;color:var(--mut2);font-weight:600}
.box2 small{display:block}
.box2>.bx{padding:8px 12px 9px}
.box2 p{margin:2px 0 0;display:flex;flex-wrap:wrap;align-items:baseline;gap:0 9px}
.box2 b{font-size:20px;font-weight:700;letter-spacing:-.5px;line-height:1.15;font-variant-numeric:tabular-nums}
.box2 span{font-size:12px;color:var(--mut)}
.box2>.bx:nth-child(3){grid-column:1/-1}
.box2 strong{font-weight:700;color:var(--txt)}
.nota-cv{list-style:none;margin:10px 0 0;padding:0;display:grid;gap:3px;color:var(--mut2);font-size:12px;line-height:1.45}
.nota-cv li{display:flex;gap:9px;align-items:center}
.nota-cv i{flex:none}
.nota-cv .lg{width:18px;height:3px;border-radius:2px;background:linear-gradient(90deg,#4da3ff,#34d3b0)}
.nota-cv .pt{width:8px;height:8px;margin:0 5px;border-radius:50%;background:rgba(255,107,87,.7)}
.nota-cv .av{width:8px;height:8px;margin:0 5px;border-radius:50%;background:var(--warn)}
.nota-cv .in,.avviso .in{box-sizing:border-box;width:11px;height:11px;margin:0 3.5px;border-radius:50%;border:1.5px solid var(--mut2);position:relative}
.nota-cv .in:after,.avviso .in:after{content:"";position:absolute;left:50%;top:2px;width:1.5px;height:4px;margin-left:-.75px;background:var(--mut2)}
.avviso .in{display:block;margin-top:4px}
.nota-cv.lun{margin:14px 2px 4px}.nota-cv.lun li{align-items:flex-start}.nota-cv.lun .in{margin-top:3px}.nota-cv.lun .tt{display:none}@media (hover:none){.nota-cv.lun .tt{display:flex}}
.vuoto{color:var(--mut);padding:30px 6px;text-align:center}

@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(98,235,166,.55)}70%{box-shadow:0 0 0 7px rgba(98,235,166,0)}100%{box-shadow:0 0 0 0 rgba(98,235,166,0)}}
@keyframes flash{0%{color:#fff;text-shadow:0 0 14px var(--acc,#fff)}100%{text-shadow:none}}

/* ---------- telefono ---------- */
@media (max-width:900px){
  .grid2{grid-template-columns:1fr}
}
@media (max-width:760px){
  .shell{grid-template-columns:1fr}
  .nav{position:fixed;z-index:10;left:0;right:0;bottom:0;top:auto;height:auto;flex-direction:row;justify-content:space-around;gap:0;
    padding:6px 6px calc(6px + var(--sab));border-right:0;border-top:1px solid var(--linea);background:rgba(11,15,23,.92)}
  .brand,.nav .sp,.nav a.mini{display:none}
  .nav a,.nav button.esci{flex-direction:column;gap:3px;padding:7px 4px;font-size:11px;text-align:center;justify-content:center;flex:1;min-height:52px}
  .nav form{flex:1;display:flex}
  main{padding:calc(16px + var(--sat)) 14px calc(86px + var(--sab))}
  .top h1{font-size:22px}
  .stato{order:3;flex-basis:100%}
  .chips{width:100%}.chips button{flex:1;padding:9px 6px}.chips:has(.conf-btn){gap:2px}.chips:has(.conf-btn) button{font-size:13px;padding:9px 3px;white-space:nowrap}.chips:has(.conf-btn) .conf-btn{padding-left:9px;margin-left:2px}
  .cards{grid-template-columns:1fr 1fr;gap:10px}
  .cards .card:last-child:nth-child(odd){grid-column:1/-1}
  .card{padding:13px 13px 8px}.custom{padding:8px 13px}
  .fsv-gr:not([open]){padding-bottom:13px}
  .val{flex-direction:column;align-items:flex-start;gap:6px}
  .val .n{font-size:30px}
  .spark{max-width:none;width:100%;height:30px;flex:none}
  .box2{grid-template-columns:1fr}.box2>.bx:nth-child(3){grid-column:auto}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}

/* l'etichetta di stato va a capo sotto il titolo se non c'e' spazio (vale a ogni larghezza, Safari compreso) */
.card .ch{flex-wrap:wrap;gap:6px 8px}
.card .ch>.et,.card .ch>h2{min-width:0}
.card .ch .badge{max-width:100%;overflow:hidden;text-overflow:ellipsis}

/* schede della dashboard: l'etichetta di stato sta sempre sotto il titolo; senza etichetta si tiene lo spazio per allineare i numeri */
.ch-st{flex-direction:column;align-items:flex-start;gap:6px}
.ch-st .badge{min-height:24px;line-height:16px}
.ch-st .badge:empty{visibility:hidden;padding:0;width:1px}

/* ---------- pagina Dispositivi e loghi ---------- */
.dev-griglia{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:14px;margin-bottom:14px;align-items:start}
.dev-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;margin-bottom:6px}
.dev-id{display:flex;align-items:center;gap:14px;min-width:0}
.dev-id h2{margin:0;font-size:17px}
.dev-logo{height:34px;max-width:150px;object-fit:contain;flex:none}
.dev-logo.piccolo{height:14px;max-width:80px}
.dev-logo.lg-smarther{height:20px}
.nel-testo{height:9px;vertical-align:baseline;margin:0 1px;object-fit:contain;opacity:.8}
.con{font-size:12px;white-space:nowrap}
.marca-riga{display:inline-flex;flex-direction:row;align-items:center;gap:7px;white-space:nowrap}
.dev-logo.in-riga{height:12px;max-width:90px;vertical-align:middle}
.bianco{filter:brightness(0) invert(1)}
.dev-marca{font-weight:700;font-size:15px;letter-spacing:.02em;padding:6px 10px;border-radius:10px;background:var(--card2);flex:none}
.dev-st{display:grid;gap:4px;justify-items:end}
.dev-gr{margin-top:12px}
.dev-gr h3{margin:0 0 2px}
.dev .riga b,.plg .riga b{max-width:68%;word-break:break-word;display:flex;flex-direction:column;align-items:flex-end;gap:2px}
.dev .riga b .val-c,.plg .riga b .val-c{font-variant-numeric:tabular-nums;text-wrap:balance}
.dev .riga>span:first-child,.plg .riga>span:first-child{max-width:62%}
.dev .riga,.plg .riga{align-items:flex-start}
.dev .riga b{margin-left:auto}
.dev .riga:has(>.matita){align-items:center}
.nota-r{font-weight:400;font-size:12px;text-align:right;text-wrap:balance}
.cv{display:flex;align-items:center;justify-content:flex-end;flex-wrap:wrap;gap:4px 8px}
.copia{font:inherit;font-size:11.5px;font-weight:600;color:var(--mut);background:var(--card2);border:1px solid var(--linea);border-radius:8px;padding:2px 9px;cursor:pointer}
.copia:hover{color:var(--txt)}
.logo-et{height:13px;vertical-align:-2px;margin-left:3px;object-fit:contain;opacity:.8}
.iniz{margin-top:10px;border-top:1px solid var(--linea);padding-top:8px}.iniz summary{cursor:pointer;color:var(--mut);font-size:13px}

/* ---------- sotto-pagine di Dispositivi e impostazioni installatore ---------- */
.sub-tabs{display:inline-flex;gap:4px;padding:4px;margin-bottom:14px;border-radius:12px;background:var(--card);border:1px solid var(--linea)}
.sub-tabs a{padding:7px 16px;border-radius:9px;color:var(--mut);text-decoration:none;font-size:14px;font-weight:600}
.sub-tabs a.on{background:var(--card2);color:var(--txt)}
.fsv-gr{margin-bottom:14px}
.fsv-gr:not([open]){padding-bottom:15px}
.fsv-gr>summary{display:flex;justify-content:space-between;gap:12px;align-items:center;cursor:pointer;list-style:none;font-weight:700}
.fsv-gr>summary::-webkit-details-marker{display:none}
.fsv-gr>summary .d{font-weight:400;font-size:12.5px;color:var(--mut);white-space:nowrap}
.fsv-gr[open]>summary{margin-bottom:6px}
.fsv-id{display:inline-block;min-width:42px;margin-right:8px;font-style:normal;font-variant-numeric:tabular-nums;font-size:12px;font-weight:700;color:var(--mut2)}
details.riq>summary .pill{margin-left:auto}
.rosso{color:#ff6b6b}
details.riq>summary::after,.fsv-gr>summary .d::after,.cl-vecchie>summary::before,.come-b::after,.tile[data-come] small::after{content:"";display:inline-block;flex:none;width:var(--chv,7px);height:var(--chv,7px);border:solid currentColor;border-width:0 var(--chw,2px) var(--chw,2px) 0;transform:rotate(-45deg);transition:transform .15s}
details.riq[open]>summary::after,.fsv-gr[open]>summary .d::after,.cl-vecchie[open]>summary::before,.come-b.on::after,.tile[data-come].on small::after{transform:rotate(45deg)}
.fsv-gr>summary .d::after{margin:0 3px 3px 12px}
.fsv-gr:not([open])>summary .d::after{margin-bottom:0}

/* ---------- comandi accendi/spegni ---------- */
.cmd{display:inline-flex;align-items:center;gap:12px}
.sw.bloccato span{opacity:.45;cursor:not-allowed}
.sw.bloccato{filter:grayscale(.4)}
#cmd-lock .sbloccato{border-color:#ffc83d;color:#ffc83d}
.pager{display:flex;align-items:center;justify-content:center;gap:14px;margin-top:10px;color:var(--mut);font-size:13px}
.pager button{width:34px;height:30px;border-radius:9px;border:1px solid var(--linea);background:var(--card);color:inherit;font-size:18px;line-height:1;cursor:pointer}
.pager button:disabled{opacity:.35;cursor:default}
.vecchio-et{color:var(--warn)}
.nav a.mini{font-size:13px;padding:8px 12px;color:var(--mut);opacity:.8}
.nav a.mini svg{width:17px;height:17px}
.nav a.mini.on{opacity:1}
.lnk{color:var(--acs);text-decoration:none;font-weight:600}
.changelog{display:flex;flex-direction:column;gap:22px;max-width:760px}
.cl-breve{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--linea);border-radius:var(--r);padding:14px 18px;display:grid;gap:10px}
.cl-bi{display:flex;align-items:flex-start;gap:12px;font-size:14px;color:var(--mut);line-height:1.4}
.cl-bi b{color:var(--txt)}
.cl-bi b::after{content:" —";font-weight:400;color:var(--mut2)}
.cl-bi .mt-i,.cl-ic{flex:none;width:22px;height:22px;margin-top:-1px}
.cl-ic{display:inline-grid;place-items:center;border-radius:7px;background:rgba(255,255,255,.07);color:var(--txt);font-weight:700;font-size:14px}
/* linea del tempo: un pallino per versione */
.cl-tl{position:relative;display:flex;flex-direction:column;gap:26px;padding-left:26px}
.cl-tl::before{content:"";position:absolute;left:6px;top:8px;bottom:8px;width:2px;background:linear-gradient(180deg,var(--en),rgba(255,255,255,.1) 160px);border-radius:2px}
.cl-v{position:relative}
.cl-v::before{content:"";position:absolute;left:-26px;top:7px;width:14px;height:14px;border-radius:50%;background:var(--bg2);border:2px solid #4b5a78}
.cl-v.att::before{border-color:var(--en);background:var(--en);box-shadow:0 0 0 4px rgba(255,181,71,.18)}
.cl-h{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px;margin-bottom:8px}
.cl-n{font-size:22px;font-weight:700;letter-spacing:-.4px;font-variant-numeric:tabular-nums}
.cl-ti{font-size:15px;font-weight:600;color:var(--mut)}
.cl-d{margin-left:auto;font-size:12.5px;color:var(--mut2);white-space:nowrap}
.cl-v ul{list-style:none;margin:0;padding:0;display:grid;gap:7px}
.cl-v li{font-size:14px;line-height:1.5;color:var(--mut);display:grid;grid-template-columns:78px 1fr;gap:0 12px;align-items:baseline}
.cl-v li b{color:var(--txt);font-weight:650}
.cl-tx::before{content:none}
.cl-tx b::after{content:" —";font-weight:400;color:var(--mut2)}
.cl-vecchie{display:flex;flex-direction:column;gap:26px}
.cl-vecchie>summary{cursor:pointer;color:var(--mut);font-size:14px;font-weight:600;list-style:none;padding:2px 0 0}
.cl-vecchie>summary::before{margin:0 10px 1px 2px}
.cl-vecchie[open]>summary{margin-bottom:22px}
.cl-vecchie>.cl-v{margin-bottom:26px}
.cl-vecchie>.cl-v:last-child{margin-bottom:0}
.cl-vecchie>.eti{margin:0 0 20px}
@media (max-width:700px){.cl-v li{display:block}.cl-v li .pill{display:inline-block;padding:1px 8px;margin-right:9px;vertical-align:1px}.cl-d{margin-left:0;flex-basis:100%}}
#dsp>article.card{margin-bottom:14px}#dsp>article.card:last-child{margin-bottom:0}
.chips .conf-btn{margin-left:4px;border-left:1px solid var(--linea);border-radius:0 9px 9px 0;padding-left:16px}
.conf-r{display:flex;justify-content:space-between;gap:10px;margin-top:8px;padding-top:8px;border-top:1px dashed var(--linea);font-size:13px;color:var(--mut)}
.conf-r b{color:var(--txt);font-weight:600}
.conf-d{font-weight:700;color:var(--mut)}
.conf-d.buono{color:var(--ok)}.conf-d.cattivo{color:var(--casa)}
.nota.conf-nota{margin-top:0}
.conf-t{display:block;margin-top:4px;color:var(--mut);font-size:12px}
.azioni-t{display:flex;gap:8px}
.azioni-t .sec{padding:6px 12px;font-size:13px}
.reg-filtro{display:flex;align-items:center;gap:8px;margin:12px 0 8px;font-size:14px;color:var(--mut)}
.reg-log{max-height:420px;overflow:auto;background:var(--card2);border:1px solid var(--linea);border-radius:10px;padding:10px 12px;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.rl{white-space:pre-wrap;word-break:break-word;color:var(--mut)}
.rl.WARNING{color:#ffb547}.rl.ERROR,.rl.CRITICAL{color:#ff7a6b}

/* Impostazioni: tab e riquadri collassabili */
#imp-tabs{overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none}
#imp-tabs::-webkit-scrollbar{display:none}
#imp-tabs .sub-tabs{margin-bottom:14px;display:flex;width:max-content;min-width:100%}
#imp-tabs .sub-tabs a{white-space:nowrap;padding:7px 14px}
.imp-tab[hidden]{display:none}
details.riq{padding-block:12px}
@media (max-width:760px){details.riq{padding-block:10.5px}}
details.riq>summary{list-style:none;cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:10px}
details.riq>summary::-webkit-details-marker{display:none}
details.riq>summary::after{margin:0 4px 0 0;color:var(--mut)}
details.riq[open]>summary::after{margin-bottom:4px}
details.riq[open]>summary{padding-bottom:8px;margin-bottom:0;border-bottom:1px solid var(--linea)}
details.riq .campi>.campo:first-child{padding-top:8px}
details.riq .campi>.campo:last-child{padding-bottom:0}
details.riq[open]{padding-bottom:7px}
details.riq .riq-c>.nota-sez:first-child,details.riq .riq-c>h2:first-child+.nota-sez{margin-top:14px}
#cf h2{display:none}
#cf .ch{justify-content:flex-end}
.lista-allarmi{display:grid;gap:8px}
.lista-allarmi .allarme{position:relative;background:var(--bg2);border:1px solid var(--linea);border-radius:12px;padding:12px 36px 12px 14px}

/* Dispositivi su telefono: logo a sinistra e stato a destra sulla stessa riga (centrati tra loro), sotto titolo e sottotitolo */
@media (max-width:560px){
  .dev-head{display:grid;grid-template-columns:minmax(0,1fr) auto;column-gap:12px;row-gap:.75px;align-items:center;margin-bottom:10px}
  .dev-id{display:contents}
  .dev-head .dev-logo,.dev-head .dev-marca{grid-column:1;grid-row:1;align-self:center;height:28px;width:auto;max-width:110px;max-height:none;object-fit:contain;object-position:left center}
  .dev-head .dev-marca{height:auto;line-height:1.2}
  .dev-head .dev-logo.lg-smarther{height:22px}
  .dev-id>div{grid-column:1 / -1;grid-row:2;min-width:0}
  .dev-st{grid-column:2;grid-row:1;align-self:center;display:grid;justify-items:end;gap:5px}
}

#c-um.um-giallo{color:var(--warn)}
#c-um.um-rosso{color:var(--bad)}

.fermo-t{display:flex;align-items:center;gap:10px;margin-top:10px;flex-wrap:wrap}
.fermo-q{font-size:14px;color:var(--mut)}
.tile .t-txt{font-size:17px;font-weight:600}.tile .t-txt i{font-size:12px;font-style:normal;opacity:.7}
.fermo-w{grid-column:1/-1}.fermo-g{display:flex;gap:10px;margin-top:10px}
.fermo-w .tile .t-txt{font-size:15px;white-space:nowrap}
@media(max-width:560px){.fermo-w .tile{padding:10px 10px}.fermo-w .tile .t-txt{font-size:14px}.fermo-w .tile .t-txt i{font-size:11px}}
p.nota-sez#prest-rit{display:flex;flex-wrap:wrap;gap:2px 16px}.nw{white-space:nowrap}
#prest-rit .rt-t{flex-basis:100%}
#prest-rit .rf{display:inline-flex;align-items:center;gap:7px}
#prest-rit .rf i{width:7px;height:7px;border-radius:50%;flex:none}#prest-rit b{color:var(--txt);font-weight:650}
p.nota-sez#prest-rit>span:first-child{flex-basis:100%}
.agg-corpo p{margin:10px 0 6px;line-height:1.5}.agg-ok b{color:var(--ok)}.agg-riavvio b{color:var(--warn)}

button.pericolo{background:#7a1f2b!important;border:1px solid #a8323f!important;color:#fff!important}button.pericolo.sec{background:transparent!important;color:#ff8a96!important}

.campo input.mono{font-family:ui-monospace,Consolas,monospace;font-size:13.5px;letter-spacing:-.2px}
.campo input::placeholder{color:var(--mut2)}

/* ---------- campi delle impostazioni: elenco ordinato, valori corti in linea ---------- */
.campi{display:flex;flex-direction:column;margin-top:6px}
.campo{display:grid;grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"et ctl" "ai ai" "er er";column-gap:16px;row-gap:0;align-items:center;padding:13px 0;border-bottom:1px solid var(--linea)}
.campo:last-child{border-bottom:0}
.campo>label{grid-area:et;font-size:15px;line-height:1.3;color:var(--txt)}
.campo>input,.campo>select,.campo>textarea,.campo>.sw{grid-area:ctl}
.campo .aiuto{grid-area:ai;margin-top:3px;color:var(--mut2);font-size:13px;line-height:1.45;max-width:62ch}
.campo .err{grid-area:er;margin-top:3px;color:var(--bad);font-size:13px;min-height:0}
.campo .err:empty{display:none}
/* campi lunghi: etichetta sopra, controllo a tutta larghezza */
.campo.lg{grid-template-columns:1fr;grid-template-areas:"et" "ctl" "ai" "er";row-gap:0;align-items:start}
.campo.lg>label{font-size:14px;color:var(--mut)}
.campo.lg>input,.campo.lg>textarea{width:100%;margin-top:6px}.campo.lg .aiuto,.campo.lg .err{margin-top:6px}
/* campi corti: in linea, a destra */
.campo.in>input[type=text]{width:112px;text-align:right;padding:8px 12px;font-variant-numeric:tabular-nums}
.campo.in>input[type=time]{width:auto;padding:8px 10px;text-align:center}
.campo.in>select{width:auto;max-width:220px;padding:8px 10px}
.campo.in>.sw{justify-self:end}
@media (max-width:420px){.campo>label{font-size:14.5px}.campo.in>input[type=text]{width:92px}.campo.in>select{max-width:170px}}

/* riquadri delle impostazioni: due colonne indipendenti su schermi larghi (nessun buco tra un riquadro e l'altro) */
.imp-cols{display:flex;flex-direction:column;gap:12px;margin-bottom:12px}
.imp-col{display:contents}
#imp .imp-cols>details.riq,#imp .imp-col>details.riq{margin-bottom:0}
#imp .imp-tab>details.riq{margin-bottom:12px}
@media (min-width:1100px){
  .imp-cols{flex-direction:row;align-items:flex-start;gap:14px}
  .imp-col{display:flex;flex-direction:column;gap:12px;flex:1 1 0;min-width:0}
}
.imp-tab>details.riq.largo .campi{display:flex}
@media (min-width:1100px){
  .imp-tab>details.riq.largo:has(textarea) .campi{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 24px}
  .imp-tab>details.riq.largo[data-id=allarmi] .lista-allarmi{grid-template-columns:repeat(2,minmax(0,1fr))}
}
/* riquadri dell'ultimo ciclo: si dividono sempre tutta la larghezza, qualunque sia il loro numero */
.fermo-g>.tile{flex:1 1 0;min-width:0}
.fermo-g>.tile:first-child{flex-grow:1.5}

.cg .tile,.tc .tile{background:rgba(var(--c),.09);border-color:rgba(var(--c),.3)}
.come-b{font:inherit;font-size:12px;line-height:inherit;font-weight:400;color:var(--acs);background:none;border:0;padding:0;cursor:pointer;white-space:nowrap}
.come-b::after{--chv:5px;--chw:1.5px;margin-left:4px;vertical-align:0}
.come-b.on::after{vertical-align:2px}
.come-b:hover{text-decoration:underline}
.come-t{margin:6px 0 0 27px;color:var(--mut2);font-size:12px;line-height:1.45;text-wrap:pretty}
.come-t[hidden]{display:none}.come-b[data-come^=eur]{color:inherit;font-size:inherit;margin-right:4px;text-decoration:underline dashed;text-decoration-thickness:1.5px;text-underline-offset:2px}.come-b[data-come^=eur]::after{vertical-align:.5px}.card>.come-t{margin:8px 0 0}.card>.come-t .nw{display:block}.card>.come-t b{margin-left:5px;color:var(--txt);font-variant-numeric:tabular-nums}
.tile[data-come]{cursor:pointer}.tile[data-come] small::after{--chv:5px;--chw:1.5px;margin-left:5px;vertical-align:1px}.tile[data-come].on small::after{vertical-align:2px}.tile[data-come] i{vertical-align:3px}.tile i b{font-size:inherit;font-weight:600;color:var(--txt)}
.come-t.sbr{margin:0;font-size:14px;color:var(--txt)}.come-t.sbr>:last-child{margin-top:10px;border:1px solid var(--linea);border-radius:12px;overflow:hidden}.come-t.sbr .tile{text-align:center}.sr{display:grid;grid-template-columns:100px 56px 56px 56px;justify-content:space-evenly;gap:6px;text-align:center;white-space:nowrap;padding:8px 12px;font-variant-numeric:tabular-nums}.sr:nth-child(even){background:var(--bg2)}.sr>:only-child{grid-column:1/-1}
.nota-cv.alto{margin:0 2px 6px}.come-t.alto{margin:0 2px 14px 29px}
.exp a{display:flex;align-items:center;gap:12px;padding:12px 14px;border-radius:12px;border:1px solid var(--linea);background:var(--bg2);color:var(--txt);text-decoration:none}
.exp a:hover{background:rgba(255,255,255,.08)}
.exp svg{flex:none;color:var(--mut)}
.exp b{display:block;font-size:14px}.exp small{display:block;margin-top:1px;color:var(--mut);font-size:12px}
@media(max-width:560px){.exp{grid-template-columns:1fr}}

/* Changelog: tab Pannello / Plugin e filtro per plugin */
.cl-tabs{display:flex;width:max-content;min-width:100%;margin-bottom:0}
.cl-tabs a{white-space:nowrap}
.cl-filtro{width:max-content;max-width:100%;overflow-x:auto;scrollbar-width:none}
.cl-filtro::-webkit-scrollbar{display:none}
.cl-filtro button{white-space:nowrap;flex:none}
.cl-v .cl-h .pill.up{align-self:center}

/* impostazioni installatore: matita, editor, cronologia */
.ch-b{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.dev .riga .matita{flex:none;width:34px;height:34px;border:0;border-radius:10px;background:rgba(255,255,255,.08);color:var(--txt);font-size:16px;cursor:pointer}
.dev .riga .matita:disabled{opacity:.3;cursor:not-allowed}
.dev .riga .matita:not(:disabled):hover{background:var(--acs);color:#06101d}
.dev .riga b .in-attesa{color:#ffc83d;font-weight:650;text-align:right}
.fsv-edit{margin:0 0 6px;padding:12px;border:1px solid var(--acs);border-radius:12px;background:rgba(255,255,255,.04);display:flex;flex-direction:column;gap:8px}
.fsv-edit .fe-t{font-weight:700}
.fsv-edit .fe-r{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px;font-size:14px}
.fsv-edit input,.fsv-edit select{font:inherit;color:var(--txt);background:rgba(255,255,255,.08);border:1px solid var(--linea);border-radius:8px;padding:6px 10px;max-width:100%}
.fsv-edit input[type=number]{width:96px}
.fsv-edit .fe-err{padding:8px 10px;border-radius:8px;background:rgba(255,77,94,.16);color:#ff8a95;font-size:13px;word-break:break-word}
.fsv-edit .fe-b{display:flex;justify-content:flex-end;gap:8px}
.fsv-edit button.cambia{font:inherit;font-weight:650;border:0;border-radius:10px;background:#ffc83d;color:#1a1200;cursor:pointer}
.fsv-edit button.cambia:disabled,.fsv-edit button:disabled{opacity:.5;cursor:wait}
#b-lock-fsv.sbloccato{border:1px solid #ffc83d;color:#ffc83d}
.fsv-edit .fe-r>span:last-child{flex:1 1 200px;min-width:0;display:flex;align-items:center;gap:6px;justify-content:flex-end}
.fsv-edit select{width:100%;min-width:0}
.fsv-edit .avviso-reg{color:#ffc83d;font-size:13px}
''',
    'app.js': r'''// impronta di questa versione dello script: la prende dall'indirizzo con cui e' stato caricato (app.js?v=...)
window.BUILD = (() => { try { return new URL(document.currentScript.src).searchParams.get('v') || ''; } catch { return ''; } })();
(() => {
'use strict';

// ---------- utilità ----------
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const ls = {
  get(k, d) { try { const v = localStorage.getItem('pdc.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('pdc.' + k, JSON.stringify(v)); } catch {} },
};
const num = (v, d = 1) => (v == null || isNaN(v)) ? '—' : Number(v).toLocaleString('it-IT', {minimumFractionDigits: d, maximumFractionDigits: d});
const gr = v => v == null ? '—' : num(v) + '°';
const hhmm = t => new Date(t * 1000).toLocaleTimeString('it-IT', {hour: '2-digit', minute: '2-digit'});
// scritta sotto i valori di una card, nel box colorato dei badge (bad = rosso, warn = giallo)
const avviso = (t, col) => `<span class="ora-n"><span class="badge on" style="--acc:var(--${col})">${t}</span></span>`;
const dm = t => new Date(t * 1000).toLocaleString('it-IT', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
const durata = m => m < 90 ? `${m} min` : `${num(m / 60, 1)} h`;

const PAGINE = {dashboard: 'Dashboard', analisi: 'Analisi', efficienza: 'Efficienza', dispositivi: 'Dispositivi', impostazioni: 'Impostazioni'};
// pagine fuori dalla barra del telefono: raggiungibili da un link (Changelog)
const TITOLI = Object.assign({changelog: 'Changelog'}, PAGINE);
const ICONE = {
  dashboard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7.5" height="9" rx="2"/><rect x="13.5" y="3" width="7.5" height="5.5" rx="2"/><rect x="13.5" y="12" width="7.5" height="9" rx="2"/><rect x="3" y="15.5" width="7.5" height="5.5" rx="2"/></svg>',
  analisi: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/></svg>',
  efficienza: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/></svg>',
  dispositivi: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="5" width="14" height="14" rx="3"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/></svg>',
  impostazioni: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  changelog: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 6h12M8 12h12M8 18h12"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/></svg>',
  esci: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/></svg>',
};
const COL = {casa: '#ff6b57', acs: '#4da3ff', riposo: '#3a4558', nodati: 'url(#tratti)'};
const NOMI = {casa: 'Riscaldamento casa', acs: 'Acqua calda', riposo: 'Riposo', nodati: 'Nessun dato'};

const S = {
  pagina: (location.hash.replace('#/', '').split('/')[0] in TITOLI) ? location.hash.replace('#/', '').split('/')[0] : 'dashboard',
  sub: location.hash.replace('#/', '').split('/')[1] || '',
  range: ls.get('range', '24h'), rangeEff: ls.get('rangeEff', '7g'), custom: ls.get('custom', null),
  dati: null, sse: false, off: 0,
};
const ora = () => Math.floor(Date.now() / 1000) + S.off;

// ---------- struttura ----------
function costruisci() {
  $('#app').innerHTML = `
  <div class="shell">
    <nav class="nav">
      <div class="brand"><img src="/icona.png" alt=""><span>Pompa di calore</span></div>
      ${Object.keys(PAGINE).map(p => `<a href="#/${p}" data-p="${p}">${ICONE[p]}<span>${PAGINE[p]}</span></a>`).join('')}
      <div class="sp"></div>
      <a href="#/changelog" data-p="changelog" class="mini">${ICONE.changelog}<span>Changelog</span></a>
      <form method="post" action="/logout"><button class="esci" type="submit">${ICONE.esci}<span>Esci</span></button></form>
    </nav>
    <main>
      <header class="top">
        <h1 id="titolo"></h1>
        <div class="stato"><span class="dot" id="dot"></span><span id="agg"></span></div>
        <div class="chips" id="chips"></div>
      </header>
      <div class="custom" id="custom" hidden>
        <label>Da<input type="datetime-local" id="c-da"></label>
        <label>A<input type="datetime-local" id="c-a"></label>
        <button id="c-ok" type="button">Applica</button>
      </div>
      <div class="custom" id="indietro" hidden><span id="i-t"></span><button id="c-adesso" type="button">↺ Adesso</button></div>
      <div id="avvisi"></div>
      <section id="pagina"></section>
    </main>
  </div>`;
  $('#chips').addEventListener('click', e => {
    if (e.target.dataset.conf) { S.confronto = !S.confronto; chips(); carica(); return; }  // non si ricorda: a ogni apertura e' spento
    const r = e.target.dataset.r; if (!r) return;
    if (S.pagina === 'efficienza') { S.rangeEff = r; ls.set('rangeEff', r); chips(); carica(); return; }
    S.range = r; S.zoom = null; S.fine = null; ls.set('range', r); ricordaFinestra(); chips();
    if (r === 'custom') apriCustom(); else { $('#custom').hidden = true; carica(); }
  });
  $('#c-adesso').addEventListener('click', () => { S.fine = null; S.zoom = null; chips(); carica(); });
  $('#c-ok').addEventListener('click', () => {
    const da = new Date($('#c-da').value).getTime() / 1000, a = new Date($('#c-a').value).getTime() / 1000;
    if (!isFinite(da) || !isFinite(a) || a <= da) { alert('Controlla le date: "A" deve venire dopo "Da".'); return; }
    S.custom = {da: Math.floor(da), a: Math.floor(a)}; S.zoom = null; S.fine = null; ls.set('custom', S.custom); carica();
  });
}

function loc(t) { const d = new Date(t * 1000); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); }
function apriCustom() {
  const c = S.custom || {da: ora() - 86400, a: ora()};
  $('#c-da').value = loc(c.da); $('#c-a').value = loc(c.a); $('#custom').hidden = false;
  if (!S.custom) { S.custom = c; ls.set('custom', c); carica(); }
}
const RANGE_EFF = {'1g': 1, '3g': 3, '7g': 7, '28g': 28};
function chips() {
  const el = $('#chips');
  el.hidden = S.pagina === 'impostazioni' || S.pagina === 'dispositivi' || S.pagina === 'changelog';
  const eff = S.pagina === 'efficienza';
  const lista = eff ? [['1g', '1 giorno'], ['3g', '3 giorni'], ['7g', '7 giorni'], ['28g', '28 giorni']]
                    : [['6h', '6h'], ['24h', '24h'], ['custom', 'Personalizzato']];
  const sel = eff ? S.rangeEff : S.range;
  const ind = $('#indietro'), dietro = !!S.fine && S.pagina === 'analisi';   // finestra spostata indietro: riga con l'intervallo e il tasto per tornare in diretta
  ind.hidden = !dietro;
  if (dietro) $('#i-t').innerHTML = `Stai guardando <b>${dm(S.fine - (S.range === '6h' ? 6 : 24) * 3600)} → ${dm(S.fine)}</b>`;
  el.innerHTML = lista.map(([r, t]) => `<button data-r="${r}" class="${sel === r ? 'on' : ''}">${t}</button>`).join('')
    + (eff ? `<button data-conf="1" class="conf-btn ${S.confronto ? 'on' : ''}" aria-pressed="${!!S.confronto}" title="Confronta con un periodo della stessa durata subito prima">Confronta</button>` : '');
}

function mostraPagina() {
  $('#titolo').textContent = TITOLI[S.pagina];
  document.querySelectorAll('.nav a').forEach(a => a.classList.toggle('on', a.dataset.p === S.pagina));
  S.fine = null; chips(); S.dati = null; S.dpag = null; S.zoom = null;
  { const st = document.querySelector('.stato'); if (st) st.hidden = S.pagina === 'changelog'; }
  $('#custom').hidden = !(S.pagina !== 'efficienza' && S.pagina !== 'impostazioni' && S.pagina !== 'dispositivi' && S.pagina !== 'changelog' && S.range === 'custom');
  S.sporco = false; S.mod = {};
  if (S.pagina === 'dashboard') { $('#pagina').innerHTML = scheletro(); }
  else if (S.pagina === 'analisi') { $('#pagina').innerHTML = scheletroAnalisi(); }
  else if (S.pagina === 'impostazioni') {
    $('#pagina').innerHTML = '<div id="imp-tabs"></div><div id="imp"><div class="card vuoto">Carico…</div></div><div id="reg" hidden></div>';
    vistaImp();
  }
  else if (S.pagina === 'dispositivi') { $('#pagina').innerHTML = '<div id="dsp"><div class="card vuoto">Carico…</div></div>'; }
  else if (S.pagina === 'changelog') { clDati = null; vistaChangelog(); }
  else { $('#pagina').innerHTML = '<div id="eff"><div class="card vuoto">Calcolo in corso…</div></div>'; }
}

function scheletro() {
  const sc = (id, cls, acc, et, extra, fine = '') => `
  <article class="card acc" style="--acc:var(--${acc})">
    <div class="ch ch-st"><span class="et" id="${id}-et">${et}</span><span class="badge" id="${id}-b"></span></div>
    <div class="val"><div class="n"><span id="${id}-t">—</span><i>${cls}</i></div><div class="spark" id="${id}-sp"></div></div>
    <div class="sub">${extra}</div><div class="nota" id="${id}-n"></div>${fine}
  </article>`;
  return `
  <div class="cards">
    ${sc('r', '°C', 'casa', 'Riscaldamento', '<span>Target <b id="r-tg">—</b></span><span>Ritorno <b id="r-rt">—</b></span><span>ΔT <b id="r-dt">—</b></span>')}
    ${sc('a', '°C', 'acs', 'Acqua calda', '<span>Target <b id="a-tg">—</b></span><span>Modo <b id="a-md">—</b></span>')}
    ${sc('e', '°C', 'est', 'Esterna', '<span>Min <b id="e-mn">—</b></span><span>Max <b id="e-mx">—</b></span>', '<button type="button" class="mt-bl" id="e-mt" data-meteo hidden aria-label="Dettaglio meteo"></button>')}
    ${sc('w', 'kWh', 'en', 'Energia', '<span id="w-per">Periodo</span><span>Ora <b id="w-pw">—</b></span><span id="w-eur-s" hidden>Costo <b id="w-eur">—</b></span>')}
    ${sc('c', '°C', 'amb', 'Casa · Smarther', '<span>Umidità <b id="c-um">—</b></span><span>Target <b id="c-tg">—</b></span>')}
  </div>
  <article class="card">
    <div class="ch"><h2>Modo di funzionamento</h2><div class="riep" id="tl-riep"></div></div>
    <div class="tlw" id="tlw"><div id="tl"></div><div class="tip" id="tip" hidden></div></div>
    <div class="assi" id="assi"></div>
    <div class="legenda">${['casa', 'acs', 'riposo', 'nodati'].map(m => `<span><i style="background:${m === 'nodati' ? '#2a3547' : COL[m]}"></i>${NOMI[m]}</span>`).join('')}</div>
  </article>
  <div class="grid2">
    <div class="colonna">
      <article class="card"><div class="ch"><h2>Stato del sistema</h2><span id="cmd-lock"></span></div><div class="righe" id="sistema"></div></article>
      <article class="card"><div class="ch"><h2>Compressore e circuito</h2><span class="et" id="prest-et"></span></div><div class="tiles" id="prest"></div><p class="nota-sez" id="prest-rit"></p></article>
    </div>
    <article class="card curva"><div class="ch"><h2>Curva climatica</h2><span class="et" id="curva-zona">Zona 1</span></div><div id="curva"></div></article>
  </div>`;
}

// ---------- disegno ----------
function set(id, t) {
  const e = document.getElementById(id); if (!e) return;
  if (e.textContent !== t) {
    if (e.dataset.v) { e.classList.remove('flash'); void e.offsetWidth; e.classList.add('flash'); }
    e.textContent = t; e.dataset.v = '1';
  }
}
function badge(id, t, cls, tip) { const e = document.getElementById(id); if (e) { e.textContent = t || ''; e.className = 'badge ' + (cls || ''); if (tip) e.title = tip; else e.removeAttribute('title'); } }
function html(id, h) { const e = document.getElementById(id); if (e && e.dataset.h !== h) { e.innerHTML = h; e.dataset.h = h; } }

function spark(pts, w = 120, h = 40) {
  if (!pts || pts.length < 2) return '';
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys), dy = (y1 - y0) || 1;
  const P = pts.map(p => `${(((p[0] - x0) / ((x1 - x0) || 1)) * w).toFixed(1)},${(h - 4 - ((p[1] - y0) / dy) * (h - 8)).toFixed(1)}`);
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path d="M${P[0]} L${P.slice(1).join(' L')} L${w},${h} L0,${h}Z" fill="currentColor" fill-opacity=".14"/>
    <path d="M${P[0]} L${P.slice(1).join(' L')}" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>`;
}

function periodo(d) {
  if (S.range === 'custom') return dm(d.da) + ' → ' + dm(d.a);
  return S.range === '6h' ? 'ultime 6 ore' : 'ultime 24 ore';
}

function disegna() {
  const d = S.dati; if (!d || S.dpag !== S.pagina) return;
  stato(); avvisi();
  if (S.pagina === 'analisi') return disegnaAnalisi(d);
  if (S.pagina === 'efficienza') return disegnaEfficienza(d);
  if (S.pagina === 'impostazioni') return disegnaImpostazioni(d);
  if (S.pagina === 'dispositivi') return disegnaDispositivi(d);
  const r = d.riscaldamento, a = d.acs, e = d.esterna, w = d.energia, c = d.casa;

  set('r-t', num(r.temp)); set('r-tg', gr(r.target)); set('r-rt', gr(r.ritorno)); set('r-dt', r.delta == null ? '—' : num(r.delta) + '°');
  badge('r-b', {attivo: 'In funzione', riposo: 'A riposo', spento: 'Spento', sbrinamento: 'Sbrinamento'}[r.stato], ({attivo: 'acceso', riposo: 'riposo', spento: 'spento', sbrinamento: 'warn'})[r.stato] || '',
    {attivo: 'Il compressore è in funzione per i termosifoni/pavimento', riposo: 'Pompa accesa ma compressore fermo: la casa non chiede calore', spento: 'La pompa di calore è spenta', sbrinamento: 'Sta sciogliendo il ghiaccio dall\'unità esterna'}[r.stato]);
  html('r-sp', spark(r.spark));

  set('a-t', num(a.temp)); set('a-tg', gr(a.target)); set('a-md', a.modo || '—');
  badge('a-b', {attivo: 'In riscaldamento', standby: 'Standby', spento: 'Spento'}[a.stato], ({attivo: 'acceso', standby: 'riposo', spento: 'spento'})[a.stato] || '',
    {attivo: 'Il compressore sta scaldando il bollitore', standby: 'Acqua calda attiva ma non sta scaldando: è già alla temperatura impostata', spento: 'La produzione di acqua calda è spenta'}[a.stato]);
  html('a-sp', spark(a.spark));

  set('e-t', num(e.temp)); set('e-mn', gr(e.min)); set('e-mx', gr(e.max)); html('e-sp', spark(e.spark)); meteoRiga(d.meteo);
  badge('e-b', 'Temperatura', 'tema', 'Temperatura dell\'aria esterna');
  html('e-n', e.ts && d.ora - e.ts > 7200 ? avviso('Dato delle ' + hhmm(e.ts) + ' (PdC ferma)', 'warn')
    : e.umidita != null ? `<span class="um-n" title="Umidità dell'aria esterna (stazione meteo vicina)">Umidità <b>${num(e.umidita, 0)}%</b></span>` : '');   // l'avviso, quando c'è, prende il posto dell'umidità

  set('w-t', num(w.kwh, w.kwh != null && w.kwh < 10 ? 2 : 1)); set('w-per', periodo(d)); set('w-pw', w.potenza_w == null ? '—' : num(w.potenza_w, 0) + ' W');
  { const e = document.getElementById('w-eur-s'); if (e) { e.hidden = !(w.prezzo_kwh > 0 && w.kwh != null); set('w-eur', w.kwh != null ? euro(w.kwh * w.prezzo_kwh) : '—'); const dt = w.dettaglio; e.title = dt ? `Riscaldamento ${euro(dt.casa)} · Acqua calda ${euro(dt.acs)} · Standby ${euro(dt.riposo)}` : ''; } }
  badge('w-b', 'Consumo', 'tema', 'Energia elettrica consumata dalla pompa nel periodo scelto');
  html('w-sp', spark(w.spark));

  set('c-t', num(c.temp)); set('c-um', c.umidita == null ? '—' : num(c.umidita, 0) + '%');
  { const u = $('#c-um'), so = c.soglie_umidita || [60, 70]; if (u) u.className = c.umidita == null ? '' : c.umidita > so[1] ? 'um-rosso' : c.umidita > so[0] ? 'um-giallo' : ''; } set('c-tg', gr(c.target));
  logoTitolo(d);
  badge('c-b', c.temp == null ? '' : (c.richiesta ? 'Richiesta calore' : 'Nessuna richiesta'), c.richiesta ? 'caldo' : 'riposo',
    c.temp == null ? '' : (c.richiesta ? 'Lo Smarther sta chiedendo di scaldare la casa' : 'Lo Smarther non chiede calore: casa a temperatura o fuori fascia oraria'));
  html('c-sp', spark(c.spark));
  html('c-n', c.temp == null ? avviso('Smarther non collegato', 'bad') : (c.ts && d.ora - c.ts > 900) ? avviso('Ultimo dato delle ' + hhmm(c.ts), 'warn')
    : c.riparte ? `<span class="ora-n"><i class="ck"></i><span class="badge on">Riparte intorno alle ${hhmm(c.riparte)}</span></span>` : '');

  timeline(d.modi, d.da, d.a);
  sistema(d);
  prestazioni(d);
  curva(d.curva);
}

function timeline(m, da, a) {
  const W = 1000, H = 46, span = Math.max(1, a - da);
  const rects = m.segmenti.map(([s0, s1, mo]) =>
    `<rect x="${((s0 - da) / span * W).toFixed(2)}" width="${Math.max(1.5, (s1 - s0) / span * W).toFixed(2)}" height="${H}" fill="${COL[mo]}"/>`).join('');
  html('tl', `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><defs><pattern id="tratti" width="12" height="12" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="12" height="12" fill="#161e2c"/><rect width="5" height="12" fill="#26324a"/></pattern></defs>${rects}</svg>`);
  const f = span > 36 * 3600 ? dm : hhmm;
  html('assi', [0, 1, 2, 3, 4, 5].map(i => `<span>${f(da + span * i / 5)}</span>`).join(''));
  const mi = m.minuti, parti = [];
  if (mi.casa) parti.push(`<span><b style="color:var(--casa)">${durata(mi.casa)}</b> riscaldamento</span>`);
  if (mi.acs) parti.push(`<span><b style="color:var(--acs)">${durata(mi.acs)}</b> acqua calda</span>`);
  parti.push(`<span><b>${durata(mi.riposo)}</b> riposo</span>`);
  html('tl-riep', parti.join(''));
  const w = $('#tlw');
  w._m = {m, da, span};
}

function tooltipTimeline(e) {
  const w = $('#tlw'); if (!w || !w._m) return;
  const {m, da, span} = w._m, r = w.getBoundingClientRect();
  const f = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), t = da + f * span;
  const s = m.segmenti.find(x => t >= x[0] && t <= x[1]); if (!s) return;
  const tip = $('#tip');
  tip.innerHTML = `<b style="color:${s[2] === 'nodati' || s[2] === 'riposo' ? 'var(--mut)' : COL[s[2]]}">${NOMI[s[2]]}</b> · ${hhmm(s[0])}–${hhmm(s[1])} (${durata(Math.round((s[1] - s[0]) / 60))})`;
  tip.hidden = false;
  const mezzo = tip.offsetWidth / 2 + 2;   // larghezza vera del tooltip: sta sempre dentro la barra
  tip.style.left = Math.max(mezzo, Math.min(r.width - mezzo, f * r.width)) + 'px';
}

function riga(k, v) { return `<div class="riga"><span>${k}</span><b>${v}</b></div>`; }
const online = b => `<span class="pill ${b == null ? 'no' : b ? 'si' : 'off'}">${b == null ? '—' : b ? 'ONLINE' : 'OFFLINE'}</span>`;
const sino = b => `<span class="pill ${b ? 'si' : 'off'}">${b ? 'ACCESO' : 'SPENTO'}</span>`;
// ---- comandi: accendi/spegni (sbloccabili per 30 secondi) ----
S.opt = S.opt || {}; S.sblocco = S.sblocco || 0;
const MODI_NT = {schedule: 'programma', home: 'programma', manual: 'manuale', away: 'assente', hg: 'antigelo', off: 'spento', max: 'massimo'};
const COMANDI_UI = [['riscaldamento', 'Riscaldamento casa'], ['acqua', 'Acqua calda sanitaria'], ['smarther', 'Termostato Smarther']];
function statoCmd(cosa, s) {
  const reale = cosa === 'riscaldamento' ? s.riscaldamento_acceso : cosa === 'acqua' ? s.pdc_accesa
    : (s.smarther_modo ? !(['off', 'hg'].includes(s.smarther_modo) || s.smarther_modo_casa === 'hg') : null);
  const o = S.opt[cosa];
  if (o) { if (reale === o.v || Date.now() > o.fino) delete S.opt[cosa]; else return o.v; }
  return reale;
}
function rigaCmd(cosa, nome, s) {
  const v = statoCmd(cosa, s);
  if (v == null) return riga(nome, '<span class="mut">—</span>');
  const sbl = Date.now() < S.sblocco, occ = !!(S.occ && S.occ[cosa]);
  const mReale = s.smarther_modo_casa === 'hg' ? 'hg' : s.smarther_modo;
  const m = cosa === 'smarther' && mReale ? ` <small class="mut">· ${esc(MODI_NT[mReale] || mReale)}</small>` : '';
  return `<div class="riga"><span>${nome}${m}</span><b class="cmd">${occ ? '<span class="pill warn">ATTENDI…</span>' : sino(v)}<label class="sw ${sbl && !occ ? '' : 'bloccato'}"><input type="checkbox" data-cmd="${cosa}" aria-label="${nome}" ${v ? 'checked' : ''} ${sbl && !occ ? '' : 'disabled'}><span></span></label></b></div>`;
}
// lo sblocco dura 30 s dall'ULTIMA azione: ogni comando (e ogni conferma) riparte da capo
function armaSblocco() { clearTimeout(S.tLock); S.sblocco = Date.now() + 30000; S.tLock = setTimeout(() => { S.sblocco = 0; ridisegnaComandi(); }, 30200); }
function ridisegnaComandi() { if (S.dati && S.pagina === 'dashboard' && S.dati.sistema) sistema(S.dati); }
document.addEventListener('click', e => {
  if (!(e.target.closest && e.target.closest('#b-lock'))) return;
  clearTimeout(S.tLock);
  if (Date.now() < S.sblocco) S.sblocco = 0;
  else armaSblocco();
  ridisegnaComandi();
});
document.addEventListener('change', async e => {
  const i = e.target.closest && e.target.closest('input[data-cmd]'); if (!i) return;
  const cosa = i.dataset.cmd, acceso = i.checked, nome = (COMANDI_UI.find(c => c[0] === cosa) || [, cosa])[1];
  if (!acceso && !window.confirm('Spegnere: ' + nome + '?')) { i.checked = true; armaSblocco(); ridisegnaComandi(); return; }
  armaSblocco();
  S.occ = S.occ || {}; S.occ[cosa] = true; S.opt[cosa] = {v: acceso, fino: Date.now() + 120000};
  ridisegnaComandi();  // badge giallo "Attendi…" finche' il comando non e' finito
  try {
    const r = await inviaPost('/api/controlli', {cosa, acceso});
    if (r.ok) toast(nome + ': comando inviato');
    else { delete S.opt[cosa]; toast(r.errore || 'Comando non riuscito', true); }
  } catch { delete S.opt[cosa]; toast('Comando non riuscito: il pannello non risponde', true); }
  delete S.occ[cosa];
  armaSblocco(); ridisegnaComandi();
});

function sistema(d) {
  const s = d.sistema, sbl = Date.now() < S.sblocco;
  html('cmd-lock', `<button type="button" class="sec piccolo ${sbl ? 'sbloccato' : ''}" id="b-lock">${sbl ? 'Comandi sbloccati · Blocca' : 'Sblocca comandi'}</button>`);
  html('sistema', [
    ...COMANDI_UI.map(([k, n]) => rigaCmd(k, n, s)),
    riga('Modo acqua calda', esc(d.acs.modo || '—')),
    riga('Valvola deviatrice', s.valvola === 'room' ? 'Verso il riscaldamento' : s.valvola ? 'Verso il bollitore' : '—'),
    riga('Sbrinamento', s.sbrinamento ? '<span class="pill warn">IN CORSO</span>' : 'No'),
    riga('Collegamento SmartThings', online(s.salute === 'online' ? true : s.salute ? false : null)),
    riga('Collegamento Smarther', online(s.smarther === 'si' ? true : s.smarther ? false : null)),
  ].join(''));
}

function notaRefresh(f) {
  if (!f || !f.attivo) return '';
  const col = f.stato === 'permesso' || f.stato === 'errore' ? 'var(--warn,#ffc83d)' : f.stato === 'attivo' ? 'var(--ok,#62eba6)' : 'var(--mut2)';
  const t = f.stato === 'attivo' ? `refresh attivo ogni ${f.ogni} s`
    : f.stato === 'a riposo' ? 'refresh pronto (parte quando la pompa lavora)'
    : f.stato === 'permesso' ? 'refresh fermo: ' + (f.messaggio || 'manca il permesso di comando')
    : f.stato === 'errore' ? 'refresh in pausa: ' + (f.messaggio || 'errore')
    : 'refresh attivo';
  return `<span class="rf"><i style="background:${col}"></i>${esc(t)}</span>`;
}
function prestazioni(d) {
  const p = d.prestazioni, vecchio = !d.prestazioni_ts || d.ora - d.prestazioni_ts > 900;
  const attivo = (p.freq_compressore || 0) > 0;
  const rt = document.getElementById('prest-rit'), r = d.ritardo_ciclo, et = document.getElementById('prest-et');
  const m = d.prestazioni_ts ? Math.round((d.ora - d.prestazioni_ts) / 60) : null;
  // il compressore arriva da SmartThings con qualche minuto di ritardo: si dice quanti, cosi' non sembra un errore
  if (et) {
    et.textContent = m == null || (!attivo && m <= 20) ? '' : m < 2 ? 'dato aggiornato' : 'dato di ' + (m < 90 ? m + ' min' : Math.round(m / 60) + ' ore') + ' fa';
    et.className = 'et' + (m != null && m > 20 ? ' vecchio-et' : '');
  }
  if (!attivo) {
    // compressore fermo: invece di una fila di zeri, una riga sull'ultimo ciclo
    const u = d.ultimo_ciclo, stesso = u && new Date(u.inizio * 1000).toDateString() === new Date(d.ora * 1000).toDateString();
    const minuti = u ? Math.max(1, Math.round((u.fine - u.inizio) / 60)) : 0;
    const dur = minuti >= 90 ? num(minuti / 60, 1) + ' ore' : minuti + ' min';
    const giorno = u && !stesso ? new Date(u.inizio * 1000).toLocaleDateString('it-IT', {day: '2-digit', month: '2-digit'}) + ' ' : '';
    const tile = (et, v) => `<div class="tile"><small>${et}</small><b class="t-txt">${v}</b></div>`;
    html('prest', `<div class="fermo-w"><div class="fermo-t"><span class="pill up">Fermo</span><span class="fermo-q">${u ? `Ultimo ciclo · ${giorno}${hhmm(u.inizio)}–${hhmm(u.fine)}` : 'Nessun ciclo negli ultimi 3 giorni'}</span></div>` +
      (u ? `<div class="fermo-g">${tile('Per', u.modo === 'acs' ? 'Acqua calda' : 'Riscaldamento')}${tile('Durata', dur)}${u.kwh != null ? tile('Energia', num(u.kwh, 1) + ' <i>kWh</i>') : ''}</div>` : '') + '</div>');
    if (rt) rt.textContent = '';
    return;
  }
  const rf = notaRefresh(d.refresh);
  if (rt) rt.innerHTML = (r ? `<span class="rt-t">Ritardo dei dati · ultime 24 ore</span><span class="nw">tipico <b>${num(r.tipico_min, 1)} min</b></span><span class="nw">massimo <b>${num(r.max_min, 1)} min</b></span>` : '') + rf;
  const t = (et, v, u, dec = 0) => `<div class="tile ${vecchio ? 'vecchio' : ''}"><small>${et}</small><b>${v == null ? '—' : num(v, dec)}</b><i>${u}</i></div>`;
  html('prest', t('Compressore', p.freq_compressore, 'Hz') + t('Pompa', p.vel_pompa, '%') + t('Portata', p.portata, 'L/min', 1) +
    (p.scarico == null ? '' : t('Gas di scarico', p.scarico, '°C')) + `<div class="tile"><small>Potenza</small><b>${d.energia.potenza_w == null ? '—' : num(d.energia.potenza_w, 0)}</b><i>W</i></div>`);
}

function nice(a, b, step) { const o = []; for (let v = Math.ceil(a / step) * step; v <= b + 1e-9; v += step) o.push(v); return o; }
function curva(c) {
  if (!c) { html('curva', '<div class="vuoto">Le impostazioni della curva non sono ancora state lette dalla PdC.</div>'); return; }
  const zn = $('#curva-zona'); if (zn) { zn.textContent = c.zona === 2 ? 'Zona 2' : 'Zona 1 · pavimento'; zn.title = c.zona_fonte ? 'Zona dedotta da: ' + c.zona_fonte : ''; }
  const W = 480, H = 300, L = 46, R = 0, T = 16, B = 44;   // R = 0: la fine del grafico sta sul bordo dei riquadri sotto (l'ultimo numero dell'asse finisce sul bordo)
  const xs = [-10, 25, c.basso.esterna - 3, c.alto.esterna + 3, ...c.punti.map(p => p[0])];
  const ys = [20, 60, c.alto.mandata - 4, c.basso.mandata + 6, ...c.punti.map(p => p[1])];
  const x0 = Math.floor(Math.min(...xs) / 5) * 5, x1 = Math.ceil(Math.max(...xs) / 5) * 5;
  const y0 = Math.floor(Math.min(...ys) / 10) * 10, y1 = Math.ceil(Math.max(...ys) / 10) * 10;
  const X = v => L + (v - x0) / (x1 - x0) * (W - L - R), Y = v => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
  let g = '';
  nice(x0, x1, 5).forEach(v => { g += `<line x1="${X(v)}" x2="${X(v)}" y1="${T}" y2="${H - B}" stroke="rgba(255,255,255,.06)"/><text class="ass" x="${X(v)}" y="${H - B + 16}" text-anchor="${X(v) > W - 12 ? 'end' : 'middle'}">${v}°</text>`; });   // l'ultimo numero finisce sul bordo, non sporge
  nice(y0, y1, 10).forEach(v => { g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="rgba(255,255,255,.06)"/><text class="ass" x="${L - 8}" y="${Y(v) + 4}" text-anchor="end">${v}°</text>`; });
  const A = c.basso, Z = c.alto;
  const pts = c.punti.map(p => `<circle cx="${X(p[0]).toFixed(1)}" cy="${Y(p[1]).toFixed(1)}" r="2.6" fill="#ff6b57" fill-opacity=".38"/>`).join('');
  let ora_ = '';
  if (c.ora.esterna != null && c.ora.mandata != null) {
    const ox = X(Math.max(x0, Math.min(x1, c.ora.esterna))), oy = Y(c.ora.mandata);
    ora_ = `<line x1="${ox}" x2="${ox}" y1="${T}" y2="${H - B}" stroke="#fff" stroke-opacity=".35" stroke-dasharray="3 4"/>
      <circle cx="${ox}" cy="${oy}" r="6" fill="#0b0f17" stroke="#fff" stroke-width="2"/>
      <text x="${ox}" y="${T + 11}" text-anchor="${ox > W - 70 ? 'end' : 'start'}" dx="${ox > W - 70 ? -6 : 6}" fill="#fff">Ora</text>`;
  }
  const lab = (x, y, t, col) => `<circle cx="${X(x)}" cy="${Y(y)}" r="6" fill="${col}" stroke="#0b0f17" stroke-width="2"/><text x="${X(x)}" y="${Y(y) - 12}" text-anchor="middle" fill="#e8edf5" style="font-size:12.5px;font-weight:650">${t}</text>`;
  html('curva', `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Curva climatica">${g}${pts}
    <path d="M${X(x0)},${Y(A.mandata)} L${X(A.esterna)},${Y(A.mandata)}" stroke="#4da3ff" stroke-opacity=".5" stroke-width="2.4" stroke-dasharray="5 5" fill="none"/>
    <path d="M${X(Z.esterna)},${Y(Z.mandata)} L${X(x1)},${Y(Z.mandata)}" stroke="#34d3b0" stroke-opacity=".5" stroke-width="2.4" stroke-dasharray="5 5" fill="none"/>
    <path d="M${X(A.esterna)},${Y(A.mandata)} L${X(Z.esterna)},${Y(Z.mandata)}" stroke="url(#gc)" stroke-width="3.2" fill="none" stroke-linecap="round"/>
    <defs><linearGradient id="gc" x1="0" x2="1"><stop offset="0" stop-color="#4da3ff"/><stop offset="1" stop-color="#34d3b0"/></linearGradient></defs>
    ${ora_}${lab(A.esterna, A.mandata, num(A.mandata, 0) + '°C', '#4da3ff')}${lab(Z.esterna, Z.mandata, num(Z.mandata, 0) + '°C', '#34d3b0')}
    <text x="${(L + W - R) / 2}" y="${H - 6}" text-anchor="middle">Temperatura esterna (°C)</text>
    <text transform="translate(8.5 ${(T + H - B) / 2}) rotate(-90)" text-anchor="middle">Mandata (°C)</text>
</svg>
    <div class="box2">
      <div class="bx tn c-acs"><small class="eti">Freddo</small><p><b>${num(A.mandata, 0)}°C</b><span>mandata a <strong>${num(A.esterna, 0)}°C</strong> esterni</span></p></div>
      <div class="bx tn c-est"><small class="eti">Mite</small><p><b>${num(Z.mandata, 0)}°C</b><span>mandata a <strong>${num(Z.esterna, 0)}°C</strong> esterni</span></p></div>
      ${c.ora.esterna != null ? `<div class="bx tn c-casa"><small class="eti">Adesso</small><p><b>${num(c.ora.mandata)}°C</b><span>mandata prevista a <strong>${num(c.ora.esterna)}°C</strong> esterni</span></p></div>` : ''}
    </div>
    <ul class="nota-cv">
      <li><i class="lg"></i>Linea dalle impostazioni della PdC</li>
      <li><i class="pt"></i>Puntini: mandate misurate mentre scaldava</li>
      <li><i class="in"></i>Offset del pannello non leggibile</li>
    </ul>`);
}

function avvisi() {
  html('avvisi', (S.dati.eventi || []).map(e =>
    `<div class="avviso ${e.livello === 'errore' ? '' : 'warn'}"><span>${e.livello === 'errore' ? '⛔' : '⚠️'}</span><div>${esc(e.testo)}<small>dalle ${hhmm(e.ts)}</small></div></div>`).join(''));
}

function stato() {
  const d = S.dati; if (!d) return;
  const et = ora() - (d.ultimo_ok || 0);
  let cls = 'ok', t = d.ultimo_ok ? 'Aggiornato ' + hhmm(d.ultimo_ok) : 'In attesa dei primi dati';
  if ((d.eventi || []).some(e => e.livello === 'errore')) cls = 'bad';
  else if (!d.ultimo_ok || et > Math.max(240, d.intervallo * 3) || !S.sse) cls = 'warn';
  if (!S.sse) t += ' · tempo reale non collegato';
  if (d.demo) t = 'DEMO · ' + t;
  $('#dot').className = 'dot ' + cls; $('#agg').textContent = t;
}

// ---------- grafici (Analisi) ----------
const COLS = {casa: COL.casa, acs: COL.acs, fin: '#3ddc97', est: '#34d3b0', en: '#ffb547', amb: '#a78bfa', ritorno: '#f59ac7', umid: '#7dd3fc'};
const tz = () => -new Date().getTimezoneOffset() * 60;

function scalaNice(lo, hi, nmax = 4) {
  if (!isFinite(lo) || !isFinite(hi)) return {lo: 0, hi: 1, tick: [0, 1]};
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const grezzo = (hi - lo) / nmax, mag = Math.pow(10, Math.floor(Math.log10(grezzo)));
  const passo = [1, 2, 2.5, 5, 10].map(f => f * mag).find(s => s >= grezzo) || 10 * mag;
  const tick = []; for (let v = Math.ceil(lo / passo) * passo; v <= hi + 1e-9; v += passo) tick.push(+v.toFixed(6));
  return {lo, hi, tick, passo};
}
function tickTempo(da, a, max) {
  const passi = [600, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800];
  const step = passi.find(s => (a - da) / s <= max) || 604800, off = tz(), out = [];
  for (let t = Math.ceil((da + off) / step) * step - off; t <= a; t += step) out.push(t);
  return {step, tick: out};
}
function etTempo(t, step) {
  const d = new Date(t * 1000);
  if (step >= 86400) return d.toLocaleDateString('it-IT', {day: '2-digit', month: '2-digit'});
  const h = hhmm(t);
  return h === '00:00' ? d.toLocaleDateString('it-IT', {day: '2-digit', month: '2-digit'}) : h;
}
function percorso(punti, X, Y, buco) {
  let s = '', prec = null;
  for (const [t, v] of punti) {
    s += (prec === null || t - prec > buco ? 'M' : 'L') + X(t).toFixed(1) + ' ' + Y(v).toFixed(1) + ' ';
    prec = t;
  }
  return s;
}
function fasce(modi, da, a, X, y0, h) {
  return (modi || []).filter(s => s[2] === 'casa' || s[2] === 'acs').map(([s0, s1, m]) =>
    `<rect x="${X(Math.max(s0, da)).toFixed(1)}" y="${y0}" width="${Math.max(1, X(Math.min(s1, a)) - X(Math.max(s0, da))).toFixed(1)}" height="${h}" fill="${COLS[m]}" opacity=".10"/>`).join('');
}
function fasceRichiesta(r, da, a, X, y0, h, op = .13) {
  const iv = [];     // intervalli di richiesta; i campioni consecutivi si fondono in un blocco solo
  for (let i = 0; i < r.length; i++) {
    if (r[i][1] < 0.5) continue;
    const t0 = r[i][0], t1 = i + 1 < r.length ? Math.min(r[i + 1][0], t0 + 900) : t0 + 300, ult = iv[iv.length - 1];
    if (ult && t0 <= ult[1] + 1) ult[1] = Math.max(ult[1], t1); else iv.push([t0, t1]);
  }
  return iv.map(([t0, t1]) => `<rect x="${X(Math.max(t0, da)).toFixed(1)}" y="${y0}" width="${Math.max(1.5, X(Math.min(t1, a)) - X(Math.max(t0, da))).toFixed(1)}" height="${h}" fill="${COLS.casa}" opacity="${op}"${op > .5 ? ' rx="2"' : ''}/>`).join('');
}

// finestre aperte: fascia dietro le linee (come le altre fasce) e iconcina in alto per toccarla; indice = posizione in d.finestre
function fasceFinestre(fin, da, a, X, y0, h) {
  return fin.filter(f => f.a > da && f.da < a).map(f => {      // confermata: piena; da confermare: tenue con i bordi tratteggiati
    const x0 = X(Math.max(f.da, da)), x1 = X(Math.min(f.a, a)), ok = f.stato === 'si';
    return `<rect x="${x0.toFixed(1)}" y="${y0}" width="${Math.max(2, x1 - x0).toFixed(1)}" height="${h}" fill="${COLS.fin}" opacity="${ok ? .2 : .08}"/>`
      + (ok ? '' : [[f.da, x0], [f.a, x1]].filter(([t]) => t > da + 1 && t < a - 1).map(([, x]) => `<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${y0}" y2="${y0 + h}" class="bordo-vuoto fin"/>`).join(''));
  }).join('');
}
function iconeFinestre(fin, da, a, X, xmin, xmax, y) {
  if (S.tel || a - da > 7 * 86400) return '';      // su telefono solo le fasce (si guarda e basta); su periodi lunghi le icone si accavallano: restano le fasce
  return fin.map((f, i) => {
    if (f.a <= da || f.da >= a) return '';
    const x = Math.min(Math.max(X((Math.max(f.da, da) + Math.min(f.a, a)) / 2), xmin), xmax), ok = f.stato === 'si';
    return `<g data-fin="${i}" role="button" tabindex="0" aria-label="${ok ? 'Finestra aperta' : 'Possibile finestra aperta'}" transform="translate(${x.toFixed(1)} ${y})"><circle r="13" fill="transparent"/><circle r="12" fill="${ok ? COLS.fin : 'var(--card)'}" stroke="${COLS.fin}" stroke-width="1.2"/><g transform="scale(1.2)" fill="none" stroke="${ok ? '#06101d' : COLS.fin}" stroke-width="1.15" stroke-linecap="round" stroke-linejoin="round"><path d="M-3.2 -4.7H3.2V4.7H-3.2zM-3.2 -4.7L-7.2 -5.8V5.8L-3.2 4.7M-7.2 0H-3.2M3.2 -4.7L7.2 -5.8V5.8L3.2 4.7M7.2 0H3.2"/></g></g>`;
  }).join('');
}

function scheletroAnalisi() {
  return `<div class="card"><div class="ch"><h2>Modo di funzionamento</h2><div class="riep" id="tl-riep"></div></div>
    <div class="tlw" id="tlw"><div id="tl"></div><div class="tip" id="tip" hidden></div></div><div class="assi" id="assi"></div></div>
    <div class="custom" id="fin-man" hidden>
      <label>Da<input type="datetime-local" id="fin-da"></label>
      <label>A<input type="datetime-local" id="fin-a"></label>
      <button id="fin-ok" type="button">Segnala finestra</button>
    </div>
    <div id="grafici"></div>
    <ul class="nota-cv lun"><li><i class="in"></i><span>Le zone colorate dietro le linee indicano quando la PdC scalda la casa (rosso) o l'acqua (blu); le righe oblique grigie, i periodi senza dati.</span></li>
      <li><i class="in"></i><span>Fascia verde nel grafico della temperatura ambiente: una finestra aperta. Piena = confermata da te; tenue con i bordi tratteggiati = da confermare (da computer, toccando l’iconcina).</span></li>
      <li><i class="in"></i><span>Tocca o passa il mouse su un grafico per leggere i valori: i grafici sono sincronizzati.</span></li>
      <li class="tt"><i class="in"></i><span>Su telefono: pizzica con due dita per ingrandire il tempo, trascina con un dito per spostarti, doppio tocco per tornare alla vista intera.</span></li></ul>`;
}

// zoom sul tempo (due dita): finestra [da, a] dentro i dati gia' scaricati; ogni linea tiene un punto in piu' per lato
function vistaZoom(o) {
  const z = S.zoom;
  if (!z || z.da < o.da - 1 || z.a > o.a + 1 || z.a - z.da >= (o.a - o.da) * 0.98) { S.zoom = null; return o; }
  const m = Math.max((o.passo || 60) * 4, 1000);
  return {...o, pieno: o, da: z.da, a: z.a, grafici: o.grafici.map(g => ({...g, linee: g.linee.map(l => ({...l, punti: l.punti.filter(p => p[0] >= z.da - m && p[0] <= z.a + m)}))}))};
}
function disegnaAnalisi(p) {
  const d = vistaZoom(p.pieno || p); S.dati = d;
  timeline(d.modi, d.da, d.a);
  const box = $('#grafici');
  const firma = d.da + ':' + d.a + ':' + d.ora + ':' + box.clientWidth + ':' + (S.zoom ? 'z' : '');
  if (box.dataset.f === firma) return;
  box.dataset.f = firma;
  const y0 = window.scrollY;
  const W = Math.max(280, box.clientWidth || 600), H = 168, tel = W < 480, Ws = tel ? W - 11 : W - 32;   // telefono: il grafico esce dal padding della card
  S.tel = tel;
  box.innerHTML = d.grafici.filter(g => g.linee.length).map(g => `
    <article class="card gr" data-g="${g.id}">
      <div class="ch"><h2>${g.titolo}</h2><div class="legg" data-l="${g.id}"></div></div>
      <div class="grw"${tel ? ' style="margin:0 -6px 0 -9px"' : ''}>${graficoLineare(g, d, Ws, H)}</div>${g.id === 'casa' && !tel ? '<div class="azioni"><button type="button" class="sec piccolo" id="fin-add" title="Segna a mano una finestra aperta">Segnala finestra</button></div>' : ''}
    </article>`).join('') || '<div class="card vuoto">Nessun dato in questo periodo.</div>';
  S.grafici = d.grafici; S.gW = Ws;
  d.grafici.forEach(g => legenda(g, null));
  if (window.scrollY !== y0) window.scrollTo(0, y0);
}

// linea a gradini: ogni valore dura fino a meta' strada dal punto successivo; dove c'e' un buco i pezzi si staccano
function segmentiGradini(punti, X, buco, mezzo) {
  const segs = []; let cur = null, prec = null;
  for (let i = 0; i < punti.length; i++) {
    const [t, v] = punti[i];
    if (prec === null || t - prec > buco) { cur = []; segs.push(cur); cur.push([t - mezzo, v]); }
    else cur.push([(t + prec) / 2, v]);
    const succ = punti[i + 1];
    if (!succ || succ[0] - t > buco) cur.push([t + mezzo, v]);
    prec = t;
  }
  return segs;
}
function percorsoGradini(punti, X, Y, buco, mezzo, area, base) {
  let s = '';
  segmentiGradini(punti, X, buco, mezzo).forEach(seg => {
    let ultY = null, x0 = null, x1 = null, p = '';
    seg.forEach(([t, v], i) => {
      const x = X(t).toFixed(1), y = Y(v).toFixed(1);
      p += (i === 0 ? 'M' + x + ' ' + y + ' ' : 'L' + x + ' ' + ultY + ' L' + x + ' ' + y + ' ');
      if (i === 0) x0 = x; ultY = y; x1 = x;
    });
    if (seg.length === 1) p += 'L' + x0 + ' ' + ultY + ' ';
    s += area ? p + 'L' + x1 + ' ' + base + ' L' + x0 + ' ' + base + ' Z ' : p;
  });
  return s;
}

let cvTesto = null;
function largTesto(t) {   // larghezza vera di un testo dei grafici (11 px, stesso font della pagina) misurata dal browser
  try {
    if (!cvTesto) { cvTesto = document.createElement('canvas').getContext('2d'); cvTesto.font = '11px ' + getComputedStyle(document.body).fontFamily; }
    return cvTesto.measureText(t).width;
  } catch { return t.length * 6.5; }
}
// colonna dei numeri dell'asse verticale, larga quanto serve. Telefono: stima (2 cifre ≈ 21 px); PC: misura vera col font della pagina,
// cosi' la prima cifra cade sulla prima lettera del titolo
// asse Y dei grafici di Efficienza: larghezza della colonna dei numeri e righe orizzontali con i valori (solo da zero in su)
const larghezzaNumeriY = (sc, tel) => colonnaNumeri(sc.tick.filter(v => v >= 0).map(v => num(v, 0)), tel);
const grigliaY = (sc, Y, L, W, R) => sc.tick.filter(v => v >= 0).map(v => `<line x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" class="gl"/><text x="${L - 4}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${num(v, 0)}</text>`).join('');
function colonnaNumeri(et, tel) {
  return tel ? Math.ceil(Math.max(...et.map(t => t.length)) * 6.5) + 8 : Math.ceil(Math.max(...et.map(largTesto))) + 4;
}
function graficoLineare(g, d, W, H) {
  const doppio = g.linee.some(l => l.asse === 1), tel = W < 480, T = 8, B = 22, ph = H - T - B;
  let L = 40, R = 8, pw = W - L - R;
  const range = a => {
    let lo = Infinity, hi = -Infinity;
    g.linee.filter(l => (l.asse || 0) === a).forEach(l => l.punti.forEach(p => { if (p[1] < lo) lo = p[1]; if (p[1] > hi) hi = p[1]; }));
    return [lo, hi];
  };
  let [lo, hi] = range(0);
  if (g.id === 'circuito' || g.id === 'pot') lo = Math.min(lo, 0);
  const soglie = g.id === 'umid' ? (d.soglie_umidita || []).filter(v => hi > v - 10 && v > 0) : [];  // le linee delle soglie si vedono solo se i dati ci si avvicinano
  soglie.forEach(v => { if (v > hi) hi = v; });
  const sc = scalaNice(lo, hi, 4);
  let sc1 = null, Y1 = null;
  if (doppio) { const r = range(1); sc1 = scalaNice(Math.min(0, r[0]), r[1], 4); Y1 = v => T + ph - (v - sc1.lo) / (sc1.hi - sc1.lo) * ph; }
  {   // colonna dei numeri larga quanto serve. Telefono: stima (2 cifre ≈ 21 px, «4000» ≈ 34 px). PC: misura vera, così la prima cifra
    // cade sulla prima lettera del titolo
    const larg = sk => colonnaNumeri(sk.tick.map(v => num(v, sk.passo < 1 ? 1 : 0)), tel);
    L = larg(sc); R = doppio ? larg(sc1) : tel ? 7 : 8;
    pw = W - L - R;
  }
  const X = t => L + (t - d.da) / (d.a - d.da) * pw, Y = v => T + ph - (v - sc.lo) / (sc.hi - sc.lo) * ph;
  const tt = tickTempo(d.da, d.a, W < 480 ? 4 : 7), buco = g.id === 'pot' ? 3600 : Math.max(d.passo * 4, 1000);
  const mezzo = Math.max(d.passo, 120) / 2, colAsse1 = doppio ? COLS[(g.linee.find(l => l.asse === 1) || {}).col] : '';
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" data-l="${L}" data-pw="${pw}" data-id="${g.id}" data-t0="${T}" data-ph="${ph}" data-lo="${sc.lo}" data-hi="${sc.hi}"${doppio ? ` data-lo1="${sc1.lo}" data-hi1="${sc1.hi}"` : ''} data-buco="${buco}" data-mezzo="${g.gradini ? mezzo : 0}">`;
  // periodi senza dati: dove le linee di questo grafico si interrompono E la pompa risulta senza dati (così i grafici che hanno dati solo
  // quando il compressore lavora, come il gas, non segnano come «nessun dato» le pause normali). Righe oblique sottili, bordi = buco della linea.
  const ts = [...new Set(g.linee.flatMap(l => l.punti.map(p => p[0])))].sort((a, b) => a - b), mz = g.gradini ? mezzo : 0, buchi = [];
  if (!ts.length || ts[0] - d.da > buco) buchi.push([d.da, ts.length ? ts[0] - mz : d.a]);
  for (let k = 1; k < ts.length; k++) if (ts[k] - ts[k - 1] > buco) buchi.push([ts[k - 1] + mz, ts[k] - mz]);
  if (ts.length && d.a - ts[ts.length - 1] > buco) buchi.push([ts[ts.length - 1] + mz, d.a]);
  const senzaDati = ((d.modi || {}).segmenti || []).filter(m => m[2] === 'nodati'), vuoti = [];
  buchi.forEach(([a, b]) => senzaDati.forEach(([s0, s1]) => {
    const lo = Math.max(a, s0 - buco), hi = Math.min(b, s1 + buco);
    if (hi > lo && Math.min(b, s1) > Math.max(a, s0)) vuoti.push([lo, hi]);
  }));
  (S.vuoti = S.vuoti || {})[g.id] = vuoti;
  s += `<defs><pattern id="tr-${g.id}" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="8" stroke="#8793a8" stroke-opacity=".22" stroke-width="1"/></pattern></defs>`;
  s += vuoti.map(([s0, s1]) => `<rect x="${X(Math.max(s0, d.da)).toFixed(1)}" y="${T}" width="${Math.max(1, X(Math.min(s1, d.a)) - X(Math.max(s0, d.da))).toFixed(1)}" height="${ph}" fill="rgba(135,147,168,.05)"/><rect x="${X(Math.max(s0, d.da)).toFixed(1)}" y="${T}" width="${Math.max(1, X(Math.min(s1, d.a)) - X(Math.max(s0, d.da))).toFixed(1)}" height="${ph}" fill="url(#tr-${g.id})"/>` + [s0, s1].filter(u => u > d.da + 1 && u < d.a - 1).map(u => `<line x1="${X(u).toFixed(1)}" x2="${X(u).toFixed(1)}" y1="${T}" y2="${T + ph}" class="bordo-vuoto"/>`).join('')).join('');
  s += g.id === 'casa' ? fasceRichiesta(d.richiesta || [], d.da, d.a, X, T, ph) : fasce(d.modi.segmenti, d.da, d.a, X, T, ph);
  if (g.id === 'casa') s += fasceFinestre(d.finestre || [], d.da, d.a, X, T, ph);
  if (g.id === 'casa') s += fasceRichiesta(d.richiesta || [], d.da, d.a, X, T + ph - 7, 7, .9);   // striscia piena in basso: quando lo Smarther chiede caldo
  s += sc.tick.map(v => `<line x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" class="gl"/><text x="${L - 4}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${num(v, sc.passo < 1 ? 1 : 0)}</text>`).join('');
  if (doppio) s += sc1.tick.map(v => `<text x="${W - R + 4}" y="${(Y1(v) + 4).toFixed(1)}" text-anchor="start" fill="${colAsse1}">${num(v, sc1.passo < 1 ? 1 : 0)}</text>`).join('');
  s += tt.tick.map(t => `<line x1="${X(t).toFixed(1)}" x2="${X(t).toFixed(1)}" y1="${T}" y2="${T + ph}" class="gv"/><text x="${X(t).toFixed(1)}" y="${H - 6}" text-anchor="middle">${etTempo(t, tt.step)}</text>`).join('');
  soglie.forEach((v, i) => { if (v >= sc.lo && v <= sc.hi) s += `<line x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" stroke="${i ? 'var(--bad)' : 'var(--warn)'}" stroke-width="1.2" stroke-dasharray="2 4" opacity=".8"/>`; });
  g.linee.forEach(l => {
    const dash = l.tratti ? ' stroke-dasharray="5 4" opacity=".75"' : '', Yl = l.asse === 1 ? Y1 : Y;
    if (g.gradini) {
      if (l.area) s += `<path d="${percorsoGradini(l.punti, X, Yl, buco, mezzo, true, Yl(0).toFixed(1))}" fill="${COLS[l.col]}" opacity=".22" stroke="none"/>`;
      s += `<path d="${percorsoGradini(l.punti, X, Yl, buco, mezzo, false)}" fill="none" stroke="${COLS[l.col]}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"${dash}/>`;
      return;
    }
    s += `<path d="${percorso(l.punti, X, Yl, buco)}" fill="none" stroke="${COLS[l.col]}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"${dash}/>`;
    if (l.punti.length === 1) s += `<circle cx="${X(l.punti[0][0]).toFixed(1)}" cy="${Yl(l.punti[0][1]).toFixed(1)}" r="3" fill="${COLS[l.col]}"/>`;
  });
  s += g.linee.map((l, i) => `<circle class="cur-d" data-i="${i}" data-a="${l.asse || 0}" r="4.5" cx="0" cy="0" fill="${COLS[l.col]}" stroke="var(--card)" stroke-width="2" visibility="hidden"/>`).join('');
  s += `<line class="cur" x1="0" x2="0" y1="${T}" y2="${T + ph}" visibility="hidden"/>
    <g class="cur-t" visibility="hidden"><rect class="cur-bg" rx="6" height="19"/><text class="cur-tx"></text></g>
    <rect class="cattura" x="${L}" y="${T}" width="${pw}" height="${ph}" fill="transparent"/>${g.id === 'casa' ? iconeFinestre(d.finestre || [], d.da, d.a, X, L + 13, W - R - 13, T + 14) : ''}</svg>`;
  return s;
}

function valoreA(punti, t, tol) {
  let lo = 0, hi = punti.length - 1;
  if (hi < 0) return null;
  while (lo < hi) { const m = (lo + hi) >> 1; if (punti[m][0] < t) lo = m + 1; else hi = m; }
  let best = punti[lo]; if (lo > 0 && Math.abs(punti[lo - 1][0] - t) < Math.abs(best[0] - t)) best = punti[lo - 1];
  return Math.abs(best[0] - t) <= tol ? best[1] : null;
}
// legenda della richiesta di calore: lo Smarther chiede caldo si/no (all'ora scelta, o adesso)
function richiestaLegenda(r, t) {
  if (!r.length) return '';
  let v = null;
  if (t == null) v = r[r.length - 1][1];
  else for (let i = r.length - 1; i >= 0; i--) if (r[i][0] <= t) { v = t - r[i][0] <= 1200 ? r[i][1] : null; break; }
  return `<span><i style="background:${COLS.casa}"></i>Richiesta caldo <b>${v == null ? '—' : v >= 0.5 ? 'sì' : 'no'}</b></span>`;
}
function legenda(g, t) {
  const el = document.querySelector(`[data-l="${g.id}"].legg`); if (!el) return;
  const d = S.dati, tol = Math.max(d.passo * 3, 900);
  el.innerHTML = g.linee.map(l => {
    const v = !l.punti.length ? null : t == null ? l.punti[l.punti.length - 1][1] : valoreA(l.punti, t, tol);
    const u = l.u != null ? l.u : g.unita;
    return `<span><i style="background:${COLS[l.col]};${l.tratti ? 'opacity:.6' : ''}"></i>${l.nome} <b>${v == null ? '—' : num(v, u === 'W' || u === 'Hz' || u === '%' ? 0 : 1)}${u ? ' ' + u : ''}</b></span>`;
  }).join('') + (g.id === 'casa' ? richiestaLegenda(d.richiesta || [], t) : '');
}
// valore sulla linea al tempo t (interpolato fra i due punti vicini; null se lì c'e' un buco)
function valoreInterp(punti, t, buco) {
  let lo = 0, hi = punti.length - 1;
  if (hi < 0 || t < punti[0][0] - buco / 2 || t > punti[hi][0] + buco / 2) return null;
  while (lo < hi) { const m = (lo + hi) >> 1; if (punti[m][0] < t) lo = m + 1; else hi = m; }
  const b = punti[lo], a = lo > 0 ? punti[lo - 1] : null;
  if (!a || b[0] === t) return Math.abs(b[0] - t) <= buco / 2 ? b[1] : null;
  if (b[0] - a[0] > buco) return Math.abs(t - a[0]) <= Math.abs(b[0] - t) && t - a[0] <= buco / 2 ? a[1] : (b[0] - t <= buco / 2 ? b[1] : null);
  return a[1] + (b[1] - a[1]) * (t - a[0]) / (b[0] - a[0]);
}
// valore di una linea a gradini al tempo t: quello del punto piu' vicino, se non c'e' un buco
function valoreGradino(punti, t, buco, mezzo) {
  let lo = 0, hi = punti.length - 1;
  if (hi < 0) return null;
  while (lo < hi) { const m = (lo + hi) >> 1; if (punti[m][0] < t) lo = m + 1; else hi = m; }
  let i = lo; if (lo > 0 && Math.abs(punti[lo - 1][0] - t) < Math.abs(punti[lo][0] - t)) i = lo - 1;
  const dist = Math.abs(punti[i][0] - t);
  if (dist <= mezzo) return punti[i][1];
  const vic = punti[i + (t > punti[i][0] ? 1 : -1)];
  return vic && Math.abs(vic[0] - punti[i][0]) <= buco ? punti[i][1] : null;
}
function cursore(t) {
  const d = S.dati; if (!d || S.dpag !== 'analisi') return;
  document.querySelectorAll('#grafici svg').forEach(svg => {
    const L = +svg.dataset.l, pw = +svg.dataset.pw, x = L + (t - d.da) / (d.a - d.da) * pw;
    const c = svg.querySelector('.cur'), tx = svg.querySelector('.cur-t');
    c.setAttribute('x1', x); c.setAttribute('x2', x); c.setAttribute('visibility', 'visible');
    const g = (S.grafici || []).find(x => x.id === svg.dataset.id);
    if (g) {
      const T0 = +svg.dataset.t0, ph = +svg.dataset.ph, buco = +svg.dataset.buco, mezzo = +svg.dataset.mezzo;
      svg.querySelectorAll('.cur-d').forEach(c => {
        const pt = g.linee[+c.dataset.i].punti, v = mezzo ? valoreGradino(pt, t, buco, mezzo) : valoreInterp(pt, t, buco);
        if (v == null) { c.setAttribute('visibility', 'hidden'); return; }
        const dx = c.dataset.a === '1', lo = +svg.dataset[dx ? 'lo1' : 'lo'], hi = +svg.dataset[dx ? 'hi1' : 'hi'];
        c.setAttribute('cx', x.toFixed(1)); c.setAttribute('cy', (T0 + ph - (v - lo) / (hi - lo) * ph).toFixed(1)); c.setAttribute('visibility', 'visible');
      });
    }
    const vuoto = ((S.vuoti || {})[svg.dataset.id] || []).find(v => t >= v[0] && t < v[1]);     // sulle righe oblique: «nessun dato» + durata del buco
    const T0 = +svg.dataset.t0, txt = tx.querySelector('.cur-tx'), bg = tx.querySelector('.cur-bg'), NS = 'http://www.w3.org/2000/svg';
    txt.textContent = '';
    [[dm(t), ''], ...(vuoto ? [[' · nessun dato' + (vuoto[1] - vuoto[0] >= 120 ? ' · ' + durata(Math.round((vuoto[1] - vuoto[0]) / 60)) : ''), 'nd']] : [])].forEach(([tt, cl]) => {
      const ts = document.createElementNS(NS, 'tspan'); if (cl) ts.setAttribute('class', cl); ts.textContent = tt; txt.appendChild(ts);
    });
    tx.setAttribute('visibility', 'visible');
    const w = txt.getComputedTextLength() + 16, bx = Math.max(L, Math.min(L + pw - w, x - w / 2));
    bg.setAttribute('x', bx.toFixed(1)); bg.setAttribute('y', T0 + 3); bg.setAttribute('width', w.toFixed(1));
    txt.setAttribute('x', (bx + 8).toFixed(1)); txt.setAttribute('y', T0 + 16);
    c.setAttribute('y1', T0 + 22);
  });
  (S.grafici || []).forEach(g => legenda(g, t));
}
// Cursore dei grafici di Efficienza: come in Analisi (linea, puntini sulle linee, etichetta in alto, valori nella legenda)
const EF = {};
const curEf = (T, ph, cols = []) => cols.map(c => `<circle class="cur-d" r="4.5" fill="${c}" stroke="var(--card)" stroke-width="2" visibility="hidden"/>`).join('') +
  `<line class="cur" x1="0" x2="0" y1="${T}" y2="${T + ph}" visibility="hidden"/><g class="cur-t" visibility="hidden"><rect class="cur-bg" rx="6" height="19"/><text class="cur-tx"></text></g>`;
const vEf = (v, u, n = 1) => ` <b>${v == null ? '—' : num(v, n) + (u ? ' ' + u : '')}</b>`;
const vuotoEf = (u, n = 1) => !tipici(EF.d) ? '' : ` <b style="visibility:hidden">${n > 1 ? '0,00' : '00,0'}${u ? ' ' + u : ''}</b>`;   // segnaposto (solo con Confronta, dove la legenda e' lunga): a riposo occupa lo stesso spazio di quella coi valori, cosi' non va a capo solo al cursore
function legEf(k, i) {
  const d = EF.d, on = i != null, sp = (st, nome, v, ph) => `<span><i ${st}></i>${nome}${on ? v : ph}</span>`;
  if (k === 'en') {
    const g = on ? d.giorni[i] : {}, tp = tipici(d), t = on && tp ? tp[g.giorno] : null, u = x => tp ? '' : x;   // l'unita' solo sull'ultimo valore: la legenda sta su una riga anche su iPhone
    return sp('style="background:var(--casa)"', 'Risc.', vEf(g.el_casa, ''), vuotoEf('')) + sp('style="background:var(--acs)"', 'Acqua', vEf(g.el_acs, ''), vuotoEf('')) +
      sp('style="background:#3a4558"', 'Standby', vEf(g.el_riposo, u('kWh')), vuotoEf(u('kWh'))) +
      (tp ? sp('class="tp"', 'Tipico', vEf(t && t.el, 'kWh'), vuotoEf('kWh')) + '<span><i class="an"></i>Sopra il normale</span>' : '');
  }
  if (k === 'cop') {
    const g = on ? d.giorni[i] : {}, t = on && tipici(d) ? tipici(d)[g.giorno] : null;
    return sp('style="background:var(--casa)"', 'Risc.', vEf(g.cop_casa, '', 2), vuotoEf('', 2)) + sp('style="background:var(--acs)"', 'Acqua', vEf(g.cop_acs, '', 2), vuotoEf('', 2)) +
      (tipici(d) ? sp('class="tp2"', 'Tipico', vEf(t && (t.cop_casa != null ? t.cop_casa : t.cop_acs), '', 2), vuotoEf('', 2)) : '');
  }
  const b = on ? EF.bin.bins[i] : null, f = m => b == null ? null : ((d.bins[m].find(x => x.da === b) || {}).cop);
  return sp('style="background:var(--casa)"', 'Risc.', vEf(f('casa'), '', 2), vuotoEf('', 2)) + sp('style="background:var(--acs)"', 'Acqua', vEf(f('acs'), '', 2), vuotoEf('', 2));
}
function cursoreEf(svg, e) {
  const k = svg.dataset.ef, c = EF[k]; if (!c || !c.xs.length) return;
  const r = svg.getBoundingClientRect(), x0 = (e.clientX - r.left) * (+svg.getAttribute('width') / r.width);
  let i = 0; c.xs.forEach((x, j) => { if (Math.abs(x - x0) < Math.abs(c.xs[i] - x0)) i = j; });
  const x = c.xs[i], ln = svg.querySelector('.cur'), tx = svg.querySelector('.cur-t'), txt = tx.querySelector('.cur-tx'), bg = tx.querySelector('.cur-bg');
  ln.setAttribute('x1', x); ln.setAttribute('x2', x); ln.setAttribute('y1', c.T + 22); ln.setAttribute('visibility', 'visible');
  const ys = c.dots ? c.dots(i) : [];
  svg.querySelectorAll('.cur-d').forEach((p, j) => {
    if (ys[j] == null) { p.setAttribute('visibility', 'hidden'); return; }
    p.setAttribute('cx', x.toFixed(1)); p.setAttribute('cy', ys[j].toFixed(1)); p.setAttribute('visibility', 'visible');
  });
  svg.querySelectorAll('.vt[data-i]').forEach(t => t.setAttribute('visibility', +t.dataset.i === i ? 'hidden' : 'visible'));   // il numero sopra la barra sparirebbe sotto l'etichetta
  const et = c.seg ? c.seg(i, (e.clientY - r.top) * (+svg.getAttribute('height') / r.height)) : c.lab[i];
  if (et == null) tx.setAttribute('visibility', 'hidden');
  else { txt.textContent = et; tx.setAttribute('visibility', 'visible'); }
  ln.setAttribute('y1', et == null ? c.T : c.T + 22);
  if (et == null) { const lg0 = document.getElementById('lg-' + k); if (lg0) lg0.innerHTML = legEf(k, i); return; }
  const w = txt.getComputedTextLength() + 16, bx = Math.max(c.L, Math.min(c.L + c.pw - w, x - w / 2));
  bg.setAttribute('x', bx.toFixed(1)); bg.setAttribute('y', c.T + 3); bg.setAttribute('width', w.toFixed(1));
  txt.setAttribute('x', (bx + 8).toFixed(1)); txt.setAttribute('y', c.T + 16);
  const lg = document.getElementById('lg-' + k); if (lg) lg.innerHTML = legEf(k, i);
}
function togliCursoreEf() {
  document.querySelectorAll('#eff .cur, #eff .cur-t, #eff .cur-d').forEach(e => e.setAttribute('visibility', 'hidden'));
  document.querySelectorAll('#eff .vt[data-i]').forEach(t => t.setAttribute('visibility', 'visible'));
  if (EF.d) ['en', 'cop', 'bin'].forEach(k => { const lg = document.getElementById('lg-' + k); if (lg) lg.innerHTML = legEf(k, null); });
}
function togliCursore() {
  togliCursoreEf();
  document.querySelectorAll('#grafici .cur, #grafici .cur-t, #grafici .cur-d').forEach(e => e.setAttribute('visibility', 'hidden'));
  (S.grafici || []).forEach(g => legenda(g, null));
}
function muoviCursore(e) {
  const ef = e.target.closest && e.target.closest('#eff svg[data-ef]'); if (ef) { cursoreEf(ef, e); return true; }
  const svg = e.target.closest && e.target.closest('#grafici svg'); const d = S.dati;
  if (!svg || !d || S.dpag !== 'analisi') return false;
  const r = svg.getBoundingClientRect(), L = +svg.dataset.l, pw = +svg.dataset.pw;
  const f = Math.max(0, Math.min(1, (e.clientX - r.left - L) / pw));
  cursore(d.da + f * (d.a - d.da)); return true;
}


// ---------- spostarsi indietro nel tempo col mouse (6h / 24h): si trascina un grafico, al rilascio si caricano le ore di quel periodo ----------
let sposta = null;
function spostaGiu(e) {
  if (e.pointerType !== 'mouse' || e.button !== 0 || S.pagina !== 'analisi' || !S.dati || S.range === 'custom' || S.zoom) return;
  const el = e.target.closest && e.target.closest('#grafici, #tlw'); if (!el) return;
  const svg = document.querySelector('#grafici svg'), pw = el.id === 'tlw' || !svg ? el.getBoundingClientRect().width : +svg.dataset.pw;
  sposta = {x0: e.clientX, dx: 0, on: false, sec: (S.dati.a - S.dati.da) / Math.max(1, pw)};
}
function spostaMuovi(e) {
  if (!sposta) return false;
  if (!(e.buttons & 1)) { spostaSu(); return false; }   // tasto rilasciato fuori dalla finestra: si chiude qui
  const dx = e.clientX - sposta.x0;
  if (!sposta.on && Math.abs(dx) < 6) return false;
  if (!sposta.on) { sposta.on = true; togliCursore(); const t = $('#tip'); if (t) t.hidden = true; document.body.style.userSelect = 'none'; }
  sposta.dx = dx; document.querySelectorAll('#grafici svg, #tlw').forEach(x => { x.style.transform = `translateX(${dx}px)`; x.style.opacity = '.55'; });
  return true;
}
function spostaSu() {
  if (!sposta) return;
  const s = sposta; sposta = null;
  document.querySelectorAll('#grafici svg, #tlw').forEach(x => { x.style.transform = x.style.opacity = ''; }); document.body.style.userSelect = '';
  if (!s.on) return;
  // trascinare verso destra = andare indietro nel tempo
  const adesso = ora(), fine = Math.min(adesso, Math.round((S.fine || adesso) - s.dx * s.sec));
  S.fine = fine > adesso - 60 ? null : fine; S.zoom = null;
  chips(); carica();
}

// ---------- zoom con due dita sui grafici di Analisi (telefono); doppio tocco = vista intera ----------
const dita = new Map();
let pizzico = null, tocco = null, mossoPizzico = false, trascina = null;
const unita = x => Math.max(0, Math.min(1, x));
function fraz(el, x) {
  const svg = document.querySelector('#grafici svg');
  if (el.id === 'tlw' || !svg) { const r = el.getBoundingClientRect(); return (x - r.left) / r.width; }
  const r = svg.getBoundingClientRect(), L = +svg.dataset.l, pw = +svg.dataset.pw;
  return (x - r.left - L) / pw;
}
function zoomaGiu(e) {
  if (e.pointerType !== 'touch' || S.pagina !== 'analisi' || !S.dati) return;
  const el = e.target.closest && e.target.closest('#grafici, #tlw'); if (!el) return;
  if (!dita.size) { mossoPizzico = false; trascina = null; tocco = tocco && Date.now() - tocco.t < 320 ? tocco : null; }
  dita.set(e.pointerId, {x: e.clientX, y: e.clientY, x0: e.clientX, y0: e.clientY, el});
  if (dita.size === 2 && !pizzico) {
    const [a, b] = [...dita.values()], v = S.dati;
    pizzico = {el, dist0: Math.max(30, Math.abs(a.x - b.x)), span0: v.a - v.da, tc: v.da + unita(fraz(el, (a.x + b.x) / 2)) * (v.a - v.da)};
    try { dita.forEach((_, id) => el.setPointerCapture(id)); } catch {}
    togliCursore();
  }
}
function zoomaMuovi(e) {
  const p = dita.get(e.pointerId); if (!p) return false;
  p.x = e.clientX; p.y = e.clientY;
  if (!pizzico && dita.size === 1 && S.zoom && !mossoPizzico) {   // con lo zoom attivo, un dito che scorre in orizzontale sposta la finestra di tempo
    if (!trascina) {
      if (Math.abs(p.x - p.x0) < 8 || Math.abs(p.x - p.x0) < Math.abs(p.y - p.y0)) return false;
      trascina = {da: S.zoom.da, span: S.zoom.a - S.zoom.da, x0: p.x0};
      try { p.el.setPointerCapture(e.pointerId); } catch {}
      togliCursore(); tocco = null;
    }
    const o = S.dati.pieno || S.dati;
    const da = Math.min(o.a - trascina.span, Math.max(o.da, trascina.da + (fraz(p.el, trascina.x0) - fraz(p.el, p.x)) * trascina.span));
    S.zoom = {da: Math.round(da), a: Math.round(da + trascina.span)};
    if (!zoomaMuovi.rid) zoomaMuovi.rid = requestAnimationFrame(() => { zoomaMuovi.rid = 0; disegna(); });
    return true;
  }
  if (!pizzico || dita.size < 2) return false;
  const [a, b] = [...dita.values()], o = S.dati.pieno || S.dati, pieno = o.a - o.da;
  const minimo = Math.max(900, (o.passo || 60) * 8), dist = Math.max(30, Math.abs(a.x - b.x));
  const span = Math.min(pieno, Math.max(minimo, pizzico.span0 * pizzico.dist0 / dist));
  const da = Math.min(o.a - span, Math.max(o.da, pizzico.tc - unita(fraz(pizzico.el, (a.x + b.x) / 2)) * span));
  S.zoom = span >= pieno * 0.98 ? null : {da: Math.round(da), a: Math.round(da + span)};
  mossoPizzico = true;
  if (!zoomaMuovi.rid) zoomaMuovi.rid = requestAnimationFrame(() => { zoomaMuovi.rid = 0; disegna(); });
  return true;
}
function zoomaSu(e) {
  const p = dita.get(e.pointerId); if (!p) return;
  dita.delete(e.pointerId);
  if (dita.size < 2) pizzico = null;
  if (dita.size) return;
  trascina = null;
  const fermo = Math.hypot(e.clientX - p.x0, e.clientY - p.y0) < 12;
  if (mossoPizzico || !fermo || e.type === 'pointercancel') { tocco = null; return; }
  const ora_ = Date.now();
  if (tocco && ora_ - tocco.t < 320 && Math.hypot(e.clientX - tocco.x, e.clientY - tocco.y) < 40) {
    tocco = null;
    if (S.zoom) { S.zoom = null; disegna(); togliCursore(); }
  } else tocco = {t: ora_, x: e.clientX, y: e.clientY};
}

// ---------- Impostazioni > Registro ----------
const TAB_IMP = [['', 'Stato'], ['collegamenti', 'Collegamenti'], ['avvisi', 'Avvisi'], ['integrazioni', 'Integrazioni'], ['accesso', 'Accesso'], ['dati', 'Dati'], ['registro', 'Registro']];
function vistaImp() {
  const tab = $('#imp-tabs'); if (!tab) return;
  const cur = TAB_IMP.some(([k]) => k === S.sub) ? S.sub : '';
  tab.innerHTML = `<nav class="sub-tabs" aria-label="Sezioni">${TAB_IMP.map(([k, t]) => `<a href="#/impostazioni${k ? '/' + k : ''}" class="${k === cur ? 'on' : ''}">${t}</a>`).join('')}</nav>`;
  const reg = cur === 'registro';
  const att = tab.querySelector('a.on'); if (att && att.scrollIntoView) att.scrollIntoView({inline: 'center', block: 'nearest'});
  $('#imp').hidden = reg; $('#reg').hidden = !reg;
  document.querySelectorAll('#imp .imp-tab').forEach(g => { g.hidden = g.dataset.t !== (cur || 'stato'); });
  if (reg) caricaRegistro();
}
S.solo = S.solo || false;
async function caricaRegistro() {
  const box = $('#reg'); if (!box) return;
  try {
    const r = await leggiJson('/api/registro?righe=150'); if (!r) return;
    S.reg = await r.json(); disegnaRegistro();
  } catch { box.innerHTML = '<article class="card"><div class="vuoto">Non riesco a leggere il registro: il pannello non risponde.</div></article>'; }
}
const pesoTxt = b => b >= 1e6 ? num(b / 1e6, 1) + ' MB' : num(b / 1e3, 0) + ' KB';
function disegnaRegistro() {
  const box = $('#reg'), d = S.reg; if (!box || !d) return;
  const righe = d.righe.filter(r => !S.solo || r.livello === 'WARNING' || r.livello === 'ERROR' || r.livello === 'CRITICAL');
  const bk = !d.backup_attivo ? 'spento' : d.backup_ts ? `${esc(dataLunga(d.backup_ts))} · ${d.backup_copie} copie` : 'ancora nessuno';
  const sc = box.querySelector('.reg-log'), inFondo = !sc || sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 24;
  box.innerHTML = `<article class="card"><div class="ch"><h2>Registro del pannello</h2>
      <div class="azioni-t"><button type="button" class="sec" id="reg-agg">Aggiorna</button><button type="button" class="sec" id="reg-copia">Copia</button></div></div>
    <div class="righe">
      ${riga('Versione', esc(d.versione) + ` <small class="mut">· acceso da ${daQuanto(d.avviato)}</small>`)}
      ${riga('Database', pesoTxt(d.db_byte))}
      ${riga('File del registro', pesoTxt(d.log_byte))}
      ${riga('Ultimo backup', bk)}
    </div>
    <label class="reg-filtro"><input type="checkbox" id="reg-solo" ${S.solo ? 'checked' : ''}> Solo avvisi ed errori</label>
    <div class="reg-log" tabindex="0">${righe.length ? righe.map(r => `<div class="rl ${esc(r.livello)}">${esc(r.testo)}</div>`).join('') : '<div class="vuoto">Nessuna riga da mostrare.</div>'}</div>
    <p class="nota f" style="margin:8px 0 0">Ultime ${d.righe.length} righe di <code>pdc_pannello.log</code>. I token e le password vengono nascosti, ma controlla comunque prima di incollare il testo in giro.</p></article>`;
  const n = box.querySelector('.reg-log'); if (n && inFondo) n.scrollTop = n.scrollHeight;
}
document.addEventListener('click', async e => {
  const t = e.target; if (!t.closest) return;
  if (t.closest('#reg-agg')) { caricaRegistro(); return; }
  if (t.closest('#reg-copia')) {
    const d = S.reg; if (!d) return;
    const ok = await copiaTesto(d.righe.filter(r => !S.solo || ['WARNING', 'ERROR', 'CRITICAL'].includes(r.livello)).map(r => r.testo).join('\n'));
    toast(ok ? 'Registro copiato' : 'Non riesco a copiare', !ok);
  }
});
document.addEventListener('change', e => { if (e.target && e.target.id === 'reg-solo') { S.solo = e.target.checked; disegnaRegistro(); } });
setInterval(() => { if (S.pagina === 'impostazioni' && S.sub === 'registro' && !document.hidden) caricaRegistro(); }, 15000);

// ---------- Meteo (riga nella scheda Esterna) ----------
// Icone a tratto, 32x32, nello stesso stile del resto dell'app. Ogni colore e' una variabile CSS (vedi .mt-*).
const NUV = 'd="M10 24a5 5 0 0 1-.5-9.97A7 7 0 0 1 23 13.2 5.4 5.4 0 0 1 22.4 24z"/>';   // il contorno della nuvola, uguale in tutte le icone
const NUV_PIOGGIA = '<path class="mt-n" transform="translate(5 1) scale(.8)" ' + NUV + '<path class="mt-p" d="M14.4 24.4l-1.2 2.6M19 24.4l-1.2 2.6M23.6 24.4l-1.2 2.6"/>';
const MT = {
  nuvola: '<path class="mt-n" ' + NUV,
  nuvolaP: '<path class="mt-n" transform="translate(6.5 7.5) scale(.72)" ' + NUV,
  nuvolaAlta: '<path class="mt-n" transform="translate(0 -3)" ' + NUV,
  sole: (cx, cy, r, ray) => `<circle class="mt-s" cx="${cx}" cy="${cy}" r="${r}"/>` + [0, 45, 90, 135, 180, 225, 270, 315].map(a => {
    const t = a * Math.PI / 180, c = Math.cos(t), n = Math.sin(t);
    return `<path class="mt-s" d="M${(cx + c * (r + 2.6)).toFixed(1)} ${(cy + n * (r + 2.6)).toFixed(1)}L${(cx + c * (r + 2.6 + ray)).toFixed(1)} ${(cy + n * (r + 2.6 + ray)).toFixed(1)}"/>`;
  }).join(''),
  luna: (tr) => `<path class="mt-l" ${tr || ''} d="M23.5 19.5A9 9 0 0 1 12.5 8.5 9 9 0 1 0 23.5 19.5z"/>`,
  gocce: '<path class="mt-p" d="M12 26.2l-1.4 3M17.4 26.2L16 29.2M22.8 26.2l-1.4 3"/>',
  fulmine: '<path class="mt-f" d="M17.6 21.2l-3.4 5h4.4l-2.4 4.6"/>',
};
const ICONE_METEO = {
  sole: MT.sole(16, 16, 5.6, 3.2),
  luna: MT.luna('transform="translate(-.5 .5)"'),
  'sole-nuvola': MT.sole(10.5, 10.5, 3.6, 2.2) + MT.nuvolaP,
  'luna-nuvola': MT.luna('transform="translate(-2.6 -2.5) scale(.74)"') + MT.nuvolaP,
  nuvola: MT.nuvola,
  pioggia: MT.nuvolaAlta + MT.gocce,
  'sole-pioggia': MT.sole(10, 9.5, 3.2, 2) + NUV_PIOGGIA,
  'luna-pioggia': MT.luna('transform="translate(-2 -3) scale(.68)"') + NUV_PIOGGIA,
  temporale: MT.nuvolaAlta + MT.fulmine,
  neve: MT.nuvolaAlta + '<path class="mt-v" d="M12 26.5v.01M16.5 28.5v.01M21 26.5v.01"/>',
  nebbia: '<path class="mt-n" d="M7 11h14M5 16h20M9 21h16M7 26h10"/>',
};
const iconaMeteo = (nome, px) => `<svg class="mt-i" viewBox="0 0 32 32" width="${px}" height="${px}" fill="none" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONE_METEO[nome] || ICONE_METEO.nuvola}</svg>`;

// Versione corta della frase per la scheda (nel pannello c'e' quella completa): niente «pioggia» (c'e' l'icona) e niente «possibile»
// (lo dice il colore: arancio = possibile, azzurro = certa)
function meteoBreve(m) {
  if (m.livello !== 'certa' && m.livello !== 'possibile') return '';
  return (m.testo || '').replace(/^(possibile )?pioggia\s*/, '').replace('per il resto della giornata', 'tutto il giorno').replace(/^(\d+)–(\d+)$/, 'dalle $1 alle $2');
}
function meteoRiga(m) {
  const el = document.getElementById('e-mt');
  if (!el) return;
  if (!m) { el.hidden = true; chiudiMeteo(); return; }
  const gradi = v => v == null ? '—' : Math.round(v) + '°';
  el.hidden = false;
  const breve = meteoBreve(m).replace(/(\d)–(\d)/g, '$1⁠–⁠$2');
  el.title = m.testo ? m.testo.charAt(0).toUpperCase() + m.testo.slice(1) : '';
  html('e-mt', iconaMeteo(m.icona, 26) + `<b class="mt-g">${gradi(m.tmin)}–${gradi(m.tmax)}</b>` +
    (breve ? `<span class="mt-pi ${m.livello}">${esc(breve)}</span>` : ''));
  if (S.meteoAperto) disegnaPannelloMeteo(m);
}
function pannelloMeteoHtml(m) {
  const gradi = v => v == null ? '—' : Math.round(v) + '°';
  const righe = (m.ore || []).map(o => {
    const mm = o.mm > 0 ? (o.mm < 0.1 ? '<0,1' : o.mm.toFixed(1).replace('.', ',')) + ' mm' : '';
    const pioggia = o.p === 'certa' ? (mm || 'pioggia') : o.p === 'possibile' ? 'possibile' + (mm ? ' · ' + mm : '') : 'asciutto';
    return `<div class="mt-r ${o.p || 'nessuna'}">${iconaMeteo(o.i || 'nuvola', 20)}<span class="mt-h">${String(o.h).padStart(2, '0')}:00</span><b>${gradi(o.t)}</b><span class="mt-mm">${pioggia}</span></div>`;
  }).join('');
  const NOMI = {italia_meteo_arpae_icon_2i: 'ICON-2I', ecmwf_ifs025: 'ECMWF'};
  const mod = (m.modelli_nomi || []).map(n => NOMI[n] || n).join(' + ');
  const agg = m.agg ? new Date(m.agg * 1000).toLocaleTimeString('it-IT', {hour: '2-digit', minute: '2-digit'}) : '';
  return `<div class="eti">${esc(m.giorno)} · ogni 3 ore</div>${righe || '<div class="mt-r"><span class="mt-h">Nessun dettaglio orario</span></div>'}` +
    `<div class="mt-fo"><span>${esc(mod)}${agg ? ' · agg. ' + agg : ''}</span><span>Dati: <a href="https://open-meteo.com/" target="_blank" rel="noopener">Open-Meteo</a> (CC BY 4.0)</span></div>`;
}
// Pannello sospeso (fuori dalla scheda, cosi' non la allunga): si chiude toccando fuori, con Esc o scorrendo la pagina.
function disegnaPannelloMeteo(m) {
  let p = document.getElementById('mt-pan');
  if (!p) { p = document.createElement('div'); p.id = 'mt-pan'; p.className = 'mt-pan'; document.body.appendChild(p); }
  p.innerHTML = pannelloMeteoHtml(m);
  const ref = document.getElementById('e-mt'); if (!ref) return;
  const r = ref.closest('article').getBoundingClientRect(), w = Math.min(290, window.innerWidth - 20);
  p.style.width = w + 'px';
  p.style.left = Math.max(10, Math.min(window.innerWidth - w - 10, r.left + r.width / 2 - w / 2)) + 'px';
  const h = p.offsetHeight, sotto = r.bottom + 8;
  p.style.top = (sotto + h > window.innerHeight - 90 && r.top - h - 8 > 10 ? r.top - h - 8 : Math.max(10, sotto)) + 'px';
}
function chiudiMeteo() { S.meteoAperto = false; const p = document.getElementById('mt-pan'); if (p) p.remove(); }
document.addEventListener('click', ev => {
  if (ev.target.closest && ev.target.closest('[data-meteo]')) {
    if (S.meteoAperto) return chiudiMeteo();
    if (S.dati && S.dati.meteo) { S.meteoAperto = true; disegnaPannelloMeteo(S.dati.meteo); }
  } else if (S.meteoAperto && !(ev.target.closest && ev.target.closest('#mt-pan'))) chiudiMeteo();
});
document.addEventListener('keydown', ev => { if (ev.key === 'Escape') chiudiMeteo(); });
window.addEventListener('scroll', () => { if (S.meteoAperto) chiudiMeteo(); }, {passive: true});

// finestre aperte: tocco sull'icona -> pannellino per confermare; «＋ Finestra» per segnarne una a mano
function finRidisegna() { const b = $('#grafici'); if (b) b.dataset.f = ''; carica(); }
async function finInvia(url, corpo) {
  const r = await inviaPost(url, corpo);
  if (!r.ok) { toast(r.errore || 'Non sono riuscito a salvare.', true); return false; }
  return true;
}
function finPannello(ic) {
  const f = ((S.dati || {}).finestre || [])[+ic.dataset.fin]; if (!f) return;
  const inf = (S.dati || {}).finestre_info || {}, ora2 = t => new Date(t * 1000).toLocaleTimeString('it-IT', {hour: '2-digit', minute: '2-digit'});
  const ok = f.stato === 'si', righe = (e, v) => `<div class="riga"><span>${e}</span><b>${v}</b></div>`;
  const p = document.createElement('div'); p.id = 'fin-pan'; p.className = 'mt-pan';
  p.innerHTML = `<div class="eti">${ok ? 'Finestra aperta' : 'Possibile finestra aperta'}</div>
    ${righe('Orario', `${ora2(f.da)}–${ora2(f.a)}`)}
    ${f.calo != null ? righe('Calo di temperatura', `−${num(f.calo, 1)} °C`) : ''}
    ${f.ah != null ? righe('Umidità assoluta', `${f.ah > 0 ? '+' : '−'}${num(Math.abs(f.ah), 1)} g/m³`) : ''}
    <div class="sub">${ok ? (f.manuale ? 'Segnata da te.' : 'Confermata da te.') : 'Era una finestra aperta?'}</div>
    <div class="azioni-t" id="fin-az">${ok ? `<button type="button" class="sec" data-az="annulla">${f.manuale ? 'Elimina' : 'Togli la conferma'}</button>`
      : '<button type="button" class="sec" data-az="si">Sì</button><button type="button" class="sec" data-az="no">No</button>'}</div>
    <div class="mt-fo">${inf.tarato ? `Soglie tarate su ${inf.n} risposte.` : `Risposte date: ${inf.risposte || 0} su 10 per tarare.`}</div>`;
  document.body.appendChild(p);
  const r = ic.getBoundingClientRect(), w = Math.min(280, window.innerWidth - 20); p.style.width = w + 'px';
  p.style.left = Math.max(10, Math.min(window.innerWidth - w - 10, r.left + r.width / 2 - w / 2)) + 'px';
  const h = p.offsetHeight, sotto = window.innerHeight - 70;      // 70: la barra in basso del telefono
  p.style.top = Math.max(10, r.bottom + 8 + h > sotto ? r.top - h - 8 : r.bottom + 8) + 'px';
  p.addEventListener('click', async ev => {
    const az = ev.target.dataset && ev.target.dataset.az; if (!az) return;
    const via = az === 'annulla' ? await finInvia('/api/finestre/annulla', {da: f.da})
      : await finInvia('/api/finestre/giudica', {da: f.da, a: f.a, calo: f.calo, ah: f.ah, stato: az});
    p.remove();
    if (via) { finRidisegna(); toast(az === 'si' ? 'Segnata come finestra aperta.' : az === 'no' ? 'Ok, non era una finestra.' : 'Fatto.'); }
  });
}
document.addEventListener('click', ev => {
  const t = ev.target, vecchio = document.getElementById('fin-pan'), ic = t.closest && t.closest('[data-fin]');
  if (t.id === 'fin-add') {
    const b = $('#fin-man'), a = ora();
    $('#fin-da').value = loc(a - 3600); $('#fin-a').value = loc(a); b.hidden = !b.hidden; if (!b.hidden) b.scrollIntoView({block: 'center', behavior: 'smooth'}); return;
  }
  if (t.id === 'fin-ok') {
    const da = new Date($('#fin-da').value).getTime() / 1000, a = new Date($('#fin-a').value).getTime() / 1000;
    if (!isFinite(da) || !isFinite(a) || a <= da || a - da > 86400) { toast('Controlla le date: "A" deve venire dopo "Da" (al massimo 24 ore).', true); return; }
    finInvia('/api/finestre/manuale', {da: Math.floor(da), a: Math.floor(a)}).then(v => { if (v) { $('#fin-man').hidden = true; finRidisegna(); toast('Finestra segnata.'); } });
    return;
  }
  if (vecchio && !(t.closest && t.closest('#fin-pan')) && !ic) vecchio.remove();
  if (ic) { if (vecchio) vecchio.remove(); finPannello(ic); }
});
document.addEventListener('keydown', ev => { if (ev.key === 'Escape') { const v = document.getElementById('fin-pan'); if (v) v.remove(); } });
window.addEventListener('scroll', () => { const v = document.getElementById('fin-pan'); if (v) v.remove(); }, {passive: true});

// ---------- Changelog ----------
// Una voce per ogni versione, la piu' recente in alto; le sotto-versioni stanno nella versione madre.
// Ogni riga: [tipo, titoletto, frase]  con tipo n = Novità, m = Migliorato, s = Sistemato. Una frase sola.
const CL_TIPI = {n: 'Novità', m: 'Migliorato', s: 'Sistemato'};
const CL_BREVE = [
  ['fin', 'Finestre aperte', 'Analisi: il pannello riconosce da solo i cali da finestra aperta, li segna con una fascia verde e impara dalle tue conferme.'],
  ['reg', 'Regolazioni della pompa', 'Dispositivi → Regolazioni: cambi gli FSV, l’acqua calda e il modo della pompa, solo da casa, con lucchetto e conferma.'],
  ['boost', 'Boost acqua calda', 'Con un tocco porta l’acqua all’obiettivo, poi ripristina le impostazioni di prima.'],
  ['indietro', 'Grafici indietro nel tempo', 'Analisi, da computer: trascina un grafico verso destra per guardare le ore (o il giorno) prima; «Torna ad adesso» rimette la diretta.'],
];
const CL_IC = {euro: '€', refresh: '↻', ora: '◷', tipici: '≈', avviso: '!', sbr: '❄', login: '🔑', plugin: '🧩', reg: '✎', boost: '⚡', indietro: '⇤', fin: '▣'};
const CHANGELOG = [
  {v: '0.25.1', quando: '8 novembre 2026', titolo: 'Avvisi finti e dati esterni', righe: [
    ['s', 'Browser che si stacca', 'Se il browser chiudeva la connessione a metà risposta (ricaricando la pagina, per esempio), il registro mostrava un errore con tutta la traccia anche se non c’era niente da correggere: ora viene ignorato, come già succedeva per gli altri distacchi.'],
    ['s', 'Meteo e stazione sempre aggiornati', 'La previsione e l’umidità della stazione vicina si leggono ora anche con la Dashboard chiusa, da un solo lavoro di fondo: niente buco alla riapertura (nemmeno per Alexa e il widget) e niente avvisi finti di «dato vecchio».'],
    ['s', 'Lampo bianco all’apertura', 'Aprendo dall’icona nella Home non compare più il lampo bianco: subito lo sfondo scuro con il simbolo. Per vederlo togli l’icona dalla Home e rimettila.'],
    ['m', 'Avviso «mandata lontana» più giusto', 'Ora ha due soglie: 12 °C sopra la curva, dove la PdC sta spesso, e 8 °C sotto, che di solito è un problema vero. Le cambi in Impostazioni → Avvisi, nel riquadro «Mandata lontana dalla curva».'],
    ['m', 'MAC dei dispositivi', 'Un MAC trovato si ricorda per una settimana, invece di 10 minuti; se non c’è (dispositivo spento) si riprova dopo 2 minuti.'],
    ['m', 'Apertura più veloce', 'I file restano nel browser senza ricontrolli e la Dashboard arriva già dentro la pagina: due richieste in meno all’apertura, soprattutto da fuori casa.'],
  ]},
  {v: '0.25', quando: '7 novembre 2026', titolo: 'Finestre aperte', righe: [
    ['n', 'Finestre aperte sul grafico', 'In Analisi, nel grafico della temperatura ambiente, il pannello cerca i cali più rapidi di quelli naturali, guardando anche l’umidità assoluta, che con la finestra aperta scende. Li segna con una fascia verde, anche sui giorni già registrati.'],
    ['n', 'Confermate e da confermare', 'La fascia piena è una finestra confermata da te; quella tenue, con i bordi tratteggiati, è ancora da confermare. Su computer c’è anche una iconcina (piena o vuota): toccala e rispondi Sì o No. I «No» spariscono, i «Sì» si possono togliere.'],
    ['n', 'Segnala finestra', 'Il tasto sotto il grafico, da computer, segna a mano una finestra scegliendo da quando a quando. Da telefono le finestre si vedono soltanto, senza icone.'],
    ['n', 'Impara dalle risposte', 'Dopo 10 risposte il pannello sceglie da solo le soglie (calo minimo e umidità) che sbagliano meno rispetto a quello che gli hai detto. Per ora le finestre le mostra soltanto: le statistiche non le escludono ancora.'],
  ]},
  {v: '0.24', quando: '5 novembre 2026', titolo: 'Grafici indietro nel tempo e «1 giorno»', righe: [
    ['n', 'Costo diviso', 'In Efficienza, toccando «Costo stimato» compare il costo di riscaldamento, acqua calda e standby; nella scheda del mese lo stesso con «Dettaglio». In Dashboard, il mouse su «Costo» (card Energia) mostra lo stesso. La somma fa sempre il totale.'],
    ['n', '«1 giorno» in Efficienza', 'Accanto a 3, 7 e 28 giorni c’è «1 giorno»: la giornata di oggi.'],
    ['n', 'Trascina per tornare indietro', 'Analisi, da computer, con 6h o 24h: tieni premuto il mouse su un grafico (o sulla barra dei modi) e trascinalo verso destra per vedere le ore prima, verso sinistra per tornare avanti. Quando rilasci il pannello carica quel periodo; finché sei indietro non si aggiorna da solo, e «Torna ad adesso» in alto rimette la diretta. Da telefono resta lo zoom.'],
  ]},
  {v: '0.23', quando: '5 novembre 2026', titolo: 'Bollitore anche di giorno', righe: [
    ['n', 'Perdita a riposo di giorno', 'Nella «Salute del bollitore» accanto alla perdita di notte c’è quella di giorno (dalle 6 alle 23): si guardano solo i tratti tranquilli di almeno un’ora, senza riscaldo dell’acqua e senza prelievi. Utile per confrontare le ore con e senza ricircolo.'],
    ['m', 'Codice più snello', 'Tolti pezzi di codice e di stile duplicati o mai usati: a vista non cambia niente, ma il file è più corto e più facile da correggere.'],
  ]},
  {v: '0.22.2', quando: '5 novembre 2026', titolo: 'Gas di scarico', righe: [
    ['m', 'Gas di scarico finalmente letto', 'La temperatura del gas di scarico del compressore stava in un byte diverso da quello che il pannello leggeva (sempre vuoto): ora viene letta e compare nella scheda Ciclo. Per averla anche nei giorni passati: python pdc_pannello.py ridecodifica.'],
  ]},
  {v: '0.22.1', quando: '5 novembre 2026', titolo: 'Limite del codice 4013', righe: [
    ['m', 'FSV 4013 fino a 45 °C', 'La temperatura esterna oltre la quale il riscaldamento si ferma può arrivare a 45 °C: prima oltre 35 °C compariva «fuori limiti», anche se la pompa li accetta.'],
  ]},
  {v: '0.22', quando: '5 novembre 2026', titolo: 'Modifica delle impostazioni installatore', righe: [
    ['n', 'Modifiche solo da casa', 'Tutte le modifiche della scheda Regolazioni (FSV, temperatura e modo dell’acqua calda, Boost, modo della pompa) si fanno solo dalla rete di casa: da fuori (tunnel Cloudflare) non compare nemmeno «Sblocca modifiche» e il pannello rifiuta la richiesta.'],
    ['m', 'Testi degli FSV rivisti', 'Nomi e scelte dei codici FSV riscritti in modo più chiaro e controllati sul manuale Samsung: «mandata» per l’acqua dell’impianto, «Fuori casa» per le impostazioni di assenza, «Miscelatrice» davanti ai codici della valvola. Premendo la matita compare una spiegazione breve del codice.'],
    ['m', 'Box chiusi centrati', 'Nei box chiusi del pannello Regolazioni il testo ora è centrato in verticale, e i valori stanno sempre a destra accanto alla matita.'],
    ['n', 'Boost acqua calda', 'Nella scheda Regolazioni → Acqua calda: ⚡ avvia un boost come Dwellsee (setpoint all’obiettivo scelto, poi modo «Forzato»). Finisce quando l’acqua arriva all’obiettivo o dopo 2 ore, e rimette modo e setpoint di prima; se il pannello si riavvia, il ripristino riparte da solo. ■ lo ferma a mano.'],
    ['n', 'Modifica degli FSV dal pannello', 'In Dispositivi → Installatore: «Sblocca modifiche» (30 secondi, come i comandi), la matita sulla riga, il nuovo valore e una conferma. Si accettano solo i codici e i limiti del manuale, e un valore alla volta.'],
    ['n', 'Scheda «Regolazioni»', 'La scheda Installatore si chiama ora Regolazioni: in alto temperatura e modo dell’acqua calda e modo della pompa (Riscaldamento, Raffrescamento, Automatico), con lo stesso lucchetto e le stesse conferme; sotto, gli FSV.'],
    ['n', 'Cronologia delle modifiche', 'In fondo alla scheda: ogni cambio visto, con il valore di prima e quello di dopo, e se l’ha fatto il pannello o è stato letto dalla pompa.'],
    ['m', 'Zona della curva climatica', 'La scheda «Curva climatica» dice se è la zona 1 (pavimento) o la 2: la prende dal codice 2041 se SmartThings lo comunica, altrimenti assume la zona 1.'],
    ['m', 'Rilettura con calma', 'Dopo una modifica la riga dice «in attesa» finché la pompa non conferma il valore nuovo: SmartThings può metterci un po’ ad aggiornarlo.'],
    ['s', 'Prudenza', 'Il comando per scrivere un FSV lo chiede il pannello a SmartThings: se non lo capisce non scrive niente e mostra cosa ha trovato.'],
  ]},
  {v: '0.21.3', quando: '4 novembre 2026', titolo: 'Ripartenza più stabile', righe: [
    ['m', 'Ripartenza prevista ferma', 'L’orario non salta più avanti e indietro a ogni decimo di grado del sensore: cambia solo se la stima si sposta davvero (oltre 30 minuti da lontano, sempre meno man mano che la ripartenza si avvicina).'],
  ]},
  {v: '0.21.2', quando: '4 novembre 2026', titolo: 'Ritocchi', righe: [
    ['m', 'Icona della scheda', 'Nel browser (Safari, Chrome) l’icona è solo il simbolo, più grande e senza il quadrato scuro; quella della Home dell’iPhone non cambia.'],
    ['m', 'Messaggi Telegram di sbrinamento', 'Anche gli avvisi di inizio e fine sbrinamento hanno la riga «Pompa di calore» in grassetto, come tutti gli altri messaggi.'],
  ]},
  {v: '0.21.1', quando: '4 novembre 2026', titolo: 'Changelog dei plugin', righe: [
    ['n', 'Tab «Plugin» nel Changelog', 'Il Changelog ha due tab: Pannello (come prima) e Plugin, con la cronologia di ogni plugin, tutti insieme in ordine di data o uno alla volta con il filtro. La versione che hai installata è segnata «Installato».'],
    ['m', 'Pannello più veloce da fuori', 'Le pagine (app.js e app.css) si scaricano una volta sola e, se non cambiano, il browser riceve soltanto «invariato»: meno dati sul tunnel e caricamento più rapido, soprattutto da telefono.'],
    ['m', 'Indirizzi dei plugin più protetti', 'Anche gli indirizzi sbagliati hanno un limite di tentativi al minuto, come quelli giusti.'],
    ['m', 'Plugin nuovi senza lavoro in più', 'Ogni plugin porta la sua cronologia nel proprio file (lista CHANGELOG): un plugin nuovo compare da solo nella tab.'],
  ]},
  {v: '0.21', quando: '4 novembre 2026', titolo: 'Umidità esterna', righe: [
    ['n', 'Umidità esterna', 'Nella card Esterna, sotto Min/Max, compare «Umidità 78%», letta da una stazione meteo vicina (WeatherLink). Sta nella riga degli avvisi: se appare «Dato delle… (PdC ferma)» prende il suo posto. Le dimensioni della Dashboard non cambiano. Si accende incollando l’indirizzo dei dati in Impostazioni → Collegamenti; senza indirizzo o senza dato la riga non compare.'],
  ]},
  {v: '0.20', quando: '4 novembre 2026', titolo: 'Integrazioni e Alexa', righe: [
    ['n', 'Integrazioni a plugin', 'Impostazioni → Integrazioni: i collegamenti opzionali sono file nella cartella «plugin», con interruttore, campi e stato. Un plugin nuovo parte spento, si ricarica con un tasto e, se si rompe, non ferma mai il pannello.'],
    ['n', 'Alexa', 'Il primo plugin: con le Routine di Alexa chiedi temperatura e umidità, se il riscaldamento è acceso e quando riparte, cosa fa la PdC, acqua calda e mandata, oppure accendi e spegni lo Smarther. Non devi pronunciare il nome della skill.'],
    ['n', 'Indirizzo protetto per Alexa', 'L’indirizzo che dai ad Amazon contiene un segreto, accetta solo la tua skill e richieste fresche e uniche; con «Verifica firma Amazon» controlla anche che arrivino davvero da Amazon.'],
    ['m', 'Stato dei plugin in diretta', 'Stato, contatore e ultima richiesta si aggiornano da soli ogni pochi secondi, senza toccare quello che stai scrivendo.'],
    ['m', 'Indirizzo da fuori casa', 'Gli indirizzi pubblici dei plugin si mostrano già con il tuo indirizzo Cloudflare, pronti da copiare.'],
  ]},
  {v: '0.19', quando: '3 novembre 2026', titolo: 'Ripartenza prevista e giorni tipici', righe: [
    ['n', 'Ripartenza prevista', 'Nella card Smarther: l’ora in cui il riscaldamento dovrebbe ripartire, stimata dallo storico della casa.'],
    ['n', 'Giorni tipici', 'Efficienza → Confronta: nei grafici dei giorni compaiono consumo e COP normali per quel freddo, e un puntino sui giorni peggiori del normale.'],
    ['n', 'Avviso se il pannello non si apre da fuori', 'Telegram ti scrive se da internet il pannello non risponde per qualche minuto; quando torna tutto a posto lo chiude da solo.'],
    ['n', 'Programma dello Smarther', 'In Dispositivi: il programma orario che il termostato ha in memoria, a fasce oppure sempre fisso.'],
    ['n', 'Zoom sui grafici', 'Analisi, da telefono: pizzica con due dita per ingrandire il tempo (tutti i grafici insieme), trascina con un dito per spostarti, doppio tocco per tornare alla vista intera. In più i grafici sono più larghi, con i numeri degli assi in una colonna stretta.'],
    ['n', 'Elenco degli sbrinamenti', 'In Efficienza → Cicli di funzionamento: il riquadro «Sbrinamenti» dice anche quanta energia hanno usato; toccalo per l’elenco a scomparsa con ora, durata, consumo approssimato e temperatura esterna.'],
    ['n', 'Cursore nei grafici di Efficienza', 'Passando il mouse o toccando un grafico di Efficienza compare, come in Analisi, la linea con i puntini e l’etichetta in alto; i valori di quel giorno (o di quella fascia di temperatura) si leggono nella legenda.'],
    ['n', 'Avvisi Telegram di sbrinamento', 'Un messaggio senza suoneria quando la PdC entra in sbrinamento e uno quando finisce (durata e consumo). Si accendono e si spengono a parte in Impostazioni → Avvisi, e i testi si possono cambiare.'],
    ['m', 'Avvisi di Efficienza in alto', 'Dati incompleti e l’avviso di Confronta ora stanno subito sotto «Aggiornato», così li vedi appena premi il tasto.'],
    ['m', 'Note e legende più ordinate', 'In Analisi le note sono un elenco con icona e la legenda di «Modo di funzionamento» sta su una riga; l’icona «i» è la stessa dappertutto.'],
    ['m', 'Riquadri di Impostazioni', 'Quando sono aperti hanno meno spazio vuoto, uguale sopra e sotto.'],
    ['m', 'Avvisi nelle card', '«Smarther non collegato» in rosso, «Ultimo dato delle…» in giallo, nello stesso box della ripartenza (anche per la PdC ferma nella card Esterna).'],
    ['m', 'Meteo sempre di oggi', 'Il dettaglio mostra 3 righe da 3 ore a partire da adesso, sempre con «Oggi», mai «Domani».'],
    ['m', 'Card Smarther stabile', 'Su PC la card ha sempre la stessa altezza, con o senza la riga della ripartenza: niente più saltelli.'],
    ['s', 'Password chiesta a ogni apertura', 'Su iPhone, se l’icona della Home era stata salvata mentre eri sulla pagina di accesso, ogni apertura mostrava il modulo di login anche con la sessione ancora valida (30 giorni). Ora, se sei già dentro, «/login» ti porta subito alla dashboard. Il login, i tentativi massimi e la sicurezza dei cookie non sono cambiati.'],
  ]},
  {v: '0.18', quando: '3 novembre 2026', titolo: 'Meteo, costo del mese e dati più freschi', righe: [
    ['n', 'Meteo', 'Nella scheda Esterna: icona e forchetta di oggi, due modelli a confronto; toccala per il dettaglio ogni 3 ore.'],
    ['n', 'Costo del mese', 'In Efficienza: speso finora, stima a fine mese e confronto col mese scorso.'],
    ['n', 'Refresh dei dati', 'Impostazioni → Collegamenti: la pompa viene interrogata ogni minuto mentre lavora, per dati meno in ritardo.'],
    ['n', 'Azzera il database', 'Impostazioni → Dati: riparte da zero, tenendo impostazioni e password, dopo una copia di sicurezza.'],
    ['m', 'Curva climatica', 'Riquadri Freddo, Mite e Adesso allineati, note brevi con il loro simbolo.'],
    ['m', 'Efficienza ordinata', 'Cicli a gruppi colorati, note brevi con il loro simbolo, formule a scomparsa e pulsanti per l’Excel.'],
    ['m', 'Ritocchi ai testi', 'Più coerenti in tutto il pannello e nei messaggi Telegram.'],
    ['m', 'Impostazioni rifatte', 'Campi corti in linea, aiuto sotto ogni campo, riquadri chiusi su due colonne che si ricordano come li hai lasciati.'],
    ['m', 'Più leggero e leggibile', 'Pagine e dati compressi (da 124 a circa 40 KB); nei grafici i periodi senza dati hanno le righe oblique.'],
    ['s', 'Stato più pronto', '«A riposo» e «Riscaldamento» si aggiornano in 1–2 minuti. Tolto l’avviso «Resistenza elettrica»: non c’è nell’impianto.'],
  ]},
  {v: '0.17', quando: '3 novembre 2026', titolo: 'Aggiornare senza fermare i dati', righe: [
    ['n', 'Applica aggiornamento', 'Impostazioni → Stato: le parti di interfaccia entrano a caldo e la lettura dei dati non si ferma mai.'],
    ['m', 'Più chiaro', 'Se è cambiato il nucleo (dati, Telegram, tunnel, backup) il pannello lo dice, e il riavvio serve come prima.'],
    ['s', 'File con errori', 'Se il file nuovo ha un errore non cambia niente: continua il codice di prima, col motivo scritto in chiaro.'],
  ]},
  {v: '0.16', quando: '3 novembre 2026', titolo: 'Cartelle in ordine', righe: [
    ['m', 'Ogni cosa al suo posto', 'Database in «dati», backup in «dati/backup», loghi in «dati/loghi», registro in «log»; in radice solo i file che usi.'],
    ['m', 'Si sistema da solo', 'Al primo avvio sposta i file vecchi (non copia né cancella) e, se qualcosa non torna, rimette tutto com’era.'],
    ['m', 'Documenti', 'README e PIANO vanno nella cartella «docs».'],
  ]},
  {v: '0.15', quando: '3 novembre 2026', titolo: 'Un solo grafico per compressore, portata e pompa', righe: [
    ['n', 'Grafico unico', 'Analisi: compressore, pompa e portata insieme, a gradini come arrivano i dati, con i valori al passaggio del mouse.'],
    ['n', 'Richiesta di caldo', 'Una striscia rossa sotto il grafico dello Smarther, con «sì/no» all’ora scelta.'],
    ['m', 'Compressore da fermo', 'Dashboard: stato «Fermo» e tre riquadri sull’ultimo ciclo: per cosa, quanto dura, quanta energia.'],
    ['m', 'Nota sul ritardo', 'Su una riga ordinata, senza «min» rimasto a capo.'],
    ['s', 'Gas di scarico', 'Compare solo se la pompa lo manda davvero.'],
  ]},
  {v: '0.14', quando: '3 novembre 2026', titolo: 'Umidità in casa: soglie e avvisi', righe: [
    ['n', 'Avvisi sull’umidità', 'Telegram avvisa sopra il 60 % e il 70 %; soglie e attese si cambiano in Impostazioni → Avvisi.'],
    ['n', 'Riepilogo con la notte', 'Il riepilogo del mattino racconta anche la notte, ma solo se è successo qualcosa di nuovo.'],
    ['m', 'Colori e soglie', 'L’umidità diventa gialla e rossa in Dashboard, e due linee tratteggiate la accompagnano nel grafico.'],
    ['m', 'Grafici', 'La potenza è una linea a gradini, e passando il mouse ogni linea ha il suo puntino.'],
  ]},
  {v: '0.13', quando: '3 novembre 2026', titolo: 'Impostazioni in tab', righe: [
    ['n', 'Sei tab', 'Stato, Collegamenti, Avvisi, Accesso, Dati e Registro; i riquadri restano aperti o chiusi come li lasci.'],
    ['n', 'Nuovi riquadri', 'In Dati: litri del bollitore e backup. In Cloudflare: «Invia il link su Telegram».'],
    ['m', 'Errori chiari', 'Un valore non valido apre il riquadro giusto, segnato in rosso.'],
    ['m', 'Telefono', 'Dispositivi più ordinato; grafici Smarther rinominati «Temperatura ambiente» e «Umidità»; «acceso da 2 minuti».'],
  ]},
  {v: '0.12', quando: '3 novembre 2026', titolo: 'Confronto, bollitore, registro e ripristino', righe: [
    ['n', 'Confronta', 'Efficienza: il periodo accanto al precedente, di pari durata: COP, energia, calore e cicli di funzionamento.'],
    ['n', 'Salute del bollitore', 'Quanto calore perde di notte e quanto ci mette a scaldarsi.'],
    ['n', 'Registro e ripristino', 'Impostazioni → Registro, e il comando «ripristina» per rimettere un backup salvato.'],
    ['s', 'Più robusto', 'Disco pieno o database non scrivibile non fermano più la raccolta: riprova da sola e lo scrive nel registro.'],
    ['s', 'Più sicuro', 'config.json leggibile solo dal proprietario, login che rifiuta richieste da altri siti, tempo reale di Safari che si ricollega.'],
  ]},
  {v: '0.11', quando: '2 novembre 2026', titolo: 'Accendere e spegnere dal pannello', righe: [
    ['n', 'Interruttori', 'Riscaldamento, Acqua calda e Smarther: bloccati finché non premi «Sblocca comandi», con conferma allo spegnimento.'],
    ['n', 'Costi e dati', 'Costo in euro (prezzo in Impostazioni), esportazione CSV per Excel, backup notturno, pagina Changelog.'],
    ['m', 'Ritardo misurato', 'Il pannello misura e mostra il ritardo dei dati, e non dice più «Standby» mentre la pompa parte.'],
    ['s', 'Smarther', 'Capisce se è spento dal pannello o dall’app, e verifica ogni comando su Netatmo.'],
  ]},
  {v: '0.10', titolo: 'Impostazioni installatore', righe: [
    ['n', 'Scheda Installatore', 'In Dispositivi: le impostazioni FSV in sola lettura, con l’intervallo del manuale, l’avviso se escono e «Copia elenco».'],
  ]},
  {v: '0.9', titolo: 'Pagina Dispositivi', righe: [
    ['n', 'Pompa e Smarther', 'Modello, versioni, identificativi con «Copia», IP e MAC, stato, storico delle firmware e loghi tuoi.'],
    ['m', 'Si aggiorna da sola', 'Dopo un aggiornamento le pagine rimaste aperte si ricaricano da sole.'],
    ['s', 'Stato corretto', '«A riposo» non compare più a pompa in marcia, nemmeno per il ritardo dei dati.'],
  ]},
  {v: '0.8', titolo: 'Colori e dettagli', antico: true, righe: [
    ['m', 'Badge di stato', 'A colori pieni, sempre sotto il titolo delle schede.'],
    ['s', 'Acqua calda', 'Non va più in «Standby» durante un ciclo.'],
  ]},
  {v: '≤ 0.7', titolo: 'Come è nato', antico: true, righe: [
    ['n', 'Il nucleo', 'Lettura della pompa (SmartThings) e dello Smarther (Netatmo), database e decodifica del ciclo.'],
    ['n', 'Il sito', 'Server web con login, Dashboard con curva climatica, tempo reale, modalità demo.'],
    ['n', 'Analisi ed Efficienza', 'Grafici sincronizzati, COP stimato, energia al giorno, cicli.'],
    ['n', 'Telegram', 'Avvisi sulle anomalie e riepilogo del mattino.'],
    ['n', 'Impostazioni e accesso', 'Config dal pannello, password, tunnel Cloudflare, README, compattazione dello storico.'],
  ]},
];

const clRiga = r => `<li><span class="pill tg ${ {n: 'c-ok', m: 'c-acs', s: 'c-en'}[r[0]] }">${CL_TIPI[r[0]]}</span><span class="cl-tx"><b>${esc(r[1])}</b> ${esc(r[2])}</span></li>`;   // una riga del Changelog (Pannello e Plugin)
function disegnaChangelogPannello() {
  const voce = (c, attuale) => `<section class="cl-v${attuale ? ' att' : ''}"><div class="cl-h"><span class="cl-n">${esc(c.v)}</span>` +
    `${attuale ? '<span class="pill en">Attuale</span>' : ''}<span class="cl-ti">${esc(c.titolo)}</span>${c.quando ? `<span class="cl-d">${esc(c.quando)}</span>` : ''}</div>` +
    `<ul>${c.righe.map(clRiga).join('')}</ul></section>`;
  const recenti = CHANGELOG.filter((c, k) => k < 3), prima = CHANGELOG.filter((c, k) => k >= 3 && !c.antico), antiche = CHANGELOG.filter(c => c.antico);
  const breve = `<div class="cl-breve"><div class="eti">In breve</div>${CL_BREVE.map(b => `<div class="cl-bi">${CL_IC[b[0]] ? `<span class="cl-ic">${CL_IC[b[0]]}</span>` : iconaMeteo(b[0], 22)}<span><b>${esc(b[1])}</b> ${esc(b[2])}</span></div>`).join('')}</div>`;
  return `${breve}<div class="cl-tl">${recenti.map((c, k) => voce(c, k === 0)).join('')}` +
    `<details class="cl-vecchie"><summary>Versioni precedenti</summary>${prima.map(c => voce(c)).join('')}` +
    `<div class="eti">Le prime versioni</div>${antiche.map(c => voce(c)).join('')}</details></div>`;
}

// Changelog: due tab, Pannello (qui sopra) e Plugin (la cronologia che ogni plugin dichiara nel suo file, dall'API)
S.clPlugin = S.clPlugin || '';      // filtro della tab Plugin: '' = tutti, altrimenti il nome del plugin
let clDati = null;
const MESI_IT = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre'];
const dataIt = d => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(d || ''); return m ? `${+m[3]} ${MESI_IT[+m[2] - 1]} ${m[1]}` : ''; };
function disegnaChangelogPlugin() {
  if (!clDati) return '<div class="card vuoto">Carico…</div>';
  const plug = clDati.plugin || [];
  if (!plug.length) return '<div class="card vuoto">Nessun plugin con una cronologia. Un plugin la dichiara con la lista <code>CHANGELOG</code> nel suo file.</div>';
  const filtro = plug.some(p => p.nome === S.clPlugin) ? S.clPlugin : '';
  const chips = `<div class="chips cl-filtro">${[['', 'Tutti']].concat(plug.map(p => [p.nome, p.titolo])).map(([k, t]) =>
    `<button data-clp="${esc(k)}" class="${k === filtro ? 'on' : ''}">${esc(t)}</button>`).join('')}</div>`;
  const voci = [];
  for (const p of plug) if (!filtro || p.nome === filtro) for (const c of p.changelog) voci.push({p, c});
  voci.sort((a, b) => (b.c.data || '').localeCompare(a.c.data || ''));   // stabile: a pari data resta l'ordine del plugin
  const corpo = voci.map(({p, c}) => {
    const att = c.v === p.versione;
    return `<section class="cl-v${att ? ' att' : ''}"><div class="cl-h">${filtro ? '' : `<span class="pill up">${esc(p.titolo)}</span>`}<span class="cl-n">${esc(c.v)}</span>` +
      `${att ? '<span class="pill en">Installato</span>' : ''}<span class="cl-ti">${esc(c.titolo)}</span>${c.data ? `<span class="cl-d">${esc(dataIt(c.data))}</span>` : ''}</div>` +
      `<ul>${c.righe.map(clRiga).join('')}</ul></section>`;
  }).join('');
  return `${chips}<div class="cl-tl">${corpo}</div>`;
}
function vistaChangelog() {
  const plugin = S.sub === 'plugin';
  const tabs = `<nav class="sub-tabs cl-tabs" aria-label="Sezioni"><a href="#/changelog" class="${plugin ? '' : 'on'}">Pannello</a><a href="#/changelog/plugin" class="${plugin ? 'on' : ''}">Plugin</a></nav>`;
  $('#pagina').innerHTML = `<div class="changelog">${tabs}${plugin ? disegnaChangelogPlugin() : disegnaChangelogPannello()}</div>`;
  window.scrollTo(0, 0);
  if (plugin && !clDati) caricaChangelogPlugin();
}
async function caricaChangelogPlugin() {
  try {
    const r = await leggiJson('/api/plugin/changelog'); if (!r) return;
    clDati = r.ok ? await r.json() : {plugin: []};
  } catch (e) { clDati = {plugin: []}; }
  if (S.pagina === 'changelog' && S.sub === 'plugin') { const y = window.scrollY; vistaChangelog(); window.scrollTo(0, y); }
}
document.addEventListener('click', e => {
  const b = e.target.closest && e.target.closest('button[data-clp]');
  if (!b || S.pagina !== 'changelog') return;
  S.clPlugin = b.dataset.clp; vistaChangelog();
});

// ---------- Efficienza ----------
const euro = v => v == null ? '—' : v.toLocaleString('it-IT', {style: 'currency', currency: 'EUR'});
const fm = (v, n = 1) => v == null ? '—' : num(v, n);
const giornoBreve = g => { const [, m, d] = g.giorno.split('-'); return `${d}/${m}`; };

// confronto con il periodo di pari durata subito prima: differenza in % (null se il dato prima e' troppo piccolo)
function diffPerc(ora_, prima, minimo = 0.5) {
  if (ora_ == null || prima == null || Math.abs(prima) < minimo) return null;
  return (ora_ - prima) / prima * 100;
}
function rigaConf(d, ora_, prima, fmt, meglio, minimo) {
  // meglio: 'su' (piu' alto e' meglio, es. COP), 'giu' (piu' basso e' meglio, es. consumo), '' (neutro)
  if (!S.confronto || !d.prec) return '';
  if (!d.prec.completo) return '';
  const p = diffPerc(ora_, prima, minimo);
  const segno = p == null ? '' : (p > 0.5 ? '▲' : p < -0.5 ? '▼' : '=');
  const buono = p == null || Math.abs(p) <= 0.5 || !meglio ? '' : ((p > 0) === (meglio === 'su') ? 'buono' : 'cattivo');
  return `<div class="conf-r"><span>Prima <b>${fmt(prima)}</b></span><span class="conf-d ${buono}">${p == null ? '—' : segno + ' ' + num(Math.abs(p), 0) + '%'}</span></div>`;
}
const notaTip = d => tipici(d) ? '' : ' Il consumo «tipico» nei grafici comparirà quando ci saranno almeno 3 giorni con lo stesso freddo.';
// avviso di Confronta: sta in cima alla pagina, sotto «Aggiornato», cosi' si vede appena premi il tasto
function avvisoConf(d) {
  if (!S.confronto || !d.prec || d.prec.completo) return '';
  return `<div class="avviso warn"><span><i class="in"></i></span><div>Il pannello non ha ancora dati per tutto il periodo di confronto (${esc(dataLunga(d.prec.da))} – ${esc(dataLunga(d.prec.a))}): il confronto comparirà quando ci saranno.${notaTip(d)}</div></div>`;
}
function notaConf(d) {
  if (!S.confronto || !d.prec || !d.prec.completo) return '';
  const da = dataLunga(d.prec.da), a = dataLunga(d.prec.a), tip = notaTip(d);
  const dm = d.prec.nodati_min;
  return `<p class="nota f conf-nota">Confronto con il periodo di pari durata subito prima: dal ${esc(da)} al ${esc(a)}.${dm > 120 ? ` In quel periodo mancano circa ${durata(dm)} di dati: il confronto è meno preciso.` : ''}${tip}</p>`;
}
function cardBollitore(d) {
  const b = d.bollitore, pb = S.confronto && d.prec && d.prec.completo ? d.prec.bollitore : null;
  const conf = (v, fmtv) => pb === undefined || !S.confronto || !d.prec || !d.prec.completo ? '' : `<small class="conf-t">${v == null ? 'Prima: dati insufficienti' : 'Prima ' + fmtv(v)}</small>`;
  const t = (et, val, sub, cf) => `<div class="tile"><small>${et}</small><b>${val}</b>${sub ? `<small>${sub}</small>` : ''}${cf || ''}</div>`;
  const p = b && b.perdita, sa = b && b.salita, g = b && b.giorno, pp_ = pb && pb.perdita, ps = pb && pb.salita, pg = pb && pb.giorno;
  const tiles = (p ? t('Perdita di notte', num(p.gradi_ora, 2) + ' °C/h', `circa ${num(p.watt, 0)} W · ${p.notti} notti`,
      conf(pp_ ? pp_.gradi_ora : null, v => num(v, 2) + ' °C/h')) : t('Perdita di notte', '—', 'servono almeno 2 notti senza acqua calda'))
    + (g ? t('Perdita di giorno', num(g.gradi_ora, 2) + ' °C/h', `circa ${num(g.watt, 0)} W · ${num(g.ore, 1)} ore`,
      conf(pg ? pg.gradi_ora : null, v => num(v, 2) + ' °C/h')) : t('Perdita di giorno', '—', 'servono almeno 2 ore tranquille'))
    + (sa ? t('Tempo per salire di 10 °C', durata(sa.min_per_10), `${num(sa.gradi_ora, 1)} °C/h · ${sa.cicli} cicli`,
      conf(ps ? ps.min_per_10 : null, v => durata(v))) : t('Tempo per salire di 10 °C', '—', 'servono almeno 2 cicli completi'))
    + (sa ? t('Ciclo tipico', '+' + num(sa.gradi, 1) + ' °C', `in ${durata(sa.minuti)}`, conf(ps ? ps.gradi : null, v => '+' + num(v, 1) + ' °C')) : '');
  return `<article class="card"><h2>Salute del bollitore</h2><div class="tiles tc c-acs">${tiles}</div>
    <ul class="nota-cv"><li><i class="in"></i><span>Stime dalla sonda dell'acqua calda (${b ? b.litri : 300} litri). Sono indicazioni, non diagnosi. <button type="button" class="come-b" data-come="boll" aria-expanded="false">Come leggerli</button></span></li></ul>
    <p class="come-t" id="come-boll" hidden>La <b>perdita</b> dice quanto si raffredda l'acqua del bollitore quando la pompa non la scalda: di notte si guarda il tratto più lungo tra le 23 e le 6, di giorno (6–23) solo i tratti tranquilli di almeno un’ora, senza docce o rubinetti aperti, quindi ce ne sono meno e il valore è meno preciso. Il confronto tra i due mostra quanto pesano i consumi e il ricircolo. Se col tempo aumenta, può esserci un problema di isolamento o di ricircolo. Il <b>tempo per salire di 10 °C</b> dice quanto ci mette la pompa a scaldare l'acqua di 10 gradi: se si allunga, la pompa rende meno oppure c'è più acqua da scaldare.</p></article>`;
}
// Costo del mese in corso (non dipende dal periodo scelto): stima a fine mese e confronto col mese scorso
function meseCosto(m) {
  if (!m) return '';
  return `<article class="card acc" style="--acc:var(--en)"><div class="ch"><span class="et">Costo di ${MESI_IT[m.mese - 1]}</span></div>
      <div class="val"><div class="n"><span>${euro(m.euro)}</span></div></div>
      <div class="sub">${m.stima_euro != null ? `<span>Stima a fine mese <b>${euro(m.stima_euro)}</b></span>` : ''}${m.scorso_euro != null ? `<span>${MESI_IT[m.mese_scorso - 1]} <b>${euro(m.scorso_euro)}</b></span>` : ''}</div>
      <div class="nota">Stima: media dei giorni passati, indicativa.${m.dettaglio ? ' <span class="come-b" data-come="eurm" role="button" tabindex="0" aria-expanded="false">Dettaglio</span>' : ''}</div>${m.dettaglio ? `<p class="come-t" id="come-eurm" hidden><span class="nw">Riscaldamento <b>${euro(m.dettaglio.casa)}</b></span><span class="nw">Acqua calda <b>${euro(m.dettaglio.acs)}</b></span><span class="nw">Standby <b>${euro(m.dettaglio.riposo)}</b></span></p>` : ''}</article>`;
}
const ICONA_SCARICA = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 11l5 5 5-5M5 20h14"/></svg>';
function disegnaEfficienza(d) {
  EF.d = d;
  const t = d.tot, nodati = d.giorni.reduce((a, g) => a + g.nodati_min, 0);
  const cop = v => v == null ? '—' : num(v, 2);
  const pt = d.prec ? d.prec.tot : {};
  const cov = d.copertura, bassa = [cov.casa, cov.acs].some(v => v != null && v < 80);
  const avvDati = bassa || nodati > 120 ? `<div class="avviso warn"><span>⚠️</span><div>Dati incompleti${bassa ? `: per il calcolo del COP ho potenza e portata solo per il ${Math.min(cov.casa ?? 100, cov.acs ?? 100)}% del tempo di funzionamento` : ''}${nodati > 120 ? ` (mancano circa ${durata(nodati)} di dati nel periodo)` : ''}. I valori sono più bassi o meno precisi del reale.</div></div>` : '';
  const html_ = `
  ${avvisoConf(d)}${avvDati}
  <div class="cards c5">
    <article class="card acc" style="--acc:var(--est)"><div class="ch"><span class="et">COP medio</span></div>
      <div class="val"><div class="n"><span>${cop(t.cop)}</span></div></div>
      <div class="sub"><span>Risc. <b>${cop(t.cop_casa)}</b></span><span>Acqua <b>${cop(t.cop_acs)}</b></span></div>${rigaConf(d, t.cop, pt.cop, cop, 'su', 0.1)}</article>
    <article class="card acc" style="--acc:var(--en)"><div class="ch"><span class="et">Energia elettrica</span></div>
      <div class="val"><div class="n"><span>${fm(t.el_casa + t.el_acs + t.el_riposo)}</span><i>kWh</i></div></div>
      <div class="sub"><span>Risc. <b>${fm(t.el_casa)}</b></span><span>Acqua <b>${fm(t.el_acs)}</b></span><span>Standby <b>${fm(t.el_riposo)}</b></span>${d.prezzo_kwh > 0 ? `<span><span class="come-b" data-come="eur" role="button" tabindex="0" aria-expanded="false">Costo stimato</span> <b>${euro((t.el_casa + t.el_acs + t.el_riposo) * d.prezzo_kwh)}</b></span>` : ''}</div>${d.prezzo_kwh > 0 ? `<p class="come-t" id="come-eur" hidden><span class="nw">Riscaldamento <b>${euro(t.el_casa * d.prezzo_kwh)}</b></span><span class="nw">Acqua calda <b>${euro(t.el_acs * d.prezzo_kwh)}</b></span><span class="nw">Standby <b>${euro(t.el_riposo * d.prezzo_kwh)}</b></span></p>` : ''}${rigaConf(d, t.el_casa + t.el_acs + t.el_riposo, pt.el_casa + pt.el_acs + pt.el_riposo, v => fm(v) + ' kWh', 'giu', 1)}</article>
    ${meseCosto(d.mese)}
    <article class="card acc" style="--acc:var(--casa)"><div class="ch"><span class="et">Calore prodotto (stima)</span></div>
      <div class="val"><div class="n"><span>${fm(t.calore_casa + t.calore_acs)}</span><i>kWh</i></div></div>
      <div class="sub"><span>Risc. <b>${fm(t.calore_casa)}</b></span><span>Acqua <b>${fm(t.calore_acs)}</b></span></div>${rigaConf(d, t.calore_casa + t.calore_acs, pt.calore_casa + pt.calore_acs, v => fm(v) + ' kWh', '', 1)}</article>
    <article class="card acc" style="--acc:var(--acs)"><div class="ch"><span class="et">Ore di funzionamento</span></div>
      <div class="val"><div class="n"><span>${fm(t.ore_casa + t.ore_acs)}</span><i>h</i></div></div>
      <div class="sub"><span>Risc. <b>${fm(t.ore_casa)}</b></span><span>Acqua <b>${fm(t.ore_acs)}</b></span></div>${rigaConf(d, t.ore_casa + t.ore_acs, pt.ore_casa + pt.ore_acs, v => fm(v) + ' h', '', 0.5)}</article>
  </div>
  ${notaConf(d)}
  <ul class="nota-cv alto"><li><i class="in"></i><span>Il calore è una stima, non una misura. <button type="button" class="come-b" data-come="calc" aria-expanded="false">Come si calcola</button></span></li></ul>
  <p class="come-t alto" id="come-calc" hidden>Il calore si stima così: portata dell'acqua × differenza di temperatura tra mandata e ritorno × 0,0698 (il risultato è in kW, con la portata in L/min e la temperatura in °C). Il COP è il calore prodotto diviso l'energia elettrica assorbita, e si calcola solo quando la pompa lavora (riscaldamento e acqua calda): lo standby non conta. Il totale dell'energia elettrica, invece, comprende anche lo standby.${d.energia_contatore != null ? ` Contatore della PdC nel periodo: ${fm(d.energia_contatore, 1)} kWh.` : ''}</p>
  <div class="grid2 uguali">
    <article class="card"><div class="ch"><h2>Energia elettrica al giorno</h2><div class="legg" id="lg-en">${legEf('en', null)}</div></div><div id="g-en"></div></article>
    <article class="card"><div class="ch"><h2>COP al giorno</h2><div class="legg" id="lg-cop">${legEf('cop', null)}</div></div><div id="g-cop"></div></article>
  </div>
  <div class="grid2 uguali">
    <article class="card"><div class="ch"><h2>COP in base alla temperatura esterna</h2><div class="legg" id="lg-bin">${legEf('bin', null)}</div></div><div id="g-bin"></div>
      <ul class="nota-cv"><li><i class="in"></i>Un punto = fascia di 2 °C, se la PdC ha lavorato almeno mezz'ora.</li></ul></article>
    <article class="card"><h2>Cicli di funzionamento</h2><div id="cicli">${cicliHtml(d)}</div>
      <ul class="nota-cv"><li><i class="in"></i>Un ciclo è un tratto continuo di lavoro.</li><li><i class="av"></i>Tanti cicli brevi: la PdC parte e si ferma troppo spesso.</li></ul></article>
  </div>
  ${cardBollitore(d)}
  <article class="card"><h2>Esporta per Excel</h2><div class="exp">
    <a href="/api/esporta.csv?tipo=giorni&giorni=${d.n_giorni}" download>${ICONA_SCARICA}<span><b>Riepilogo per giorno</b><small>${d.n_giorni === 1 ? 'oggi, una riga' : d.n_giorni + ' giorni, una riga al giorno'}</small></span></a>
    <a href="/api/esporta.csv?tipo=misure&giorni=7" download>${ICONA_SCARICA}<span><b>Dati grezzi</b><small>ultimi 7 giorni, ogni misura</small></span></a>
  </div></article>`;
  const box = $('#eff'); const f = JSON.stringify([d.ora, d.n_giorni, box.clientWidth]);
  if (box.dataset.f === f) return;
  box.dataset.f = f; const y0 = window.scrollY; box.innerHTML = html_;
  (S.comeAperti || []).forEach(id => apriCome(box, id, true));
  const w0 = box.querySelector('#g-en').clientWidth || 320, tel = w0 < 480, w = tel ? w0 + 15 : w0;   // telefono: i grafici escono dal padding, come in Analisi
  if (tel) ['g-en', 'g-cop', 'g-bin'].forEach(id => { $('#' + id).style.margin = '0 -6px 0 -9px'; });
  $('#g-en').innerHTML = barreGiorni(d.giorni, w, tipici(d), tel);
  $('#g-cop').innerHTML = lineeGiorni(d.giorni, w, tipici(d), tel);
  $('#g-bin').innerHTML = curvaCop(d.bins, w, tel);
  if (window.scrollY !== y0) window.scrollTo(0, y0);
}
// «Come leggerli» / «Come si calcola»: testo a scomparsa che si ricorda se era aperto
function apriCome(radice, id, apri) {
  const t = radice.querySelector('#come-' + id), b = radice.querySelector('[data-come="' + id + '"]');
  if (!t || !b) return;
  t.hidden = !apri; b.classList.toggle('on', apri); b.setAttribute('aria-expanded', apri ? 'true' : 'false');
  if (!S.comeAperti) S.comeAperti = new Set();
  apri ? S.comeAperti.add(id) : S.comeAperti.delete(id);
}
document.addEventListener('click', ev => {
  const b = ev.target.closest && ev.target.closest('[data-come]'); if (!b) return;
  apriCome(document, b.dataset.come, b.getAttribute('aria-expanded') !== 'true');
});

function cicliHtml(d) {
  const c = d.cicli, t = (k, v) => `<div class="tile"><small>${k}</small><b>${v}</b></div>`;
  const dm = x => x == null ? '—' : durata(Math.round(x));
  const gr = (cl, tit, righe) => `<div class="cg ${cl}"><h3 class="eti"><i></i>${tit}</h3><div class="tiles">${righe}</div></div>`;
  return gr('c-casa', 'Riscaldamento', t('Cicli', c.casa.n) + t('Durata media', dm(c.casa.media_min))) +
    gr('c-acs', 'Acqua calda', t('Cicli', c.acs.n) + t('Durata media', dm(c.acs.media_min)) +
      (d.prezzo_kwh > 0 && c.acs.n > 0 ? t('Costo medio di un ciclo', euro(d.tot.el_acs * d.prezzo_kwh / c.acs.n)) : '')) +
    gr('c-en', 'Da tenere d’occhio', t('Cicli brevi (<10 min)', c.casa.brevi) + sbrinamentiTile(d.sbr)) + sbrinamentiElenco(d.sbr);
}
// Riquadro «Sbrinamenti» (numero · kWh): se ce ne sono, al tocco apre l'elenco a scomparsa (stesso meccanismo di «Come si calcola»)
function sbrinamentiTile(s) {
  if (!s || !s.n) return `<div class="tile"><small>Sbrinamenti</small><b>${s ? 0 : '—'}</b></div>`;
  return `<div class="tile" data-come="sbr" role="button" tabindex="0" aria-expanded="false"><small>Sbrinamenti</small><b>${s.n}</b>${s.kwh != null ? `<i>· <b>${num(s.kwh, 1)}</b> kWh</i>` : ''}</div>`;
}
function sbrinamentiElenco(s) {
  if (!s || !s.n) return '';
  const gr = v => num(Math.round(v) + 0, 0) + ' °C', quando = t => { const d = new Date(t * 1000); return d.toLocaleDateString('it-IT', {day: 'numeric', month: 'short'}) + ' · ' + d.toLocaleTimeString('it-IT', {hour: '2-digit', minute: '2-digit'}); };
  const riga = x => `<div class="sr"><span>${quando(x.ts)}</span><span>${x.incorso ? 'in corso' : x.min + ' min'}</span><span>${x.kwh != null ? '~' + num(x.kwh, 2) : '—'}</span><span>${x.esterna != null ? gr(x.esterna) : '—'}</span></div>`;
  const v = [s.media_min != null ? num(s.media_min, 0) + ' min' : '', s.kwh != null && s.n ? '~' + num(s.kwh / s.n, 2) + ' kWh' : '', s.esterna != null ? gr(s.esterna) : ''].filter(Boolean).map(t => `<span class="pill">${t}</span>`);
  const testa = `<div class="tiles"><div class="tile"><span class="mut">Media</span> ${v.join(' ')}</div></div>`;
  return `<div class="come-t sbr" id="come-sbr" hidden>${testa}<div>${s.elenco.map(riga).join('')}</div></div>`;
}

// Giorni "tipici" (solo con Confronta): per ogni giorno, consumo e COP normali per il suo freddo. Vuoto se non ce ne sono abbastanza.
const tipici = d => S.confronto && d.tipici && Object.keys(d.tipici).length ? d.tipici : null;
function barreGiorni(giorni, W, tip, tel) {
  const H = 210, R = tel ? 7 : 6, T = 14, B = 24, ph = H - T - B, n = giorni.length;
  let L = 34, pw = W - L - R;
  const tot = g => g.el_casa + g.el_acs + g.el_riposo, mx = Math.max(1, ...giorni.map(tot), ...(tip ? Object.values(tip).map(t => t.el) : []));
  const sc = scalaNice(0, mx, 4); sc.lo = 0;
  L = larghezzaNumeriY(sc, tel); pw = W - L - R;
  const Y = v => T + ph - v / sc.hi * ph, bw = Math.min(46, pw / n * 0.7), cx = i => L + pw / n * (i + 0.5);
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" data-ef="en">`;
  EF.en = {xs: giorni.map((_, i) => cx(i)), lab: giorni.map(giornoBreve), L, pw, T,
    // etichetta = valore del segmento (rosso, blu, standby) sotto il dito; sopra la barra niente (il totale e' gia' scritto sulla barra)
    seg: (i, yy) => { const g = giorni[i]; let y0 = Y(0);
      for (const [k, nome] of [['el_riposo', 'Standby'], ['el_acs', 'Acqua'], ['el_casa', 'Risc.']]) { const h = g[k] / sc.hi * ph; if (yy >= y0 - h || k === 'el_riposo' && yy >= y0) return `${nome} ${num(g[k], 1)} kWh`; y0 -= h; }
      return null; }};
  s += grigliaY(sc, Y, L, W, R);
  giorni.forEach((g, i) => {
    let y = Y(0);
    [['el_riposo', '#3a4558'], ['el_acs', COLS.acs], ['el_casa', COLS.casa]].forEach(([k, c]) => {
      const h = g[k] / sc.hi * ph; if (h <= 0) return; y -= h;
      s += `<rect x="${(cx(i) - bw / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(0.5, h).toFixed(1)}" fill="${c}" rx="2"><title>${giornoBreve(g)}: ${k.replace('el_', '')} ${num(g[k], 1)} kWh</title></rect>`;
    });
    const t = tip && tip[g.giorno];
    if (t && Y(t.el) < y && y - Y(t.el) < 15) y = Y(t.el);
    if (t) {
      const yt = Y(t.el).toFixed(1), tt = `${giornoBreve(g)}: ${num(tot(g), 1)} kWh · tipico per ${num(t.esterna, 0)}°C esterni: ${num(t.el, 1)} kWh (da ${t.n} giorni simili)`;
      s += `<line x1="${(cx(i) - bw / 2 - 3).toFixed(1)}" x2="${(cx(i) + bw / 2 + 3).toFixed(1)}" y1="${yt}" y2="${yt}" class="tip-l"><title>${tt}</title></line>`;
      if (t.sopra) s += `<circle cx="${cx(i).toFixed(1)}" cy="${(y - (n <= 10 ? 17 : 8)).toFixed(1)}" r="3.2" class="tip-a"><title>${tt} — sopra il normale</title></circle>`;
    }
    if (n <= 10) s += `<text x="${cx(i).toFixed(1)}" y="${(y - 4).toFixed(1)}" text-anchor="middle" class="vt" data-i="${i}">${num(tot(g), 1)}</text>`;
    if (n <= 10 || i % Math.ceil(n / 7) === 0) s += `<text x="${cx(i).toFixed(1)}" y="${H - 7}" text-anchor="middle">${giornoBreve(g)}</text>`;
  });
  return s + curEf(T, ph) + '</svg>';
}

function lineeGiorni(giorni, W, tip, tel) {
  const H = 210, R = tel ? 7 : 6, T = 14, B = 24, ph = H - T - B, n = giorni.length;
  let L = 34, pw = W - L - R;
  const vals = giorni.flatMap(g => [g.cop_casa, g.cop_acs, ...(tip && tip[g.giorno] ? [tip[g.giorno].cop_casa, tip[g.giorno].cop_acs] : [])]).filter(v => v != null);
  const sc = scalaNice(0, Math.max(5, ...(vals.length ? vals : [0])), 5); sc.lo = 0;
  L = larghezzaNumeriY(sc, tel); pw = W - L - R;
  const Y = v => T + ph - v / sc.hi * ph, X = i => n === 1 ? L + pw / 2 : L + pw * i / (n - 1);
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" data-ef="cop">`;
  EF.cop = {xs: giorni.map((_, i) => X(i)), lab: giorni.map(giornoBreve), L, pw, T, dots: i => ['cop_casa', 'cop_acs'].map(k => giorni[i][k] == null ? null : Y(giorni[i][k]))};
  s += grigliaY(sc, Y, L, W, R);
  giorni.forEach((g, i) => { if (n <= 10 || i % Math.ceil(n / 7) === 0) s += `<text x="${X(i).toFixed(1)}" y="${H - 7}" text-anchor="${i === n - 1 && n > 1 ? 'end' : 'middle'}">${giornoBreve(g)}</text>`; });
  [['cop_casa', COLS.casa], ['cop_acs', COLS.acs]].forEach(([k, c]) => {
    let p = '', prec = false;
    giorni.forEach((g, i) => { if (g[k] == null) { prec = false; return; } p += (prec ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(g[k]).toFixed(1) + ' '; prec = true; });
    if (tip) {   // COP tipico per il freddo di ciascun giorno: linea tratteggiata sottile
      let q = '', pq = false;
      giorni.forEach((g, i) => { const v = tip[g.giorno] && tip[g.giorno][k]; if (v == null) { pq = false; return; } q += (pq ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(v).toFixed(1) + ' '; pq = true; });
      if (q) s += `<path d="${q}" fill="none" stroke="${c}" stroke-width="1.4" stroke-dasharray="4 4" opacity=".6"/>`;
    }
    s += `<path d="${p}" fill="none" stroke="${c}" stroke-width="2.2" stroke-linejoin="round"/>`;
    giorni.forEach((g, i) => { if (g[k] != null) { const tv = tip && tip[g.giorno] && tip[g.giorno][k];
      s += `<circle cx="${X(i).toFixed(1)}" cy="${Y(g[k]).toFixed(1)}" r="${n > 14 ? 2.5 : 3.5}" fill="${c}"><title>${giornoBreve(g)}: COP ${num(g[k], 2)}${tv != null ? ` · tipico per ${num(tip[g.giorno].esterna, 0)}°C esterni: ${num(tv, 2)}` : ''}</title></circle>`; } });
  });
  return s + curEf(T, ph, [COLS.casa, COLS.acs]) + '</svg>';
}

function curvaCop(bins, W, tel) {
  const H = 210, R = tel ? 7 : 6, T = 14, B = 34, ph = H - T - B;
  let L = 34, pw = W - L - R;
  const tutti = [...bins.casa, ...bins.acs];
  if (!tutti.length) return '<div class="vuoto">Servono più ore di funzionamento per questo grafico.</div>';
  const x0 = Math.min(...tutti.map(b => b.da)), x1 = Math.max(...tutti.map(b => b.a)), mx = Math.max(5, ...tutti.map(b => b.cop));
  const sc = scalaNice(0, mx, 5); sc.lo = 0;
  L = larghezzaNumeriY(sc, tel); pw = W - L - R;
  const X = t => L + (t - x0) / Math.max(1, x1 - x0) * pw, Y = v => T + ph - v / sc.hi * ph;
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" data-ef="bin">`;
  const das = [...new Set(tutti.map(b => b.da))].sort((a, b) => a - b);
  EF.bin = {bins: das, xs: das.map(t => X(t + 1)), lab: das.map(t => `${t}…${t + 2} °C`), L, pw, T,
    dots: i => ['casa', 'acs'].map(m => { const b = bins[m].find(x => x.da === das[i]); return b ? Y(b.cop) : null; })};
  s += grigliaY(sc, Y, L, W, R);
  for (let t = Math.ceil(x0 / 2) * 2; t <= x1; t += (x1 - x0 > 20 ? 5 : 2)) s += `<text x="${X(t).toFixed(1)}" y="${H - 16}" text-anchor="${X(t) > W - 14 ? 'end' : 'middle'}">${t}°</text>`;
  s += `<text x="${(L + pw / 2).toFixed(1)}" y="${H - 2}" text-anchor="middle" class="ass">Temperatura esterna</text>`;
  [['casa', COLS.casa], ['acs', COLS.acs]].forEach(([k, c]) => {
    const b = bins[k]; if (!b.length) return;
    const P = b.map(x => [X((x.da + x.a) / 2), Y(x.cop)]);
    s += `<path d="${P.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ')}" fill="none" stroke="${c}" stroke-width="2.2" stroke-linejoin="round"/>`;
    b.forEach((x, i) => { s += `<circle cx="${P[i][0].toFixed(1)}" cy="${P[i][1].toFixed(1)}" r="4" fill="${c}"><title>${x.da}…${x.a} °C esterni: COP ${num(x.cop, 2)} (${num(x.ore, 1)} h)</title></circle>`;
      if (b.length <= 12) s += `<text x="${P[i][0].toFixed(1)}" y="${(P[i][1] + (k === 'casa' ? -8 : 15)).toFixed(1)}" text-anchor="middle" class="vt">${num(x.cop, 1)}</text>`; });   // rosso: valore sopra il punto, blu: sotto
  });
  return s + curEf(T, ph, [COLS.casa, COLS.acs]) + '</svg>';
}

// ---------- Impostazioni ----------
function fa(t) { if (!t) return 'mai'; const s = ora() - t; return s < 90 ? 'adesso' : s < 5400 ? Math.round(s / 60) + ' min fa' : s < 172800 ? Math.round(s / 3600) + ' ore fa' : Math.round(s / 86400) + ' giorni fa'; }
// "da quanto" (senza "fa"): acceso da 2 minuti, da 3 ore, da 4 giorni
function daQuanto(t) {
  if (!t) return '—'; const s = Math.max(0, ora() - t), n = (v, u, p) => `${v} ${v === 1 ? u : p}`;
  if (s < 90) return 'meno di un minuto';
  if (s < 5400) return n(Math.round(s / 60), 'minuto', 'minuti');
  if (s < 172800) return n(Math.round(s / 3600), 'ora', 'ore');
  return n(Math.round(s / 86400), 'giorno', 'giorni');
}
const SEP = ' <span class="sep">·</span> ';
const pallino = (cls, t) => `<span class="pill ${cls}">${t}</span>`;

function statoImpostazioni(st) {
  const s = st.smartthings, n = st.netatmo, t = st.telegram, db = st.database;
  const acc = s.accesso.tipo === 'oauth' ? 'OAuth' + SEP + pallino('si', 'si rinnova da solo') + (s.accesso.scade ? `${SEP}<small class="mut">token valido fino alle ${hhmm(s.accesso.scade)}</small>` : '')
    : s.accesso.tipo === 'pat' ? 'Token personale' + SEP + pallino('no', s.accesso.eta_ore == null ? 'dura 24 ore' : 'attivo da ' + num(s.accesso.eta_ore, 0) + ' h su 24')
    : s.accesso.tipo === 'demo' ? 'Demo' : '—';
  const ok = (v, msg) => v ? pallino('si', 'OK') : pallino('bad', msg || 'Problema');
  return [
    riga('SmartThings · accesso', acc),
    riga('SmartThings · ultima lettura', s.messaggio ? pallino('bad', 'Problema') + `${SEP}<small class="mut">${esc(s.messaggio)}</small>` : `${ok(s.ultimo_ok)}${SEP}<small class="mut">${fa(s.ultimo_ok)}</small>`),
    riga('Netatmo · Smarther', !n.configurato ? pallino('no', 'non configurato') : n.messaggio ? pallino('bad', 'Problema') + `${SEP}<small class="mut">${esc(n.messaggio)}</small>` : `${ok(n.ultimo_ok)}${SEP}<small class="mut">${fa(n.ultimo_ok)}</small>`),
    riga('Telegram', !t.abilitato ? pallino('off', 'spento') : t.problema ? pallino('bad', 'da configurare') + `${SEP}<small class="mut">${esc(t.problema)}</small>` : t.messaggio ? pallino('bad', 'ultimo invio fallito') + `${SEP}<small class="mut">${esc(t.messaggio)}</small>` : pallino('si', 'attivo') + (t.ultimo_ok ? `${SEP}<small class="mut">ultimo messaggio ${fa(t.ultimo_ok)}</small>` : '')),
    riga('Database', `${num(db.righe, 0)} misure${db.mb != null ? ' · ' + num(db.mb, 1) + ' MB' : ''}${db.dal ? ' <small class="mut">dal ' + dm(db.dal).slice(0, 5) + '</small>' : ''}`),
    riga('Anomalie aperte', st.eventi_aperti ? pallino('bad', st.eventi_aperti) : pallino('si', 'nessuna')),
    riga('Versione', `${esc(st.versione)} <small class="mut">· Python ${esc(st.python)} · acceso da ${daQuanto(st.avviato)}</small>${SEP}<a class="lnk" href="#/changelog">Changelog</a>`),
  ].join('');
}

// ---------- Dispositivi ----------
const dataLunga = t => new Date(t * 1000).toLocaleString('it-IT', {day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'});
const logoImg = (d, nome, alt, cls = '') => {
  const u = d.loghi && d.loghi[nome];
  return u ? `<img class="dev-logo lg-${esc(nome)} ${cls} ${d.loghi_bianchi ? 'bianco' : ''}" src="${esc(u)}" alt="${esc(alt)}">` : '';
};
function logoTitolo(d) {
  const el = document.getElementById('c-et'); if (!el) return;
  const u = d.loghi && d.loghi.smarther, k = (u || '') + '|' + d.loghi_bianchi;
  if (el.dataset.k === k) return;
  el.dataset.k = k;
  el.innerHTML = u ? `Casa · <img class="logo-et ${d.loghi_bianchi ? 'bianco' : ''}" src="${esc(u)}" alt="Smarther">` : 'Casa · Smarther';
}
// dove nel testo c'e' la parola "Netatmo", se l'utente ha il suo logo lo mostro al posto della scritta
function conLogo(d, testo) {
  const u = d.loghi && d.loghi.netatmo, t = esc(testo);
  return u ? t.replace(/Netatmo/g, `<img class="nel-testo ${d.loghi_bianchi ? 'bianco' : ''}" src="${esc(u)}" alt="Netatmo">`) : t;
}
function rigaDisp(r, d) {
  let v;
  if (r.v == null) v = '<span class="mut">—</span>';
  else if (r.k === 'd') v = esc(dataLunga(r.v));
  else if (r.k === 'b') v = `<span class="pill ${esc(r.cls)}">${esc(r.v)}</span>`;
  else if (r.k === 'l') v = `<span class="marca-riga">${logoImg(d, r.logo, r.v, 'in-riga') || esc(r.v)}${r.nota ? `<span class="mut con">${conLogo(d, r.nota)}</span>` : ''}</span>`;
  else if (r.k === 'c') v = `<span class="cv"><span class="val-c">${esc(r.v)}</span><button type="button" class="copia" data-copia="${esc(r.v)}" title="Copia" aria-label="Copia ${esc(r.et)}">Copia</button></span>`;
  else v = esc(r.v);
  return `<div class="riga"><span>${esc(r.et).replace(/Wi-Fi/g, 'Wi\u2011Fi')}</span><b>${v}${r.nota && r.k !== 'l' ? `<small class="mut nota-r">${esc(r.nota)}</small>` : ''}</b></div>`;
}
function schedaDisp(s, d) {
  const marca = logoImg(d, s.loghi[0], s.marca) || `<span class="dev-marca">${esc(s.marca)}</span>`;
  return `<article class="card dev">
    <div class="dev-head">
      <div class="dev-id">${marca}<div><h2>${esc(s.titolo)}</h2><small class="mut">${conLogo(d, s.sotto)}</small></div></div>
      <div class="dev-st"><span class="pill ${esc(s.stato.cls)}">${esc(s.stato.testo.toUpperCase())}</span><small class="mut">ultimo dato ${s.ultimo_dato ? fa(s.ultimo_dato) : 'mai'}</small></div>
    </div>
    ${s.gruppi.map(g => `<section class="dev-gr"><h3 class="eti">${esc(g.titolo)}</h3>${g.righe.map(r => rigaDisp(r, d)).join('')}</section>`).join('')}
  </article>`;
}
const TAB_DISP = [['', 'Dispositivi'], ['installatore', 'Regolazioni']];
const tabsDisp = () => `<nav class="sub-tabs" aria-label="Sezioni">${TAB_DISP.map(([k, t]) => `<a href="#/dispositivi${k ? '/' + k : ''}" class="${S.sub === k || (k === '' && S.sub !== 'installatore') ? 'on' : ''}">${t}</a>`).join('')}</nav>`;
S.fsvAperti = S.fsvAperti || new Set(['20']);
document.addEventListener('toggle', e => {
  const g = e.target && e.target.dataset && e.target.dataset.fsv; if (!g) return;
  if (e.target.open) S.fsvAperti.add(g); else S.fsvAperti.delete(g);
}, true);
// ---- impostazioni installatore: lettura, cronologia e modifica (lucchetto di 30 s, poi matita, poi conferma) ----
// la matita ✎ sulla riga: attiva solo con le modifiche sbloccate e da casa
const bottoneMatita = (attr, sbl, occ, aria, titolo) => `<button type="button" class="matita" ${attr} ${sbl && !occ && !S.fuori ? '' : 'disabled'} aria-label="${aria}" title="${S.fuori ? 'Solo da casa' : sbl ? titolo : 'Prima sblocca le modifiche'}">✎</button>`;
S.sbloccoFsv = S.sbloccoFsv || 0; S.fsvMod = S.fsvMod || null; S.fsvVal = S.fsvVal || ''; S.fsvErr = S.fsvErr || ''; S.fsvOcc = false;
function armaSbloccoFsv() { clearTimeout(S.tLockFsv); S.sbloccoFsv = Date.now() + 30000; S.tLockFsv = setTimeout(() => { S.sbloccoFsv = 0; S.fsvMod = null; S.fsvErr = ''; S.regMod = null; S.regErr = ''; S.boostApri = false; S.boostErr = ''; ridisegnaInstallatore(); }, 30200); }
function ridisegnaInstallatore() { if (S.dati && S.pagina === 'dispositivi' && S.sub === 'installatore') disegnaDispositivi(S.dati); }
function trovaFsv(id) { const i = S.dati && S.dati.installatore; if (!i) return null; for (const g of i.gruppi) { const r = g.righe.find(x => x.id === id); if (r) return r; } return null; }
function editorFsv(r) {
  const campo = r.opzioni
    ? `<select data-fsv-val aria-label="Nuovo valore di ${esc(r.id)}">${r.opzioni.map(([k, t]) => `<option value="${k}" ${String(k) === String(S.fsvVal) ? 'selected' : ''}>${esc(t)} · ${k}</option>`).join('')}</select>`
    : `<input type="number" inputmode="numeric" step="1" min="${r.lo}" max="${r.hi}" data-fsv-val value="${esc(S.fsvVal)}" aria-label="Nuovo valore di ${esc(r.id)}"> <span class="mut">${esc(r.unita)}</span>`;
  return `<div class="fsv-edit"><div class="fe-t">Modifica ${esc(r.id)} · ${esc(r.nome)}</div>
    <div class="fe-r"><span class="mut">Ora</span><b>${esc(r.v)}</b></div>
    <div class="fe-r"><span class="mut">Nuovo valore</span><span>${campo}</span></div>
    ${r.nota ? `<small class="mut">${esc(r.nota)}</small>` : ''}
    ${r.limiti ? `<small class="mut">Limiti del manuale: ${esc(r.limiti)}</small>` : ''}
    <small class="mut">Il valore parte dal pannello alla pompa tramite SmartThings. Poi va riletto: finché la pompa non lo conferma, la riga dice «in attesa».</small>
    ${S.fsvErr ? `<div class="fe-err">${esc(S.fsvErr)}</div>` : ''}
    <div class="fe-b"><button type="button" class="sec piccolo" data-fsv-annulla ${S.fsvOcc ? 'disabled' : ''}>Annulla</button><button type="button" class="piccolo cambia" data-fsv-conf ${S.fsvOcc ? 'disabled' : ''}>${S.fsvOcc ? 'Invio…' : 'Cambia nella pompa'}</button></div></div>`;
}
function rigaFsv(r) {
  const sbl = Date.now() < S.sbloccoFsv;
  const fuori = r.fuori ? ' <span class="pill bad" title="Fuori dai limiti indicati dal manuale">fuori limiti</span>' : '';
  const note = [];
  if (r.limiti) note.push(esc(r.limiti));
  const cambio = r.cambio ? `<small class="mut nota-r">visto cambiato il ${esc(dataLunga(r.cambio.ts))} (prima: ${esc(r.cambio.prima)})</small>` : '';
  const attesa = r.attesa ? `<small class="nota-r in-attesa">in attesa: richiesto ${esc(r.attesa.dopo)} alle ${esc(hhmm(r.attesa.ts))}${r.attesa.vecchia ? ', la pompa non l’ha ancora confermato (controlla sul suo pannello)' : ''}</small>` : '';
  const matita = r.modificabile && !S.fuori ? bottoneMatita(`data-fsv-mod="${esc(r.id)}"`, sbl, S.fsvOcc, `Modifica ${esc(r.id)}`, 'Modifica questa impostazione') : '';
  const riga = `<div class="riga"><span><i class="fsv-id">${esc(r.id)}</i>${esc(r.nome)}</span><b><span class="val-c">${esc(r.v)}${fuori}</span>${note.length ? `<small class="mut nota-r nw">${note.join(' · ')}</small>` : ''}${cambio}${attesa}</b>${matita}</div>`;
  return riga + (sbl && S.fsvMod === r.id ? editorFsv(r) : '');
}
const PER_PAG_FSV = 6;
function cronologiaFsv(lista) {
  if (!lista || !lista.length) return `<article class="card dev"><div class="ch"><h2>Cronologia modifiche</h2></div><p class="nota-sez">Nessun cambio visto da quando il pannello osserva.</p></article>`;
  const pagine = Math.ceil(lista.length / PER_PAG_FSV);
  S.pagFsv = Math.min(Math.max(S.pagFsv || 0, 0), pagine - 1);
  const righe = lista.slice(S.pagFsv * PER_PAG_FSV, (S.pagFsv + 1) * PER_PAG_FSV).map(v => {
    const fonte = v.fonte === 'pannello'
      ? (v.stato === 'in attesa' ? `chiesto dal pannello, in attesa della pompa` : v.stato === 'non confermata' ? `chiesto dal pannello, la pompa non l’ha confermato` : v.stato ? 'chiesto dal pannello (già a quel valore)' : `cambiato dal pannello${v.ip ? ' (' + esc(v.ip) + ')' : ''}`)
      : 'letto dalla pompa';
    return `<div class="riga"><span>${esc(dataLunga(v.ts))}</span><b>${esc(v.id)} · ${esc(v.nome)}<small class="mut nota-r">${esc(v.prima)} → ${esc(v.dopo)}</small><small class="mut nota-r">${fonte}</small></b></div>`;
  }).join('');
  const nav = pagNav('pagFsv', pagine);
  return `<article class="card dev"><div class="ch"><h2>Cronologia modifiche</h2></div>
    <p class="nota-sez">I cambi che il pannello ha visto nelle impostazioni, con il valore di prima. La data è quella in cui il pannello ha visto il valore nuovo: SmartThings può essere in ritardo rispetto alla pompa.</p>${righe}${nav}</article>`;
}
function disegnaInstallatore(d) {
  S.fuori = d.da_casa === false;     // da fuori casa: FSV, temperatura/modo dell'acqua e boost restano bloccati
  const i = d.installatore, testa = cardRegolazioni(d);
  if (!i || !i.totale) return testa + `<article class="card dev"><div class="vuoto">La pompa non ha ancora comunicato impostazioni installatore. Appena SmartThings le manda, compaiono qui (il pannello le legge ogni ora circa).</div></article>`;
  const testo = i.gruppi.map(g => `${g.titolo}\n` + g.righe.map(r => `${r.id}  ${r.nome}: ${r.v}`).join('\n')).join('\n\n');
  const sommario = [`<b>${i.totale}</b> impostazioni`, i.cambiati ? `<b>${i.cambiati}</b> cambiate da quando il pannello osserva` : '', i.fuori ? `<b class="rosso">${i.fuori}</b> fuori limiti` : '', i.letto ? `lette ${esc(fa(i.letto))}` : ''].filter(Boolean).join(' · ');
  return testa + `<article class="card dev">
    <div class="ch"><h2>Impostazioni installatore (FSV)</h2><span class="ch-b"><button type="button" class="copia" data-copia="${esc(testo)}" title="Copia tutto l'elenco">Copia elenco</button></span></div>
    <p class="nota-sez">${sommario}</p>
    <p class="nota-sez">Sono i valori che la pompa comunica tramite SmartThings (i codici FSV del manuale). L'intervallo sotto ogni valore è quello del manuale Samsung EHS Mono e può variare da modello a modello. ${S.fuori ? '' : 'Per cambiarne uno: «Sblocca modifiche» in alto, la matita ✎ della riga, il nuovo valore e la conferma.'}</p>
  </article>` + i.gruppi.map(g => `<details class="card dev fsv-gr" data-fsv="${esc(g.id)}" ${S.fsvAperti.has(g.id) || (S.fsvMod && g.righe.some(r => r.id === S.fsvMod)) ? 'open' : ''}><summary><span class="t">${esc(g.titolo)}</span><span class="d">${g.righe.length} impostazioni</span></summary>${g.righe.map(rigaFsv).join('')}</details>`).join('') + cronologiaFsv(i.cronologia);
}
// pagine «‹ 1 di N ›» (cronologia FSV e registro comandi): chiave = campo di S che tiene la pagina corrente
const pagNav = (k, tot) => tot > 1 ? `<div class="pager"><button type="button" data-pag="${k}:-1" ${S[k] === 0 ? 'disabled' : ''} aria-label="Più recenti">‹</button><span>${S[k] + 1} di ${tot}</span><button type="button" data-pag="${k}:1" ${S[k] >= tot - 1 ? 'disabled' : ''} aria-label="Meno recenti">›</button></div>` : '';
document.addEventListener('click', e => {
  const b = e.target.closest && e.target.closest('[data-pag]'); if (!b || b.disabled) return;
  const [k, d] = b.dataset.pag.split(':'); S[k] = (S[k] || 0) + Number(d);
  disegna();
});
document.addEventListener('click', e => {
  const t = e.target;
  if (!t.closest) return;
  if (t.closest('#b-lock-fsv')) {
    clearTimeout(S.tLockFsv);
    if (Date.now() < S.sbloccoFsv) { S.sbloccoFsv = 0; S.fsvMod = null; S.fsvErr = ''; S.regMod = null; S.regErr = ''; } else armaSbloccoFsv();
    ridisegnaInstallatore(); return;
  }
  const m = t.closest('[data-fsv-mod]');
  if (m && !m.disabled) {
    const r = trovaFsv(m.dataset.fsvMod); if (!r) return;
    S.regMod = null; S.regErr = ''; S.fsvMod = r.id; S.fsvErr = ''; S.fsvVal = r.n == null ? '' : String(r.n);
    armaSbloccoFsv(); ridisegnaInstallatore(); return;
  }
  if (t.closest('[data-fsv-annulla]')) { S.fsvMod = null; S.fsvErr = ''; armaSbloccoFsv(); ridisegnaInstallatore(); return; }
  if (t.closest('[data-fsv-conf]')) confermaFsv();
});
async function confermaFsv() {
  const r = trovaFsv(S.fsvMod); if (!r || S.fsvOcc) return;
  const nuovo = Number(String(S.fsvVal).replace(',', '.'));
  const err = m => { S.fsvErr = m; armaSbloccoFsv(); ridisegnaInstallatore(); };
  if (S.fsvVal === '' || !Number.isFinite(nuovo)) return err('Scrivi o scegli un valore.');
  if (nuovo !== Math.round(nuovo)) return err('Queste impostazioni vanno a numeri interi.');
  if (nuovo === r.n) return err('È già a questo valore.');
  if (!r.opzioni && (nuovo < r.lo || nuovo > r.hi)) return err('Fuori dai limiti del manuale (' + r.limiti + ').');
  const opz = r.opzioni && r.opzioni.find(o => Number(o[0]) === nuovo);
  const descr = opz ? `${opz[1]} · ${nuovo}` : `${nuovo} ${r.unita}`.trim();
  armaSbloccoFsv();
  if (!window.confirm(`Stai per cambiare un'impostazione installatore della pompa di calore.\n\n${r.id} · ${r.nome}\nOra: ${r.v}\nNuovo: ${descr}\n\nUn valore sbagliato può far lavorare male la pompa. Confermi?`)) return;
  inviaOp('fsv', '/api/fsv', {codice: r.id, valore: nuovo}, `${r.id}: modifica inviata, aspetto che la pompa la confermi`, () => { S.fsvMod = null; }, 'Modifica non riuscita', 'la modifica è partita, controlla la cronologia');
}
// invio di un cambio alla pompa (FSV, regolazione, boost): k = prefisso dello stato in S (fsv/reg/boost: Occ = in corso, Err = messaggio)
async function inviaOp(k, url, corpo, okMsg, fine, erroreDef, partito) {
  S[k + 'Occ'] = true; S[k + 'Err'] = ''; ridisegnaInstallatore();
  try {
    const x = await inviaPost(url, corpo);
    if (x.ok) { toast(okMsg); fine(); carica(); }
    else { S[k + 'Err'] = x.errore || erroreDef; toast(erroreDef, true); }
  } catch { S[k + 'Err'] = `Il pannello non risponde: non so se ${partito}.`; toast('Il pannello non risponde', true); }
  S[k + 'Occ'] = false; armaSbloccoFsv(); ridisegnaInstallatore();
}
// ---- regolazioni: temperatura e modo dell'acqua calda, modo della pompa (stesso lucchetto degli FSV) ----
S.regMod = S.regMod || null; S.regVal = S.regVal || ''; S.regErr = S.regErr || ''; S.regOcc = false;
function trovaReg(cosa) { return S.dati && S.dati.regolazioni ? S.dati.regolazioni[cosa] : null; }
function editorReg(r) {
  const opz = r.opzioni && r.opzioni.find(o => String(o[0]) === String(S.regVal));
  const campo = r.opzioni
    ? `<select data-reg-val aria-label="Nuovo valore: ${esc(r.nome)}">${r.opzioni.map(o => `<option value="${esc(o[0])}" ${String(o[0]) === String(S.regVal) ? 'selected' : ''}>${esc(o[1])}</option>`).join('')}</select>`
    : `<input type="number" inputmode="numeric" step="1" min="${r.lo}" max="${r.hi}" data-reg-val value="${esc(S.regVal)}" aria-label="Nuovo valore: ${esc(r.nome)}"> <span class="mut">${esc(r.unita)}</span>`;
  return `<div class="fsv-edit"><div class="fe-t">Modifica · ${esc(r.nome)}</div>
    <div class="fe-r"><span class="mut">Ora</span><b>${esc(r.testo)}</b></div>
    <div class="fe-r"><span class="mut">Nuovo valore</span><span>${campo}</span></div>
    ${r.lo != null ? `<small class="mut">Limiti della pompa: da ${r.lo} a ${r.hi} ${esc(r.unita)}</small>` : ''}
    ${opz && opz[2] ? `<small class="avviso-reg">${esc(opz[2])}</small>` : ''}
    <small class="mut">Il comando parte dal pannello alla pompa tramite SmartThings. Poi va riletto: finché la pompa non lo conferma, la riga dice «in attesa».</small>
    ${S.regErr ? `<div class="fe-err">${esc(S.regErr)}</div>` : ''}
    <div class="fe-b"><button type="button" class="sec piccolo" data-reg-annulla ${S.regOcc ? 'disabled' : ''}>Annulla</button><button type="button" class="piccolo cambia" data-reg-conf ${S.regOcc ? 'disabled' : ''}>${S.regOcc ? 'Invio…' : 'Cambia nella pompa'}</button></div></div>`;
}
function rigaReg(r) {
  if (!r) return '';
  const sbl = Date.now() < S.sbloccoFsv;
  const attesa = r.attesa ? `<small class="nota-r in-attesa">in attesa: richiesto ${esc(r.attesa.dopo)} alle ${esc(hhmm(r.attesa.ts))}, la pompa non l’ha ancora confermato</small>` : '';
  const matita = r.modificabile && !S.fuori ? bottoneMatita(`data-reg-mod="${esc(r.cosa)}"`, sbl, S.regOcc, `Modifica: ${esc(r.nome)}`, 'Modifica') : '';
  return `<div class="riga"><span>${esc(r.nome)}</span><b><span class="val-c">${esc(r.testo)}</span>${attesa}</b>${matita}</div>` + (sbl && S.regMod === r.cosa ? editorReg(r) : '');
}
function cardRegolazioni(d) {
  const g = d.regolazioni; if (!g) return '';
  const sbl = Date.now() < S.sbloccoFsv;
  const lock = S.fuori ? '' : `<span class="ch-b"><button type="button" class="sec piccolo ${sbl ? 'sbloccato' : ''}" id="b-lock-fsv">${sbl ? 'Modifiche sbloccate · Blocca' : 'Sblocca modifiche'}</button></span>`;
  const testo = S.fuori ? 'Qui sotto i valori attuali della pompa.'
    : 'Per cambiare un valore (qui sotto o negli FSV): «Sblocca modifiche» (restano sbloccate 30 secondi dall’ultima azione), la matita ✎, il nuovo valore e la conferma. La pompa deve poi comunicare il valore nuovo: SmartThings può metterci un po’, e finché non lo fa la riga dice «in attesa».';
  const avviso = S.fuori ? '<div class="avviso warn"><span>⚠️</span><div>Sei collegato da fuori casa: le modifiche (FSV, acqua calda, Boost, modo della pompa) si fanno solo dalla rete di casa.</div></div>' : '';
  return `<article class="card dev"><div class="ch"><h2>Regolazioni</h2>${lock}</div>
    <p class="nota-sez">${testo}</p></article>${avviso}
    <article class="card dev"><div class="ch"><h2>Acqua calda</h2></div>${rigaReg(g.acs_setpoint)}${rigaReg(g.acs_modo)}${rigaBoost(d.boost)}</article>
    <article class="card dev"><div class="ch"><h2>Pompa</h2></div>${rigaReg(g.pompa_modo)}<p class="nota-sez">Il modo «Riscaldamento» è quello giusto d’inverno; «Raffrescamento» serve d’estate.</p></article>`;
}
async function confermaReg() {
  const r = trovaReg(S.regMod); if (!r || S.regOcc) return;
  const err = m => { S.regErr = m; armaSbloccoFsv(); ridisegnaInstallatore(); };
  let valore = S.regVal, descr, avviso = '';
  if (r.opzioni) {
    const o = r.opzioni.find(x => String(x[0]) === String(valore)); if (!o) return err('Scegli un valore.');
    descr = o[1]; avviso = o[2] || '';
    if (String(o[0]) === String(r.valore)) return err('È già a questo valore.');
  } else {
    const n = Number(String(valore).replace(',', '.'));
    if (valore === '' || !Number.isFinite(n)) return err('Scrivi un valore.');
    if (n !== Math.round(n)) return err('La temperatura va a gradi interi.');
    if (n < r.lo || n > r.hi) return err(`Fuori dai limiti della pompa (da ${r.lo} a ${r.hi} ${r.unita}).`);
    if (n === Number(r.valore)) return err('È già a questo valore.');
    valore = n; descr = `${n} ${r.unita}`;
  }
  armaSbloccoFsv();
  if (!window.confirm(`Stai per cambiare una regolazione della pompa di calore.\n\n${r.nome}\nOra: ${r.testo}\nNuovo: ${descr}\n${avviso ? '\n' + avviso + '\n' : ''}\nConfermi?`)) return;
  inviaOp('reg', '/api/regolazioni', {cosa: r.cosa, valore}, `${r.nome}: comando inviato, aspetto che la pompa lo confermi`, () => { S.regMod = null; }, 'Comando non riuscito', 'il comando è partito, controlla più tardi');
}
document.addEventListener('click', e => {
  const t = e.target; if (!t.closest) return;
  const m = t.closest('[data-reg-mod]');
  if (m && !m.disabled) {
    const r = trovaReg(m.dataset.regMod); if (!r) return;
    S.fsvMod = null; S.fsvErr = ''; S.boostApri = false; S.regMod = r.cosa; S.regErr = '';
    S.regVal = r.opzioni ? String(r.valore) : (r.valore == null ? '' : String(r.valore));
    armaSbloccoFsv(); ridisegnaInstallatore(); return;
  }
  if (t.closest('[data-reg-annulla]')) { S.regMod = null; S.regErr = ''; armaSbloccoFsv(); ridisegnaInstallatore(); return; }
  if (t.closest('[data-reg-conf]')) confermaReg();
});
// il campo del valore nuovo (FSV, regolazione, boost): si tiene nello stato S mentre lo scrivi
for (const ev of ['input', 'change']) document.addEventListener(ev, e => {
  const t = e.target, k = t.matches && ['fsv', 'reg', 'boost'].find(x => t.matches(`[data-${x}-val]`)); if (!k) return;
  S[k + 'Val'] = t.value; S[k + 'Err'] = ''; armaSbloccoFsv();
  if (k === 'reg' && ev === 'change' && t.tagName === 'SELECT') ridisegnaInstallatore();
});
// ---- Boost acqua calda (come Dwellsee: setpoint più alto + modo «forzato»; a fine boost si rimette tutto com'era) ----
S.boostApri = false; S.boostVal = ''; S.boostErr = ''; S.boostOcc = false;
function rigaBoost(b) {
  if (!b) return '';
  const sbl = Date.now() < S.sbloccoFsv;
  const fermaB = S.fuori ? '' : `<button type="button" class="matita" data-boost-ferma ${sbl && !S.boostOcc ? '' : 'disabled'} title="${sbl ? 'Ferma il boost' : 'Prima sblocca le modifiche'}" aria-label="Ferma il boost">■</button>`;
  const avviaB = S.fuori ? '' : `<button type="button" class="matita" data-boost-apri ${sbl && !S.boostOcc ? '' : 'disabled'} title="${sbl ? 'Avvia un boost' : 'Prima sblocca le modifiche'}" aria-label="Avvia un boost">⚡</button>`;
  if (b.attivo) {
    const t = b.temperatura != null ? ` · ora ${(Math.round(b.temperatura * 10) / 10).toString().replace('.', ',')} °C` : '';
    const err = b.errore ? `<small class="nota-r in-attesa">${esc(b.errore)}</small>` : '';
    return `<div class="riga"><span>Boost</span><b><span class="val-c">attivo · obiettivo ${b.obiettivo} °C${t}</span><small class="nota-r">finisce quando l’acqua arriva all’obiettivo, al più tardi alle ${esc(hhmm(b.scade))}; poi torna a ${esc(String(b.sp_prima))} °C e al modo di prima</small>${err}</b>${fermaB}</div>${S.boostErr ? `<div class="fe-err">${esc(S.boostErr)}</div>` : ''}`;
  }
  const riga = `<div class="riga"><span>Boost</span><b><span class="val-c">spento</span></b>${avviaB}</div>`;
  if (!(sbl && S.boostApri)) return riga;
  return riga + `<div class="fsv-edit"><div class="fe-t">Boost acqua calda</div>
    <div class="fe-r"><span class="mut">Temperatura obiettivo</span><span><input type="number" inputmode="numeric" step="1" data-boost-val value="${esc(S.boostVal)}" aria-label="Temperatura obiettivo del boost"> <span class="mut">°C</span></span></div>
    <small class="mut">Come Dwellsee: il pannello alza il setpoint a questo valore e mette il modo «Forzato». Finito il boost (acqua arrivata all’obiettivo, o dopo ${b.massimo_min} minuti al massimo) rimette il modo e il setpoint di adesso. Il ripristino riparte anche se il pannello si riavvia.</small>
    ${S.boostErr ? `<div class="fe-err">${esc(S.boostErr)}</div>` : ''}
    <div class="fe-b"><button type="button" class="sec piccolo" data-boost-annulla ${S.boostOcc ? 'disabled' : ''}>Annulla</button><button type="button" class="piccolo cambia" data-boost-conf ${S.boostOcc ? 'disabled' : ''}>${S.boostOcc ? 'Invio…' : 'Avvia il boost'}</button></div></div>`;
}
const inviaBoost = (corpo, okMsg) => inviaOp('boost', '/api/boost', corpo, okMsg, () => { S.boostApri = false; }, 'Comando non riuscito', 'il comando è partito, controlla più tardi');
document.addEventListener('click', e => {
  const t = e.target; if (!t.closest) return;
  const b = S.dati && S.dati.boost;
  if (t.closest('[data-boost-apri]')) {
    S.fsvMod = null; S.fsvErr = ''; S.regMod = null; S.regErr = ''; S.boostApri = true; S.boostErr = '';
    S.boostVal = String(b && b.predefinito != null ? b.predefinito : 50); armaSbloccoFsv(); ridisegnaInstallatore(); return;
  }
  if (t.closest('[data-boost-annulla]')) { S.boostApri = false; S.boostErr = ''; armaSbloccoFsv(); ridisegnaInstallatore(); return; }
  if (t.closest('[data-boost-conf]')) {
    const n = Number(String(S.boostVal).replace(',', '.'));
    if (S.boostVal === '' || !Number.isFinite(n) || n !== Math.round(n)) { S.boostErr = 'Scrivi una temperatura a gradi interi.'; armaSbloccoFsv(); ridisegnaInstallatore(); return; }
    armaSbloccoFsv();
    if (!window.confirm(`Stai per avviare un BOOST dell’acqua calda.\n\nObiettivo: ${n} °C\nModo: Forzato (poi torna a quello di prima)\n\nConfermi?`)) return;
    inviaBoost({azione: 'avvia', obiettivo: n}, 'Boost avviato: la pompa lo conferma tra poco');
    return;
  }
  if (t.closest('[data-boost-ferma]')) {
    armaSbloccoFsv();
    if (!window.confirm('Fermo il boost e rimetto modo e setpoint di prima?')) return;
    inviaBoost({azione: 'ferma'}, 'Boost fermato: rimetto le impostazioni di prima');
  }
});
function disegnaDispositivi(d) {
  const box = $('#dsp'); if (!box) return;
  if (S.sub === 'installatore') {
    if ((S.fsvMod || S.regMod || S.boostApri) && box.contains(document.activeElement) && document.activeElement.matches && document.activeElement.matches('[data-fsv-val],[data-reg-val],[data-boost-val]')) return;  // si sta scrivendo un valore
    const h2 = tabsDisp() + disegnaInstallatore(d);
    if (box.dataset.h !== h2) { box.dataset.h = h2; box.innerHTML = h2; }
    return;
  }
  const sto = d.storico, ini = sto.iniziale.length ? Math.min(...sto.iniziale.map(e => e.ts)) : null;
  const righe = sto.cambi.map(e => `<div class="riga"><span>${esc(dataLunga(e.ts))}</span><b>${esc(e.dispositivo)} · ${esc(e.cosa)}<small class="mut nota-r">${esc(e.prima)} → ${esc(e.dopo)}</small></b></div>`).join('')
    || `<div class="vuoto">Nessun cambio di versione da quando il pannello osserva${ini ? ' (dal ' + esc(dataLunga(ini)) + ')' : ''}.</div>`;
  const inizio = sto.iniziale.length ? `<details class="iniz"><summary>Versioni viste alla prima lettura (${esc(dataLunga(ini))})</summary>${sto.iniziale.map(e => `<div class="riga"><span>${esc(e.dispositivo)} · ${esc(e.cosa)}</span><b>${esc(e.valore)}</b></div>`).join('')}</details>` : '';
  const h = tabsDisp() + `<div class="dev-griglia">${d.schede.map(s => schedaDisp(s, d)).join('')}</div>
    ${cardComandi(d.comandi)}
    <article class="card dev"><div class="ch"><h2>Storico delle versioni</h2></div>
    <p class="nota-sez">Il pannello segna ogni volta che cambia una versione (per esempio quando SmartThings aggiorna la pompa). Le date partono da quando il pannello ha iniziato a guardare.</p>${righe}${inizio}</article>`;
  if (box.dataset.h !== h) { box.dataset.h = h; box.innerHTML = h; }
}
const PER_PAG = 5;
function cardComandi(lista) {
  if (!lista || !lista.length) return '';
  const pagine = Math.ceil(lista.length / PER_PAG);
  S.pagCmd = Math.min(Math.max(S.pagCmd || 0, 0), pagine - 1);
  const righe = lista.slice(S.pagCmd * PER_PAG, (S.pagCmd + 1) * PER_PAG).map(c => `<div class="riga"><span>${esc(dataLunga(c.ts))}</span><b>${esc(c.cosa)} · ${c.testo ? esc(c.testo) : c.acceso ? 'acceso' : 'spento'}<small class="mut nota-r">da ${esc(c.ip)} · ${esc(c.esito)}</small></b></div>`).join('');
  const nav = pagNav('pagCmd', pagine);
  return `<article class="card dev"><div class="ch"><h2>Comandi dati dal pannello</h2></div>${righe}${nav}</article>`;
}
async function copiaTesto(t) {
  try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(t); return true; } } catch { /* si prova l'altro metodo */ }
  try {
    const a = document.createElement('textarea'); a.value = t; a.setAttribute('readonly', '');
    a.style.cssText = 'position:fixed;top:0;left:0;opacity:0;font-size:16px'; document.body.appendChild(a);
    a.select(); a.setSelectionRange(0, t.length); const ok = document.execCommand('copy'); a.remove(); return ok;
  } catch { return false; }
}
document.addEventListener('click', async e => {
  const b = e.target.closest && e.target.closest('.copia'); if (!b) return;
  const ok = await copiaTesto(b.dataset.copia); toast(ok ? 'Copiato' : 'Non riesco a copiare', !ok);
});

function campoHtml(c) {
  const id = 'f-' + c.chiave.replace(/\./g, '_');
  const h = c.aiuto ? `<small class="aiuto">${esc(c.aiuto)}</small>` : '';
  let ctl;
  if (c.tipo === 'bool') ctl = `<label class="sw"><input type="checkbox" id="${id}" data-k="${c.chiave}" ${c.valore ? 'checked' : ''}><span></span></label>`;
  else if (c.tipo === 'scelta') ctl = `<select id="${id}" data-k="${c.chiave}">${c.opzioni.map(([v, t]) => `<option value="${esc(v)}" ${v === c.valore ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>`;
  else if (c.tipo === 'segreto') ctl = `<input type="password" id="${id}" data-k="${c.chiave}" autocomplete="new-password" placeholder="${c.impostato ? '•••••• impostato' : 'non impostato'}">`;
  else if (c.tipo === 'lungo') ctl = `<textarea id="${id}" data-k="${c.chiave}" rows="${Math.min(7, Math.max(2, String(c.valore).split('\n').length + 1))}" spellcheck="false">${esc(c.valore)}</textarea>`;
  else if (c.tipo === 'int' || c.tipo === 'float') ctl = `<input type="text" inputmode="decimal" id="${id}" data-k="${c.chiave}" value="${esc(String(c.valore).replace('.', ','))}">`;
  else ctl = `<input type="${c.tipo === 'ora' ? 'time' : 'text'}" id="${id}" data-k="${c.chiave}" value="${esc(c.valore)}" class="${/_id$/.test(c.chiave) ? 'mono' : ''}" autocapitalize="none" autocorrect="off" spellcheck="false">`;
  const corto = ['bool', 'scelta', 'int', 'float', 'ora'].includes(c.tipo);
  return `<div class="campo ${corto ? 'in' : 'lg'}"><label for="${id}">${esc(c.et)}</label>${ctl}${h}<small class="err" data-e="${c.chiave}"></small></div>`;
}

// Riquadri collassabili: lo stato aperto/chiuso si ricorda per ogni riquadro (per il primo di ogni tab, aperto di partenza)
function impAperti() { try { return JSON.parse(localStorage.getItem('pdc_imp_aperti2') || '{}') || {}; } catch { return {}; } }
function impRicorda(id, aperto) { try { const m = impAperti(); m[id] = aperto; localStorage.setItem('pdc_imp_aperti2', JSON.stringify(m)); } catch { /* niente */ } }
function riquadro(id, titolo, corpo, primo, extra = '') {
  const m = impAperti(), ap = id in m ? m[id] : false;   // chiusi di partenza; poi si ricorda cosa apri
  return `<details class="card imp riq" data-id="${id}" ${ap ? 'open' : ''}><summary><h2>${esc(titolo)}</h2>${extra}</summary><div class="riq-c">${corpo}</div></details>`;
}
document.addEventListener('toggle', e => { const t = e.target; if (t.classList && t.classList.contains('riq') && t.dataset.id) { const m = impAperti(); if (t.open !== (t.dataset.id in m ? m[t.dataset.id] : false)) impRicorda(t.dataset.id, t.open); } }, true);
// mostra un campo (per esempio con un errore): apre i riquadri che lo contengono e passa al tab giusto
function mostraCampo(el) {
  let n = el; while ((n = n.closest && n.closest('details'))) { n.open = true; n = n.parentElement; }
  const g = el.closest('.imp-tab'); if (g && g.hidden) location.hash = '#/impostazioni' + (g.dataset.t === 'stato' ? '' : '/' + g.dataset.t);
}
// Mette i riquadri di ogni scheda in due colonne indipendenti, a turno da sinistra e nell'ordine di partenza: aprire o chiudere un riquadro
// normale non sposta mai gli altri. I riquadri "larghi" (testi, avvisi, Cloudflare) stanno in colonna da chiusi; da aperti passano a tutta riga.
// Su telefono è una colonna sola, nell'ordine di prima.
function riquadroLargo(f) { return !!(f.querySelector('textarea') || f.dataset.id === 'allarmi' || f.id === 'cf-d'); }
function disponiColonne() {
  document.querySelectorAll('#imp .imp-tab:not([data-t=stato])').forEach(tab => {
    tab.querySelectorAll(':scope > details.riq, :scope .imp-col > details.riq').forEach((f, n) => { if (f.dataset.ix === undefined) f.dataset.ix = n; });
    const tutti = [...tab.querySelectorAll('details.riq')].sort((a, b) => a.dataset.ix - b.dataset.ix);
    const altri = [...tab.children].filter(c => !c.matches('details.riq') && !c.classList.contains('imp-cols'));
    tab.querySelectorAll(':scope > .imp-cols').forEach(c => c.remove());
    let gruppo = [], ord = 0;
    const fine = () => {
      if (!gruppo.length) return;
      const box = document.createElement('div'); box.className = 'imp-cols';
      const cols = [document.createElement('div'), document.createElement('div')];
      cols.forEach(c => { c.className = 'imp-col'; box.appendChild(c); });
      gruppo.forEach((g, i) => { g.style.order = ord++; cols[i % 2].appendChild(g); });
      tab.appendChild(box); gruppo = [];
    };
    tutti.forEach(f => {
      f.classList.remove('largo');
      if (riquadroLargo(f) && f.open) { fine(); f.classList.add('largo'); f.style.order = ''; tab.appendChild(f); }
      else gruppo.push(f);
    });
    fine();
    altri.forEach(a => tab.appendChild(a));
    tab.querySelectorAll(':scope > .cima').forEach(a => tab.prepend(a));
  });
}
document.addEventListener('toggle', e => { const t = e.target; if (t.classList && t.classList.contains('riq') && riquadroLargo(t) && t.closest('#imp')) disponiColonne(); }, true);
// ---------- Impostazioni > Integrazioni (plugin) ----------
const PLG_STATO = {attivo: ['si', 'attivo'], spento: ['no', 'spento'], errore: ['bad', 'errore']};
function pluginRighe(p) {
  const base = p.base || location.origin;      // l'indirizzo da fuori casa, se il tunnel c'è
  const rotte = p.rotte.map(r => `<span class="cv"><span class="val-c">${esc(base + r)}</span><button type="button" class="copia" data-copia="${esc(base + r)}" aria-label="Copia l'indirizzo">Copia</button></span>`).join('<br>');
  return [
    p.messaggio ? riga('Stato', esc(p.messaggio)) : '',
    p.errore ? riga('Problema', `<small class="msg bad">${esc(p.errore)}</small>`) : '',
    riga('File', `<span>${esc(p.file)}${SEP}<small class="mut">${p.versione ? 'versione ' + esc(p.versione) + SEP : ''}${esc(p.sha)}</small></span>`),
    p.rotte.length ? riga('Indirizzi pubblici', rotte) : '',
    p.rotte.length ? riga('Richieste', `<span>${num(p.richieste, 0)}${SEP}<small class="mut">ultima ${fa(p.ultima)}</small></span>`) : '',
  ].join('');
}
function pluginPillola(p) { const [cl, tx] = PLG_STATO[p.stato] || ['no', p.stato]; return pallino(cl, tx); }
function pluginRiquadro(s) {
  const p = s.plugin;
  return riquadro('s-' + s.id, s.titolo, `${s.nota ? `<p class="nota-sez">${esc(s.nota)}</p>` : ''}<div class="righe plg">${pluginRighe(p)}</div>
      ${s.campi.length ? `<div class="campi">${s.campi.map(campoHtml).join('')}</div>` : ''}`, false, pluginPillola(p));
}
// Stato e contatore dei plugin si aggiornano da soli (ogni 5 s, solo se guardi la scheda Integrazioni): si rifanno le righe e il badge,
// mai i campi, cosi' quello che stai scrivendo e i riquadri aperti restano com'erano.
let plgOccupato = false;
async function plgAggiorna() {
  if (plgOccupato || document.hidden || S.pagina !== 'impostazioni' || S.sub !== 'integrazioni') return;
  plgOccupato = true;
  try {
    const r = await leggiJson('/api/impostazioni');
    if (!r || !r.ok || S.sub !== 'integrazioni') return;
    const d = await r.json();
    d.sezioni.filter(x => x.tab === 'integrazioni' && x.plugin).forEach(x => {
      const det = document.querySelector(`#imp details.riq[data-id="s-${x.id}"]`); if (!det) return;
      const righe = det.querySelector('.righe.plg'), pill = det.querySelector('summary .pill'), sel = window.getSelection && window.getSelection();
      if (righe && !(sel && !sel.isCollapsed && righe.contains(sel.anchorNode))) {      // non toccare un testo che stai selezionando
        const nuovo = pluginRighe(x.plugin);
        if (righe.innerHTML !== nuovo) righe.innerHTML = nuovo;
      }
      if (pill) { const n = pluginPillola(x.plugin); if (pill.outerHTML !== n) pill.outerHTML = n; }
    });
  } catch { /* al prossimo giro */ } finally { plgOccupato = false; }
}
setInterval(plgAggiorna, 5000);
function pluginTab(d) {
  const lista = d.sezioni.filter(s => s.tab === 'integrazioni');
  return `<article class="card cima"><div class="ch"><h2>Integrazioni</h2><button type="button" class="sec piccolo" id="plg-ricarica">Ricarica i plugin</button></div>
      <p class="nota-sez">Le integrazioni sono file nella cartella <code>plugin</code>, accanto a <code>pdc_pannello.py</code>: si aggiungono e si aggiornano senza toccare il pannello.
      Un plugin nuovo parte spento; un errore dentro un plugin non ferma mai il pannello. Dopo aver cambiato un file premi «Ricarica i plugin».${lista.length ? '' : '<br><b>Nessun plugin trovato.</b>'}</p></article>${lista.map(pluginRiquadro).join('')}`;
}
function disegnaImpostazioni(d) {
  const box = $('#imp'); if (!box) return;
  const stato_ = $('#imp-stato'); if (stato_) stato_.innerHTML = statoImpostazioni(d.stato);
  { const z = $('#stz-st'); if (z) z.textContent = d.stato.stazione || ''; }                 // riga di stato della stazione meteo vicina
  if (box.dataset.pronto && S.sporco) return;            // non cancellare quello che stai scrivendo
  if (box.dataset.pronto === '1' && !S.ricarica) return;
  S.ricarica = false; box.dataset.pronto = '1';
  const sez = t => d.sezioni.filter(s => s.tab === t).sort((a, b) => (a.id === 'testi') - (b.id === 'testi')).map((s, i) => riquadro('s-' + s.id, s.titolo, `
      ${s.nota ? `<p class="nota-sez">${esc(s.nota)}</p>` : ''}
      <div class="campi">${s.campi.map(campoHtml).join('')}</div>
      ${s.id === 'stazione' ? `<p class="nota-sez" id="stz-st">${esc(d.stato.stazione || '')}</p>` : ''}
      ${s.prova ? '<div class="azioni"><button type="button" class="sec" id="b-prova">Invia messaggio di prova</button><small class="mut">Usa i valori già salvati: salva prima.</small></div>' : ''}`, i === 0)).join('');
  const all = d.allarmi.map(a => `
    <details class="allarme"><summary><span class="t">${esc(a.titolo)}</span><span class="d">${esc(a.descrizione)}</span></summary>
      <div class="campi">${a.campi.map(campoHtml).join('')}</div></details>`).join('');
  const pw = `<div class="campi">
      <div class="campo lg"><label for="p-ut">Utente</label><input id="p-ut" type="text" autocomplete="username" value="${esc(d.utente || '')}" autocapitalize="none"></div>
      <div class="campo lg"><label for="p-at">Password attuale</label><input id="p-at" type="password" autocomplete="current-password"></div>
      <div class="campo lg"><label for="p-nu">Nuova password (almeno 10 caratteri)</label><input id="p-nu" type="password" autocomplete="new-password"></div></div>
      <div class="azioni"><button type="button" class="sec" id="b-pw">Cambia password</button><small class="msg" id="pw-msg"></small></div>`;
  const m = impAperti(), cfAp = 'cf' in m ? m.cf : false;
  box.innerHTML = `
    <div class="imp-tab" data-t="stato">
      <article class="card" id="agg"><div class="ch"><h2>Aggiornamento</h2><button type="button" class="sec piccolo" id="agg-ricontrolla">Controlla</button></div><div id="agg-corpo" class="agg-corpo">Controllo…</div></article>
      <article class="card"><div class="ch"><h2>Stato</h2><button type="button" class="sec piccolo" id="b-agg">Aggiorna</button></div><div class="righe" id="imp-stato">${statoImpostazioni(d.stato)}</div></article>
    </div>
    <div class="imp-tab" data-t="collegamenti" hidden>${sez('collegamenti')}</div>
    <div class="imp-tab" data-t="avvisi" hidden>${sez('avvisi')}
      ${riquadro('allarmi', 'Quando avvisarti', `<p class="nota-sez">Tocca un avviso per cambiarne le soglie o spegnerlo.</p><div class="lista-allarmi">${all}</div>`, false)}</div>
    <div class="imp-tab" data-t="integrazioni" hidden>${pluginTab(d)}</div>
    <div class="imp-tab" data-t="accesso" hidden>
      ${riquadro('password', 'Password del pannello', pw, true)}
      <details class="card imp riq" data-id="cf" id="cf-d" ${cfAp ? 'open' : ''}><summary><h2>Accesso da internet (Cloudflare)</h2></summary><div class="riq-c" id="cf"><div class="vuoto" style="padding:14px 0">Controllo l'accesso da internet…</div></div></details></div>
    <div class="imp-tab" data-t="dati" hidden>${sez('dati')}
      ${riquadro('reset', 'Azzera il database', `<p class="nota-sez">Cancella <b>tutti i dati raccolti</b> (storico, grafici, anomalie) e riparte da zero. Restano le impostazioni, la password e i collegamenti.<br>Prima di cancellare il pannello fa una <b>copia di sicurezza</b> nella cartella dei backup (<code>prima-del-reset-…db</code>); se la copia non riesce, non cancella niente. Si rimette con <code>python pdc_pannello.py ripristina</code>.</p>
        <div class="azioni"><button type="button" class="sec pericolo" id="rst-apri">Azzera il database…</button></div>
        <div id="rst-form" hidden><div class="campi">
          <div class="campo lg"><label for="rst-parola">Per confermare scrivi RESETTA</label><input id="rst-parola" type="text" autocomplete="off" autocapitalize="characters"></div>
          <div class="campo lg"><label for="rst-pw">La tua password</label><input id="rst-pw" type="password" autocomplete="current-password"></div></div>
          <div class="azioni"><button type="button" class="pericolo" id="rst-vai">Sì, cancella tutto</button><button type="button" class="sec" id="rst-annulla">Annulla</button><small class="msg" id="rst-msg"></small></div></div>`, false)}</div>
    <div class="barra-salva" id="barra" hidden><span id="barra-t">Modifiche non salvate</span><button type="button" class="sec" id="b-annulla">Annulla</button><button type="button" id="b-salva">Salva</button></div>`;
  S.mod = {}; S.sporco = false;
  disponiColonne();
  vistaImp();
  aggCarica();
  cfCarica(true);
  clearInterval(S.cfTimer); S.cfTimer = setInterval(() => { if (S.pagina === 'impostazioni') cfCarica(false); else clearInterval(S.cfTimer); }, 5000);
}

function segnaModifica(e) {
  const k = e.target.dataset && e.target.dataset.k; if (!k) return;
  S.mod[k] = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
  S.sporco = true; $('#barra').hidden = false;
  const er = document.querySelector(`[data-e="${k}"]`); if (er) er.textContent = '';
}
function toast(t, cattivo) {
  let el = $('#toast'); if (!el) { el = document.createElement('div'); el.id = 'toast'; document.body.appendChild(el); }
  el.textContent = t; el.className = 'on' + (cattivo ? ' bad' : ''); clearTimeout(toast.t); toast.t = setTimeout(() => el.className = '', cattivo ? 12000 : 4200);
}
async function leggiJson(url) {         // GET con sessione; se e' scaduta torna al login e ridà null
  const r = await fetch(url, {credentials: 'same-origin', cache: 'no-store'});
  if (r.status === 401) { location.href = '/login'; return null; }
  return r;
}
async function inviaPost(url, corpo) {
  const r = await fetch(url, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-Pdc': '1'}, body: JSON.stringify(corpo)});
  if (r.status === 401) { location.href = '/login'; return {}; }
  return r.json();
}
// ---------- Aggiornamento a caldo ----------
async function aggCarica() {
  const box = $('#agg-corpo'); if (!box || box.dataset.occupato) return;
  try {
    const r = await leggiJson('/api/aggiornamento'); if (!r) return;
    const a = await r.json(), cosa = (a.parti || []).map(p => esc(p.titolo)).join(', ');
    box.innerHTML = a.stato === 'applicabile'
      ? `<p class="agg-ok"><b>È pronta la versione ${esc(a.versione_disco)}</b> (cambiato: ${cosa}).<br>Si applica subito, <b>senza fermare la raccolta dei dati</b>.</p><div class="azioni"><button type="button" id="agg-applica">Applica aggiornamento</button></div>`
      : a.stato === 'riavvio'
        ? `<p class="agg-riavvio"><b>Per la versione ${esc(a.versione_disco)} serve il riavvio completo</b>${cosa ? ' (cambiato: ' + cosa + ')' : ''}.<br>La raccolta dei dati continua finché non chiudi e rilanci il programma Python.${a.messaggio ? '<br>' + esc(a.messaggio) : ''}</p>`
        : a.stato === 'errore' ? `<p class="agg-riavvio">${esc(a.messaggio || 'Non riesco a controllare.')}</p>`
        : `<p class="agg-ok">Il pannello è aggiornato (versione ${esc(a.versione)}). Quando sostituisci il file, qui compare cosa è cambiato e se si può applicare a caldo.</p>`;
  } catch { box.textContent = 'Non riesco a controllare gli aggiornamenti.'; }
}
setInterval(() => { if (S.pagina === 'impostazioni' && !S.sub && !document.hidden) aggCarica(); }, 20000);
// bottone disattivato mentre lavora; se il pannello non risponde lo dice
async function conBottone(b, f) { b.disabled = true; try { await f(); } catch { toast('Server non raggiungibile', true); } b.disabled = false; }
async function azioniImpostazioni(e) {
  const id = e.target.id;
  if (id === 'agg-ricontrolla') { aggCarica(); return; }
  if (id === 'agg-applica') {
    const box = $('#agg-corpo'); e.target.disabled = true; box.dataset.occupato = '1';
    try {
      const r = await inviaPost('/api/aggiornamento/applica', {});
      if (r.ok) { toast(r.messaggio || 'Aggiornato'); setTimeout(() => location.reload(), 900); return; }
      toast(r.messaggio || 'Non è stato possibile aggiornare', true);
    } catch { toast('Il pannello non risponde: riprova fra qualche secondo', true); }
    delete box.dataset.occupato; aggCarica(); return;
  }
  if (id === 'rst-apri' || id === 'rst-annulla') {
    const f = $('#rst-form'); if (f) { f.hidden = id === 'rst-annulla'; if (id === 'rst-annulla') { $('#rst-parola').value = ''; $('#rst-pw').value = ''; $('#rst-msg').textContent = ''; } }
    return;
  }
  if (id === 'rst-vai') {
    const msg = $('#rst-msg'); e.target.disabled = true; msg.textContent = '';
    try {
      const r = await inviaPost('/api/database/reset', {conferma: $('#rst-parola').value, password: $('#rst-pw').value});
      if (r.ok) { toast(r.messaggio || 'Fatto'); setTimeout(() => location.reload(), 1500); return; }
      msg.textContent = r.messaggio || 'Non è stato possibile.'; toast(msg.textContent, true);
    } catch { toast('Il pannello non risponde: riprova fra qualche secondo', true); }
    e.target.disabled = false; return;
  }
  if (id === 'b-agg') { S.ricarica = false; carica(); toast('Stato aggiornato'); }
  else if (id === 'b-annulla') { S.sporco = false; S.ricarica = true; disegnaImpostazioni(S.dati); }
  else if (id === 'b-salva') {
    e.target.disabled = true;
    try {
      const r = await inviaPost('/api/impostazioni/salva', {modifiche: S.mod});
      document.querySelectorAll('.err').forEach(x => x.textContent = '');
      if (r.ok) { toast(r.riavvio ? 'Salvato. Gli intervalli nuovi valgono dal prossimo riavvio.' : 'Salvato!'); S.sporco = false; S.ricarica = true; await carica(); }
      else if (r.errori) {
        Object.entries(r.errori).forEach(([k, m]) => { const el = document.querySelector(`[data-e="${k}"]`); if (el) { el.textContent = m; mostraCampo(el); } });
        toast('Controlla i campi in rosso', true);
      } else toast(r.errore || 'Non salvato', true);
    } catch { toast('Server non raggiungibile', true); }
    e.target.disabled = false;
  } else if (id === 'b-prova') {
    await conBottone(e.target, async () => { const r = await inviaPost('/api/impostazioni/telegram-prova', {}); toast(r.messaggio || 'Fatto', !r.ok); });
  } else if (id === 'plg-ricarica') {
    await conBottone(e.target, async () => { const r = await inviaPost('/api/plugin/ricarica', {}); toast(r.ok ? `Plugin riletti: ${r.plugin}` : 'Non riuscito', !r.ok); S.sporco = false; S.ricarica = true; await carica(); });
  } else if (id === 'b-pw') {
    const r = await inviaPost('/api/impostazioni/password', {utente: $('#p-ut').value, attuale: $('#p-at').value, nuova: $('#p-nu').value});
    $('#pw-msg').textContent = r.messaggio || ''; $('#pw-msg').className = 'msg ' + (r.ok ? 'ok' : 'bad');
    if (r.ok) { $('#p-at').value = ''; $('#p-nu').value = ''; }
  }
}

// ---------- Accesso da internet (Cloudflare) ----------
const CFS = {zone: null, token: '', passi: null, occupato: false};
async function cfLeggi() { const r = await leggiJson('/api/cloudflare'); return r && r.json(); }
async function cfCarica(forza) {
  const el = $('#cf'); if (!el || S.pagina !== 'impostazioni') return;
  let st; try { st = await cfLeggi(); } catch { return; }
  if (!st) return;
  S.cfStato = st;
  if (!st.configurato) { if (forza || !el.dataset.wiz) { el.dataset.wiz = '1'; el.innerHTML = cfWizard(st); } return; }
  el.dataset.wiz = '';
  if (CFS.occupato && !forza) return;
  if (el.dataset.rimuovi === '1' && !forza) { const s = $('#cf-stato'); if (s) s.innerHTML = cfStatoHtml(st); return; }
  el.innerHTML = cfGestione(st);
}
function cfWizard(st) {
  return `<h2>Accesso da internet (Cloudflare)</h2>
  <p class="nota-sez">Così apri il pannello anche fuori casa, all'indirizzo che scegli tu (es. <b>https://pdc.tuodominio.it</b>), sempre con utente e password. Non devi aprire porte sul router. Serve un dominio che usa i DNS di Cloudflare.</p>
  ${st.demo ? '<p class="msg bad">In modalità demo la procedura non è disponibile.</p>' : ''}
  <details class="guida"><summary>1 · Come creo il token di Cloudflare (2 minuti)</summary><ol>
    <li>Apri <b>dash.cloudflare.com/profile/api-tokens</b> e premi <b>Create Token</b>, poi <b>Create Custom Token</b> (“Get started”).</li>
    <li>Dagli un nome, per esempio <i>pannello PdC</i>.</li>
    <li>In <b>Permissions</b> aggiungi tre righe:<br>• <b>Account</b> · <b>Cloudflare Tunnel</b> · <b>Edit</b><br>• <b>Zone</b> · <b>DNS</b> · <b>Edit</b><br>• <b>Zone</b> · <b>Zone</b> · <b>Read</b></li>
    <li>In <b>Account Resources</b> scegli il tuo account; in <b>Zone Resources</b> scegli <b>Include → Specific zone</b> e il tuo dominio.</li>
    <li>Premi <b>Continue to summary</b>, poi <b>Create Token</b> e <b>copia il token</b> (si vede una volta sola).</li></ol>
    <p class="nota-sez">Il token serve solo adesso: il pannello <b>non lo conserva</b>. Dopo puoi anche cancellarlo dal sito di Cloudflare.</p></details>
  <div class="campi" style="grid-template-columns:1fr">
    <div class="campo lg"><label for="cf-tok">2 · Incolla qui il token</label><input id="cf-tok" type="password" autocomplete="off" placeholder="il token di Cloudflare"></div></div>
  <div class="azioni"><button type="button" id="cf-verifica" ${st.demo ? 'disabled' : ''}>Verifica e cerca i miei domini</button><small class="msg" id="cf-msg"></small></div>
  <div id="cf-passo2"></div>`;
}
function cfPasso2(zone) {
  const opz = zone.map((z, i) => `<option value="${i}">${esc(z.nome)}</option>`).join('');
  return `<h3 class="sez-t" style="margin-top:20px">3 · Scegli l'indirizzo</h3>
  <div class="campi">
    <div class="campo lg"><label for="cf-zona">Dominio</label><select id="cf-zona">${opz}</select></div>
    <div class="campo lg"><label for="cf-sub">Sottodominio</label><input id="cf-sub" type="text" value="pdc" autocapitalize="none" autocorrect="off" spellcheck="false"></div>
    <div class="campo lg"><label for="cf-nome">Nome del tunnel (solo per riconoscerlo su Cloudflare)</label><input id="cf-nome" type="text" value="pannello-pdc" spellcheck="false"></div></div>
  <p class="nota-sez" id="cf-anteprima"></p>
  <div class="azioni"><button type="button" id="cf-crea">Crea tunnel e collega</button><small class="msg" id="cf-msg2"></small></div>
  <ul class="passi" id="cf-passi"></ul>`;
}
function cfAnteprima() { const z = CFS.zone && CFS.zone[+$('#cf-zona').value]; const e = $('#cf-anteprima'); if (e && z) e.innerHTML = 'Il pannello sarà raggiungibile su <b>https://' + esc(($('#cf-sub').value || '…').toLowerCase()) + '.' + esc(z.nome) + '</b>'; }
function cfStatoHtml(st) {
  const p = (c, t) => `<span class="pill ${c}">${t}</span>`;
  return [
    riga('Indirizzo', `<a href="${esc(st.indirizzo)}" target="_blank" rel="noopener">${esc(st.hostname)}</a>`),
    riga('Programma cloudflared', st.programma ? p('si', 'presente') : p('bad', 'manca')),
    riga('Tunnel', st.collegato ? p('si', 'collegato a Cloudflare') : st.in_esecuzione ? p('no', 'in avvio…') : p('off', 'spento')),
  ].join('') + (st.problema ? `<p class="msg bad" style="margin:8px 0 0">${esc(st.problema)}</p>` : '') +
    (st.porta_diversa ? `<p class="msg bad" style="margin:8px 0 0">Il tunnel punta alla porta ${st.porta_servizio}, ma il pannello ora usa un'altra porta: rifai la procedura (Rimuovi, poi di nuovo).</p>` : '');
}
function cfGestione(st) {
  return `<div class="ch"><h2>Accesso da internet (Cloudflare)</h2><button type="button" class="sec piccolo" id="cf-agg">Aggiorna</button></div>
  <div class="righe" id="cf-stato">${cfStatoHtml(st)}</div>
  <div class="azioni">
    ${st.in_esecuzione ? '<button type="button" class="sec" id="cf-ferma">Ferma</button><button type="button" class="sec" id="cf-riavvia">Riavvia</button>' : `<button type="button" id="cf-avvia" ${st.programma ? '' : 'disabled'}>Avvia tunnel</button>`}
    <button type="button" class="sec" id="cf-telegram">Invia il link su Telegram</button>
    <button type="button" class="sec" id="cf-scarica">${st.programma ? 'Aggiorna cloudflared' : 'Scarica cloudflared'}</button></div>
  <div class="campo riga-sw" style="margin-top:14px"><label for="cf-auto">Avvia il tunnel insieme al pannello</label><label class="sw"><input type="checkbox" id="cf-auto" ${st.avvio_automatico ? 'checked' : ''}><span></span></label></div>
  <details class="guida" style="margin-top:12px" id="cf-rim"><summary>Rimuovi l'accesso da internet…</summary>
    <p class="nota-sez">Ferma il tunnel e lo toglie dal pannello. Per cancellare anche tunnel e DNS su Cloudflare incolla un token con gli stessi permessi (altrimenti dovrai cancellarli tu dal loro sito).</p>
    <div class="campo lg"><label for="cf-rtok">Token Cloudflare (facoltativo)</label><input id="cf-rtok" type="password" autocomplete="off"></div>
    <div class="azioni"><button type="button" class="sec" id="cf-rimuovi" style="color:var(--bad)">Rimuovi</button><small class="msg" id="cf-rmsg"></small></div></details>
  <p class="nota-sez">Il tunnel è protetto dal tuo login. Se cambi la porta del pannello, rifai la procedura.</p>`;
}
async function cfAzione(e) {
  const id = e.target.id;
  if (id === 'cf-agg') { toast('Stato aggiornato'); return cfCarica(true); }
  if (id === 'cf-verifica') {
    e.target.disabled = true; $('#cf-msg').textContent = 'Controllo…'; $('#cf-msg').className = 'msg';
    CFS.token = $('#cf-tok').value.trim();
    try { const r = await inviaPost('/api/cloudflare/verifica', {token: CFS.token});
      if (r.ok) { CFS.zone = r.zone; $('#cf-msg').textContent = 'Token valido: ' + r.zone.length + ' dominio/i trovato/i.'; $('#cf-msg').className = 'msg ok'; $('#cf-passo2').innerHTML = cfPasso2(r.zone); cfAnteprima(); }
      else { $('#cf-msg').textContent = r.messaggio; $('#cf-msg').className = 'msg bad'; }
    } catch { $('#cf-msg').textContent = 'Server non raggiungibile'; $('#cf-msg').className = 'msg bad'; }
    e.target.disabled = false;
  } else if (id === 'cf-crea') {
    const z = CFS.zone[+$('#cf-zona').value]; e.target.disabled = true; CFS.occupato = true;
    const ul = $('#cf-passi'), m = $('#cf-msg2'); m.textContent = 'Lavoro su Cloudflare…'; m.className = 'msg'; ul.innerHTML = '';
    const mostra = ps => ul.innerHTML = ps.map(p => `<li class="${p.ok ? 'ok' : 'bad'}">${p.ok ? '✓' : '✗'} ${esc(p.t)}</li>`).join('');
    try {
      const r = await inviaPost('/api/cloudflare/crea', {token: CFS.token, zone_id: z.id, sottodominio: $('#cf-sub').value, nome_tunnel: $('#cf-nome').value});
      mostra(r.passi || []);
      if (!r.ok) { m.textContent = r.messaggio || 'Non riuscito'; m.className = 'msg bad'; e.target.disabled = false; CFS.occupato = false; return; }
      CFS.token = ''; $('#cf-tok').value = '';
      const st = await cfLeggi();
      if (!st.programma) { m.textContent = 'Scarico cloudflared (circa 30 MB)…'; const d = await inviaPost('/api/cloudflare/scarica', {});
        if (!d.ok) { m.textContent = ''; ul.insertAdjacentHTML('beforeend', `<li class="bad">✗ ${esc(d.messaggio)}</li>`); CFS.occupato = false; toast('Tunnel creato, ma manca cloudflared', true); return cfCarica(true); }
        ul.insertAdjacentHTML('beforeend', `<li class="ok">✓ cloudflared scaricato (${esc(d.versione)})</li>`); }
      const a = await inviaPost('/api/cloudflare/avvia', {});
      ul.insertAdjacentHTML('beforeend', a.ok ? '<li class="ok">✓ Tunnel avviato</li>' : `<li class="bad">✗ ${esc(a.messaggio)}</li>`);
      CFS.occupato = false; toast(a.ok ? 'Fatto! Tra qualche secondo l\'indirizzo risponde.' : 'Tunnel creato', !a.ok);
      setTimeout(() => cfCarica(true), 1500);
    } catch { m.textContent = 'Server non raggiungibile'; m.className = 'msg bad'; e.target.disabled = false; CFS.occupato = false; }
  } else if (id === 'cf-avvia' || id === 'cf-ferma' || id === 'cf-riavvia') {
    e.target.disabled = true; CFS.occupato = true;
    const r = await inviaPost('/api/cloudflare/' + id.slice(3), {}); CFS.occupato = false;
    toast(r.ok ? 'Fatto' : (r.messaggio || 'Non riuscito'), !r.ok); setTimeout(() => cfCarica(true), 800);
  } else if (id === 'cf-telegram') {
    e.target.disabled = true; const r = await inviaPost('/api/cloudflare/telegram', {});
    toast(r.messaggio || (r.ok ? 'Inviato' : 'Non riuscito'), !r.ok); e.target.disabled = false;
  } else if (id === 'cf-scarica') {
    e.target.disabled = true; CFS.occupato = true; toast('Scarico cloudflared…'); const r = await inviaPost('/api/cloudflare/scarica', {}); CFS.occupato = false;
    toast(r.ok ? 'Scaricato: ' + r.versione : r.messaggio, !r.ok); cfCarica(true);
  } else if (id === 'cf-rimuovi') {
    if (!confirm('Rimuovere l\'accesso da internet?')) return;
    e.target.disabled = true; const r = await inviaPost('/api/cloudflare/rimuovi', {token: $('#cf-rtok').value.trim()});
    toast(r.avviso || 'Rimosso', !!r.avviso); if (r.avviso) alert(r.avviso); const el = $('#cf'); if (el) el.dataset.rimuovi = ''; cfCarica(true);
  }
}
document.addEventListener('change', async e => {
  if (e.target.id === 'cf-zona' || e.target.id === 'cf-sub') cfAnteprima();
  if (e.target.id === 'cf-auto') { const r = await inviaPost('/api/cloudflare/opzioni', {avvio_automatico: e.target.checked}); toast(r.ok ? 'Salvato' : r.messaggio, !r.ok); }
});
document.addEventListener('input', e => { if (e.target.id === 'cf-sub') cfAnteprima(); if (e.target.id === 'cf-rtok') { const el = $('#cf'); if (el) el.dataset.rimuovi = '1'; } });
document.addEventListener('toggle', e => { if (e.target.id === 'cf-rim') { const el = $('#cf'); if (el) el.dataset.rimuovi = e.target.open ? '1' : ''; } }, true);

// ---------- dati e tempo reale ----------
function finestra() {
  if (S.pagina === 'impostazioni') return {da: 0, a: 0, live: false};
  if (S.pagina === 'dispositivi' || S.pagina === 'efficienza') return {da: 0, a: 0, live: true};
  if (S.range === 'custom' && S.custom) return {da: S.custom.da, a: S.custom.a, live: false};
  const a = S.fine || ora();   // S.fine: fine della finestra quando ci si e' spostati indietro nel tempo (vuoto = in diretta)
  return {da: a - (S.range === '6h' ? 6 : 24) * 3600, a, live: !S.fine};
}
function urlDati() {
  const w = finestra();
  if (S.pagina === 'impostazioni') return '/api/impostazioni';
  if (S.pagina === 'dispositivi') return '/api/dispositivi';
  if (S.pagina === 'efficienza') return `/api/efficienza?giorni=${RANGE_EFF[S.rangeEff] || 7}${S.confronto ? '&confronta=1' : ''}`;
  if (S.pagina === 'analisi') return `/api/analisi?da=${w.da}&a=${w.a}&n=${Math.max(120, Math.min(400, Math.round(($('#pagina').clientWidth || 800) / 3)))}`;
  return `/api/dashboard?da=${w.da}&a=${w.a}`;
}
// La finestra della Dashboard (6h/24h) si scrive anche in un cookie: cosi' il server puo' mettere i dati giusti dentro la pagina.
function ricordaFinestra() {
  try { document.cookie = `pdc_r=${S.range};path=/;max-age=31536000;samesite=lax${location.protocol === 'https:' ? ';secure' : ''}`; } catch { /* niente */ }
}
// La Dashboard arriva gia' dentro la pagina (blocco #dati0): si disegna subito, senza aspettare il giro dei dati. Solo in diretta e sulla finestra salvata.
function datiIniziali() {
  const el = $('#dati0'); if (!el) return false;
  let d = null; try { d = JSON.parse(el.textContent); } catch { /* niente */ }
  el.remove();
  if (!d || !d.dati || S.pagina !== 'dashboard' || S.fine || d.r !== S.range) return false;
  S.dati = d.dati; S.dpag = 'dashboard'; S.off = S.dati.ora - Math.floor(Date.now() / 1000); S.fetchedAt = Date.now();
  disegna();
  return true;
}
let caricando = false;
async function carica() {
  if (S.pagina === 'changelog') return;  // pagina fissa: niente da caricare
  if (caricando) { S.ripeti = true; return; }
  caricando = true;
  const pag = S.pagina;
  try {
    const r = await leggiJson(urlDati()); if (!r) return;
    // se lo script del pannello e' stato aggiornato mentre la pagina era aperta, si ricarica da sola (una volta sola, per evitare giri a vuoto)
    const nuova = r.headers.get('X-Pdc-Build');
    if (nuova && window.BUILD && nuova !== window.BUILD) {
      let gia = null; try { gia = sessionStorage.getItem('pdc_build'); } catch { /* niente */ }
      if (gia !== nuova) { try { sessionStorage.setItem('pdc_build', nuova); } catch { /* niente */ } location.reload(); return; }
    }
    const dati = await r.json();
    if (pag !== S.pagina) return;
    S.dati = dati; S.dpag = pag; S.off = S.dati.ora - Math.floor(Date.now() / 1000); S.fetchedAt = Date.now();
    disegna();
  } catch {
    $('#dot').className = 'dot bad'; $('#agg').textContent = 'Server non raggiungibile';
  } finally {
    caricando = false;
    if (S.ripeti) { S.ripeti = false; carica(); }
  }
}
let attesa = null;
const presto = () => { clearTimeout(attesa); attesa = setTimeout(() => { if (finestra().live) carica(); }, S.pagina === 'dashboard' ? 400 : 3000); };

let flusso = null, ultimoMsg = 0;
// Safari chiude il canale in tempo reale (EventSource) quando la scheda dorme o la rete cambia, e spesso non lo riapre da solo:
// lo si riapre a mano (dopo un errore, al ritorno sulla scheda, quando torna la rete, o se per 45 s non arriva piu' nulla).
function collega() {
  if (!window.EventSource) return;
  if (flusso) { try { flusso.close(); } catch { /* niente */ } flusso = null; }
  const es = new EventSource('/api/flusso');
  flusso = es; ultimoMsg = Date.now();
  const vivo = () => { ultimoMsg = Date.now(); };
  es.addEventListener('ciao', () => { vivo(); S.sse = true; stato(); });
  es.addEventListener('ping', vivo);
  es.addEventListener('dati', () => { vivo(); presto(); });
  es.addEventListener('anomalia', () => { vivo(); carica(); });
  es.onerror = () => {
    S.sse = false; stato();
    if (es.readyState === 2) setTimeout(() => { if (flusso === es) collega(); }, 3000);  // chiuso del tutto: il browser non riprova
  };
}
function controllaFlusso() {
  if (!window.EventSource) return;
  if (!flusso || flusso.readyState === 2 || Date.now() - ultimoMsg > 45000) collega();
}

function avvia() {
  costruisci();
  mostraPagina();
  window.addEventListener('hashchange', () => {
    const [p, sub = ''] = location.hash.replace('#/', '').split('/');
    if (p in TITOLI && p !== S.pagina) { S.pagina = p; S.sub = sub; mostraPagina(); carica(); }
    else if (p === S.pagina && sub !== S.sub) {
      S.sub = sub;
      if (p === 'impostazioni') vistaImp();
      else if (p === 'changelog') vistaChangelog();
      else if (S.dati) { const b = $('#dsp'); if (b) b.dataset.h = ''; disegna(); }
    }
  });
  document.addEventListener('pointermove', e => {
    if (spostaMuovi(e) || zoomaMuovi(e) || dita.size > 1) return;
    if (e.target.closest && e.target.closest('#tlw')) tooltipTimeline(e);
    else { const t = $('#tip'); if (t) t.hidden = true; if (!muoviCursore(e)) togliCursore(); }
  });
  document.addEventListener('pointerleave', togliCursore);
  document.addEventListener('input', segnaModifica); document.addEventListener('change', segnaModifica);
  document.addEventListener('click', e => { if (e.target.closest && e.target.closest('#imp') && e.target.tagName === 'BUTTON') { if (e.target.id.startsWith('cf-')) cfAzione(e); else azioniImpostazioni(e); } });
  let rid = null;
  window.addEventListener('resize', () => { clearTimeout(rid); rid = setTimeout(() => { if (S.dati && S.pagina !== 'dashboard') { const b = $('#grafici') || $('#eff'); if (b) b.dataset.f = ''; disegna(); } }, 250); });
  document.addEventListener('pointerdown', e => { spostaGiu(e); zoomaGiu(e); if (dita.size > 1) return; if (e.target.closest && e.target.closest('#tlw')) tooltipTimeline(e); else muoviCursore(e); });
  document.addEventListener('pointerup', e => { spostaSu(); zoomaSu(e); }); document.addEventListener('pointercancel', e => { spostaSu(); zoomaSu(e); });
  if (S.range === 'custom' && S.pagina !== 'efficienza') { $('#custom').hidden = false; if (S.custom) { $('#c-da').value = loc(S.custom.da); $('#c-a').value = loc(S.custom.a); } }
  ricordaFinestra();
  datiIniziali() || carica();
  collega();
  setInterval(() => { if (finestra().live) carica(); }, 60000);
  setInterval(stato, 15000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { controllaFlusso(); if (finestra().live) carica(); } });
  window.addEventListener('pageshow', controllaFlusso); window.addEventListener('online', controllaFlusso);
  setInterval(controllaFlusso, 10000);
}
avvia();
})();
''',
    'index.html': r'''<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b0f17">
<meta name="color-scheme" content="dark">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="PDC">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icona.png">
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="apple-touch-startup-image" media="(device-width: 440px) and (device-height: 956px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="/avvio/1320x2868.png">
<link rel="apple-touch-startup-image" media="(device-width: 430px) and (device-height: 932px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="/avvio/1290x2796.png">
<link rel="apple-touch-startup-image" media="(device-width: 428px) and (device-height: 926px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="/avvio/1284x2778.png">
<link rel="apple-touch-startup-image" media="(device-width: 420px) and (device-height: 912px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="/avvio/1260x2736.png">
<link rel="apple-touch-startup-image" media="(device-width: 414px) and (device-height: 896px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="/avvio/1242x2688.png">
<link rel="apple-touch-startup-image" media="(device-width: 414px) and (device-height: 896px) and (-webkit-device-pixel-ratio: 2) and (orientation: portrait)" href="/avvio/828x1792.png">
<link rel="apple-touch-startup-image" media="(device-width: 414px) and (device-height: 736px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="/avvio/1242x2208.png">
<link rel="apple-touch-startup-image" media="(device-width: 402px) and (device-height: 874px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="/avvio/1206x2622.png">
<link rel="apple-touch-startup-image" media="(device-width: 393px) and (device-height: 852px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="/avvio/1179x2556.png">
<link rel="apple-touch-startup-image" media="(device-width: 390px) and (device-height: 844px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="/avvio/1170x2532.png">
<link rel="apple-touch-startup-image" media="(device-width: 375px) and (device-height: 812px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="/avvio/1125x2436.png">
<link rel="apple-touch-startup-image" media="(device-width: 375px) and (device-height: 667px) and (-webkit-device-pixel-ratio: 2) and (orientation: portrait)" href="/avvio/750x1334.png">
<title>Pompa di calore</title>
<link rel="stylesheet" href="/app.css?v={{build}}">
</head>
<body>
<div id="app"></div>
<noscript>Per usare il pannello serve JavaScript.</noscript>
<script type="application/json" id="dati0">{{dati0}}</script>
<script src="/app.js?v={{build}}"></script>
</body>
</html>
''',
    'login.html': r'''<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b0f17">
<meta name="color-scheme" content="dark">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="PDC">
<link rel="apple-touch-icon" href="/icona.png">
<link rel="icon" type="image/png" href="/favicon.png">
<title>Accesso · Pompa di calore</title>
<link rel="stylesheet" href="/app.css?v={{build}}">
</head>
<body class="solo">
<form class="accesso" method="post" action="/login" autocomplete="on">
  <img src="/icona.png" alt="" width="72" height="72">
  <h1>Pompa di calore</h1>
  <p class="mut">Accedi per vedere i dati</p>
  <label>Utente<input name="utente" type="text" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" required autofocus></label>
  <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
  <p class="errore">{{errore}}</p>
  <button type="submit">Entra</button>
</form>
</body>
</html>
''',
    'setup.html': r'''<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Prima configurazione · Pompa di calore</title>
<link rel="stylesheet" href="/app.css?v={{build}}">
</head>
<body class="solo">
<div class="accesso">
  <h1>Manca la password</h1>
  <p>Per sicurezza il pannello non mostra nulla finché non scegli utente e password.</p>
  <p>Apri un terminale nella cartella del pannello e lancia:</p>
  <p><code>python pdc_pannello.py imposta-password</code></p>
  <p class="mut">Oppure scrivi <b>utente</b> e <b>password</b> nella sezione <b>accesso</b> di config.json e riavvia il pannello.</p>
</div>
</body>
</html>
''',
}
# impronta di pagina+stile+script: cambia a ogni modifica, anche senza cambiare numero di versione
def _calcola_build(versione, assets):
    """Impronta delle pagine: cambia con la versione e con ogni file web (le pagine aperte si ricaricano da sole)."""
    return hashlib.sha256((versione + "".join(assets[k] for k in sorted(assets))).encode("utf-8")).hexdigest()[:8]


BUILD = _calcola_build(VERSIONE, WEB_ASSETS)


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
    punti = [[es[t], ma[t]] for t in sorted(es)
             if t in ma and fr.get(t, 0) > 0 and stato_a(cambi, t) in (None, "room")]
    if len(punti) > 300:
        passo = len(punti) / 300
        punti = [punti[int(i * passo)] for i in range(300)]
    curva = (t1, m1, t2, m2)
    return {"basso": {"esterna": t1, "mandata": m1}, "alto": {"esterna": t2, "mandata": m2},
            "zona": zona, "zona_fonte": fonte, "punti": punti,
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
    coppie = sorted(zip(vals, pesi))
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
    scarti = [(c["scarto"], p) for c, p in zip(campioni, pesi) if c["scarto"] is not None]
    assoluti = [(c["te"], p) for c, p in zip(campioni, pesi)]
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
        for (ta, va), (tb, vb) in zip(pts, pts[1:]):
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
#@@PARTE telegram@@
# ==========================================================================
# PEZZO 4: TELEGRAM E SENTINELLA (controlli automatici)
# ==========================================================================

import fnmatch

TITOLI = {
    "st_conn_rete": "Collegamento a SmartThings (internet)",
    "st_conn_auth": "Accesso a SmartThings (token)",
    "st_conn_pagamento": "Servizio SmartThings (piano a pagamento?)",
    "st_conn_limite": "Limite di richieste SmartThings",
    "st_conn_server": "Server SmartThings",
    "st_conn_richiesta": "Richiesta a SmartThings",
    "nt_conn_rete": "Collegamento a Netatmo (internet)",
    "nt_conn_auth": "Accesso a Netatmo (token)",
    "nt_conn_pagamento": "Servizio Netatmo",
    "nt_conn_limite": "Limite di richieste Netatmo",
    "nt_conn_server": "Server Netatmo",
    "nt_conn_richiesta": "Richiesta a Netatmo",
    "st_config": "Configurazione SmartThings",
    "nt_config": "Configurazione Netatmo",
    "st_script": "Script (lettura SmartThings)",
    "nt_script": "Script (lettura Netatmo)",
    "pannello_script": "Controlli automatici del pannello",
    "st_pat": "Token personale SmartThings",
    "st_pdc_offline": "Pompa di calore offline",
    "st_ciclo_formato": "Formato dei dati ciclo",
    "nessun_dato": "Dati dalla PdC",
    "pdc_ferma_con_richiesta": "Pompa ferma con richiesta di calore",
    "casa_fredda": "Temperatura in casa",
    "umidita_alta": "Umidità alta",
    "umidita_molto_alta": "Umidità molto alta",
    "cicli_brevi": "Cicli troppo brevi",
    "portata_bassa": "Portata dell'acqua",
    "mandata_lontana": "Mandata lontana dalla curva",
    "acs_bassa": "Acqua calda sanitaria",
    "cop_basso": "Efficienza (COP)",
    "sbrinamenti_frequenti": "Sbrinamenti frequenti",
    "sbrinamento_inizio": "Sbrinamento iniziato",
    "sbrinamento_fine": "Sbrinamento finito",
}

MESSAGGI_DEFAULT = {
    "_nota": "Testi dei messaggi Telegram (HTML semplice: <b>grassetto</b>, <i>corsivo</i>). Segnaposto: "
             "{impianto} {testo} {titolo} {codice} {ora} {data} {durata}. Per cambiare il testo di UN solo "
             "allarme usa per_codice, es.: \"per_codice\": {\"casa_fredda\": {\"apertura\": \"Brrr! {testo}\"}}",
    "apertura_errore": "🚨 <b>{impianto}</b>\n{testo}\n<i>dalle {ora}</i>",
    "apertura_avviso": "⚠️ <b>{impianto}</b>\n{testo}\n<i>dalle {ora}</i>",
    "ripristino": "✅ <b>{impianto}</b>\n{titolo}: tutto di nuovo regolare.\n<i>il problema è durato {durata}</i>",
    "promemoria": "⏰ <b>{impianto}</b>\nCi siamo ancora: {testo}\n<i>da {durata}</i>",
    "riepilogo": "☀️ <b>{impianto} · ieri, {data}</b>\n"
                 "⚡ Energia: <b>{energia} kWh</b> · COP <b>{cop}</b>\n"
                 "🔥 Riscaldamento {ore_casa} h · 🚿 Acqua calda {ore_acs} h ({cicli} cicli)\n"
                 "🏠 In casa {casa_min}–{casa_max} °C · fuori {esterna} °C\n{anomalie}",
    "prova": "👋 <b>{impianto}</b>\nProva riuscita! Da qui arriveranno gli avvisi del pannello.",
    "sbrinamento_inizio": "🧊 <b>{impianto}</b>\nSbrinamento iniziato: esterna {esterna}\n<i>dalle {ora}</i>",
    "sbrinamento_fine": "✅ <b>{impianto}</b>\nSbrinamento finito\n<i>dopo {durata}, {consumo}</i>",
    "troppi": "🤐 <b>{impianto}</b>\nStanno arrivando troppi messaggi: per un po' ne salto alcuni (li trovi sempre nel pannello).",
    "per_codice": {},
}

ALLARMI_DEFAULT = {
    "_nota": "Ogni allarme si spegne con \"attivo\": false. I minuti sono 'da quanto tempo la condizione deve durare'. "
             "Per spegnere anche gli allarmi di collegamento aggiungi una voce col codice (o con * : \"st_conn_*\"): "
             "{\"attivo\": false}. Gli allarmi compaiono sempre anche nel pannello; qui decidi solo cosa va su Telegram.",
    "nessun_dato": {"attivo": True, "minuti": 20},
    "pdc_ferma_con_richiesta": {"attivo": True, "minuti": 60},
    "casa_fredda": {"attivo": True, "sotto_gradi": 17.0, "minuti": 30},
    "umidita_alta": {"attivo": True},
    "umidita_molto_alta": {"attivo": True},
    "cicli_brevi": {"attivo": True, "durata_minima_min": 10, "quanti": 4, "ore": 3},
    "portata_bassa": {"attivo": True, "sotto_lmin": 6.0, "minuti": 15},
    # sopra la curva la PdC ci sta spesso (il compressore resta a bassa frequenza: mediana +8 °C sui dati reali), sotto e' raro e vero
    "mandata_lontana": {"attivo": True, "scarto_sopra_gradi": 12.0, "scarto_gradi": 8.0, "minuti": 40},
    "acs_bassa": {"attivo": True, "sotto_gradi": 35.0, "minuti": 30},
    "cop_basso": {"attivo": True, "sotto": 2.0, "ore_minime": 3},
    "sbrinamenti_frequenti": {"attivo": True, "quanti": 6, "ore": 3},
    "sbrinamento_inizio": {"attivo": True},     # non sono anomalie: avvisi informativi, sempre senza suoneria
    "sbrinamento_fine": {"attivo": True},
}

TELEGRAM_DEFAULT = {
    "_nota": "Puoi scrivere bot_token e chat_id qui, OPPURE indicare un tuo file .json che li contiene "
             "(file_credenziali, con i nomi delle chiavi). Prova: python pdc_pannello.py telegram-prova",
    "abilitato": True,
    "bot_token": "", "chat_id": "",
    "file_credenziali": "", "chiave_token": "bot_token", "chiave_chat": "chat_id",
    "nome_impianto": "Pompa di calore",
    "promemoria_ore": 6,
    "max_messaggi_ora": 20,
    "silenzio_notturno": {"attivo": False, "da": "23:00", "a": "07:00", "anche_errori": False, "oscillazione_gradi": 2.0},
    "riepilogo_mattina": {"attivo": False, "ora": "08:00"},
    "messaggi": MESSAGGI_DEFAULT,
}


def _cerca_chiave(dati, percorso):
    cur = dati
    for p in str(percorso).split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur if isinstance(cur, (str, int)) and str(cur).strip() else None


def _chiavi_file(dati, prefisso=""):
    out = []
    if isinstance(dati, dict):
        for k, v in dati.items():
            out.append(prefisso + str(k))
            if isinstance(v, dict):
                out += _chiavi_file(v, prefisso + str(k) + ".")
    return out


class Telegram:
    """Invia i messaggi in un thread a parte: se Telegram non risponde il pannello non si blocca."""

    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.coda = []                       # elementi: dict(due, tipo, codice, livello, testo, tentativi)
        self.ult_invio = []                  # timestamp degli ultimi invii (limite orario)
        self.troppi_ts = 0
        self.stato = {"ultimo_ok": None, "ultimo_errore": None, "messaggio": None}
        self.promemoria = {}                 # codice -> ts dell'ultimo messaggio (apertura o promemoria)
        try:
            self.notificati = set(json.loads(app.db.meta_get("tg_notificati", "[]")))
        except ValueError:
            self.notificati = set()
        self.fermo = threading.Event()
        self.thread = threading.Thread(target=self._lavora, daemon=True, name="telegram")
        self._cache_file = (None, None, None)

    # ----- configurazione -----
    def cfg(self):
        return self.app.cfg.leggi()["telegram"]

    def credenziali(self):
        """-> (token, chat, errore). Il file dell'utente ha la precedenza se indicato."""
        c = self.cfg()
        f = (c.get("file_credenziali") or "").strip()
        if f:
            p = Path(f)
            p = p if p.is_absolute() else self.app.base / p
            try:
                mt = p.stat().st_mtime
                if self._cache_file[0] != (str(p), mt):
                    self._cache_file = ((str(p), mt), json.loads(p.read_text(encoding="utf-8-sig")), None)
                dati = self._cache_file[1]
            except OSError:
                return None, None, f"Non trovo il file delle credenziali Telegram: {p}"
            except ValueError:
                return None, None, f"Il file {p.name} non e' un JSON valido."
            tok = _cerca_chiave(dati, c.get("chiave_token") or "bot_token")
            chat = _cerca_chiave(dati, c.get("chiave_chat") or "chat_id")
            if tok is None or chat is None:
                return None, None, (f"In {p.name} non trovo le chiavi '{c.get('chiave_token')}' / "
                                    f"'{c.get('chiave_chat')}'. Chiavi presenti: {', '.join(_chiavi_file(dati)) or 'nessuna'}. "
                                    "Correggi chiave_token e chiave_chat in config.json.")
            return str(tok).strip(), str(chat).strip(), None
        tok, chat = (c.get("bot_token") or "").strip(), str(c.get("chat_id") or "").strip()
        if not tok or not chat:
            return None, None, "Telegram non e' configurato (bot_token/chat_id vuoti)."
        return tok, chat, None

    def attivo(self):
        c = self.cfg()
        if not c.get("abilitato", True):
            return False
        return self.credenziali()[0] is not None

    def allarme_attivo(self, codice):
        al = self.app.cfg.leggi()["allarmi"]
        if codice in al and isinstance(al[codice], dict):
            return bool(al[codice].get("attivo", True))
        for k, v in al.items():
            if "*" in k and isinstance(v, dict) and fnmatch.fnmatch(codice, k):
                return bool(v.get("attivo", True))
        return True

    # ----- testi -----
    def formatta(self, tipo, codice, livello, testo, durata_s=0, extra=None):
        c = self.cfg()
        m = c["messaggi"]
        chiave = {"apertura": "apertura_errore" if livello == "errore" else "apertura_avviso"}.get(tipo, tipo)
        sp = (m.get("per_codice") or {}).get(codice, {})
        modello = sp.get(tipo) or sp.get(chiave) or m.get(chiave) or MESSAGGI_DEFAULT.get(chiave, "{testo}")
        titoli = dict(TITOLI)
        titoli.update(m.get("titoli", {}) if isinstance(m.get("titoli"), dict) else {})
        ora = time.time()
        valori = {"impianto": esc_html(c["nome_impianto"]), "testo": esc_html(testo), "codice": codice,
                  "titolo": esc_html(titoli.get(codice, codice)), "ora": time.strftime("%H:%M", time.localtime(ora)),
                  "data": time.strftime("%d/%m/%Y", time.localtime(ora)), "durata": durata_it(durata_s)}
        if extra:
            valori.update(extra)
        try:
            return modello.format_map(_Sicuro(valori))
        except (ValueError, IndexError):
            return f"{valori['impianto']}: {valori['testo']}"

    # ----- coda -----
    def accoda(self, tipo, codice, livello, testo, ritardo=0, durata_s=0, extra=None, forza=False, muto=False):
        with self.lock:
            self.coda.append({"due": time.time() + ritardo, "tipo": tipo, "codice": codice, "livello": livello,
                              "testo": testo, "durata_s": durata_s, "extra": extra, "tentativi": 0, "forza": forza,
                              "muto": muto})

    def ascolta(self, tipo, codice, livello, testo):
        """Registrato in Eventi.ascoltatori: arriva da tutte le anomalie (script, collegamenti, sentinella)."""
        if not self.allarme_attivo(codice):
            return
        if tipo == "apertura":
            r = self.app.cfg.leggi()["allarmi"].get(codice, {})
            ritardo = int(r.get("ritardo_minuti", 0) or 0) * 60 if isinstance(r, dict) else 0
            self.accoda("apertura", codice, livello, testo, ritardo)
        elif tipo == "ripristino":
            with self.lock:
                in_attesa = [e for e in self.coda if e["codice"] == codice and e["tipo"] == "apertura"]
                for e in in_attesa:      # risolto prima ancora di avvisare: niente messaggi
                    self.coda.remove(e)
            if codice in self.notificati and not in_attesa:
                self.accoda("ripristino", codice, "info", testo, 0, self._durata_evento(codice))

    def _durata_evento(self, codice):
        with closing(self.app.db._c()) as c:
            r = c.execute("SELECT ts, chiuso_ts FROM eventi WHERE codice=? ORDER BY id DESC LIMIT 1", (codice,)).fetchone()
        return max(0, (r[1] or int(time.time())) - r[0]) if r else 0

    def _segna(self, codice, si):
        if si:
            self.notificati.add(codice)
        else:
            self.notificati.discard(codice)
        self.app.db.meta_set("tg_notificati", json.dumps(sorted(self.notificati)))

    def silenzioso(self, livello):
        s = self.cfg()["silenzio_notturno"]
        if not s.get("attivo"):
            return False
        if livello == "errore" and not s.get("anche_errori"):
            return False
        try:
            da = _minuti(s["da"])
            a = _minuti(s["a"])
        except (ValueError, KeyError):
            return False
        lt = time.localtime()
        m = lt.tm_hour * 60 + lt.tm_min
        return (da <= m < a) if da < a else (m >= da or m < a)

    # ----- invio -----
    def _http(self, token, chat, testo, silenzioso, html=True):
        dati = {"chat_id": chat, "text": testo, "disable_web_page_preview": "true"}
        if html:
            dati["parse_mode"] = "HTML"
        if silenzioso:
            dati["disable_notification"] = "true"
        return http_json("POST", f"{TG_BASE}{token}/sendMessage",
                         {"Content-Type": "application/x-www-form-urlencoded"}, dati, timeout=20)

    def invia_ora(self, testo, silenzioso=False):
        """Invio immediato. -> (ok, messaggio). Mai il token nei messaggi di errore."""
        tok, chat, err = self.credenziali()
        if err:
            return False, err
        try:
            code, js = self._http(tok, chat, testo, silenzioso)
            if code == 400 and "parse" in str(js.get("description", "")).lower():
                code, js = self._http(tok, chat, re.sub(r"<[^>]+>", "", testo), silenzioso, html=False)
        except ErroreAPI as e:
            return False, e.messaggio
        if code == 200 and js.get("ok"):
            self.stato.update(ultimo_ok=int(time.time()), messaggio=None)
            return True, "inviato"
        d = js.get("description") or js.get("raw") or f"errore {code}"
        if code in (401, 404):
            d += " (token del bot sbagliato?)"
        elif code == 400 and "chat" in d.lower():
            d += " (chat_id sbagliato, oppure non hai ancora scritto al bot?)"
        elif code == 403:
            d += " (il bot e' stato bloccato o non ha il permesso di scriverti)"
        elif code == 429:
            d += f" (riprovo tra {js.get('parameters', {}).get('retry_after', '?')} s)"
        return False, f"Telegram: {d}"

    def _limite_ok(self, forza):
        c = self.cfg()
        adesso = time.time()
        self.ult_invio = [t for t in self.ult_invio if adesso - t < 3600]
        if forza or len(self.ult_invio) < int(c["max_messaggi_ora"]):
            return True
        if adesso - self.troppi_ts > 3600:
            self.troppi_ts = adesso
            self.invia_ora(self.formatta("troppi", "troppi", "avviso", ""))
        return False

    def _lavora(self):
        while not self.fermo.wait(2):
            try:
                self._giro()
            except Exception:
                log.exception("errore nel thread Telegram")

    def _giro(self):
        adesso = time.time()
        with self.lock:
            pronti = [e for e in self.coda if e["due"] <= adesso]
        for e in pronti:
            if e["tipo"] == "apertura" and not self.app.db.evento_aperto(e["codice"]):
                self._togli(e)          # si e' risolto da solo durante il ritardo: nessun messaggio
                continue
            if not self.attivo():
                self._togli(e) if e["tentativi"] > 0 else None
                continue
            if not self._limite_ok(e["forza"]):
                self._togli(e)
                continue
            testo = self.formatta(e["tipo"], e["codice"], e["livello"], e["testo"], e["durata_s"], e["extra"])
            ok, msg = self.invia_ora(testo, e.get("muto") or self.silenzioso(e["livello"]))
            if ok:
                self.ult_invio.append(time.time())
                self._togli(e)
                if e["tipo"] == "apertura":
                    self._segna(e["codice"], True)
                    self.promemoria[e["codice"]] = time.time()
                elif e["tipo"] == "ripristino":
                    self._segna(e["codice"], False)
                    self.promemoria.pop(e["codice"], None)
            else:
                e["tentativi"] += 1
                self.stato.update(ultimo_errore=int(time.time()), messaggio=msg)
                log.warning("Telegram: invio non riuscito (%d): %s", e["tentativi"], msg)
                if e["tentativi"] >= 6:
                    self._togli(e)
                else:
                    e["due"] = time.time() + min(900, 30 * 2 ** e["tentativi"])

    def _togli(self, e):
        with self.lock:
            if e in self.coda:
                self.coda.remove(e)

    def promemoria_aperti(self):
        """Se un'anomalia resta aperta, ogni N ore un promemoria (0 = mai)."""
        ore = float(self.cfg().get("promemoria_ore") or 0)
        if ore <= 0:
            return
        adesso = time.time()
        for ts, codice, livello, testo, chiuso in self.app.db.eventi_recenti(50):
            if chiuso is not None or codice not in self.notificati or not self.allarme_attivo(codice):
                continue
            ultimo = self.promemoria.get(codice, ts)
            if adesso - ultimo >= ore * 3600 and not any(e["codice"] == codice for e in self.coda):
                self.promemoria[codice] = adesso
                self.accoda("promemoria", codice, livello, testo, 0, int(adesso - ts))

    def collega(self):
        self.app.eventi.ascoltatori.append(self.ascolta)
        self.thread.start()

    def ferma(self):
        self.fermo.set()


class _Sicuro(dict):
    def __missing__(self, k):
        return ""


def esc_html(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def durata_it(s):
    s = int(s)
    if s < 90:
        return f"{s} secondi"
    if s < 5400:
        return f"{round(s / 60)} minuti"
    if s < 172800:
        return f"{s / 3600:.1f} ore".replace(".", ",")
    return f"{round(s / 86400)} giorni"


def _minuti(hhmm):
    h, m = str(hhmm).split(":")
    return int(h) * 60 + int(m)


TG_BASE = "https://api.telegram.org/bot"


# --------------------------------------------------------------------------
# Sentinella: controlla i dati ogni minuto e apre/chiude le anomalie
# --------------------------------------------------------------------------

class Sentinella(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True, name="sentinella")
        self.app = app
        self.fermo = threading.Event()
        self.da = {}          # nome regola -> da quando la condizione e' vera (ts) o None
        self.falso_da = {}    # codice -> da quando la condizione e' falsa
        self.ult_cop = 0
        self.cop_cond = False
        self.sbr_primo = True   # primo controllo dopo l'avvio: uno sbrinamento gia' in corso non si riannuncia

    def run(self):
        self.fermo.wait(45)    # lascia partire i raccoglitori
        while not self.fermo.is_set():
            try:
                self.valuta()
                self.avvisa_sbrinamento()
                self.app.eventi.chiudi("pannello_script")
                if getattr(self.app, "telegram", None):
                    self.app.telegram.promemoria_aperti()
                    self.riepilogo()
            except Exception:
                tb = traceback.format_exc()
                log.error("errore nella sentinella:\n%s", tb)
                try:
                    self.app.eventi.apri("pannello_script", "Errore nei controlli automatici: " + tb.strip().splitlines()[-1])
                except Exception:
                    log.exception("non riesco nemmeno a segnalare l'errore (database non scrivibile?)")
            self.fermo.wait(60)

    # ----- utilita' -----
    def durata(self, nome, cond, ora):
        """Minuti da cui 'cond' e' continuamente vera (0 se falsa)."""
        if not cond:
            self.da[nome] = None
            return 0
        if self.da.get(nome) is None:
            self.da[nome] = ora
        return (ora - self.da[nome]) / 60.0

    def imposta(self, codice, cond, testo, livello="avviso"):
        ev = self.app.eventi
        if cond:
            self.falso_da.pop(codice, None)
            ev.apri(codice, testo, livello)
        elif self.app.db.evento_aperto(codice):
            t0 = self.falso_da.setdefault(codice, time.time())
            if time.time() - t0 >= 600:      # 10 minuti di calma prima di dichiarare risolto
                self.falso_da.pop(codice, None)
                ev.chiudi(codice)

    # ----- le regole -----
    def valuta(self):
        app, ora = self.app, int(time.time())
        al = app.cfg.leggi()["allarmi"]
        db = app.db
        ult = db.ultimi_dict()

        def num(s, max_eta=900):
            r = ult.get(s)
            return r[2] if r and r[2] is not None and ora - r[1] <= max_eta else None

        def txt(s):
            r = ult.get(s)
            return r[3] if r else None

        def on(nome):
            r = al.get(nome)
            return isinstance(r, dict) and r.get("attivo", True)

        tol = max(150, int(app.intervallo_pdc() * 1.5))
        modo = modo_corrente(db, ora, tol)
        pdc_on, risc_on = txt("pdc.acceso") == "on", txt("risc.acceso") == "on"

        if on("nessun_dato"):
            r = al["nessun_dato"]
            ts = ult.get("acs.temperatura", (None, 0))[1] or 0
            causa_nota = any(db.evento_aperto(c) for c in ("st_pdc_offline", "st_config")) or \
                any(e[1].startswith("st_conn_") and e[4] is None for e in db.eventi_recenti(30))
            vecchio = ora - ts > float(r["minuti"]) * 60
            if ts and vecchio and not causa_nota:
                m = round((ora - ts) / 60)
                self.imposta("nessun_dato", True, f"Non arrivano dati nuovi dalla pompa di calore da {m} minuti.", "errore")
            else:
                self.imposta("nessun_dato", False, "")

        if on("pdc_ferma_con_richiesta") and risc_on and pdc_on:
            r = al["pdc_ferma_con_richiesta"]
            richiesta = num("casa.richiesta_calore", 1200) == 1.0
            m = self.durata("ferma", richiesta and modo == "riposo", ora)
            self.imposta("pdc_ferma_con_richiesta", m >= float(r["minuti"]),
                         f"Lo Smarther chiede calore da {round(m)} minuti ma la pompa di calore è ferma.")

        if on("casa_fredda"):
            r = al["casa_fredda"]
            t = num("casa.temperatura", 1800)
            m = self.durata("fredda", t is not None and t < float(r["sotto_gradi"]), ora)
            self.imposta("casa_fredda", m >= float(r["minuti"]),
                         f"In casa ci sono {num_it(t)} °C (soglia {num_it(r['sotto_gradi'])} °C).")

        # umidita' (Smarther): scatta sopra la soglia per N minuti, rientra 2 punti sotto la soglia (isteresi)
        umid = num("casa.umidita", 1800)
        cu = app.cfg.leggi().get("umidita") or {}
        for cod, livello, soglia in (("umidita_alta", "avviso", soglie_umidita(app)[0]),
                                     ("umidita_molto_alta", "errore", soglie_umidita(app)[1])):
            if on(cod):
                aperto = db.evento_aperto(cod)
                cond = umid is not None and (umid > soglia or (aperto and umid > soglia - float(cu.get("rientro_punti", 2))))
                m = self.durata(cod, cond, ora)
                self.imposta(cod, m >= float(cu.get("minuti", 30)),
                             f"L'umidità in casa è {num_it(umid, 0)} % (soglia {num_it(soglia, 0)} %).", livello)

        if on("cicli_brevi"):
            r = al["cicli_brevi"]
            da = ora - int(float(r["ore"]) * 3600)
            brevi = [c for c in cicli_compressore(db, da, ora, ora)
                     if c[2] == "casa" and (c[1] - c[0]) < float(r["durata_minima_min"]) * 60]
            self.imposta("cicli_brevi", len(brevi) >= int(r["quanti"]),
                         f"{len(brevi)} cicli di riscaldamento sotto i {r['durata_minima_min']} minuti nelle ultime "
                         f"{num_it(r['ore'], 0)} ore: la pompa parte e si ferma troppo spesso.")

        if on("portata_bassa"):
            r = al["portata_bassa"]
            p, hz = num("ciclo.portata", 900), num("ciclo.freq_compressore", 900)
            m = self.durata("portata", p is not None and (hz or 0) > 0 and p < float(r["sotto_lmin"]), ora)
            self.imposta("portata_bassa", m >= float(r["minuti"]),
                         f"La portata dell'acqua è {num_it(p)} L/min (soglia {num_it(r['sotto_lmin'])}): "
                         "filtro sporco, aria nel circuito o valvole chiuse?")

        if on("mandata_lontana"):
            r = al["mandata_lontana"]
            curva = dati_curva(db, ult, ora - 3600, ora, num("ciclo.esterna", 1800))
            att = curva["ora"]["mandata"] if curva else None
            man = num("ciclo.mandata", 900)
            in_casa = self.durata("casa_run", modo == "casa", ora) >= 20
            sotto = float(r["scarto_gradi"])
            sopra = float(r.get("scarto_sopra_gradi", sotto))
            lim = sopra if (man is not None and att is not None and man > att) else sotto
            fuori = in_casa and att is not None and man is not None and abs(man - att) > lim
            m = self.durata("mandata", fuori, ora)
            self.imposta("mandata_lontana", m >= float(r["minuti"]),
                         f"La mandata è {num_it(man)} °C, la curva prevede {num_it(att)} °C (scarto oltre "
                         f"{num_it(lim)} °C).")

        if on("acs_bassa") and pdc_on:
            r = al["acs_bassa"]
            t = num("acs.temperatura", 1800)
            m = self.durata("acs", t is not None and t < float(r["sotto_gradi"]), ora)
            self.imposta("acs_bassa", m >= float(r["minuti"]),
                         f"L'acqua calda è a {num_it(t)} °C (soglia {num_it(r['sotto_gradi'])} °C).")

        if on("sbrinamenti_frequenti"):
            r = al["sbrinamenti_frequenti"]
            da = ora - int(float(r["ore"]) * 3600)
            n = sum(1 for _, v in q_cambi(db, "pdc.sbrinamento", da, ora)[1:] if v == "on")
            self.imposta("sbrinamenti_frequenti", n >= int(r["quanti"]),
                         f"{n} sbrinamenti nelle ultime {num_it(r['ore'], 0)} ore.")

        if on("cop_basso") and ora - self.ult_cop > 1800:
            self.ult_cop = ora
            r = al["cop_basso"]
            da = ora - 86400
            da -= da % 300
            e = calcola_efficienza(db, da, ora, tol, ora)
            t, cop = e["tot"], e["tot"]["cop"]
            ore_lav = t["ore_casa"] + t["ore_acs"]
            cov = [v for v in e["copertura"].values() if v is not None]
            valido = cop is not None and ore_lav >= float(r["ore_minime"]) and (not cov or min(cov) >= 60)
            if valido:
                self.cop_cond = cop < float(r["sotto"]) if not self.cop_cond else cop < float(r["sotto"]) + 0.3
            self.imposta("cop_basso", valido and self.cop_cond,
                         f"Negli ultimi giorni il COP stimato è {num_it(cop, 2)} (soglia {num_it(r['sotto'])}): "
                         "la pompa rende meno del solito.")

    # ----- sbrinamento: due avvisi informativi (inizio e fine), sempre senza suoneria -----
    def avvisa_sbrinamento(self):
        """Segue lo stato pdc.sbrinamento. L'inizio dello sbrinamento annunciato resta in 'tg_sbr_inizio': dopo un riavvio
        non si riannuncia l'inizio, ma la fine si'. Non sono anomalie: niente eventi, niente promemoria."""
        db, tg = self.app.db, self.app.telegram
        ora = int(time.time())
        r = db.ultimi_dict().get("pdc.sbrinamento")
        primo, self.sbr_primo = self.sbr_primo, False
        if not r or r[3] not in ("on", "off") or ora - r[1] > 900:
            return
        try:
            inizio = int(db.meta_get("tg_sbr_inizio", "0") or 0)
        except ValueError:
            inizio = 0
        if inizio and ora - inizio > 3 * 3600:      # dimenticato da ore (pannello spento): non si racconta
            inizio = 0
            db.meta_set("tg_sbr_inizio", "0")
        if r[3] == "on" and not inizio:
            on_ts = [t for t, v in q_cambi(db, "pdc.sbrinamento", ora - 6 * 3600, ora) if v == "on"]
            inizio = on_ts[-1] if on_ts else ora
            db.meta_set("tg_sbr_inizio", str(inizio))
            if not primo and tg.allarme_attivo("sbrinamento_inizio"):
                est = db.ultimi_dict().get("ciclo.esterna")
                est_t = f"{num_it(est[2], 0)} °C" if est and est[2] is not None and ora - est[1] <= 1800 else "n.d."
                tg.accoda("sbrinamento_inizio", "sbrinamento_inizio", "info", "", extra={"esterna": est_t,
                          "ora": time.strftime("%H:%M", time.localtime(inizio))}, muto=True)
        elif r[3] == "off" and inizio:
            db.meta_set("tg_sbr_inizio", "0")
            if tg.allarme_attivo("sbrinamento_fine"):
                x = [e for e in elenco_sbrinamenti(db, inizio - 1, ora, ora)["elenco"] if not e["incorso"]]
                minuti = x[0]["min"] if x else max(1, round((ora - inizio) / 60))
                kwh = x[0]["kwh"] if x else None
                tg.accoda("sbrinamento_fine", "sbrinamento_fine", "info", "", extra={
                    "durata": f"{minuti} min",
                    "consumo": f"consumo circa {num_it(kwh, 2)} kWh" if kwh is not None else "consumo non disponibile"}, muto=True)

    # ----- riepilogo del mattino -----
    # ----- cosa e' successo durante il silenzio notturno -----
    def _finestra_notte(self, ora):
        """(inizio, fine) dell'ultima notte silenziosa gia' finita, oppure None."""
        s = self.app.telegram.cfg()["silenzio_notturno"]
        if not s.get("attivo"):
            return None
        try:
            da, a = _minuti(s["da"]), _minuti(s["a"])
        except (ValueError, KeyError):
            return None
        lt = time.localtime(ora)
        g0 = _ore_locali(lt)
        fine = g0 + a * 60
        inizio = (g0 if da < a else g0 - 86400) + da * 60
        if ora < fine:  # riepilogo prima della fine del silenzio: si racconta la notte fino a ora
            return (inizio, ora) if inizio < ora else None
        return inizio, fine

    def _notte_righe(self, ora):
        """Righe (testo HTML) sulle grandi oscillazioni avvenute nel silenzio notturno; vuoto se e' stata una notte tranquilla."""
        w = self._finestra_notte(ora)
        if not w:
            return ""
        ini, fin = w
        db, cfg = self.app.db, self.app.cfg.leggi()
        soglia = float((cfg["telegram"]["silenzio_notturno"].get("oscillazione_gradi") or 2.0))
        righe = []
        t = q_serie(db, "casa.temperatura", ini, fin)
        if t:
            lo, hi = min(v for _, v in t), max(v for _, v in t)
            if hi - lo >= soglia:
                righe.append(f"🏠 In casa tra {num_it(lo)} e {num_it(hi)} °C")
        u = q_serie(db, "casa.umidita", ini, fin)
        if u:
            ts_max, v_max = max(u, key=lambda x: x[1])
            if v_max > soglie_umidita(self.app)[0]:
                righe.append(f"💧 Umidità fino a {num_it(v_max, 0)} % (alle {time.strftime('%H:%M', time.localtime(ts_max))})")
        hm = lambda x: time.strftime("%H:%M", time.localtime(x))
        for ts, cod, liv, testo, chiuso in reversed(db.eventi_recenti(100)):
            if ini <= ts < fin:
                fino = f"alle {hm(chiuso)}" if chiuso else "ancora aperto"
                righe.append(f"⚠️ {esc_html(TITOLI.get(cod, cod))}: dalle {hm(ts)} {fino}")
        return "\n".join(righe)

    def riepilogo(self):
        tg = self.app.telegram
        c = tg.cfg()["riepilogo_mattina"]
        if not c.get("attivo") or not tg.attivo():
            return
        lt = time.localtime()
        try:
            if lt.tm_hour * 60 + lt.tm_min < _minuti(c["ora"]):
                return
        except (ValueError, KeyError):
            return
        oggi = time.strftime("%Y-%m-%d", lt)
        if self.app.db.meta_get("tg_riepilogo_giorno") == oggi:
            return
        self.app.db.meta_set("tg_riepilogo_giorno", oggi)
        oggi0 = _ore_locali(lt)
        ieri0 = oggi0 - 86400
        ora = int(time.time())
        tol = max(150, int(self.app.intervallo_pdc() * 1.5))
        e = calcola_efficienza(self.app.db, ieri0, oggi0, tol, ora)
        g = e["giorni"][0] if e["giorni"] else None
        if not g:
            return
        casa = [v for _, v in q_serie(self.app.db, "casa.temperatura", ieri0, oggi0)]
        est = [v for _, v in q_serie(self.app.db, "ciclo.esterna", ieri0, oggi0)]
        aperti = [x for x in self.app.db.eventi_recenti(30) if x[4] is None]
        anom = ("\n⚠️ Anomalie aperte: " + ", ".join(esc_html(TITOLI.get(x[1], x[1])) for x in aperti)) if aperti else "\n✅ Nessuna anomalia aperta."
        righe_notte = self._notte_righe(ora)
        if righe_notte:
            anom += "\n\n🌙 <b>Di notte</b>\n" + righe_notte
        extra = {"energia": num_it(g["el_casa"] + g["el_acs"] + g["el_riposo"]), "cop": num_it(g["cop"], 2) if g["cop"] else "—",
                 "ore_casa": num_it(g["min_casa"] / 60), "ore_acs": num_it(g["min_acs"] / 60), "cicli": e["cicli"]["casa"]["n"],
                 "casa_min": num_it(min(casa)) if casa else "—", "casa_max": num_it(max(casa)) if casa else "—",
                 "esterna": num_it(sum(est) / len(est)) if est else "—", "anomalie": anom,
                 "data": time.strftime("%d/%m", time.localtime(ieri0 + 3600))}
        tg.accoda("riepilogo", "riepilogo", "info", "", 0, 0, extra, forza=True)


def num_it(v, d=1):
    return "—" if v is None else f"{float(v):.{d}f}".replace(".", ",")


def avvia_telegram_e_sentinella(app):
    app.telegram = Telegram(app)
    app.telegram.collega()
    app.sentinella = Sentinella(app)
    app.sentinella.start()
    ok, err = (app.telegram.attivo(), app.telegram.credenziali()[2])
    if app.cfg.leggi()["telegram"]["abilitato"] and not ok:
        log.info("Telegram non attivo: %s", err)
    elif ok:
        log.info("Telegram attivo.")

#@@PARTE impostazioni@@
# ==========================================================================
# PEZZO 5: PAGINA IMPOSTAZIONI (stato dei collegamenti + modifica di config.json dal pannello)
# ==========================================================================

AVVIO = time.time()

# Tipi di campo: testo, segreto (si scrive ma non si rilegge), bool, int, float, ora (HH:MM), lungo (testo su piu' righe),
# scelta (menu a tendina: opzioni = [[valore, etichetta], ...])
def _c(chiave, tipo, et, aiuto="", **kw):
    d = {"chiave": chiave, "tipo": tipo, "et": et, "aiuto": aiuto}
    d.update(kw)
    return d


ETICHETTE_ALLARME = {
    "minuti": ("Per quanti minuti di fila", "int", 1, 1440),
    "sotto_gradi": ("Avvisa sotto (°C)", "float", -30, 90),
    "sotto_lmin": ("Avvisa sotto (L/min)", "float", 0, 60),
    "scarto_sopra_gradi": ("Scarto sopra la curva (°C)", "float", 1, 40),
    "scarto_gradi": ("Scarto sotto la curva (°C)", "float", 1, 40),
    "durata_minima_min": ("Un ciclo è 'breve' se dura meno di (minuti)", "int", 1, 120),
    "quanti": ("Quanti eventi fanno scattare l'avviso", "int", 1, 100),
    "ore": ("Periodo controllato (ore)", "float", 0.5, 48),
    "sotto": ("Avvisa sotto (COP)", "float", 0.5, 10),
    "ore_minime": ("Ore minime di lavoro per valutarlo", "float", 0.5, 24),
    "ritardo_minuti": ("Aspetta prima di avvisare (minuti)", "int", 0, 1440),
}

DESCRIZIONI_ALLARME = {
    "nessun_dato": "Non arrivano dati dalla pompa di calore.",
    "pdc_ferma_con_richiesta": "Lo Smarther chiede calore ma la pompa è ferma.",
    "casa_fredda": "La temperatura in casa è troppo bassa.",
    "umidita_alta": "L'umidità in casa supera la soglia di guardia (giallo nella Dashboard).",
    "umidita_molto_alta": "L'umidità in casa è molto alta (rosso nella Dashboard).",
    "cicli_brevi": "La pompa parte e si ferma troppo spesso.",
    "portata_bassa": "Poca acqua nel circuito (filtro, aria, valvole).",
    "mandata_lontana": "La mandata è lontana da quella prevista dalla curva climatica, con una soglia per sopra e una per sotto.",
    "acs_bassa": "L'acqua calda sanitaria è troppo fredda.",
    "cop_basso": "L'efficienza stimata (COP) è bassa.",
    "sbrinamenti_frequenti": "Troppi sbrinamenti ravvicinati.",
    "sbrinamento_inizio": "Un messaggio, senza suoneria, quando la pompa entra in sbrinamento.",
    "sbrinamento_fine": "Un messaggio, senza suoneria, quando lo sbrinamento finisce (durata e consumo).",
}

AVVISI_INFORMATIVI = ("sbrinamento_inizio", "sbrinamento_fine")     # non sono anomalie: solo interruttore, niente soglie né ritardo


MODELLI_SMARTHER = [
    ["", "Non specificato"],
    ["XW8002", "XW8002 · da incasso"],
    ["XM8002", "XM8002 · da incasso"],
    ["XG8002", "XG8002 · da incasso"],
    ["XW8002W", "XW8002W · da parete (solo bianco)"],
]


def schema_impostazioni():
    sezioni = [
        {"id": "telegram", "titolo": "Telegram", "nota": "Dove arrivano gli avvisi. Il token del bot si può inserire qui oppure in un "
         "tuo file .json (riquadro sotto): se il file è indicato ha la precedenza.", "prova": True, "campi": [
            _c("telegram.abilitato", "bool", "Invia gli avvisi su Telegram"),
            _c("telegram.bot_token", "segreto", "Token del bot", "Quello che ti ha dato BotFather."),
            _c("telegram.chat_id", "testo", "Chat ID", "Il tuo numero di chat con il bot."),
            _c("telegram.nome_impianto", "testo", "Nome mostrato nei messaggi"),
        ]},
        {"id": "telegram_file", "titolo": "Telegram: credenziali da un tuo file (facoltativo)", "nota": "Se il token e la chat sono già in un tuo "
         "file .json (per esempio quello di un altro bot), scrivi qui dove sta e come si chiamano le chiavi.", "campi": [
            _c("telegram.file_credenziali", "testo", "Percorso del file", "Per es. C:\\bot\\telegram.json"),
            _c("telegram.chiave_token", "testo", "Chiave del token nel file", "Per chiavi annidate: a.b"),
            _c("telegram.chiave_chat", "testo", "Chiave della chat nel file"),
        ]},
        {"id": "telegram_limiti", "titolo": "Telegram: promemoria e limiti", "campi": [
            _c("telegram.promemoria_ore", "float", "Promemoria ogni (ore)", "Se l'anomalia resta aperta. 0 = mai.", min=0, max=168),
            _c("telegram.max_messaggi_ora", "int", "Massimo messaggi all'ora", min=1, max=200),
        ]},
        {"id": "orari", "titolo": "Silenzio notturno", "nota": "Di notte i messaggi arrivano senza suoneria.", "campi": [
            _c("telegram.silenzio_notturno.attivo", "bool", "Silenzio notturno attivo"),
            _c("telegram.silenzio_notturno.da", "ora", "Dalle"),
            _c("telegram.silenzio_notturno.a", "ora", "Alle"),
            _c("telegram.silenzio_notturno.anche_errori", "bool", "Silenzia anche gli errori gravi"),
            _c("telegram.silenzio_notturno.oscillazione_gradi", "float", "Oscillazione notturna da segnalare (°C)",
               "Nel riepilogo del mattino (se attivo) aggiungo cosa è successo durante il silenzio: temperatura in casa variata di almeno questi gradi, umidità sopra la soglia gialla, avvisi scattati.", min=0.5, max=20),
        ]},
        {"id": "riepilogo", "titolo": "Riepilogo del mattino", "campi": [
            _c("telegram.riepilogo_mattina.attivo", "bool", "Riepilogo di ieri ogni mattina"),
            _c("telegram.riepilogo_mattina.ora", "ora", "Ora del riepilogo"),
        ]},
        {"id": "testi", "titolo": "Testi dei messaggi", "nota": "HTML semplice (<b>grassetto</b>, <i>corsivo</i>). Segnaposto: "
         "{impianto} {testo} {titolo} {ora} {data} {durata}. Il riepilogo ha i suoi: {energia} {cop} {ore_casa} "
         "{ore_acs} {cicli} {casa_min} {casa_max} {esterna} {anomalie}.", "campi": [
            _c("telegram.messaggi.apertura_errore", "lungo", "Problema grave"),
            _c("telegram.messaggi.apertura_avviso", "lungo", "Avviso"),
            _c("telegram.messaggi.ripristino", "lungo", "Tutto risolto"),
            _c("telegram.messaggi.promemoria", "lungo", "Promemoria"),
            _c("telegram.messaggi.riepilogo", "lungo", "Riepilogo del mattino"),
            _c("telegram.messaggi.sbrinamento_inizio", "lungo", "Sbrinamento iniziato", "Segnaposto: {impianto} {esterna} {ora}."),
            _c("telegram.messaggi.sbrinamento_fine", "lungo", "Sbrinamento finito", "Segnaposto: {impianto} {durata} {consumo}."),
        ]},
        {"id": "collegamenti", "titolo": "SmartThings (pompa di calore)", "nota": "I token che si rinnovano da soli (OAuth) non si modificano qui.",
         "campi": [
            _c("smartthings.device_id", "testo", "ID della pompa di calore"),
            _c("smartthings.pat", "segreto", "Token personale (PAT, dura 24 ore)", "Usato solo se l'OAuth non è configurato."),
            _c("generale.intervallo_pdc_secondi", "int", "Leggi la pompa ogni (secondi)", "Minimo 20.", min=20, max=3600),
        ]},
        {"id": "refresh", "titolo": "Dati più freschi (refresh)", "nota": "SmartThings consegna i dati del compressore con 4-5 minuti di ritardo. "
         "Con questa opzione, solo mentre la pompa lavora, il pannello le manda il comando «refresh» (non cambia nessuna impostazione) e a volte "
         "i dati arrivano molto prima. Serve il permesso di comando del token (x:devices:*). È una prova: migliora, non elimina il ritardo.",
         "campi": [
            _c("smartthings.refresh.attivo", "bool", "Chiedi alla pompa di aggiornare i dati mentre lavora"),
            _c("smartthings.refresh.ogni_secondi", "int", "Manda il refresh ogni (secondi)", "Minimo 30, consigliato 60.", min=30, max=600),
        ]},
        {"id": "netatmo", "titolo": "Netatmo (Smarther)", "campi": [
            _c("netatmo.home_id", "testo", "ID della casa"),
            _c("netatmo.room_id", "testo", "ID della stanza (Smarther)"),
            _c("generale.intervallo_netatmo_secondi", "int", "Leggi lo Smarther ogni (secondi)", min=30, max=3600),
        ]},
        {"id": "stazione", "titolo": "Stazione meteo vicina (umidità esterna)", "nota": "Facoltativo. Incolla l'indirizzo dei dati grezzi di una "
         "stazione WeatherLink vicina (non quello della pagina: lo trovi con «python pdc_strumenti.py meteo-prova», che lo stampa pronto da incollare; "
         "se la stazione cambia, rilancialo). Il pannello legge l'umidità ogni 10 minuti e la mostra nella card Esterna della Dashboard. "
         "Vuoto = funzione spenta.", "campi": [
            _c("stazione.url", "testo", "Indirizzo dei dati", "Deve iniziare con https:// e stare su weatherlink.com."),
        ]},
        {"id": "accessoweb", "titolo": "Login del pannello", "campi": [
            _c("generale.sessione_giorni", "int", "Giorni prima di rifare il login", min=1, max=365),
        ]},
        {"id": "umidita", "titolo": "Umidità in casa", "nota": "Soglie dell'umidità (dallo Smarther): sopra la prima il valore diventa giallo "
         "in Dashboard e parte l'avviso Telegram «Umidità alta»; sopra la seconda diventa rosso e parte «Umidità molto alta». "
         "Valgono per tutto: Dashboard, grafico e Telegram. Gli avvisi si possono spegnere in «Quando avvisarti».", "campi": [
            _c("umidita.giallo", "float", "Soglia gialla (%)", "Predefinita 60.", min=20, max=100),
            _c("umidita.rosso", "float", "Soglia rossa (%)", "Predefinita 70. Deve essere maggiore della gialla.", min=20, max=100),
            _c("umidita.minuti", "int", "Avvisa dopo (minuti di fila)", "Evita gli avvisi per un picco breve (una doccia).", min=1, max=1440),
            _c("umidita.rientro_punti", "float", "Rientro sotto la soglia di (punti)", "Evita messaggi continui se l'umidità oscilla attorno alla soglia.", min=0, max=20),
        ]},
        {"id": "tariffa", "titolo": "Costo dell'energia", "nota": "Se metti il prezzo, il pannello mostra quanto costa l'energia "
         "della pompa (stima: kWh della pompa per il prezzo, senza quota fissa).", "campi": [
            _c("tariffa.prezzo_kwh", "float", "Prezzo dell'energia (€ per kWh)", "Per esempio 0.28. Con 0 i costi non si vedono.", min=0, max=5),
        ]},
        {"id": "dispositivi", "titolo": "Dispositivi", "nota": "Per la pagina Dispositivi. Tutto facoltativo. I loghi non li fornisco io: "
         "metti i tuoi file nella cartella \"dati/loghi\" (smarther.svg, samsung.svg, netatmo.svg, bticino.svg; "
         "vanno bene anche .png).", "campi": [
            _c("dispositivi.ip_pdc", "testo", "IP della pompa di calore", "Per es. 192.168.1.50. SmartThings non lo dice: "
               "serve solo per mostrarlo e ricavare il MAC."),
            _c("dispositivi.ip_smarther", "testo", "IP dello Smarther", "Per es. 192.168.1.51."),
            _c("dispositivi.modello_smarther", "scelta", "Modello dello Smarther", "Netatmo dice solo \"BNS\": qui scegli "
               "l'articolo che hai (è scritto sulla scatola o sul retro).", opzioni=MODELLI_SMARTHER),
            _c("dispositivi.loghi_bianchi", "bool", "Mostra i loghi in bianco",
               "Utile se un logo è scuro e sul tema scuro non si legge."),
        ]},
        {"id": "bollitore", "titolo": "Bollitore", "nota": "Serve alla «Salute del bollitore» (pagina Efficienza) per trasformare "
         "il calo di temperatura di notte in watt.", "campi": [
            _c("bollitore.litri", "int", "Litri del bollitore", "Per es. 300.", min=50, max=2000),
        ]},
        {"id": "backup", "titolo": "Backup", "nota": "Ogni notte il pannello copia database e config.json nella cartella \"dati/backup\". "
         " Per rimettere una copia: python pdc_pannello.py ripristina (a pannello fermo).", "campi": [
            _c("backup.attivo", "bool", "Backup automatico notturno"),
            _c("backup.copie", "int", "Quante copie tenere", "Le più vecchie vengono cancellate.", min=1, max=60),
        ]},
        {"id": "storico", "titolo": "Storico dei dati", "nota": "Più indietro dei giorni indicati tengo una media ogni N "
         "minuti invece di un punto al minuto: i grafici vecchi restano ma il database non cresce all'infinito.", "campi": [
            _c("conservazione.dati_completi_giorni", "int", "Giorni con dati dettagliati", min=30, max=3650),
            _c("conservazione.media_minuti", "int", "Dopo, una media ogni (minuti)", min=5, max=240),
        ]},
    ]
    allarmi = []
    for codice, regola in ALLARMI_DEFAULT.items():
        if not isinstance(regola, dict):
            continue
        campi = [_c(f"allarmi.{codice}.attivo", "bool", "Attivo")]
        for k in regola:
            if k in ETICHETTE_ALLARME:
                et, tipo, mn, mx = ETICHETTE_ALLARME[k]
                campi.append(_c(f"allarmi.{codice}.{k}", tipo, et, min=mn, max=mx))
        if codice not in AVVISI_INFORMATIVI:
            et, tipo, mn, mx = ETICHETTE_ALLARME["ritardo_minuti"]
            campi.append(_c(f"allarmi.{codice}.ritardo_minuti", tipo, et, "Se si risolve prima, non ti scrivo.", min=mn, max=mx))
        allarmi.append({"id": codice, "titolo": TITOLI.get(codice, codice), "descrizione": DESCRIZIONI_ALLARME.get(codice, ""),
                        "campi": campi})
    for s in sezioni:  # in quale tab della pagina sta ogni riquadro
        s["tab"] = TAB_SEZIONI.get(s["id"], "dati")
    sezioni += sezioni_plugin()
    return sezioni, allarmi


TAB_SEZIONI = {"telegram": "avvisi", "telegram_file": "avvisi", "telegram_limiti": "avvisi", "orari": "avvisi", "riepilogo": "avvisi", "testi": "avvisi", "umidita": "avvisi", "collegamenti": "collegamenti", "refresh": "collegamenti", "netatmo": "collegamenti", "stazione": "collegamenti", "accessoweb": "collegamenti",
               "dispositivi": "collegamenti", "tariffa": "dati", "bollitore": "dati", "backup": "dati", "storico": "dati"}


def _tutti_i_campi():
    sezioni, allarmi = schema_impostazioni()
    return {c["chiave"]: c for s in sezioni + allarmi for c in s["campi"]}


def _leggi_percorso(cfg, chiave):
    cur = cfg
    for p in chiave.split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur


def _scrivi_percorso(raw, chiave, valore):
    parti = chiave.split(".")
    cur = raw
    for p in parti[:-1]:
        if not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    cur[parti[-1]] = valore


_cache_db = {"t": 0, "v": None}


def info_database(app):
    if time.time() - _cache_db["t"] < 300 and _cache_db["v"]:
        return _cache_db["v"]
    with closing(app.db._c()) as c:
        n, primo = c.execute("SELECT COUNT(*), MIN(ts) FROM misure").fetchone()
    try:
        peso = os.path.getsize(app.db.path) / 1e6
    except OSError:
        peso = None
    v = {"righe": n, "dal": primo, "mb": round(peso, 1) if peso is not None else None}
    _cache_db.update(t=time.time(), v=v)
    return v


def stato_stazione(app):
    """Riga di stato della stazione meteo vicina per Impostazioni: testo breve (vuoto se la funzione è spenta)."""
    i = stazione_info(app)
    if i is None:
        return ""
    if i["umidita"] is None and i["ts"] and not i["errore"]:
        return "La stazione non manda dati nuovi dalle " + datetime.fromtimestamp(i["ts"]).strftime("%H:%M")
    if i["umidita"] is None:
        return "In attesa del primo dato…" if not i["errore"] else "Non riesco a leggere la stazione: " + i["errore"]
    quando = datetime.fromtimestamp(i["ts"]).strftime("%H:%M") if i["ts"] else "?"
    return f"Umidità esterna {num_it(i['umidita'])} % · ultimo dato della stazione alle {quando}" + (" · ultima lettura non riuscita" if i["errore"] else "")


def stato_collegamenti(app, ora):
    cfg = app.cfg.leggi()
    srv = {s.sigla: s for s in app.servizi}

    def sv(sigla):
        s = srv.get(sigla)
        return {"ultimo_ok": s.stato["ultimo_ok"], "messaggio": s.stato["messaggio"]} if s else {"ultimo_ok": None, "messaggio": None}
    tg = getattr(app, "telegram", None) or Telegram(app)
    _, _, err = tg.credenziali()
    nt = cfg["netatmo"]
    return {
        "versione": VERSIONE, "python": sys.version.split()[0], "demo": app.demo,
        "avviato": int(AVVIO), "porta": cfg["generale"]["porta"], "cartella": str(app.base),
        "smartthings": {**sv("st"), "accesso": info_accesso_st(app)},
        "netatmo": {**sv("nt"), "token_scade": int(float(nt.get("expires_at") or 0)) or None,
                    "configurato": bool(nt.get("client_id") and nt.get("refresh_token"))},
        "telegram": {"abilitato": bool(cfg["telegram"]["abilitato"]), "attivo": tg.attivo(), "problema": err,
                     "ultimo_ok": tg.stato["ultimo_ok"], "messaggio": tg.stato["messaggio"]},
        "stazione": stato_stazione(app),
        "database": info_database(app),
        "eventi_aperti": sum(1 for e in app.db.eventi_recenti(50) if e[4] is None),
    }


@api("/api/impostazioni")
def api_impostazioni(app, q):
    ora = int(time.time())
    cfg = app.cfg.leggi()
    sezioni, allarmi = schema_impostazioni()
    for s in sezioni + allarmi:
        for c in s["campi"]:
            v = _leggi_percorso(cfg, c["chiave"])
            if v is None and c.get("default") is not None:
                v = c["default"]
            if c["tipo"] == "segreto":
                c["valore"], c["impostato"] = "", bool(v)
            else:
                c["valore"] = v if v is not None else ("" if c["tipo"] in ("testo", "lungo", "ora", "scelta") else (False if c["tipo"] == "bool" else 0))
    cf = cfg.get("cloudflare") or {}
    base = f"https://{cf['hostname']}" if cf.get("tunnel_token") and cf.get("hostname") else ""      # indirizzo da fuori casa, per i plugin
    for s in sezioni:
        if s.get("plugin"):
            s["plugin"]["base"] = base
    out = cornice(app, ora)
    out.update({"stato": stato_collegamenti(app, ora), "sezioni": sezioni, "allarmi": allarmi,
                "utente": cfg["accesso"].get("utente") or ""})
    return out


def _valida(c, v):
    """-> (valore_pulito, errore)"""
    t = c["tipo"]
    if t == "bool":
        return (v, None) if isinstance(v, bool) else (None, "Valore non valido")
    if t in ("int", "float"):
        try:
            x = float(str(v).replace(",", "."))
        except ValueError:
            return None, "Scrivi un numero"
        if not math.isfinite(x) or x < c.get("min", -1e9) or x > c.get("max", 1e9):
            return None, f"Deve stare tra {c.get('min')} e {c.get('max')}"
        return (int(round(x)) if t == "int" else x), None
    if t == "ora":
        try:
            m = _minuti(str(v))
        except ValueError:
            return None, "Scrivi l'ora come HH:MM"
        if not (0 <= m < 1440) or len(str(v)) != 5:
            return None, "Scrivi l'ora come HH:MM"
        return str(v), None
    if t == "scelta":
        return (v, None) if any(v == o[0] for o in c.get("opzioni", [])) else (None, "Scelta non valida")
    s = str(v if v is not None else "")
    if len(s) > 4000:
        return None, "Testo troppo lungo"
    if t == "lungo":
        try:
            s.format_map(_Sicuro({}))
        except (ValueError, IndexError):
            return None, "Le parentesi graffe non tornano: i segnaposto vanno scritti come {testo}"
        return s, None
    s = s.strip()
    if c["chiave"] == "stazione.url" and s and not stazione_url_valido(s):
        return None, "Scrivi un indirizzo https:// di weatherlink.com"
    if c["chiave"] in ("dispositivi.ip_pdc", "dispositivi.ip_smarther") and s:
        try:
            ipaddress.IPv4Address(s)
        except ValueError:
            return None, "Scrivi un indirizzo IP, per es. 192.168.1.50"
    return s, None


@api_post("/api/impostazioni/salva")
def salva_impostazioni(app, corpo, h):
    if app.demo:
        return {"ok": False, "errore": "In modalità demo non si salva nulla."}
    mod = corpo.get("modifiche")
    campi = _tutti_i_campi()
    if not isinstance(mod, dict) or not mod:
        return {"ok": True, "salvati": 0}
    puliti, errori = {}, {}
    for chiave, v in mod.items():
        c = campi.get(chiave)
        if not c:
            errori[chiave] = "Impostazione sconosciuta"
            continue
        if c["tipo"] == "segreto" and not str(v).strip():
            continue                      # vuoto = non cambiare
        val, err = _valida(c, v)
        if err:
            errori[chiave] = err
        else:
            puliti[chiave] = val
    if "umidita.giallo" in puliti or "umidita.rosso" in puliti:
        cu = app.cfg.leggi().get("umidita") or {}
        g = puliti.get("umidita.giallo", cu.get("giallo", 60))
        r = puliti.get("umidita.rosso", cu.get("rosso", 70))
        if float(r) <= float(g) and "umidita.rosso" not in errori and "umidita.giallo" not in errori:
            errori["umidita.rosso"] = "Deve essere maggiore della soglia gialla."
    if errori:
        return {"ok": False, "errori": errori}

    def scrivi(raw):
        for k, v in puliti.items():
            _scrivi_percorso(raw, k, v)
    app.cfg.aggiorna(scrivi)
    log.info("Impostazioni modificate dal pannello: %s", ", ".join(sorted(k for k in puliti if campi[k]["tipo"] != "segreto"))
             + (" (+ segreti)" if any(campi[k]["tipo"] == "segreto" for k in puliti) else ""))
    if any(k.startswith("plugin.") for k in puliti):
        plugin_sincronizza(app)
    riavvio = [k for k in puliti if k.startswith("generale.")]
    return {"ok": True, "salvati": len(puliti), "riavvio": bool(riavvio)}


@api_post("/api/impostazioni/telegram-prova")
def prova_telegram(app, corpo, h):
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo non si invia nulla."}
    tg = getattr(app, "telegram", None) or Telegram(app)
    _, _, err = tg.credenziali()
    if err:
        return {"ok": False, "messaggio": err}
    ok, msg = tg.invia_ora(tg.formatta("prova", "prova", "info", ""))
    return {"ok": ok, "messaggio": "Messaggio inviato: guarda Telegram." if ok else msg}


@api_post("/api/impostazioni/password")
def cambia_password(app, corpo, h):
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo non si cambia la password."}
    ip = ip_client(h)
    if app.limite.bloccato(ip):
        return {"ok": False, "messaggio": "Troppi tentativi. Riprova più tardi."}
    acc = app.cfg.leggi()["accesso"]
    if not verifica_password(str(corpo.get("attuale", "")), acc.get("password_hash") or ""):
        app.limite.fallito(ip)
        time.sleep(1.0)
        return {"ok": False, "messaggio": "La password attuale non è corretta."}
    nuova = str(corpo.get("nuova", ""))
    if len(nuova) < PASSWORD_MIN:
        return {"ok": False, "messaggio": f"La nuova password deve avere almeno {PASSWORD_MIN} caratteri."}
    nuovo_hash = hash_password(nuova)
    utente = str(corpo.get("utente", "")).strip() or acc.get("utente")
    app.cfg.aggiorna(lambda raw: raw.setdefault("accesso", {}).update(password_hash=nuovo_hash, utente=utente, password=""))
    app.sessioni.elimina_altre(h._cookie("pdc_sid"))
    app.limite.ok(ip)
    log.info("Password del pannello cambiata dal pannello (le altre sessioni sono state chiuse)")
    return {"ok": True, "messaggio": "Fatto. Gli altri dispositivi dovranno rifare il login."}


# --------------------------------------------------------------------------
# Registro del pannello (Impostazioni > Registro): ultime righe del log, senza segreti
# --------------------------------------------------------------------------

_SEGRETI = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=\-]{8,}"), r"\1***"),
    (re.compile(r"(?i)((?:access_token|refresh_token|client_secret|secret|password|passwd|pat|token|api_key|apikey)[\"']?\s*[:=]\s*[\"']?)[^\s,\"'}&]+"), r"\1***"),
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_\-]{30,}\b"), "***"),
    (re.compile(r"[A-Za-z0-9_\-]{40,}"), "***"),
]


def pulisci_riga(t):
    for rx, sost in _SEGRETI:
        t = rx.sub(sost, t)
    return t


def leggi_coda_log(percorso, n=120, max_byte=300_000):
    """Ultime n righe del log: [{livello, testo}]. Le righe senza data (traceback) ereditano il livello della precedente."""
    try:
        with open(percorso, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - max_byte))
            testo = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    righe = testo.splitlines()[-n:]
    rx, liv, out = re.compile(r"^\d\d/\d\d \d\d:\d\d:\d\d (\w+) "), "INFO", []
    for r in righe:
        m = rx.match(r)
        if m:
            liv = m.group(1)
        out.append({"livello": liv, "testo": pulisci_riga(r)})
    return out


@api("/api/registro")
def api_registro(app, q):
    try:
        n = max(20, min(400, int(q.get("righe", ["150"])[0])))
    except ValueError:
        n = 150
    base = app.base

    def peso(p):
        try:
            return os.path.getsize(p)
        except OSError:
            return 0
    dbp = str(app.db.path)
    cfg_b = app.cfg.leggi().get("backup") or {}
    cart = cartella_backup(app)
    copie = sorted(cart.glob("pdc-*.db")) if cart.is_dir() else []
    return {
        "righe": leggi_coda_log(base / "log" / "pdc_pannello.log", n),
        "db_byte": peso(dbp) + peso(dbp + "-wal"), "log_byte": peso(base / "log" / "pdc_pannello.log"),
        "backup_ts": float(app.db.meta_get("backup_ts", 0) or 0) or None,
        "backup_copie": len(copie), "backup_attivo": bool(cfg_b.get("attivo", True)),
        "versione": VERSIONE, "avviato": int(AVVIO), "ora": int(time.time()),
    }


@api_post("/api/database/reset")
def reset_database(app, corpo, h):
    """Azzera i dati raccolti (misure, stati, cicli, anomalie). Restano config.json, la password e i login.
    Prima di toccare qualcosa fa una copia del database; se la copia non riesce non cancella niente."""
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo non si azzera niente."}
    ip = ip_client(h)
    if app.limite.bloccato(ip):
        return {"ok": False, "messaggio": "Troppi tentativi. Riprova più tardi."}
    if str(corpo.get("conferma", "")).strip() != "RESETTA":
        return {"ok": False, "messaggio": "Per confermare scrivi esattamente RESETTA (tutto maiuscolo)."}
    acc = app.cfg.leggi()["accesso"]
    if not verifica_password(str(corpo.get("password", "")), acc.get("password_hash") or ""):
        app.limite.fallito(ip)
        time.sleep(1.0)
        return {"ok": False, "messaggio": "La password non è corretta: non ho cancellato niente."}
    cart = cartella_backup(app)
    copia = cart / f"prima-del-reset-{time.strftime('%Y%m%d-%H%M%S')}.db"
    try:
        cart.mkdir(parents=True, exist_ok=True)
        with closing(app.db._c()) as src, closing(sqlite3.connect(str(copia))) as dst:
            src.backup(dst)
        if not copia.exists() or copia.stat().st_size < 4096:
            raise OSError("la copia è vuota")
    except Exception as e:
        copia.unlink(missing_ok=True)
        log.error("Reset database annullato: copia non riuscita (%s)", e)
        return {"ok": False, "messaggio": f"Non sono riuscito a fare la copia di sicurezza ({e}): non ho cancellato niente."}
    with app.db.lock, closing(app.db._c()) as c, c:
        for tab in ("misure", "stati", "ultimo", "ciclo", "eventi", "finestre"):
            c.execute(f"DELETE FROM {tab}")
        c.execute("DELETE FROM meta WHERE k IN ('ciclo_ultimo_ts', 'tg_notificati', 'fin_soglie')")
    try:
        vacuum(app.db)
    except Exception:
        log.exception("reset: compattazione non riuscita (non è grave)")
    log.warning("DATABASE AZZERATO dal pannello (da %s). Copia di sicurezza: %s", ip, copia.name)
    for s in app.servizi:
        s.sveglia.set()     # rileggi subito: i primi dati ricompaiono in pochi secondi
    return {"ok": True, "messaggio": "Database azzerato. I dati ricominciano da adesso.", "copia": copia.name}
#@@PARTE cloudflare@@
# ==========================================================================
# PEZZO 6: ACCESSO DA INTERNET CON UN TUNNEL CLOUDFLARE (procedura guidata)
# ==========================================================================

import subprocess

CF_API = "https://api.cloudflare.com/client/v4"
CF_DOWNLOAD = "https://github.com/cloudflare/cloudflared/releases/latest/download/"
TITOLI["cf_tunnel"] = "Tunnel Cloudflare"
TITOLI["cf_esterno"] = "Pannello raggiungibile da internet"

CLOUDFLARE_DEFAULT = {
    "_nota": "Compilato dalla procedura guidata (Impostazioni > Accesso da internet). Il token del tunnel permette solo di "
             "far girare QUEL tunnel: tieni comunque config.json riservato.",
    "attivo": False,
    "avvio_automatico": True,
    "hostname": "", "nome_tunnel": "", "tunnel_id": "", "account_id": "", "zone_id": "",
    "tunnel_token": "", "porta_servizio": 0,
    "percorso_cloudflared": "",
    "protocollo": "auto",
}


class ErroreCF(Exception):
    pass


def cf_chiama(token, metodo, percorso, corpo=None, query=None):
    """Una chiamata all'API Cloudflare -> result. Solleva ErroreCF con un messaggio in italiano."""
    url = CF_API + percorso + (("?" + urlencode(query)) if query else "")
    dati = json.dumps(corpo).encode() if corpo is not None else None
    h = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if dati is not None:
        h["Content-Type"] = "application/json"
    try:
        code, js = http_json(metodo, url, h, dati, timeout=30)
    except ErroreAPI as e:
        raise ErroreCF(f"Non riesco a raggiungere Cloudflare ({e.messaggio}).")
    if js.get("success") is True:
        return js.get("result")
    errs = js.get("errors") or []
    testo = "; ".join(f"{e.get('message', '?')} (codice {e.get('code', '?')})" for e in errs) or f"errore HTTP {code}"
    if code in (401, 403) or any(e.get("code") in (10000, 9109, 9106, 9103) for e in errs):
        raise ErroreCF("Cloudflare rifiuta il token: scaduto, copiato male o senza tutti i permessi richiesti. "
                       f"Dettaglio: {testo}")
    if code == 429:
        raise ErroreCF("Cloudflare dice di rallentare (troppe richieste): riprova fra un minuto.")
    raise ErroreCF(f"Cloudflare: {testo}")


def _percorso_exe(app, cfg=None):
    c = (cfg or app.cfg.leggi())["cloudflare"]
    if c.get("percorso_cloudflared"):
        p = Path(c["percorso_cloudflared"])
        return p if p.is_absolute() else app.base / p
    nome = "cloudflared.exe" if os.name == "nt" else "cloudflared"
    locale = app.base / "cloudflared" / nome
    if locale.exists():
        return locale
    import shutil
    trovato = shutil.which("cloudflared")
    return Path(trovato) if trovato else locale


def _url_download():
    macchina = (os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or "").lower()
    import platform
    m = (platform.machine() or macchina).lower()
    if sys.platform.startswith("win"):
        return CF_DOWNLOAD + ("cloudflared-windows-386.exe" if m in ("x86", "i386", "i686") else "cloudflared-windows-amd64.exe")
    if sys.platform.startswith("linux"):
        return CF_DOWNLOAD + ("cloudflared-linux-arm64" if m in ("aarch64", "arm64") else "cloudflared-linux-amd64")
    return None


def scarica_cloudflared(app):
    url = _url_download()
    if not url:
        raise ErroreCF("Su questo sistema non so scaricare cloudflared da solo: installalo a mano e scrivi il percorso in "
                       "config.json (cloudflare -> percorso_cloudflared).")
    dest = app.base / "cloudflared" / ("cloudflared.exe" if os.name == "nt" else "cloudflared")
    dest.parent.mkdir(exist_ok=True)
    tmp = dest.with_name(dest.name + ".scarico")
    try:
        req = Request(url, headers={"User-Agent": "pdc-pannello"})
        with urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            tot = 0
            while True:
                blocco = r.read(1 << 16)
                if not blocco:
                    break
                tot += len(blocco)
                if tot > 150_000_000:
                    raise ErroreCF("Il file scaricato e' troppo grande: lo scarto.")
                f.write(blocco)
    except (URLError, OSError) as e:
        tmp.unlink(missing_ok=True)
        raise ErroreCF(f"Download non riuscito ({getattr(e, 'reason', e)}). Controlla internet e il firewall, oppure scarica "
                       "cloudflared a mano da github.com/cloudflare/cloudflared/releases e metti il percorso in config.json.")
    if tot < 5_000_000:
        tmp.unlink(missing_ok=True)
        raise ErroreCF("Il file scaricato e' troppo piccolo per essere cloudflared.")
    if os.name == "nt":
        with open(tmp, "rb") as f:
            if f.read(2) != b"MZ":
                tmp.unlink(missing_ok=True)
                raise ErroreCF("Il file scaricato non e' un programma Windows valido.")
    os.replace(tmp, dest)
    if os.name != "nt":
        dest.chmod(0o755)
    return versione_cloudflared(dest)


def versione_cloudflared(exe):
    try:
        r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or r.stderr).strip().splitlines()[0][:80]
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


class TunnelCF:
    """Tiene acceso cloudflared (processo figlio, senza finestra) e lo rilancia se si ferma."""

    def __init__(self, app):
        self.app = app
        self.proc = None
        self.voluto = False
        self.thread = None
        self.lock = threading.RLock()
        self.log_path = cartella_log(app.base) / "cloudflared.log"
        self.pid_path = app.base / "cloudflared" / "cloudflared.pid"
        try:
            self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.partito_il = 0
        self.fallimenti = 0
        self.fermo = threading.Event()
        self.ultimo_errore = None
        self.esterno_ts = 0       # ultimo controllo dall'esterno
        self.esterno_ko = 0       # controlli falliti di fila

    # ----- ciclo di vita -----
    def _cfg(self):
        return self.app.cfg.leggi()["cloudflare"]

    def pronto(self):
        c = self._cfg()
        return bool(c["tunnel_token"] and c["hostname"] and _percorso_exe(self.app).exists())

    def _pulisci_vecchio(self):
        """Se una copia precedente del pannello e' stata chiusa a forza, il suo cloudflared puo' essere rimasto."""
        try:
            pid = int(self.pid_path.read_text().strip())
        except (OSError, ValueError):
            return
        try:
            if os.name == "nt":
                # l'output di tasklist usa la tabella di caratteri di Windows (non UTF-8, ha le lettere accentate): lo leggo
                # come byte e cerco solo il nome del programma, cosi' nessun carattere strano puo' far fallire l'avvio
                r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                   timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if b"cloudflared" in (r.stdout or b"").lower():
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=15,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                if "cloudflared" in Path(f"/proc/{pid}/cmdline").read_text().lower():
                    os.kill(pid, 15)
        except Exception as e:      # ripulire un vecchio processo non deve mai impedire al pannello di partire
            log.warning("cloudflared: non riesco a controllare il processo vecchio (%s)", e)
        try:
            self.pid_path.unlink(missing_ok=True)
        except OSError:
            pass

    def avvia(self):
        with self.lock:
            if self.app.demo:
                raise ErroreCF("In modalita' demo il tunnel non parte.")
            c = self._cfg()
            exe = _percorso_exe(self.app)
            if not c["tunnel_token"]:
                raise ErroreCF("Il tunnel non e' ancora stato creato: usa la procedura guidata.")
            if not exe.exists():
                raise ErroreCF("Manca il programma cloudflared: scaricalo dalla procedura guidata.")
            self.voluto = True
            self.app.cfg.aggiorna(lambda r: r.setdefault("cloudflare", {}).update(attivo=True))
            if self.proc and self.proc.poll() is None:
                return
            self._lancia(exe, c)
            if not self.thread or not self.thread.is_alive():
                self.fermo.clear()
                self.thread = threading.Thread(target=self._guardia, daemon=True, name="tunnel-cf")
                self.thread.start()

    def _lancia(self, exe, c):
        self._pulisci_vecchio()
        env = dict(os.environ)
        env["TUNNEL_TOKEN"] = c["tunnel_token"]      # via ambiente: cosi' non compare nell'elenco dei processi
        args = [str(exe), "tunnel", "--no-autoupdate", "--loglevel", "info"]
        if c.get("protocollo") in ("http2", "quic"):
            args += ["--protocol", c["protocollo"]]
        args.append("run")
        try:
            if self.log_path.exists() and self.log_path.stat().st_size > 500_000:     # oltre 500 KB: si tiene la parte finale, da un inizio di avvio
                dati = self.log_path.read_bytes()[-400_000:]
                i = dati.find(b"\n--- avvio")
                self.log_path.write_bytes(dati[i + 1:] if i >= 0 else dati)
        except OSError:
            pass
        logf = open(self.log_path, "ab")
        logf.write(f"\n--- avvio {time.strftime('%d/%m/%Y %H:%M:%S')} ---\n".encode())
        logf.flush()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            self.proc = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                                         creationflags=flags, cwd=str(self.app.base))
        except OSError as e:
            logf.close()
            raise ErroreCF(f"Non riesco ad avviare cloudflared: {e}")
        logf.close()
        self.partito_il = time.time()
        try:
            self.pid_path.write_text(str(self.proc.pid))
        except OSError:
            pass
        log.info("cloudflared avviato (pid %s)", self.proc.pid)

    def ferma(self, spegni=True):
        with self.lock:
            self.voluto = False
            self.fermo.set()
            if spegni and not self.app.demo:
                try:
                    self.app.cfg.aggiorna(lambda r: r.setdefault("cloudflare", {}).update(attivo=False))
                except ErroreConfig:
                    pass
            p, self.proc = self.proc, None
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    p.kill()
            self.pid_path.unlink(missing_ok=True)
            self.app.eventi.chiudi("cf_tunnel")

    def chiudi_con_il_pannello(self):
        """All'uscita del pannello ferma il processo ma ricorda che il tunnel e' 'attivo'."""
        self.ferma(spegni=False)

    def avvia_se_serve(self):
        c = self._cfg()
        if c["attivo"] and c["avvio_automatico"] and self.pronto():
            try:
                self.avvia()
            except ErroreCF as e:
                log.warning("Tunnel Cloudflare: %s", e)
                self.ultimo_errore = str(e)

    def _guardia(self):
        """Controllo del tunnel: se per un imprevisto (per esempio database non scrivibile) si ferma, riparte."""
        while not self.fermo.is_set():
            try:
                return self._guardia_interna()
            except Exception:
                log.exception("errore nel controllo del tunnel Cloudflare; riprovo tra 30 secondi")
                self.fermo.wait(30)

    def _guardia_interna(self):
        attesa = 5
        while not self.fermo.wait(5):
            with self.lock:
                if not self.voluto:
                    return
                p = self.proc
                if p is None or p.poll() is not None:
                    self.fallimenti += 1
                    codice = None if p is None else p.returncode
                    self.ultimo_errore = f"cloudflared si e' fermato (codice {codice}). " + self._ultima_riga_problema()
                    log.warning("%s", self.ultimo_errore)
                    if self.fallimenti >= 3:
                        self.app.eventi.apri("cf_tunnel", f"Il tunnel Cloudflare continua a fermarsi: {self._ultima_riga_problema()}", "avviso")
                    try:
                        self._lancia(_percorso_exe(self.app), self._cfg())
                    except ErroreCF as e:
                        self.ultimo_errore = str(e)
                    self.fermo.wait(min(300, attesa))
                    attesa = min(300, attesa * 2)
                    continue
                if self.collegato():
                    self.fallimenti, attesa = 0, 5
                    self.app.eventi.chiudi("cf_tunnel")
                elif time.time() - self.partito_il > 180:
                    self.app.eventi.apri("cf_tunnel", "Il tunnel e' avviato ma non risulta collegato a Cloudflare da oltre 3 minuti. "
                                         + self._ultima_riga_problema(), "avviso")
            try:
                self._controllo_esterno()
            except Exception:
                log.exception("controllo del pannello da internet in errore")

    # ----- il pannello e' raggiungibile da internet? -----
    CONTROLLO_OGNI = 120        # secondi fra un controllo e l'altro
    CONTROLLO_FALLITI = 3       # controlli falliti di fila prima di avvisare (circa 6 minuti)

    def _controllo_esterno(self):
        """Prova il percorso intero, come lo farebbe il tuo iPhone da fuori casa: da qui a Cloudflare e di nuovo al pannello.
        Usa la pagina pubblica manifest.webmanifest (non serve la password). Avvisa solo dopo alcuni fallimenti di fila, per
        non disturbare con un singolo intoppo; quando torna tutto a posto l'avviso si chiude da solo."""
        if self.app.demo or not self.proc or self.proc.poll() is not None or time.time() - self.partito_il < 120:
            return
        if time.time() - self.esterno_ts < self.CONTROLLO_OGNI:
            return
        self.esterno_ts = time.time()
        host = self._cfg()["hostname"]
        if not host:
            return
        motivo = ""
        try:
            code, js = http_json("GET", f"https://{host}/manifest.webmanifest",
                                 {"User-Agent": "pdc-pannello", "Accept": "application/json"}, timeout=15)
            if code == 200 and isinstance(js, dict) and "start_url" in js:
                self.esterno_ko = 0
                self.app.eventi.chiudi("cf_esterno")
                return
            if code in (530, 1033):
                motivo = f"Cloudflare non trova il tunnel (codice {code})"
            elif code in (502, 504, 521, 522, 523, 524):
                motivo = f"Cloudflare risponde ma il pannello no (codice {code})"
            else:
                motivo = f"risposta inattesa (codice {code})"
        except ErroreAPI as e:
            motivo = str(e)
        self.esterno_ko += 1
        if self.esterno_ko >= self.CONTROLLO_FALLITI:
            self.app.eventi.apri("cf_esterno", f"Da internet il pannello non risponde ({host}): {motivo}.", "avviso")

    # ----- stato -----
    def _coda_log(self, n=12000):
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - n))
                testo = f.read().decode("utf-8", "replace")
            return testo[testo.rfind("--- avvio"):] if "--- avvio" in testo else testo
        except OSError:
            return ""

    def collegato(self):
        return "Registered tunnel connection" in self._coda_log()

    def _ultima_riga_problema(self):
        for riga in reversed(self._coda_log().splitlines()):
            if " ERR " in riga or "error" in riga.lower() or "failed" in riga.lower():
                return re.sub(r"^\S+\s+\w+\s+", "", riga).strip()[:220]
        return ""

    def stato(self):
        c = self._cfg()
        exe = _percorso_exe(self.app)
        in_esecuzione = bool(self.proc and self.proc.poll() is None)
        s = {"configurato": bool(c["tunnel_token"] and c["hostname"]), "hostname": c["hostname"],
             "indirizzo": f"https://{c['hostname']}" if c["hostname"] else "", "nome_tunnel": c["nome_tunnel"],
             "avvio_automatico": c["avvio_automatico"], "attivo": c["attivo"],
             "programma": exe.exists(), "in_esecuzione": in_esecuzione,
             "collegato": in_esecuzione and self.collegato(), "problema": self.ultimo_errore if not in_esecuzione else self._ultima_riga_problema() if not self.collegato() else "",
             "porta_diversa": bool(c["porta_servizio"] and int(c["porta_servizio"]) != int(self.app.cfg.leggi()["generale"]["porta"])),
             "porta_servizio": c["porta_servizio"], "demo": self.app.demo}
        return s


def tunnel(app):
    t = getattr(app, "tunnel", None)
    if t is None:
        t = app.tunnel = TunnelCF(app)
    return t


# --------------------------------------------------------------------------
# API della procedura guidata
# --------------------------------------------------------------------------

def _controllo_token(corpo):
    tok = str(corpo.get("token", "")).strip()
    if len(tok) < 20 or any(ch.isspace() for ch in tok):
        raise ErroreCF("Il token sembra incompleto: copialo per intero (senza spazi) dalla pagina di Cloudflare.")
    return tok


def _risposta_errore(e):
    return {"ok": False, "messaggio": str(e)}


@api("/api/cloudflare")
def api_cloudflare(app, q):
    return tunnel(app).stato()


@api_post("/api/cloudflare/verifica")
def cf_verifica(app, corpo, h):
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo la procedura non è disponibile."}
    try:
        tok = _controllo_token(corpo)
        zone = cf_chiama(tok, "GET", "/zones", query={"per_page": 50, "status": "active"}) or []
    except ErroreCF as e:
        return _risposta_errore(e)
    if not zone:
        return {"ok": False, "messaggio": "Il token funziona ma non vede nessun dominio. Nel token, alla voce 'Zone Resources' "
                                          "scegli il tuo dominio (o 'All zones') e aggiungi il permesso Zone > Zone > Read."}
    return {"ok": True, "zone": [{"id": z["id"], "nome": z["name"], "account_id": (z.get("account") or {}).get("id", ""),
                                  "account_nome": (z.get("account") or {}).get("name", "")} for z in zone]}


@api_post("/api/cloudflare/crea")
def cf_crea(app, corpo, h):
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo la procedura non è disponibile."}
    passi = []

    def ok(t):
        passi.append({"t": t, "ok": True})
    tunnel_id = account = None
    tok = None
    try:
        tok = _controllo_token(corpo)
        sub = str(corpo.get("sottodominio", "")).strip().lower()
        nome = str(corpo.get("nome_tunnel", "")).strip() or "pannello-pdc"
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?", sub):
            raise ErroreCF("Il nome del sottodominio può contenere solo lettere minuscole, numeri e trattini (es. pdc).")
        if not re.fullmatch(r"[A-Za-z0-9 _.-]{1,60}", nome):
            raise ErroreCF("Il nome del tunnel può contenere solo lettere, numeri, spazi, trattino, punto e underscore.")
        zona = cf_chiama(tok, "GET", f"/zones/{corpo.get('zone_id', '')}")
        account = (zona.get("account") or {}).get("id")
        if not account:
            raise ErroreCF("Non riesco a capire a quale account appartiene il dominio.")
        host = f"{sub}.{zona['name']}"
        ok(f"Dominio verificato: {zona['name']}")
        esistenti = cf_chiama(tok, "GET", f"/zones/{zona['id']}/dns_records", query={"name": host}) or []
        record = None
        for r in esistenti:
            if r.get("type") == "CNAME" and str(r.get("content", "")).endswith(".cfargotunnel.com"):
                record = r
            else:
                raise ErroreCF(f"Esiste già un record DNS '{r.get('type')}' per {host}: non lo tocco. Scegli un altro sottodominio "
                               "oppure cancella quel record dal pannello DNS di Cloudflare.")
        doppi = cf_chiama(tok, "GET", f"/accounts/{account}/cfd_tunnel", query={"name": nome, "is_deleted": "false"}) or []
        if doppi:
            raise ErroreCF(f"Esiste già un tunnel chiamato '{nome}' nel tuo account: scegli un altro nome.")
        ok("Nome e sottodominio liberi")
        t = cf_chiama(tok, "POST", f"/accounts/{account}/cfd_tunnel", {"name": nome, "config_src": "cloudflare"})
        tunnel_id = t["id"]
        tunnel_token = t.get("token") or cf_chiama(tok, "GET", f"/accounts/{account}/cfd_tunnel/{tunnel_id}/token")
        ok(f"Tunnel '{nome}' creato")
        porta = int(app.cfg.leggi()["generale"]["porta"])
        cf_chiama(tok, "PUT", f"/accounts/{account}/cfd_tunnel/{tunnel_id}/configurations",
                  {"config": {"ingress": [{"hostname": host, "service": f"http://127.0.0.1:{porta}", "originRequest": {}},
                                          {"service": "http_status:404"}]}})
        ok(f"Il tunnel porta {host} al pannello (porta {porta})")
        contenuto = f"{tunnel_id}.cfargotunnel.com"
        dati_dns = {"type": "CNAME", "name": host, "content": contenuto, "proxied": True}
        if record:
            cf_chiama(tok, "PUT", f"/zones/{zona['id']}/dns_records/{record['id']}", dati_dns)
        else:
            cf_chiama(tok, "POST", f"/zones/{zona['id']}/dns_records", dati_dns)
        ok("Record DNS creato")
    except ErroreCF as e:
        if tunnel_id and account and tok:               # a meta' strada: riporto tutto com'era
            try:
                cf_chiama(tok, "DELETE", f"/accounts/{account}/cfd_tunnel/{tunnel_id}")
                passi.append({"t": "Ho annullato il tunnel creato a metà", "ok": True})
            except ErroreCF:
                passi.append({"t": f"Attenzione: il tunnel '{nome}' è rimasto nel tuo account Cloudflare, cancellalo a mano", "ok": False})
        passi.append({"t": str(e), "ok": False})
        return {"ok": False, "messaggio": str(e), "passi": passi}

    def salva(raw):
        raw.setdefault("cloudflare", {}).update(
            hostname=host, nome_tunnel=nome, tunnel_id=tunnel_id, account_id=account, zone_id=zona["id"],
            tunnel_token=tunnel_token, porta_servizio=porta, attivo=False)
    app.cfg.aggiorna(salva)
    ok("Impostazioni salvate (il token di Cloudflare NON è stato conservato)")
    log.info("Tunnel Cloudflare creato per %s", host)
    return {"ok": True, "passi": passi, "indirizzo": f"https://{host}"}


@api_post("/api/cloudflare/scarica")
def cf_scarica(app, corpo, h):
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo non scarico nulla."}
    try:
        v = scarica_cloudflared(app)
    except ErroreCF as e:
        return _risposta_errore(e)
    return {"ok": True, "versione": v or "installato"}


@api_post("/api/cloudflare/avvia")
def cf_avvia(app, corpo, h):
    try:
        t = tunnel(app)
        t.ultimo_errore = None
        t.avvia()
    except ErroreCF as e:
        return _risposta_errore(e)
    return {"ok": True}


@api_post("/api/cloudflare/ferma")
def cf_ferma(app, corpo, h):
    tunnel(app).ferma()
    return {"ok": True}


@api_post("/api/cloudflare/telegram")
def cf_telegram(app, corpo, h):
    """Manda su Telegram l'indirizzo per raggiungere il pannello da fuori casa."""
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo non si invia nulla."}
    c = app.cfg.leggi().get("cloudflare") or {}
    if not c.get("hostname"):
        return {"ok": False, "messaggio": "L'accesso da internet non è ancora configurato."}
    tg = getattr(app, "telegram", None) or Telegram(app)
    _, _, err = tg.credenziali()
    if err:
        return {"ok": False, "messaggio": err}
    nome = esc_html(tg.cfg().get("nome_impianto") or "Pompa di calore")
    ok, msg = tg.invia_ora(f"🔗 <b>{nome}</b>\nPannello da fuori casa:\nhttps://{esc_html(c['hostname'])}")
    return {"ok": ok, "messaggio": "Inviato: guarda Telegram." if ok else msg}


@api_post("/api/cloudflare/riavvia")
def cf_riavvia(app, corpo, h):
    try:
        t = tunnel(app)
        t.ferma(spegni=False)
        t.ultimo_errore = None
        t.avvia()
    except ErroreCF as e:
        return _risposta_errore(e)
    return {"ok": True}


@api_post("/api/cloudflare/opzioni")
def cf_opzioni(app, corpo, h):
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo non si salva nulla."}
    val = bool(corpo.get("avvio_automatico"))
    app.cfg.aggiorna(lambda raw: raw.setdefault("cloudflare", {}).update(avvio_automatico=val))
    return {"ok": True}


@api_post("/api/cloudflare/rimuovi")
def cf_rimuovi(app, corpo, h):
    """Ferma il tunnel e lo toglie dal pannello. Con un token Cloudflare cancella anche DNS e tunnel online."""
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo non si cambia nulla."}
    c = app.cfg.leggi()["cloudflare"]
    tunnel(app).ferma()
    passi, avviso = [], None
    tok = str(corpo.get("token", "")).strip()
    if tok and c["tunnel_id"]:
        try:
            tok = _controllo_token(corpo)
            for r in cf_chiama(tok, "GET", f"/zones/{c['zone_id']}/dns_records", query={"name": c["hostname"]}) or []:
                if r.get("type") == "CNAME" and str(r.get("content", "")).endswith(".cfargotunnel.com"):
                    cf_chiama(tok, "DELETE", f"/zones/{c['zone_id']}/dns_records/{r['id']}")
            passi.append("Record DNS cancellato")
            try:    # le connessioni appena chiuse possono risultare ancora attive per qualche secondo
                cf_chiama(tok, "DELETE", f"/accounts/{c['account_id']}/cfd_tunnel/{c['tunnel_id']}/connections")
            except ErroreCF:
                pass
            cf_chiama(tok, "DELETE", f"/accounts/{c['account_id']}/cfd_tunnel/{c['tunnel_id']}")
            passi.append("Tunnel cancellato da Cloudflare")
        except ErroreCF as e:
            avviso = f"Online non sono riuscito a cancellare tutto ({e}). Puoi farlo a mano dal sito di Cloudflare."
    elif c["tunnel_id"]:
        avviso = ("Ho spento il tunnel qui, ma su Cloudflare restano il tunnel e il record DNS: cancellali dal loro sito "
                  "(Zero Trust > Networks > Tunnels, e DNS del dominio) oppure rifai questo passaggio dando un token.")
    app.cfg.aggiorna(lambda raw: raw.setdefault("cloudflare", {}).update(
        hostname="", nome_tunnel="", tunnel_id="", account_id="", zone_id="", tunnel_token="", porta_servizio=0, attivo=False))
    log.info("Tunnel Cloudflare rimosso dal pannello")
    return {"ok": True, "passi": passi, "avviso": avviso}


def avvia_tunnel_se_serve(app):
    if not app.demo:
        tunnel(app).avvia_se_serve()


aggiungi_default_pezzi()
#@@PARTE manutenzione@@
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


def cartella_backup(app):
    """La cartella dei backup (config backup.cartella, di solito dati/backup), come percorso assoluto."""
    cart = Path((app.cfg.leggi().get("backup") or BACKUP_DEFAULT).get("cartella") or "dati/backup")
    return cart if cart.is_absolute() else app.base / cart


def fai_backup(app):
    """Copia coerente del database (anche mentre il pannello lavora) + config.json. -> (cartella, [file creati])."""
    c = app.cfg.leggi().get("backup") or BACKUP_DEFAULT
    cart = cartella_backup(app)
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
        cart = cartella_backup(app)
        copie = (sorted(list(cart.glob("pdc-*.db")) + list(cart.glob("prima-del-reset-*.db")),
                        key=lambda f: f.stat().st_mtime, reverse=True) if cart.is_dir() else [])
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
#@@PARTE dispositivi@@
# ==========================================================================
# PEZZO 8 (v0.9): PAGINA DISPOSITIVI (pompa di calore e Smarther: identita', versioni, rete, storico delle versioni)
# ==========================================================================

LOGHI_NOMI = ("smarther", "samsung", "netatmo", "bticino")
LOGHI_ESTENSIONI = ("svg", "png", "webp", "jpg", "jpeg")


def elenco_loghi(app):
    """I loghi che l'utente ha messo nella cartella dati/loghi: {nome: url}. Non ne fornisco io."""
    out = {}
    for nome in LOGHI_NOMI:
        for est in LOGHI_ESTENSIONI:
            f = app.base / "dati" / "loghi" / f"{nome}.{est}"
            try:
                if f.is_file():
                    out[nome] = f"/loghi/{nome}.{est}?v={int(f.stat().st_mtime)}"
                    break
            except OSError:
                pass
    return out


# --------------------------------------------------------------------------
# MAC dall'IP (best-effort: legge la tabella ARP del sistema)
# --------------------------------------------------------------------------

_RE_MAC = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{2}([:-])(?:[0-9a-fA-F]{2}\2){4}[0-9a-fA-F]{2})(?![0-9a-fA-F])")
_cache_mac = {}
MAC_RICORDO = 7 * 86400     # un MAC trovato non cambia quasi mai: si ricorda una settimana (cambiando l'IP in Impostazioni si rilegge subito)
MAC_RIPROVA = 120           # se non c'e' (dispositivo spento) si riprova presto


def _mac_normale(testo):
    m = _RE_MAC.search(testo or "")
    if not m:
        return None
    mac = m.group(1).replace("-", ":").upper()
    return None if mac in ("FF:FF:FF:FF:FF:FF", "00:00:00:00:00:00") else mac


def mac_da_righe(righe, ip):
    """Cerca nelle righe dell'output di 'arp -a' / '/proc/net/arp' quella dell'IP dato."""
    for riga in righe:
        parti = riga.split()
        if parti and parti[0].strip("()") == ip:
            mac = _mac_normale(riga)
            if mac:
                return mac
        elif f"({ip})" in riga:                        # formato Linux/macOS di 'arp -a'
            mac = _mac_normale(riga)
            if mac:
                return mac
    return None


def mac_da_ip(ip):
    """MAC di un dispositivo della rete locale, oppure None. Nessuna libreria esterna: 'arp' del sistema."""
    ora = time.time()
    c = _cache_mac.get(ip)
    if c and ora - c[0] < (MAC_RICORDO if c[1] else MAC_RIPROVA):
        return c[1]
    mac = None
    try:
        # una "bussata" UDP fa popolare la tabella ARP anche se il dispositivo non risponde
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        try:
            s.sendto(b"\x00", (ip, 9))
        except OSError:
            pass
        finally:
            s.close()
        time.sleep(0.15)
        import subprocess
        if os.name == "nt":
            r = subprocess.run(["arp", "-a", ip], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                               creationflags=0x08000000)          # CREATE_NO_WINDOW
            mac = mac_da_righe(r.stdout.splitlines(), ip)
        else:
            try:
                mac = mac_da_righe(Path("/proc/net/arp").read_text().splitlines(), ip)
            except OSError:
                pass
            if not mac:
                r = subprocess.run(["arp", "-n", ip], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
                mac = mac_da_righe(r.stdout.splitlines(), ip) or _mac_normale(r.stdout)
    except Exception:
        mac = None
    _cache_mac[ip] = (ora, mac)
    return mac


# --------------------------------------------------------------------------
# Costruzione delle schede
# --------------------------------------------------------------------------

def _riga(et, v=None, k="t", cls="", nota=""):
    """Una riga: k = t testo, c testo con pulsante Copia, d data (v = epoch), b etichetta colorata (cls)."""
    return {"et": et, "v": v if v not in ("", None) else None, "k": k, "cls": cls, "nota": nota}


WIFI_SMARTHER = ((56, "ottima", "si"), (71, "buona", "si"), (86, "sufficiente", "warn"), (999, "debole", "off"))
MODI_SMARTHER = {"schedule": "Programma", "home": "Programma (in casa)", "manual": "Manuale", "away": "Assente",
                 "hg": "Antigelo", "off": "Spento", "max": "Massimo"}

# dal catalogo BTicino "Smarther2 with Netatmo": dati comuni a tutti gli articoli
CATALOGO_SMARTHER = (
    ("Alimentazione", "110-240 V~ 50/60 Hz, 2 W max"),
    ("Uscita", "1 contatto in scambio libero da potenziale, 5(2) A"),
    ("Regolazione", "5-40 °C, passi di 0,5 °C"),
    ("Wi-Fi", "802.11 b/g/n, 2,4 GHz, WPA/WPA2"),
    ("Funzioni sul termostato", "Temperatura manuale e Boost (30, 60 o 90 minuti)"),
    ("Senza internet", "Continua a seguire il programma impostato (la app non funziona da remoto)"),
)
CATALOGO_ARTICOLI = {
    "XW8002": ("Da incasso (scatole 503E o tonde art. 500; cartongesso PB503/PB502)", "Bianco, nero o sabbia (secondo l'articolo)"),
    "XM8002": ("Da incasso (scatole 503E o tonde art. 500; cartongesso PB503/PB502)", "Bianco, nero o sabbia (secondo l'articolo)"),
    "XG8002": ("Da incasso (scatole 503E o tonde art. 500; cartongesso PB503/PB502)", "Bianco, nero o sabbia (secondo l'articolo)"),
    "XW8002W": ("Da parete (viti e tasselli)", "Solo bianco"),
}


def _stato_connessione(ult, ora, serie_stato, valore_ok, serie_dato, tol):
    """-> (testo, classe) per l'etichetta Online/Offline."""
    r = ult.get(serie_stato)
    d = ult.get(serie_dato)
    if r and r[3] is not None and r[3] != valore_ok:
        return "Offline", "off"
    if d and ora - d[1] > tol:
        return "Dati non recenti", "warn"
    if r and r[3] == valore_ok:
        return "Online", "si"
    return ("Online", "si") if d and ora - d[1] <= tol else ("—", "no")


def _riga_rete(app, ip, mac_noto, etichetta_mac="MAC"):
    righe = []
    if not ip:
        righe.append(_riga("IP", None, nota="Non impostato: scrivilo in Impostazioni → Dispositivi."))
        if mac_noto:
            righe.append(_riga(etichetta_mac, mac_noto, "c"))
        return righe
    righe.append(_riga("IP", ip, "c"))
    mac = mac_da_ip(ip)
    if mac_noto and not mac:
        righe.append(_riga(etichetta_mac, mac_noto, "c"))
    elif mac:
        righe.append(_riga(etichetta_mac, mac, "c"))
    else:
        righe.append(_riga(etichetta_mac, None, nota="Non lo trovo nella rete: controlla l'IP e che il dispositivo sia acceso."))
    return righe


def _versioni_pdc(testo):
    try:
        voci = json.loads(testo)
    except (TypeError, ValueError):
        return []
    out, n_fw = [], 0
    for voce in voci:
        if not isinstance(voce, list) or len(voce) < 2:
            continue
        tipo, ver, descr = voce[0], voce[1], (voce[2] if len(voce) > 2 else "")
        if tipo == "Firmware":
            n_fw += 1
            et = f"Firmware scheda {n_fw}"
        else:
            et = "Software" if tipo == "Software" else str(tipo or "Versione")
        out.append(_riga(et + (f" ({descr})" if descr and descr != "Version" else ""), ver))
    return out


def _scheda_pdc(app, ult, cfg, ora, tol):
    def t(s):
        r = ult.get(s)
        return r[3] if r else None
    testo, cls = _stato_connessione(ult, ora, "pdc.salute", "online", "acs.temperatura", tol)
    anno = t("info.pdc.anno")
    try:
        anno = str(2000 + int(anno)) if anno else None
    except ValueError:
        pass
    agg = t("info.pdc.aggiornamento")
    gruppi = [
        {"titolo": "Identità", "righe": [
            _riga("Nome", t("info.pdc.nome")),
            _riga("Produttore", "Samsung Electronics"),
            _riga("Modello", t("info.pdc.binary"), "c"),
            _riga("Tipo", t("info.pdc.descrizione")),
            _riga("Anno di rilascio", anno),
            _riga("Numero di serie", t("info.pdc.seriale"), "c",
                  nota="" if t("info.pdc.seriale") else "SmartThings non lo comunica."),
            _riga("Codice modello completo", t("info.pdc.modello"), "c"),
        ]},
        {"titolo": "Versioni", "righe": [
            _riga("Firmware modulo Wi-Fi", t("info.pdc.firmware")),
            *_versioni_pdc(t("info.pdc.versioni")),
            _riga("Piattaforma", t("info.pdc.piattaforma")),
            _riga("Driver SmartThings", t("info.pdc.driver")),
            _riga("Aggiornamento disponibile",
                  "Sì" if agg == "true" else "No" if agg == "false" else None, "b",
                  "warn" if agg == "true" else "no"),
        ]},
        {"titolo": "Identificativi", "righe": [
            _riga("ID SmartThings", cfg["smartthings"].get("device_id") or None, "c"),
            _riga("ID OCF", t("info.pdc.id_ocf"), "c"),
            _riga("ID profilo", t("info.pdc.vid"), "c"),
            _riga("Registrata su SmartThings", int(t("info.pdc.registrata")) if (t("info.pdc.registrata") or "").isdigit() else None, "d"),
        ]},
        {"titolo": "Rete", "righe": _riga_rete(app, cfg["dispositivi"].get("ip_pdc", ""), None)},
    ]
    r = ult.get("acs.temperatura")
    return {"id": "pdc", "titolo": "Pompa di calore", "sotto": "Samsung EHS · via SmartThings",
            "loghi": ["samsung"], "marca": "Samsung",
            "stato": {"testo": testo, "cls": cls}, "ultimo_dato": r[1] if r else None, "gruppi": gruppi}


def _scheda_smarther(app, ult, cfg, ora, tol):
    def t(s):
        r = ult.get(s)
        return r[3] if r else None

    def num(s):
        r = ult.get(s)
        return r[2] if r else None
    dc = cfg["dispositivi"]
    testo, cls = _stato_connessione(ult, ora, "casa.raggiungibile", "si", "casa.temperatura", tol * 2)
    w = num("casa.wifi")
    if w is None:
        wifi = _riga("Qualità del Wi-Fi", None)
    else:
        q = next(x for x in WIFI_SMARTHER if w <= x[0])
        wifi = _riga("Qualità del Wi-Fi", f"{q[1].capitalize()} ({int(w)})", "b", q[2],
                     "Il numero è quello di Netatmo: più è basso, meglio è.")
    richiesta = num("casa.richiesta_calore")
    mod = dc.get("modello_smarther", "")
    arti = CATALOGO_ARTICOLI.get(mod)
    id_mod = t("info.nt.id")
    mac_nt = _mac_normale(id_mod) if id_mod else None
    inst = t("info.nt.installato")
    modo = t("casa.modo") or t("info.nt.modo_termico")
    gruppi = [
        {"titolo": "Identità", "righe": [
            _riga("Nome", t("info.nt.nome")),
            dict(_riga("Produttore", "BTicino", "l"), logo="bticino", nota="con Netatmo"),
            _riga("Prodotto", "Smarther2 with Netatmo"),
            _riga("Articolo", mod or None, "c", nota="" if mod else "Sceglilo in Impostazioni → Dispositivi."),
            _riga("Tipo per Netatmo", t("info.nt.tipo")),
            _riga("Stanza", t("info.nt.stanza")),
            _riga("Casa", t("info.nt.casa")),
        ]},
        {"titolo": "Versioni", "righe": [
            _riga("Firmware", t("info.nt.firmware")),
        ]},
        {"titolo": "Stato", "righe": [
            _riga("Richiesta di calore", "Sì" if richiesta == 1.0 else "No" if richiesta == 0.0 else None, "b",
                  "warn" if richiesta == 1.0 else "no"),
            _riga("Modalità", MODI_SMARTHER.get(modo, modo)),
            (lambda vn: _riga("Programma orario", vn[0], nota=vn[1]))(_testo_programma(t("info.nt.programma"))),
            wifi,
        ]},
        {"titolo": "Identificativi", "righe": [
            _riga("ID modulo Netatmo" + (" (è il MAC)" if mac_nt else ""), id_mod, "c"),
            _riga("ID casa Netatmo", cfg["netatmo"].get("home_id") or t("info.nt.casa_id"), "c"),
            _riga("ID stanza Netatmo", cfg["netatmo"].get("room_id") or None, "c"),
            _riga("Installato su Netatmo", int(inst) if (inst or "").isdigit() else None, "d"),
        ]},
        {"titolo": "Rete", "righe": _riga_rete(app, dc.get("ip_smarther", ""), mac_nt)},
        {"titolo": "Dal catalogo BTicino", "righe": (
            ([_riga("Installazione", arti[0]), _riga("Colorazione", arti[1])] if arti else []) +
            [_riga(a, b) for a, b in CATALOGO_SMARTHER])},
    ]
    r = ult.get("casa.temperatura")
    return {"id": "smarther", "titolo": "Termostato", "sotto": "via Netatmo",
            "loghi": ["smarther"], "marca": "Smarther",
            "stato": {"testo": testo, "cls": cls}, "ultimo_dato": r[1] if r else None, "gruppi": gruppi}


GIORNI_BREVI = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]


def _testo_programma(raw):
    """Programma dello Smarther in una frase: 'sempre 20°' oppure i giorni raggruppati con le fasce."""
    if not raw:
        return None, ""
    try:
        p = json.loads(raw)
    except ValueError:
        return None, ""
    if p.get("errore"):
        return "Formato non riconosciuto", ""
    fasce = p.get("fasce") or []
    if not fasce:
        return "Non disponibile", "Lo Smarther non ha restituito nessun programma orario."
    g = lambda v: (f"{v:.1f}".rstrip("0").rstrip(".")).replace(".", ",") + "°"
    nome = f"«{p['nome']}»" if p.get("nome") else "A fasce"
    temps = {x[1] for x in fasce}
    if len(temps) == 1:
        return f"Fisso a {g(fasce[0][1])}", "Stessa temperatura 24 ore su 24, 7 giorni su 7: nessuna fascia."
    giorni = []
    for d in range(7):
        prima = [x for x in fasce if x[0] < d * 1440]
        riga = [(x[0] % 1440, x[1]) for x in fasce if d * 1440 <= x[0] < (d + 1) * 1440]
        if (not riga or riga[0][0] > 0) and (prima or fasce):
            riga.insert(0, (0, (prima[-1] if prima else fasce[-1])[1]))
        giorni.append(" ".join(f"{m // 60:02d}:{m % 60:02d} {g(t)}" for m, t in riga))
    gruppi = []
    for d, txt in enumerate(giorni):
        if gruppi and gruppi[-1][1] == txt and gruppi[-1][0][-1] == d - 1:
            gruppi[-1][0].append(d)
        else:
            gruppi.append(([d], txt))
    parti = []
    for dd, txt in gruppi:
        nome_g = GIORNI_BREVI[dd[0]] if len(dd) == 1 else f"{GIORNI_BREVI[dd[0]]}–{GIORNI_BREVI[dd[-1]]}"
        parti.append(f"{nome_g}: {txt}")
    return nome, " · ".join(parti)


STORICO_SERIE = {
    "info.pdc.firmware": ("Pompa di calore", "Firmware modulo Wi-Fi"),
    "info.pdc.versioni": ("Pompa di calore", "Software e firmware schede"),
    "info.pdc.driver": ("Pompa di calore", "Driver SmartThings"),
    "info.pdc.piattaforma": ("Pompa di calore", "Piattaforma"),
    "info.nt.firmware": ("Smarther", "Firmware"),
}


def _leggibile(serie, v):
    if serie == "info.pdc.versioni":
        try:
            return "; ".join(f"{x[1]}" for x in json.loads(v) if isinstance(x, list) and len(x) > 1)
        except (TypeError, ValueError):
            return v
    return v


def storico_versioni(app, massimo=40):
    """{cambi: [{ts, dispositivo, cosa, prima, dopo}] dal piu' recente, iniziale: [{ts, dispositivo, cosa, valore}]}.
    Il primo valore visto di ogni serie non e' un cambio: finisce in 'iniziale' (cosa c'era quando il pannello ha iniziato)."""
    with closing(app.db._c()) as c:
        righe = c.execute(f"SELECT ts, serie, valore FROM stati WHERE serie IN ({','.join('?' * len(STORICO_SERIE))}) "
                          "ORDER BY ts", tuple(STORICO_SERIE)).fetchall()
    prec, cambi, iniziale = {}, [], []
    for ts, serie, v in righe:
        dev, cosa = STORICO_SERIE[serie]
        if serie in prec:
            cambi.append({"ts": ts, "dispositivo": dev, "cosa": cosa, "prima": _leggibile(serie, prec[serie]),
                          "dopo": _leggibile(serie, v)})
        else:
            iniziale.append({"ts": ts, "dispositivo": dev, "cosa": cosa, "valore": _leggibile(serie, v)})
        prec[serie] = v
    return {"cambi": cambi[::-1][:massimo], "iniziale": iniziale}


# --------------------------------------------------------------------------
# Impostazioni installatore (FSV): quello che la pompa comunica tramite SmartThings, in sola lettura
# --------------------------------------------------------------------------
# Codice: (nome, unita', valore di riferimento, minimo, massimo). Le etichette sono mie, in italiano; il significato
# dei codici viene dal manuale Samsung EHS Mono (riassunto nella guida FSV di Dwellsee). Il valore di riferimento e'
# quello "di fabbrica" di quel manuale: puo' variare da modello a modello, quindi e' solo un'indicazione.
FSV_MENU = {
    "10": "Limiti impostabili dal telecomando",
    "20": "Curva climatica (Water Law)",
    "30": "Acqua calda sanitaria",
    "40": "Riscaldamento",
    "50": "Altro",
}
FSV_CATALOGO = {
    "1011": ("Raffrescamento: massima temperatura dell'acqua impostabile", "°C", 25, 18, 25),
    "1012": ("Raffrescamento: minima temperatura dell'acqua impostabile", "°C", 16, 5, 18),
    "1021": ("Raffrescamento: massima temperatura ambiente impostabile", "°C", 30, 28, 30),
    "1022": ("Raffrescamento: minima temperatura ambiente impostabile", "°C", 18, 18, 28),
    "1031": ("Riscaldamento: massima temperatura dell'acqua impostabile", "°C", 70, 37, 70),
    "1032": ("Riscaldamento: minima temperatura dell'acqua impostabile", "°C", 25, 15, 37),
    "1041": ("Riscaldamento: massima temperatura ambiente impostabile", "°C", 30, 18, 30),
    "1042": ("Riscaldamento: minima temperatura ambiente impostabile", "°C", 16, 16, 18),
    "1051": ("Acqua calda: massima temperatura impostabile", "°C", 55, 50, 70),
    "1052": ("Acqua calda: minima temperatura impostabile", "°C", 40, 30, 40),
    "2011": ("Curva riscaldamento: temperatura esterna, punto freddo", "°C", -10, -20, 5),
    "2012": ("Curva riscaldamento: temperatura esterna, punto caldo", "°C", 15, 10, 20),
    "2021": ("Curva riscaldamento zona 1: temperatura di mandata al punto freddo", "°C", 40, 17, 65),
    "2022": ("Curva riscaldamento zona 1: temperatura di mandata al punto caldo", "°C", 25, 17, 65),
    "2031": ("Curva riscaldamento zona 2: temperatura di mandata al punto freddo", "°C", 50, 17, 65),
    "2032": ("Curva riscaldamento zona 2: temperatura di mandata al punto caldo", "°C", 35, 17, 65),
    "2041": ("Curva climatica del riscaldamento: tipo di impianto", "", 1, 1, 2),
    "2051": ("Curva raffrescamento: temperatura esterna, punto freddo", "°C", 30, 25, 35),
    "2052": ("Curva raffrescamento: temperatura esterna, punto caldo", "°C", 40, 35, 45),
    "2061": ("Curva raffrescamento zona 1: temperatura di mandata più alta", "°C", 25, 5, 25),
    "2062": ("Curva raffrescamento zona 1: temperatura di mandata più bassa", "°C", 18, 5, 25),
    "2071": ("Curva raffrescamento zona 2: temperatura di mandata più alta", "°C", 18, 5, 25),
    "2072": ("Curva raffrescamento zona 2: temperatura di mandata più bassa", "°C", 5, 5, 25),
    "2081": ("Curva climatica del raffrescamento: tipo di impianto", "", 1, 1, 2),
    "2091": ("Termostato esterno 1 (zona 1, pavimento)", "", 0, 0, 4),
    "2092": ("Termostato esterno 2 (zona 2, ventilconvettori)", "", 0, 0, 4),
    "2093": ("Sensore ambiente del telecomando", "", 4, 1, 4),
    "3011": ("Acqua calda: bollitore e criterio di avvio", "", 1, 0, 2),
    "3021": ("Acqua calda: temperatura massima raggiunta dalla sola pompa di calore", "°C", 55, 45, 55),
    "3022": ("Acqua calda: spegnimento della pompa sotto il massimo", "°C", 0, 0, 10),
    "3023": ("Acqua calda: scarto tra spegnimento e riaccensione della pompa", "°C", 5, 5, 30),
    "3024": ("Riscaldamento: tempo minimo di funzionamento tra due cicli acqua calda", "min", 5, 1, 20),
    "3025": ("Acqua calda: durata massima di un ciclo prima di tornare al riscaldamento", "min", 30, 5, 95),
    "3026": ("Riscaldamento: durata massima dopo un ciclo acqua calda", "ore", 3, 0.5, 10),
    "3031": ("Acqua calda: resistenza nel bollitore (BSH)", "", 1, 0, 1),
    "3032": ("Acqua calda: ritardo prima che parta la resistenza del bollitore", "min", 20, 20, 95),
    "3033": ("Acqua calda: spegnimento della resistenza del bollitore sopra l'obiettivo", "°C", 0, 0, 4),
    "3041": ("Disinfezione anti-legionella", "", 1, 0, 1),
    "3042": ("Disinfezione: giorno della settimana", "", 5, 0, 7),
    "3043": ("Disinfezione: ora di avvio", "ore", 23, 0, 23),
    "3044": ("Disinfezione: temperatura da raggiungere", "°C", 70, 40, 70),
    "3045": ("Disinfezione: tempo di mantenimento", "min", 10, 5, 60),
    "3046": ("Disinfezione: durata massima del ciclo", "ore", 8, 1, 24),
    "3051": ("Acqua calda: modo Forzato a tempo", "", 0, 0, 1),
    "3052": ("Acqua calda: durata del modo Forzato", "×10 min", 6, 3, 30),
    "3061": ("Acqua calda: pannello solare", "", 0, 0, 2),
    "3071": ("Valvola a 3 vie: posizione a riposo", "", 0, 0, 1),
    "3081": ("Potenza della resistenza di integrazione (BUH), primo stadio", "kW", 2, 1, 6),
    "3082": ("Potenza della resistenza di integrazione (BUH), secondo stadio", "kW", 2, 0, 6),
    "3083": ("Potenza della resistenza del bollitore (BSH)", "kW", 3, 1, 6),
    "4011": ("Priorità tra riscaldamento e acqua calda", "", 0, 0, 1),
    "4012": ("Priorità al riscaldamento: temperatura esterna sotto la quale vale", "°C", 0, -15, 20),
    "4013": ("Riscaldamento: temperatura esterna oltre la quale si ferma", "°C", 35, 14, 45),
    "4021": ("Resistenza di integrazione (BUH) per il riscaldamento", "", 0, 0, 2),
    "4022": ("Se ci sono due resistenze: quale parte per prima", "", 2, 0, 2),
    "4023": ("Resistenza di integrazione: usata solo con temperatura esterna bassa", "", 1, 0, 1),
    "4024": ("Resistenza di integrazione: temperatura esterna sotto la quale può partire", "°C", 0, -25, 35),
    "4025": ("Sbrinamento: temperatura dell'acqua sotto la quale parte la resistenza", "°C", 15, 10, 55),
    "4031": ("Caldaia di supporto", "", 0, 0, 1),
    "4032": ("Caldaia di supporto: priorità", "", 0, 0, 1),
    "4033": ("Caldaia di supporto: temperatura esterna sotto la quale entra", "°C", -15, -20, 5),
    "4041": ("Valvola miscelatrice", "", 0, 0, 2),
    "4042": ("Miscelatrice: salto di temperatura (ΔT) obiettivo in riscaldamento", "°C", 10, 5, 15),
    "4043": ("Miscelatrice: salto di temperatura (ΔT) obiettivo in raffrescamento", "°C", 10, 5, 15),
    "4044": ("Miscelatrice: fattore di controllo (più alto = più reattiva)", "", 2, 1, 5),
    "4045": ("Miscelatrice: intervallo di controllo (più basso = più reattiva)", "min", 2, 1, 30),
    "4046": ("Miscelatrice: tempo di corsa", "×10 s", 9, 6, 24),
    "4051": ("Pompa di circolazione a velocità variabile", "", 1, 0, 2),
    "4052": ("Pompa a velocità variabile: salto di temperatura (ΔT) obiettivo tra mandata e ritorno", "°C", 5, 2, 8),
    "4053": ("Pompa a velocità variabile: fattore di controllo", "", 2, 1, 3),
    "4061": ("Controllo a due zone (telecomando cablato Samsung)", "", 0, 0, 1),
    "5011": ("Fuori casa: temperatura dell'acqua in raffrescamento", "°C", 25, 5, 25),
    "5012": ("Fuori casa: temperatura ambiente in raffrescamento", "°C", 30, 18, 30),
    "5013": ("Fuori casa: temperatura dell'acqua in riscaldamento", "°C", 15, 15, 55),
    "5014": ("Fuori casa: temperatura ambiente in riscaldamento", "°C", 16, 16, 30),
    "5015": ("Fuori casa: temperatura dell'acqua, raffrescamento con curva climatica zona 1", "°C", 25, 5, 25),
    "5016": ("Fuori casa: temperatura dell'acqua, raffrescamento con curva climatica zona 2", "°C", 25, 5, 25),
    "5017": ("Fuori casa: temperatura dell'acqua, riscaldamento con curva climatica zona 1", "°C", 15, 15, 55),
    "5018": ("Fuori casa: temperatura dell'acqua, riscaldamento con curva climatica zona 2", "°C", 15, 15, 55),
    "5019": ("Fuori casa: temperatura del bollitore", "°C", 30, 30, 70),
    "5021": ("Acqua calda in modo Eco: di quanto si abbassa la temperatura", "°C", 5, 0, 40),
    "5022": ("Acqua calda in modo Eco: funzione risparmio", "", 0, 0, 1),
    "5023": ("Acqua calda in modo Eco: temperatura di riaccensione", "°C", 25, 0, 40),
    "5041": ("Limitazione nei picchi di consumo", "", 0, 0, 1),
    "5042": ("Componenti spenti durante un picco", "", 0, 0, 3),
    "5043": ("Segnale di picco: livello che lo attiva", "", 1, 0, 1),
    "5051": ("Controllo del rapporto di frequenza (0–10 V o Modbus)", "", 0, 0, 1),
    "5061": ("Ripartizione della capacità tra riscaldamento e acqua calda", "", None, None, None),
    "5081": ("Controllo con fotovoltaico", "", 0, 0, 1),
    "5082": ("Fotovoltaico: scarto di temperatura in raffrescamento", "°C", 1, 0, 5),
    "5083": ("Fotovoltaico: scarto di temperatura in riscaldamento", "°C", 1, 0, 5),
    "5091": ("Rete intelligente (smart grid)", "", 0, 0, 1),
    "5092": ("Rete intelligente: aumento della temperatura di riscaldamento", "°C", 2, 2, 5),
    "5093": ("Rete intelligente: aumento della temperatura dell'acqua calda", "°C", 5, 2, 5),
    "5094": ("Rete intelligente: temperatura massima dell'acqua calda", "", 0, 0, 1),
}

# Spiegazioni brevi sotto il nome (dal manuale Samsung EHS Mono / Gen 6 e dalla guida FSV di Dwellsee)
FSV_NOTE = {
    "2041": "Quale curva climatica vale per il riscaldamento: la 1 (pavimento) o la 2 (ventilconvettori).",
    "2091": "Con 0 comanda il telecomando della pompa (metti a 0 sia 2091 sia 2092). Con 1 il compressore segue solo il "
            "termostato; da 2 a 4 segue il termostato o la curva climatica, il primo che scatta, e il numero decide la pompa di "
            "circolo a richiesta finita: ferma (2), accesa (3), 7 minuti ferma e 3 accesa (4).",
    "2092": "Come 2091, per la seconda zona.",
    "2093": "Con 1 il compressore segue solo il sensore ambiente del telecomando; da 2 a 4 segue il sensore o la curva climatica, e la pompa di circolo a richiesta finita fa come in 2091.",
    "3011": "0 = nessun bollitore. 1 = l'acqua calda riparte quando scende alla temperatura di accensione, 2 = a quella di spegnimento.",
    "3021": "Oltre questa temperatura il bollitore si scalda solo con la resistenza, se c'è.",
    "3022": "La pompa si ferma a: massimo (o obiettivo) meno questo valore.",
    "3023": "Isteresi: la pompa riparte quando l'acqua scende di questo valore sotto la temperatura di spegnimento.",
    "3024": "Vale solo quando riscaldamento e acqua calda sono richiesti insieme.",
    "3025": "Vale solo quando riscaldamento e acqua calda sono richiesti insieme: poi si torna al riscaldamento.",
    "3026": "Vale solo quando riscaldamento e acqua calda sono richiesti insieme.",
    "3031": "Serve la resistenza elettrica dentro il bollitore.",
    "3032": "Non conta nei modi Potenza e Forzato. In modo Eco la resistenza non si usa. Tienilo sotto il valore di 3025.",
    "3033": "La resistenza si spegne a: obiettivo più questo valore.",
    "3041": "Richiede la resistenza nel bollitore: riscalda il bollitore ad alta temperatura contro la legionella.",
    "3051": "Con 1 il modo Forzato dell'acqua calda finisce da solo dopo la durata di 3052.",
    "3071": "Dove sta la valvola a 3 vie quando nessuno la comanda: 0 = riscaldamento, 1 = bollitore.",
    "4011": "Con 1 il riscaldamento ha la precedenza solo se fuori fa meno di 4012.",
    "4012": "Conta solo se 4011 = 1.",
    "4023": "Con 1 la resistenza di integrazione parte solo sotto la temperatura esterna di 4024.",
    "4041": "Solo se c'è una valvola miscelatrice nell'impianto.",
    "4051": "Con 2 la pompa di circolazione non supera il 70% della velocità.",
    "4061": "Serve il telecomando cablato Samsung. Non serve con un termostato di terzi e vuole 2091 e 2092 a 0.",
    "5021": "Si toglie dalla temperatura impostata per l'acqua calda quando il modo è Eco.",
    "5041": "Non può essere attivo insieme al controllo con fotovoltaico (5081).",
}
_FSV_SINO = {"3031", "3041", "3051", "4023", "4031", "4061", "5022", "5041", "5051", "5081", "5091"}
_TERM = {0: "Non usato", 1: "Solo il termostato", 2: "Termostato o curva, pompa ferma",
         3: "Termostato o curva, pompa accesa", 4: "Termostato o curva, pompa 7 min ferma e 3 accesa"}
_FSV_VALORI = {
    "2091": _TERM,
    "2092": _TERM,
    "2093": {1: "Solo il sensore ambiente", 2: "Sensore o curva, pompa ferma", 3: "Sensore o curva, pompa accesa",
             4: "Sensore o curva, pompa 7 min ferma e 3 accesa"},
    "3011": {0: "Bollitore non usato", 1: "Parte quando scende alla temperatura di accensione",
             2: "Parte quando scende alla temperatura di spegnimento"},
    "4011": {0: "Priorità all'acqua calda", 1: "Priorità al riscaldamento sotto la temperatura esterna di 4012"},
    "4021": {0: "Nessuna resistenza di integrazione", 1: "Resistenza a due stadi", 2: "Resistenza a uno stadio"},
    "4022": {0: "Partono insieme", 1: "Prima la resistenza di integrazione", 2: "Prima la resistenza del bollitore"},
    "4041": {0: "Non installata", 1: "Controllo con salto di temperatura fisso (4042 e 4043)",
             2: "Controllo con il salto di temperatura della curva climatica"},
    "4051": {0: "Non usata", 1: "Fino al 100% della velocità", 2: "Fino al 70% della velocità"},
    "2041": {1: "Zona 1: pavimento radiante", 2: "Zona 2: ventilconvettori o radiatori"},
    "2081": {1: "Zona 1: pavimento radiante", 2: "Zona 2: ventilconvettori"},
    "3042": {0: "Domenica", 1: "Lunedì", 2: "Martedì", 3: "Mercoledì", 4: "Giovedì", 5: "Venerdì", 6: "Sabato",
             7: "Ogni giorno"},
    "3071": {0: "Verso il riscaldamento", 1: "Verso il bollitore"},
    "5043": {0: "Segnale basso", 1: "Segnale alto"},
    "5094": {0: "Solo pompa di calore (55 °C)", 1: "Pompa e resistenza del bollitore (70 °C)"},
}
_FSV_MOLT = {"×10 min": (10, "min"), "×10 s": (10, "s")}


def _num_it(x):
    if isinstance(x, float) and x == int(x):
        x = int(x)
    return str(x).replace(".", ",")


def _fsv_numero(txt):
    try:
        return float(str(txt).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _fsv_t(codice, grezzo, unita):
    """_fsv_testo per un valore grezzo (il numero lo ricava da solo)."""
    return _fsv_testo(codice, _fsv_numero(grezzo), grezzo, unita)


def _fsv_testo(codice, n, grezzo, unita):
    """Il valore come lo leggerebbe una persona: testo dove il codice e' una scelta, unita' dove c'e'."""
    if n is None:
        return str(grezzo)
    k = int(n) if n == int(n) else None
    if codice in _FSV_VALORI and k in _FSV_VALORI[codice]:
        return f"{_num_it(n)}\u00a0· {_FSV_VALORI[codice][k]}"      # prima il numero: non resta mai solo a capo
    if codice in _FSV_SINO and k in (0, 1):
        return f"{k}\u00a0· {'Sì' if k else 'No'}"
    if unita in _FSV_MOLT:
        m, u = _FSV_MOLT[unita]
        return f"{_num_it(n)} ({_num_it(n * m)} {u})"
    return f"{_num_it(n)} {unita}".strip()


def fsv_opzioni(cod):
    """Se il codice e' una scelta (non un numero libero): [[valore, testo], ...]; altrimenti None."""
    if cod in _FSV_VALORI:
        return [[k, t] for k, t in sorted(_FSV_VALORI[cod].items())]
    if cod in _FSV_SINO:
        return [[0, "No"], [1, "Sì"]]
    return None


def fsv_valore_valido(cod, valore):
    """-> (numero, errore). Si accetta solo cio' che il manuale prevede per quel codice: niente codici ignoti,
    niente valori fuori dai limiti, niente decimali (le impostazioni installatore vanno a passi di 1)."""
    cat = FSV_CATALOGO.get(cod)
    if not cat or cat[3] is None:
        return None, "Questo codice non e' nell'elenco del manuale: da qui non lo modifico."
    try:
        n = float(str(valore).replace(",", "."))
    except (TypeError, ValueError):
        return None, "Il valore non e' un numero."
    if n != int(n):
        return None, "Queste impostazioni vanno a numeri interi."
    n = int(n)
    opz = fsv_opzioni(cod)
    if opz is not None and n not in [o[0] for o in opz]:
        return None, "Valore non previsto per questo codice."
    if not (cat[3] <= n <= cat[4]):
        return None, f"Fuori dai limiti del manuale (da {_num_it(cat[3])} a {_num_it(cat[4])})."
    return n, None


def _cronologia_fsv(storia, richieste, nomi, unita, adesso):
    """Elenco dei cambi: quelli letti dalla pompa (prima -> dopo) e le richieste fatte dal pannello. Una richiesta
    seguita dalla lettura del valore chiesto diventa una sola voce 'dal pannello'; se la lettura non e' ancora
    arrivata resta 'in attesa'. Dal piu' recente."""
    voci, usate = [], set()
    for cod, st in storia.items():
        for i in range(1, len(st)):
            if st[i][1] == st[i - 1][1]:
                continue
            ts, dopo, prima = st[i][0], st[i][1], st[i - 1][1]
            fonte, ip, chiesta = "letto", "", None
            for k, (rts, rprima, rdopo, rip, _) in enumerate(richieste.get(cod, [])):
                if (cod, k) not in usate and rts <= ts <= rts + 172800 and _fsv_numero(rdopo) == _fsv_numero(dopo):
                    usate.add((cod, k))
                    fonte, ip, chiesta = "pannello", rip, rts
                    break
            voci.append({"ts": ts, "id": cod, "nome": nomi.get(cod, "Codice non in elenco"),
                         "prima": _fsv_t(cod, prima, unita.get(cod, "")),
                         "dopo": _fsv_t(cod, dopo, unita.get(cod, "")),
                         "fonte": fonte, "ip": ip, "chiesta": chiesta})
    for cod, lista in richieste.items():
        for k, (rts, rprima, rdopo, rip, _) in enumerate(lista):
            if (cod, k) in usate:
                continue
            attuale = storia.get(cod, [(0, "")])[-1][1]
            fatto = _fsv_numero(attuale) == _fsv_numero(rdopo)
            voci.append({"ts": rts, "id": cod, "nome": nomi.get(cod, "Codice non in elenco"),
                         "prima": _fsv_t(cod, rprima, unita.get(cod, "")),
                         "dopo": _fsv_t(cod, rdopo, unita.get(cod, "")),
                         "fonte": "pannello", "ip": rip, "chiesta": rts,
                         "stato": "gia' uguale" if fatto else ("in attesa" if adesso - rts < 7200 else "non confermata")})
    voci.sort(key=lambda v: -v["ts"])
    return voci[:60]


def impostazioni_installatore(app, ult):
    """Tutti i parametri FSV letti dalla pompa, raggruppati per menu, con la cronologia dei cambi.
    Le modifiche partono da /api/fsv (8_controlli.py); qui si legge soltanto."""
    codici = sorted(k[4:] for k in ult if k.startswith("fsv."))
    with closing(app.db._c()) as c:
        righe = c.execute("SELECT serie, ts, valore FROM stati WHERE serie LIKE 'fsv.%' ORDER BY serie, ts").fetchall()
        mod = c.execute("SELECT serie, ts, valore FROM stati WHERE serie LIKE 'fsvmod.%' ORDER BY ts").fetchall()
    storia = {}
    for serie, ts, v in righe:
        storia.setdefault(serie[4:], []).append((ts, v))
    richieste = {}
    for serie, ts, v in mod:
        parti = (v.split("|") + ["", "", "", ""])[:4]
        if parti[3].startswith("inviato"):   # le richieste rifiutate non sono modifiche: stanno nel registro dei comandi
            richieste.setdefault(serie[7:], []).append((ts, parti[0], parti[1], parti[2], parti[3]))
    gruppi, letto, nomi, unita = {}, 0, {}, {}
    adesso = int(time.time())
    for cod in codici:
        r = ult["fsv." + cod]
        grezzo = r[3] if r[3] not in (None, "") else (_num_it(r[2]) if r[2] is not None else "")
        n = _fsv_numero(grezzo) if grezzo != "" else None
        cat = FSV_CATALOGO.get(cod)
        nome, un, rif, lo, hi = cat if cat else ("Codice non in elenco", "", None, None, None)
        nomi[cod], unita[cod] = nome, un
        riga = {"id": cod, "nome": nome, "nota": FSV_NOTE.get(cod), "v": _fsv_testo(cod, n, grezzo, un), "noto": bool(cat),
                "rif": _fsv_testo(cod, float(rif), rif, un) if rif is not None else None,
                "diverso": bool(rif is not None and n is not None and abs(n - rif) > 1e-9),
                "limiti": f"da {_num_it(lo)} a {_num_it(hi)} {un}".strip().replace(" ", "\u00a0") if lo is not None else None,
                "fuori": bool(lo is not None and n is not None and not (lo - 1e-9 <= n <= hi + 1e-9)),
                "cambio": None, "n": n, "lo": lo, "hi": hi, "unita": un, "opzioni": fsv_opzioni(cod),
                "modificabile": bool(cat and lo is not None and n is not None), "attesa": None}
        st = storia.get(cod, [])
        if len(st) >= 2:
            riga["cambio"] = {"ts": st[-1][0], "prima": _fsv_t(cod, st[-2][1], un)}
        rq = richieste.get(cod, [])
        if rq:   # chiesto dal pannello ma la pompa non l'ha ancora riletto
            rts, rprima, rdopo, rip, _ = rq[-1]
            if n is not None and _fsv_numero(rdopo) != n and (not st or st[-1][0] <= rts) and adesso - rts < 172800:
                riga["attesa"] = {"ts": rts, "dopo": _fsv_t(cod, rdopo, un),
                                  "vecchia": adesso - rts >= 7200}
        letto = max(letto, r[1] or 0)
        menu = cod[:2] if cat else "altri"
        gruppi.setdefault(menu, []).append(riga)
    out = []
    for m in sorted(gruppi, key=lambda x: (x == "altri", x)):
        out.append({"id": m, "titolo": FSV_MENU.get(m, "Altri codici letti dalla pompa"), "righe": gruppi[m]})
    tutte = [r for g in out for r in g["righe"]]
    return {"gruppi": out, "totale": len(tutte), "diversi": sum(1 for r in tutte if r["diverso"]),
            "cambiati": sum(1 for r in tutte if r["cambio"]), "fuori": sum(1 for r in tutte if r["fuori"]),
            "letto": letto,
            "cronologia": sorted(_cronologia_fsv(storia, richieste, nomi, unita, adesso)
                                 + regolazioni_dati(app, ult, adesso)[1], key=lambda v: -v["ts"])[:60]}


# --------------------------------------------------------------------------
# Regolazioni: temperatura e modo dell'acqua calda, modo della pompa (comandi standard di SmartThings)
# --------------------------------------------------------------------------
REGOLAZIONI = {
    "acs_setpoint": {"nome": "Temperatura obiettivo acqua calda", "gruppo": "Acqua calda", "comp": "main",
                     "cap": "thermostatCoolingSetpoint", "cmd": "setCoolingSetpoint", "serie": "acs.setpoint",
                     "tipo": "num", "unita": "°C"},
    "acs_modo": {"nome": "Modo acqua calda", "gruppo": "Acqua calda", "comp": "main", "cap": "airConditionerMode",
                 "cmd": "setAirConditionerMode", "serie": "pdc.modo", "tipo": "txt", "unita": ""},
    "pompa_modo": {"nome": "Modo della pompa", "gruppo": "Pompa", "comp": "INDOOR", "cap": "airConditionerMode",
                   "cmd": "setAirConditionerMode", "serie": "risc.modo", "tipo": "txt", "unita": ""},
}
REG_MODI = {   # valore SmartThings, nome, avviso da mostrare nella conferma
    "acs_modo": [("eco", "Eco", "Scalda con la sola pompa di calore e tiene l'acqua un po' più fredda (vedi FSV 5021)."),
                 ("std", "Standard", ""),
                 ("power", "Potenza", "Scalda più in fretta: la resistenza del bollitore, se c'è, parte subito."),
                 ("force", "Forzato", "Scalda subito al massimo dello sforzo (lo usa anche il Boost). Se l'FSV 3051 è "
                                      "attivo si ferma da solo dopo la durata di 3052.")],
    "pompa_modo": [("heat", "Riscaldamento", ""),
                   ("cool", "Raffrescamento", "Manda acqua FREDDA nel circuito: d'inverno la casa si raffredda."),
                   ("auto", "Automatico", "La pompa sceglie da sola tra caldo e freddo.")],
}


def reg_limiti_acs(ult):
    """Limiti del setpoint dell'acqua calda: sono gli FSV 1052 (minimo) e 1051 (massimo); senza, 40-55."""
    lo, hi = _fsv(ult, "1052"), _fsv(ult, "1051")
    return (int(lo) if lo is not None else 40), (int(hi) if hi is not None else 55)


def reg_testo(cosa, v):
    d = REGOLAZIONI[cosa]
    if v is None or v == "":
        return "—"
    if d["tipo"] == "num":
        try:
            return f"{_num_it(round(float(v), 1))} {d['unita']}".strip()
        except (TypeError, ValueError):
            return str(v)
    return next((n for k, n, _ in REG_MODI.get(cosa, []) if k == v), str(v))


def _reg_uguali(cosa, a, b):
    if REGOLAZIONI[cosa]["tipo"] == "num":
        x, y = _fsv_numero(a), _fsv_numero(b)
        return x is not None and y is not None and abs(x - y) < 1e-9
    return str(a) == str(b)


def regolazioni_dati(app, ult, adesso):
    """Valori attuali, scelte possibili e modifiche in attesa. -> ({cosa: {...}}, cronologia)"""
    with closing(app.db._c()) as c:
        mod = c.execute("SELECT serie, ts, valore FROM stati WHERE serie LIKE 'regmod.%' ORDER BY ts").fetchall()
    richieste = {}
    for serie, ts, v in mod:
        parti = (v.split("|") + ["", "", "", ""])[:4]
        if parti[3].startswith("inviato"):
            richieste.setdefault(serie[7:], []).append((ts, parti[0], parti[1], parti[2]))
    out, cronologia = {}, []
    lo, hi = reg_limiti_acs(ult)
    for cosa, d in REGOLAZIONI.items():
        r = ult.get(d["serie"])
        cur = (r[2] if d["tipo"] == "num" else r[3]) if r else None
        riga = {"cosa": cosa, "nome": d["nome"], "gruppo": d["gruppo"], "valore": cur, "testo": reg_testo(cosa, cur),
                "unita": d["unita"], "attesa": None,
                "opzioni": [[k, n, av] for k, n, av in REG_MODI[cosa]] if cosa in REG_MODI else None,
                "lo": lo if cosa == "acs_setpoint" else None, "hi": hi if cosa == "acs_setpoint" else None,
                "modificabile": cur is not None}
        rq = richieste.get(cosa, [])
        if rq and cur is not None:
            rts, rprima, rdopo, rip = rq[-1]
            if not _reg_uguali(cosa, rdopo, cur) and adesso - rts < 7200:
                riga["attesa"] = {"ts": rts, "dopo": reg_testo(cosa, rdopo)}
        for rts, rprima, rdopo, rip in rq:
            cronologia.append({"ts": rts, "id": d["gruppo"], "nome": d["nome"], "prima": reg_testo(cosa, rprima),
                               "dopo": reg_testo(cosa, rdopo), "fonte": "pannello", "ip": rip, "chiesta": rts})
        out[cosa] = riga
    return out, cronologia


@api("/api/dispositivi")
def api_dispositivi(app, q):
    ora = int(time.time())
    cfg = app.cfg.leggi()
    cfg.setdefault("dispositivi", {})
    ult = app.db.ultimi_dict()
    tol = max(900, int(app.intervallo_pdc() * 3))
    out = cornice(app, ora)
    try:
        boost_riprendi(app)
    except Exception:
        log.exception("boost: ripresa del controllo")
    out.update({
        "schede": [_scheda_pdc(app, ult, cfg, ora, tol), _scheda_smarther(app, ult, cfg, ora, tol)],
        "storico": storico_versioni(app),
        "installatore": impostazioni_installatore(app, ult),
        "regolazioni": regolazioni_dati(app, ult, ora)[0],
        "boost": boost_dati(app, ult, ora),
        "da_casa": (q.get("_casa") or ["1"])[0] == "1",
        "comandi": elenco_comandi(app),
        "loghi": elenco_loghi(app),
        "loghi_bianchi": bool(cfg["dispositivi"].get("loghi_bianchi")),
    })
    return out
#@@PARTE controlli@@
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
                                      "rigeneralo come spiegato in docs/comandi-e-regolazioni.md.")
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
                    + " (se compaiono 403/permessi: rigenera i token con write_smarther, vedi docs/comandi-e-regolazioni.md)")


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
#@@PARTE plugin@@

# ==========================================================================
# PLUGIN: integrazioni opzionali in file esterni (cartella «plugin», accanto a pdc_pannello.py)
# ==========================================================================
# Un plugin e' un file plugin/nome.py che il pannello carica all'avvio (e con «Ricarica i plugin» in Impostazioni > Integrazioni).
# Un errore dentro un plugin non ferma mai il pannello: lo segna e basta. Il plugin gira con gli stessi permessi del pannello,
# quindi nella cartella plugin ci va solo codice di cui ci si fida.
#
# Cosa deve contenere un plugin (INTERFACCIA 1):
#   INFO  = {"titolo": "Alexa", "versione": "1.0", "interfaccia": 1, "descrizione": "..."}
#   CAMPI = [{"chiave": "segreto", "tipo": "segreto", "et": "...", "aiuto": "...", "default": ...}, ...]   (facoltativo)
#   CHANGELOG = [{"v": "1.1", "data": "2026-11-04T18:00", "titolo": "...", "righe": [["n", "Titoletto", "Una frase."], ...]}, ...]   (facoltativo)
#       la cronologia del plugin, la piu' recente in alto: compare in Changelog > Plugin. Tipo riga: n = Novita', m = Migliorato, s = Sistemato.
#   def avvia(ctx): ...        (deve tornare subito; per lavori lunghi si usa un thread)
#   def ferma(ctx): ...        (facoltativa)
# Il campo «attivo» (interruttore) lo aggiunge il pannello: un plugin nuovo parte SPENTO.
# `ctx` (ContestoPlugin) e' tutto quello che il plugin puo' usare del pannello: config, dati, comandi, rotte pubbliche, registro.

PLUGIN_INTERFACCIA = 1
PLUGIN_MAX_FILE = 500_000       # byte
PLUGIN_MAX_CORPO = 65_536       # byte di una richiesta pubblica
PLUGIN_RICHIESTE_MIN = 60       # richieste al minuto, per plugin e per indirizzo
PLUGIN_ATTESA_AVVIO = 10        # secondi concessi a avvia()
_RX_NOME_PLUGIN = re.compile(r"[a-z][a-z0-9_]{0,23}")

PLUGIN = globals().get("PLUGIN") or {}               # nome -> Plug (sopravvive alla ricarica a caldo di questa parte)
PLUGIN_ROTTE = globals().get("PLUGIN_ROTTE") or {}   # "/plugin/nome/percorso" -> (nome, funzione, metodi)
_lock_plugin = globals().get("_lock_plugin") or threading.RLock()
_ritmo_plugin = globals().get("_ritmo_plugin") or {}  # (nome, ip) -> orari delle ultime richieste


class Plug:
    """Un plugin trovato nella cartella: stato = spento | attivo | errore."""
    def __init__(self, nome, file):
        self.nome, self.file = nome, file
        self.modulo = None
        self.info, self.campi = {}, []
        self.changelog = []
        self.sha = ""
        self.stato, self.errore = "errore", ""
        self.avviato = False
        self.ctx = None
        self.messaggio = ""            # una riga che il plugin puo' mostrare in Impostazioni (ctx.stato)
        self.richieste, self.ultima = 0, None
        self.ultimo_errore = ""


class Richiesta:
    """Cio' che una rotta pubblica di un plugin riceve."""
    def __init__(self, metodo, percorso, query, corpo, intestazioni, ip):
        self.metodo, self.percorso, self.query, self.corpo, self.intestazioni, self.ip = metodo, percorso, query, corpo, intestazioni, ip

    def json(self):
        """Il corpo come JSON (dict), oppure ValueError."""
        v = json.loads(self.corpo.decode("utf-8") or "{}")
        if not isinstance(v, dict):
            raise ValueError("serve un oggetto JSON")
        return v


class ContestoPlugin:
    """L'unica porta fra un plugin e il pannello. Piccola e stabile (PLUGIN_INTERFACCIA)."""
    def __init__(self, app, plug):
        self._app, self._plug = app, plug
        self.nome = plug.nome
        self.log = logging.getLogger("pdc.plugin." + plug.nome)
        self.demo = app.demo

    def config(self):
        """I valori del plugin (campi + «attivo»), con i predefiniti dove l'utente non ha scritto nulla."""
        sal = (self._app.cfg.leggi().get("plugin") or {}).get(self.nome) or {}
        out = {c["chiave"]: c.get("default") for c in self._plug.campi}
        out.update({k: v for k, v in sal.items() if k in out or k == "attivo"})
        return out

    def salva(self, chiave, valore):
        """Scrive un valore nel config.json, nella sezione del plugin (per esempio un segreto generato al primo avvio)."""
        def f(raw):
            raw.setdefault("plugin", {}).setdefault(self.nome, {})[chiave] = valore
        self._app.cfg.aggiorna(f)

    def ultimi(self):
        """{serie: (ts, numero, testo)}: gli ultimi valori letti dalla pompa e dallo Smarther."""
        return {s: (ts, num, txt) for s, ts, num, txt in self._app.db.ultimi()}

    def dashboard(self):
        """Lo stesso riepilogo che mostra la Dashboard (dict)."""
        return API["/api/dashboard"](self._app, {})

    def comando(self, cosa, acceso):
        """cosa = 'smarther' | 'riscaldamento' | 'acqua'. Stesse regole e stesso registro dei comandi del pannello."""
        return esegui_comando(self._app, cosa, acceso, "plugin " + self.nome)

    def rotta(self, percorso, funzione, metodi=("POST",)):
        """Una pagina PUBBLICA (senza login) su /plugin/<nome>/<percorso>. funzione(Richiesta) -> dict | (codice, dict) |
        (codice, testo_o_byte, tipo). Il pannello limita dimensione e frequenza delle richieste; l'autenticazione e' del plugin."""
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", percorso):
            raise ValueError("percorso non valido: solo lettere, numeri, - e _")
        PLUGIN_ROTTE[f"/plugin/{self.nome}/{percorso}"] = (self.nome, funzione, tuple(m.upper() for m in metodi))

    def stato(self, testo):
        """Una riga di stato mostrata in Impostazioni > Integrazioni."""
        self._plug.messaggio = str(testo)[:200]


def _riassunto(e):
    return f"{type(e).__name__}: {e}"[:300]


def _pulisci_changelog(grezzo):
    """La cronologia dichiarata da un plugin, ripulita (testi corti, limiti). Mai eccezioni: se e' sbagliata, resta vuota."""
    out = []
    try:
        for c in list(grezzo or [])[:60]:
            righe = [[str(x[0]) if str(x[0]) in ("n", "m", "s") else "m", str(x[1])[:80], str(x[2])[:600]]
                     for x in list(c.get("righe") or [])[:12] if isinstance(x, (list, tuple)) and len(x) >= 3]
            data = str(c.get("data", ""))[:16]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?", data):
                data = ""
            out.append({"v": str(c.get("v", ""))[:20], "data": data, "titolo": str(c.get("titolo", ""))[:80], "righe": righe})
    except Exception:
        return []
    return out


def _leggi_plugin(f):
    """Carica il file di un plugin SENZA avviarlo. Mai eccezioni: l'errore finisce nel Plug."""
    p = Plug(f.stem, f)
    try:
        if f.stat().st_size > PLUGIN_MAX_FILE:
            raise ValueError("file troppo grande")
        dati = f.read_bytes()
        p.sha = hashlib.sha256(dati).hexdigest()[:8]
        modulo = types.ModuleType("pdc_plugin_" + p.nome)
        modulo.__file__ = str(f)
        exec(compile(dati, str(f), "exec"), modulo.__dict__)
        info = getattr(modulo, "INFO", None)
        if not isinstance(info, dict):
            raise ValueError("manca INFO (un dizionario con titolo, versione, interfaccia)")
        p.info = {k: str(v)[:300] for k, v in info.items()}
        if info.get("interfaccia") != PLUGIN_INTERFACCIA:
            raise ValueError(f"pensato per l'interfaccia {info.get('interfaccia')!r}, questo pannello usa la {PLUGIN_INTERFACCIA}: "
                             "serve una versione del plugin adatta")
        if not callable(getattr(modulo, "avvia", None)):
            raise ValueError("manca la funzione avvia(ctx)")
        campi = []
        for c in getattr(modulo, "CAMPI", None) or []:
            if not isinstance(c, dict) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", str(c.get("chiave", ""))) or c.get("chiave") == "attivo":
                raise ValueError(f"campo non valido: {str(c)[:80]}")
            campi.append({"chiave": c["chiave"], "tipo": c.get("tipo", "testo"), "et": str(c.get("et", c["chiave"])),
                          "aiuto": str(c.get("aiuto", "")), "default": c.get("default"),
                          **{k: c[k] for k in ("min", "max", "opzioni") if k in c}})
        p.campi, p.modulo = campi, modulo
        p.changelog = _pulisci_changelog(getattr(modulo, "CHANGELOG", None))
        p.stato = "spento"
    except BaseException as e:            # anche SystemExit: un plugin non deve mai fermare il pannello
        if isinstance(e, KeyboardInterrupt):
            raise
        p.stato, p.errore = "errore", _riassunto(e)
        log.error("Plugin %s: non lo carico (%s)", p.nome, p.errore)
    return p


def _togli_rotte(nome):
    for k in [k for k, v in PLUGIN_ROTTE.items() if v[0] == nome]:
        del PLUGIN_ROTTE[k]


def _avvia_plugin(app, p):
    """avvia(ctx) con un limite di tempo. Se fallisce il plugin va in «errore» e le sue rotte vengono tolte."""
    ctx = ContestoPlugin(app, p)
    esito = {}

    def lavoro():
        try:
            p.modulo.avvia(ctx)
        except BaseException as e:
            esito["errore"] = _riassunto(e)
            log.exception("Plugin %s: errore in avvia()", p.nome)
    th = threading.Thread(target=lavoro, daemon=True, name="plugin-" + p.nome)
    th.start()
    th.join(PLUGIN_ATTESA_AVVIO)
    if th.is_alive():
        esito["errore"] = f"avvia() non è tornata entro {PLUGIN_ATTESA_AVVIO} secondi (per i lavori lunghi serve un thread)"
    if esito.get("errore"):
        _togli_rotte(p.nome)
        p.stato, p.errore, p.avviato = "errore", esito["errore"], False
        return
    p.ctx, p.avviato, p.stato, p.errore = ctx, True, "attivo", ""
    log.info("Plugin %s avviato", p.nome)


def _ferma_plugin(p):
    _togli_rotte(p.nome)
    f = getattr(p.modulo, "ferma", None)
    if p.avviato and callable(f):
        try:
            f(p.ctx)
        except BaseException as e:
            if isinstance(e, KeyboardInterrupt):
                raise
            log.warning("Plugin %s: errore in ferma() (%s)", p.nome, _riassunto(e))
    if p.avviato:
        log.info("Plugin %s fermato", p.nome)
    p.avviato = False
    if p.stato == "attivo":
        p.stato = "spento"


def plugin_sincronizza(app):
    """Accende i plugin con l'interruttore su «attivo», spegne gli altri."""
    with _lock_plugin:
        sal = app.cfg.leggi().get("plugin") or {}
        for p in PLUGIN.values():
            if p.modulo is None:
                continue
            voglio = bool((sal.get(p.nome) or {}).get("attivo"))
            if voglio and not p.avviato:
                _avvia_plugin(app, p)
            elif not voglio and p.avviato:
                _ferma_plugin(p)
            elif not voglio and p.stato == "errore" and p.modulo is not None:
                p.stato, p.errore = "spento", ""


def plugin_ferma_tutti():
    with _lock_plugin:
        for p in PLUGIN.values():
            if p.avviato:
                _ferma_plugin(p)


def plugin_ricarica(app):
    """Rilegge la cartella plugin e riavvia quelli accesi. -> numero di plugin trovati."""
    with _lock_plugin:
        plugin_ferma_tutti()
        PLUGIN.clear()
        PLUGIN_ROTTE.clear()
        cart = app.base / "plugin"
        if cart.is_dir():
            for f in sorted(cart.glob("*.py"))[:20]:
                if f.name.startswith(("_", ".")) or f.is_symlink() or not _RX_NOME_PLUGIN.fullmatch(f.stem):
                    continue
                PLUGIN[f.stem] = _leggi_plugin(f)
        plugin_sincronizza(app)
        return len(PLUGIN)


@api("/api/plugin/changelog")
def api_plugin_changelog(app, q):
    """Le cronologie dei plugin, per la tab Changelog > Plugin."""
    with _lock_plugin:
        return {"plugin": [{"nome": p.nome, "titolo": p.info.get("titolo") or p.nome, "versione": p.info.get("versione", ""),
                            "changelog": p.changelog} for p in PLUGIN.values() if p.changelog]}


def sezioni_plugin():
    """I riquadri di Impostazioni > Integrazioni: uno per plugin, con interruttore e campi (stessa forma delle altre sezioni)."""
    out = []
    with _lock_plugin:
        for p in PLUGIN.values():
            campi = [] if p.modulo is None else (
                [{"chiave": f"plugin.{p.nome}.attivo", "tipo": "bool", "et": "Attivo", "aiuto": "Accende o spegne il plugin.", "default": False}]
                + [{**c, "chiave": f"plugin.{p.nome}.{c['chiave']}"} for c in p.campi])
            out.append({"id": "plugin_" + p.nome, "tab": "integrazioni", "campi": campi,
                        "titolo": p.info.get("titolo") or p.nome, "nota": p.info.get("descrizione", ""),
                        "plugin": {"nome": p.nome, "file": p.file.name, "sha": p.sha, "versione": p.info.get("versione", ""),
                                   "stato": p.stato, "errore": p.errore or p.ultimo_errore, "messaggio": p.messaggio,
                                   "richieste": p.richieste, "ultima": p.ultima,
                                   "rotte": sorted(k for k, v in PLUGIN_ROTTE.items() if v[0] == p.nome)}})
    return out


def _troppo_veloce(nome, ip):
    ora = time.time()
    q = _ritmo_plugin.setdefault((nome, ip), [])
    while q and q[0] < ora - 60:
        q.pop(0)
    if len(q) >= PLUGIN_RICHIESTE_MIN:
        return True
    q.append(ora)
    if len(_ritmo_plugin) > 2000:       # non cresce senza limite
        for k in [k for k, v in _ritmo_plugin.items() if not v or v[-1] < ora - 60]:
            del _ritmo_plugin[k]
    return False


def plugin_pubblico(app, h, p, q):
    """Risponde alle rotte pubbliche dei plugin (chiamata dal server web PRIMA del controllo del login)."""
    voce = PLUGIN_ROTTE.get(p)
    plug = PLUGIN.get(voce[0]) if voce else None
    if not voce or not plug or not plug.avviato:
        if _troppo_veloce("-", ip_client(h)):      # anche gli indirizzi sbagliati hanno un limite: niente tentativi a raffica
            return h._json({"errore": "troppe richieste"}, 429)
        return h._invia(404, "Non trovato", "text/plain; charset=utf-8")
    _, funzione, metodi = voce
    if h.command not in metodi:
        return h._invia(405, "Metodo non consentito", "text/plain; charset=utf-8")
    ip = ip_client(h)
    if _troppo_veloce(plug.nome, ip):
        return h._json({"errore": "troppe richieste"}, 429)
    try:
        n = int(h.headers.get("Content-Length", "0"))
    except ValueError:
        n = -1
    if n < 0 or n > PLUGIN_MAX_CORPO:
        return h._json({"errore": "richiesta non valida" if n < 0 else "richiesta troppo grande"}, 400 if n < 0 else 413)
    corpo = h.rfile.read(n) if n else b""
    plug.richieste, plug.ultima = plug.richieste + 1, int(time.time())
    req = Richiesta(h.command, p, q, corpo, {k.lower(): v for k, v in h.headers.items()}, ip)
    try:
        r = funzione(req)
    except Exception as e:
        plug.ultimo_errore = _riassunto(e)
        log.exception("Plugin %s: errore nella rotta %s", plug.nome, p)
        return h._json({"errore": "errore interno"}, 500)
    plug.ultimo_errore = ""
    if isinstance(r, dict):
        return h._json(r)
    if isinstance(r, tuple) and len(r) == 2 and isinstance(r[1], dict):
        return h._json(r[1], int(r[0]))
    if isinstance(r, tuple) and len(r) == 3:
        return h._invia(int(r[0]), r[1], str(r[2]))
    plug.ultimo_errore = "la rotta ha risposto con un tipo non previsto"
    return h._json({"errore": "errore interno"}, 500)


@api_post("/api/plugin/ricarica")
def api_plugin_ricarica(app, corpo, h):
    n = plugin_ricarica(app)
    return {"ok": True, "plugin": n}
#@@PARTE aggiorna@@


# ==========================================================================
# AGGIORNAMENTO A CALDO: ricarica le parti di interfaccia senza fermare la raccolta dei dati
# ==========================================================================
# Il file e' costruito a "parti" (segnalibri #@@PARTE nome@@). Le parti di interfaccia (pagine, API, Impostazioni, Dispositivi,
# comandi) si possono rieseguire dentro il programma gia' acceso; le altre (nucleo della raccolta, Telegram, tunnel, backup,
# avvio) no: se cambiano serve il riavvio completo. Il server web non si ferma: gli si cambia solo la classe che risponde.

RICARICABILI = ("web", "impostazioni", "dispositivi", "controlli", "plugin")
NOMI_PARTI = {"nucleo": "nucleo della raccolta dati", "web": "pagine e API", "telegram": "avvisi Telegram",
              "impostazioni": "Impostazioni", "cloudflare": "tunnel Cloudflare", "manutenzione": "backup e manutenzione",
              "dispositivi": "Dispositivi", "controlli": "comandi allo Smarther", "plugin": "plugin e integrazioni", "aggiorna": "aggiornamento a caldo",
              "comandi": "avvio e comandi"}
_RX_PARTE = re.compile(r"^#@@PARTE (\w+)@@\n", re.M)
_RX_VERSIONE = re.compile(r'^VERSIONE = "([^"]*)"$', re.M)
_lock_aggiorna = threading.Lock()


def _leggi_parti(testo):
    """Il file diviso nelle sue parti: {nome: (sorgente, righe_prima)} nell'ordine in cui compaiono."""
    parti, nome, inizio = {}, "nucleo", 0
    for m in _RX_PARTE.finditer(testo):
        parti[nome] = (testo[inizio:m.start()], testo.count("\n", 0, inizio))
        nome, inizio = m.group(1), m.end()
    parti[nome] = (testo[inizio:], testo.count("\n", 0, inizio))
    return parti


def _impronta(nome, sorgente):
    if nome == "nucleo":    # il numero di versione sta nel nucleo, ma cambiarlo non richiede il riavvio
        sorgente = _RX_VERSIONE.sub('VERSIONE = ""', sorgente, count=1)
    return hashlib.sha256(sorgente.encode("utf-8")).hexdigest()[:16]


def _parti_su_disco():
    testo = Path(__file__).resolve().read_text(encoding="utf-8")
    return testo, _leggi_parti(testo)


def _impronte_correnti():
    try:
        return {n: _impronta(n, s) for n, (s, _) in _parti_su_disco()[1].items()}
    except Exception:
        return {}


PARTI_CARICATE = _impronte_correnti()    # com'era il file quando e' partito il pannello


def _parti_cambiate(parti):
    """Nomi delle parti del file su disco diverse da quelle caricate all'avvio."""
    return [n for n, (s, _) in parti.items() if _impronta(n, s) != PARTI_CARICATE.get(n)]


def stato_aggiornamento():
    """{'stato': aggiornato|applicabile|riavvio|errore, 'parti': [...], 'versione_disco', 'versione'}"""
    out = {"versione": VERSIONE, "versione_disco": VERSIONE, "parti": [], "stato": "aggiornato"}
    try:
        testo, parti = _parti_su_disco()
    except (OSError, UnicodeDecodeError) as e:
        return dict(out, stato="errore", messaggio=f"Non riesco a leggere il file del pannello ({e}).")
    m = _RX_VERSIONE.search(testo)
    out["versione_disco"] = m.group(1) if m else VERSIONE
    if not PARTI_CARICATE or "nucleo" not in parti:
        return dict(out, stato="riavvio", parti=[], messaggio="Non riesco a confrontare il file con quello in uso: riavvia il pannello.")
    cambiate = _parti_cambiate(parti)
    out["parti"] = [{"nome": n, "titolo": NOMI_PARTI.get(n, n), "a_caldo": n in RICARICABILI} for n in cambiate]
    if any(n not in RICARICABILI for n in cambiate):
        out["stato"] = "riavvio"
    elif cambiate:
        out["stato"] = "applicabile"
    return out


def applica_aggiornamento(app):
    """Rilegge il file dal disco e ricarica a caldo le parti di interfaccia cambiate. Prima controlla tutto: se qualcosa non va,
    non cambia nulla e continua a girare il codice di prima. -> {'ok': bool, ...}"""
    if not _lock_aggiorna.acquire(blocking=False):
        return {"ok": False, "messaggio": "Un aggiornamento e' gia' in corso."}
    try:
        try:
            testo, parti = _parti_su_disco()
        except (OSError, UnicodeDecodeError) as e:
            return {"ok": False, "messaggio": f"Non riesco a leggere il file del pannello ({e})."}
        if not PARTI_CARICATE or "nucleo" not in parti:
            return {"ok": False, "messaggio": "Non riesco a confrontare il file con quello in uso: serve il riavvio completo."}
        cambiate = _parti_cambiate(parti)
        fissi = [n for n in cambiate if n not in RICARICABILI]
        if fissi:
            return {"ok": False, "riavvio": True, "messaggio": "Per questa versione serve il riavvio completo (cambiato: "
                    + ", ".join(NOMI_PARTI.get(n, n) for n in fissi) + "). La raccolta dei dati continua finche' non lo fai."}
        if not cambiate:
            return {"ok": True, "gia": True, "messaggio": "Il pannello e' gia' aggiornato.", "versione": VERSIONE}
        codici = {}
        for n in cambiate:                      # 1) il codice nuovo deve almeno compilare
            sorg, righe = parti[n]
            try:
                codici[n] = compile("\n" * righe + sorg, str(Path(__file__).resolve()), "exec")
            except SyntaxError as e:
                return {"ok": False, "messaggio": f"Il file nuovo ha un errore (parte «{NOMI_PARTI.get(n, n)}», riga {e.lineno}): "
                        f"{e.msg}. Non ho cambiato nulla, il pannello continua con la versione di prima."}
        g = globals()
        prima = dict(g)                         # 2) per tornare indietro se l'esecuzione fallisce
        nuova = _RX_VERSIONE.search(testo)
        try:
            if nuova:
                g["VERSIONE"] = nuova.group(1)
            for n in [n for n in parti if n in codici]:
                exec(codici[n], g)
        except Exception as e:
            for k in set(g) | set(prima):
                if prima.get(k, g) is not g.get(k, prima):
                    if k in prima:
                        g[k] = prima[k]
                    else:
                        g.pop(k, None)
            log.exception("aggiornamento a caldo fallito")
            return {"ok": False, "messaggio": f"Il codice nuovo si e' fermato per un errore ({type(e).__name__}: {e}). "
                    "Ho rimesso quello di prima."}
        # l'impronta delle pagine (la stessa formula della parte web) dipende anche dal numero di versione
        g["BUILD"] = g["_calcola_build"](g["VERSIONE"], g["WEB_ASSETS"])
        for n in cambiate:
            PARTI_CARICATE[n] = _impronta(n, parti[n][0])
        if "plugin" in cambiate and "plugin_ricarica" in g:      # il caricatore e' nuovo: rilegge la cartella plugin
            try:
                g["plugin_ricarica"](app)
            except Exception:
                log.exception("ricarica dei plugin fallita")
        srv = getattr(app, "server_web", None)
        if srv is not None:
            srv.RequestHandlerClass = g["Gestore"]   # le richieste nuove usano il codice nuovo; il server non si ferma
        titoli = ", ".join(NOMI_PARTI.get(n, n) for n in cambiate)
        log.info("Aggiornamento a caldo applicato: %s (versione %s)", titoli, VERSIONE)
        return {"ok": True, "versione": VERSIONE, "parti": titoli,
                "messaggio": f"Aggiornato alla versione {VERSIONE} senza fermare la raccolta dei dati."}
    finally:
        _lock_aggiorna.release()


@api("/api/aggiornamento")
def api_aggiornamento(app, q):
    return stato_aggiornamento()


@api_post("/api/aggiornamento/applica")
def api_applica_aggiornamento(app, corpo, h):
    if app.demo:
        return {"ok": False, "messaggio": "In modalità demo non si aggiorna a caldo."}
    return applica_aggiornamento(app)
#@@PARTE comandi@@
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
        elif len(p1) < PASSWORD_MIN:
            print(f"Troppo corta: servono almeno {PASSWORD_MIN} caratteri (il pannello potra' essere visibile da internet).")
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
    imposta_log(base)
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
            esterni_assicura(app)
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
        try:
            n_plugin = plugin_ricarica(app)
            if n_plugin:
                log.info("Plugin trovati: %d", n_plugin)
        except Exception:
            log.exception("caricamento dei plugin fallito (il pannello parte lo stesso)")
        try:
            boost_riprendi(app)
        except Exception:
            log.exception("boost: ripresa dopo il riavvio")
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
        try:
            plugin_ferma_tutti()
        except Exception:
            log.exception("arresto dei plugin")
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
