"""
Étude du profit : que rapportent réellement les configurations Fa et S sur de vrais cours ?

Règles (définies avec l'utilisateur) :
  Fa : entrée = clôture de la 1re bougie Fa validée ; sortie = clôture de la 1re bougie Fo de la même unité de temps ; succès si prix de sortie > prix
       d'entrée. Les Fa pas encore terminées sont ignorées. Aucune limite de durée.
  S haussière : entrée = clôture de la bougie S ; stop loss (plusieurs variantes) ; objectifs (TP) = les 5 niveaux les plus proches au-dessus de l'entrée
       (MM7 HA, MM20/50/100/200 de la même UT, Tenkan et MM7 baissière des UT supérieures jusqu'à 3 mois), fixés à l'entrée ; plusieurs schémas de sortie.
  Frais : --fee par ordre (0,5 % = formule Découverte du PEA Boursorama pour ~1 000 €), donc 2 x fee par aller-retour.
  Période de contrôle : les 30 % d'entrées les plus récentes servent de test hors échantillon (les réglages ne doivent pas être choisis dessus).

Limites : l'univers = actions actuellement cotées (biais du survivant) ; si SL et TP sont touchés dans la même bougie, on suppose le SL d'abord (prudent) ;
l'historique 4h ne remonte qu'à 2 ans ; l'UT inférieure (pour le SL « application ») n'est connue que sur 2 ans.

Usage : python profit.py --tickers 0 --fee 0.5 --out profit_report.md
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

import scanner as sc

TFS = ["4h", "1d", "1w", "1mo", "3mo"]
TRADE_TFS = ["4h", "1d", "1w", "1mo"]
LOWER = {"1d": "4h", "1w": "1d", "1mo": "1w"}
SL_VARIANTS = ["base", "app", "atr1", "atr1.5"]
# schémas de sortie S : liste de (part de la position, cible) ; cible = ("tp", i) ou ("fo",) ; le stop loss s'applique au reste
SCHEMES = {
    "TP1": [(1.0, ("tp", 0))], "TP2": [(1.0, ("tp", 1))], "TP3": [(1.0, ("tp", 2))], "TP4": [(1.0, ("tp", 3))], "TP5": [(1.0, ("tp", 4))],
    "½ TP1 + ½ TP2": [(0.5, ("tp", 0)), (0.5, ("tp", 1))],
    "½ TP1 + ½ Fo": [(0.5, ("tp", 0)), (0.5, ("fo",))],
    "Fo même UT": [(1.0, ("fo",))],
}
MAX_BARS = 600            # au-delà : trade non résolu (exclu)
HOLDOUT = 0.30


# ------------------------------------------------------------------ séries ------------------------------------------------------------------

def clean_series(df, up=1.8, down=0.55):
    """Données douteuses : un saut de cours d'une bougie à l'autre au-delà de +80 % / -45 % est presque toujours un regroupement ou une division d'actions
    mal ajusté par Yahoo (ex. Atos 2024-25). On ne garde que l'historique APRÈS le dernier saut. Renvoie (série, nombre de bougies écartées)."""
    c = df["Close"].to_numpy(dtype=float)
    if len(c) < 2:
        return df, 0
    r = c[1:] / c[:-1]
    bad = np.flatnonzero((r > up) | (r < down))
    if not len(bad):
        return df, 0
    cut = int(bad[-1]) + 1
    return df.iloc[cut:], cut


def end_of(start, tf):
    if tf == "3mo":
        first = start.tz_localize(None).replace(day=1)
        last = (first + pd.DateOffset(months=2)) + pd.offsets.BMonthEnd(0)
        return sc.at(last.tz_localize(sc.PARIS), sc.CLOSE_MIN)
    return sc.bar_end(start, tf)


