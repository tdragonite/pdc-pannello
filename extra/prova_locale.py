#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prova_locale.py - "La mia pompa parla in locale?"  (sola LETTURA)

Cosa fa, in 3 tappe:
  1. RETE      : guarda se l'IP risponde sulle porte locali Samsung (UDP/DTLS) e prova la
                 lettura "in chiaro" di /oic/d (modello, nome, id).
  2. LOGIN     : prova a collegarsi (DTLS) con un certificato autofirmato generato al volo,
                 come fa il fork localthings. Dice se la pompa lo ACCETTA o lo RIFIUTA.
  3. LETTURA   : se accettato, legge (GET) TUTTE le risorse e salva tutto in un JSON.
                 Poi stampa un riassunto: ci sono ehscycle/ehsfsv? c'e' qualcosa di
                 energia/consumi? quali temperature/portate?

NON scrive niente sulla pompa (nessun POST/PUT/DELETE: non c'e' proprio nel codice).
Contatto esterno unico: una connessione TLS a connect-v2.samsungiotcloud.com per leggere
un UUID pubblico dal certificato (serve per il certificato locale). Si puo' evitare con --uuid.

Uso:   python prova_locale.py 192.168.1.50
       python prova_locale.py 192.168.1.50 --uuid xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
Requisiti: Python >= 3.11. Alla prima esecuzione crea da solo una cartella "venv_prova"
accanto allo script e ci installa smartthings-local e cbor2 (non tocca il resto del PC).

