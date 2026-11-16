"""
Contrôle de robustesse de l'étude du profit : les résultats de profit.py viennent-ils d'un vrai avantage, ou de la hausse générale, de quelques
valeurs extrêmes, de quelques années, de données douteuses ou du hasard ?

Pour chaque stratégie (Fa par UT ; S par UT avec les meilleures combinaisons SL / TP) :
  - comparaison avec des entrées au hasard sur la même action, la même UT et la MÊME ANNÉE (même sortie : même durée pour Fa, mêmes SL / TP pour S) ;
  - marge d'erreur à 95 % (rééchantillonnage par mois : les signaux d'un même mois ne sont pas indépendants) ;
  - moyenne sans les 1 % de trades les plus extrêmes, médiane ;
  - résultat par année (part d'années positives) ;
  - dépendance à quelques actions (sans la meilleure, part des 10 meilleures) ;
  - liste des trades les plus extrêmes, pour repérer d'éventuelles erreurs de données.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

import profit
import scanner as sc

BEST = [("atr1.5", "TP5"), ("atr1", "TP5"), ("atr1.5", "TP4"), ("base", "TP5"), ("atr1.5", "TP3"), ("app", "TP5")]
N_RANDOM = 3
BOOT = 2000


def pct(x, d=2):
    return "–" if x is None or not np.isfinite(x) else f"{x * 100:.{d}f} %"


def boot_ci(times, rets, rng):
    """IC 95 % de la moyenne, en rééchantillonnant les mois (les trades d'un même mois sont liés à la même conjoncture)."""
    months = pd.PeriodIndex(pd.DatetimeIndex(times).tz_localize(None), freq="M")
    df = pd.DataFrame({"m": months, "r": rets}).groupby("m")["r"].agg(["sum", "count"])
    s, c = df["sum"].to_numpy(), df["count"].to_numpy()
    if len(s) < 6:
        return (np.nan, np.nan)
    idx = rng.integers(0, len(s), size=(BOOT, len(s)))
    m = s[idx].sum(axis=1) / c[idx].sum(axis=1)
    return (float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5)))


def trimmed(r, q=0.01):
    r = np.sort(np.asarray(r, dtype=float))
    k = int(len(r) * q)
    return float(r[k:len(r) - k].mean()) if len(r) > 2 * k + 1 else float(r.mean())


def random_year_index(A):
    years = np.array([e.year for e in A["ends"]])
    by = {}
    for k, y in enumerate(years):
        if k >= 30 and k + 1 < A["n"]:
            by.setdefault(y, []).append(k)
    return years, by


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
    turn = {t: sc.turnover(daily[t]) for t in daily}
    ranked = sorted((t for t in daily if turn[t] >= sc.MIN_TURNOVER_EUR), key=lambda t: turn[t], reverse=True)
    if args.tickers:
        ranked = ranked[:args.tickers]
    hourly = {} if args.no_4h else sc.download(ranked, "2y", "1h")
    now = pd.Timestamp.now(tz=sc.PARIS)
    print(f"{len(ranked)} actions.", flush=True)
    rows, base_rows, t0 = [], [], time.time()
    for i, t in enumerate(ranked, 1):
        frames = {"1d": daily[t], "1w": sc.aggregate(daily[t], "1w"), "1mo": sc.aggregate(daily[t], "1mo"), "3mo": sc.aggregate(daily[t], "3mo")}
        if t in hourly and len(hourly[t]):
            frames["4h"] = sc.aggregate(hourly[t], "4h")
        arrs = {}
        for tf, df in frames.items():
            try:
                a = profit.prep(df, tf, now)
            except Exception:
                a = None
            if a is not None:
                arrs[tf] = a
        for tf in profit.TRADE_TFS:
            A = arrs.get(tf)
            if A is None:
                continue
            years, by = random_year_index(A)
            fa, _ = profit.fa_trades(t, A, arrs)
            for x in fa:
                rows.append({"strat": f"Fa|{tf}", "tf": tf, "ticker": t, "time": x["entry_time"], "ret": x["ret"] - fee_rt, "bars": x["bars"]})
                cand = by.get(x["entry_time"].year, [])
                cand = [k for k in cand if k + x["bars"] < A["n"]]
                for k in (rng.choice(cand, size=min(N_RANDOM, len(cand)), replace=False) if cand else []):
                    base_rows.append({"strat": f"Fa|{tf}", "ticker": t, "time": A["ends"][k], "ret": A["c"][k + x["bars"]] / A["c"][k] - 1 - fee_rt})
            s_list, _ = profit.s_trades(t, A, arrs, fee_rt)
            for x in s_list:
                for sv, name in BEST:
                    if (sv, name) in x["res"]:
                        rows.append({"strat": f"S|{tf}|{sv}|{name}", "tf": tf, "ticker": t, "time": x["entry_time"], "ret": x["res"][(sv, name)][0]})
                cand = by.get(x["entry_time"].year, [])
                for k in (rng.choice(cand, size=min(N_RANDOM, len(cand)), replace=False) if cand else []):
                    entry = A["c"][k]
                    tps, sls = profit.tp_levels(A, k, arrs, entry), profit.sl_levels(A, k, arrs, entry)
                    for sv, name in BEST:
                        sl = sls[sv]
                        if not np.isfinite(sl) or sl >= entry * 0.9995:
                            continue
                        r = profit.simulate(A, k, entry, sl, tps, profit.SCHEMES[name])
                        if r is not None:
                            base_rows.append({"strat": f"S|{tf}|{sv}|{name}", "ticker": t, "time": A["ends"][k], "ret": r[0] - fee_rt})
        if i % 20 == 0:
            print(f"  {i}/{len(ranked)} actions, {len(rows)} trades, {int(time.time() - t0)} s", flush=True)
    return pd.DataFrame(rows), pd.DataFrame(base_rows), len(ranked)


