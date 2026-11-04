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
            f = next((c / f"{nome}.{est}" for c in cartelle_loghi(app.base) if (c / f"{nome}.{est}").is_file()), None)
            try:
                if f is not None:
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
    if c and ora - c[0] < 600:
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
    "1011": ("Raffrescamento: limite massimo temperatura acqua", "°C", 25, 18, 25),
    "1012": ("Raffrescamento: limite minimo temperatura acqua", "°C", 16, 5, 18),
    "1021": ("Raffrescamento: limite massimo temperatura ambiente", "°C", 30, 28, 30),
    "1022": ("Raffrescamento: limite minimo temperatura ambiente", "°C", 18, 18, 28),
    "1031": ("Riscaldamento: limite massimo temperatura acqua", "°C", 70, 37, 70),
    "1032": ("Riscaldamento: limite minimo temperatura acqua", "°C", 25, 15, 37),
    "1041": ("Riscaldamento: limite massimo temperatura ambiente", "°C", 30, 18, 30),
    "1042": ("Riscaldamento: limite minimo temperatura ambiente", "°C", 16, 16, 18),
    "1051": ("Acqua calda: limite massimo temperatura bollitore", "°C", 55, 50, 70),
    "1052": ("Acqua calda: limite minimo temperatura bollitore", "°C", 40, 30, 40),
    "2011": ("Curva riscaldamento: temperatura esterna, punto freddo", "°C", -10, -20, 5),
    "2012": ("Curva riscaldamento: temperatura esterna, punto caldo", "°C", 15, 10, 20),
    "2021": ("Curva riscaldamento zona 1: temperatura acqua al punto freddo", "°C", 40, 17, 65),
    "2022": ("Curva riscaldamento zona 1: temperatura acqua al punto caldo", "°C", 25, 17, 65),
    "2031": ("Curva riscaldamento zona 2: temperatura acqua al punto freddo", "°C", 50, 17, 65),
    "2032": ("Curva riscaldamento zona 2: temperatura acqua al punto caldo", "°C", 35, 17, 65),
    "2041": ("Tipo di emissione per il riscaldamento", "", 1, 1, 2),
    "2051": ("Curva raffrescamento: temperatura esterna, punto freddo", "°C", 30, 25, 35),
    "2052": ("Curva raffrescamento: temperatura esterna, punto caldo", "°C", 40, 35, 45),
    "2061": ("Curva raffrescamento zona 1: temperatura acqua al punto caldo", "°C", 25, 5, 25),
    "2062": ("Curva raffrescamento zona 1: temperatura acqua al punto freddo", "°C", 18, 5, 25),
    "2071": ("Curva raffrescamento zona 2: temperatura acqua al punto caldo", "°C", 18, 5, 25),
    "2072": ("Curva raffrescamento zona 2: temperatura acqua al punto freddo", "°C", 5, 5, 25),
    "2081": ("Tipo di emissione per il raffrescamento", "", 1, 1, 2),
    "2091": ("Termostato esterno 1 (zona a pavimento)", "", 0, 0, 4),
    "2092": ("Termostato esterno 2 (zona ventilconvettori)", "", 0, 0, 4),
    "2093": ("Controllo con il sensore di temperatura ambiente del telecomando", "", 4, 1, 4),
    "3011": ("Acqua calda: avvio in base alla temperatura del bollitore", "", 1, 0, 2),
    "3021": ("Acqua calda: temperatura massima con la sola pompa di calore", "°C", 55, 45, 55),
    "3022": ("Acqua calda: differenziale di temperatura per lo spegnimento della pompa", "°C", 0, 0, 10),
    "3023": ("Acqua calda: differenziale di temperatura per l'accensione della pompa", "°C", 5, 5, 30),
    "3024": ("Tempo minimo di riscaldamento tra due cicli acqua calda", "min", 5, 1, 20),
    "3025": ("Tempo massimo di un ciclo acqua calda", "min", 30, 5, 95),
    "3026": ("Tempo massimo di riscaldamento dopo l'acqua calda", "ore", 3, 0.5, 10),
    "3031": ("Resistenza del bollitore (BSH) per l'acqua calda", "", 1, 0, 1),
    "3032": ("Ritardo di avvio della resistenza del bollitore", "min", 20, 20, 95),
    "3033": ("Scarto di temperatura per lo spegnimento della resistenza del bollitore", "°C", 0, 0, 4),
    "3041": ("Disinfezione anti-legionella", "", 1, 0, 1),
    "3042": ("Disinfezione: giorno della settimana", "", 5, 0, 7),
    "3043": ("Disinfezione: ora di avvio", "ore", 23, 0, 23),
    "3044": ("Disinfezione: temperatura da raggiungere", "°C", 70, 40, 70),
    "3045": ("Disinfezione: tempo di mantenimento", "min", 10, 5, 60),
    "3046": ("Disinfezione: durata massima del ciclo", "ore", 8, 1, 24),
    "3051": ("Spegnimento forzato dell'acqua calda a tempo", "", 0, 0, 1),
    "3052": ("Durata dello spegnimento forzato dell'acqua calda", "×10 min", 6, 3, 30),
    "3061": ("Pannello solare per l'acqua calda", "", 0, 0, 2),
    "3071": ("Posizione di riposo della valvola a 3 vie", "", 0, 0, 1),
    "3081": ("Potenza resistenza di integrazione (BUH), primo stadio", "kW", 2, 1, 6),
    "3082": ("Potenza resistenza di integrazione (BUH), secondo stadio", "kW", 2, 0, 6),
    "3083": ("Potenza resistenza del bollitore (BSH)", "kW", 3, 1, 6),
    "4011": ("Priorità tra riscaldamento e acqua calda", "", 0, 0, 1),
    "4012": ("Temperatura esterna sotto la quale vale la priorità al riscaldamento", "°C", 0, -15, 20),
    "4013": ("Temperatura esterna oltre la quale il riscaldamento si ferma", "°C", 35, 14, 35),
    "4021": ("Resistenza di integrazione (BUH) per il riscaldamento", "", 0, 0, 2),
    "4022": ("Priorità tra resistenza di integrazione e resistenza del bollitore", "", 2, 0, 2),
    "4023": ("Compensazione per clima freddo", "", 1, 0, 1),
    "4024": ("Temperatura esterna sotto la quale entra la resistenza di integrazione", "°C", 0, -25, 35),
    "4025": ("Temperatura acqua a cui interviene la resistenza durante lo sbrinamento", "°C", 15, 10, 55),
    "4031": ("Caldaia di supporto", "", 0, 0, 1),
    "4032": ("Priorità della caldaia di supporto", "", 0, 0, 1),
    "4033": ("Temperatura esterna sotto la quale entra la caldaia", "°C", -15, -20, 5),
    "4041": ("Valvola miscelatrice", "", 0, 0, 2),
    "4042": ("Salto di temperatura obiettivo (ΔT) in riscaldamento", "°C", 10, 5, 15),
    "4043": ("Salto di temperatura obiettivo (ΔT) in raffrescamento", "°C", 10, 5, 15),
    "4044": ("Fattore di controllo della miscelatrice", "", 2, 1, 5),
    "4045": ("Intervallo di controllo della miscelatrice", "min", 2, 1, 30),
    "4046": ("Tempo di corsa della miscelatrice", "×10 s", 9, 6, 24),
    "4051": ("Pompa di circolazione a velocità variabile", "", 1, 0, 2),
    "4052": ("Salto di temperatura obiettivo (ΔT) della pompa a velocità variabile", "°C", 5, 2, 8),
    "4053": ("Fattore di controllo della pompa a velocità variabile", "", 2, 1, 3),
    "4061": ("Controllo a due zone", "", 0, 0, 1),
    "5011": ("Assenza: temperatura acqua in raffrescamento", "°C", 25, 5, 25),
    "5012": ("Assenza: temperatura ambiente in raffrescamento", "°C", 30, 18, 30),
    "5013": ("Assenza: temperatura acqua in riscaldamento", "°C", 15, 15, 55),
    "5014": ("Assenza: temperatura ambiente in riscaldamento", "°C", 16, 16, 30),
    "5015": ("Assenza: temperatura acqua, raffrescamento automatico zona 1", "°C", 25, 5, 25),
    "5016": ("Assenza: temperatura acqua, raffrescamento automatico zona 2", "°C", 25, 5, 25),
    "5017": ("Assenza: temperatura acqua, riscaldamento automatico zona 1", "°C", 15, 15, 55),
    "5018": ("Assenza: temperatura acqua, riscaldamento automatico zona 2", "°C", 15, 15, 55),
    "5019": ("Assenza: temperatura obiettivo del bollitore", "°C", 30, 30, 70),
    "5021": ("Risparmio: riduzione della temperatura dell'acqua calda", "°C", 5, 0, 40),
    "5022": ("Risparmio: modalità economica per l'acqua calda", "", 0, 0, 1),
    "5023": ("Risparmio: temperatura di accensione", "°C", 25, 0, 40),
    "5041": ("Controllo dei picchi di rete", "", 0, 0, 1),
    "5042": ("Componenti spenti a forza durante un picco", "", 0, 0, 3),
    "5043": ("Livello di tensione del segnale di ingresso", "", 1, 0, 1),
    "5051": ("Controllo del rapporto di frequenza", "", 0, 0, 1),
    "5061": ("Rapporto di capacità tra riscaldamento e acqua calda", "", None, None, None),
    "5081": ("Controllo con fotovoltaico", "", 0, 0, 1),
    "5082": ("Fotovoltaico: scarto di temperatura in raffrescamento", "°C", 1, 0, 5),
    "5083": ("Fotovoltaico: scarto di temperatura in riscaldamento", "°C", 1, 0, 5),
    "5091": ("Rete intelligente (smart grid)", "", 0, 0, 1),
    "5092": ("Rete intelligente: aumento temperatura riscaldamento", "°C", 2, 2, 5),
    "5093": ("Rete intelligente: aumento temperatura acqua calda", "°C", 5, 2, 5),
    "5094": ("Rete intelligente: modalità acqua calda", "", 0, 0, 1),
}
_FSV_SINO = {"3031", "3041", "3051", "4023", "4031", "4061", "5022", "5041", "5051", "5081", "5091"}
_TERM = {0: "Non usato", 1: "Il compressore segue solo il termostato",
         2: "Termostato o temperatura di mandata · pompa ferma a termostato spento",
         3: "Termostato o temperatura di mandata · pompa accesa a termostato spento",
         4: "Termostato o temperatura di mandata · pompa 7 min ferma e 3 accesa"}