IMPORTANTE: la pompa accetta UNA sola sessione DTLS alla volta. Se hai Home Assistant con
localthings gia' collegato, spegnilo prima. L'app SmartThings (cloud) non da' fastidio.
"""
import argparse, json, os, re, socket, ssl, subprocess, sys, time, datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(AQUI, "venv_prova")


def _python_venv():
    return os.path.join(VENV, "Scripts" if os.name == "nt" else "bin",
                        "python.exe" if os.name == "nt" else "python")


def bootstrap():
    """Se mancano le librerie, crea il venv, installa e rilancia se stesso."""
    try:
        import smartthings_local, cbor2, cryptography  # noqa
        return
    except ImportError:
        pass
    if sys.version_info < (3, 11):
        print("Serve Python >= 3.11 (hai %s)." % sys.version.split()[0]); sys.exit(2)
    if os.environ.get("PROVA_RELANCIATO"):
        print("Installazione librerie fallita, vedi sopra."); sys.exit(2)
    if not os.path.exists(_python_venv()):
        print("Prima esecuzione: creo l'ambiente isolato in", VENV)
        subprocess.check_call([sys.executable, "-m", "venv", VENV])
    print("Installo smartthings-local e cbor2 (puo' volerci un minuto)...")
    subprocess.check_call([_python_venv(), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                           "smartthings-local", "cbor2"])
    env = dict(os.environ, PROVA_RELANCIATO="1")
    sys.exit(subprocess.call([_python_venv()] + sys.argv, env=env))


# ----------------------------------------------------------------------------- utilita'
def jsonable(o):
    if isinstance(o, (bytes, bytearray)):
        return {"__hex__": bytes(o).hex()}
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [jsonable(v) for v in o]
    return o


def ok(m): print("  [OK]  " + m)
def ko(m): print("  [NO]  " + m)
def info(m): print("        " + m)


def uuid_da_samsung():
    from cryptography import x509
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    host = "connect-v2.samsungiotcloud.com"
    with socket.create_connection((host, 443), timeout=15) as raw, \
            ctx.wrap_socket(raw, server_hostname=host) as tls:
        der = tls.getpeercert(binary_form=True)
    cert = x509.load_der_x509_certificate(der)
    for a in cert.subject:
        if a.oid == x509.oid.NameOID.ORGANIZATIONAL_UNIT_NAME:
            m = re.search(r"uuid:([0-9a-f-]+)", str(a.value), re.I)
            if m:
                return m.group(1)
    raise RuntimeError("UUID non trovato nel certificato")


def certificato_autofirmato(uuid):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    chiave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "KR"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Samsung Electronics"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "uuid:" + uuid),
        x509.NameAttribute(NameOID.COMMON_NAME, "urn:uuid:" + uuid),
    ])
    ora = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome)
            .public_key(chiave.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(ora).not_valid_after(ora + datetime.timedelta(days=3650))
            .sign(chiave, hashes.SHA256()))
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = chiave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()).decode()
    return cert_pem, key_pem


# ----------------------------------------------------------------------------- tappa 1
def tappa_rete(host, risultato):
    import cbor2
    from smartthings_local.protocol.dtls_probe import probe_dtls_ports
    from smartthings_local.protocol.ocf_discovery import read_plaintext_ocf_resource, discover_ocf_secure_ports
    print("\n== TAPPA 1: rete =========================================")
    rete = risultato.setdefault("rete", {})
    try:
        socket.gethostbyname(host)
    except OSError:
        ko("indirizzo non valido: " + host); return None
    # 1) la pompa dichiara da se' la porta sicura (lettura in chiaro di /oic/res)
    annunciate = []
    for porta in (5683, 49153):
        try:
            d = discover_ocf_secure_ports(host, discovery_port=porta, timeout=4.0, retries=2)
            rete.setdefault("scoperta_porte", {})[str(porta)] = {
                "risposta": bool(d.response_received), "porte": list(d.ports),
                "errore": getattr(d, "error_code", None)}
            for x in d.ports:
                if x not in annunciate:
                    annunciate.append(x)
            if annunciate:
                break
        except Exception as e:
            rete.setdefault("errori_scoperta", []).append("%d: %r" % (porta, e))
    rete["porte_annunciate"] = annunciate
    if annunciate:
        ok("la pompa dichiara queste porte sicure: %s" % annunciate)
    else:
        ko("la pompa non dichiara nessuna porta sicura in chiaro")
    # 2) sonda DTLS: porte annunciate + intervallo classico (max 32)
    porte = list(dict.fromkeys(annunciate + list(range(49152, 49161))))[:32]
    r = None
    try:
        r = probe_dtls_ports(host, porte, preferred_port=(annunciate[0] if annunciate else 49154),
                             timeout=3.0, retries=2)
        rete["dtls_esito"] = r.outcome
        rete["dtls_porta_scelta"] = r.selected_port
        rete["dtls_porte_che_rispondono"] = list(r.responder_ports)
    except Exception as e:
        ko("sonda DTLS fallita: %r" % (e,)); rete["errore_sonda"] = repr(e)
    candidati = []
    if r is not None and r.selected_port:
        candidati.append(r.selected_port)
    if r is not None:
        candidati += [x for x in r.responder_ports if x not in candidati]
    # se la sonda stateless non vede niente ma la pompa ha dichiarato una porta, la proviamo comunque
    for x in annunciate:
        if x != 5684 and x not in candidati:
            candidati.append(x)
    rete["candidati"] = candidati
    if candidati:
        ok("porte che proveremo per il login: %s" % candidati)
    else:
        ko("nessuna porta DTLS utilizzabile (esito sonda: %s)" % (getattr(r, "outcome", "?"),))
    # lettura in chiaro di /oic/d
    for porta in (5683, 49153):
        try:
            p = read_plaintext_ocf_resource(host, "/oic/d", port=porta, timeout=3.0, retries=1)
            if p.successful:
                d = cbor2.loads(p.payload)
                rete["oic_d_in_chiaro"] = jsonable(d)
                rete["porta_in_chiaro"] = porta
                try:
                    pp = read_plaintext_ocf_resource(host, "/oic/p", port=porta, timeout=3.0, retries=1)
                    if pp.successful:
                        rete["oic_p_in_chiaro"] = jsonable(cbor2.loads(pp.payload))
                        info("/oic/p in chiaro: modello=%r" % (rete["oic_p_in_chiaro"].get("mnmo"),))
                except Exception:
                    pass
                ok("/oic/d in chiaro su porta %d: nome=%r, tipo=%r" % (porta, d.get("n"), d.get("rt")))
                break
        except Exception as e:
            rete.setdefault("errori_in_chiaro", []).append("%d: %r" % (porta, e))
    else:
        info("nessuna risposta in chiaro (normale per molti apparecchi)")
    return candidati


# ----------------------------------------------------------------------------- tappa 2
def tappa_login(host, porte, uuid, risultato):
    from smartthings_local.protocol.auth import CertificateAuth
    from smartthings_local.protocol.dtls_session import DtlsCoapSession
    print("\n== TAPPA 2: login locale (certificato autofirmato) ========")
    login = risultato.setdefault("login", {})
    if not porte:
        ko("salto: nessuna porta DTLS trovata"); login["esito"] = "nessuna_porta"; return None
    if not uuid:
        try:
            info("leggo l'UUID pubblico da Samsung (unico contatto esterno)...")
            uuid = uuid_da_samsung()
        except Exception as e:
            ko("non riesco a leggere l'UUID da Samsung: %r" % (e,))
            info("rilancia con --uuid <uuid> (te lo posso dare io) oppure verifica la rete.")
            login["esito"] = "uuid_non_disponibile"; return None
    login["uuid"] = uuid
    cert, key = certificato_autofirmato(uuid)
    auth = CertificateAuth.from_memory(cert, key)
    errori = {}
    for porta in porte:
        for tentativo in (1, 2):
            sess = None
            try:
                sess = DtlsCoapSession(host, porta, auth=auth)
                sess.connect(timeout=20)
                sess.start_reader()
                ok("collegato! porta %d (tentativo %d)" % (porta, tentativo))
                login.update(esito="ok", porta=porta)
                return sess
            except Exception as e:
                errori["%d/%d" % (porta, tentativo)] = repr(e)
                if sess is not None:
                    try: sess.close()
                    except Exception: pass
                time.sleep(2)
    login["esito"] = "fallito"; login["errori"] = errori
    testo = " ".join(errori.values()).lower()
    ko("collegamento non riuscito su nessuna porta.")
    if any(k in testo for k in ("certificate", "bad_certificate", "unknown_ca", "handshake failure", "alert")):
        info("-> la pompa sembra RIFIUTARE il certificato (alert TLS). Probabile serva una chiave PSK.")
        login["lettura"] = "certificato_rifiutato"
    elif "timeout" in testo or "timed out" in testo:
        info("-> timeout: o c'e' gia' un'altra sessione DTLS aperta (Home Assistant? riavvia la pompa "
             "dal quadro per liberarla) oppure la pompa ignora il certificato in silenzio.")
        login["lettura"] = "timeout"
    for k, v in list(errori.items())[:4]:
        info("%s: %s" % (k, v[:200]))
    try:
        diagnosi(host, porte[0], auth, risultato)
    except Exception as e:
        info("diagnosi fallita: %r" % (e,))
    return None



def diagnosi(host, porta, auth, risultato):
    """Perche' e' fallito? Legge doxm in chiaro e fa UNA stretta di mano diagnostica per vedere l'alert."""
    import cbor2
    from smartthings_local.protocol.dtls_probe import diagnose_dtls_handshake
    from smartthings_local.protocol.ocf_discovery import read_plaintext_ocf_resource
    print("\n== DIAGNOSI del rifiuto ====================================")
    dg = risultato.setdefault("diagnosi", {})
    try:
        r = read_plaintext_ocf_resource(host, "/oic/sec/doxm", port=5683, timeout=3.0, retries=1)
        dg["doxm_code"] = r.code
        if r.successful and r.payload:
            d = cbor2.loads(r.payload)
            dg["doxm"] = jsonable(d)
            sct = d.get("sct")
            info("doxm: sct=%r (tipi di credenziale ammessi), owned=%r, oxmsel=%r" % (sct, d.get("owned"), d.get("oxmsel")))
            if isinstance(sct, int):
                psk = bool(sct & 1) and not (sct & 8)
                info("-> la pompa %s credenziali PSK" % ("ammette" if psk else "NON annuncia"))
        else:
            info("doxm in chiaro non leggibile (codice %r)" % (r.code,))
    except Exception as e:
        dg["doxm_errore"] = repr(e)
    try:
        time.sleep(3)
        res = diagnose_dtls_handshake(host, porta, auth=auth, timeout=5.0, retries=2)
        dg["esito"] = getattr(res, "outcome", None)
        dg["alert"] = list(res.alert) if getattr(res, "alert", None) else None
        dg["messaggi_server"] = list(getattr(res, "handshake_msgs", []) or [])
        dg["conteggi"] = dict(getattr(res, "handshake_counts", {}) or {})
        info("esito stretta di mano: %s" % dg["esito"])
        info("alert ricevuto dalla pompa: %s" % (dg["alert"],))
        info("messaggi inviati dalla pompa: %s" % (dg["messaggi_server"],))
        a = (dg["alert"] or [0, ""])[1] if dg["alert"] else ""
        if a in ("bad_certificate", "unknown_ca", "certificate_unknown", "unsupported_certificate",
                 "certificate_expired", "access_denied", "handshake_failure", "illegal_parameter"):
            ko("la pompa RIFIUTA il certificato locale (alert %s)." % a)
        elif not dg["alert"]:
            info("nessun alert: la pompa non risponde alla stretta di mano (sessione occupata o credenziale ignorata).")
    except Exception as e:
        dg["diagnosi_errore"] = repr(e)
        info("diagnosi non riuscita: %r" % (e,))

