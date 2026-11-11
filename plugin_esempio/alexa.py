# Plugin Alexa: una skill "personalizzata" che risponde alle domande sulla casa e accende/spegne lo Smarther.
# Installazione: copia questo file nella cartella "plugin" accanto a pdc_pannello.py, apri Impostazioni -> Integrazioni,
# premi "Ricarica i plugin" e accendi l'interruttore. L'indirizzo da incollare nella console Amazon compare in «Indirizzi pubblici».
#
# SICUREZZA:
#  0. (facoltativo, serve la libreria "cryptography") si puo' verificare la firma digitale di Amazon su ogni richiesta:
#     campo «Verifica firma Amazon» = spenta / solo avviso / attiva. Senza la libreria «spenta» e «solo avviso» lasciano passare tutto (lo stato lo dice), «attiva» rifiuta.
#  1. l'indirizzo contiene un segreto casuale, generato al primo avvio: senza non si arriva a nulla (404);
#  2. ogni richiesta deve venire dalla TUA skill (la prima volta se ne memorizza l'ID). L'ID utente Amazon NON si controlla:
#     cambia ogni volta che la skill viene disattivata e riattivata, e una skill in sviluppo la vede comunque solo il tuo account;
#  3. le richieste con data vecchia (piu' di 150 secondi) vengono rifiutate, e ogni richiesta (ha un ID unico) vale una volta sola:
#     chi ne intercettasse una non puo' ripeterla;
#  4. a voce si puo' solo accendere/spegnere lo Smarther: la pompa di calore e' in sola lettura;
#  5. il pannello limita comunque dimensione e numero di richieste al minuto.
import base64
import re
import secrets
import ssl
import threading
import time
import urllib.request
import warnings
from urllib.parse import urlsplit
from datetime import datetime, timezone

INFO = {
    "titolo": "Alexa",
    "versione": "1.4.2",
    "interfaccia": 1,
    "descrizione": "Skill personale per Alexa: risponde su temperatura e umidità (in casa e fuori), pompa di calore, acqua calda, mandata e ripartenza, "
                   "e accende o spegne lo Smarther. Si usa con le Routine di Alexa, senza pronunciare il nome della skill.",
}
CHANGELOG = [
    {"v": "1.4.2", "data": "2026-11-11T14:30", "titolo": "Richieste malformate", "righe": [
        ["s", "Richieste strane", "Una richiesta scritta male (non un oggetto, o con un intento senza nome) ora riceve la risposta giusta invece di un errore interno nel registro."]]},
    {"v": "1.4.1", "data": "2026-11-09T23:45", "titolo": "Firma «Attiva» più severa", "righe": [
        ["s", "Firma non verificabile", "Con «Attiva», se la firma non si può controllare (manca la libreria cryptography o i certificati di sistema) la richiesta ora viene rifiutata, invece di passare."]]},
    {"v": "1.4.0", "data": "2026-11-04T15:00", "titolo": "Temperatura e umidità di fuori", "righe": [
        ["n", "Due domande «fuori»", "Temperatura e umidità di fuori, nei due ordini. L’umidità viene dalla stazione meteo vicina (Impostazioni → Collegamenti)."],
        ["m", "Pompa di calore", "La risposta sulla pompa di calore dice anche l’umidità di fuori."],
        ["m", "Aiuto", "«Aiuto» e l’apertura della skill nominano anche «com’è fuori»."],
        ["m", "Da rifare in Amazon", "Reimporta il modello alexa_modello_it.json nella console (frasi nuove)."]]},
    {"v": "1.3.1", "data": "2026-11-04T13:00", "titolo": "Console più pulita", "righe": [
        ["s", "Avvisi in console", "Spariscono gli avvisi sul certificato vecchio dell’archivio di Windows: erano innocui."]]},
    {"v": "1.3", "data": "2026-11-04T12:00", "titolo": "Firma più robusta", "righe": [
        ["m", "Esito della firma", "Ora appartiene alla singola richiesta, anche con richieste ravvicinate."],
        ["m", "Certificato Amazon", "Il download aspetta al massimo 3 secondi, per non consumare gli 8 concessi da Alexa."]]},
    {"v": "1.2", "data": "2026-11-04T11:00", "titolo": "Certificato in background", "righe": [
        ["m", "Certificato Amazon", "Si riscarica da solo ogni 50 minuti: nessuna richiesta di Alexa aspetta il download."]]},
    {"v": "1.1", "data": "2026-11-04T10:00", "titolo": "Verifica della firma", "righe": [
        ["n", "Verifica firma Amazon", "Campo Spenta / Solo avviso / Attiva: controlla che le richieste arrivino davvero da Amazon (serve la libreria cryptography)."],
        ["m", "Nome di invocazione", "Il modello vocale usa «centrale termica»."]]},
    {"v": "1.0", "data": "2026-11-04T09:00", "titolo": "Prima versione", "righe": [
        ["n", "Skill personale", "Temperatura, umidità, riscaldamento acceso, ripartenza, pompa di calore, acqua calda, mandata e Smarther acceso/spento, con le Routine di Alexa."],
        ["n", "Indirizzo protetto", "Segreto casuale, skill riconosciuta, richieste fresche e uniche, limite di richieste."]]},
]