_FSV_VALORI = {
    "2091": _TERM,
    "2092": _TERM,
    "2093": {1: "Il compressore segue solo il sensore ambiente",
             2: _TERM[2].replace("Termostato", "Sensore ambiente").replace("a termostato spento", "a sensore soddisfatto"),
             3: _TERM[3].replace("Termostato", "Sensore ambiente").replace("a termostato spento", "a sensore soddisfatto"),
             4: _TERM[4].replace("Termostato", "Sensore ambiente")},
    "3011": {1: "Avvio alla soglia di accensione", 2: "Avvio alla soglia di spegnimento"},
    "4011": {0: "Priorità all'acqua calda", 1: "Priorità al riscaldamento (sotto la temperatura di 4012)"},
    "4021": {0: "Nessuna resistenza di integrazione", 1: "Resistenza a due stadi", 2: "Resistenza a uno stadio"},
    "4022": {0: "Entrambe", 1: "Priorità alla resistenza di integrazione", 2: "Priorità alla resistenza del bollitore"},
    "4041": {0: "Non installata", 1: "Controllo sulla differenza di temperatura", 2: "Controllo sulla differenza del valore della curva"},
    "4051": {0: "Non usata", 1: "Uscita al 100%", 2: "Uscita al 70%"},
    "2041": {1: "Pavimento radiante", 2: "Ventilconvettori o radiatori"},
    "2081": {1: "Pavimento radiante", 2: "Ventilconvettori"},
    "3042": {0: "Domenica", 1: "Lunedì", 2: "Martedì", 3: "Mercoledì", 4: "Giovedì", 5: "Venerdì", 6: "Sabato",
             7: "Ogni giorno"},
    "3071": {0: "Verso il riscaldamento", 1: "Verso il bollitore"},
    "5043": {0: "Bassa", 1: "Alta"},
    "5094": {0: "Solo pompa di calore (55 °C)", 1: "Pompa e resistenza (70 °C)"},
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


def _fsv_testo(codice, n, grezzo, unita):
    """Il valore come lo leggerebbe una persona: testo dove il codice e' una scelta, unita' dove c'e'."""
    if n is None:
        return str(grezzo)
    k = int(n) if n == int(n) else None
    if codice in _FSV_VALORI and k in _FSV_VALORI[codice]:
        return f"{_FSV_VALORI[codice][k]} · {_num_it(n)}"
    if codice in _FSV_SINO and k in (0, 1):
        return f"{'Sì' if k else 'No'} · {k}"
    if unita in _FSV_MOLT:
        m, u = _FSV_MOLT[unita]
        return f"{_num_it(n)} ({_num_it(n * m)} {u})"
    return f"{_num_it(n)} {unita}".strip()


def impostazioni_installatore(app, ult):
    """Tutti i parametri FSV letti dalla pompa, raggruppati per menu. Solo lettura."""
    codici = sorted(k[4:] for k in ult if k.startswith("fsv."))
    with closing(app.db._c()) as c:
        righe = c.execute("SELECT serie, ts, valore FROM stati WHERE serie LIKE 'fsv.%' ORDER BY serie, ts").fetchall()
    storia = {}
    for serie, ts, v in righe:
        storia.setdefault(serie[4:], []).append((ts, v))
    gruppi, letto = {}, 0
    for cod in codici:
        r = ult["fsv." + cod]
        grezzo = r[3] if r[3] not in (None, "") else (_num_it(r[2]) if r[2] is not None else "")
        n = _fsv_numero(grezzo) if grezzo != "" else None
        cat = FSV_CATALOGO.get(cod)
        nome, unita, rif, lo, hi = cat if cat else ("Codice non in elenco", "", None, None, None)
        riga = {"id": cod, "nome": nome, "v": _fsv_testo(cod, n, grezzo, unita), "noto": bool(cat),
                "rif": _fsv_testo(cod, float(rif), rif, unita) if rif is not None else None,
                "diverso": bool(rif is not None and n is not None and abs(n - rif) > 1e-9),
                "limiti": f"da {_num_it(lo)} a {_num_it(hi)} {unita}".strip() if lo is not None else None,
                "fuori": bool(lo is not None and n is not None and not (lo - 1e-9 <= n <= hi + 1e-9)),
                "cambio": None}
        st = storia.get(cod, [])
        if len(st) >= 2:
            riga["cambio"] = {"ts": st[-1][0], "prima": _fsv_testo(cod, _fsv_numero(st[-2][1]), st[-2][1], unita)}
        letto = max(letto, r[1] or 0)
        menu = cod[:2] if cat else "altri"
        gruppi.setdefault(menu, []).append(riga)
    out = []
    for m in sorted(gruppi, key=lambda x: (x == "altri", x)):
        out.append({"id": m, "titolo": FSV_MENU.get(m, "Altri codici letti dalla pompa"), "righe": gruppi[m]})
    tutte = [r for g in out for r in g["righe"]]
    return {"gruppi": out, "totale": len(tutte), "diversi": sum(1 for r in tutte if r["diverso"]),
            "cambiati": sum(1 for r in tutte if r["cambio"]), "fuori": sum(1 for r in tutte if r["fuori"]),
            "letto": letto}


@api("/api/dispositivi")
def api_dispositivi(app, q):
    ora = int(time.time())
    cfg = app.cfg.leggi()
    cfg.setdefault("dispositivi", {})
    ult = {r[0]: r for r in app.db.ultimi()}
    tol = max(900, int(app.intervallo_pdc() * 3))
    out = cornice(app, ora)
    out.update({
        "schede": [_scheda_pdc(app, ult, cfg, ora, tol), _scheda_smarther(app, ult, cfg, ora, tol)],
        "storico": storico_versioni(app),
        "installatore": impostazioni_installatore(app, ult),
        "comandi": elenco_comandi(app),
        "loghi": elenco_loghi(app),
        "loghi_bianchi": bool(cfg["dispositivi"].get("loghi_bianchi")),
    })
    return out