# ----------------------------------------------------------------------------- tappa 3
PAROLE = ("energy", "power", "watt", "kwh", "consum", "ehscycle", "ehsfsv", "flow", "pump", "temperature",
          "outdoor", "defrost", "compressor", "dhw", "heater", "water")


def leggi(sess, path, query=()):
    import cbor2
    segs = [s for s in path.strip("/").split("/") if s]
    code, blob = sess.get(segs, query=tuple(query), timeout=10.0)
    corpo = None
    if blob:
        try: corpo = cbor2.loads(blob)
        except Exception: corpo = {"__raw_hex__": bytes(blob).hex()}
    return code, corpo


def tappa_lettura(sess, risultato):
    print("\n== TAPPA 3: lettura risorse (solo GET) ====================")
    dump = risultato.setdefault("risorse", {})
    for p in ("/oic/d", "/oic/p", "/oic/res"):
        try:
            code, corpo = leggi(sess, p)
            dump[p] = {"code": hex(code), "corpo": jsonable(corpo)}
            time.sleep(0.4)
        except Exception as e:
            dump[p] = {"errore": repr(e)}
    d = dump.get("/oic/d", {}).get("corpo") or {}
    pp = dump.get("/oic/p", {}).get("corpo") or {}
    if d: ok("dispositivo: %r  tipo=%r  modello=%r" % (d.get("n"), d.get("rt"), pp.get("mnmo")))
    links = dump.get("/oic/res", {}).get("corpo")
    hrefs = []
    if isinstance(links, list):
        for l in links:
            h = l.get("href") if isinstance(l, dict) else None
            if h:
                hrefs.append(h)
    hrefs = [h for h in dict.fromkeys(hrefs) if not h.startswith("/oic/") and not h.startswith("/introspection")]
    ok("la pompa elenca %d risorse (oltre alle /oic)" % len(hrefs))
    # collezione /device/0 in batch (di solito contiene gia' quasi tutto)
    for base in [h for h in hrefs if re.fullmatch(r"/device/\d+", h)]:
        try:
            code, corpo = leggi(sess, base, ("if=oic.if.b",))
            dump[base + "?batch"] = {"code": hex(code), "corpo": jsonable(corpo)}
            time.sleep(0.4)
        except Exception as e:
            dump[base + "?batch"] = {"errore": repr(e)}
    letti = errori = 0
    for h in hrefs:
        if h in dump or re.fullmatch(r"/device/\d+", h):
            continue
        try:
            code, corpo = leggi(sess, h)
            dump[h] = {"code": hex(code), "corpo": jsonable(corpo)}
            letti += 1
        except Exception as e:
            dump[h] = {"errore": repr(e)}; errori += 1
        time.sleep(0.4)  # gentile con la pompa
    info("risorse lette: %d  errori: %d" % (letti, errori))
    return hrefs