def s_signals(hao, hah, hal, hac):
    """S haussière / baissière à chaque bougie, avec exactement les règles du scanner (voir analyze_tf)."""
    n = len(hac)
    rng = hah - hal
    body = np.abs(hac - hao)
    nobody = (rng > 0) & (body <= sc.S_BODY_MAX * rng)
    bear, bull = hac < hao, hac > hao
    top_w = hah - np.maximum(hao, hac)
    bot_w = np.minimum(hao, hac) - hal
    bear_clean = bear & (rng > 0) & (top_w <= sc.S_WICK_MAX * rng)
    bull_clean = bull & (rng > 0) & (bot_w <= sc.S_WICK_MAX * rng)
    real = (rng > 0) & (body >= sc.S_FIRM_BODY * rng)
    bear_firm = bear & real & (top_w <= sc.S_FIRM_WICK * rng)
    bull_firm = bull & real & (bot_w <= sc.S_FIRM_WICK * rng)
    up, dn, runlen = np.zeros(n, bool), np.zeros(n, bool), np.zeros(n, int)

    def run(k, arr):
        m = 0
        while k - 1 - m >= 0 and arr[k - 1 - m]:
            m += 1
        return m
    for k in range(sc.S_MIN_RUN, n):
        if not nobody[k]:
            continue
        nb, nu = run(k, bear), run(k, bull)
        if nb >= sc.S_MIN_RUN and bear_firm[k - 1] and bear_firm[k - 2] and int(bear_clean[k - nb:k].sum()) >= sc.S_WICK_MIN:
            up[k], runlen[k] = True, nb
        elif nu >= sc.S_MIN_RUN and bull_firm[k - 1] and bull_firm[k - 2] and int(bull_clean[k - nu:k].sum()) >= sc.S_WICK_MIN:
            dn[k], runlen[k] = True, nu
    return up, dn, runlen


def prep(df, tf, now):
    """Tous les tableaux utiles pour une unité de temps (bougies clôturées seulement)."""
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    if len(df) < sc.MIN_BARS:
        return None
    ends = [end_of(ts, tf) for ts in df.index]
    keep = np.array([e <= now for e in ends])
    df = df[keep]
    ends = pd.DatetimeIndex([e for e, k in zip(ends, keep) if k]).as_unit("ns")      # unité fixe : comparaisons en nanosecondes fiables
    if len(df) < sc.MIN_BARS:
        return None
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    vol = df["Volume"].to_numpy(dtype=float)
    hao, hah, hal, hac = sc.heikin_ashi(o, h, l, c)
    sh, sl_, sc_ = (hah, hal, hac) if sc.INDICATORS_ON == "ha" else (h, l, c)
    mm, tk = sc.lines(sh, sl_, sc_)
    st = sc.persist(sc.classify(hao, hac, mm, tk))
    up, dn, runlen = s_signals(hao, hah, hal, hac)
    cs = pd.Series(c)
    prevc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prevc), np.abs(l - prevc)))
    fa_first = np.full(len(c), -1)
    for k in range(len(c)):
        if st[k] == 1:
            fa_first[k] = k if k == 0 or st[k - 1] != 1 else fa_first[k - 1]
    return {
        "tf": tf, "n": len(c), "ends": ends, "end_ns": ends.asi8, "o": o, "h": h, "l": l, "c": c, "v": vol,
        "hao": hao, "hah": hah, "hal": hal, "hac": hac, "mm": mm, "tk": tk, "st": st, "up": up, "dn": dn, "runlen": runlen,
        "ma": {n_: cs.rolling(n_).mean().to_numpy() for n_ in sc.MA_TP},
        "atr": pd.Series(tr).rolling(14).mean().to_numpy(),
        "vavg": pd.Series(vol).rolling(20).mean().to_numpy(),
        "fa_first": fa_first,
    }


def asof(A, t_ns):
    """Indice de la dernière bougie de A clôturée à l'instant t (ou -1)."""
    return int(np.searchsorted(A["end_ns"], t_ns, side="right")) - 1


# ------------------------------------------------------------------ Fa ------------------------------------------------------------------

def features(A, k, others):
    c, rng = A["c"][k], A["hah"][k] - A["hal"][k]
    t = A["end_ns"][k]
    f = {"dist_mm7": (c - A["mm"][k]) / c if np.isfinite(A["mm"][k]) else np.nan,
         "dist_tk": (c - A["tk"][k]) / c if np.isfinite(A["tk"][k]) else np.nan,
         "body_pct": abs(A["hac"][k] - A["hao"][k]) / rng if rng > 0 else np.nan,
         "range_pct": rng / c,
         "atr_pct": A["atr"][k] / c if np.isfinite(A["atr"][k]) else np.nan,
         "vol_ratio": A["v"][k] / A["vavg"][k] if np.isfinite(A["vavg"][k]) and A["vavg"][k] > 0 else np.nan,
         "month": int(A["ends"][k].month)}
    i = TFS.index(A["tf"])
    for nm, tfn in (("up1", TFS[i + 1] if i + 1 < len(TFS) else None), ("lo1", TFS[i - 1] if i > 0 else None)):
        B = others.get(tfn) if tfn else None
        j = asof(B, t) if B is not None else -1
        f["st_" + nm] = int(B["st"][j]) if B is not None and j >= 0 else np.nan
    return f


