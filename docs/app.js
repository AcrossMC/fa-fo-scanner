(() => {
  "use strict";

  const TFS = [["1h", "1h"], ["4h", "4h"], ["1d", "1j"], ["1w", "1sem"], ["1mo", "1mois"]];
  const TF_NAME = { "1h": "1 heure", "4h": "4 heures", "1d": "1 jour", "1w": "1 semaine", "1mo": "1 mois" };
  const STATES = [["fa", "Fa"], ["s", 'S <i class="sq up"></i>']];
  const GH_VAR = "HOLDINGS";

  let data = null;
  let investing = {};                       // docs/investing.json : ticker -> identifiant de page investing.com (facultatif)
  let tf = store("tf") || "1d";
  let st = store("st") === "s" ? "s" : "fa";
  let query = "";
  let held = new Set(readList(store("held")));
  let token = store("gh_token") || "";
  let repo = store("gh_repo") || detectRepo();

  const $ = (id) => document.getElementById(id);
  const tfTabs = $("tfTabs"), stTabs = $("stTabs"), list = $("list"), sheet = $("sheet"), backdrop = $("backdrop");

  function store(k, v) {
    try { if (v === undefined) return localStorage.getItem(k); if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { return null; }
  }
  function readList(raw) { return (raw || "").split(",").map((x) => x.trim().toUpperCase()).filter((x) => /^[A-Z0-9]+(\.[A-Z]+)?$/.test(x)); }
  function detectRepo() {
    const h = location.hostname;
    return h.endsWith(".github.io") ? h.split(".")[0] + "/" + (location.pathname.split("/")[1] || "") : "";
  }
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const short = (t) => t.replace(/\.PA$/, "");
  const fmtPrice = (p) => p.toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " €";
  const fmtChg = (c) => (c > 0 ? "+" : "") + c.toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " %";
  const pct = (p) => Math.round(p * 100) + " %";
  const sq = (dir) => `<i class="sq ${dir === "up" ? "up" : "down"}" aria-hidden="true"></i>`;
  const sLabel = (s) => (s.dir === "up" ? "S haussière" : "S baissière");
  const sWhat = (s) => `bougie sans corps après ${s.n} bougie${s.n > 1 ? "s" : ""} ${s.dir === "up" ? "baissière" : "verte"}${s.n > 1 ? "s" : ""}`;

  function fmtSince(iso, tfKey) {
    const d = new Date(iso);
    const o = { timeZone: "Europe/Paris" };
    if (tfKey === "1h" || tfKey === "4h") return d.toLocaleString("fr-FR", { ...o, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).replace(",", "");
    if (tfKey === "1d") return d.toLocaleDateString("fr-FR", { ...o, weekday: "short", day: "2-digit", month: "2-digit" });
    if (tfKey === "1w") return "semaine du " + d.toLocaleDateString("fr-FR", { ...o, day: "2-digit", month: "2-digit" });
    return d.toLocaleDateString("fr-FR", { ...o, month: "long", year: "numeric" });
  }

  // ---- « En cours de validation » : la bougie en formation a une probabilité >= seuil de valider la configuration ----
  const visibleTf = () => (data && data.meta.visible_tf) || ["4h", "1d", "1w", "1mo"];
  const thr = (k, isHeld) => (isHeld && k === "1h" ? (data.meta.prob_min_held_1h ?? 0.5) : (data.meta.prob_min ?? 0.3));
  function pend(r, k, isHeld) {
    // fa / fo : pas déjà dans cet état ; su / sd : S haussière / baissière en formation
    if (!r || !r.forming) return {};
    const th = thr(k, isHeld), ok = (p) => (p != null && p >= th ? p : null);
    return { fa: r.state !== "fa" ? ok(r.p_fa) : null, fo: r.state !== "fo" ? ok(r.p_fo) : null, su: ok(r.p_s_up), sd: ok(r.p_s_down) };
  }
  const tfList = () => TFS.filter(([k]) => (k === "1h" ? held.size > 0 : visibleTf().includes(k)));     // le 1h : uniquement si j'ai des positions

  function rowsFor(tfKey, bucket) {
    const out = [];
    for (const [t, s] of Object.entries(data.stocks)) {
      const isHeld = held.has(t);
      if (!isHeld && !visibleTf().includes(tfKey)) continue;           // actions non détenues : 4h, 1j, 1sem, 1mois seulement
      const r = s.tf[tfKey];
      const p = pend(r, tfKey, isHeld)[bucket === "fa" ? "fa" : "su"];
      if (p != null) out.push([t, s, r, p]);
    }
    out.sort((a, b) => b[3] - a[3]);                                   // probabilité décroissante
    return out.sort((a, b) => held.has(b[0]) - held.has(a[0]));        // mes positions toujours en premier (tri stable)
  }

  function renderTabs() {
    if (!tfList().some(([k]) => k === tf)) tf = tfList()[0][0];
    tfTabs.innerHTML = tfList().map(([k, l]) => `<button role="tab" data-tf="${k}" aria-selected="${k === tf}">${l}</button>`).join("");
    stTabs.innerHTML = STATES.map(([k, l]) => {
      const n = data ? rowsFor(tf, k).length : 0;
      return `<button role="tab" data-st="${k}" aria-selected="${k === st}">${l}<span class="count">${n}</span></button>`;
    }).join("");
  }

  const dot = (state) => `<i class="dot ${state}" aria-hidden="true"></i>`;
  const stateWord = (r) => (r.state === "fa" ? "Fa validé" : r.state === "fo" ? "Fo" : "neutre");

  const pb = (html, p, cls) => `<span class="pb ${cls}">${html}${p != null ? " " + pct(p) : ""}</span>`;

  // Carte d'une liste : probabilité pour l'unité de temps affichée + autres unités de temps où l'action est aussi « en cours »
  function otherChips(t, s) {
    const isHeld = held.has(t);
    return tfList().filter(([k]) => k !== tf).map(([k, l]) => {
      const p = pend(s.tf[k], k, isHeld)[st === "fa" ? "fa" : "su"];
      return p == null ? "" : `<span class="chip"><b>${l}</b>${st === "fa" ? dot("fa") : sq("up")}<span>${pct(p)}</span></span>`;
    }).join("");
  }

  function card(t, s, r, p) {
    const isHeld = held.has(t);
    const over = st === "fa" && r.over && r.over.fa;
    const others = otherChips(t, s);
    return `<button class="card ${st === "s" ? "sup" : "pending"}${isHeld ? " held" : ""}" data-t="${esc(t)}">
      <div class="row"><span class="left">${isHeld ? '<span class="star" aria-label="En position">⭐</span>' : ""}<span class="tick">${esc(short(t))}</span><span class="name">${esc(s.name)}</span></span>
      <span class="price">${fmtPrice(s.price)}<span class="chg ${s.chg >= 0 ? "up" : "down"}">${fmtChg(s.chg)}</span></span></div>
      <div class="pline"><span class="pbig">${st === "fa" ? dot("fa") : sq("up")}<b>${pct(p)}</b></span>
        <span class="pbar"><i class="${p >= (data.meta.prob_high ?? 0.6) ? "fa" : "mid"}" style="width:${Math.max(p * 100, 2)}%"></i></span>
        ${over ? '<span class="over">déjà au-dessus</span>' : ""}</div>
      ${others ? `<div class="chips">${others}</div>` : ""}</button>`;
  }

  const LEGEND = `<div class="legend">${dot("fa")} Fa en cours ${sq("up")} S haussière en cours<span class="muted" style="width:100%">Pourcentage = chance que la bougie en formation clôture avec cette configuration. Touchez une action pour le détail.</span></div>`;

  function renderList() {
    if (!data || !Object.keys(data.stocks).length) {
      list.innerHTML = `<div class="empty">En attente de la première analyse.<br>Lancez le workflow « Scan Fa/Fo » sur GitHub.</div>`;
      return;
    }
    const q = query.trim().toLowerCase();
    if (q) { renderSearch(q); return; }          // recherche : toutes les actions suivies, pas seulement celles « en cours »
    let rows = rowsFor(tf, st);
    if (!rows.length) {
      const msg = { fa: "Aucune Fa en cours de validation", s: "Aucune S haussière en cours de validation" }[st];
      list.innerHTML = `<div class="empty">${msg} en ${esc(TF_NAME[tf])}.</div>`;
      return;
    }
    list.innerHTML = LEGEND + rows.map(([t, s, r, p]) => card(t, s, r, p)).join("");
  }

  // ---- Recherche : retrouver n'importe quelle action suivie, ou ajouter une action absente de la liste comme position ----
  function candidateTicker(q) {
    const t = q.trim().toUpperCase().replace(/\s+/g, "");
    if (!/^[A-Z0-9][A-Z0-9\-]{0,9}(\.[A-Z]{1,3})?$/.test(t)) return null;
    if (t.includes(".") && !t.endsWith(".PA")) return null;   // le scanner suit uniquement Euronext Paris (horaires 9h-17h35)
    return t.includes(".") ? t : t + ".PA";                 // sans suffixe : action de Paris
  }
  function renderSearch(q) {
    const hits = Object.entries(data.stocks)
      .filter(([t, s]) => t.toLowerCase().includes(q) || s.name.toLowerCase().includes(q))
      .sort((a, b) => held.has(b[0]) - held.has(a[0]) || a[0].localeCompare(b[0])).slice(0, 30);
    let html = hits.map(([t, s]) => `<button class="card${held.has(t) ? " held" : ""}" data-t="${esc(t)}">
      <div class="row"><span class="left">${held.has(t) ? '<span class="star">⭐</span>' : ""}<span class="tick">${esc(short(t))}</span><span class="name">${esc(s.name)}</span></span>
      <span class="price">${fmtPrice(s.price)}<span class="chg ${s.chg >= 0 ? "up" : "down"}">${fmtChg(s.chg)}</span></span></div></button>`).join("");
    const cand = candidateTicker(query);
    if (cand && !data.stocks[cand]) {
      html += held.has(cand)
        ? `<div class="card held nodata"><span class="tick">${esc(short(cand))}</span><span class="muted"> · en position, analyse au prochain passage (15 min au plus)</span></div>`
        : `<div class="card addcard"><div><b>${esc(cand)}</b> n'est pas dans la liste suivie.</div>
           <button class="posbtn" data-add="${esc(cand)}">☆ J'ai cette action : l'ajouter à mes positions</button>
           <div class="muted small">Code Yahoo Finance d'une action de Paris (ex. ALCLS.PA). Sans suffixe, « .PA » est ajouté. Analyse dès le prochain passage, comme les autres actions.</div></div>`;
    }
    list.innerHTML = html || `<div class="empty">Aucune action ne correspond à « ${esc(query.trim())} ».</div>`;
  }

  // ---- Bloc « Mes positions' (toujours en haut) : toutes les unités de temps, danger (Fo, S baissière) comprises ----
  function posChips(t, s) {
    return TFS.map(([k, l]) => {
      const r = s.tf[k];
      if (!r) return `<span class="chip"><b>${l}</b><i class="dot none" aria-hidden="true"></i></span>`;
      const p = pend(r, k, true);
      let h = dot(r.state);
      if (r.s && r.s.active) h += sq(r.s.dir);
      if (p.fa != null) h += pb(dot("fa"), p.fa, "fa");
      if (p.fo != null) h += pb(dot("fo"), p.fo, "danger");
      if (p.su != null) h += pb(sq("up"), p.su, "fa");
      if (p.sd != null) h += pb(sq("down"), p.sd, "danger");
      const danger = r.state === "fo" || (r.s && r.s.active && r.s.dir === "down") || p.fo != null || p.sd != null;
      return `<span class="chip${danger ? " danger" : ""}"><b>${l}</b>${h}</span>`;
    }).join("");
  }

  function renderPositions() {
    const el = $("positions");
    if (!data || !held.size) { el.hidden = true; el.innerHTML = ""; return; }
    const open = el.dataset.open !== "0";
    const cards = [...held].sort().map((t) => {
      const s = data.stocks[t];
      if (!s) return `<div class="card held nodata"><span class="tick">${esc(short(t))}</span><span class="muted"> · analyse au prochain passage (15 min au plus)</span></div>`;
      return `<button class="card held pos" data-t="${esc(t)}">
        <div class="row"><span class="left"><span class="star">⭐</span><span class="tick">${esc(short(t))}</span><span class="name">${esc(s.name)}</span></span>
        <span class="price">${fmtPrice(s.price)}<span class="chg ${s.chg >= 0 ? "up" : "down"}">${fmtChg(s.chg)}</span></span></div>
        <div class="chips">${posChips(t, s)}</div></button>`;
    }).join("");
    el.hidden = false;
    el.innerHTML = `<button class="sec" id="posToggle" aria-expanded="${open}"><span>⭐ Mes positions <span class="count">${held.size}</span></span><span aria-hidden="true">${open ? "▾" : "▸"}</span></button>`
      + `<div class="pos-list" ${open ? "" : "hidden"}>${cards}</div>`;
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
      + `${sq("up")} S haussière en cours : elle peut clôturer sans corps après au moins 3 bougies baissières dont au moins 2 sans mèche haute. `
      + `Pour vos positions ⭐ : toutes les unités de temps (1h comprise, seuil ${pct(m.prob_min_held_1h ?? 0.5)}), avec Fo et ${sq("down")} S baissière. `
      + `Le pourcentage est une estimation statistique, pas une prévision. Information indicative, pas un conseil en investissement.`;
  }

  function render() { renderPositions(); renderTabs(); renderList(); }

  // ---- Fiche état des lieux ----
  function openSheet(t) {
    const s = data && data.stocks[t];
    if (!s) return;
    const isHeld = held.has(t);
    const probRow = (marker, label, p, cls) => {
      const v = p > 0 && Math.round(p * 100) === 0 ? "< 1 %" : pct(p);
      return `<div class="prob${p < 0.05 ? " low" : ""}"><span class="pl">${marker}${label}</span><span class="pbar"><i class="${cls}" style="width:${Math.max(p * 100, p > 0 ? 2 : 0)}%"></i></span><span class="pv">${v}</span></div>`;
    };
    const rows = TFS.filter(([k]) => isHeld || visibleTf().includes(k)).map(([k, l]) => {
      const r = s.tf[k];
      if (!r) return `<div class="tf-row"><div class="tf-head"><span class="lab">${l}</span><span class="muted">Pas assez d'historique</span></div></div>`;
      const b = { s: !!(r.s && r.s.active) };
      let pills = `<span class="pill ${r.state}">${dot(r.state)}${r.state === "fa" ? "Fa validé" : r.state === "fo" ? "Fo" : "Fa non validé"}</span>`;
      if (b.s) pills += `<span class="sqpill ${r.s.dir}">${sq(r.s.dir)}${sLabel(r.s)}</span>`;
      let info = "";
      if (r.state !== "none") info += `<div class="since">${r.state === "fa" ? "Fa" : "Fo"} depuis ${esc(fmtSince(r.since, k))} · ${r.bars} bougie${r.bars > 1 ? "s" : ""}</div>`;
      if (b.s) info += `<div class="since">${sLabel(r.s)} : ${sWhat(r.s)}</div>`;
      let probs;
      if (r.forming && r.p_fa != null) {
        probs = `<div class="probs"><div class="ptitle">À la clôture de la bougie en cours</div>`
          + (isHeld
            ? (r.state === "fa"
              ? probRow(dot("fa"), "Fa maintenu", Math.max(0, 1 - r.p_fo), "fa") + probRow(dot("fo"), "Invalidation Fo", r.p_fo, "fo")
              : r.state === "fo"
                ? probRow(dot("fo"), "Fo maintenu", Math.max(0, 1 - r.p_fa), "fo") + probRow(dot("fa"), "Validation Fa", r.p_fa, "fa")
                : probRow(dot("fa"), "Validation Fa", r.p_fa, "fa") + probRow(dot("fo"), "Validation Fo", r.p_fo, "fo"))
            : (r.state === "fa" ? `<div class="since">Déjà en Fa : suivi seulement pour les positions.</div>` : probRow(dot("fa"), "Validation Fa", r.p_fa, "fa")))
          + (r.p_s_up > 0 ? probRow(sq("up"), "S haussière", r.p_s_up, "fa") : "")
          + (isHeld && r.p_s_down > 0 ? probRow(sq("down"), "S baissière", r.p_s_down, "fo") : "") + `</div>`;
      } else {
        probs = `<div class="probs"><div class="ptitle muted">${r.forming ? "Probabilités indisponibles" : "Marché fermé : la prochaine bougie s'ouvre à la reprise"}</div></div>`;
      }
      return `<div class="tf-row"><div class="tf-head"><span class="lab">${l}</span><span class="what">${pills}</span></div>${info}${probs}</div>`;
    }).join("");
    // Boutons graphique : même unité de temps que l'onglet ouvert (TradingView) ; Investing.com : page de l'action (voir docs/investing.json)
    const TV_INT = { "1h": "60", "4h": "240", "1d": "D", "1w": "W", "1mo": "M" };
    const tv = "https://www.tradingview.com/chart/?symbol=" + encodeURIComponent("EURONEXT:" + short(t)) + "&interval=" + TV_INT[tf];
    const slug = investing[t];
    const inv = slug ? `https://www.investing.com/equities/${slug}-chart` : "https://www.investing.com/search/?q=" + encodeURIComponent(s.name);
    sheet.innerHTML = `<div class="sheet-head"><div><h2 id="sheetTitle">${isHeld ? "⭐ " : ""}${esc(short(t))}</h2>
      <div class="sub">${esc(s.name)} · ${fmtPrice(s.price)} · ${fmtChg(s.chg)}</div></div>
      <button class="close" id="closeSheet" aria-label="Fermer">✕</button></div>
      <button class="posbtn${isHeld ? " on" : ""}" id="posBtn" data-t="${esc(t)}">${isHeld ? "⭐ En position · retirer" : "☆ J'ai cette action"}</button>
      <div class="tf-table">${rows}</div>
      <a class="link" href="${tv}" target="_blank" rel="noopener">Graphique ${esc(TF_NAME[tf])} · TradingView</a>
      <a class="link alt" href="${inv}" target="_blank" rel="noopener">${slug ? "Page Investing.com" : "Chercher sur Investing.com"} (régler ${esc(TF_NAME[tf])} sur place)</a>
      <p class="note">Fa = démarre quand le haut du corps Heikin Ashi clôturé dépasse la Tenkan et une MM7 ascendante, dure jusqu'au Fo · Fo = le bas du corps passe sous les deux · S = bougie sans corps après des bougies de même sens (${sq("up")} haussière, ${sq("down")} baissière). Les pourcentages sont des estimations statistiques (simulation sur le temps restant de la bougie), pas des prévisions.</p>`;
    showSheet();
    if (!location.hash.slice(1).toUpperCase().startsWith(t)) history.replaceState(null, "", "#" + t);
  }
  function showSheet() { sheet.hidden = false; backdrop.hidden = false; const c = $("closeSheet"); if (c) c.focus(); }
  function closeSheet() {
    sheet.hidden = true; backdrop.hidden = true;
    if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  }
  function openFromHash() {
    const [t, k] = decodeURIComponent(location.hash.slice(1)).toUpperCase().split("@");      // #AIR.PA@4H : ouvre la fiche sur l'unité de la notification
    const k2 = (k || "").toLowerCase();
    if (TFS.some(([x]) => x === k2) && k2 !== tf) { tf = k2; render(); }
    if (t && data) openSheet(t);
  }

  // ---- Positions : enregistrement local + synchronisation GitHub (variable privée HOLDINGS) ----
  let toastTimer;
  function toast(msg) {
    const el = $("toast"); el.textContent = msg; el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 6000);
  }
  function saveHeld() { store("held", [...held].sort().join(",")); }

  async function gh(method, path, body) {
    return fetch(`https://api.github.com/repos/${repo}/actions/variables${path}`, {
      method,
      headers: Object.assign({ Authorization: "Bearer " + token, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" },
        body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined,
    });
  }
  async function readRemote() {
    const r = await gh("GET", "/" + GH_VAR);
    if (r.status === 404) return null;
    if (r.status === 401 || r.status === 403) throw new Error("jeton refusé (permission « Variables » en lecture et écriture requise)");
    if (!r.ok) throw new Error("GitHub " + r.status);
    return new Set(readList((await r.json()).value));
  }
  async function writeRemote(set) {
    const value = set.size ? [...set].sort().join(",") : "-";          // GitHub refuse une valeur vide
    let r = await gh("PATCH", "/" + GH_VAR, { name: GH_VAR, value });
    if (r.status === 404) r = await gh("POST", "", { name: GH_VAR, value });
    if (r.status === 401 || r.status === 403) throw new Error("jeton refusé (permission « Variables » en lecture et écriture requise)");
    if (!r.ok) throw new Error("GitHub " + r.status);
  }
  const canSync = () => !!(token && repo);

  async function toggleHeld(t) {
    const on = !held.has(t);
    on ? held.add(t) : held.delete(t);
    saveHeld(); render(); if (data && data.stocks[t]) openSheet(t);
    if (!canSync()) { toast("Enregistrée sur ce téléphone. Sans jeton GitHub (réglages ⚙), pas de notifications spéciales."); return; }
    try {
      const remote = (await readRemote()) || new Set();
      on ? remote.add(t) : remote.delete(t);
      await writeRemote(remote);
      held = remote; saveHeld(); render();
      toast(on ? "⭐ Position enregistrée : notifications prioritaires dès le prochain passage (≤ 15 min)." : "Position retirée.");
    } catch (e) {
      toast("Synchronisation impossible : " + e.message + ". Position gardée sur ce téléphone seulement.");
    }
  }

  async function syncFromRemote() {
    if (!canSync()) return;
    try {
      const remote = await readRemote();
      if (remote) { held = remote; saveHeld(); render(); }
    } catch (e) { /* hors-ligne ou jeton invalide : on garde la liste locale */ }
  }

  // ---- Réglages ----
  function openSettings() {
    sheet.innerHTML = `<div class="sheet-head"><h2 id="sheetTitle">Réglages</h2><button class="close" id="closeSheet" aria-label="Fermer">✕</button></div>
      <p class="note" style="margin-top:12px">Pour que vos positions déclenchent des notifications ⭐ prioritaires, l'application met à jour une variable <b>privée</b> de votre dépôt GitHub.
      Il faut pour cela un jeton à coller une fois (voir SETUP.md, étape 7). Il reste sur ce téléphone.</p>
      <label class="field" for="repoIn">Dépôt (pseudo/nom)</label><input class="text" id="repoIn" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="pseudo/fa-fo-scanner">
      <label class="field" for="tokIn">Jeton GitHub</label><input class="text" id="tokIn" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="github_pat_…">
      <button class="link" id="saveSet" style="width:100%;border:0;cursor:pointer;margin-top:12px">Enregistrer et synchroniser</button>
      <button class="posbtn" id="clearTok" style="margin-top:10px">Effacer le jeton de ce téléphone</button>
      <p class="note" id="setStatus" role="status">${held.size} position${held.size > 1 ? "s" : ""} enregistrée${held.size > 1 ? "s" : ""} sur ce téléphone.${canSync() ? " Synchronisation GitHub active." : ""}</p>`;
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
      const remote = (await readRemote()) || new Set();
      const merged = new Set([...remote, ...held]);
      if (merged.size !== remote.size || remote.size === 0) await writeRemote(merged);   // crée la variable si besoin
      held = merged; saveHeld(); render();
      status.textContent = `Connecté. ${held.size} position${held.size > 1 ? "s" : ""} synchronisée${held.size > 1 ? "s" : ""}.`;
    } catch (e) {
      status.textContent = "Échec : " + e.message;
    }
  }

  // ---- Données ----
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

  // ---- Événements ----
  tfTabs.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tf]"); if (!b) return;
    tf = b.dataset.tf; store("tf", tf); render();
  });
  stTabs.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-st]"); if (!b) return;
    st = b.dataset.st; store("st", st); render();
  });
  list.addEventListener("click", (e) => {
    const add = e.target.closest("[data-add]");
    if (add) { toggleHeld(add.dataset.add); return; }
    const c = e.target.closest(".card[data-t]"); if (c) openSheet(c.dataset.t);
  });
  $("positions").addEventListener("click", (e) => {
    if (e.target.closest("#posToggle")) {
      const el = $("positions"); el.dataset.open = el.dataset.open === "0" ? "1" : "0"; renderPositions(); return;
    }
    const c = e.target.closest(".card[data-t]"); if (c) openSheet(c.dataset.t);
  });
  sheet.addEventListener("click", (e) => {
    if (e.target.closest("#closeSheet")) closeSheet();
    else if (e.target.closest("#posBtn")) toggleHeld(e.target.closest("#posBtn").dataset.t);
    else if (e.target.closest("#saveSet")) saveSettings();
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
