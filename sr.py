"""
Supports et résistances « de prix » : améliorent-ils les sorties (TP) et les stops (SL) par rapport aux moyennes mobiles ?

Niveaux testés (tous calculés avec les seules données connues à l'entrée) :
  Swing   : sommets (résistance) / creux (support) locaux, confirmés par 3 bougies de chaque côté, sur les 104 dernières bougies hebdo ou 250 journalières ;
            niveaux voisins (< 1 %) fusionnés.
  Donchian: plus haut (résistance) / plus bas (support) des N dernières bougies (N = 13/26/52/104 en hebdo, 20/50/100/250 en journalier).
  Rond    : prochain nombre rond au-dessus (pas = moitié de la puissance de 10 du cours : 50 pour un cours de 47 €, 0,5 pour 3,2 €).
  POC     : zones de prix les plus échangées (histogramme des clôtures pondéré par le volume, 30 tranches) au-dessus du cours.
  Prix    : tous ces niveaux réunis ; Tout : réunis avec les MM / Tenkan des UT supérieures.
Règles fixées avant les résultats :
  S : référence = SL atr1.5 + TP5 sur les moyennes mobiles (règle actuelle de l'application, SL) ; SL testés : atr1.5, swing (support le plus proche - 0,25 ATR), plus bas 20 bougies - 0,25 ATR ;
      sorties testées : TP sur le 1er/2e/3e niveau de chaque famille. Comparaison APPARIÉE avec la référence (mêmes entrées).
  Fa : entrée à la validation, sortie « niveau de prix ou Fo » comparée à « Fo seul » (mêmes entrées).
  Une combinaison n'est retenue que si l'écart avec la référence est > 0 avec IC 99 % > 0 en apprentissage (jusqu'en 2015) ET IC 95 % > 0 en test (2016 et après).
"""
import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

import profit
import robust
import scanner as sc

SPLIT_YEAR = 2016
W = 3
BOOT = 2000
LOOK = {"1d": 250, "1w": 104}
DON = {"1d": (20, 50, 100, 250), "1w": (13, 26, 52, 104)}
TP_FAMILIES = ["MM", "Swing-1", "Swing-2", "Swing-3", "Donchian-1", "Donchian-2", "Rond-1", "Rond-2", "POC-1", "Prix-1", "Prix-2", "Prix-3", "Tout-1", "Tout-3"]
SL_KINDS = ["atr1.5", "swing", "bas20"]
FA_FAMILIES = ["MM", "Swing-1", "Swing-2", "Donchian-1", "Rond-1", "POC-1", "Prix-1", "Prix-2", "Tout-1"]


def cluster(vals, tol=0.01):
    out = []
    for v in sorted(vals):
        if not out or (v - out[-1]) / out[-1] >= tol:
            out.append(v)
    return out


def sr_prep(A, tf):
    h, l = A["h"], A["l"]
    hh = pd.Series(h).rolling(2 * W + 1, center=True).max().to_numpy()
    ll = pd.Series(l).rolling(2 * W + 1, center=True).min().to_numpy()
    S = {"sh": np.flatnonzero(h == hh), "sl": np.flatnonzero(l == ll),
         "dmax": {N: pd.Series(h).rolling(N).max().to_numpy() for N in DON[tf]},
         "min20": pd.Series(l).rolling(20).min().to_numpy()}
    return S


def levels_at(A, S, tf, k, entry):
    """Familles de niveaux au-dessus du cours (listes croissantes) + support le plus proche en dessous."""
    L = LOOK[tf]
    sh = S["sh"][(S["sh"] >= k - L) & (S["sh"] <= k - W)]
    swing = cluster([v for v in A["h"][sh] if v > entry * 1.0001])
    don = cluster([S["dmax"][N][k] for N in DON[tf] if np.isfinite(S["dmax"][N][k]) and S["dmax"][N][k] > entry * 1.0001])
    step = 10 ** math.floor(math.log10(entry)) / 2
    r1 = (math.floor(entry / step) + 1) * step
    rond = [r1, r1 + step]
    lo = max(0, k - L + 1)
    poc = []
    if k - lo >= 30:
        cl, vo = A["c"][lo:k + 1], A["v"][lo:k + 1]
        hist, edges = np.histogram(cl, bins=30, weights=vo)
        cen = (edges[:-1] + edges[1:]) / 2
        top = np.argsort(hist)[::-1][:3]
        poc = sorted(float(cen[i]) for i in top if cen[i] > entry * 1.0001)[:1]
    prix = cluster(swing + don + rond + poc)
    return {"Swing": swing, "Donchian": don, "Rond": rond, "POC": poc, "Prix": prix}


def pick(fam, L, mm):
    """Liste de TP (croissante) pour une famille ; l'indice utilisé est donné par le nom (-1, -2, -3)."""
    base, _, n = fam.partition("-")
    if fam == "MM":
        return mm
    if base == "Tout":
        return cluster(L["Prix"] + mm)
    return L[base]


def tp_index(fam):
    if fam == "MM":
        return 4
    return int(fam.split("-")[1]) - 1