def fa_trades(t, A, others):
    out, opened = [], 0
    st, c = A["st"], A["c"]
    for k in range(1, A["n"]):
        if st[k] == 1 and st[k - 1] != 1:
            m = k + 1
            while m < A["n"] and st[m] != -1:
                m += 1
            if m >= A["n"]:
                opened += 1
                continue
            out.append({"ticker": t, "tf": A["tf"], "entry_time": A["ends"][k], "entry": c[k], "exit": c[m], "ret": c[m] / c[k] - 1,
                        "bars": m - k, "mae": A["l"][k + 1:m + 1].min() / c[k] - 1, **features(A, k, others)})
    return out, opened


# ------------------------------------------------------------------ S ------------------------------------------------------------------

def tp_levels(A, k, others, entry):
    lv = [A["mm"][k]] + [A["ma"][n][k] for n in sc.MA_TP]
    t = A["end_ns"][k]
    for tfn in TFS[TFS.index(A["tf"]) + 1:]:
        B = others.get(tfn)
        j = asof(B, t) if B is not None else -1
        if B is None or j < 1:
            continue
        lv.append(B["tk"][j])
        if np.isfinite(B["mm"][j]) and np.isfinite(B["mm"][j - 1]) and B["mm"][j] < B["mm"][j - 1]:
            lv.append(B["mm"][j])
    return sorted(x for x in lv if np.isfinite(x) and x > entry * 1.0001)[:5]


def sl_levels(A, k, others, entry):
    base = A["hal"][k - 1]
    app = base
    lo = LOWER.get(A["tf"])
    B = others.get(lo) if lo else None
    if B is not None:
        j = asof(B, A["end_ns"][k])
        if j >= 0 and B["st"][j] == 1 and B["fa_first"][j] >= 0:
            f = B["fa_first"][j]
            app = max(base, min(B["hao"][f], B["hac"][f]))
    atr = A["atr"][k]
    out = {"base": base, "app": app}
    for name, mult in (("atr1", 1.0), ("atr1.5", 1.5)):
        out[name] = min(base, entry - mult * atr) if np.isfinite(atr) else np.nan
    return out


def simulate(A, k, entry, sl, tps, plan):
    """Rejoue la suite des bougies. Renvoie (rendement brut pondéré, sortie_par_SL) ou None si non résolu / niveaux manquants."""
    for _frac, target in plan:
        if target[0] == "tp" and target[1] >= len(tps):                 # "tpfo" : sans TP disponible, seul le Fo sort
            return None
    remaining = 1.0
    pnl = 0.0
    done = [False] * len(plan)
    hit_sl = False
    last = min(A["n"], k + 1 + MAX_BARS)
    for j in range(k + 1, last):
        o, h, l, c = A["o"][j], A["h"][j], A["l"][j], A["c"][j]
        if l <= sl:                                   # SL d'abord (prudent) ; ouverture sous le SL : sortie à l'ouverture
            px = min(o, sl)
            pnl += remaining * (px / entry - 1)
            return pnl, True
        for idx, (frac, target) in enumerate(plan):
            if done[idx]:
                continue
            if target[0] in ("tp", "tpfo") and target[1] < len(tps) and h >= tps[target[1]]:
                lvl = tps[target[1]]
                pnl += frac * (max(o, lvl) / entry - 1)
                remaining -= frac
                done[idx] = True
            elif target[0] in ("fo", "tpfo") and A["st"][j] == -1:
                pnl += frac * (c / entry - 1)
                remaining -= frac
                done[idx] = True
        if all(done):
            return pnl, hit_sl
    return None


def s_trades(t, A, others, fee_rt):
    out, skipped = [], 0
    for k in np.flatnonzero(A["up"]):
        if k < 2 or k + 1 >= A["n"]:
            continue
        entry = A["c"][k]
        tps = tp_levels(A, k, others, entry)
        sls = sl_levels(A, k, others, entry)
        rec = {"ticker": t, "tf": A["tf"], "entry_time": A["ends"][k], "entry": entry, "run": int(A["runlen"][k]), "n_tp": len(tps),
               **features(A, k, others), "res": {}}
        for sv in SL_VARIANTS:
            sl = sls[sv]
            if not np.isfinite(sl) or sl >= entry * 0.9995:
                continue
            risk = entry - sl
            rec["risk_" + sv] = risk / entry
            for name, plan in SCHEMES.items():
                r = simulate(A, k, entry, sl, tps, plan)
                if r is not None:
                    rec["res"][(sv, name)] = (r[0] - fee_rt, r[1], (r[0] - fee_rt) / (risk / entry))
        if rec["res"]:
            out.append(rec)
        else:
            skipped += 1
    return out, skipped