CAMPI = [
    {"chiave": "segreto", "tipo": "segreto", "et": "Segreto dell’indirizzo",
     "aiuto": "Generato da solo al primo avvio. Per cambiarlo scrivine uno nuovo (da 24 a 64 caratteri tra lettere, numeri, trattino e "
              "underscore), poi ricarica i plugin e aggiorna l’indirizzo nella console Amazon."},
    {"chiave": "skill_id", "tipo": "testo", "et": "ID della skill",
     "aiuto": "Si compila da solo alla prima richiesta di Alexa. Svuotalo se vuoi che impari di nuovo la skill."},
    {"chiave": "verifica_firma", "tipo": "scelta", "et": "Verifica firma Amazon", "default": "spenta",
     "opzioni": [("spenta", "Spenta"), ("avviso", "Solo avviso"), ("attiva", "Attiva")],
     "aiuto": "Controlla che ogni richiesta sia davvero firmata da Amazon (serve la libreria Python «cryptography»). "
              "«Solo avviso» controlla e scrive l’esito nello stato, ma accetta comunque; «Attiva» rifiuta le richieste con firma non valida. "
              "Consiglio: prova prima «Solo avviso», poi passa ad «Attiva» quando lo stato dice «firma valida»."},
]

_ctx = {}
_lock = threading.Lock()
ETA_MAX = 150          # secondi: oltre, la richiesta e' vecchia (o riproposta da qualcuno)
_viste = {}            # requestId -> quando e' arrivata: una richiesta gia' vista non si accetta di nuovo
MAX_VISTE = 500
DATO_VECCHIO = 1800    # secondi: oltre, avviso che il dato non e' fresco


_locale = threading.local()      # l'esito della firma vale per la richiesta in corso (ogni richiesta ha il suo thread)
_cache_cert = {}       # url -> (quando, [certificati])
_radici_cache = {}
CERT_TTL = 3600
RINNOVO = 3000         # secondi: il certificato si riscarica in background prima che scada la copia (50 minuti)
RINNOVO_RIPROVA = 300  # se il rinnovo fallisce, si riprova dopo 5 minuti
_fermo = threading.Event()
_rinnovi = set()       # indirizzi per cui gira gia' un rinnovo
CERT_MAX_BYTE = 32768
SCARICA_ATTESA = 3     # secondi: Alexa ne concede 8 in tutto, il download non deve mangiarseli
HOST_CERT = "s3.amazonaws.com"
NOME_CERT = "echo-api.amazon.com"


def _stato(testo):
    _ctx["ctx"].stato(testo + getattr(_locale, "firma", ""))