def sl_levels_sr(A, S, tf, k, entry, base_atr):
    atr = A["atr"][k]
    out = {"atr1.5": base_atr}
    sup = A["l"][S["sl"][(S["sl"] >= k - LOOK[tf]) & (S["sl"] <= k - W)]]
    sup = sup[sup < entry * 0.9995]
    out["swing"] = (float(sup.max()) - 0.25 * atr) if len(sup) and np.isfinite(atr) else np.nan
    out["bas20"] = (S["min20"][k] - 0.25 * atr) if np.isfinite(S["min20"][k]) and np.isfinite(atr) else np.nan
    return out


def collect(args, rng):
    fee_rt = 2 * args.fee / 100.0
    universe = sc.load_universe()
    try:
        cache = json.loads((sc.HERE / "sectors.json").read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    ov = sc.load_pea_override()
    universe = {t: n for t, n in universe.items() if sc.pea_eligible(t, cache, ov)}
    daily = sc.download(list(universe), "max", "1d")
    for t in list(daily):
        daily[t], _ = profit.clean_series(daily[t])
    turn = {t: sc.turnover(daily[t]) for t in daily}
    ranked = sorted((t for t in daily if turn[t] >= sc.MIN_TURNOVER_EUR), key=lambda t: turn[t], reverse=True)
    if args.tickers:
        ranked = ranked[:args.tickers]
    now = pd.Timestamp.now(tz=sc.PARIS)
    s_rows, fa_rows, t0 = [], [], time.time()
    for i, t in enumerate(ranked, 1):
        frames = {"1d": daily[t], "1w": sc.aggregate(daily[t], "1w"), "1mo": sc.aggregate(daily[t], "1mo"), "3mo": sc.aggregate(daily[t], "3mo")}
        arrs = {}
        for tf, df in frames.items():
            try:
                a = profit.prep(df, tf, now)
            except Exception:
                a = None
            if a is not None:
                arrs[tf] = a
        for tf in ("1d", "1w"):
            A = arrs.get(tf)
            if A is None:
                continue
            S = sr_prep(A, tf)
            c, st = A["c"], A["st"]
            # ---- S haussière ----
            for k in np.flatnonzero(A["up"]):
                if k < 105 or k + 1 >= A["n"]:
                    continue
                entry = c[k]
                mm = profit.tp_levels(A, k, arrs, entry)
                L = levels_at(A, S, tf, k, entry)
                sls = sl_levels_sr(A, S, tf, k, entry, profit.sl_levels(A, k, arrs, entry)["atr1.5"])
                row = {"tf": tf, "ticker": t, "time": A["ends"][k]}
                for sk in SL_KINDS:
                    sl = sls[sk]
                    if not np.isfinite(sl) or sl >= entry * 0.9995:
                        continue
                    for fam in TP_FAMILIES:
                        tps = pick(fam, L, mm)
                        r = profit.simulate(A, k, entry, sl, tps, [(1.0, ("tp", tp_index(fam)))])
                        if r is not None:
                            row[f"{sk}|{fam}"] = r[0] - fee_rt
                if len(row) > 3:
                    s_rows.append(row)
            # ---- Fa ----
            for k in range(105, A["n"] - 1):
                if not (st[k] == 1 and st[k - 1] != 1):
                    continue
                entry = c[k]
                mm = profit.tp_levels(A, k, arrs, entry)
                L = levels_at(A, S, tf, k, entry)
                row = {"tf": tf, "ticker": t, "time": A["ends"][k]}
                r0 = profit.simulate(A, k, entry, 0.0, mm, [(1.0, ("fo",))])
                if r0 is None:
                    continue
                row["Fo"] = r0[0] - fee_rt
                for fam in FA_FAMILIES:
                    r = profit.simulate(A, k, entry, 0.0, pick(fam, L, mm), [(1.0, ("tpfo", tp_index(fam)))])
                    if r is not None:
                        row[fam + " ou Fo"] = r[0] - fee_rt
                fa_rows.append(row)
        if i % 10 == 0:
            print(f"  {i}/{len(ranked)} actions, {len(s_rows)} S, {len(fa_rows)} Fa, {int(time.time() - t0)} s", flush=True)
    return pd.DataFrame(s_rows), pd.DataFrame(fa_rows), len(ranked)


def ci(times, vals, rng, level):
    months = pd.PeriodIndex(pd.DatetimeIndex(times).tz_localize(None), freq="M")
    g = pd.DataFrame({"m": months, "r": vals}).groupby("m")["r"].agg(["sum", "count"])
    s, c = g["sum"].to_numpy(), g["count"].to_numpy()
    if len(s) < 6:
        return np.nan, np.nan
    idx = rng.integers(0, len(s), size=(BOOT, len(s)))
    m = s[idx].sum(1) / c[idx].sum(1)
    a = (100 - level) / 2
    return float(np.percentile(m, a)), float(np.percentile(m, 100 - a))


def compare(df, col, ref, rng):
    """Écarts appariés (col - ref) en apprentissage (IC 99 %) et en test (IC 95 %) + moyennes."""
    d = pd.DataFrame({"a": df[col], "b": df[ref], "time": df["time"]}).dropna()
    yr = pd.DatetimeIndex(d["time"]).year
    out = {"n": len(d), "mean": d["a"].mean()}
    for per, sel, lvl in (("tr", yr < SPLIT_YEAR, 99), ("te", yr >= SPLIT_YEAR, 95)):
        x = d[sel]
        if len(x) < 30:
            out[per] = None
            continue
        diff = (x["a"] - x["b"]).to_numpy()
        lo, hi = ci(x["time"], diff, rng, lvl)
        out[per] = (float(diff.mean()), lo, hi, len(x), float(x["a"].mean()))
    out["ok"] = bool(out.get("tr") and out.get("te") and out["tr"][1] > 0 and out["te"][1] > 0)
    return out


def report(s, fa, n, fee, rng):
    pct = robust.pct
    L = [f"# Supports et résistances de prix — {n} actions françaises, frais {fee * 100:.2f} % par ordre", "",
         "Écarts **appariés** avec la référence sur les mêmes entrées ; IC 99 % en apprentissage (jusqu'en 2015), 95 % en test (2016+). « Retenue » = écart > 0 dans les deux périodes "
         "(borne basse de l'IC > 0). TP « MM » = les 5 niveaux de l'application (MM/Tenkan) au 5e rang ; Swing-n / Donchian-n / Rond-n / POC-n / Prix-n / Tout-n = n-ième niveau de la famille.", ""]
    L += ["## S haussière : SL et TP (référence : SL atr1.5 + TP MM5)", ""]
    for tf in ("1d", "1w"):
        d = s[s["tf"] == tf]
        if len(d) < 50:
            continue
        L += [f"### {tf} — {len(d)} signaux S", "", "| SL | sortie | n | moyenne | apprent. : écart [IC 99 %] | test : écart [IC 95 %] | moyenne test | verdict |", "|---|---|---|---|---|---|---|---|"]
        ref = "atr1.5|MM"
        rows = []
        for sk in SL_KINDS:
            for fam in TP_FAMILIES:
                col = f"{sk}|{fam}"
                if col not in d or col == ref:
                    continue
                r = compare(d, col, ref, rng)
                if r["n"] >= 50:
                    rows.append((col, sk, fam, r))
        rd = compare(d, ref, ref, rng)
        L.append(f"| atr1.5 | MM5 (référence) | {rd['n']} | **{pct(rd['mean'])}** | – | – | {pct(rd['te'][4]) if rd.get('te') else '–'} | référence |")
        for col, sk, fam, r in sorted(rows, key=lambda x: -(x[3]["te"][0] if x[3].get("te") else -9)):
            tr, te = r.get("tr"), r.get("te")
            f = lambda v: f"{pct(v[0])} [{pct(v[1])} ; {pct(v[2])}]" if v else "–"
            L.append(f"| {sk} | {fam} | {r['n']} | {pct(r['mean'])} | {f(tr)} | {f(te)} | {pct(te[4]) if te else '–'} | {'**retenue**' if r['ok'] else 'non'} |")
        L.append("")
    L += ["## Fa : sortie « niveau de prix ou Fo » (référence : Fo seul)", ""]
    for tf in ("1d", "1w"):
        d = fa[fa["tf"] == tf]
        if len(d) < 50:
            continue
        L += [f"### {tf} — {len(d)} entrées Fa", "", "| sortie | n | moyenne | apprent. : écart [IC 99 %] | test : écart [IC 95 %] | moyenne test | verdict |", "|---|---|---|---|---|---|---|"]
        rd = compare(d, "Fo", "Fo", rng)
        L.append(f"| Fo seul (référence) | {rd['n']} | **{pct(rd['mean'])}** | – | – | {pct(rd['te'][4]) if rd.get('te') else '–'} | référence |")
        for fam in FA_FAMILIES:
            col = fam + " ou Fo"
            if col not in d:
                continue
            r = compare(d, col, "Fo", rng)
            tr, te = r.get("tr"), r.get("te")
            f = lambda v: f"{pct(v[0])} [{pct(v[1])} ; {pct(v[2])}]" if v else "–"
            L.append(f"| {col} | {r['n']} | {pct(r['mean'])} | {f(tr)} | {f(te)} | {pct(te[4]) if te else '–'} | {'**retenue**' if r['ok'] else 'non'} |")
        L.append("")
    L += ["Attention : plus de 100 combinaisons sont testées ; avec un IC à 99 % puis 95 %, une ou deux « retenues » peuvent encore sortir du hasard. Seule une famille cohérente (plusieurs niveaux voisins, 1d ET 1w) serait crédible.", ""]
    L += ["Lecture : si rien n'est « retenu », les supports / résistances de prix n'apportent pas d'amélioration démontrée par rapport aux moyennes mobiles actuelles."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=int, default=0)
    ap.add_argument("--fee", type=float, default=0.5)
    ap.add_argument("--out", default="sr_report.md")
    args = ap.parse_args()
    rng = np.random.default_rng(2026)
    s, fa, n = collect(args, rng)
    text = report(s, fa, n, args.fee / 100.0, rng)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