# ------------------------------------------------------------------ rapport ------------------------------------------------------------------

def pct(x, d=1):
    return "–" if x is None or not np.isfinite(x) else f"{x * 100:.{d}f} %"


def stats(rets):
    r = np.array(rets, dtype=float)
    if not len(r):
        return None
    w, lo = r[r > 0], r[r <= 0]
    return {"n": len(r), "win": float((r > 0).mean()), "exp": float(r.mean()), "gain": float(w.mean()) if len(w) else np.nan,
            "loss": float(lo.mean()) if len(lo) else np.nan, "med": float(np.median(r))}


def split_cut(times):
    if not len(times):
        return None
    lo, hi = min(times), max(times)
    return lo + (hi - lo) * (1 - HOLDOUT)


def report(fa, s, meta, fee):
    L = ["# Étude du profit — configurations Fa et S", "",
         f"*{meta['stocks']} actions françaises, historique journalier jusqu'à {meta['start']}, 4h sur 2 ans. Frais : {fee * 100:.2f} % par ordre "
         f"({fee * 200:.2f} % l'aller-retour), déjà déduits. Les {int(HOLDOUT * 100)} % d'entrées les plus récentes (« contrôle ») ne servent pas à choisir un réglage.*", "",
         "Lecture : **espérance** = gain moyen par trade, frais inclus. Un schéma est intéressant si l'espérance est positive à la fois sur l'ensemble ET sur le contrôle.", ""]
    L += ["## Fa : entrée à la validation, sortie au Fo de la même UT", "",
          "| UT | période | trades | succès | gain moyen | perte moyenne | espérance | médiane | durée médiane (bougies) | baisse max moyenne |", "|---|---|---|---|---|---|---|---|---|---|"]
    for tf in TRADE_TFS:
        rows = [x for x in fa if x["tf"] == tf]
        if not rows:
            continue
        cut = split_cut([x["entry_time"] for x in rows])
        for lab, sel in (("tout", rows), ("avant", [x for x in rows if x["entry_time"] <= cut]), ("contrôle", [x for x in rows if x["entry_time"] > cut])):
            r = stats([x["ret"] - 2 * fee for x in sel])
            if r:
                L.append(f"| {tf} | {lab} | {r['n']} | {pct(r['win'])} | {pct(r['gain'])} | {pct(r['loss'])} | **{pct(r['exp'], 2)}** | {pct(r['med'])} | "
                         f"{int(np.median([x['bars'] for x in sel]))} | {pct(np.mean([x['mae'] for x in sel]))} |")
    L += ["", f"Fa encore ouvertes (ignorées) : {meta['fa_open']}.",
          "Données : historique tronqué après un saut de cours suspect (division / regroupement mal ajusté) pour : "
          + (", ".join(f"{t} ({n} barres écartées)" for t, n in meta.get("cleaned", {}).items()) or "aucune action") + ".", ""]
    L += ["## S haussière : stop loss et objectifs", "",
          "SL : **base** = plus bas de la bougie HA précédente · **app** = règle de l'application (+ bas du corps de la 1re Fa de l'UT inférieure, 2 ans seulement) · "
          "**atr1 / atr1.5** = base, mais distance au SL au moins 1 ou 1,5 fois l'amplitude moyenne (ATR 14). "
          "TPn = n-ième niveau au-dessus de l'entrée (fixé à l'entrée). « % SL » = part des trades sortis par le stop. R = rendement / risque pris.", ""]
    for tf in TRADE_TFS:
        rows = [x for x in s if x["tf"] == tf]
        if not rows:
            continue
        cut = split_cut([x["entry_time"] for x in rows])
        L += [f"### {tf} — {len(rows)} signaux S", "", "| SL | sortie | trades | succès | espérance | contrôle (espér.) | gain moy. | perte moy. | % SL | R moyen |", "|---|---|---|---|---|---|---|---|---|---|"]
        table = []
        for sv in SL_VARIANTS:
            for name in SCHEMES:
                sel = [(x, x["res"][(sv, name)]) for x in rows if (sv, name) in x["res"]]
                if len(sel) < 10:
                    continue
                a = stats([r[0] for _, r in sel])
                h = stats([r[0] for x, r in sel if x["entry_time"] > cut])
                table.append((a["exp"], sv, name, a, h, np.mean([r[1] for _, r in sel]), np.mean([r[2] for _, r in sel])))
        for exp, sv, name, a, h, slr, rr in sorted(table, key=lambda x: -x[0])[:14]:
            L.append(f"| {sv} | {name} | {a['n']} | {pct(a['win'])} | **{pct(a['exp'], 2)}** | {pct(h['exp'], 2) if h else '–'} ({h['n'] if h else 0}) | {pct(a['gain'])} | {pct(a['loss'])} | {pct(slr, 0)} | {rr:.2f} |")
        L.append("")
    L += ["*(14 meilleures combinaisons par UT ; la liste complète est dans `profit_s_trades.csv`.)*", "",
          f"Signaux S sans réglage possible (SL au-dessus du cours, etc.) : {meta['s_skipped']}.", ""]
    return "\n".join(L)