def avvia(ctx):
    _ctx["ctx"] = ctx
    seg = str(ctx.config().get("segreto") or "")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{24,64}", seg):
        seg = secrets.token_urlsafe(24)
        ctx.salva("segreto", seg)
    _ctx["segreto"] = seg
    _fermo.clear()
    _rinnovi.clear()
    ctx.rotta(seg, richiesta, ("POST",))
    _stato("in attesa della prima richiesta")


# ------------------------------------------------------------------ frasi

def n(v, dec=1):
    """Numero all'italiana per la voce: 20,5 - e senza ',0' se e' tondo."""
    s = f"{float(v):.{dec}f}".replace(".", ",")
    return s[:-2] if s.endswith(",0") else s


def ora_parlata(ts):
    t = time.localtime(ts)
    adesso = time.localtime()
    h = "l'una" if t.tm_hour == 1 else "mezzanotte" if t.tm_hour == 0 else f"le {t.tm_hour}"      # si usa dopo «verso»
    m = "" if t.tm_min == 0 else " e mezza" if t.tm_min == 30 else f" e {t.tm_min}"
    giorno = "" if t.tm_yday == adesso.tm_yday and t.tm_year == adesso.tm_year else " di domani"
    return f"{h}{m}{giorno}"


def il_num(v):
    """«il 52», ma «l'87», «l'8», «l'11», «l'18»: l'articolo giusto davanti a un numero parlato."""
    k = int(round(float(v)))
    return ("l'" if k in (8, 11, 18) or 80 <= k <= 89 else "il ") + str(k)


def umidita_fuori(e):
    """' e l'87 per cento di umidità' (con lo spazio davanti) se la stazione meteo vicina dà il dato, altrimenti ''."""
    return f" e {il_num(e['umidita'])} per cento di umidità" if e.get("umidita") is not None else ""


def vecchio(ts):
    if not ts:
        return ""
    eta = time.time() - ts
    if eta < DATO_VECCHIO:
        return ""
    return f" Attenzione: l'ultimo dato risale a {int(eta // 60)} minuti fa."


