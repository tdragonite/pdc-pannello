// Widget medio per Scriptable: casa, termostato, pompa di calore, acqua calda e fuori.
// 1) Incolla qui sotto l'INDIRIZZO che il plugin «Widget iPhone» mostra in Impostazioni -> Integrazioni -> Indirizzi pubblici.
const INDIRIZZO = "INCOLLA-QUI-L-INDIRIZZO";
// 1b) Incolla qui il TOKEN che hai scritto in Impostazioni -> Integrazioni -> Widget iPhone -> «Token del widget».
const TOKEN = "INCOLLA-QUI-IL-TOKEN";
// (L'indirizzo si ricorda da solo: nelle versioni future dello script puoi lasciare il testo qui sopra com'e'.)
const CHIAVE = "pdc_widget_indirizzo";
// Nome del Comando rapido (app Comandi) con l'unica azione Scriptable «Refresh All Widgets»: serve al tocco sull'ora in basso a destra.
const COMANDO_AGGIORNA = "Aggiorna widget";
let ind = INDIRIZZO, tok = TOKEN;
try {
  if (tok === "INCOLLA-QUI-IL-TOKEN") { if (Keychain.contains("pdc_widget_token")) tok = Keychain.get("pdc_widget_token"); }
  else Keychain.set("pdc_widget_token", tok);
} catch (e) {}
try {
  if (ind === "INCOLLA-QUI-L-INDIRIZZO") { if (Keychain.contains(CHIAVE)) ind = Keychain.get(CHIAVE); }
  else Keychain.set(CHIAVE, ind);
} catch (e) {}

// ---- colori (gli stessi della Dashboard)
const C = {
  testo: new Color("#eef2f8"), grigio: new Color("#a9b4c7"), grigio2: new Color("#8491a8"),
  viola: new Color("#a78bfa"), verde: new Color("#34d3b0"), corallo: new Color("#ff6b57"),
  azzurro: new Color("#4da3ff"), giallo: new Color("#ffb547"), rosso: new Color("#ff5d6c"), spento: new Color("#8491a8"),
};
const alfa = (hex, a) => new Color(hex, a);
const CHIP = {   // colore del testo e dello sfondo del riquadro in basso a sinistra
  chiama: [new Color("#ff8d7c"), alfa("#ff6b57", 0.18)], riparte: [C.viola, alfa("#a78bfa", 0.22)],
  fermo: [C.grigio, alfa("#ffffff", 0.08)], warn: [new Color("#ffc873"), alfa("#ffb547", 0.18)], bad: [C.rosso, alfa("#ff5d6c", 0.18)],
};
const PALLINO = {riscaldamento: C.corallo, acqua: C.azzurro, sbrinamento: C.giallo, riposo: C.spento, spenta: C.spento, nodati: C.spento};

// ---- misure (in punti). Il widget medio ha circa 315 punti utili: 13 + 132 + 8 + 1 + 8 + 152 = 314
const COL_SX = 132, COL_DX = 152, GAP = 8, SPOSTA = 13;
const PICCOLO = 13.5;       // corpo dei testi piccoli (etichetta + valore)
const MARGINE = 1.05;     // sicurezza: una scritta «sta» solo se la stima, +5%, entra nello spazio

// ---- stima della larghezza di una scritta, cosi' scegliamo NOI la grandezza (niente riduzioni automatiche di iOS).
// Larghezze in «em» dei caratteri di San Francisco, tarate sul widget vero (screenshot del 4/11): la stima sbaglia da -4% a +8%.
const LOW = {a: .55, b: .6, c: .53, d: .6, e: .56, f: .33, g: .6, h: .58, i: .27, j: .27, k: .55, l: .27, m: .88, n: .58, o: .6, p: .6, q: .6, r: .37,
  s: .5, t: .35, u: .58, v: .53, w: .8, x: .53, y: .53, z: .5, "à": .55, "è": .56, "é": .56, "ì": .27, "ò": .6, "ù": .58};
const UP = {A: .68, B: .66, C: .7, D: .74, E: .6, F: .58, G: .74, H: .74, I: .3, J: .5, K: .66, L: .56, M: .9, N: .76, O: .78, P: .64, Q: .78, R: .66,
  S: .62, T: .6, U: .74, V: .66, W: .96, X: .66, Y: .64, Z: .62};