# ------------------------------------------------------------------ programme ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=int, default=0, help="nombre d'actions (les plus liquides) ; 0 = toutes")
    ap.add_argument("--fee", type=float, default=0.5, help="frais par ordre en %% (0,5 = formule Découverte du PEA Boursorama, ordres ~1 000 €)")
    ap.add_argument("--min-turnover", type=float, default=sc.MIN_TURNOVER_EUR)
    ap.add_argument("--out", default="profit_report.md")
    ap.add_argument("--no-4h", action="store_true")
    ap.add_argument("--all-countries", action="store_true", help="ne pas limiter aux actions françaises")
    args = ap.parse_args()
    fee = args.fee / 100.0
    now = pd.Timestamp.now(tz=sc.PARIS)

    universe = sc.load_universe()
    try:
        cache = json.loads((sc.HERE / "sectors.json").read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    if not args.all_countries:
        ov = sc.load_pea_override()
        universe = {t: n for t, n in universe.items() if sc.pea_eligible(t, cache, ov)}
    print(f"{len(universe)} actions dans l'univers (France).", flush=True)
    daily = sc.download(list(universe), "max", "1d")
    cleaned = {}
    for t in list(daily):
        daily[t], n_cut = clean_series(daily[t])
        if n_cut:
            cleaned[t] = n_cut
    turn = {t: sc.turnover(daily[t]) for t in daily}
    ranked = sorted((t for t in daily if turn[t] >= args.min_turnover), key=lambda t: turn[t], reverse=True)
    if args.tickers:
        ranked = ranked[:args.tickers]
    hourly = {} if args.no_4h else {t: clean_series(df)[0] for t, df in sc.download(ranked, "2y", "1h").items()}
    print(f"{len(ranked)} actions testées ; historique tronqué après un saut suspect pour : {', '.join(f'{t} ({n} barres)' for t, n in cleaned.items()) or 'aucune'}.", flush=True)

    fa_all, s_all, fa_open, s_skipped, t0 = [], [], 0, 0, time.time()
    for i, t in enumerate(ranked, 1):
        frames = {"1d": daily[t], "1w": sc.aggregate(daily[t], "1w"), "1mo": sc.aggregate(daily[t], "1mo"), "3mo": sc.aggregate(daily[t], "3mo")}
        if t in hourly and len(hourly[t]):
            frames["4h"] = sc.aggregate(hourly[t], "4h")
        arrs = {}
        for tf, df in frames.items():
            try:
                a = prep(df, tf, now)
            except Exception as e:
                print(f"{t} {tf}: {e}", file=sys.stderr)
                a = None
            if a is not None:
                arrs[tf] = a
        for tf in TRADE_TFS:
            A = arrs.get(tf)
            if A is None:
                continue
            f, o = fa_trades(t, A, arrs)
            fa_all += f
            fa_open += o
            s_, sk = s_trades(t, A, arrs, 2 * fee)
            s_all += s_
            s_skipped += sk
        if i % 10 == 0:
            print(f"  {i}/{len(ranked)} actions, {len(fa_all)} Fa, {len(s_all)} S, {int(time.time() - t0)} s", flush=True)

    start = min((daily[t].index[0] for t in ranked if t in daily), default=now).strftime("%Y-%m-%d")
    meta = {"stocks": len(ranked), "start": start, "fa_open": fa_open, "s_skipped": s_skipped, "cleaned": cleaned}
    text = report(fa_all, s_all, meta, fee)
    Path(args.out).write_text(text, encoding="utf-8")
    pd.DataFrame(fa_all).to_csv("profit_fa_trades.csv", index=False)
    rows = []
    for x in s_all:
        base = {k: v for k, v in x.items() if k != "res"}
        for (sv, name), (net, slhit, r) in x["res"].items():
            base[f"ret|{sv}|{name}"] = round(net, 5)
        rows.append(base)
    pd.DataFrame(rows).to_csv("profit_s_trades.csv", index=False)
    print(text)


if __name__ == "__main__":
    main()