def risposte(ctx):
    """intento -> funzione che restituisce il testo parlato. Tutte leggono lo stesso riepilogo della Dashboard."""
    def d():
        return ctx.dashboard()

    def temperatura():
        c = d()["casa"]
        if c["temp"] is None:
            return "Non ho la temperatura di casa."
        t = f"In casa ci sono {n(c['temp'])} gradi"
        t += f" e {il_num(c['umidita'])} per cento di umidità." if c["umidita"] is not None else "."
        if c.get("target") is not None:
            t += f" Lo Smarther è impostato a {n(c['target'])}."
        return t + vecchio(c.get("ts"))

    def umidita():
        c = d()["casa"]
        if c["umidita"] is None:
            return "Non ho l'umidità di casa."
        t = f"In casa c'è {il_num(c['umidita'])} per cento di umidità"
        t += f" e ci sono {n(c['temp'])} gradi." if c["temp"] is not None else "."
        return t + vecchio(c.get("ts"))

    def stato_pdc():
        x = d()
        s, modo = x["sistema"], x["modo"]
        if not s["pdc_accesa"]:
            t = "La pompa di calore è spenta."
        elif s["sbrinamento"] or x["riscaldamento"]["stato"] == "sbrinamento":
            t = "La pompa di calore sta facendo lo sbrinamento."
        elif modo == "casa":
            t = "La pompa di calore sta scaldando la casa."
            if x["riscaldamento"]["temp"] is not None:
                t += f" La mandata è a {n(x['riscaldamento']['temp'])} gradi."
        elif modo == "acs":
            t = "La pompa di calore sta scaldando l'acqua calda."
        elif modo == "riposo":
            t = "La pompa di calore è ferma, in riposo."
        else:
            return "Non ho dati recenti dalla pompa di calore."
        w = x["energia"]["potenza_w"]
        if w is not None and w >= 50:
            t += f" Sta assorbendo {n(w, 0)} watt."
        if x["esterna"]["temp"] is not None:
            t += f" Fuori ci sono {n(x['esterna']['temp'])} gradi{umidita_fuori(x['esterna'])}."
        return t

    def vecchio_fuori(e):
        if e.get("ts") and time.time() - e["ts"] > 7200:
            return f" Attenzione: l'ultimo dato della temperatura risale a {int((time.time() - e['ts']) // 3600)} ore fa."
        return ""

    def temperatura_fuori():
        e = d()["esterna"]
        if e["temp"] is None:
            return "Non ho la temperatura di fuori."
        t = f"Fuori ci sono {n(e['temp'])} gradi{umidita_fuori(e)}."
        if e.get("umidita") is None and e.get("umidita_attiva"):
            t += " Non ho l'umidità della stazione."
        return t + vecchio_fuori(e)

    def umidita_fuori_f():
        e = d()["esterna"]
        if e.get("umidita") is None:
            if e["temp"] is None:
                return "Non ho né l'umidità né la temperatura di fuori."
            return f"Non ho l'umidità di fuori. Ci sono {n(e['temp'])} gradi." + vecchio_fuori(e)
        t = f"Fuori c'è {il_num(e['umidita'])} per cento di umidità"
        t += f" e ci sono {n(e['temp'])} gradi." if e["temp"] is not None else "."
        return t + vecchio_fuori(e)

    def acqua_calda():
        a = d()["acs"]
        if a["temp"] is None:
            return "Non ho la temperatura dell'acqua calda."
        t = f"L'acqua calda è a {n(a['temp'])} gradi."
        if a["target"] is not None:
            t += f" L'obiettivo è {n(a['target'])}."
        if a["stato"] == "attivo":
            t += " La pompa la sta scaldando adesso."
        elif a["stato"] == "spento":
            t += " Il bollitore è spento."
        return t

    def mandata():
        r = d()["riscaldamento"]
        if r["temp"] is None:
            return "Non ho la mandata del riscaldamento."
        t = f"La mandata è a {n(r['temp'])} gradi."
        if r["ritorno"] is not None:
            t += f" Il ritorno è a {n(r['ritorno'])}."
        return t

    def dati_casa(c):
        """'In casa ci sono 20,7 gradi e lo Smarther è impostato a 21.' (con le sole parti disponibili)"""
        t = ""
        if c["temp"] is not None:
            t += f" In casa ci sono {n(c['temp'])} gradi"
            t += " e lo Smarther è impostato a " + n(c["target"]) + "." if c.get("target") is not None else "."
        elif c.get("target") is not None:
            t += f" Lo Smarther è impostato a {n(c['target'])}."
        return t

    def riscaldamento_acceso():
        x = d()
        c, st = x["casa"], x["riscaldamento"]["stato"]
        if st == "spento":
            t = "No, il riscaldamento è disattivato."
            if c.get("target") is not None:
                t += f" Lo Smarther è impostato a {n(c['target'])}."
            return t
        if st == "sbrinamento":
            return "No, ora la pompa sta sbrinando."
        if x["modo"] == "acs":
            return "No, ora la pompa sta scaldando l'acqua calda."
        if x["modo"] == "nodati":
            return "Non ho dati recenti dalla pompa di calore."
        if st == "attivo":
            return "Sì, il riscaldamento è acceso." + dati_casa(c) + vecchio(c.get("ts"))
        t = "No, in questo momento il riscaldamento è spento." + dati_casa(c)
        if c.get("riparte"):
            t += f" Dovrebbe ripartire verso {ora_parlata(c['riparte'])}."
        return t + vecchio(c.get("ts"))

    def ripartenza():
        x = d()
        st = x["riscaldamento"]["stato"]
        if st == "spento":
            return "È disattivato."
        c = x["casa"]
        if c["richiesta"] or st == "attivo":
            return "È già acceso."
        if not c["riparte"]:
            return "Non riesco a stimare quando ripartirà: non ho abbastanza dati."
        return f"Dovrebbe ripartire verso {ora_parlata(c['riparte'])}."

    def termostato(acceso):
        def f():
            r = ctx.comando("smarther", acceso)
            parola = "acceso" if acceso else "spento"
            if r.get("ok"):
                return f"Fatto, ho {parola} il termostato."
            ctx.log.warning("comando Smarther %s non riuscito: %s", parola, r.get("errore"))
            return f"Non sono riuscito a {'accendere' if acceso else 'spegnere'} il termostato."
        return f

    return {
        "TemperaturaIntent": temperatura, "UmiditaIntent": umidita, "StatoPdcIntent": stato_pdc,
        "AcquaCaldaIntent": acqua_calda, "MandataIntent": mandata, "RiscaldamentoAccesoIntent": riscaldamento_acceso,
        "RipartenzaIntent": ripartenza, "TemperaturaFuoriIntent": temperatura_fuori, "UmidFuoriIntent": umidita_fuori_f,
        "AccendiTermostatoIntent": termostato(True), "SpegniTermostatoIntent": termostato(False),
    }


