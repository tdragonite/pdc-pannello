# Plugin Analisi: i dati dell'«analisi completa» (gli stessi del comando `python pdc_strumenti.py analisi`) su un indirizzo di SOLA LETTURA,
# raggiungibile SOLO dalla rete di casa. Serve a chi deve analizzare i dati senza che tu li esporti a mano ogni volta.
# Installazione: copia questo file nella cartella "plugin" accanto a pdc_pannello.py (pdc_strumenti.py deve stare accanto a
# pdc_pannello.py), apri Impostazioni -> Integrazioni, premi "Ricarica i plugin", scrivi un TOKEN tuo e accendi l'interruttore.
#
# SICUREZZA (tutte e quattro le cose insieme, altrimenti 404):
#  1. rete di casa: le richieste che arrivano dal tunnel Cloudflare (o da un altro proxy) o da un indirizzo non privato sono rifiutate,
#     come per le regolazioni della pompa. L'indirizzo con il dominio Cloudflare che compare in «Indirizzi pubblici» quindi NON funziona
#     da fuori, di proposito: da casa si usa l'indirizzo del PC con la porta;
#  2. l'indirizzo contiene un segreto casuale, generato al primo avvio;
#  3. serve il TOKEN (campo «Token», obbligatorio) nell'intestazione «X-Analisi-Token»: senza token il plugin non risponde a nessuno;
#  4. solo GET: non comanda niente e non scrive niente nel pannello.
# COME LAVORA: lancia il comando «analisi» di pdc_strumenti.py come programma a parte (lo stesso che lanci tu a mano) e ne manda il
# risultato. Cosi' memoria e calcolo (centinaia di MB e qualche secondo, con mesi di storico) non pesano sul pannello, che continua a
# raccogliere i dati; se il comando si pianta o ci mette troppo viene fermato, e il pannello non se ne accorge.
# Il contenuto e' quello del comando: nessun token, password, indirizzo o identificativo (vedi capitolo 9 di strumenti.md).
# Come si legge: GET /plugin/analisi/<segreto>?giorni=45&grezzi=1&ciclo=7 con l'intestazione X-Analisi-Token; la risposta e' un JSON compresso (gzip).
import gzip
import hmac
import ipaddress
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

INFO = {
    "titolo": "Analisi dei dati",
    "versione": "1.0.0",
    "interfaccia": 1,
    "descrizione": "Indirizzo di sola lettura, solo dalla rete di casa e con token, con i dati dell'analisi completa (gli stessi del comando "
                   "«analisi» di pdc_strumenti.py). Non può comandare né scrivere niente.",
}
CHANGELOG = [
    {"v": "1.0.0", "data": "2026-11-11T23:00", "titolo": "Primo rilascio", "righe": [
        ["n", "Analisi su richiesta", "Indirizzo di sola lettura con i dati dell'analisi completa, senza esportare il file a mano. Solo dalla rete di casa, con segreto e token."]]},
]

CAMPI = [
    {"chiave": "segreto", "tipo": "segreto", "et": "Segreto dell’indirizzo",
     "aiuto": "Generato da solo al primo avvio. Per cambiarlo scrivine uno nuovo (da 24 a 64 caratteri tra lettere, numeri, trattino e "
              "underscore), oppure svuota il campo: ne esce uno nuovo. Poi ricarica i plugin."},
    {"chiave": "token", "tipo": "segreto", "et": "Token (obbligatorio)",
     "aiuto": "Scrivine uno tuo (da 24 a 64 caratteri tra lettere, numeri, trattino e underscore). Senza un token valido il plugin non "
              "risponde a nessuno. Chi legge i dati lo manda nell'intestazione «X-Analisi-Token»."},
]

STRUMENTI = Path(__file__).resolve().parent.parent / "pdc_strumenti.py"     # accanto a pdc_pannello.py
ATTESA_MAX = 180                    # secondi: oltre, il comando viene fermato
CACHE_SECONDI = 60                  # la stessa richiesta entro un minuto riusa il risultato (per esempio se lo scaricamento si interrompe)
NON_TROVATO = (404, "Non trovato", "text/plain; charset=utf-8")       # stessa risposta per tutti i rifiuti: non dice quale controllo e' fallito
_DA_PROXY = ("cf-connecting-ip", "cf-ray", "cf-visitor", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
             "forwarded", "x-real-ip", "true-client-ip")
_RX_CHIAVE = re.compile(r"[A-Za-z0-9_\-]{24,64}")

_ctx = {}
_lavoro = threading.Lock()          # un'analisi alla volta
_cache = {}                         # al massimo una voce: (giorni, grezzi, ciclo) -> (quando, byte)


