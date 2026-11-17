"""
Question : entrer AVANT que la Fa soit validée (quand la probabilité annoncée par le scanner dépasse un seuil) et sortir AVANT le Fo (quand la probabilité de Fo
dépasse un seuil) améliore-t-il le résultat ?

Méthode : on rejoue 2 ans d'historique horaire, comme le test de fiabilité, avec le VRAI calcul de probabilité du scanner (Monte-Carlo) à 11h, 14h et 15h de chaque
séance, en unité de temps 1 jour.
  Référence (B) : entrée à la clôture de la bougie Fa validée, sortie à la clôture de la bougie Fo.
  Entrée anticipée (E) : à 11h, 14h ou 15h, si p(Fa) >= seuil et que l'action n'est pas déjà en Fa -> achat au cours de cet instant. Si la bougie ne clôture finalement pas en Fa
                         (fausse alerte), on revend à la clôture du jour (perte réelle, frais payés).
  Sortie anticipée (X) : en position, à 11h, 14h ou 15h, si p(Fo) >= seuil -> vente au cours de cet instant (même si le Fo ne se réalise finalement pas).
Frais : --fee par ordre, déjà déduits à chaque aller-retour (les fausses alertes en paient aussi).
Limite : 2 ans seulement, donc peu de marge d'erreur ; l'historique horaire ne permet pas davantage.
"""
import argparse
import multiprocessing as mp
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

import backtest as bt
import profit
import robust
import scanner as sc

SNAPS = (11, 14, 15)
ENTRY = ["B", 0.6, 0.7, 0.8]
EXIT = ["Fo", 0.6, 0.7, 0.8]


def early_ticker(args):
    t, hourly, seed, fee_rt = args
    rng = np.random.default_rng(seed)
    hourly = hourly.dropna(subset=["Open", "High", "Low", "Close"])
    hourly = hourly[hourly.index.tz_localize(None).weekday < 5]
    if len(hourly) < 500:
        return t, []
    d_full = bt.daily_from_hourly(hourly)
    now = pd.Timestamp.now(tz=sc.PARIS)
    A = profit.prep(d_full, "1d", now)
    if A is None:
        return t, []
    st, c, n = A["st"], A["c"], A["n"]
    days = pd.DatetimeIndex(A["ends"]).normalize()
    naive_h = hourly.index.tz_localize(None)
    memo = {}

    def snap(k, hr):
        key = (k, hr)
        if key in memo:
            return memo[key]
        day = d_full.index[d_full.index.tz_localize(None).normalize() == days[k].tz_localize(None)]
        out = None
        if len(day):
            day = day[0]
            dn = day.tz_localize(None)
            ok = (naive_h.normalize() == dn) & (naive_h + pd.Timedelta(hours=1) <= dn + pd.Timedelta(hours=hr))
            part = bt.merge_rows(hourly[ok], day)
            if part is not None:
                prev = d_full[d_full.index < day]
                if len(prev) >= sc.MIN_BARS:
                    res = sc.analyze_tf(pd.concat([prev, part]), "1d", day.replace(hour=hr, minute=0), rng)
                    if res is not None and res.get("forming"):
                        out = (res.get("p_fa") or 0.0, res.get("p_fo") or 0.0, float(part["Close"].iloc[-1]))
        memo[key] = out
        return out

    start = sc.MIN_BARS + 5
    trades = []
    for entry in ENTRY:
        for ex in EXIT:
            pos = None
            for k in range(start, n):
                if pos is None:
                    if st[k - 1] == 1:
                        continue
                    if entry == "B":
                        if st[k] == 1:
                            pos = {"px": c[k], "k": k, "early": False}
                        continue
                    for hr in SNAPS:
                        s = snap(k, hr)
                        if s and s[0] >= entry:
                            pos = {"px": s[2], "k": k, "early": True, "validated": bool(st[k] == 1), "valpx": c[k]}
                            break
                    if pos is not None and not pos["validated"]:           # fausse alerte : vente à la clôture du jour
                        trades.append({"ticker": t, "entry": entry, "exit": ex, "time": A["ends"][k], "ret": c[k] / pos["px"] - 1 - fee_rt,
                                       "early": True, "cut": True, "gain_entry": np.nan})
                        pos = None
                    continue
                sold = None
                if ex != "Fo":
                    for hr in SNAPS:
                        s = snap(k, hr)
                        if s and s[1] >= ex:
                            sold = s[2]
                            break
                if sold is None and st[k] == -1:
                    sold = c[k]
                if sold is not None:
                    trades.append({"ticker": t, "entry": entry, "exit": ex, "time": A["ends"][k], "ret": sold / pos["px"] - 1 - fee_rt,
                                   "early": pos["early"], "cut": False,
                                   "gain_entry": (pos["valpx"] / pos["px"] - 1) if pos["early"] else np.nan})
                    pos = None
    return t, trades


