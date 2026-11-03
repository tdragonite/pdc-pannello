// impronta di questa versione dello script: la prende dall'indirizzo con cui e' stato caricato (app.js?v=...)
window.BUILD = (() => { try { return new URL(document.currentScript.src).searchParams.get('v') || ''; } catch (e) { return ''; } })();
(() => {
'use strict';

// ---------- utilità ----------
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const ls = {
  get(k, d) { try { const v = localStorage.getItem('pdc.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('pdc.' + k, JSON.stringify(v)); } catch (e) {} },
};
const num = (v, d = 1) => (v == null || isNaN(v)) ? '—' : Number(v).toLocaleString('it-IT', {minimumFractionDigits: d, maximumFractionDigits: d});
const gr = v => v == null ? '—' : num(v) + '°';
const hhmm = t => new Date(t * 1000).toLocaleTimeString('it-IT', {hour: '2-digit', minute: '2-digit'});
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
    S.range = r; ls.set('range', r); chips();
    if (r === 'custom') apriCustom(); else { $('#custom').hidden = true; carica(); }
  });
  $('#c-ok').addEventListener('click', () => {
    const da = new Date($('#c-da').value).getTime() / 1000, a = new Date($('#c-a').value).getTime() / 1000;
    if (!isFinite(da) || !isFinite(a) || a <= da) { alert('Controlla le date: "A" deve venire dopo "Da".'); return; }
    S.custom = {da: Math.floor(da), a: Math.floor(a)}; ls.set('custom', S.custom); carica();
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
  chips(); S.dati = null; S.dpag = null;
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
  else if (S.pagina === 'changelog') { $('#pagina').innerHTML = disegnaChangelog(); window.scrollTo(0, 0); }
  else { $('#pagina').innerHTML = '<div id="eff"><div class="card vuoto">Calcolo in corso…</div></div>'; }
}

function scheletro() {
  const sc = (id, cls, acc, et, extra) => `
  <article class="card acc" style="--acc:var(--${acc})">
    <div class="ch ch-st"><span class="et" id="${id}-et">${et}</span><span class="badge" id="${id}-b"></span></div>
    <div class="val"><div class="n"><span id="${id}-t">—</span><i>${cls}</i></div><div class="spark" id="${id}-sp"></div></div>
    <div class="sub">${extra}</div><div class="nota" id="${id}-n"></div>
  </article>`;
  return `
  <div class="cards">
    ${sc('r', '°C', 'casa', 'Riscaldamento', '<span>Target <b id="r-tg">—</b></span><span>Ritorno <b id="r-rt">—</b></span><span>ΔT <b id="r-dt">—</b></span>')}
    ${sc('a', '°C', 'acs', 'Acqua calda', '<span>Target <b id="a-tg">—</b></span><span>Modo <b id="a-md">—</b></span>')}
    ${sc('e', '°C', 'est', 'Esterna', '<span>Min <b id="e-mn">—</b></span><span>Max <b id="e-mx">—</b></span>')}
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

  set('e-t', num(e.temp)); set('e-mn', gr(e.min)); set('e-mx', gr(e.max)); html('e-sp', spark(e.spark));
  badge('e-b', 'Temperatura', 'tema', 'Temperatura dell\'aria esterna');
  set('e-n', e.ts && d.ora - e.ts > 7200 ? 'Ultimo dato delle ' + hhmm(e.ts) + ' (pdc ferma)' : '');

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
  set('c-n', c.temp == null ? 'Smarther non collegato' : (c.ts && d.ora - c.ts > 900 ? 'Ultimo dato delle ' + hhmm(c.ts) : ''));

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
  tip.innerHTML = `<b style="color:${s[2] === 'nodati' ? 'var(--mut)' : COL[s[2]]}">${NOMI[s[2]]}</b> · ${hhmm(s[0])}–${hhmm(s[1])} (${durata(Math.round((s[1] - s[0]) / 60))})`;
  tip.hidden = false; tip.style.left = Math.max(70, Math.min(r.width - 70, f * r.width)) + 'px';
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
  } catch (err) { delete S.opt[cosa]; toast('Comando non riuscito: il pannello non risponde', true); }
  delete S.occ[cosa];
  armaSblocco(); ridisegnaComandi();
});

function sistema(d) {
  const s = d.sistema, sbl = Date.now() < S.sblocco;
  html('cmd-lock', `<button type="button" class="sec piccolo ${sbl ? 'sbloccato' : ''}" id="b-lock">${sbl ? 'Comandi sbloccati · Blocca' : 'Sblocca comandi'}</button>`);
  html('sistema', [
    ...COMANDI_UI.map(([k, n]) => rigaCmd(k, n, s)),
    riga('Modo acqua calda', esc(d.acs.modo || '—')),
    riga('Valvola deviatrice', s.valvola === 'room' ? 'Verso la casa' : s.valvola ? 'Verso il bollitore' : '—'),
    riga('Sbrinamento', s.sbrinamento ? '<span class="pill warn">IN CORSO</span>' : 'No'),
    riga('Collegamento SmartThings', online(s.salute === 'online' ? true : s.salute ? false : null)),
    riga('Collegamento Smarther', online(s.smarther === 'si' ? true : s.smarther ? false : null)),
  ].join(''));
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
    html('prest', `<div class="fermo-w"><div class="fermo-t"><span class="fermo-p">Fermo</span><span class="fermo-q">${u ? `Ultimo ciclo · ${giorno}${hhmm(u.inizio)}–${hhmm(u.fine)}` : 'Nessun ciclo negli ultimi 3 giorni'}</span></div>` +
      (u ? `<div class="fermo-g">${tile('Per', u.modo === 'acs' ? 'Acqua calda' : 'Riscaldamento')}${tile('Durata', dur)}${u.kwh != null ? tile('Energia', num(u.kwh, 1) + ' <i>kWh</i>') : ''}</div>` : '') + '</div>');
    if (rt) rt.textContent = '';
    return;
  }
  if (rt) rt.innerHTML = r ? `<span>Ritardo dei dati · ultime 24 ore</span><span class="nw">tipico <b>${num(r.tipico_min, 1)} min</b></span><span class="nw">massimo <b>${num(r.max_min, 1)} min</b></span>` : '';
  const t = (et, v, u, dec = 0) => `<div class="tile ${vecchio ? 'vecchio' : ''}"><small>${et}</small><b>${v == null ? '—' : num(v, dec)}</b><i>${u}</i></div>`;
  html('prest', t('Compressore', p.freq_compressore, 'Hz') + t('Pompa', p.vel_pompa, '%') + t('Portata', p.portata, 'L/min', 1) +
    (p.scarico == null ? '' : t('Gas di scarico', p.scarico, '°C')) + `<div class="tile"><small>Potenza</small><b>${d.energia.potenza_w == null ? '—' : num(d.energia.potenza_w, 0)}</b><i>W</i></div>`);
}

function nice(a, b, step) { const o = []; for (let v = Math.ceil(a / step) * step; v <= b + 1e-9; v += step) o.push(v); return o; }
function curva(c) {
  if (!c) { html('curva', '<div class="vuoto">Le impostazioni della curva non sono ancora state lette dalla pdc.</div>'); return; }
  const W = 480, H = 300, L = 46, R = 16, T = 16, B = 44;
  const xs = [-10, 25, c.basso.esterna - 3, c.alto.esterna + 3, ...c.punti.map(p => p[0])];
  const ys = [20, 60, c.alto.mandata - 4, c.basso.mandata + 6, ...c.punti.map(p => p[1])];
  const x0 = Math.floor(Math.min(...xs) / 5) * 5, x1 = Math.ceil(Math.max(...xs) / 5) * 5;
  const y0 = Math.floor(Math.min(...ys) / 10) * 10, y1 = Math.ceil(Math.max(...ys) / 10) * 10;
  const X = v => L + (v - x0) / (x1 - x0) * (W - L - R), Y = v => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
  let g = '';
  nice(x0, x1, 5).forEach(v => { g += `<line x1="${X(v)}" x2="${X(v)}" y1="${T}" y2="${H - B}" stroke="rgba(255,255,255,.06)"/><text class="ass" x="${X(v)}" y="${H - B + 16}" text-anchor="middle">${v}°</text>`; });
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
    <text transform="translate(12 ${(T + H - B) / 2}) rotate(-90)" text-anchor="middle">Mandata (°C)</text></svg>
    <div class="box2">
      <div class="f"><small>Freddo</small>a ${num(A.esterna, 0)}°C esterni · mandata <b>${num(A.mandata, 0)}°C</b></div>
      <div class="m"><small>Mite</small>a ${num(Z.esterna, 0)}°C esterni · mandata <b>${num(Z.mandata, 0)}°C</b></div>
      ${c.ora.esterna != null ? `<div class="o"><small>Adesso</small>esterna ${num(c.ora.esterna)}°C → mandata prevista <b>${num(c.ora.mandata)}°C</b></div>` : ''}
    </div>
    <div class="nota" style="margin-top:10px;color:var(--mut2);font-size:12px">La linea è calcolata dalle impostazioni della pdc (FSV 2011, 2012, 2021, 2022); un eventuale offset impostato sul pannello non si può leggere. I puntini sono le mandate misurate durante il riscaldamento nel periodo scelto.</div>`);
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
    <p class="nota-fondo">Le zone colorate dietro le linee indicano quando la pdc scalda la casa (rosso) o l'acqua (blu). Tocca o passa il mouse su un grafico per leggere i valori: i grafici sono sincronizzati.</p>`;
}

function disegnaAnalisi(d) {
  timeline(d.modi, d.da, d.a);
  const box = $('#grafici');
  const firma = d.da + ':' + d.a + ':' + d.ora + ':' + box.clientWidth;
  if (box.dataset.f === firma) return;
  box.dataset.f = firma;
  const W = Math.max(280, box.clientWidth || 600), H = 168;
  box.innerHTML = d.grafici.filter(g => g.linee.length).map(g => `
    <article class="card gr" data-g="${g.id}">
      <div class="ch"><h2>${g.titolo}</h2><div class="legg" data-l="${g.id}"></div></div>
      <div class="grw">${graficoLineare(g, d, W - 32, H)}</div>
    </article>`).join('') || '<div class="card vuoto">Nessun dato in questo periodo.</div>';
  S.grafici = d.grafici; S.gW = W - 32;
  d.grafici.forEach(g => legenda(g, null));
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

function graficoLineare(g, d, W, H) {
  const doppio = g.linee.some(l => l.asse === 1), L = 40, R = doppio ? 40 : 8, T = 8, B = 22, pw = W - L - R, ph = H - T - B;
  const range = a => {
    let lo = Infinity, hi = -Infinity;
    g.linee.filter(l => (l.asse || 0) === a).forEach(l => l.punti.forEach(p => { if (p[1] < lo) lo = p[1]; if (p[1] > hi) hi = p[1]; }));
    return [lo, hi];
  };
  let [lo, hi] = range(0);
  if (g.id === 'circuito' || g.id === 'pot') lo = Math.min(lo, 0);
  const soglie = g.id === 'umid' ? (d.soglie_umidita || []).filter(v => hi > v - 10 && v > 0) : [];  // le linee delle soglie si vedono solo se i dati ci si avvicinano
  soglie.forEach(v => { if (v > hi) hi = v; });
  const sc = scalaNice(lo, hi, 4), X = t => L + (t - d.da) / (d.a - d.da) * pw, Y = v => T + ph - (v - sc.lo) / (sc.hi - sc.lo) * ph;
  let sc1 = null, Y1 = null;
  if (doppio) { const r = range(1); sc1 = scalaNice(Math.min(0, r[0]), r[1], 4); Y1 = v => T + ph - (v - sc1.lo) / (sc1.hi - sc1.lo) * ph; }
  const tt = tickTempo(d.da, d.a, W < 480 ? 4 : 7), buco = g.id === 'pot' ? 3600 : Math.max(d.passo * 4, 1000);
  const mezzo = Math.max(d.passo, 120) / 2, colAsse1 = doppio ? COLS[(g.linee.find(l => l.asse === 1) || {}).col] : '';
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" data-l="${L}" data-pw="${pw}" data-id="${g.id}" data-t0="${T}" data-ph="${ph}" data-lo="${sc.lo}" data-hi="${sc.hi}"${doppio ? ` data-lo1="${sc1.lo}" data-hi1="${sc1.hi}"` : ''} data-buco="${buco}" data-mezzo="${g.gradini ? mezzo : 0}">`;
  s += g.id === 'casa' ? fasceRichiesta(d.richiesta || [], d.da, d.a, X, T, ph) : fasce(d.modi.segmenti, d.da, d.a, X, T, ph);
  if (g.id === 'casa') s += fasceRichiesta(d.richiesta || [], d.da, d.a, X, T + ph - 7, 7, .9);   // striscia piena in basso: quando lo Smarther chiede caldo
  s += sc.tick.map(v => `<line x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" class="gl"/><text x="${L - 6}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${num(v, sc.passo < 1 ? 1 : 0)}</text>`).join('');
  if (doppio) s += sc1.tick.map(v => `<text x="${W - R + 6}" y="${(Y1(v) + 4).toFixed(1)}" text-anchor="start" fill="${colAsse1}">${num(v, sc1.passo < 1 ? 1 : 0)}</text>`).join('');
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
    <text class="cur-t" x="0" y="${T + 10}" visibility="hidden"></text>
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
    const v = t == null ? l.punti[l.punti.length - 1][1] : valoreA(l.punti, t, tol);
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
    tx.textContent = dm(t); tx.setAttribute('x', Math.max(L + 40, Math.min(L + pw - 40, x))); tx.setAttribute('text-anchor', 'middle'); tx.setAttribute('visibility', 'visible');
  });
  (S.grafici || []).forEach(g => legenda(g, t));
}
function togliCursore() {
  document.querySelectorAll('#grafici .cur, #grafici .cur-t, #grafici .cur-d').forEach(e => e.setAttribute('visibility', 'hidden'));
  (S.grafici || []).forEach(g => legenda(g, null));
}
function muoviCursore(e) {
  const svg = e.target.closest && e.target.closest('#grafici svg'); const d = S.dati;
  if (!svg || !d || S.dpag !== 'analisi') return false;
  const r = svg.getBoundingClientRect(), L = +svg.dataset.l, pw = +svg.dataset.pw;
  const f = Math.max(0, Math.min(1, (e.clientX - r.left - L) / pw));
  cursore(d.da + f * (d.a - d.da)); return true;
}


// ---------- Impostazioni > Registro ----------
const TAB_IMP = [['', 'Stato'], ['collegamenti', 'Collegamenti'], ['avvisi', 'Avvisi'], ['accesso', 'Accesso'], ['dati', 'Dati'], ['registro', 'Registro']];
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
  } catch (e) { box.innerHTML = '<article class="card"><div class="vuoto">Non riesco a leggere il registro: il pannello non risponde.</div></article>'; }
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
    <p class="nota-fondo" style="margin:8px 0 0">Ultime ${d.righe.length} righe di <code>pdc_pannello.log</code>. I token e le password vengono nascosti, ma controlla comunque prima di incollare il testo in giro.</p></article>`;
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

// ---------- Changelog ----------
// Una voce per ogni consegna, la piu' recente in alto. Testi in italiano semplice (i dettagli tecnici stanno in PIANO.md).
const CHANGELOG = [
  {v: '0.17', quando: '3 novembre 2026', titolo: 'Aggiornare senza fermare la raccolta dei dati', righe: [
    'Impostazioni → Stato → «Aggiornamento»: quando sostituisci il file del pannello senza riavviarlo, qui compare cosa è cambiato. Se sono cambiate solo le parti di interfaccia (pagine, grafici, API, Impostazioni, Dispositivi, comandi allo Smarther) premi «Applica aggiornamento»: il codice nuovo entra a caldo e la lettura dei dati dalla pompa di calore e dallo Smarther non si ferma mai. Le pagine aperte si ricaricano da sole.',
    'Se invece è cambiato il nucleo (come si leggono e si decodificano i dati, Telegram, tunnel, backup) il pannello lo dice e serve il riavvio completo, come prima. Il numero di versione da solo non richiede riavvio.',
    'Se il file nuovo ha un errore, non cambia niente: continua a girare il codice di prima, con il motivo scritto in chiaro.',
    'La prima volta (per far entrare questa funzione) serve comunque un riavvio completo.',
  ]},
  {v: '0.16', quando: '3 novembre 2026', titolo: 'Cartelle in ordine', righe: [
    'Il pannello non butta più tutto nella stessa cartella: il database sta in «dati», le copie di sicurezza in «dati/backup», i tuoi loghi in «dati/loghi», i file di registro in «log», il tunnel Cloudflare nella sua cartella. In radice restano pdc_pannello.py, pdc_strumenti.py e config.json (quello che modifichi a mano).',
    'Al primo avvio dopo l\'aggiornamento sposta da solo i file vecchi nei posti nuovi e aggiorna config.json (solo se avevi i percorsi di prima: se ne avevi scelti di tuoi non li tocca). Non copia e non cancella: sposta, e se qualcosa non torna rimette tutto com\'era e si ferma dicendolo.',
    'Se il pannello è ancora acceso (o c\'è un altro database con lo stesso nome) non sposta niente e te lo dice. I loghi messi per abitudine nella vecchia cartella «loghi» si vedono ancora.',
    'README e PIANO vanno in una cartella «docs» (nel pacco te li mando separati: spostali tu).',
  ]},
  {v: '0.15', quando: '3 novembre 2026', titolo: 'Un solo grafico per compressore, portata e pompa', righe: [
    'Analisi: i tre grafici separati diventano uno, «Compressore, portata e pompa». Il compressore è un\'area a gradini (Hz), la pompa una linea a gradini (%) sulla stessa scala a sinistra, la portata una linea a gradini (L/min) con la sua scala a destra, dello stesso colore.',
    'I valori sono a gradini perché arrivano ogni pochi minuti e restano validi fino al successivo: prima la linea inclinata inventava valori intermedi. Passando il mouse compaiono i puntini su ogni linea e la legenda mostra i valori di quell\'ora.',
    'Potenza elettrica e gas refrigerante restano grafici a parte.',
    'Analisi, «Temperatura ambiente · Smarther»: in basso c\'è una striscia rossa piena quando il termostato chiede caldo (prima c\'era solo una tinta leggera dietro il grafico) e la legenda dice «Richiesta caldo sì/no» all\'ora scelta.',
    'Dashboard: la nota sul ritardo dei dati è su una riga ordinata, senza «min» rimasto solo a capo.',
    'Dashboard: «Gas di scarico» compare solo se la pompa di calore manda davvero quel valore (la tua mostrava sempre un trattino).',
    'Dashboard: la scheda «Compressore e circuito» mostra i valori solo mentre il compressore lavora. Quando è fermo (quasi sempre) vedi lo stato «Fermo» e tre riquadri sull\'ultimo ciclo: per cosa (riscaldamento o acqua calda), durata ed energia.',
  ]},
  {v: '0.14.2', quando: '3 novembre 2026', titolo: 'Grafici più leggibili', righe: [
    'Analisi: il grafico della potenza elettrica non è più spezzettato. Ogni rapporto della pompa vale per la sua finestra di tempo (è una media), quindi ora la linea è continua a gradini.',
    'Analisi: passando il mouse su un grafico compare un puntino colorato su ogni linea, esattamente sul valore di quell\'ora.',
    '«Modo di funzionamento»: nel riepilogo in alto «casa» diventa «riscaldamento».',
  ]},
  {v: '0.14.1', quando: '3 novembre 2026', titolo: 'Il riepilogo racconta anche la notte', righe: [
    'Se il silenzio notturno è acceso, il riepilogo del mattino aggiunge una sezione «Di notte» solo quando è successo qualcosa: temperatura in casa variata di almeno 2 °C (minimo e massimo), umidità salita sopra la soglia gialla (con il picco e l\'ora) e avvisi scattati e già rientrati durante il silenzio.',
    'La soglia delle oscillazioni di temperatura si cambia in Impostazioni → Avvisi → «Silenzio notturno e riepilogo». Se la notte è tranquilla la sezione non compare.',
  ]},
  {v: '0.14', quando: '3 novembre 2026', titolo: 'Umidità in casa: soglie e avvisi', righe: [
    'Due nuovi avvisi Telegram: «Umidità alta» (sopra 60 %) e «Umidità molto alta» (sopra 70 %). L\'umidità arriva dallo Smarther.',
    'Le soglie si cambiano in un solo posto, Impostazioni → Avvisi → «Umidità in casa», e valgono per Telegram, Dashboard e grafico. Lì si regolano anche i minuti da aspettare prima di avvisare (30) e di quanti punti deve scendere per dire che è rientrata (2). Ogni avviso si può spegnere in «Quando avvisarti».',
    'Dashboard: il valore dell\'umidità diventa giallo sopra la prima soglia e rosso sopra la seconda. Il resto non cambia.',
    'Analisi: nel grafico «Umidità · Smarther» due linee tratteggiate (gialla e rossa) mostrano le soglie quando i valori si avvicinano.',
  ]},
  {v: '0.13.1', quando: '3 novembre 2026', titolo: 'Rifiniture per iPhone e grafici', righe: [
    'Dispositivi su telefono: logo a sinistra e stato (ONLINE e «ultimo dato») a destra sulla stessa riga, sotto titolo e sottotitolo. I loghi hanno una casella di misura fissa, così non si sovrappongono più al testo.',
    'Analisi: il grafico della temperatura dello Smarther si chiama «Temperatura ambiente · Smarther» e quello dell\'umidità «Umidità · Smarther», per far capire da dove arrivano i dati.',
    'Impostazioni → Stato mostra «acceso da 2 minuti» (non più «da 2 min fa»), e anche nella scheda Dispositivi.',
  ]},
  {v: '0.13', quando: '3 novembre 2026', titolo: 'Impostazioni in tab', righe: [
    'Impostazioni è divisa in sei tab: Stato, Collegamenti, Avvisi, Accesso, Dati e Registro. Ogni riquadro si apre e si chiude con un tocco e il pannello ricorda come l\'hai lasciato.',
    'Nuovi riquadri nel tab Dati: litri del bollitore e backup (acceso/spento e quante copie tenere), prima solo in config.json.',
    'Se un valore non è valido, il pannello apre il riquadro giusto, passa al tab e lo segna in rosso.',
    'In Cloudflare c\'è il pulsante «Invia il link su Telegram».',
    'Messaggio di avvio più pulito (solo rete di casa e indirizzo Cloudflare) e «acceso da 2 minuti» al posto di «acceso da 2 min fa».',
  ]},
  {v: '0.12.1', quando: '3 novembre 2026', titolo: 'Più sicuro e più robusto', righe: [
    'Il «tempo reale» su Safari si ricollega da solo quando la scheda era in pausa o la rete è cambiata.',
    'Se il disco è pieno o il database non si può scrivere, la raccolta dati, i controlli automatici e il tunnel Cloudflare non si fermano più per sempre: riprovano da soli e lo scrivono nel registro.',
    'Se il database è danneggiato, il pannello lo dice chiaramente e indica il comando «ripristina», che ora funziona anche in quel caso.',
    'config.json (password e token) è leggibile solo dal proprietario del file, e viene scritto con più cura per resistere a un blackout.',
    'Il login rifiuta richieste che arrivano da altri siti, le richieste malformate non bloccano più il pannello e le pagine aperte in tempo reale sono al massimo 20.',
    'Efficienza: sistemato lo spazio sopra la scheda «Salute del bollitore».',
  ]},
  {v: '0.12', quando: '3 novembre 2026', titolo: 'Confronto, bollitore, registro e ripristino', righe: [
    'Efficienza: nuovo pulsante «Confronta» che mette i numeri del periodo accanto a quelli del periodo precedente della stessa lunghezza (COP, energia, calore, cicli). All\'apertura della pagina è spento.',
    'Efficienza: nuova scheda «Salute del bollitore» con quanto calore perde di notte e quanto ci mette a scaldarsi. Serve qualche notte di dati; i litri si cambiano in config.json (sezione bollitore).',
    'Impostazioni: nuova scheda «Registro» con le ultime righe del registro del pannello, le dimensioni di database e log e lo stato dei backup.',
    'Nuovo comando «python pdc_pannello.py ripristina»: mostra le copie di backup, ti fa scegliere, mette da parte il database attuale e rimette quello scelto. Si usa a pannello fermo.',
  ]},
  {v: '0.11.2', quando: '2 novembre 2026', titolo: 'Correzioni dopo le prove sull\'impianto vero', righe: [
    'Acqua calda e riscaldamento non restano più in «Standby» o «A riposo» mentre la pompa sta partendo: se il compressore è in ritardo, il pannello guarda anche l\'acqua dell\'impianto, che sale subito.',
    'Nella scheda «Compressore e circuito» compare «dato di N min fa», e il pannello misura il ritardo con cui arrivano i dati (tipico e massimo delle ultime 24 ore).',
    'Smarther: ora il pannello capisce se è spento sia dal pannello sia dall\'app, e lo riaccende anche se l\'hai spento dall\'app. Ogni comando viene verificato su Netatmo prima di dirti che è andato.',
    'Badge giallo «ATTENDI…» sugli interruttori mentre il comando è in corso; lo sblocco di 30 secondi riparte a ogni azione.',
    'Comandi dati dal pannello: a pagine da 5.',
    'Costo dell\'energia in euro (metti il prezzo in Impostazioni), anche per un ciclo di acqua calda.',
    'Esportazione in CSV per Excel (pagina Efficienza).',
    'Backup notturno automatico di database e configurazione, e comando «backup».',
    'Nuova pagina Changelog.']},
  {v: '0.11.1', quando: '2 novembre 2026', titolo: 'Primo aggiustamento dello Smarther', righe: ['Prima correzione dell\'accensione dello Smarther, completata poi nella 0.11.2.']},
  {v: '0.11', titolo: 'Accendere e spegnere dal pannello', righe: [
    'Interruttori per Riscaldamento casa, Acqua calda sanitaria e Termostato Smarther, nella scheda «Stato del sistema».',
    'Bloccati finché non premi «Sblocca comandi» (30 secondi), conferma allo spegnimento, registro dei comandi in Dispositivi.',
    'Funzionano anche da fuori casa. Servono token con il permesso di comando (vedi README).']},
  {v: '0.10', titolo: 'Impostazioni installatore', righe: [
    'Nuova scheda «Installatore» dentro Dispositivi: le impostazioni FSV che la pompa comunica, in sola lettura, per gruppi e con il nome in italiano.',
    'Per ognuna c\'è l\'intervallo previsto dal manuale e un avviso se il valore ne esce; «Copia elenco» per mandarle all\'installatore.']},
  {v: '0.9.3', titolo: 'Loghi e cache', righe: [
    'Loghi più ordinati nella pagina Dispositivi (BTicino nella riga «Produttore»).',
    'Quando il pannello viene aggiornato, le pagine rimaste aperte si ricaricano da sole.']},
  {v: '0.9.2', titolo: 'Rifinitura', righe: ['Logo Smarther più piccolo e allineato nella scheda Casa.']},
  {v: '0.9.1', titolo: 'Stato «In funzione / A riposo»', righe: ['Corretto il caso in cui il pannello diceva «A riposo» a pompa in marcia, per il ritardo dei dati del compressore.']},
  {v: '0.9', titolo: 'Pagina Dispositivi', righe: [
    'Una scheda per la pompa e una per lo Smarther: modello, versioni, identificativi con «Copia», IP e MAC, stato Online/Offline.',
    'Storico delle versioni: il pannello segna quando cambia un firmware o un software.',
    'Loghi dalla cartella «loghi» (li metti tu).']},
  {v: '0.8 – 0.8.4', titolo: 'Colori e dettagli', antico: true, righe: [
    'Badge di stato a colori pieni (verde acceso, arancio richiesta di calore, giallo sbrinamento, blu fermo, rosso spento).',
    'Etichette di stato sempre sotto il titolo delle schede; verde più chiaro e leggibile.',
    'Primo intervento sul ritardo del compressore: l\'acqua calda non va più in «Standby» durante un ciclo.']},
  {v: 'Fino alla 0.7', titolo: 'Come è nato (ricostruito dal piano di lavoro)', antico: true, righe: [
    'Pezzo 1 – Il nucleo: lettura della pompa da SmartThings e dello Smarther da Netatmo, database SQLite, decodifica dei dati del ciclo (compressore, mandata, ritorno, portata…).',
    'Pezzo 2 – Il sito: server web, login con sessioni, Dashboard con la curva climatica, aggiornamento in tempo reale, modalità demo.',
    'Pezzo 3 – Analisi ed Efficienza: grafici sincronizzati, COP stimato, energia al giorno, cicli di funzionamento.',
    'Pezzo 4 – Telegram: avvisi e controllo anomalie (pompa ferma con richiesta di calore, casa fredda, cicli brevi, portata bassa…), riepilogo del mattino.',
    'Pezzo 5 – Impostazioni: modifica di config.json dal pannello, cambio password, stato dei collegamenti.',
    'Pezzo 6 – Accesso da fuori casa: procedura guidata per il tunnel Cloudflare.',
    'Pezzo 7 – README, compattazione automatica dello storico vecchio, rifinitura.']},
];
function disegnaChangelog() {
  const voce = c => `<article class="card cl"><div class="ch"><h2>${esc(c.v)}${c.titolo ? ` <span class="cl-t">· ${esc(c.titolo)}</span>` : ''}</h2>${c.quando ? `<span class="et">${esc(c.quando)}</span>` : ''}</div><ul>${c.righe.map(r => `<li>${esc(r)}</li>`).join('')}</ul></article>`;
  const nuove = CHANGELOG.filter(c => !c.antico), vecchie = CHANGELOG.filter(c => c.antico);
  return `<div class="changelog">${nuove.map(voce).join('')}<details class="cl-vecchie"><summary>Le prime versioni (fino alla 0.8)</summary>${vecchie.map(voce).join('')}</details></div>`;
}

// ---------- Efficienza ----------
const euro = v => v == null ? '—' : v.toLocaleString('it-IT', {style: 'currency', currency: 'EUR'});
const fm = (v, n = 1) => v == null ? '—' : num(v, n);
const giornoBreve = g => { const [y, m, d] = g.giorno.split('-'); return `${d}/${m}`; };

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
function notaConf(d) {
  if (!S.confronto) return '';
  if (!d.prec) return '';
  const da = dataLunga(d.prec.da), a = dataLunga(d.prec.a);
  if (!d.prec.completo)
    return `<div class="avviso warn"><span>ℹ️</span><div>Il pannello non ha ancora dati per tutto il periodo di confronto (${esc(da)} – ${esc(a)}): il confronto comparirà quando ci saranno.</div></div>`;
  const dm = d.prec.nodati_min;
  return `<p class="nota-fondo conf-nota">Confronto con il periodo di pari durata subito prima: dal ${esc(da)} al ${esc(a)}.${dm > 120 ? ` In quel periodo mancano circa ${durata(dm)} di dati: il confronto è meno preciso.` : ''}</p>`;
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
  return `<article class="card"><h2>Salute del bollitore</h2><div class="tiles">${tiles}</div>
    <p class="nota-fondo" style="margin:10px 0 0">Stime dalla sonda dell'acqua calda. La <b>perdita</b> è di quanto scende la temperatura nel tratto notturno più lungo (23–06) senza che la pompa scaldi l'acqua; i watt sono calcolati su ${b ? b.litri : 300} litri (modificabili in <code>config.json</code>, sezione <code>bollitore</code>). Se cresce nel tempo può indicare un problema di isolamento o di ricircolo. Il <b>tempo per salire di 10 °C</b> dice quanto è rapido il riscaldo: se si allunga nel tempo, la pompa rende di meno (o l'acqua da scaldare è di più). Sono indicazioni, non diagnosi.</p></article>`;
}
function disegnaEfficienza(d) {
  const t = d.tot, nodati = d.giorni.reduce((a, g) => a + g.nodati_min, 0);
  const cop = v => v == null ? '—' : num(v, 2);
  const pt = d.prec ? d.prec.tot : {};
  const cov = d.copertura, bassa = [cov.casa, cov.acs].some(v => v != null && v < 80);
  const html_ = `
  <div class="cards">
    <article class="card acc" style="--acc:var(--est)"><div class="ch"><span class="et">COP medio</span></div>
      <div class="val"><div class="n"><span>${cop(t.cop)}</span></div></div>
      <div class="sub"><span>Casa <b>${cop(t.cop_casa)}</b></span><span>Acqua calda <b>${cop(t.cop_acs)}</b></span></div>${rigaConf(d, t.cop, pt.cop, cop, 'su', 0.1)}</article>
    <article class="card acc" style="--acc:var(--en)"><div class="ch"><span class="et">Energia elettrica</span></div>
      <div class="val"><div class="n"><span>${fm(t.el_casa + t.el_acs + t.el_riposo)}</span><i>kWh</i></div></div>
      <div class="sub"><span>Casa <b>${fm(t.el_casa)}</b></span><span>Acqua <b>${fm(t.el_acs)}</b></span><span>Standby <b>${fm(t.el_riposo)}</b></span>${d.prezzo_kwh > 0 ? `<span>Costo stimato <b>${euro((t.el_casa + t.el_acs + t.el_riposo) * d.prezzo_kwh)}</b></span>` : ''}</div>${rigaConf(d, t.el_casa + t.el_acs + t.el_riposo, pt.el_casa + pt.el_acs + pt.el_riposo, v => fm(v) + ' kWh', 'giu', 1)}</article>
    <article class="card acc" style="--acc:var(--casa)"><div class="ch"><span class="et">Calore prodotto (stima)</span></div>
      <div class="val"><div class="n"><span>${fm(t.calore_casa + t.calore_acs)}</span><i>kWh</i></div></div>
      <div class="sub"><span>Casa <b>${fm(t.calore_casa)}</b></span><span>Acqua <b>${fm(t.calore_acs)}</b></span></div>${rigaConf(d, t.calore_casa + t.calore_acs, pt.calore_casa + pt.calore_acs, v => fm(v) + ' kWh', '', 1)}</article>
    <article class="card acc" style="--acc:var(--acs)"><div class="ch"><span class="et">Ore di funzionamento</span></div>
      <div class="val"><div class="n"><span>${fm(t.ore_casa + t.ore_acs)}</span><i>h</i></div></div>
      <div class="sub"><span>Casa <b>${fm(t.ore_casa)}</b></span><span>Acqua <b>${fm(t.ore_acs)}</b></span></div>${rigaConf(d, t.ore_casa + t.ore_acs, pt.ore_casa + pt.ore_acs, v => fm(v) + ' h', '', 0.5)}</article>
  </div>
  ${notaConf(d)}
  ${bassa || nodati > 120 ? `<div class="avviso warn"><span>⚠️</span><div>Dati incompleti${bassa ? `: per il calcolo del COP ho potenza e portata solo per il ${Math.min(cov.casa ?? 100, cov.acs ?? 100)}% del tempo di funzionamento` : ''}${nodati > 120 ? ` (mancano circa ${durata(nodati)} di dati nel periodo)` : ''}. I valori sono più bassi o meno precisi del reale.</div></div>` : ''}
  <div class="grid2 uguali">
    <article class="card"><div class="ch"><h2>Energia elettrica al giorno</h2><div class="legg"><span><i style="background:var(--casa)"></i>Casa</span><span><i style="background:var(--acs)"></i>Acqua</span><span><i style="background:#3a4558"></i>Standby</span></div></div><div id="g-en"></div></article>
    <article class="card"><div class="ch"><h2>COP al giorno</h2><div class="legg"><span><i style="background:var(--casa)"></i>Casa</span><span><i style="background:var(--acs)"></i>Acqua</span></div></div><div id="g-cop"></div></article>
  </div>
  <div class="grid2 uguali">
    <article class="card"><div class="ch"><h2>COP in base alla temperatura esterna</h2></div><div id="g-bin"></div>
      <p class="nota-fondo" style="margin:6px 0 0">Ogni punto è una fascia di 2 °C e compare solo se la pdc ha lavorato lì almeno mezz'ora.</p></article>
    <article class="card"><h2>Cicli di funzionamento</h2><div class="tiles" id="cicli">${cicliHtml(d)}</div>
      <p class="nota-fondo" style="margin:10px 0 0">Un ciclo è un tratto continuo di lavoro. Molti cicli sotto i 10 minuti sono un segnale da tenere d'occhio: l'impianto parte e si ferma troppo spesso.</p></article>
  </div>
  ${cardBollitore(d)}
  <p class="nota-fondo">Il calore è una stima: portata × salto di temperatura tra mandata e ritorno × 0,0698 kW per (L/min·°C). Il COP è calore / energia elettrica nei soli momenti di lavoro (casa + acqua calda); lo standby è escluso dal COP. Il totale elettrico include tutto.${d.energia_contatore != null ? ` Contatore della pdc nel periodo: ${fm(d.energia_contatore, 1)} kWh.` : ''}</p>
  <p class="nota-fondo esporta">Esporta per Excel: <a href="/api/esporta.csv?tipo=giorni&giorni=${d.n_giorni}" download>riepilogo per giorno (${d.n_giorni} giorni)</a> · <a href="/api/esporta.csv?tipo=misure&giorni=7" download>dati grezzi degli ultimi 7 giorni</a></p>`;
  const box = $('#eff'); const f = JSON.stringify([d.ora, d.n_giorni, box.clientWidth]);
  if (box.dataset.f === f) return;
  box.dataset.f = f; box.innerHTML = html_;
  const w = box.querySelector('#g-en').clientWidth || 320;
  $('#g-en').innerHTML = barreGiorni(d.giorni, w);
  $('#g-cop').innerHTML = lineeGiorni(d.giorni, w);
  $('#g-bin').innerHTML = curvaCop(d.bins, w);
}

function cicliHtml(d) {
  const c = d.cicli, t = (k, v) => `<div class="tile"><small>${k}</small><b>${v}</b></div>`;
  return t('Cicli casa', c.casa.n) + t('Durata media casa', c.casa.media_min == null ? '—' : durata(Math.round(c.casa.media_min))) +
    t('Cicli brevi (<10 min)', c.casa.brevi) + t('Cicli acqua calda', c.acs.n) +
    t('Durata media acqua', c.acs.media_min == null ? '—' : durata(Math.round(c.acs.media_min))) + t('Sbrinamenti', d.sbrinamenti) +
    (d.prezzo_kwh > 0 && c.acs.n > 0 ? t('Costo medio di un ciclo acqua calda', euro(d.tot.el_acs * d.prezzo_kwh / c.acs.n)) : '');
}

function barreGiorni(giorni, W) {
  const H = 210, L = 34, R = 6, T = 14, B = 24, pw = W - L - R, ph = H - T - B, n = giorni.length;
  const tot = g => g.el_casa + g.el_acs + g.el_riposo, mx = Math.max(1, ...giorni.map(tot));
  const sc = scalaNice(0, mx, 4); sc.lo = 0;
  const Y = v => T + ph - v / sc.hi * ph, bw = Math.min(46, pw / n * 0.7), cx = i => L + pw / n * (i + 0.5);
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">`;
  s += sc.tick.filter(v => v >= 0).map(v => `<line x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" class="gl"/><text x="${L - 6}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${num(v, 0)}</text>`).join('');
  giorni.forEach((g, i) => {
    let y = Y(0);
    [['el_riposo', '#3a4558'], ['el_acs', COLS.acs], ['el_casa', COLS.casa]].forEach(([k, c]) => {
      const h = g[k] / sc.hi * ph; if (h <= 0) return; y -= h;
      s += `<rect x="${(cx(i) - bw / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(0.5, h).toFixed(1)}" fill="${c}" rx="2"><title>${giornoBreve(g)}: ${k.replace('el_', '')} ${num(g[k], 1)} kWh</title></rect>`;
    });
    if (n <= 10) s += `<text x="${cx(i).toFixed(1)}" y="${(y - 4).toFixed(1)}" text-anchor="middle" class="vt">${num(tot(g), 1)}</text>`;
    if (n <= 10 || i % Math.ceil(n / 7) === 0) s += `<text x="${cx(i).toFixed(1)}" y="${H - 7}" text-anchor="middle">${giornoBreve(g)}</text>`;
  });
  return s + '</svg>';
}

function lineeGiorni(giorni, W) {
  const H = 210, L = 34, R = 22, T = 14, B = 24, pw = W - L - R, ph = H - T - B, n = giorni.length;
  const vals = giorni.flatMap(g => [g.cop_casa, g.cop_acs]).filter(v => v != null);
  const sc = scalaNice(0, Math.max(5, ...(vals.length ? vals : [0])), 5); sc.lo = 0;
  const Y = v => T + ph - v / sc.hi * ph, X = i => n === 1 ? L + pw / 2 : L + pw * i / (n - 1);
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">`;
  s += sc.tick.filter(v => v >= 0).map(v => `<line x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" class="gl"/><text x="${L - 6}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${num(v, 0)}</text>`).join('');
  giorni.forEach((g, i) => { if (n <= 10 || i % Math.ceil(n / 7) === 0) s += `<text x="${X(i).toFixed(1)}" y="${H - 7}" text-anchor="middle">${giornoBreve(g)}</text>`; });
  [['cop_casa', COLS.casa], ['cop_acs', COLS.acs]].forEach(([k, c]) => {
    let p = '', prec = false;
    giorni.forEach((g, i) => { if (g[k] == null) { prec = false; return; } p += (prec ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(g[k]).toFixed(1) + ' '; prec = true; });
    s += `<path d="${p}" fill="none" stroke="${c}" stroke-width="2.2" stroke-linejoin="round"/>`;
    giorni.forEach((g, i) => { if (g[k] != null) s += `<circle cx="${X(i).toFixed(1)}" cy="${Y(g[k]).toFixed(1)}" r="${n > 14 ? 2.5 : 3.5}" fill="${c}"><title>${giornoBreve(g)}: COP ${num(g[k], 2)}</title></circle>`; });
  });
  return s + '</svg>';
}

function curvaCop(bins, W) {
  const H = 210, L = 34, R = 10, T = 14, B = 34, pw = W - L - R, ph = H - T - B;
  const tutti = [...bins.casa, ...bins.acs];
  if (!tutti.length) return '<div class="vuoto">Servono più ore di funzionamento per questo grafico.</div>';
  const x0 = Math.min(...tutti.map(b => b.da)), x1 = Math.max(...tutti.map(b => b.a)), mx = Math.max(5, ...tutti.map(b => b.cop));
  const sc = scalaNice(0, mx, 5); sc.lo = 0;
  const X = t => L + (t - x0) / Math.max(1, x1 - x0) * pw, Y = v => T + ph - v / sc.hi * ph;
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">`;
  s += sc.tick.filter(v => v >= 0).map(v => `<line x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" class="gl"/><text x="${L - 6}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${num(v, 0)}</text>`).join('');
  for (let t = Math.ceil(x0 / 2) * 2; t <= x1; t += (x1 - x0 > 20 ? 5 : 2)) s += `<text x="${X(t).toFixed(1)}" y="${H - 16}" text-anchor="middle">${t}°</text>`;
  s += `<text x="${(L + pw / 2).toFixed(1)}" y="${H - 2}" text-anchor="middle" class="ass">Temperatura esterna</text>`;
  [['casa', COLS.casa], ['acs', COLS.acs]].forEach(([k, c]) => {
    const b = bins[k]; if (!b.length) return;
    const P = b.map(x => [X((x.da + x.a) / 2), Y(x.cop)]);
    s += `<path d="${P.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ')}" fill="none" stroke="${c}" stroke-width="2.2" stroke-linejoin="round"/>`;
    b.forEach((x, i) => { s += `<circle cx="${P[i][0].toFixed(1)}" cy="${P[i][1].toFixed(1)}" r="4" fill="${c}"><title>${x.da}…${x.a} °C esterni: COP ${num(x.cop, 2)} (${num(x.ore, 1)} h)</title></circle>`;
      if (k === 'casa' && b.length <= 12) s += `<text x="${P[i][0].toFixed(1)}" y="${(P[i][1] - 8).toFixed(1)}" text-anchor="middle" class="vt">${num(x.cop, 1)}</text>`; });
  });
  return s + '</svg>';
}

// ---------- Impostazioni ----------
const quando = t => t ? dm(t) : '—';
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
    ${s.gruppi.map(g => `<section class="dev-gr"><h3>${esc(g.titolo)}</h3>${g.righe.map(r => rigaDisp(r, d)).join('')}</section>`).join('')}
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
  try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(t); return true; } } catch (e) { /* si prova l'altro metodo */ }
  try {
    const a = document.createElement('textarea'); a.value = t; a.setAttribute('readonly', '');
    a.style.cssText = 'position:fixed;top:0;left:0;opacity:0;font-size:16px'; document.body.appendChild(a);
    a.select(); a.setSelectionRange(0, t.length); const ok = document.execCommand('copy'); a.remove(); return ok;
  } catch (e) { return false; }
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
  else if (c.tipo === 'segreto') ctl = `<input type="password" id="${id}" data-k="${c.chiave}" autocomplete="new-password" placeholder="${c.impostato ? '•••••• (impostato: scrivi per cambiare)' : 'non impostato'}">`;
  else if (c.tipo === 'lungo') ctl = `<textarea id="${id}" data-k="${c.chiave}" rows="${Math.min(7, Math.max(2, String(c.valore).split('\n').length + 1))}" spellcheck="false">${esc(c.valore)}</textarea>`;
  else if (c.tipo === 'int' || c.tipo === 'float') ctl = `<input type="text" inputmode="decimal" id="${id}" data-k="${c.chiave}" value="${esc(String(c.valore).replace('.', ','))}">`;
  else ctl = `<input type="${c.tipo === 'ora' ? 'time' : 'text'}" id="${id}" data-k="${c.chiave}" value="${esc(c.valore)}" autocapitalize="none" autocorrect="off" spellcheck="false">`;
  return `<div class="campo ${c.tipo === 'bool' ? 'riga-sw' : ''}"><label for="${id}">${esc(c.et)}</label>${ctl}${h}<small class="err" data-e="${c.chiave}"></small></div>`;
}