def riassunto(hrefs, risultato):
    print("\n== RIASSUNTO ===============================================")
    ris = risultato.get("risorse", {})
    trovati = {k: [h for h in hrefs if k in h.lower()] for k in
               ("ehscycle", "ehsfsv", "energy", "power", "consum", "temperature", "flow")}
    for k, v in trovati.items():
        (ok if v else ko)("%-12s %s" % (k, ", ".join(v[:6]) if v else "assente"))
    # blob ciclo: lunghezza
    c = ris.get("/ehscycle/vs/0", {}).get("corpo")
    if isinstance(c, dict):
        for k, v in c.items():
            if isinstance(v, str) and len(v) > 40:
                info("ehscycle: campo %s lungo %d caratteri (il nostro decoder lo sa leggere)" % (k, len(v)))
    # chiavi che sembrano energia dentro qualunque risorsa
    sospette = []
    def scandisci(o, perc):
        if isinstance(o, dict):
            for k, v in o.items():
                if any(w in str(k).lower() for w in ("energy", "kwh", "watt", "consum", "power")):
                    sospette.append((perc, k, v if not isinstance(v, (dict, list)) else "..."))
                scandisci(v, perc)
        elif isinstance(o, list):
            for v in o: scandisci(v, perc)
    for h, v in ris.items():
        scandisci(v.get("corpo") if isinstance(v, dict) else None, h)
    if sospette:
        print("  Campi che somigliano a potenza/energia:")
        for perc, k, v in sospette[:25]:
            info("%s -> %s = %s" % (perc, k, str(v)[:60]))
    else:
        ko("nessun campo che somigli a energia/consumi (il COP richiederebbe un contatore esterno)")