def report(df, n_stocks, fee, rng):
    pct = robust.pct
    L = [f"# Entrer / sortir avant la validation — {n_stocks} actions, 1 jour, 2 ans d'historique horaire, frais {fee * 100:.2f} % par ordre", "",
         "B = entrée à la clôture de la Fa validée. E0,6 / E0,7 / E0,8 = entrée dès que p(Fa) >= 60 / 70 / 80 % (11h, 14h ou 15h). Fo = sortie à la clôture de la bougie Fo ; "
         "X0,6 / X0,7 / X0,8 = sortie dès que p(Fo) >= 60 / 70 / 80 %. Les fausses alertes d'entrée (la bougie ne devient pas Fa) sont revendues à la clôture, frais payés. "
         "**Total** = somme des rendements nets de tous les trades de la période (même mise à chaque fois) : c'est la mesure à comparer, car les stratégies anticipées font plus de trades. "
         "**Écart / mois** = différence de total mensuel avec la référence (B + Fo), avec marge d'erreur à 95 % par rééchantillonnage des mois.", "",
         "| entrée | sortie | trades | fausses alertes | moyenne / trade | médiane | gagnants | total | écart / mois vs référence | IC 95 % |", "|---|---|---|---|---|---|---|---|---|---|"]
    df = df.copy()
    df["month"] = pd.PeriodIndex(pd.DatetimeIndex(df["time"]).tz_localize(None), freq="M")
    ref = df[(df["entry"] == "B") & (df["exit"] == "Fo")].groupby("month")["ret"].sum()
    months = sorted(set(df["month"]))
    ref = ref.reindex(months, fill_value=0.0)
    for e in ENTRY:
        for x in EXIT:
            d = df[(df["entry"] == e) & (df["exit"] == x)]
            if not len(d):
                continue
            m = d.groupby("month")["ret"].sum().reindex(months, fill_value=0.0)
            diff = (m - ref).to_numpy()
            idx = rng.integers(0, len(diff), size=(2000, len(diff)))
            bs = diff[idx].mean(axis=1)
            lo, hi = np.percentile(bs, [2.5, 97.5])
            lab = lambda v, p: "B" if v == "B" else ("Fo" if v == "Fo" else f"{p}{str(v).replace('.', ',')}")
            L.append(f"| {lab(e, 'E')} | {lab(x, 'X')} | {len(d)} | {int(d['cut'].sum())} | {pct(d['ret'].mean())} | {pct(d['ret'].median())} | "
                     f"{pct((d['ret'] > 0).mean(), 0)} | **{d['ret'].sum() * 100:.0f} pts** | {pct(diff.mean())} | [{pct(lo)} ; {pct(hi)}] |")
    L += ["", "### Entrées anticipées : combien gagne-t-on à entrer plus tôt, et combien de fausses alertes ?", "",
          "| seuil d'entrée | entrées | validées en Fa | fausses alertes | gain moyen d'entrer plus tôt (si validée) |", "|---|---|---|---|---|"]
    for e in ENTRY[1:]:
        d = df[(df["entry"] == e) & (df["exit"] == "Fo") & df["early"]]
        if len(d):
            v = d[~d["cut"]]
            L.append(f"| {int(e * 100)} % | {len(d)} | {len(v)} ({len(v) / len(d) * 100:.0f} %) | {int(d['cut'].sum())} | {pct(v['gain_entry'].mean())} |")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=int, default=60, help="nombre d'actions (les plus liquides) ; 0 = toutes")
    ap.add_argument("--fee", type=float, default=0.5)
    ap.add_argument("--out", default="early_report.md")
    args = ap.parse_args()
    fee_rt = 2 * args.fee / 100.0
    universe = sc.load_universe()
    import json
    try:
        cache = json.loads((sc.HERE / "sectors.json").read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    ov = sc.load_pea_override()
    universe = {t: n for t, n in universe.items() if sc.pea_eligible(t, cache, ov)}
    hourly = sc.download(list(universe), "2y", "1h")
    hourly = {t: profit.clean_series(df)[0] for t, df in hourly.items()}
    turn = {t: sc.turnover(bt.daily_from_hourly(hourly[t])) for t in hourly}
    ranked = sorted((t for t in hourly if turn[t] >= sc.MIN_TURNOVER_EUR), key=lambda t: turn[t], reverse=True)
    if args.tickers:
        ranked = ranked[:args.tickers]
    print(f"{len(ranked)} actions, rejeu en cours…", flush=True)
    jobs = [(t, hourly[t], 4000 + i, fee_rt) for i, t in enumerate(ranked)]
    rows, t0 = [], time.time()
    with mp.Pool(max(1, os.cpu_count() or 1)) as pool:
        for i, (t, tr) in enumerate(pool.imap_unordered(early_ticker, jobs), 1):
            rows += tr
            if i % 5 == 0:
                print(f"  {i}/{len(ranked)} actions, {len(rows)} trades, {int(time.time() - t0)} s", flush=True)
    df = pd.DataFrame(rows)
    text = report(df, len(ranked), args.fee / 100.0, np.random.default_rng(7))
    Path(args.out).write_text(text, encoding="utf-8")
    df.to_csv("early_trades.csv", index=False)
    print(text)


if __name__ == "__main__":
    main()