# ------------------------------------------------------------------ firma di Amazon

def _url_certificato_ok(url):
    """Solo https://s3.amazonaws.com[:443]/echo.api/... (regole di Amazon), niente utente/password, niente '..'."""
    try:
        u = urlsplit(str(url).strip())
        porta = u.port
    except ValueError:
        return False
    if u.scheme.lower() != "https" or (u.hostname or "").lower() != HOST_CERT:
        return False
    if u.username is not None or u.password is not None or porta not in (None, 443):
        return False
    return u.path.startswith("/echo.api/") and ".." not in u.path and "%" not in u.path and "\\" not in u.path


def _scarica(url):
    """Scarica la catena di certificati (testo PEM). Sostituibile nei test."""
    rq = urllib.request.Request(url, headers={"User-Agent": "pdc-pannello"})
    with urllib.request.urlopen(rq, timeout=SCARICA_ATTESA) as r:
        dati = r.read(CERT_MAX_BYTE + 1)
    if len(dati) > CERT_MAX_BYTE:
        raise ValueError("catena di certificati troppo grande")
    return dati


def _radici():
    """Certificati radice di fiducia del sistema (cache). Sostituibile nei test."""
    if "r" in _radici_cache:
        return _radici_cache["r"]
    from cryptography import x509
    lista = []

    def aggiungi(carica, dati):
        try:
            lista.append(carica(dati))
        except ValueError:               # in una versione futura le radici con numero di serie fuori norma daranno errore: si saltano
            pass
    with warnings.catch_warnings():        # qualche radice vecchia di Windows ha un numero di serie fuori norma: la libreria avvisa, ma e' innocua
        warnings.filterwarnings("ignore", message="Parsed a serial number")
        for der in ssl.create_default_context().get_ca_certs(binary_form=True):
            aggiungi(x509.load_der_x509_certificate, der)
        if not lista:                                    # Linux: i certificati stanno in un file, non in una cartella caricata
            import os
            percorsi = ssl.get_default_verify_paths()
            file = percorsi.cafile or percorsi.openssl_cafile
            if not (file and os.path.isfile(file)):
                try:
                    import certifi
                    file = certifi.where()
                except ImportError:
                    file = None
            if file and os.path.isfile(file):
                with open(file, "rb") as f:
                    testo = f.read()
                for blocco in re.findall(rb"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", testo, re.S):
                    aggiungi(x509.load_pem_x509_certificate, blocco)
    _radici_cache["r"] = lista
    return lista


def ferma(ctx):
    _fermo.set()


def _leggi_catena(url):
    from cryptography import x509
    pem = _scarica(url)
    blocchi = re.findall(rb"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", pem, re.S)
    if not 1 <= len(blocchi) <= 6:
        raise ValueError("catena di certificati non valida")
    return [x509.load_pem_x509_certificate(b) for b in blocchi]


def _rinnova(url):
    """Thread di sfondo: tiene fresca la copia del certificato, cosi' nessuna richiesta di Alexa aspetta il download."""
    attesa = RINNOVO
    while not _fermo.wait(attesa):
        try:
            cert = _leggi_catena(url)
            _cache_cert[url] = (time.time(), cert)
            attesa = RINNOVO
        except Exception:
            attesa = RINNOVO_RIPROVA