// Riquadri collassabili: lo stato aperto/chiuso si ricorda per ogni riquadro (per il primo di ogni tab, aperto di partenza)
function impAperti() { try { return JSON.parse(localStorage.getItem('pdc_imp_aperti') || '{}') || {}; } catch (e) { return {}; } }
function impRicorda(id, aperto) { try { const m = impAperti(); m[id] = aperto; localStorage.setItem('pdc_imp_aperti', JSON.stringify(m)); } catch (e) { /* niente */ } }
function riquadro(id, titolo, corpo, primo, extra = '') {
  const m = impAperti(), ap = id in m ? m[id] : !!primo;
  return `<details class="card imp riq" data-id="${id}" ${ap ? 'open' : ''}><summary><h2>${esc(titolo)}</h2>${extra}</summary><div class="riq-c">${corpo}</div></details>`;
}
document.addEventListener('toggle', e => { const t = e.target; if (t.classList && t.classList.contains('riq') && t.dataset.id) impRicorda(t.dataset.id, t.open); }, true);
// mostra un campo (per esempio con un errore): apre i riquadri che lo contengono e passa al tab giusto
function mostraCampo(el) {
  let n = el; while ((n = n.closest && n.closest('details'))) { n.open = true; n = n.parentElement; }
  const g = el.closest('.imp-tab'); if (g && g.hidden) location.hash = '#/impostazioni' + (g.dataset.t === 'stato' ? '' : '/' + g.dataset.t);
}
function disegnaImpostazioni(d) {
  const box = $('#imp'); if (!box) return;
  const stato_ = $('#imp-stato'); if (stato_) stato_.innerHTML = statoImpostazioni(d.stato);
  if (box.dataset.pronto && S.sporco) return;            // non cancellare quello che stai scrivendo
  if (box.dataset.pronto === '1' && !S.ricarica) return;
  S.ricarica = false; box.dataset.pronto = '1';
  const sez = t => d.sezioni.filter(s => s.tab === t).map((s, i) => riquadro('s-' + s.id, s.titolo, `
      ${s.nota ? `<p class="nota-sez">${esc(s.nota)}</p>` : ''}
      <div class="campi">${s.campi.map(campoHtml).join('')}</div>
      ${s.prova ? '<div class="azioni"><button type="button" class="sec" id="b-prova">Invia messaggio di prova</button><small class="mut">Usa i valori già salvati: salva prima.</small></div>' : ''}`, i === 0)).join('');
  const all = d.allarmi.map(a => `
    <details class="allarme"><summary><span class="t">${esc(a.titolo)}</span><span class="d">${esc(a.descrizione)}</span></summary>
      <div class="campi">${a.campi.map(campoHtml).join('')}</div></details>`).join('');
  const pw = `<div class="campi">
      <div class="campo"><label for="p-ut">Utente</label><input id="p-ut" type="text" autocomplete="username" value="${esc(d.utente || '')}" autocapitalize="none"></div>
      <div class="campo"><label for="p-at">Password attuale</label><input id="p-at" type="password" autocomplete="current-password"></div>
      <div class="campo"><label for="p-nu">Nuova password (almeno 10 caratteri)</label><input id="p-nu" type="password" autocomplete="new-password"></div></div>
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
    <div class="imp-tab" data-t="accesso" hidden>
      ${riquadro('password', 'Password del pannello', pw, true)}
      <details class="card imp riq" data-id="cf" id="cf-d" ${cfAp ? 'open' : ''}><summary><h2>Accesso da internet (Cloudflare)</h2></summary><div class="riq-c" id="cf"><div class="vuoto" style="padding:14px 0">Controllo l'accesso da internet…</div></div></details></div>
    <div class="imp-tab" data-t="dati" hidden>${sez('dati')}</div>
    <div class="barra-salva" id="barra" hidden><span id="barra-t">Modifiche non salvate</span><button type="button" class="sec" id="b-annulla">Annulla</button><button type="button" id="b-salva">Salva</button></div>`;
  S.mod = {}; S.sporco = false;
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
  } catch (e) { box.textContent = 'Non riesco a controllare gli aggiornamenti.'; }
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
    } catch (err) { toast('Il pannello non risponde: riprova fra qualche secondo', true); }
    delete box.dataset.occupato; aggCarica(); return;
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
    } catch (x) { toast('Server non raggiungibile', true); }
    e.target.disabled = false;
  } else if (id === 'b-prova') {
    e.target.disabled = true; try { const r = await inviaPost('/api/impostazioni/telegram-prova', {}); toast(r.messaggio || 'Fatto', !r.ok); } catch (x) { toast('Server non raggiungibile', true); }
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
  let st; try { st = await cfLeggi(); } catch (e) { return; }
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
    <li>Dagli un nome, per esempio <i>pannello pdc</i>.</li>
    <li>In <b>Permissions</b> aggiungi tre righe:<br>• <b>Account</b> · <b>Cloudflare Tunnel</b> · <b>Edit</b><br>• <b>Zone</b> · <b>DNS</b> · <b>Edit</b><br>• <b>Zone</b> · <b>Zone</b> · <b>Read</b></li>
    <li>In <b>Account Resources</b> scegli il tuo account; in <b>Zone Resources</b> scegli <b>Include → Specific zone</b> e il tuo dominio.</li>
    <li>Premi <b>Continue to summary</b>, poi <b>Create Token</b> e <b>copia il token</b> (si vede una volta sola).</li></ol>
    <p class="nota-sez">Il token serve solo adesso: il pannello <b>non lo conserva</b>. Dopo puoi anche cancellarlo dal sito di Cloudflare.</p></details>
  <div class="campi" style="grid-template-columns:1fr">
    <div class="campo"><label for="cf-tok">2 · Incolla qui il token</label><input id="cf-tok" type="password" autocomplete="off" placeholder="il token di Cloudflare"></div></div>
  <div class="azioni"><button type="button" id="cf-verifica" ${st.demo ? 'disabled' : ''}>Verifica e cerca i miei domini</button><small class="msg" id="cf-msg"></small></div>
  <div id="cf-passo2"></div>`;
}
function cfPasso2(zone) {
  const opz = zone.map((z, i) => `<option value="${i}">${esc(z.nome)}</option>`).join('');
  return `<h3 class="sez-t" style="margin-top:20px">3 · Scegli l'indirizzo</h3>
  <div class="campi">
    <div class="campo"><label for="cf-zona">Dominio</label><select id="cf-zona">${opz}</select></div>
    <div class="campo"><label for="cf-sub">Sottodominio</label><input id="cf-sub" type="text" value="pdc" autocapitalize="none" autocorrect="off" spellcheck="false"></div>
    <div class="campo"><label for="cf-nome">Nome del tunnel (solo per riconoscerlo su Cloudflare)</label><input id="cf-nome" type="text" value="pannello-pdc" spellcheck="false"></div></div>
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
    <div class="campo"><label for="cf-rtok">Token Cloudflare (facoltativo)</label><input id="cf-rtok" type="password" autocomplete="off"></div>
    <div class="azioni"><button type="button" class="sec" id="cf-rimuovi" style="color:var(--bad)">Rimuovi</button><small class="msg" id="cf-rmsg"></small></div></details>
  <p class="nota-sez">Il tunnel è protetto dal tuo login. Se cambi la porta del pannello, rifai la procedura.</p>`;
}
async function cfAzione(e) {
  const id = e.target.id, msg = t => { const m = $('#cf-msg') || $('#cf-rmsg'); if (m) { m.textContent = t; m.className = 'msg bad'; } };
  if (id === 'cf-agg') { toast('Stato aggiornato'); return cfCarica(true); }
  if (id === 'cf-verifica') {
    e.target.disabled = true; $('#cf-msg').textContent = 'Controllo…'; $('#cf-msg').className = 'msg';
    CFS.token = $('#cf-tok').value.trim();
    try { const r = await inviaPost('/api/cloudflare/verifica', {token: CFS.token});
      if (r.ok) { CFS.zone = r.zone; $('#cf-msg').textContent = 'Token valido: ' + r.zone.length + ' dominio/i trovato/i.'; $('#cf-msg').className = 'msg ok'; $('#cf-passo2').innerHTML = cfPasso2(r.zone); cfAnteprima(); }
      else { $('#cf-msg').textContent = r.messaggio; $('#cf-msg').className = 'msg bad'; }
    } catch (x) { $('#cf-msg').textContent = 'Server non raggiungibile'; $('#cf-msg').className = 'msg bad'; }
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
    } catch (x) { m.textContent = 'Server non raggiungibile'; m.className = 'msg bad'; e.target.disabled = false; CFS.occupato = false; }
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
      let gia = null; try { gia = sessionStorage.getItem('pdc_build'); } catch (e) { /* niente */ }
      if (gia !== nuova) { try { sessionStorage.setItem('pdc_build', nuova); } catch (e) { /* niente */ } location.reload(); return; }
    }
    const dati = await r.json();
    if (pag !== S.pagina) return;
    S.dati = dati; S.dpag = pag; S.off = S.dati.ora - Math.floor(Date.now() / 1000); S.fetchedAt = Date.now();
    disegna();
  } catch (e) {
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
  if (flusso) { try { flusso.close(); } catch (e) { /* niente */ } flusso = null; }
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
      else if (S.dati) { const b = $('#dsp'); if (b) b.dataset.h = ''; disegna(); }
    }
  });
  document.addEventListener('pointermove', e => {
    if (e.target.closest && e.target.closest('#tlw')) tooltipTimeline(e);
    else { const t = $('#tip'); if (t) t.hidden = true; if (!muoviCursore(e)) togliCursore(); }
  });
  document.addEventListener('pointerleave', togliCursore);
  document.addEventListener('input', segnaModifica); document.addEventListener('change', segnaModifica);
  document.addEventListener('click', e => { if (e.target.closest && e.target.closest('#imp') && e.target.tagName === 'BUTTON') { if (e.target.id.startsWith('cf-')) cfAzione(e); else azioniImpostazioni(e); } });
  let rid = null;
  window.addEventListener('resize', () => { clearTimeout(rid); rid = setTimeout(() => { if (S.dati && S.pagina !== 'dashboard') { const b = $('#grafici') || $('#eff'); if (b) b.dataset.f = ''; disegna(); } }, 250); });
  document.addEventListener('pointerdown', e => { if (e.target.closest && e.target.closest('#tlw')) tooltipTimeline(e); else muoviCursore(e); });
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