def analyse(rows, base, rng):
    L = []
    strats = [s for s in sorted(rows["strat"].unique(), key=lambda s: (s.split("|")[0] != "Fa", ["4h", "1d", "1w", "1mo"].index(s.split("|")[1]), s))]
    L += ["## 1. Résumé par stratégie", "",
          "« Hasard » = mêmes actions, même UT, même année, entrées tirées au sort avec la même sortie (même durée pour Fa ; mêmes SL et TP pour S). "
          "« Avantage » = moyenne de la stratégie − moyenne du hasard. IC = intervalle de confiance à 95 % de la moyenne (rééchantillonnage par mois). "
          "Un avantage n'est crédible que si l'IC est entièrement au-dessus de 0 ET si l'avantage sur le hasard est positif.", "",
          "| stratégie | trades | moyenne | IC 95 % | médiane | moyenne sans 1 % extrêmes | hasard | avantage | années positives | sans la meilleure action | 10 meilleures actions (part du gain) |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in strats:
        d = rows[rows["strat"] == s]
        if len(d) < 30:
            continue
        r = d["ret"].to_numpy()
        lo, hi = boot_ci(d["time"], r, rng)
        b = base[base["strat"] == s]["ret"]
        yr = d.assign(y=pd.DatetimeIndex(d["time"]).year).groupby("y")["ret"].mean()
        per_stock = d.groupby("ticker")["ret"].sum().sort_values(ascending=False)
        rest = d[d["ticker"] != per_stock.index[0]]["ret"].mean() if len(per_stock) > 1 else np.nan
        tot = per_stock.sum()
        share = per_stock.head(10).sum() / tot if tot > 0 else np.nan
        label = s.replace("|", " · ")
        L.append(f"| {label} | {len(d)} | **{pct(r.mean())}** | [{pct(lo)} ; {pct(hi)}] | {pct(np.median(r))} | {pct(trimmed(r))} | "
                 f"{pct(b.mean()) if len(b) else '–'} | **{pct(r.mean() - b.mean()) if len(b) else '–'}** | {int((yr > 0).sum())}/{len(yr)} | {pct(rest)} | "
                 f"{pct(share, 0) if np.isfinite(share) else '–'} |")
    L += ["", "## 2. Résultat moyen par année (stratégies principales)", ""]
    main = [s for s in strats if s in ("Fa|1d", "Fa|1w", "S|1d|atr1.5|TP5", "S|1w|atr1.5|TP5", "S|1d|atr1|TP5", "S|1w|atr1|TP5")]
    years = sorted(set(pd.DatetimeIndex(rows["time"]).year))
    L.append("| année | " + " | ".join(s.replace("|", " · ") for s in main) + " |")
    L.append("|---|" + "---|" * len(main))
    for y in years:
        cells = []
        for s in main:
            d = rows[(rows["strat"] == s) & (pd.DatetimeIndex(rows["time"]).year == y)]
            cells.append(f"{pct(d['ret'].mean(), 1)} ({len(d)})" if len(d) >= 5 else "–")
        if any(c != "–" for c in cells):
            L.append(f"| {y} | " + " | ".join(cells) + " |")
    L += ["", "## 3. Trades les plus extrêmes (à vérifier : erreurs de données possibles : splits, cotations anciennes)", "",
          "| stratégie | action | date d'entrée | rendement net |", "|---|---|---|---|"]
    ext = rows.assign(a=rows["ret"].abs()).sort_values("a", ascending=False).head(25)
    for _, x in ext.iterrows():
        L.append(f"| {x['strat'].replace('|', ' · ')} | {x['ticker']} | {pd.Timestamp(x['time']).strftime('%Y-%m-%d')} | {pct(x['ret'], 0)} |")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=int, default=0)
    ap.add_argument("--fee", type=float, default=0.5)
    ap.add_argument("--no-4h", action="store_true")
    ap.add_argument("--out", default="robust_report.md")
    args = ap.parse_args()
    rng = np.random.default_rng(2026)
    rows, base, n = collect(args, rng)
    head = [f"# Contrôle de robustesse — {n} actions françaises, frais {args.fee:.2f} % par ordre", ""]
    text = "\n".join(head) + analyse(rows, base, rng)
    Path(args.out).write_text(text, encoding="utf-8")
    rows.to_csv("robust_trades.csv", index=False)
    print(text)


if __name__ == "__main__":
    main()
