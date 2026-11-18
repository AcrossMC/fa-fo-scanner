"""
Test fixé à l'avance : couper un trade S+H4 hebdo (SL 1,5 ATR, objectif Donchian-2) s'il est encore ouvert ET en Fo hebdomadaire après N semaines.

Variantes : N = 4, 8 (hypothèse principale), 13 ; et « Fo à 8 semaines ET en perte ». Vente à la clôture de la semaine N (même nombre d'ordres que la référence).
Comparaison appariée avec la référence (mêmes entrées ; apprentissage jusqu'en 2015 IC 99 %, test 2016+ IC 95 %). Retenue seulement si l'écart est > 0 avec IC > 0 dans les deux périodes.
Mesure complémentaire : rendement par semaine d'immobilisation du capital (somme des rendements / somme des durées), car couper libère du capital pour un autre trade.
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
import trade

VARS = {"Référence": None, "Coupe si Fo à 4 sem.": (4, False), "Coupe si Fo à 8 sem.": (8, False), "Coupe si Fo à 13 sem.": (13, False), "Coupe si Fo et en perte à 8 sem.": (8, True)}


def collect(args, rng, universe=None, now=None):
    fee = args.fee / 100.0
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
        c, h = A["c"], A["h"]
        for k in np.flatnonzero(A["up"]):
            if k < 105 or k + 1 >= A["n"] or c[k] / h[k - 52:k + 1].max() - 1 > -args.h4:
                continue
            entry = c[k]
            L = sr.levels_at(A, S, "1w", k, entry)["Donchian"]
            base, atr = A["hal"][k - 1], A["atr"][k]
            if len(L) < 2 or not np.isfinite(atr):
                continue
            sl = min(base, entry - 1.5 * atr)
            if sl >= entry * 0.9995:
                continue
            legs = [{"frac": 1.0, "sl": sl, "tp": L[1]}]
            row = {"ticker": t, "market": eu.market_of(t), "time": A["ends"][k], "risk": float((entry - sl) / entry)}
            for name, fc in VARS.items():
                r = trade.manage(A, k, entry, legs, focut=fc)
                if r is not None:
                    row[name] = r[0] - (1 + r[1]) * fee
                    row["bars:" + name] = r[2] - k
                    row["cut:" + name] = r[3] == "FO"
            if "Référence" in row:
                rows.append(row)
        if i % 25 == 0:
            print(f"  {i}/{len(ranked)} actions, {len(rows)} trades, {int(time.time() - t0)} s", flush=True)
    return pd.DataFrame(rows), len(ranked)


def report(df, n, fee, rng):
    pct = robust.pct
    L = [f"# Couper un S+H4 hebdo en Fo après N semaines — {len(df)} trades, {n} actions, frais {fee * 100:.2f} % par ordre", "",
         "Règle fixée avant les résultats (voir l'en-tête de `fo_cut.py`). **Coupés** = part des trades vendus par cette règle. **Rendement / semaine** = somme des rendements nets / somme des durées (capital immobilisé).", "",
         "| variante | trades | coupés | moyenne nette | médiane | durée moyenne (sem.) | rendement / semaine | écart apprentissage [IC 99 %] | écart test [IC 95 %] | verdict |", "|---|---|---|---|---|---|---|---|---|---|"]
    for v in VARS:
        d = df[df[v].notna()]
        wk = d[v].sum() / d["bars:" + v].sum()
        if v == "Référence":
            L.append(f"| **Référence** | {len(d)} | – | **{pct(d[v].mean())}** | {pct(d[v].median())} | {d['bars:' + v].mean():.1f} | {pct(wk)} | – | – | référence |")
            continue
        r = sr.compare(df, v, "Référence", rng)
        f = lambda z: f"{pct(z[0])} [{pct(z[1])} ; {pct(z[2])}]" if z else "–"
        L.append(f"| {v} | {len(d)} | {pct(d['cut:' + v].mean(), 0)} | **{pct(d[v].mean())}** | {pct(d[v].median())} | {d['bars:' + v].mean():.1f} | {pct(wk)} | {f(r.get('tr'))} | {f(r.get('te'))} | {'**retenue**' if r['ok'] else 'non'} |")
    v = "Coupe si Fo à 8 sem."
    L += ["", "## Hypothèse principale (Fo à 8 semaines) selon la place", "", "| place | trades | coupés | écart moyen avec la référence | IC 95 % |", "|---|---|---|---|---|"]
    for lab, sel in (("Paris", df["market"].str.startswith("Paris")), ("Hors Paris", ~df["market"].str.startswith("Paris"))):
        d = df[sel & df[v].notna()]
        if len(d) < 30:
            continue
        diff = (d[v] - d["Référence"]).to_numpy()
        lo, hi = sr.ci(d["time"], diff, rng, 95)
        L.append(f"| {lab} | {len(d)} | {pct(d['cut:' + v].mean(), 0)} | **{pct(diff.mean())}** | [{pct(lo)} ; {pct(hi)}] |")
    L += ["", "Lecture : couper n'est utile que si l'écart est positif dans les deux périodes et dans les deux groupes de places ; le rendement par semaine dit si le capital libéré compense un éventuel manque à gagner."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fee", type=float, default=0.398)
    ap.add_argument("--h4", type=float, default=0.30)
    ap.add_argument("--out", default="fo_cut_report.md")
    args = ap.parse_args()
    rng = np.random.default_rng(2026)
    df, n = collect(args, rng)
    text = report(df, n, args.fee / 100.0, rng)
    Path(args.out).write_text(text, encoding="utf-8")
    df.to_csv("fo_cut_trades.csv", index=False)
    print(text)


if __name__ == "__main__":
    main()
