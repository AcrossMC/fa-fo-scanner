"""
S hebdo + action à plus de 30 % sous son plus haut 52 sem. (S+H4), sortie Donchian-2 : que change un stop loss plus serré ou plus large ?

SL testés (fixés avant les résultats) : base = plus bas de la bougie HA précédente ; atr1 / atr1.5 / atr2 / atr3 = base, mais distance au SL au moins 1 / 1,5 / 2 / 3 fois l'ATR 14.
Mesures : % de trades stoppés, moyenne nette, médiane, perte moyenne, pire trade, et surtout le résultat PAR UNITÉ DE RISQUE (R = rendement / distance au SL) :
un stop serré permet de miser plus gros pour le même risque, donc seul le R compare vraiment les stops. « Hasard » = entrées au sort (même action, même année) avec le même SL.
"""
import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

import eu
import profit
import robust
import scanner as sc
import sr

MULTS = {"atr1": 1.0, "atr1.5": 1.5, "atr2": 2.0, "atr3": 3.0}
SLS = ["base"] + list(MULTS)
N_RANDOM = 5


def sims(A, arrs, S, k, fee_rt):
    entry = A["c"][k]
    if k < 105 or k + 1 >= A["n"]:
        return None
    L = sr.levels_at(A, S, "1w", k, entry)["Donchian"]
    if len(L) < 2:
        return None
    base, atr = A["hal"][k - 1], A["atr"][k]
    out = {}
    for name in SLS:
        sl = base if name == "base" else (min(base, entry - MULTS[name] * atr) if np.isfinite(atr) else np.nan)
        if not np.isfinite(sl) or sl >= entry * 0.9995:
            continue
        r = profit.simulate(A, k, entry, sl, L, [(1.0, ("tp", 1))])
        if r is not None:
            out[name] = (r[0] - fee_rt, bool(r[1]), (entry - sl) / entry)
    return out


def collect(args, rng, universe=None, now=None):
    fee_rt = 2 * args.fee / 100.0
    universe = universe or {**eu.load_eu(), **sc.load_universe()}
    daily = sc.download(list(universe), "max", "1d")
    for t in list(daily):
        daily[t], _ = profit.clean_series(daily[t])
    turn = {t: sc.turnover(daily[t]) for t in daily}
    ranked = sorted((t for t in daily if turn[t] >= sc.MIN_TURNOVER_EUR), key=lambda t: turn[t], reverse=True)
    now = now or pd.Timestamp.now(tz=sc.PARIS)
    rows, t0 = [], time.time()
    for i, t in enumerate(ranked, 1):
        arrs = {}
        for tf in ("1w", "1mo", "3mo"):
            try:
                a = profit.prep(sc.aggregate(daily[t], tf), tf, now)
            except Exception:
                a = None
            if a is not None:
                arrs[tf] = a
        A = arrs.get("1w")
        if A is None:
            continue
        S = sr.sr_prep(A, "1w")
        years, by = robust.random_year_index(A)
        c, h = A["c"], A["h"]
        n_e = 0
        for k in np.flatnonzero(A["up"]):
            if k < 105 or k + 1 >= A["n"] or c[k] / h[k - 52:k + 1].max() - 1 > -0.30:
                continue
            r = sims(A, arrs, S, k, fee_rt)
            if not r:
                continue
            n_e += 1
            for sl, (ret, hit, risk) in r.items():
                rows.append({"kind": "S", "sl": sl, "ticker": t, "time": A["ends"][k], "ret": ret, "hit": hit, "risk": risk})
            cand = [kk for kk in by.get(A["ends"][k].year, []) if kk >= 105]
            for kk in (rng.choice(cand, size=min(N_RANDOM, len(cand)), replace=False) if cand else []):
                r2 = sims(A, arrs, S, kk, fee_rt)
                for sl, (ret, hit, risk) in (r2 or {}).items():
                    rows.append({"kind": "H", "sl": sl, "ticker": t, "time": A["ends"][kk], "ret": ret, "hit": hit, "risk": risk})
        if i % 25 == 0:
            print(f"  {i}/{len(ranked)} actions, {len(rows)} lignes, {int(time.time() - t0)} s", flush=True)
    return pd.DataFrame(rows), len(ranked)


def report(df, n, fee, rng):
    pct = robust.pct
    yr = pd.DatetimeIndex(df["time"]).year
    L = [f"# S+H4 avec sortie Donchian-2 : effet du stop loss — {n} actions liquides, frais {fee * 100:.2f} % par ordre", "",
         "SL : base = plus bas de la bougie HA précédente ; atrX = base mais au moins X fois l'ATR sous l'entrée. **R** = rendement / distance au SL (résultat par unité de risque : "
         "à risque égal par trade, c'est ce qui compte). **Perte si stop** = rendement moyen des trades stoppés. IC par rééchantillonnage des mois.", "",
         "| SL | période | trades | stoppés | distance moyenne au SL | moyenne nette | IC | médiane | perte si stop | pire trade | **R moyen** | hasard | avantage |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for sl in SLS:
        for lab, sel in (("tout", yr >= 0), ("avant 2016", yr < 2016), ("2016+", yr >= 2016)):
            d = df[(df["kind"] == "S") & (df["sl"] == sl) & sel]
            hh = df[(df["kind"] == "H") & (df["sl"] == sl) & sel]
            if len(d) < 30:
                continue
            lo, hi = sr.ci(d["time"], d["ret"].to_numpy(), rng, 95)
            stop = d[d["hit"]]
            R = (d["ret"] / d["risk"]).mean()
            L.append(f"| **{sl}** | {lab} | {len(d)} | {pct(d['hit'].mean(), 0)} | {pct(d['risk'].mean())} | **{pct(d['ret'].mean())}** | [{pct(lo)} ; {pct(hi)}] | {pct(d['ret'].median())} | "
                     f"{pct(stop['ret'].mean()) if len(stop) else '–'} | {pct(d['ret'].min())} | **{R:.2f}** | {pct(hh['ret'].mean()) if len(hh) else '–'} | {pct(d['ret'].mean() - hh['ret'].mean()) if len(hh) else '–'} |")
    L += ["", "Lecture : un stop plus serré réduit la perte par trade stoppé mais en stoppe davantage (bruit) et coupe des rebonds qui auraient atteint l'objectif. "
          "Si le R moyen baisse quand le stop se rapproche, le stop serré n'améliore pas le rapport gain / risque ; s'il monte, il faut en tenir compte en dimensionnant la ligne selon la distance au SL."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fee", type=float, default=0.398)
    ap.add_argument("--out", default="sl_report.md")
    args = ap.parse_args()
    rng = np.random.default_rng(2026)
    df, n = collect(args, rng)
    text = report(df, n, args.fee / 100.0, rng)
    Path(args.out).write_text(text, encoding="utf-8")
    df.to_csv("sl_trades.csv", index=False)
    print(text)


if __name__ == "__main__":
    main()