function em(ch, peso) {
  if (/[0-9]/.test(ch)) return ch === "1" ? (peso === "light" ? 0.36 : 0.4) : (peso === "light" ? 0.56 : 0.6);
  if (ch === "," || ch === ".") return 0.27;
  if (ch === "°") return peso === "light" ? 0.38 : 0.42;
  if (ch === "%") return 0.9;
  if (ch === ":") return 0.27;
  if (ch === " ") return 0.26;
  if (ch === "-" || ch === "−") return 0.5;
  if (ch === "→") return 0.8;
  if (ch === "—") return 0.9;
  if (UP[ch]) return UP[ch] * (peso === "bold" ? 1.03 : 1);
  const w = LOW[ch] === undefined ? 0.55 : LOW[ch];
  return peso === "semibold" || peso === "bold" ? w : w * 0.97;
}
const largh = (t, grandezza, peso) => [...t].reduce((s, ch) => s + em(ch, peso), 0) * grandezza;
const entra = (parti, spazio) => parti.reduce((s, [t, g, p]) => s + largh(t, g, p), 0) * MARGINE <= spazio;
function scegli(grandezze, parti, spazio) {         // la prima grandezza (dalla piu' grande) che entra; se nessuna, la piu' piccola
  for (const g of grandezze) if (entra(parti(g), spazio)) return g;
  return grandezze[grandezze.length - 1];
}

// Fuori: i gradi con il decimale (come nel pannello), un solo corpo (22), umidità sempre sulla stessa riga: «Umidità 100%» entra con
// temperature fino a -9,9° (con -10,5° e 100% l'etichetta verrebbe troncata: a Ciriè praticamente mai).
const CORPO_FUORI = 22;

// ---- dati: dal pannello; se non risponde, l'ultimo salvato (con l'avviso)
const fm = FileManager.local();
const FILE = fm.joinPath(fm.documentsDirectory(), "widget_pdc.json");
async function leggi() {
  try {
    const r = new Request(ind); r.timeoutInterval = 12;
    if (tok !== "INCOLLA-QUI-IL-TOKEN") r.headers = {"X-Widget-Token": tok};
    const d = await r.loadJSON();
    if (!d || d.versione !== 1) throw new Error("risposta non valida");
    fm.writeString(FILE, JSON.stringify(d));
    return {d, vecchio: false};
  } catch (e) {
    if (fm.fileExists(FILE)) return {d: JSON.parse(fm.readString(FILE)), vecchio: true};
    return {d: null, vecchio: false};
  }
}

const FONT = {light: Font.lightSystemFont, regular: Font.systemFont, semibold: Font.semiboldSystemFont, bold: Font.boldSystemFont};
function testo(st, t, grandezza, peso, colore) {
  const x = st.addText(t); x.font = FONT[peso](grandezza); x.textColor = colore; x.lineLimit = 1;   // mai a capo, mai ridotto da iOS
  return x;
}
const titolo = (st, t, colore) => testo(st, t.toUpperCase(), 11.5, "bold", colore);
// «etichetta grigia + valore bianco in grassetto» (stessa regola per tutto il widget)
function coppia(st, etichetta, valore, g) { testo(st, etichetta, g, "regular", C.grigio); testo(st, valore, g, "semibold", C.testo); }

