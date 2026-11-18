"""
Quelle taille de position pour S+H4 hebdo (SL 1,5 ATR, objectif Donchian-2) avec 5 000 € et 8 lignes simultanées au plus ?

Méthode : on prend tous les trades historiques S+H4 (mêmes que fo_cut.py, règle simple SL / objectif fixes), on rejoue le portefeuille semaine par semaine (8 lignes maximum,
ordre aléatoire quand plus de signaux que de places), puis on tire au hasard des années (avec remise) pour fabriquer 1 000 « futurs » de 15 ans.
Frais : 1,99 € par ordre jusqu'à 500 €, 0,5 % au-delà (aller-retour = 2 ordres). Le rendement brut de chaque trade est reconstitué à partir du net de fo_cut (frais de 0,398 % x 2 ajoutés).
Politiques testées (fixées avant les résultats) : mise fixe 300 / 500 / 625 € ; part du capital 1/8, 1/12, 1/16 par ligne ; risque par trade 0,5 % / 1 % / 2 % du capital (taille = risque / distance au SL, plafonnée à 1/8 du capital).
Limites : la valeur du portefeuille n'est mise à jour qu'à la sortie des trades (le creux réel peut être plus profond) ; les gains viennent de rares trades très gagnants, donc les résultats varient beaucoup d'un tirage à l'autre.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import fo_cut
import robust

CAP0 = 5000.0
MAX_LINES = 8
YEARS = 15
N_PATHS = 1000
FEE_FIXED, FEE_PCT, FEE_THRESHOLD = 1.99, 0.005, 500.0
POLICIES = {"fixe 300 €": ("fix", 300), "fixe 500 €": ("fix", 500), "fixe 625 €": ("fix", 625),
            "1/8 du capital": ("frac", 1 / 8), "1/12 du capital": ("frac", 1 / 12), "1/16 du capital": ("frac", 1 / 16),
            "risque 0,5 %": ("risk", 0.005), "risque 1 %": ("risk", 0.01), "risque 2 %": ("risk", 0.02)}


def fee_rt(size):
    one = FEE_FIXED if size <= FEE_THRESHOLD else FEE_PCT * size
    return 2 * one / size


def size_for(pol, equity, risk):
    kind, v = pol
    cap = equity / MAX_LINES
    if kind == "fix":
        return v
    if kind == "frac":
        return v * equity
    return min(v * equity / max(risk, 0.02), cap)


def replay(tr, pol, rng, fee0):
    """tr : DataFrame trié (entry, exit, gross, risk). Renvoie capital final, plus bas relatif, nb de trades pris, frais payés."""
    tr = tr.sample(frac=1, random_state=int(rng.integers(1 << 30))).sort_values("entry", kind="stable")
    E, peak, mdd, taken, fees = CAP0, CAP0, 0.0, 0, 0.0
    cash, openp = CAP0, []
    for i, row in enumerate(tr.itertuples(index=False)):
        still = []
        for p in openp:                                    # sorties avant cette entrée
            if p[0] <= row.entry:
                cash += p[1] + p[2]
                E = cash + sum(q[1] for q in openp if q is not p and q[0] > row.entry)
            else:
                still.append(p)
        openp = still
        equity = cash + sum(q[1] for q in openp)
        peak = max(peak, equity)
        mdd = max(mdd, 1 - equity / peak)
        if len(openp) >= MAX_LINES:
            continue
        size = min(size_for(pol, equity, row.risk), cash)
        if size < 100:
            continue
        f = fee_rt(size)
        openp.append((row.exit, size, size * (row.gross - f)))
        cash -= size
        taken += 1
        fees += size * f
    for p in openp:
        cash += p[1] + p[2]
    equity = cash
    peak = max(peak, equity)
    mdd = max(mdd, 1 - equity / peak)
    return equity, mdd, taken, fees


def make_paths(tr, rng):
    tr = tr.copy()
    tr["year"] = pd.DatetimeIndex(tr["entry"]).year
    years = sorted(tr["year"].unique())
    groups = {y: tr[tr["year"] == y] for y in years}
    base = pd.Timestamp("2000-01-01")
    for _ in range(N_PATHS):
        parts = []
        for k, y in enumerate(rng.choice(years, size=YEARS, replace=True)):
            g = groups[y].copy()
            off = (pd.Timestamp(year=int(y), month=1, day=1) - base).days
            shift = pd.Timedelta(days=k * 366 - off)
            g["entry"] = (pd.DatetimeIndex(g["entry"]).tz_localize(None) + shift)
            g["exit"] = g["entry"] + pd.to_timedelta(g["weeks"] * 7, unit="D")
            parts.append(g[["entry", "exit", "gross", "risk"]])
        yield pd.concat(parts)


def report(df, rng):
    pct = robust.pct
    tr = pd.DataFrame({"entry": pd.DatetimeIndex(df["time"]).tz_localize(None), "weeks": df["bars:Référence"].astype(float), "risk": df["risk"].astype(float),
                       "gross": df["Référence"].astype(float) + 2 * fo_cut_fee})
    tr["exit"] = tr["entry"] + pd.to_timedelta(tr["weeks"] * 7, unit="D")
    res = {k: {"cagr": [], "mdd": [], "taken": [], "fee": [], "end": []} for k in POLICIES}
    for path in make_paths(tr, rng):
        seed = int(rng.integers(1 << 30))
        for k, pol in POLICIES.items():
            e, mdd, taken, fees = replay(path, pol, np.random.default_rng(seed), None)
            res[k]["end"].append(e)
            res[k]["cagr"].append((max(e, 1.0) / CAP0) ** (1 / YEARS) - 1)
            res[k]["mdd"].append(mdd)
            res[k]["taken"].append(taken / YEARS)
            res[k]["fee"].append(fees / YEARS)
    L = [f"# Taille de position pour S+H4 hebdo — capital {CAP0:.0f} €, {MAX_LINES} lignes au plus, {N_PATHS} futurs de {YEARS} ans (années tirées au hasard)", "",
         f"Base : {len(tr)} trades historiques. Frais : 1,99 € par ordre jusqu'à 500 €, 0,5 % au-delà. **CAGR** = rendement annuel moyen composé ; **creux max** = plus forte baisse depuis un sommet (calculée sur le capital réalisé) ; "
         "**perdants** = part des futurs finissant sous le capital de départ.", "",
         "| politique | CAGR médian | CAGR 5 % pires | CAGR 5 % meilleurs | creux max médian | creux max (95e centile) | futurs perdants | trades / an | frais / an |", "|---|---|---|---|---|---|---|---|---|"]
    for k in POLICIES:
        r = res[k]
        e = np.array(r["end"]); c = np.array(r["cagr"]); m = np.array(r["mdd"])
        L.append(f"| **{k}** | **{pct(np.median(c))}** | {pct(np.percentile(c, 5))} | {pct(np.percentile(c, 95))} | {pct(np.median(m), 0)} | {pct(np.percentile(m, 95), 0)} | {pct((e < CAP0).mean(), 0)} | {np.mean(r['taken']):.0f} | {np.mean(r['fee']):.0f} € |")
    L += ["", "Lecture : la « meilleure » taille dépend de ce que tu peux supporter. Compare le CAGR au creux max : une taille qui double le gain mais triple le creux n'est pas forcément préférable. "
          "Au-dessus de 500 € les frais passent à 0,5 % par ordre ; en dessous de 300 € le coût fixe de 1,99 € pèse davantage. Ces chiffres supposent que le passé se répète (même ordre de grandeur de gains rares) : "
          "ils servent à comparer des tailles entre elles, pas à promettre un rendement."]
    return "\n".join(L)


fo_cut_fee = 0.00398


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fee", type=float, default=0.398)
    ap.add_argument("--h4", type=float, default=0.30)
    ap.add_argument("--out", default="sizing_report.md")
    args = ap.parse_args()
    global fo_cut_fee
    fo_cut_fee = args.fee / 100.0
    rng = np.random.default_rng(2026)
    df, n = fo_cut.collect(args, rng)
    text = report(df, rng)
    Path(args.out).write_text(text, encoding="utf-8")
    df.to_csv("sizing_trades.csv", index=False)
    print(text)


if __name__ == "__main__":
    main()
