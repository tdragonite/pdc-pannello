// impronta di questa versione dello script: la prende dall'indirizzo con cui e' stato caricato (app.js?v=...)
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
      <div id="avvisi"></div>
      <section id="pagina"></section>
    </main>
  </div>`;
  $('#chips').addEventListener('click', e => {
    if (e.target.dataset.conf) { S.confronto = !S.confronto; chips(); carica(); return; }  // non si ricorda: a ogni apertura e' spento
    const r = e.target.dataset.r; if (!r) return;
    if (S.pagina === 'efficienza') { S.rangeEff = r; ls.set('rangeEff', r); chips(); carica(); return; }
    S.range = r; S.zoom = null; ls.set('range', r); chips();
    if (r === 'custom') apriCustom(); else { $('#custom').hidden = true; carica(); }
  });
  $('#c-ok').addEventListener('click', () => {
    const da = new Date($('#c-da').value).getTime() / 1000, a = new Date($('#c-a').value).getTime() / 1000;
    if (!isFinite(da) || !isFinite(a) || a <= da) { alert('Controlla le date: "A" deve venire dopo "Da".'); return; }
    S.custom = {da: Math.floor(da), a: Math.floor(a)}; S.zoom = null; ls.set('custom', S.custom); carica();
  });
}

function loc(t) { const d = new Date(t * 1000); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); }
function apriCustom() {
  const c = S.custom || {da: ora() - 86400, a: ora()};
  $('#c-da').value = loc(c.da); $('#c-a').value = loc(c.a); $('#custom').hidden = false;
  if (!S.custom) { S.custom = c; ls.set('custom', c); carica(); }
}
const RANGE_EFF = {'3g': 3, '7g': 7, '28g': 28};
function chips() {
  const el = $('#chips');
  el.hidden = S.pagina === 'impostazioni' || S.pagina === 'dispositivi' || S.pagina === 'changelog';
  const eff = S.pagina === 'efficienza';
  const lista = eff ? [['3g', '3 giorni'], ['7g', '7 giorni'], ['28g', '28 giorni']]
                    : [['6h', '6h'], ['24h', '24h'], ['custom', 'Personalizzato']];
  const sel = eff ? S.rangeEff : S.range;
  el.innerHTML = lista.map(([r, t]) => `<button data-r="${r}" class="${sel === r ? 'on' : ''}">${t}</button>`).join('')
    + (eff ? `<button data-conf="1" class="conf-btn ${S.confronto ? 'on' : ''}" aria-pressed="${!!S.confronto}" title="Confronta con un periodo della stessa durata subito prima">Confronta</button>` : '');
}

function mostraPagina() {
  $('#titolo').textContent = TITOLI[S.pagina];
  document.querySelectorAll('.nav a').forEach(a => a.classList.toggle('on', a.dataset.p === S.pagina));
  chips(); S.dati = null; S.dpag = null; S.zoom = null;
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
    <article class="card curva"><div class="ch"><h2>Curva climatica</h2><span class="et">Zona 1</span></div><div id="curva"></div></article>
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
  { const e = document.getElementById('w-eur-s'); if (e) { e.hidden = !(w.prezzo_kwh > 0 && w.kwh != null); set('w-eur', w.kwh != null ? euro(w.kwh * w.prezzo_kwh) : '—'); } }
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
const COLS = {casa: '#ff6b57', acs: '#4da3ff', est: '#34d3b0', en: '#ffb547', amb: '#a78bfa', ritorno: '#f59ac7', umid: '#7dd3fc'};
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

function scheletroAnalisi() {
  return `<div class="card"><div class="ch"><h2>Modo di funzionamento</h2><div class="riep" id="tl-riep"></div></div>
    <div class="tlw" id="tlw"><div id="tl"></div><div class="tip" id="tip" hidden></div></div><div class="assi" id="assi"></div></div>
    <div id="grafici"></div>
    <ul class="nota-cv lun"><li><i class="in"></i><span>Le zone colorate dietro le linee indicano quando la PdC scalda la casa (rosso) o l'acqua (blu); le righe oblique grigie, i periodi senza dati.</span></li>
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
  box.innerHTML = d.grafici.filter(g => g.linee.length).map(g => `
    <article class="card gr" data-g="${g.id}">
      <div class="ch"><h2>${g.titolo}</h2><div class="legg" data-l="${g.id}"></div></div>
      <div class="grw"${tel ? ' style="margin:0 -6px 0 -9px"' : ''}>${graficoLineare(g, d, Ws, H)}</div>
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
    <rect class="cattura" x="${L}" y="${T}" width="${pw}" height="${ph}" fill="transparent"/></svg>`;
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
    const r = await fetch('/api/registro?righe=150', {credentials: 'same-origin', cache: 'no-store'});
    if (r.status === 401) { location.href = '/login'; return; }
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
const MT = {
  nuvola: '<path class="mt-n" d="M10 24a5 5 0 0 1-.5-9.97A7 7 0 0 1 23 13.2 5.4 5.4 0 0 1 22.4 24z"/>',
  nuvolaP: '<path class="mt-n" transform="translate(6.5 7.5) scale(.72)" d="M10 24a5 5 0 0 1-.5-9.97A7 7 0 0 1 23 13.2 5.4 5.4 0 0 1 22.4 24z"/>',
  nuvolaAlta: '<path class="mt-n" transform="translate(0 -3)" d="M10 24a5 5 0 0 1-.5-9.97A7 7 0 0 1 23 13.2 5.4 5.4 0 0 1 22.4 24z"/>',
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
  'sole-pioggia': MT.sole(10, 9.5, 3.2, 2) + '<path class="mt-n" transform="translate(5 1) scale(.8)" d="M10 24a5 5 0 0 1-.5-9.97A7 7 0 0 1 23 13.2 5.4 5.4 0 0 1 22.4 24z"/><path class="mt-p" d="M14.4 24.4l-1.2 2.6M19 24.4l-1.2 2.6M23.6 24.4l-1.2 2.6"/>',
  'luna-pioggia': MT.luna('transform="translate(-2 -3) scale(.68)"') + '<path class="mt-n" transform="translate(5 1) scale(.8)" d="M10 24a5 5 0 0 1-.5-9.97A7 7 0 0 1 23 13.2 5.4 5.4 0 0 1 22.4 24z"/><path class="mt-p" d="M14.4 24.4l-1.2 2.6M19 24.4l-1.2 2.6M23.6 24.4l-1.2 2.6"/>',
  temporale: MT.nuvolaAlta + MT.fulmine,
  neve: MT.nuvolaAlta + '<path class="mt-v" d="M12 26.5v.01M16.5 28.5v.01M21 26.5v.01"/>',
  nebbia: '<path class="mt-n" d="M7 11h14M5 16h20M9 21h16M7 26h10"/>',
};
const iconaMeteo = (nome, px) => `<svg class="mt-i" viewBox="0 0 32 32" width="${px}" height="${px}" fill="none" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONE_METEO[nome] || ICONE_METEO.nuvola}</svg>`;

// Versione corta della frase per la scheda (nel pannello c'e' quella completa)
function meteoBreve(m) {
  if (m.livello !== 'certa' && m.livello !== 'possibile') return '';
  const r = (m.testo || '').replace(/^(possibile )?pioggia\s*/, '').replace('per il resto della giornata', 'tutto il giorno');
  if (m.livello === 'possibile') return 'possibile ' + r;
  return /^(fino|tutto)/.test(r) ? r : 'pioggia ' + r;
}
function meteoRiga(m) {
  const el = document.getElementById('e-mt');
  if (!el) return;
  if (!m) { el.hidden = true; chiudiMeteo(); return; }
  const gradi = v => v == null ? '—' : Math.round(v) + '°';
  el.hidden = false;
  const breve = meteoBreve(m).replace(/(\d)–(\d)/g, '$1⁠–⁠$2');
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

// ---------- Changelog ----------
// Una voce per ogni versione, la piu' recente in alto; le sotto-versioni stanno nella versione madre.
// Ogni riga: [tipo, titoletto, frase]  con tipo n = Novità, m = Migliorato, s = Sistemato. Una frase sola: i dettagli tecnici stanno in PIANO.md.
const CL_TIPI = {n: 'Novità', m: 'Migliorato', s: 'Sistemato'};
const CL_BREVE = [
  ['plugin', 'Integrazioni a plugin', 'Impostazioni → Integrazioni: i collegamenti opzionali sono file nella cartella «plugin», si accendono e si ricaricano senza riavviare.'],
  ['alexa', 'Alexa', 'Il primo plugin: chiedi temperatura, umidità, PdC e acqua calda, o accendi e spegni lo Smarther, con le Routine di Alexa.'],
  ['live', 'Stato in diretta', 'Stato e richieste dei plugin si aggiornano da soli, senza ricaricare la pagina.'],
];
const CL_IC = {euro: '€', refresh: '↻', ora: '◷', tipici: '≈', avviso: '!', sbr: '❄', login: '🔑', plugin: '🧩', alexa: '🔊', live: '↻'};
const CHANGELOG = [
  {v: '0.21.1', quando: '4 novembre 2026', titolo: 'Changelog dei plugin', righe: [
    ['n', 'Tab «Plugin» nel Changelog', 'Il Changelog ha due tab: Pannello (come prima) e Plugin, con la cronologia di ogni plugin, tutti insieme in ordine di data o uno alla volta con il filtro. La versione che hai installata è segnata «Installato».'],
    ['m', 'Pannello più veloce da fuori', 'Le pagine (app.js e app.css) si scaricano una volta sola e, se non cambiano, il browser riceve soltanto «invariato»: meno dati sul tunnel e caricamento più rapido, soprattutto da telefono.'],
    ['m', 'Indirizzi dei plugin più protetti', 'Anche i tentativi su indirizzi sbagliati hanno un limite al minuto, come quelli giusti.'],
    ['m', 'Plugin nuovi senza lavoro in più', 'Ogni plugin porta la sua cronologia nel proprio file (lista CHANGELOG): un plugin nuovo compare da solo nella tab.'],
  ]},
  {v: '0.21', quando: '4 novembre 2026', titolo: 'Umidità esterna', righe: [
    ['n', 'Umidità esterna', 'Nella card Esterna, sotto Min/Max, compare «Umidità 78%», letta da una stazione meteo vicina (WeatherLink). Sta nella riga degli avvisi: se appare «Dato delle… (PdC ferma)» prende il suo posto. Le dimensioni della Dashboard non cambiano. Si accende incollando l’indirizzo dei dati in Impostazioni → Collegamenti; senza indirizzo o senza dato non compare niente.'],
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
    ['n', 'Giorni tipici', 'Efficienza → Confronta: sui grafici dei giorni compare il consumo e il COP normali per quel freddo, e un puntino sui giorni sopra il normale.'],
    ['n', 'Avviso se il pannello non si apre da fuori', 'Telegram ti scrive se da internet il pannello non risponde per qualche minuto; quando torna tutto a posto lo chiude da solo.'],
    ['n', 'Programma dello Smarther', 'In Dispositivi: il programma orario letto dal termostato, a fasce o fisso.'],
    ['n', 'Zoom sui grafici', 'Analisi, da telefono: pizzica con due dita per ingrandire il tempo (tutti i grafici insieme), trascina con un dito per spostarti, doppio tocco per tornare alla vista intera. In più i grafici sono più larghi, con i numeri degli assi in una colonna stretta.'],
    ['n', 'Elenco degli sbrinamenti', 'In Efficienza → Cicli di funzionamento: il riquadro «Sbrinamenti» dice anche quanta energia hanno usato; toccalo per l’elenco a scomparsa con ora, durata, consumo approssimato e temperatura esterna.'],
    ['n', 'Cursore nei grafici di Efficienza', 'Passando il mouse o toccando un grafico di Efficienza compare, come in Analisi, la linea con i puntini e l’etichetta in alto; i valori di quel giorno (o di quella fascia di temperatura) si leggono nella legenda.'],
    ['n', 'Avvisi Telegram di sbrinamento', 'Un messaggio senza suoneria quando la PdC entra in sbrinamento e uno quando finisce (durata e consumo). Si accendono e si spengono a parte in Impostazioni → Avvisi, e i testi si possono cambiare.'],
    ['m', 'Avvisi di Efficienza in alto', 'Dati incompleti e l’avviso di Confronta ora stanno subito sotto «Aggiornato», così li vedi appena premi il tasto.'],
    ['m', 'Note e legende più ordinate', 'In Analisi le note sono un elenco con icona e la legenda di «Modo di funzionamento» sta su una riga; l’icona «i» è la stessa dappertutto.'],
    ['m', 'Riquadri di Impostazioni', 'Aperti, hanno meno spazio vuoto e tutti uguali, sopra e sotto.'],
    ['m', 'Avvisi nelle card', '«Smarther non collegato» in rosso, «Ultimo dato delle…» in giallo, nello stesso box della ripartenza (anche per la PdC ferma nella card Esterna).'],
    ['m', 'Meteo sempre di oggi', 'Il dettaglio mostra sempre 3 righe da 3 ore a partire da adesso, con la scritta «Oggi»: mai «Domani», nemmeno a sera tarda.'],
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
    ['s', 'Stato più pronto', '«A riposo» e «Riscaldamento» si aggiornano in 1–2 minuti. Tolto l’avviso «Resistenza elettrica»: l’impianto non ce l’ha.'],
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
    ['m', 'Compressore da fermo', 'Dashboard: stato «Fermo» e tre riquadri sull’ultimo ciclo (per cosa, durata, energia).'],
    ['m', 'Nota sul ritardo', 'Su una riga ordinata, senza «min» rimasto a capo.'],
    ['s', 'Gas di scarico', 'Compare solo se la pompa lo manda davvero.'],
  ]},
  {v: '0.14', quando: '3 novembre 2026', titolo: 'Umidità in casa: soglie e avvisi', righe: [
    ['n', 'Avvisi sull’umidità', 'Telegram avvisa sopra il 60 % e il 70 %; soglie e attese si cambiano in Impostazioni → Avvisi.'],
    ['n', 'Riepilogo con la notte', 'Il riepilogo del mattino racconta anche la notte, ma solo se è successo qualcosa.'],
    ['m', 'Colori e soglie', 'L’umidità diventa gialla e rossa in Dashboard, e due linee tratteggiate la accompagnano nel grafico.'],
    ['m', 'Grafici', 'La potenza è una linea continua a gradini, e al passaggio del mouse ogni linea ha il suo puntino.'],
  ]},
  {v: '0.13', quando: '3 novembre 2026', titolo: 'Impostazioni in tab', righe: [
    ['n', 'Sei tab', 'Stato, Collegamenti, Avvisi, Accesso, Dati e Registro; i riquadri si ricordano come li hai lasciati.'],
    ['n', 'Nuovi riquadri', 'In Dati: litri del bollitore e backup. In Cloudflare: «Invia il link su Telegram».'],
    ['m', 'Errori chiari', 'Un valore non valido apre il riquadro giusto e si segna in rosso.'],
    ['m', 'Telefono', 'Dispositivi più ordinato; grafici Smarther rinominati «Temperatura ambiente» e «Umidità»; «acceso da 2 minuti».'],
  ]},
  {v: '0.12', quando: '3 novembre 2026', titolo: 'Confronto, bollitore, registro e ripristino', righe: [
    ['n', 'Confronta', 'Efficienza: il periodo accanto al precedente di pari durata (COP, energia, calore, cicli).'],
    ['n', 'Salute del bollitore', 'Quanto calore perde di notte e quanto ci mette a scaldarsi.'],
    ['n', 'Registro e ripristino', 'Impostazioni → Registro, e il comando «ripristina» per rimettere un backup.'],
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
    ['s', 'Stato corretto', '«A riposo» non compare più a pompa in marcia per il ritardo dei dati.'],
  ]},
  {v: '0.8', titolo: 'Colori e dettagli', antico: true, righe: [
    ['m', 'Badge di stato', 'A colori pieni, sempre sotto il titolo delle schede.'],
    ['s', 'Acqua calda', 'Non va più in «Standby» durante un ciclo.'],
  ]},
  {v: '≤ 0.7', titolo: 'Come è nato', antico: true, righe: [
    ['n', 'Il nucleo', 'Lettura della pompa (SmartThings) e dello Smarther (Netatmo), database, decodifica del ciclo.'],
    ['n', 'Il sito', 'Server web con login, Dashboard con curva climatica, tempo reale, modalità demo.'],
    ['n', 'Analisi ed Efficienza', 'Grafici sincronizzati, COP stimato, energia al giorno, cicli.'],
    ['n', 'Telegram', 'Avvisi sulle anomalie e riepilogo del mattino.'],
    ['n', 'Impostazioni e accesso', 'Config dal pannello, password, tunnel Cloudflare, README, compattazione dello storico.'],
  ]},
];

function disegnaChangelogPannello() {
  const riga = r => `<li><span class="pill tg ${ {n: 'c-ok', m: 'c-acs', s: 'c-en'}[r[0]] }">${CL_TIPI[r[0]]}</span><span class="cl-tx"><b>${esc(r[1])}</b> ${esc(r[2])}</span></li>`;
  const voce = (c, attuale) => `<section class="cl-v${attuale ? ' att' : ''}"><div class="cl-h"><span class="cl-n">${esc(c.v)}</span>` +
    `${attuale ? '<span class="pill en">Attuale</span>' : ''}<span class="cl-ti">${esc(c.titolo)}</span>${c.quando ? `<span class="cl-d">${esc(c.quando)}</span>` : ''}</div>` +
    `<ul>${c.righe.map(riga).join('')}</ul></section>`;
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
  const riga = r => `<li><span class="pill tg ${ {n: 'c-ok', m: 'c-acs', s: 'c-en'}[r[0]] }">${CL_TIPI[r[0]]}</span><span class="cl-tx"><b>${esc(r[1])}</b> ${esc(r[2])}</span></li>`;
  const corpo = voci.map(({p, c}) => {
    const att = c.v === p.versione;
    return `<section class="cl-v${att ? ' att' : ''}"><div class="cl-h">${filtro ? '' : `<span class="pill up">${esc(p.titolo)}</span>`}<span class="cl-n">${esc(c.v)}</span>` +
      `${att ? '<span class="pill en">Installato</span>' : ''}<span class="cl-ti">${esc(c.titolo)}</span>${c.data ? `<span class="cl-d">${esc(dataIt(c.data))}</span>` : ''}</div>` +
      `<ul>${c.righe.map(riga).join('')}</ul></section>`;
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
    const r = await fetch('/api/plugin/changelog', {credentials: 'same-origin', cache: 'no-store'});
    if (r.status === 401) { location.href = '/login'; return; }
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
  const p = b && b.perdita, sa = b && b.salita, pp_ = pb && pb.perdita, ps = pb && pb.salita;
  const tiles = (p ? t('Perdita a riposo, di notte', num(p.gradi_ora, 2) + ' °C/h', `circa ${num(p.watt, 0)} W · ${p.notti} notti`,
      conf(pp_ ? pp_.gradi_ora : null, v => num(v, 2) + ' °C/h')) : t('Perdita a riposo, di notte', '—', 'servono almeno 2 notti senza acqua calda'))
    + (sa ? t('Tempo per salire di 10 °C', durata(sa.min_per_10), `${num(sa.gradi_ora, 1)} °C/h · ${sa.cicli} cicli`,
      conf(ps ? ps.min_per_10 : null, v => durata(v))) : t('Tempo per salire di 10 °C', '—', 'servono almeno 2 cicli completi'))
    + (sa ? t('Ciclo tipico', '+' + num(sa.gradi, 1) + ' °C', `in ${durata(sa.minuti)}`, conf(ps ? ps.gradi : null, v => '+' + num(v, 1) + ' °C')) : '');
  return `<article class="card"><h2>Salute del bollitore</h2><div class="tiles tc c-acs">${tiles}</div>
    <ul class="nota-cv"><li><i class="in"></i><span>Stime dalla sonda dell'acqua calda (${b ? b.litri : 300} litri). Sono indicazioni, non diagnosi. <button type="button" class="come-b" data-come="boll" aria-expanded="false">Come leggerli</button></span></li></ul>
    <p class="come-t" id="come-boll" hidden>La <b>perdita</b> dice quanto si raffredda l'acqua del bollitore di notte, quando non la usa nessuno e la pompa non la scalda (si guarda il tratto più lungo tra le 23 e le 6). Se col tempo aumenta, può esserci un problema di isolamento o di ricircolo. Il <b>tempo per salire di 10 °C</b> dice quanto ci mette la pompa a scaldare l'acqua di 10 gradi: se si allunga, la pompa rende meno oppure c'è più acqua da scaldare.</p></article>`;
}
// Costo del mese in corso (non dipende dal periodo scelto): stima a fine mese e confronto col mese scorso
function meseCosto(m) {
  if (!m) return '';
  const MES = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre'];
  return `<article class="card acc" style="--acc:var(--en)"><div class="ch"><span class="et">Costo di ${MES[m.mese - 1]}</span></div>
      <div class="val"><div class="n"><span>${euro(m.euro)}</span></div></div>
      <div class="sub">${m.stima_euro != null ? `<span>Stima a fine mese <b>${euro(m.stima_euro)}</b></span>` : ''}${m.scorso_euro != null ? `<span>${MES[m.mese_scorso - 1]} <b>${euro(m.scorso_euro)}</b></span>` : ''}</div>
      <div class="nota">Stima: media dei giorni passati, indicativa.</div></article>`;
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
      <div class="sub"><span>Risc. <b>${fm(t.el_casa)}</b></span><span>Acqua <b>${fm(t.el_acs)}</b></span><span>Standby <b>${fm(t.el_riposo)}</b></span>${d.prezzo_kwh > 0 ? `<span>Costo stimato <b>${euro((t.el_casa + t.el_acs + t.el_riposo) * d.prezzo_kwh)}</b></span>` : ''}</div>${rigaConf(d, t.el_casa + t.el_acs + t.el_riposo, pt.el_casa + pt.el_acs + pt.el_riposo, v => fm(v) + ' kWh', 'giu', 1)}</article>
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
    <a href="/api/esporta.csv?tipo=giorni&giorni=${d.n_giorni}" download>${ICONA_SCARICA}<span><b>Riepilogo per giorno</b><small>${d.n_giorni} giorni, una riga al giorno</small></span></a>
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
  L = colonnaNumeri(sc.tick.filter(v => v >= 0).map(v => num(v, 0)), tel); pw = W - L - R;
  const Y = v => T + ph - v / sc.hi * ph, bw = Math.min(46, pw / n * 0.7), cx = i => L + pw / n * (i + 0.5);
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" data-ef="en">`;
  EF.en = {xs: giorni.map((_, i) => cx(i)), lab: giorni.map(giornoBreve), L, pw, T,
    // etichetta = valore del segmento (rosso, blu, standby) sotto il dito; sopra la barra niente (il totale e' gia' scritto sulla barra)
    seg: (i, yy) => { const g = giorni[i]; let y0 = Y(0);
      for (const [k, nome] of [['el_riposo', 'Standby'], ['el_acs', 'Acqua'], ['el_casa', 'Risc.']]) { const h = g[k] / sc.hi * ph; if (yy >= y0 - h || k === 'el_riposo' && yy >= y0) return `${nome} ${num(g[k], 1)} kWh`; y0 -= h; }
      return null; }};
  s += sc.tick.filter(v => v >= 0).map(v => `<line x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" class="gl"/><text x="${L - 4}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${num(v, 0)}</text>`).join('');
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
  L = colonnaNumeri(sc.tick.filter(v => v >= 0).map(v => num(v, 0)), tel); pw = W - L - R;
  const Y = v => T + ph - v / sc.hi * ph, X = i => n === 1 ? L + pw / 2 : L + pw * i / (n - 1);
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" data-ef="cop">`;
  EF.cop = {xs: giorni.map((_, i) => X(i)), lab: giorni.map(giornoBreve), L, pw, T, dots: i => ['cop_casa', 'cop_acs'].map(k => giorni[i][k] == null ? null : Y(giorni[i][k]))};
  s += sc.tick.filter(v => v >= 0).map(v => `<line x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" class="gl"/><text x="${L - 4}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${num(v, 0)}</text>`).join('');
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
  L = colonnaNumeri(sc.tick.filter(v => v >= 0).map(v => num(v, 0)), tel); pw = W - L - R;
  const X = t => L + (t - x0) / Math.max(1, x1 - x0) * pw, Y = v => T + ph - v / sc.hi * ph;
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" data-ef="bin">`;
  const das = [...new Set(tutti.map(b => b.da))].sort((a, b) => a - b);
  EF.bin = {bins: das, xs: das.map(t => X(t + 1)), lab: das.map(t => `${t}…${t + 2} °C`), L, pw, T,
    dots: i => ['casa', 'acs'].map(m => { const b = bins[m].find(x => x.da === das[i]); return b ? Y(b.cop) : null; })};
  s += sc.tick.filter(v => v >= 0).map(v => `<line x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" class="gl"/><text x="${L - 4}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${num(v, 0)}</text>`).join('');
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
const TAB_DISP = [['', 'Dispositivi'], ['installatore', 'Installatore']];
const tabsDisp = () => `<nav class="sub-tabs" aria-label="Sezioni">${TAB_DISP.map(([k, t]) => `<a href="#/dispositivi${k ? '/' + k : ''}" class="${S.sub === k || (k === '' && S.sub !== 'installatore') ? 'on' : ''}">${t}</a>`).join('')}</nav>`;
S.fsvAperti = S.fsvAperti || new Set(['20']);
document.addEventListener('toggle', e => {
  const g = e.target && e.target.dataset && e.target.dataset.fsv; if (!g) return;
  if (e.target.open) S.fsvAperti.add(g); else S.fsvAperti.delete(g);
}, true);
function rigaFsv(r) {
  const fuori = r.fuori ? ' <span class="pill bad" title="Fuori dai limiti indicati dal manuale">fuori limiti</span>' : '';
  const note = [];
  if (r.limiti) note.push(esc(r.limiti));
  const cambio = r.cambio ? `<small class="mut nota-r">cambiato il ${esc(dataLunga(r.cambio.ts))} (prima: ${esc(r.cambio.prima)})</small>` : '';
  return `<div class="riga"><span><i class="fsv-id">${esc(r.id)}</i>${esc(r.nome)}</span><b><span class="val-c">${esc(r.v)}${fuori}</span>${note.length ? `<small class="mut nota-r">${note.join(' · ')}</small>` : ''}${cambio}</b></div>`;
}
function disegnaInstallatore(d) {
  const i = d.installatore;
  if (!i || !i.totale) return `<article class="card dev"><div class="vuoto">La pompa non ha ancora comunicato impostazioni installatore. Appena SmartThings le manda, compaiono qui (il pannello le legge ogni ora circa).</div></article>`;
  const testo = i.gruppi.map(g => `${g.titolo}\n` + g.righe.map(r => `${r.id}  ${r.nome}: ${r.v}`).join('\n')).join('\n\n');
  const sommario = [`<b>${i.totale}</b> impostazioni`, i.cambiati ? `<b>${i.cambiati}</b> cambiate da quando il pannello osserva` : '', i.fuori ? `<b class="rosso">${i.fuori}</b> fuori limiti` : '', i.letto ? `lette ${esc(fa(i.letto))}` : ''].filter(Boolean).join(' · ');
  return `<article class="card dev">
    <div class="ch"><h2>Impostazioni installatore</h2><button type="button" class="copia" data-copia="${esc(testo)}" title="Copia tutto l'elenco">Copia elenco</button></div>
    <p class="nota-sez">${sommario}</p>
    <p class="nota-sez">Sola lettura: da qui non si cambia nulla. Sono i valori che la pompa comunica tramite SmartThings (i codici FSV del manuale). L'intervallo sotto ogni valore è quello del manuale Samsung EHS Mono e può variare da modello a modello.</p>
  </article>` + i.gruppi.map(g => `<details class="card dev fsv-gr" data-fsv="${esc(g.id)}" ${S.fsvAperti.has(g.id) ? 'open' : ''}><summary><span class="t">${esc(g.titolo)}</span><span class="d">${g.righe.length} impostazioni</span></summary>${g.righe.map(rigaFsv).join('')}</details>`).join('');
}
function disegnaDispositivi(d) {
  const box = $('#dsp'); if (!box) return;
  if (S.sub === 'installatore') {
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
  const righe = lista.slice(S.pagCmd * PER_PAG, (S.pagCmd + 1) * PER_PAG).map(c => `<div class="riga"><span>${esc(dataLunga(c.ts))}</span><b>${esc(c.cosa)} · ${c.acceso ? 'acceso' : 'spento'}<small class="mut nota-r">da ${esc(c.ip)} · ${esc(c.esito)}</small></b></div>`).join('');
  const nav = pagine > 1 ? `<div class="pager"><button type="button" data-pagcmd="-1" ${S.pagCmd === 0 ? 'disabled' : ''} aria-label="Più recenti">‹</button><span>${S.pagCmd + 1} di ${pagine}</span><button type="button" data-pagcmd="1" ${S.pagCmd >= pagine - 1 ? 'disabled' : ''} aria-label="Meno recenti">›</button></div>` : '';
  return `<article class="card dev"><div class="ch"><h2>Comandi dati dal pannello</h2></div>${righe}${nav}</article>`;
}
document.addEventListener('click', e => {
  const b = e.target.closest && e.target.closest('[data-pagcmd]'); if (!b || b.disabled) return;
  S.pagCmd = (S.pagCmd || 0) + Number(b.dataset.pagcmd);
  disegna();
});
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
    const r = await fetch('/api/impostazioni', {credentials: 'same-origin', cache: 'no-store'});
    if (!r.ok || S.sub !== 'integrazioni') return;
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
async function inviaPost(url, corpo) {
  const r = await fetch(url, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-Pdc': '1'}, body: JSON.stringify(corpo)});
  if (r.status === 401) { location.href = '/login'; return {}; }
  return r.json();
}
// ---------- Aggiornamento a caldo ----------
async function aggCarica() {
  const box = $('#agg-corpo'); if (!box || box.dataset.occupato) return;
  try {
    const r = await fetch('/api/aggiornamento', {credentials: 'same-origin', cache: 'no-store'});
    if (r.status === 401) { location.href = '/login'; return; }
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
    e.target.disabled = true; try { const r = await inviaPost('/api/impostazioni/telegram-prova', {}); toast(r.messaggio || 'Fatto', !r.ok); } catch { toast('Server non raggiungibile', true); }
    e.target.disabled = false;
  } else if (id === 'plg-ricarica') {
    e.target.disabled = true;
    try { const r = await inviaPost('/api/plugin/ricarica', {}); toast(r.ok ? `Plugin riletti: ${r.plugin}` : 'Non riuscito', !r.ok); S.sporco = false; S.ricarica = true; await carica(); }
    catch { toast('Server non raggiungibile', true); }
    e.target.disabled = false;
  } else if (id === 'b-pw') {
    const r = await inviaPost('/api/impostazioni/password', {utente: $('#p-ut').value, attuale: $('#p-at').value, nuova: $('#p-nu').value});
    $('#pw-msg').textContent = r.messaggio || ''; $('#pw-msg').className = 'msg ' + (r.ok ? 'ok' : 'bad');
    if (r.ok) { $('#p-at').value = ''; $('#p-nu').value = ''; }
  }
}

// ---------- Accesso da internet (Cloudflare) ----------
const CFS = {zone: null, token: '', passi: null, occupato: false};
async function cfLeggi() { const r = await fetch('/api/cloudflare', {credentials: 'same-origin', cache: 'no-store'}); if (r.status === 401) { location.href = '/login'; return null; } return r.json(); }
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
  if (S.pagina === 'dispositivi') return {da: 0, a: 0, live: true};
  if (S.pagina === 'efficienza') return {da: 0, a: 0, live: true};
  if (S.range === 'custom' && S.custom) return {da: S.custom.da, a: S.custom.a, live: false};
  return {da: ora() - (S.range === '6h' ? 6 : 24) * 3600, a: ora(), live: true};
}
function urlDati() {
  const w = finestra();
  if (S.pagina === 'impostazioni') return '/api/impostazioni';
  if (S.pagina === 'dispositivi') return '/api/dispositivi';
  if (S.pagina === 'efficienza') return `/api/efficienza?giorni=${RANGE_EFF[S.rangeEff] || 7}${S.confronto ? '&confronta=1' : ''}`;
  if (S.pagina === 'analisi') return `/api/analisi?da=${w.da}&a=${w.a}&n=${Math.max(120, Math.min(400, Math.round(($('#pagina').clientWidth || 800) / 3)))}`;
  return `/api/dashboard?da=${w.da}&a=${w.a}`;
}
let caricando = false;
async function carica() {
  if (S.pagina === 'changelog') return;  // pagina fissa: niente da caricare
  if (caricando) { S.ripeti = true; return; }
  caricando = true;
  const pag = S.pagina;
  try {
    const r = await fetch(urlDati(), {credentials: 'same-origin', cache: 'no-store'});
    if (r.status === 401) { location.href = '/login'; return; }
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
    if (zoomaMuovi(e) || dita.size > 1) return;
    if (e.target.closest && e.target.closest('#tlw')) tooltipTimeline(e);
    else { const t = $('#tip'); if (t) t.hidden = true; if (!muoviCursore(e)) togliCursore(); }
  });
  document.addEventListener('pointerleave', togliCursore);
  document.addEventListener('input', segnaModifica); document.addEventListener('change', segnaModifica);
  document.addEventListener('click', e => { if (e.target.closest && e.target.closest('#imp') && e.target.tagName === 'BUTTON') { if (e.target.id.startsWith('cf-')) cfAzione(e); else azioniImpostazioni(e); } });
  let rid = null;
  window.addEventListener('resize', () => { clearTimeout(rid); rid = setTimeout(() => { if (S.dati && S.pagina !== 'dashboard') { const b = $('#grafici') || $('#eff'); if (b) b.dataset.f = ''; disegna(); } }, 250); });
  document.addEventListener('pointerdown', e => { zoomaGiu(e); if (dita.size > 1) return; if (e.target.closest && e.target.closest('#tlw')) tooltipTimeline(e); else muoviCursore(e); });
  document.addEventListener('pointerup', zoomaSu); document.addEventListener('pointercancel', zoomaSu);
  if (S.range === 'custom' && S.pagina !== 'efficienza') { $('#custom').hidden = false; if (S.custom) { $('#c-da').value = loc(S.custom.da); $('#c-a').value = loc(S.custom.a); } }
  carica(); collega();
  setInterval(() => { if (finestra().live) carica(); }, 60000);
  setInterval(stato, 15000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { controllaFlusso(); if (finestra().live) carica(); } });
  window.addEventListener('pageshow', controllaFlusso); window.addEventListener('online', controllaFlusso);
  setInterval(controllaFlusso, 10000);
}
avvia();
})();