def _catena(url):
    ora = time.time()
    c = _cache_cert.get(url)
    if c and ora - c[0] < CERT_TTL:
        return c[1]
    cert = _leggi_catena(url)
    if len(_cache_cert) > 20:
        _cache_cert.clear()
    _cache_cert[url] = (ora, cert)
    with _lock:
        nuovo = url not in _rinnovi and len(_rinnovi) < 5
        if nuovo:
            _rinnovi.add(url)
    if nuovo:
        threading.Thread(target=_rinnova, args=(url,), daemon=True, name="alexa-certificato").start()
    return cert


def verifica_firma(rich):
    """-> (True, '') firma valida; (False, motivo) non valida; (None, motivo) impossibile verificare (manca la libreria)."""
    try:
        from cryptography import x509
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.x509.verification import PolicyBuilder, Store
    except ImportError:
        return None, "manca la libreria cryptography"
    url = rich.intestazioni.get("signaturecertchainurl", "")
    firma = rich.intestazioni.get("signature-256", "")
    if not url or not firma:
        return False, "intestazioni della firma mancanti"
    if not _url_certificato_ok(url):
        return False, "indirizzo del certificato non ammesso"
    try:
        cert = _catena(url.strip())
        radici = _radici()
        if not radici:
            return None, "nessun certificato radice di sistema"
        foglia = cert[0]
        v = PolicyBuilder().store(Store(radici)).build_server_verifier(x509.DNSName(NOME_CERT))
        v.verify(foglia, cert[1:])
        chiave = foglia.public_key()
        if not isinstance(chiave, rsa.RSAPublicKey):
            return False, "chiave del certificato non RSA"
        try:
            sig = base64.b64decode(firma.strip(), validate=True)
        except ValueError:
            return False, "firma non decodificabile"
        try:
            chiave.verify(sig, rich.corpo, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature:
            return False, "firma diversa dal contenuto"
    except Exception as e:           # certificato scaduto, nome sbagliato, catena non fidata, rete... tutto = non valida
        return False, f"{type(e).__name__}: {str(e)[:80]}"
    return True, ""


# ------------------------------------------------------------------ richieste di Alexa

def _risposta(testo, fine=True, reprompt=None):
    r = {"outputSpeech": {"type": "SSML", "ssml": "<speak>" + _ssml(testo) + "</speak>"}, "shouldEndSession": fine}
    if reprompt:
        r["reprompt"] = {"outputSpeech": {"type": "SSML", "ssml": "<speak>" + _ssml(reprompt) + "</speak>"}}
    return {"version": "1.0", "response": r}


def _ssml(t):
    return str(t).replace("&", " e ").replace("<", " ").replace(">", " ")


def _id(corpo, cosa):
    """applicationId / userId, dalla sessione o dal contesto."""
    try:
        s = (corpo.get("session") or {}).get(cosa[0]) or {}
        c = ((corpo.get("context") or {}).get("System") or {}).get(cosa[0]) or {}
        return str(s.get(cosa[1]) or c.get(cosa[1]) or "")
    except AttributeError:
        return ""


def _fresca(ts):
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return -60 <= time.time() - t.timestamp() <= ETA_MAX
    except (TypeError, ValueError):
        return False


def _gia_vista(rid):
    """True se questo requestId e' gia' arrivato (o manca). Si ricordano per ETA_MAX + 60 secondi, al massimo MAX_VISTE."""
    if not isinstance(rid, str) or not 8 <= len(rid) <= 200:
        return True
    ora = time.time()
    with _lock:
        for k in [k for k, t in _viste.items() if ora - t > ETA_MAX + 60]:
            del _viste[k]
        if rid in _viste:
            return True
        _viste[rid] = ora
        while len(_viste) > MAX_VISTE:          # pieno: si toglie la piu' vecchia, mai si blocca tutto
            del _viste[min(_viste, key=_viste.get)]
    return False


def richiesta(rich):
    ctx = _ctx["ctx"]
    _locale.firma = ""
    try:
        corpo = rich.json()
    except ValueError:
        corpo = None
    if not isinstance(corpo, dict):
        return 400, {"errore": "richiesta non valida"}
    modo = str(ctx.config().get("verifica_firma") or "spenta")
    if modo in ("avviso", "attiva"):
        ok, motivo = verifica_firma(rich)
        if ok:
            _locale.firma = " · firma Amazon valida"
        elif ok is None:
            _locale.firma = f" · firma non verificata ({motivo})"
            if modo == "attiva":        # «Attiva» non lascia passare cio' che non si riesce a controllare
                ctx.log.warning("firma Amazon non verificabile, richiesta rifiutata: %s", motivo)
                _stato("rifiutata una richiesta: firma non verificabile")
                return 403, {"errore": "non autorizzato"}
        else:
            _locale.firma = f" · firma NON valida ({motivo})"
            ctx.log.warning("firma Amazon non valida: %s", motivo)
            if modo == "attiva":
                _stato("rifiutata una richiesta")
                return 403, {"errore": "non autorizzato"}
    req = corpo.get("request") if isinstance(corpo.get("request"), dict) else {}
    if not _fresca(req.get("timestamp")):
        _stato("rifiutata una richiesta: data mancante o vecchia")
        return 400, {"errore": "richiesta scaduta"}
    app_id, utente = _id(corpo, ("application", "applicationId")), _id(corpo, ("user", "userId"))
    if not app_id.startswith("amzn1.ask.skill.") or not utente.startswith("amzn1.ask.account."):      # solo una verifica di forma
        _stato("rifiutata una richiesta: non sembra di Alexa")
        return 403, {"errore": "non autorizzato"}
    with _lock:
        cfg = ctx.config()
        # al primo uso si impara quale skill e' la tua; dopo, qualunque altra viene rifiutata
        if not cfg.get("skill_id"):
            ctx.salva("skill_id", app_id)
            cfg["skill_id"] = app_id
    if app_id != cfg["skill_id"]:
        _stato("rifiutata una richiesta: skill diversa da quella registrata")
        return 403, {"errore": "non autorizzato"}

    if _gia_vista(req.get("requestId")):       # solo dopo aver verificato la skill: chi sbaglia skill non riempie la memoria
        _stato("rifiutata una richiesta: ripetuta o senza identificativo")
        return 400, {"errore": "richiesta non valida"}
    tipo = req.get("type")
    if tipo == "LaunchRequest":
        _stato("ultima richiesta: apertura della skill")
        return _risposta("Dimmi pure: temperatura, umidità, com'è fuori, pompa di calore, acqua calda, o accendi e spegni il termostato.",
                         False, "Cosa vuoi sapere?")
    if tipo == "SessionEndedRequest":
        return {"version": "1.0", "response": {}}
    if tipo != "IntentRequest":
        return _risposta("Non ho capito.")
    intento = req.get("intent")
    nome = str(intento.get("name") or "") if isinstance(intento, dict) else ""
    if nome in ("AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NavigateHomeIntent"):
        return _risposta("D'accordo.")
    if nome == "AMAZON.HelpIntent":
        return _risposta("Puoi chiedermi la temperatura, l'umidità, com'è fuori, cosa fa la pompa di calore, l'acqua calda, la mandata, "
                         "se il riscaldamento è acceso, quando riparte, oppure dirmi di accendere o spegnere il termostato.", False, "Cosa vuoi sapere?")
    f = risposte(ctx).get(nome)
    if not f:
        return _risposta("Non ho capito la domanda.")
    try:
        testo = f()
    except Exception:
        ctx.log.exception("errore nell'intento %s", nome)
        testo = "Non riesco a leggere i dati in questo momento."
    _stato(f"ultima richiesta: {nome} alle {time.strftime('%H:%M')}")
    return _risposta(testo)
