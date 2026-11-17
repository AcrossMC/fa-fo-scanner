(() => {
  "use strict";

  const TFS = [["1h", "1h"], ["4h", "4h"], ["1d", "1j"], ["1w", "1sem"], ["1mo", "1mois"]];
  const TF_KEYS = TFS.map(([k]) => k);
  const TF_LAB = Object.fromEntries(TFS);
  const TF_NAME = { "1h": "1 heure", "4h": "4 heures", "1d": "1 jour", "1w": "1 semaine", "1mo": "1 mois" };
  const BAR_MS = { "1h": 36e5, "4h": 144e5, "1d": 864e5, "1w": 6048e5, "1mo": 2678e6 };
  const TV_INT = { "1h": "60", "4h": "240", "1d": "D", "1w": "W", "1mo": "M" };
  const STATES = [["fa", "Fa"], ["s", 'S <i class="sq up"></i>']];
  const GH_VAR = "HOLDINGS";
  const lowerTf = (k) => TF_KEYS[TF_KEYS.indexOf(k) - 1];
  const higherTf = (k) => TF_KEYS[TF_KEYS.indexOf(k) + 1];

  let data = null;
  let investing = {};                       // docs/investing.json : ticker -> identifiant de page investing.com (facultatif)
  let view = store("view") === "own" ? "own" : "scan";
  let tf = store("tf") || "1d";
  let st = store("st") === "s" ? "s" : "fa";
  let query = "";
  let pos = loadPos();                      // positions : [{t, c: "S"|"Fa"|null, tf, e: prix d'entrée|null, ts}]
  let draft = null;                         // entrée en cours de saisie : {t, c, tf}
  let token = store("gh_token") || "";
  let repo = store("gh_repo") || detectRepo();

  const $ = (id) => document.getElementById(id);
  const tfTabs = $("tfTabs"), stTabs = $("stTabs"), viewTabs = $("viewTabs"), list = $("list"), sheet = $("sheet"), backdrop = $("backdrop");

  function store(k, v) {
    try { if (v === undefined) return localStorage.getItem(k); if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { return null; }
  }
  function detectRepo() {
    const h = location.hostname;
    return h.endsWith(".github.io") ? h.split(".")[0] + "/" + (location.pathname.split("/")[1] || "") : "";
  }
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const short = (t) => t.replace(/\.PA$/, "");
  const nf = (p, d = 2) => p.toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });
  const fmtPrice = (p) => nf(p, Math.abs(p) < 1 ? 4 : Math.abs(p) < 10 ? 3 : 2) + " €";   // décimales selon le niveau de prix (pas de cotation)
  const fmtChg = (c) => (c > 0 ? "+" : "") + nf(c) + " %";
  const fmtVs = (v, ref) => { const d = (v / ref - 1) * 100; return (d > 0 ? "+" : "") + nf(d, 1) + " %"; };
  const fmtEur = (n) => (n >= 1e6 ? nf(n / 1e6, 1) + " M€" : n >= 1e3 ? Math.round(n / 1e3) + " k€" : Math.round(n) + " €");
  const pct = (p) => Math.round(p * 100) + " %";
  const sq = (dir) => `<i class="sq ${dir === "up" ? "up" : "down"}" aria-hidden="true"></i>`;
  const dot = (state) => `<i class="dot ${state}" aria-hidden="true"></i>`;
  const sLabel = (s) => (s.dir === "up" ? "S haussière" : "S baissière");
  const sWhat = (s) => `bougie sans corps après ${s.n} bougie${s.n > 1 ? "s" : ""} ${s.dir === "up" ? "baissière" : "verte"}${s.n > 1 ? "s" : ""}`;
  const isHeld = (t) => pos.some((p) => p.t === t);

  function fmtSince(iso, tfKey) {
    const d = new Date(iso);
    const o = { timeZone: "Europe/Paris" };
    if (tfKey === "1h" || tfKey === "4h") return d.toLocaleString("fr-FR", { ...o, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).replace(",", "");
    if (tfKey === "1d") return d.toLocaleDateString("fr-FR", { ...o, weekday: "short", day: "2-digit", month: "2-digit" });
    if (tfKey === "1w") return "semaine du " + d.toLocaleDateString("fr-FR", { ...o, day: "2-digit", month: "2-digit" });
    return d.toLocaleDateString("fr-FR", { ...o, month: "long", year: "numeric" });
  }

  // ===================== Positions : stockage, format GitHub « TICKER:CONFIG:UT:PRIX:HORODATAGE » =====================
  const posKey = (p) => `${p.t}|${p.c || "O"}|${p.tf || ""}`;
  function serialize(p) { return [p.t, p.c || "O", p.tf || "", p.e == null ? "" : p.e, Math.round((p.ts || Date.now()) / 1000)].join(":"); }
  function parsePos(raw) {
    const out = [];
    for (const part of (raw || "").replace(/[;\n]/g, ",").split(",")) {
      const f = part.trim().split(":");
      const t0 = (f[0] || "").toUpperCase();
      if (!/^[A-Z0-9]+(\.[A-Z]+)?$/.test(t0)) continue;
      const c = f[1] === "S" || f[1] === "Fa" ? f[1] : "O";
      const k = TF_KEYS.includes(f[2]) ? f[2] : null;
      const e = f[3] !== undefined && f[3] !== "" && isFinite(+f[3]) ? +f[3] : null;
      const ts = f[4] && isFinite(+f[4]) ? +f[4] * 1000 : Date.now();
      out.push({ t: t0.includes(".") ? t0 : t0 + ".PA", c, tf: k, e, ts });
    }
    return out;
  }
  function loadPos() {
    try { const raw = store("pos"); if (raw) return JSON.parse(raw).map((p) => ({ ...p, c: p.c === "S" || p.c === "Fa" ? p.c : "O" })); } catch (e) { /* rien */ }
    return parsePos(store("held") || "");                    // ancienne version : simples tickers
  }
  function savePos() { store("pos", JSON.stringify(pos)); }

  // ===================== Stops (SL) et objectifs (TP) d'une position =====================
  // S : SL = le plus BAS de deux valeurs : plus bas (mèche ou corps) de la bougie HA précédente, ou prix d'entrée − 1,5 × ATR (stop large :
  //     l'étude du profit montre que les stops serrés sont touchés par le bruit). Fa : pas de SL, sortie par un TP ou par un Fo dans la même UT.
  // S devenue Fa dans la même UT (Fa apparue après l'entrée) : plus de SL, sorties de la Fa.
  // TP : MM7 (HA), MM20/50/100/200 (classiques) de la même UT, ou « Fo » des UT supérieures (jusqu'à 3 mois) : Tenkan, MM7 si baissière.
  function plan(p, s) {
    if (p.c === "O" || !p.c || !p.tf) return { other: true };
    const r = s.tf[p.tf];
    if (!r || !r.lv) return { pending: true };
    const price = s.price;
    let mode = p.c, converted = false;
    if (p.c === "S" && r.state === "fa" && new Date(r.since).getTime() + BAR_MS[p.tf] > p.ts) { mode = "Fa"; converted = true; }
    let sl = null;
    if (mode === "S") {
      const a = { v: r.lv.low_prev, label: `Plus bas (mèche ou corps) de la bougie précédente, ${TF_LAB[p.tf]}` };
      const ref = p.e != null ? p.e : price;
      const b = r.lv.atr ? { v: ref - 1.5 * r.lv.atr, label: `1,5 × amplitude moyenne (ATR) sous le prix ${p.e != null ? "d'entrée" : "actuel"}, ${TF_LAB[p.tf]}` } : null;
      const best = b && b.v < a.v ? b : a, other = best === a ? b : a;
      sl = { v: best.v, label: best.label, other: other || null };
    }
    const cands = [];
    for (const k of ["7", "20", "50", "100", "200"]) {
      const v = r.lv.ma[k];
      if (v != null) cands.push({ v, label: `MM${k} ${TF_LAB[p.tf]}`, kind: "MM" });
    }
    // Toutes les UT supérieures jusqu'au trimestre : Tenkan, et MM7 (HA) si elle est baissière
    const order = ["1h", "4h", "1d", "1w", "1mo", "3mo"];
    for (const hi of order.slice(order.indexOf(p.tf) + 1)) {
      const lv = hi === "3mo" ? s.lv3m : s.tf[hi] && s.tf[hi].lv;
      if (!lv) continue;
      const lab = hi === "3mo" ? "3 mois" : TF_LAB[hi];
      cands.push({ v: lv.tk, label: `Fo ${lab} : Tenkan ${lab}`, kind: "Fo" });
      if (lv.mm7_dn && lv.ma["7"] != null) cands.push({ v: lv.ma["7"], label: `Fo ${lab} : MM7 baissière ${lab}`, kind: "Fo" });
    }
    cands.sort((x, y) => x.v - y.v);
    const tps = cands.filter((c) => c.v > price).slice(0, 5);
    const passed = p.e != null ? cands.filter((c) => c.v > p.e && c.v <= price) : [];
    const reasons = [];
    if (sl && price <= sl.v) reasons.push("Stop loss atteint : le cours est sous le SL");
    if (mode === "Fa" && r.state === "fo") reasons.push(`Fo en ${TF_LAB[p.tf]} : la Fa est terminée`);
    if (passed.length) reasons.push("TP franchi : " + passed.map((c) => c.label).join(", "));
    return { mode, converted, sl, tps, passed, reasons, price };
  }
  const modeLabel = (p, pl) => (p.c === "S" || p.c === "Fa" ? (pl && pl.converted ? "S → Fa" : p.c) + " · " + TF_LAB[p.tf] : "Autre");
const GROUPS = ["1h · 4h · 1j", "1 semaine", "1 mois", "Autres entrées"];
const groupOf = (p) => (p.c !== "S" && p.c !== "Fa" ? 3 : p.tf === "1w" ? 1 : p.tf === "1mo" ? 2 : 0);

  // ===================== Scanner : « en cours de validation », classé par volume d'achat du secteur =====================
  const visibleTf = () => (data && data.meta.visible_tf) || ["4h", "1d", "1w", "1mo"];
  const thr = () => (data.meta.prob_min ?? 0.3);
  // Dernières bougies HA clôturées (🔴 rouge / 🟢 verte / ⚪ sans corps), pour contrôler d'un coup d'œil la série qui précède un S
  function histRow(h, want) {
    if (!h) return "";
    return `<div class="since">Bougies HA précédentes (la plus récente à droite) : ${[...h].map((x) => (x === "r" ? "🔴" : x === "n" ? "⚪" : "🟢")).join("")}</div>`;
  }
  function pend(r) {
    if (!r) return {};
    const ok = (p) => (p != null && p >= thr() ? p : null);
    if (r.forming) return { fa: r.state !== "fa" ? ok(r.p_fa) : null, su: ok(r.p_s_up) };
    if (r.closed) return { fa: ok(r.closed.p_fa), su: ok(r.closed.p_s_up), closed: true };   // marché fermé : dernière analyse conservée
    return {};
  }
  // Filtre par secteur / thème (OR) : mémorisé sur ce téléphone
  let secSel = [];
  try { secSel = JSON.parse(store("secsel") || "[]"); } catch (e) { secSel = []; }
  const tagsOf = (s) => [s.sector || "Autres", ...(s.themes || [])];
  const passFilter = (s) => !secSel.length || tagsOf(s).some((x) => secSel.includes(x));
  function rowsFor(tfKey, bucket, filtered = true) {
    const out = [];
    for (const [t, s] of Object.entries(data.stocks)) {
      const r = s.tf[tfKey];
      const p = pend(r)[bucket === "fa" ? "fa" : "su"];
      if (p != null && (!filtered || passFilter(s))) out.push({ t, s, r, p, sec: s.sector || "Autres", buy: r.buy || 0 });
    }
    return out.sort((a, b) => b.p - a.p || b.buy - a.buy || (a.t < b.t ? -1 : 1));      // pourcentage de réussite décroissant
  }
  let secOpen = false;
  function renderFilter() {
    const box = $("secFilter");
    if (view !== "scan" || !data || query.trim()) { box.hidden = true; return; }
    const all = rowsFor(tf, st, false);
    const secs = {}, ths = {};
    for (const { s } of all) { secs[s.sector || "Autres"] = (secs[s.sector || "Autres"] || 0) + 1; for (const th of s.themes || []) ths[th] = (ths[th] || 0) + 1; }
    secSel = secSel.filter((x) => secs[x] || ths[x]);
    box.hidden = !all.length;
    const sortd = (o) => Object.entries(o).sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0]));
    const opt = ([k, n]) => `<label class="opt"><input type="checkbox" data-sec="${esc(k)}"${secSel.includes(k) ? " checked" : ""}><span>${esc(k)}</span><span class="muted">${n}</span></label>`;
    const sum = secSel.length ? (secSel.length === 1 ? secSel[0] : secSel.length + " sélectionnés") : "Tous";
    box.innerHTML = `<details class="secdrop"${secOpen ? " open" : ""}><summary>Secteur / thème : <b>${esc(sum)}</b></summary>
      <div class="secpanel">${secSel.length ? '<button class="fchip" data-clear="1">Tout afficher</button>' : ""}
      <div class="optgrp">Secteurs</div>${sortd(secs).map(opt).join("")}
      ${Object.keys(ths).length ? '<div class="optgrp">Thèmes</div>' + sortd(ths).map(opt).join("") : ""}</div></details>`;
  }

  function renderViews() {
    viewTabs.innerHTML = `<button role="tab" data-view="scan" aria-selected="${view === "scan"}">Scanner</button>`
      + `<button role="tab" data-view="own" aria-selected="${view === "own"}">Mon PF<span class="count">${pos.length}</span></button>`;
    for (const id of ["tfTabs", "stTabs"]) $(id).hidden = view !== "scan";
    $("search").placeholder = view === "own" ? "Rechercher dans Mon PF…" : "Rechercher une action…";
  }
  function renderTabs() {
    const tl = TFS.filter(([k]) => visibleTf().includes(k));
    if (!tl.some(([k]) => k === tf)) tf = tl.some(([k]) => k === "1d") ? "1d" : tl[0][0];
    tfTabs.innerHTML = tl.map(([k, l]) => `<button role="tab" data-tf="${k}" aria-selected="${k === tf}">${l}</button>`).join("");
    stTabs.innerHTML = STATES.map(([k, l]) => {
      const n = data ? rowsFor(tf, k).length : 0;
      return `<button role="tab" data-st="${k}" aria-selected="${k === st}">${l}<span class="count">${n}</span></button>`;
    }).join("");
  }

  const pb = (html, p, cls) => `<span class="pb ${cls}">${html}${p != null ? " " + pct(p) : ""}</span>`;
  function otherChips(s) {
    return TFS.filter(([k]) => k !== tf && visibleTf().includes(k)).map(([k, l]) => {
      const p = pend(s.tf[k])[st === "fa" ? "fa" : "su"];
      return p == null ? "" : `<span class="chip"><b>${l}</b>${st === "fa" ? dot("fa") : sq("up")}<span>${pct(p)}</span></span>`;
    }).join("");
  }
  // Marché fermé : heure de clôture de la bougie + résultat réel
  function closedLine(r) {
    if (r.forming || !r.closed) return "";
    const c = r.closed, d = c.end ? new Date(c.end) : null;
    const when = d ? d.toLocaleString("fr-FR", { timeZone: "Europe/Paris", weekday: "short", hour: "2-digit", minute: "2-digit" }) : "";
    const hit = st === "fa" ? c.hit_fa : c.hit_s;
    return `<div class="closedline"><span class="closedtag">Marché fermé</span><span class="muted">bougie clôturée ${esc(when)}</span><b class="${hit ? "up" : "muted"}">${hit ? (st === "fa" ? "✓ Fa validée" : "✓ S validée") : "non validée"}</b></div>`;
  }
  function card(row) {
    const { t, s, r, p } = row;
    const over = st === "fa" && r.over && r.over.fa;
    const others = otherChips(s);
    return `<button class="card ${st === "s" ? "sup" : "pending"}${isHeld(t) ? " held" : ""}" data-t="${esc(t)}">
      <div class="row"><span class="left">${isHeld(t) ? '<span class="star" aria-label="En position">⭐</span>' : ""}<span class="tick">${esc(short(t))}</span><span class="name">${esc(s.name)}</span></span>
      <span class="price">${fmtPrice(s.price)}<span class="chg ${s.chg >= 0 ? "up" : "down"}">${fmtChg(s.chg)}</span></span></div>
      <div class="secline"><span class="secbadge">${esc(row.sec)}</span>${(s.themes || []).map((x) => `<span class="tag">${esc(x)}</span>`).join("")}</div>
      <div class="pline"><span class="pbig">${st === "fa" ? dot("fa") : sq("up")}<b>${pct(p)}</b></span>
        <span class="pbar"><i class="${p >= (data.meta.prob_high ?? 0.6) ? "fa" : "mid"}" style="width:${Math.max(p * 100, 2)}%"></i></span>
        ${over ? '<span class="over">déjà au-dessus</span>' : ""}</div>
      ${closedLine(r)}
      ${others ? `<div class="chips">${others}</div>` : ""}</button>`;
  }
  const LEGEND = `<div class="legend">${dot("fa")} Fa en cours ${sq("up")} S haussière en cours<span class="muted" style="width:100%">Classées par pourcentage décroissant : chance que la bougie en formation valide la configuration. Marché fermé : la dernière analyse reste affichée avec le résultat de la clôture. Menu « Secteur / thème » pour filtrer.</span></div>`;

  function renderList() {
    if (!data || !Object.keys(data.stocks).length) {
      list.innerHTML = `<div class="empty">En attente de la première analyse.<br>Lancez le workflow « Scan Fa/Fo » sur GitHub.</div>`;
      return;
    }
    if (view === "own") { renderOwned(); return; }
    const q = query.trim().toLowerCase();
    if (q) { renderSearch(q); return; }
    const rows = rowsFor(tf, st);
    if (!rows.length) {
      const msg = { fa: "Aucune Fa en cours de validation", s: "Aucune S haussière en cours de validation" }[st];
      list.innerHTML = `<div class="empty">${msg} en ${esc(TF_NAME[tf])}.</div>`;
      return;
    }
    list.innerHTML = LEGEND + rows.map(card).join("");
  }

  // ---- Recherche : retrouver n'importe quelle action suivie, ou en ajouter une absente de la liste ----
  function candidateTicker(q) {
    const t = q.trim().toUpperCase().replace(/\s+/g, "");
    if (!/^[A-Z0-9][A-Z0-9\-]{0,9}(\.[A-Z]{1,3})?$/.test(t)) return null;
    if (t.includes(".") && !t.endsWith(".PA")) return null;   // le scanner suit uniquement Euronext Paris (horaires 9h-17h35)
    return t.includes(".") ? t : t + ".PA";
  }
  function renderSearch(q) {
    const hits = Object.entries(data.stocks)
      .filter(([t, s]) => t.toLowerCase().includes(q) || s.name.toLowerCase().includes(q))
      .sort((a, b) => isHeld(b[0]) - isHeld(a[0]) || a[0].localeCompare(b[0])).slice(0, 30);
    let html = hits.map(([t, s]) => `<button class="card${isHeld(t) ? " held" : ""}" data-t="${esc(t)}">
      <div class="row"><span class="left">${isHeld(t) ? '<span class="star">⭐</span>' : ""}<span class="tick">${esc(short(t))}</span><span class="name">${esc(s.name)}</span></span>
      <span class="price">${fmtPrice(s.price)}<span class="chg ${s.chg >= 0 ? "up" : "down"}">${fmtChg(s.chg)}</span></span></div></button>`).join("");
    const cand = candidateTicker(query);
    if (cand && !data.stocks[cand]) {
      html += `<div class="card addcard"><div><b>${esc(cand)}</b> n'est pas dans la liste suivie.</div>
        <button class="posbtn" data-add="${esc(cand)}">☆ J'entre sur cette action (l'ajouter aux possédées)</button>
        <div class="muted small">Code Yahoo Finance d'une action de Paris (ex. ALCLS.PA). Sans suffixe, « .PA » est ajouté. Analyse dès le prochain passage, comme les autres actions.</div></div>`;
    }
    list.innerHTML = html || `<div class="empty">Aucune action ne correspond à « ${esc(query.trim())} ».</div>`;
  }

  // ===================== Mon PF =====================
  function stateChips(s) {
    return `<div class="chips">` + TFS.map(([k, l]) => {
      const r = s.tf[k];
      if (!r) return "";
      return `<span class="chip"><b>${l}</b>${dot(r.state)}${r.s && r.s.active ? sq(r.s.dir) : ""}</span>`;
    }).join("") + `</div>`;
  }
  function renderOwned() {
    if (!pos.length) {
      list.innerHTML = `<div class="empty">Aucune action dans Mon PF.<br>Dans le Scanner, touchez une action puis choisissez S, Fa ou Autre.</div>`;
      return;
    }
    const q = query.trim().toLowerCase();
    const shown = q ? pos.filter((p) => p.t.toLowerCase().includes(q) || ((data.stocks[p.t] || {}).name || "").toLowerCase().includes(q)) : pos;
    if (!shown.length) { list.innerHTML = `<div class="empty">Aucune action de Mon PF ne correspond à « ${esc(query.trim())} ».</div>`; return; }
    const items = shown.map((p) => {
      const s = data.stocks[p.t];
      const pl = s ? plan(p, s) : null;
      const dist = pl && pl.sl ? (s.price - pl.sl.v) / s.price : Infinity;          // écart au SL : les plus proches d'abord
      return { p, s, pl, dist, g: groupOf(p) };
    }).sort((a, b) => a.g - b.g || a.dist - b.dist || b.p.ts - a.p.ts);
    let html = "", cur = -1;
    for (const { p, s, pl, g } of items) {
      if (g !== cur) { cur = g; html += `<h3 class="grp">${GROUPS[g]}</h3>`; }
      if (!s) { html += `<div class="card held nodata"><span class="tick">${esc(short(p.t))}</span><span class="muted"> · ${esc(modeLabel(p))} · analyse au prochain passage (15 min au plus)</span></div>`; continue; }
      let body = "";
      if (pl.other) body = stateChips(s);
      else if (pl.pending) body = `<div class="muted small">Niveaux SL / TP calculés au prochain passage (15 min au plus).</div>`;
      else {
        body += pl.sl ? `<div class="lvl"><b>SL</b> ${fmtPrice(pl.sl.v)} <span class="muted">${fmtVs(pl.sl.v, s.price)}</span></div>` : `<div class="lvl muted"><b>SL</b> aucun (Fa)</div>`;
        body += pl.tps.length ? `<div class="lvl"><b>TP</b> ${esc(pl.tps[0].label)} ${fmtPrice(pl.tps[0].v)} <span class="muted">${fmtVs(pl.tps[0].v, s.price)}</span></div>` : "";
        if (pl.reasons.length) body += `<div class="banner">Sortie signalée : ${esc(pl.reasons.join(" · "))}</div>`;
      }
      const pnl = p.e != null ? ` · entrée ${fmtPrice(p.e)} <span class="${s.price >= p.e ? "up" : "down"}">${fmtVs(s.price, p.e)}</span>` : "";
      html += `<button class="card held pos" data-pos="${esc(posKey(p))}">
        <div class="row"><span class="left"><span class="star">⭐</span><span class="tick">${esc(short(p.t))}</span><span class="name">${esc(s.name)}</span></span>
        <span class="price">${fmtPrice(s.price)}<span class="chg ${s.chg >= 0 ? "up" : "down"}">${fmtChg(s.chg)}</span></span></div>
        <div class="secline"><span class="secbadge modebadge">${esc(modeLabel(p, pl))}</span><span class="muted">${esc(s.sector || "")}${pnl}</span></div>${(s.themes || []).length ? `<div class="secline">${s.themes.map((x) => `<span class="tag">${esc(x)}</span>`).join("")}</div>` : ""}
        ${body}</button>`;
    }
    list.innerHTML = html;
  }

  function renderHead() {
    if (!data || !data.meta.updated) { $("updated").textContent = "Aucune donnée"; return; }
    const d = new Date(data.meta.updated);
    const hm = d.toLocaleTimeString("fr-FR", { timeZone: "Europe/Paris", hour: "2-digit", minute: "2-digit" });
    const day = d.toLocaleDateString("fr-FR", { timeZone: "Europe/Paris", day: "2-digit", month: "2-digit" });
    const ageMin = Math.round((Date.now() - d.getTime()) / 60000);
    $("updated").textContent = `Analyse du ${day} à ${hm}` + (ageMin > 90 ? " (marché fermé ou données anciennes)" : "");
    const m = data.meta;
    $("foot").innerHTML = `${m.analysed} actions analysées sur ${m.universe} (liquidité minimale requise). Cours différés d'environ ${m.delay_min} min.<br>`
      + `Fa en cours : la bougie Heikin Ashi en formation peut encore clôturer avec le haut du corps au-dessus de la Tenkan et d'une MM7 ascendante ; ${pct(m.prob_min ?? 0.3)} minimum, hors actions déjà en Fa. `
      + `${sq("up")} S haussière en cours : elle peut clôturer sans corps après au moins 3 bougies baissières (aucune verte), les 2 juste avant franches (vrai corps, peu de mèche haute), dont au moins 2 sans mèche haute dans la série. `
      + `Le pourcentage est une estimation statistique, pas une prévision. Les stops et objectifs sont des repères calculés d'après vos règles, pas des ordres. Information indicative, pas un conseil en investissement.`;
  }

  function render() { renderViews(); renderTabs(); renderFilter(); renderList(); }

  // ===================== Fiches =====================
  const probRow = (marker, label, p, cls) => {
    const v = p > 0 && Math.round(p * 100) === 0 ? "< 1 %" : pct(p);
    return `<div class="prob${p < 0.05 ? " low" : ""}"><span class="pl">${marker}${label}</span><span class="pbar"><i class="${cls}" style="width:${Math.max(p * 100, p > 0 ? 2 : 0)}%"></i></span><span class="pv">${v}</span></div>`;
  };
  function tfRows(s, held) {
    return TFS.filter(([k]) => held || visibleTf().includes(k)).map(([k, l]) => {
      const r = s.tf[k];
      if (!r) return `<div class="tf-row"><div class="tf-head"><span class="lab">${l}</span><span class="muted">Pas assez d'historique</span></div></div>`;
      const hasS = !!(r.s && r.s.active);
      let pills = `<span class="pill ${r.state}">${dot(r.state)}${r.state === "fa" ? "Fa validé" : r.state === "fo" ? "Fo" : "Fa non validé"}</span>`;
      if (hasS) pills += `<span class="sqpill ${r.s.dir}">${sq(r.s.dir)}${sLabel(r.s)}</span>`;
      let info = "";
      if (r.state !== "none") info += `<div class="since">${r.state === "fa" ? "Fa" : "Fo"} depuis ${esc(fmtSince(r.since, k))} · ${r.bars} bougie${r.bars > 1 ? "s" : ""}</div>`;
      if (hasS) info += `<div class="since">${sLabel(r.s)} : ${sWhat(r.s)}</div>`;
      let probs;
      if (r.forming && r.p_fa != null) {
        probs = `<div class="probs"><div class="ptitle">À la clôture de la bougie en cours</div>`
          + (held
            ? (r.state === "fa"
              ? probRow(dot("fa"), "Fa maintenu", Math.max(0, 1 - r.p_fo), "fa") + probRow(dot("fo"), "Invalidation Fo", r.p_fo, "fo")
              : r.state === "fo"
                ? probRow(dot("fo"), "Fo maintenu", Math.max(0, 1 - r.p_fa), "fo") + probRow(dot("fa"), "Validation Fa", r.p_fa, "fa")
                : probRow(dot("fa"), "Validation Fa", r.p_fa, "fa") + probRow(dot("fo"), "Validation Fo", r.p_fo, "fo"))
            : (r.state === "fa" ? `<div class="since">Déjà en Fa.</div>` : probRow(dot("fa"), "Validation Fa", r.p_fa, "fa")))
          + (r.p_s_up > 0 ? probRow(sq("up"), "S haussière", r.p_s_up, "fa") + histRow(r.hist, "r") : "")
          + (held && r.p_s_down > 0 ? probRow(sq("down"), "S baissière", r.p_s_down, "fo") + histRow(r.hist, "v") : "") + `</div>`;
      } else {
        probs = `<div class="probs"><div class="ptitle muted">${r.forming ? "Probabilités indisponibles" : "Marché fermé : la prochaine bougie s'ouvre à la reprise"}</div></div>`;
      }
      return `<div class="tf-row"><div class="tf-head"><span class="lab">${l}</span><span class="what">${pills}</span></div>${info}${probs}</div>`;
    }).join("");
  }

  // Boutons graphique. TradingView « application » : page de l'action (s'ouvre dans l'application installée, sur la bonne action) ;
  // « graphique » : lien web avec l'unité de temps. Investing.com : page de l'action (voir docs/investing.json).
  function chartLinks(t, name, k) {
    const sym = short(t);
    const tvApp = `https://www.tradingview.com/symbols/EURONEXT-${encodeURIComponent(sym)}/`;
    const tvChart = "https://www.tradingview.com/chart/?symbol=" + encodeURIComponent("EURONEXT:" + sym) + "&interval=" + TV_INT[k];
    const slug = investing[t];
    const inv = slug ? `https://www.investing.com/equities/${slug}-chart` : "https://www.investing.com/search/?q=" + encodeURIComponent(name);
    return `<a class="link" href="${tvApp}" target="_blank" rel="noopener">TradingView : ouvrir ${esc(sym)} (application)</a>
      <a class="link alt" href="${tvChart}" target="_blank" rel="noopener">TradingView : graphique ${esc(TF_NAME[k])} (web)</a>
      <button class="link alt" data-copy="EURONEXT:${esc(sym)}" style="width:100%;border:1px solid var(--line);cursor:pointer">Copier le symbole EURONEXT:${esc(sym)}</button>
      <a class="link alt" href="${inv}" target="_blank" rel="noopener">${slug ? "Page Investing.com" : "Chercher sur Investing.com"} (régler ${esc(TF_NAME[k])} sur place)</a>`;
  }
  const sheetHead = (title, sub) => `<div class="sheet-head"><div><h2 id="sheetTitle">${title}</h2><div class="sub">${sub}</div></div><button class="close" id="closeSheet" aria-label="Fermer">✕</button></div>`;

  // ---- Fiche d'une action du Scanner : état des lieux + choix de l'entrée ----
  function openSheet(t) {
    const s = data && data.stocks[t];
    if (!s) { if (isHeld(t)) return; openUnknown(t); return; }
    if (!draft || draft.t !== t) draft = { t, c: null, tf: null };
    sheet.innerHTML = sheetHead(`${isHeld(t) ? "⭐ " : ""}${esc(short(t))}`, `${esc(s.name)} · ${fmtPrice(s.price)} · ${fmtChg(s.chg)}${s.sector ? " · " + esc(s.sector) : ""}`)
      + ((s.themes || []).length ? `<div class="secline">${s.themes.map((x) => `<span class="tag">${esc(x)}</span>`).join("")}</div>` : "")
      + `<div id="entryBox" class="entry"></div>`
      + `<div class="tf-table">${tfRows(s, isHeld(t))}</div>` + chartLinks(t, s.name, tf)
      + `<p class="note">Fa = démarre quand le haut du corps Heikin Ashi clôturé dépasse la Tenkan et une MM7 ascendante, dure jusqu'au Fo · Fo = le bas du corps passe sous la Tenkan et une MM7 descendante · S = bougie sans corps après des bougies de même sens (${sq("up")} haussière, ${sq("down")} baissière). Les pourcentages sont des estimations statistiques (simulation sur le temps restant de la bougie), pas des prévisions.</p>`;
    renderEntry();
    showSheet();
    if (!location.hash.slice(1).toUpperCase().startsWith(t)) history.replaceState(null, "", "#" + t);
  }
  function openUnknown(t) {
    if (!draft || draft.t !== t) draft = { t, c: null, tf: null };
    sheet.innerHTML = sheetHead(esc(short(t)), "Action absente de la liste suivie : analysée dès le prochain passage")
      + `<div id="entryBox" class="entry"></div>`;
    renderEntry(); showSheet();
  }
  function renderEntry() {
    const box = $("entryBox"); if (!box || !draft) return;
    const ch = (attr, val, html, on) => `<button class="chbtn${on ? " on" : ""}" ${attr}="${val}">${html}</button>`;
    let h = `<h3>J'entre sur cette action</h3><div class="choices three">`
      + ch("data-c", "S", `${sq("up")} S haussière`, draft.c === "S") + ch("data-c", "Fa", `${dot("fa")} Fa`, draft.c === "Fa") + ch("data-c", "O", "Autre", draft.c === "O") + `</div>`;
    if (draft.c === "O") h += `<p class="note">« Autre » : simple suivi, sans SL ni TP. Vous voyez les configurations sur chaque unité de temps.</p>`;
    if (draft.c && draft.c !== "O") {
      h += `<h3>Unité de temps de l'entrée</h3><div class="choices five">`
        + TFS.map(([k, l]) => ch("data-etf", k, l, draft.tf === k)).join("") + `</div>`;
    }
    if (draft.c === "O" || (draft.c && draft.tf)) {
      h += `<label class="field" for="entryPx">Prix d'entrée (facultatif)</label><input class="text" id="entryPx" inputmode="decimal" autocomplete="off" placeholder="ex. 36,50">`
        + `<button class="link" id="entryOk" style="width:100%;border:0;cursor:pointer;margin-top:12px">Ajouter à Mon PF · ${draft.c === "O" ? "Autre" : draft.c + " " + TF_LAB[draft.tf]}</button>`;
    }
    box.innerHTML = h;
  }

  // ---- Fiche d'une position : SL, 5 TP, règles de sortie ----
  function openPosSheet(key) {
    const p = pos.find((x) => posKey(x) === key);
    if (!p) return;
    const s = data && data.stocks[p.t];
    if (!s) { sheet.innerHTML = sheetHead("⭐ " + esc(short(p.t)), "Analyse au prochain passage") + posActions(key); showSheet(); return; }
    const pl = plan(p, s);
    let h = sheetHead(`⭐ ${esc(short(p.t))}`, `${esc(s.name)} · ${fmtPrice(s.price)} · ${fmtChg(s.chg)}${s.sector ? " · " + esc(s.sector) : ""}`);
    h += `<div class="plan"><div class="planhead"><span class="secbadge modebadge">${esc(modeLabel(p, pl.other || pl.pending ? null : pl))}</span>`
      + `<span class="muted">${p.e != null ? `entrée ${fmtPrice(p.e)} · <span class="${s.price >= p.e ? "up" : "down"}">${fmtVs(s.price, p.e)}</span>` : "prix d'entrée non renseigné"}</span></div>`;
    if (pl.other) h += `<p class="note">Entrée « Autre » : pas de SL ni de TP. Voici les configurations sur chaque unité de temps.</p>`;
    else if (pl.pending) h += `<p class="note">Les niveaux SL / TP seront calculés au prochain passage (15 minutes au plus).</p>`;
    else {
      if (pl.reasons.length) h += `<div class="banner">Sortie signalée : ${esc(pl.reasons.join(" · "))}</div>`;
      if (pl.sl) {
        const help = (pl.converted ? "" : "")
          + (pl.sl.other ? `Autre valeur examinée : ${fmtPrice(pl.sl.other.v)} (${pl.sl.other.label}), plus basse, donc non retenue.`
            : `Pas de 2e valeur : aucune Fa en cours dans l'unité de temps inférieure${lowerTf(p.tf) ? "" : " (pas d'unité inférieure)"}.`)
          + " Le SL est un stop en séance, volontairement large : le plus bas de deux valeurs (plus bas de la bougie précédente, mèche ou corps ; prix d'entrée moins 1,5 fois l'amplitude moyenne). Un stop trop serré est presque toujours touché par le simple bruit du marché.";
        h += `<h3>Stop loss (en séance) ${helpBtn(help)}</h3><div class="lvrow"><span class="lv-v">${fmtPrice(pl.sl.v)}</span><span class="muted">${fmtVs(pl.sl.v, s.price)}</span></div>`
          + `<div class="since">${esc(pl.sl.label)}</div>`;
      } else {
        const help = (pl.converted ? `La S est devenue une Fa en ${TF_LAB[p.tf]} : le stop loss de la S est retiré, les sorties sont celles de la Fa en cours. ` : "")
          + `Pas de SL pour une Fa : sortie par un TP, ou quand l'action bascule en Fo en ${TF_LAB[p.tf]}.`;
        h += `<h3>Stop loss ${helpBtn(help)}</h3><div class="since">Aucun stop loss.</div>`
          + `<div class="since">État ${TF_LAB[p.tf]} : <span class="pill ${s.tf[p.tf].state}">${dot(s.tf[p.tf].state)}${s.tf[p.tf].state === "fa" ? "Fa validé" : s.tf[p.tf].state === "fo" ? "Fo" : "Fa non validé"}</span></div>`;
      }
      h += `<h3>Take profit (du plus petit au plus grand)</h3>`;
      if (pl.passed.length) h += `<div class="since muted">Déjà franchis depuis l'entrée : ${esc(pl.passed.map((c) => c.label + " " + fmtPrice(c.v)).join(", "))}</div>`;
      h += pl.tps.length
        ? pl.tps.map((c, i) => `<div class="lvrow"><span class="tpn">TP${i + 1}</span><span class="lv-v">${fmtPrice(c.v)}</span><span class="tptype ${c.kind === "Fo" ? "fo" : ""}">${esc(c.label)}</span><span class="muted">${fmtVs(c.v, s.price)}</span></div>`).join("")
        : `<div class="since">Aucun niveau au-dessus du cours actuel.</div>`;
    }
    h += `</div>` + posActions(key) + `<div class="tf-table">${tfRows(s, true)}</div>` + chartLinks(p.t, s.name, p.tf || tf);
    sheet.innerHTML = h; showSheet();
  }
  const helpBtn = (txt) => `<span class="helpwrap"><button class="qmark" data-help type="button" aria-label="Explication">?</button><span class="bubble" role="note">${esc(txt)}</span></span>`;
  const posActions = (key) => `<div class="posacts"><button class="posbtn" data-edit="${esc(key)}">Modifier le prix d'entrée</button><button class="posbtn on" data-sell="${esc(key)}">J'ai vendu : retirer de Mon PF</button></div>`;

  function showSheet() { sheet.hidden = false; backdrop.hidden = false; const c = $("closeSheet"); if (c) c.focus(); }
  function closeSheet() {
    sheet.hidden = true; backdrop.hidden = true;
    if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  }
  function openFromHash() {
    const [t, k] = decodeURIComponent(location.hash.slice(1)).toUpperCase().split("@");      // #AIR.PA@4H : ouvre la fiche sur l'unité de la notification
    const k2 = (k || "").toLowerCase();
    if (TF_KEYS.includes(k2) && visibleTf().includes(k2) && k2 !== tf) { tf = k2; render(); }
    if (t && data && data.stocks[t]) { const p = pos.find((x) => x.t === t); if (p) { view = "own"; render(); openPosSheet(posKey(p)); } else openSheet(t); }
  }

  // ===================== Synchronisation GitHub (variable privée HOLDINGS) =====================
  let toastTimer;
  function toast(msg) {
    const el = $("toast"); el.textContent = msg; el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 6000);
  }
  async function gh(method, path, body) {
    return fetch(`https://api.github.com/repos/${repo}/actions/variables${path}`, {
      method,
      headers: Object.assign({ Authorization: "Bearer " + token, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" },
        body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined,
    });
  }
  async function ghError(r, what) {
    let msg = "";
    try { msg = (await r.json()).message || ""; } catch (e) { /* rien */ }
    const hint = r.status === 401 ? " : jeton invalide ou expiré"
      : r.status === 403 ? " : le jeton n'a pas la permission « Variables » (lecture et écriture) sur CE dépôt"
      : r.status === 404 ? " : dépôt introuvable (vérifiez pseudo/nom) ou jeton sans accès à ce dépôt" : "";
    return new Error(`GitHub ${r.status} en ${what}${hint}${msg ? " — « " + msg + " »" : ""}`);
  }
  async function readRemote() {
    const r = await gh("GET", "/" + GH_VAR);
    if (r.status === 404) return null;
    if (!r.ok) throw await ghError(r, "lecture");
    return parsePos((await r.json()).value);
  }
  async function writeRemote(list_) {
    const value = list_.length ? list_.map(serialize).join(",") : "-";          // GitHub refuse une valeur vide
    let r = await gh("PATCH", "/" + GH_VAR, { name: GH_VAR, value });
    if (r.status === 404) r = await gh("POST", "", { name: GH_VAR, value });
    if (!r.ok) throw await ghError(r, "écriture");
  }
  const canSync = () => !!(token && repo);

  async function changePositions(mutate, okMsg) {
    mutate(pos); savePos(); render();
    if (!canSync()) { toast("Enregistré sur ce téléphone. Sans jeton GitHub (réglages ⚙), pas de notifications spéciales."); return; }
    try {
      const remote = (await readRemote()) || [];
      mutate(remote);
      await writeRemote(remote);
      pos = remote; savePos(); render();
      toast(okMsg);
    } catch (e) {
      toast("Synchronisation impossible : " + e.message + ". Gardé sur ce téléphone seulement.");
    }
  }
  const upsert = (np) => (arr) => { const i = arr.findIndex((x) => posKey(x) === posKey(np)); if (i >= 0) arr[i] = np; else arr.push(np); };
  const removeKey = (key) => (arr) => { const i = arr.findIndex((x) => posKey(x) === key); if (i >= 0) arr.splice(i, 1); };

  async function syncFromRemote() {
    if (!canSync()) return;
    try {
      const remote = await readRemote();
      if (remote) { pos = remote; savePos(); render(); }
    } catch (e) { /* hors-ligne ou jeton invalide : on garde la liste locale */ }
  }

  // ===================== Réglages =====================
  function openSettings() {
    sheet.innerHTML = sheetHead("Réglages", "") + `
      <p class="note" style="margin-top:12px">Pour que vos positions déclenchent des notifications ⭐ prioritaires, l'application met à jour une variable <b>privée</b> de votre dépôt GitHub.
      Il faut pour cela un jeton à coller une fois (voir SETUP.md, étape 7). Il reste sur ce téléphone.</p>
      <label class="field" for="repoIn">Dépôt (pseudo/nom)</label><input class="text" id="repoIn" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="pseudo/fa-fo-scanner">
      <label class="field" for="tokIn">Jeton GitHub</label><input class="text" id="tokIn" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="github_pat_…">
      <button class="link" id="saveSet" style="width:100%;border:0;cursor:pointer;margin-top:12px">Enregistrer et synchroniser</button>
      <button class="posbtn" id="clearTok" style="margin-top:10px">Effacer le jeton de ce téléphone</button>
      <button class="posbtn" id="statsBtn" style="margin-top:10px">Bilan des alertes (fiabilité réelle)</button>
      <p class="note" id="setStatus" role="status">${pos.length} position${pos.length > 1 ? "s" : ""} enregistrée${pos.length > 1 ? "s" : ""} sur ce téléphone.${canSync() ? " Synchronisation GitHub active." : ""}</p>`;
    $("repoIn").value = repo; $("tokIn").value = token;
    showSheet();
  }
  async function saveSettings() {
    const status = $("setStatus");
    repo = $("repoIn").value.trim().replace(/^https?:\/\/github\.com\//, "").replace(/\/$/, "");
    token = $("tokIn").value.trim();
    store("gh_repo", repo || null); store("gh_token", token || null);
    if (!canSync()) { status.textContent = "Renseignez le dépôt et le jeton."; return; }
    status.textContent = "Vérification…";
    try {
      const remote = (await readRemote()) || [];
      const merged = remote.slice();
      for (const p of pos) if (!merged.some((x) => posKey(x) === posKey(p))) merged.push(p);
      if (merged.length !== remote.length || remote.length === 0) await writeRemote(merged);   // crée la variable si besoin
      pos = merged; savePos(); render();
      status.textContent = `Connecté. ${pos.length} position${pos.length > 1 ? "s" : ""} synchronisée${pos.length > 1 ? "s" : ""}.`;
    } catch (e) {
      status.textContent = "Échec : " + e.message;
    }
  }

  // ===================== Bilan des alertes : pourcentage annoncé / résultat réel (stats.json, mis à jour par le scanner) =====================
  async function openStats() {
    sheet.innerHTML = sheetHead("Bilan des alertes", "Annoncé et réalisé, mis à jour automatiquement") + `<p class="note" id="statsBody">Chargement…</p>`;
    let st = null;
    try { const r = await fetch("stats.json?t=" + Date.now(), { cache: "no-store" }); if (r.ok) st = await r.json(); } catch (e) { /* rien */ }
    const body = $("statsBody"); if (!body) return;
    if (!st || !st.rows || !st.rows.length) { body.textContent = "Pas encore de données : le suivi démarre au prochain passage du scanner et se remplit chaque jour de bourse."; return; }
    const TFN = { "4h": "4h", "1d": "1j", "1w": "1sem", "1mo": "1mois" };
    const rows = st.rows.map((r) => {
      const a = r.all;
      const ok = r.resolved >= (st.min_cases || 20);
      return `<tr><td>${esc(r.label)} ${TFN[r.tf] || r.tf}<div class="muted small">${esc(r.verdict)}</div></td><td>${r.resolved}</td>`
        + `<td>${ok ? pct(a.p) : "–"}</td><td>${ok ? pct(a.h) : "–"}</td>`
        + `<td>${r.suggest != null ? "≥ " + Math.round(r.suggest * 100) + " %" : "–"}</td></tr>`;
    }).join("");
    body.outerHTML = `<p class="note">${st.resolved} configurations résolues sur ${st.logged} suivies, depuis ${st.days} jour${st.days > 1 ? "s" : ""}.
      « Annoncé » = pourcentage moyen au premier passage au-dessus du seuil de suivi (40-50 %) ; « Réalisé » = part réellement produite à la clôture de la bougie.
      « Seuil » = seuil conseillé, le plus bas seuil dont la fiabilité réelle atteint ${Math.round((st.target || 0.6) * 100)} %. En dessous de ${st.min_cases || 20} cas, pas de conclusion.</p>
      <div class="statwrap"><table class="stat"><thead><tr><th>Configuration</th><th>Cas</th><th>Annoncé</th><th>Réalisé</th><th>Seuil</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="note">Les stops loss ne sont pas suivis ici (ils concernent vos positions seulement).</p>`;
  }

  // ===================== Données =====================
  async function load(manual) {
    const btn = $("refresh");
    btn.classList.add("spin");
    try {
      const r = await fetch("data.json?t=" + Date.now(), { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      data = await r.json();
      renderHead(); render(); if (location.hash) openFromHash();
    } catch (e) {
      if (!data) { $("updated").textContent = "Données indisponibles"; list.innerHTML = `<div class="empty">Impossible de charger les données.<br>Vérifiez la connexion puis actualisez.</div>`; }
      else if (manual) $("updated").textContent = "Échec de l'actualisation";
    } finally { setTimeout(() => btn.classList.remove("spin"), 400); }
  }

  // ===================== Événements =====================
  viewTabs.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-view]"); if (!b) return;
    view = b.dataset.view; store("view", view); render();
  });
  tfTabs.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tf]"); if (!b) return;
    tf = b.dataset.tf; store("tf", tf); render();
  });
  stTabs.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-st]"); if (!b) return;
    st = b.dataset.st; store("st", st); render();
  });
  $("secFilter").addEventListener("change", (e) => {
    const k = e.target.dataset && e.target.dataset.sec; if (k == null) return;
    secSel = e.target.checked ? [...secSel, k] : secSel.filter((x) => x !== k);
    store("secsel", JSON.stringify(secSel)); secOpen = true; render();
  });
  $("secFilter").addEventListener("click", (e) => {
    if (e.target.closest("[data-clear]")) { secSel = []; store("secsel", "[]"); secOpen = true; render(); return; }
    if (e.target.closest("summary")) secOpen = !secOpen;
  });
  list.addEventListener("click", (e) => {
    const add = e.target.closest("[data-add]");
    if (add) { draft = null; openUnknown(add.dataset.add); return; }
    const pc = e.target.closest(".card[data-pos]"); if (pc) { openPosSheet(pc.dataset.pos); return; }
    const c = e.target.closest(".card[data-t]"); if (c) openSheet(c.dataset.t);
  });
  sheet.addEventListener("click", async (e) => {
    if (e.target.closest("#closeSheet")) { closeSheet(); return; }
    const hb = e.target.closest("[data-help]");
    if (hb) { hb.parentElement.classList.toggle("open"); return; }
    const c = e.target.closest("[data-c]");
    if (c && draft) { draft.c = c.dataset.c; if (draft.c === "O") draft.tf = null; renderEntry(); return; }
    const k = e.target.closest("[data-etf]");
    if (k && draft) { draft.tf = k.dataset.etf; renderEntry(); return; }
    if (e.target.closest("#entryOk") && draft && (draft.c === "O" || (draft.c && draft.tf))) {
      const raw = ($("entryPx").value || "").trim().replace(",", ".");
      const px = raw !== "" && isFinite(+raw) && +raw > 0 ? +raw : null;
      const np = { t: draft.t, c: draft.c, tf: draft.c === "O" ? null : draft.tf, e: px, ts: Date.now() };
      draft = null; closeSheet(); view = "own"; store("view", view);
      await changePositions(upsert(np), "⭐ Entrée enregistrée : notifications prioritaires dès le prochain passage (15 min au plus).");
      return;
    }
    const sell = e.target.closest("[data-sell]");
    if (sell) { closeSheet(); await changePositions(removeKey(sell.dataset.sell), "Position retirée."); return; }
    const ed = e.target.closest("[data-edit]");
    if (ed) {
      const p = pos.find((x) => posKey(x) === ed.dataset.edit); if (!p) return;
      const v = prompt("Prix d'entrée (vide = non renseigné) :", p.e == null ? "" : String(p.e).replace(".", ","));
      if (v === null) return;
      const raw = v.trim().replace(",", ".");
      const np = { ...p, e: raw !== "" && isFinite(+raw) && +raw > 0 ? +raw : null };
      await changePositions(upsert(np), "Prix d'entrée mis à jour.");
      openPosSheet(posKey(np)); return;
    }
    const cp = e.target.closest("[data-copy]");
    if (cp) { try { await navigator.clipboard.writeText(cp.dataset.copy); toast("Symbole copié : " + cp.dataset.copy); } catch (err) { toast("Copie impossible : " + cp.dataset.copy); } return; }
    if (e.target.closest("#statsBtn")) { openStats(); return; }
    if (e.target.closest("#saveSet")) saveSettings();
    else if (e.target.closest("#clearTok")) { token = ""; store("gh_token", null); $("tokIn").value = ""; $("setStatus").textContent = "Jeton effacé de ce téléphone."; }
  });
  backdrop.addEventListener("click", closeSheet);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !sheet.hidden) closeSheet(); });
  $("search").addEventListener("input", (e) => { query = e.target.value; renderList(); });
  $("refresh").addEventListener("click", () => { load(true); syncFromRemote(); });
  $("settingsBtn").addEventListener("click", openSettings);
  window.addEventListener("hashchange", () => { if (location.hash) openFromHash(); else if (!sheet.hidden) { sheet.hidden = true; backdrop.hidden = true; } });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(false); });
  setInterval(() => { if (!document.hidden) load(false); }, 5 * 60 * 1000);

  fetch("investing.json").then((r) => (r.ok ? r.json() : {})).then((j) => { investing = j || {}; }).catch(() => {});
  load(false).then(syncFromRemote);

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
  }
})();