function costruisci({d, vecchio}) {
  const w = new ListWidget();
  w.backgroundGradient = (() => { const g = new LinearGradient(); g.colors = [new Color("#2a1420"), new Color("#1a0b14")]; g.locations = [0, 1]; return g; })();
  w.setPadding(10, 16, 14, 16);
  w.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);      // iOS decide lui quando aggiornare davvero
  try { w.url = ind.split("/plugin/")[0] + "/"; } catch (e) {}      // toccando il widget si apre il pannello

  if (!d) {
    titolo(w, "Pannello", C.grigio2); w.addSpacer(8);
    testo(w, "Nessuna risposta", 16, "semibold", C.testo);
    testo(w, "Controlla l'indirizzo nello script", 13, "regular", C.grigio);
    return w;
  }

  const riga = w.addStack(); riga.layoutHorizontally();
  riga.addSpacer(SPOSTA - 6);                                          // centra il blocco a occhio (i testi a destra sono piu' corti della colonna)
  const sx = riga.addStack(); sx.layoutVertically(); sx.size = new Size(COL_SX, 0);
  riga.addSpacer(GAP);
  const sep = riga.addStack(); sep.size = new Size(1, 0); sep.backgroundColor = alfa("#ffffff", 0.08);
  riga.addSpacer(GAP);
  const dx = riga.addStack(); dx.layoutVertically(); dx.size = new Size(COL_DX, 0);

  // ===== sinistra: casa
  const blocco = sx.addStack(); blocco.layoutVertically(); blocco.setPadding(0, 6, 0, 0);   // i testi partono 6pt dentro: cosi' il testo del riquadro in fondo si allinea a loro
  titolo(blocco, "Casa", C.viola);
  const gHero = scegli([48, 46, 44, 42, 40, 36], g => [[d.casa.temp, g, "light"]], COL_SX - 6);
  testo(blocco, d.casa.temp, gHero, "light", C.testo);
  blocco.addSpacer(5);                                                 // la coda della virgola non deve toccare la riga «Target»
  const kv = blocco.addStack(); kv.layoutHorizontally();
  const et = kv.addStack(); et.layoutVertically(); kv.addSpacer(10); const va = kv.addStack(); va.layoutVertically();
  testo(et, "Target", PICCOLO, "regular", C.grigio); testo(va, d.casa.target, PICCOLO, "semibold", C.testo);
  testo(et, "Umidità", PICCOLO, "regular", C.grigio); testo(va, d.casa.umidita, PICCOLO, "semibold", C.testo);
  sx.addSpacer();
  const [col, sfondo] = CHIP[d.casa.chip.tipo] || CHIP.fermo;
  const gChip = scegli([12, 11.5, 11, 10.5], g => [[d.casa.chip.testo, g, "semibold"]], COL_SX - 12);
  const chip = sx.addStack(); chip.backgroundColor = sfondo; chip.cornerRadius = 9; chip.setPadding(3, 6, 3, 6);
  testo(chip, d.casa.chip.testo, gChip, "semibold", col);

  // ===== destra: pompa di calore, fuori, aggiornamento
  titolo(dx, "Pompa di calore", C.grigio2);
  const st = dx.addStack(); st.layoutHorizontally(); st.centerAlignContent();
  const p = st.addStack(); p.size = new Size(8, 8); p.cornerRadius = 4; p.backgroundColor = PALLINO[d.pdc.stato] || C.spento;
  st.addSpacer(7);
  testo(st, d.pdc.testo, 17, "semibold", C.testo);                 // «Riscaldamento» (la piu' lunga) entra a 17: lo stesso corpo per tutti gli stati
  if (d.pdc.dettaglio) {
    const m = d.pdc.dettaglio.match(/^(.*?)(\d[\d,]*°)( → )?(\d[\d,]*°)?$/);   // etichetta grigia, valori bianchi in grassetto
    const r = dx.addStack(); r.layoutHorizontally();
    if (m) {
      coppia(r, m[1], m[2], PICCOLO);
      if (m[3]) coppia(r, m[3], m[4], PICCOLO);
    } else testo(r, d.pdc.dettaglio, PICCOLO, "regular", C.grigio);
  }
  dx.addSpacer();
  titolo(dx, "Fuori", C.verde);
  const um = d.fuori.umidita;
  const f = dx.addStack(); f.layoutHorizontally(); f.bottomAlignContent();
  testo(f, d.fuori.temp, CORPO_FUORI, "light", C.testo);
  if (um) {
    f.addSpacer(8);
    const u = f.addStack(); u.layoutHorizontally(); u.setPadding(0, 0, 3, 0);        // sulla linea del numero
    coppia(u, "Umidità ", um, PICCOLO);
  }
  dx.addSpacer();
  const nota = vecchio ? "Pannello non raggiungibile" : d.piede.testo;
  const gN = scegli([12, 11.5, 11], g => [[nota, g, "regular"]], COL_DX);
  const piede = dx.addStack(); piede.setPadding(3, 0, 3, 0);            // stesse spaziature verticali del riquadro a sinistra: i due testi stanno sulla stessa linea
  testo(piede, nota, gN, "regular", vecchio || d.piede.tipo === "warn" ? new Color("#ffc873") : C.grigio2);
  try { piede.url = "shortcuts://run-shortcut?name=" + encodeURIComponent(COMANDO_AGGIORNA); } catch (e) {}   // toccando l'ora dell'aggiornamento parte il comando rapido che ricarica i widget (il resto del widget apre il pannello)
  return w;
}

const w = costruisci(await leggi());
if (config.runsInWidget) Script.setWidget(w); else await w.presentMedium();
Script.complete();
