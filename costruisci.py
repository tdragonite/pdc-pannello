#!/usr/bin/env python3
"""Riunisce sorgenti/*.py e sorgenti/web/* in un unico pdc_pannello.py.  Uso:  python costruisci.py"""
from pathlib import Path
import py_compile

AQUI = Path(__file__).resolve().parent
S = AQUI / "sorgenti"
assets = {}
for f in sorted((S / "web").iterdir()):
    t = f.read_text(encoding="utf-8")
    assert "'''" not in t, f"{f.name}: contiene ''' (non incorporabile)"
    assets[f.name] = t
blocco = "WEB_ASSETS = {\n" + "".join(f"    {n!r}: r'''{t}''',\n" for n, t in assets.items()) + "}"
out = ""
for i, p in enumerate(sorted(S.glob("[0-9]_*.py"))):
    t = p.read_text(encoding="utf-8")
    t = t.replace("WEB_ASSETS = {}  # @@ASSETS@@  (riempito da costruisci.py: pagine, stile e script)", blocco)
    if i:   # segnalibro di parte: serve al pannello per ricaricare a caldo solo le parti che cambiano (vedi 9_aggiorna.py)
        out += f"#@@PARTE {p.stem.split('_', 1)[1]}@@\n"
    out += t if t.endswith("\n") else t + "\n"
dest = AQUI / "pdc_pannello.py"
dest.write_text(out, encoding="utf-8")
py_compile.compile(str(dest), doraise=True)
print(f"creato {dest.name}: {len(out.splitlines())} righe, {len(assets)} file web incorporati")