def main():
    ap = argparse.ArgumentParser(description="Prova se la pompa Samsung EHS parla in locale (sola lettura).")
    ap.add_argument("ip", help="IP della pompa nella LAN, es. 192.168.1.50")
    ap.add_argument("--uuid", help="UUID per il certificato (evita il contatto con Samsung)")
    ap.add_argument("--solo-rete", action="store_true", help="ferma dopo la tappa 1 (nessun login)")
    a = ap.parse_args()
    risultato = {"quando": datetime.datetime.now().isoformat(timespec="seconds"), "ip": a.ip,
                 "python": sys.version.split()[0]}
    try:
        import smartthings_local
        risultato["smartthings_local"] = getattr(smartthings_local, "__version__", "?")
    except Exception:
        pass
    print("Prova locale su", a.ip, "(sola lettura)")
    porte = tappa_rete(a.ip, risultato)
    sess = None
    if not a.solo_rete:
        sess = tappa_login(a.ip, porte, a.uuid, risultato)
    hrefs = []
    if sess is not None:
        try:
            hrefs = tappa_lettura(sess, risultato)
            riassunto(hrefs, risultato)
        finally:
            try: sess.close()
            except Exception: pass
    out = os.path.join(AQUI, "risultato_prova_locale.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(jsonable(risultato), f, ensure_ascii=False, indent=1)
    print("\nHo salvato tutto in:", out)
    print("Mandami quel file (non contiene password; contiene l'id del dispositivo e l'IP).")


if __name__ == "__main__":
    bootstrap()
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrotto.")