def _valida(ctx, chiave):
    """Il segreto o il token scritto nelle impostazioni, se ha la forma giusta; altrimenti ''."""
    t = str(ctx.config().get(chiave) or "").strip()
    return t if _RX_CHIAVE.fullmatch(t) else ""


def _da_casa(rich):
    """Stessa regola delle regolazioni della pompa: niente intestazioni di tunnel o proxy, e indirizzo privato o locale."""
    if any(rich.intestazioni.get(x) for x in _DA_PROXY):
        return False
    try:
        ip = ipaddress.ip_address(str(rich.ip).split("%")[0])
    except ValueError:
        return False
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _numero(q, nome, predefinito, minimo, massimo):
    try:
        v = int(q.get(nome, [predefinito])[0])
    except (TypeError, ValueError, IndexError):
        v = predefinito
    return max(minimo, min(massimo, v))


def _esegui(giorni, grezzi, ciclo):
    """Lancia il comando «analisi» a parte e ne ritorna il file compresso (byte). RuntimeError con il motivo se non riesce."""
    if not STRUMENTI.exists():
        raise RuntimeError("manca pdc_strumenti.py: deve stare nella stessa cartella di pdc_pannello.py")
    cartella = Path(tempfile.mkdtemp(prefix="pdc_analisi_"))
    try:
        uscita = cartella / "analisi.json"
        try:
            r = subprocess.run([sys.executable or "python", str(STRUMENTI), "analisi", "--giorni", str(giorni), "--grezzi", str(grezzi),
                                "--ciclo", str(ciclo), "--uscita", str(uscita)],
                               cwd=str(STRUMENTI.parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               timeout=ATTESA_MAX, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"l'analisi non ha finito in {ATTESA_MAX} secondi: prova con meno giorni") from None
        except OSError as e:
            raise RuntimeError(f"non riesco a lanciare pdc_strumenti.py ({e})") from None
        if r.returncode == 2:           # argparse non conosce --uscita
            raise RuntimeError("pdc_strumenti.py è una versione vecchia: aggiornalo (serve quella con «analisi --uscita»)")
        if r.returncode != 0 or not uscita.exists():
            motivo = r.stderr.decode("utf-8", "replace").replace("ERRORE:", "").strip().splitlines()
            raise RuntimeError(motivo[-1][:200] if motivo else f"il comando «analisi» è uscito con codice {r.returncode}")
        return gzip.compress(uscita.read_bytes(), 6)
    finally:
        shutil.rmtree(cartella, ignore_errors=True)


def avvia(ctx):
    _ctx["ctx"] = ctx
    seg = _valida(ctx, "segreto")
    if not seg:
        seg = secrets.token_urlsafe(24)
        ctx.salva("segreto", seg)
    ctx.rotta(seg, richiesta, ("GET",))
    ctx.stato("manca pdc_strumenti.py accanto a pdc_pannello.py" if not STRUMENTI.exists()
              else "in attesa della prima richiesta" if _valida(ctx, "token")
              else "SPENTO DI FATTO: scrivi un token (da 24 a 64 caratteri) e ricarica i plugin")


def ferma(ctx):
    _ctx.clear()
    _cache.clear()


def richiesta(rich):
    ctx = _ctx.get("ctx")
    tok = _valida(ctx, "token") if ctx else ""
    if not tok or not _da_casa(rich) or not hmac.compare_digest(str(rich.intestazioni.get("x-analisi-token", "")).encode(), tok.encode()):
        return NON_TROVATO
    if ctx.demo:
        return 503, {"errore": "nella demo l'analisi non è disponibile"}
    chiave = (_numero(rich.query, "giorni", 45, 1, 120), _numero(rich.query, "grezzi", 1, 0, 7), _numero(rich.query, "ciclo", 7, 0, 14))
    pronta = _cache.get(chiave)
    if pronta and time.time() - pronta[0] < CACHE_SECONDI:
        return 200, pronta[1], "application/gzip"
    if not _lavoro.acquire(blocking=False):
        return 429, {"errore": "un'analisi è già in corso, riprova tra qualche secondo"}
    try:
        t0 = time.time()
        try:
            dati = _esegui(*chiave)
        except RuntimeError as e:
            ctx.log.error("analisi: %s", e)
            ctx.stato("errore: " + str(e))
            return 500, {"errore": str(e)}
        _cache.clear()
        _cache[chiave] = (time.time(), dati)
        ctx.stato(f"ultima lettura alle {time.strftime('%H:%M')} ({len(dati) // 1024 + 1} KB compressi, {time.time() - t0:.1f} s)")
        return 200, dati, "application/gzip"
    finally:
        _lavoro.release()
