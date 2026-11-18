"""
Taille de position et nombre de lignes pour S+H4 hebdo (SL 1,5 ATR, objectif Donchian-2), capital 5 000 € : S+H4 contre le hasard contre le CAC 40.

Méthode : trades historiques S+H4 (mêmes que fo_cut.py, règle simple SL / objectif fixes) rejoués semaine par semaine dans un portefeuille de N lignes (ordre aléatoire s'il y a plus de signaux que de places),
puis 1 000 « futurs » de 15 ans obtenus en tirant des années au hasard (avec remise). Chaque futur est rejoué avec :
  - S+H4 (les vrais trades de ces années) ;
  - hasard : même nombre d'entrées par année, tirées au sort (même action, même année) avec les mêmes SL / objectif / gestion ;
  - CAC 40 acheté et conservé (indice de cours, hors dividendes) sur les mêmes années.
Configurations (fixées avant les résultats) : 8 lignes × 500 €, 12 × 333 €, 16 × 250 €, 20 × 200 € (80 % du capital investi au plus).
Frais : 1,99 € par ordre jusqu'à 500 €, 0,5 % au-delà (aller-retour = 2 ordres) ; rendement brut reconstitué à partir du net de fo_cut (frais 2 x 0,398 % ajoutés).
Limites : le capital n'est mis à jour qu'à la sortie des trades (le creux réel peut être plus profond) ; survie des actions ; peu de trades gagnants très gros.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import fo_cut
import robust
import scanner as sc

CAP0 = 5000.0
YEARS = 15
N_PATHS = 1000
FEE_FIXED, FEE_PCT, FEE_THRESHOLD = 1.99, 0.005, 500.0
CONFIGS = {"8 lignes × 500 €": (8, 500.0), "12 lignes × 333 €": (12, 333.0), "16 lignes × 250 €": (16, 250.0), "20 lignes × 200 €": (20, 200.0)}
fo_cut_fee = 0.00398


def fee_rt(size):
    one = FEE_FIXED if size <= FEE_THRESHOLD else FEE_PCT * size
    return 2 * one / size


def replay(tr, lines, size0, rng):
    """tr : DataFrame (entry, exit, gross). Renvoie capital final, creux max (capital réalisé), trades pris, frais payés."""
    tr = tr.sample(frac=1, random_state=int(rng.integers(1 << 30))).sort_values("entry", kind="stable")
    peak, mdd, taken, fees = CAP0, 0.0, 0, 0.0
    cash, openp = CAP0, []
    for row in tr.itertuples(index=False):
        still = []
        for p in openp:
            if p[0] <= row.entry:
                cash += p[1] + p[2]
            else:
                still.append(p)
        openp = still
        equity = cash + sum(q[1] for q in openp)
        peak = max(peak, equity)
        mdd = max(mdd, 1 - equity / peak)
        if len(openp) >= lines:
            continue
        size = min(size0, cash)
        if size < 100:
            continue
        f = fee_rt(size)
        openp.append((row.exit, size, size * (row.gross - f)))
        cash -= size
        taken += 1
        fees += size * f
    for p in openp:
        cash += p[1] + p[2]
    peak = max(peak, cash)
    mdd = max(mdd, 1 - cash / peak)
    return cash, mdd, taken, fees


def to_tr(df):
    tr = pd.DataFrame({"entry": pd.DatetimeIndex(df["time"]).tz_localize(None), "weeks": df["bars:Référence"].astype(float),
                       "gross": df["Référence"].astype(float) + 2 * fo_cut_fee})
    tr["year"] = tr["entry"].dt.year
    return tr


def shifted(g, k, y):
    base = pd.Timestamp("2000-01-01")
    g = g.copy()
    g["entry"] = g["entry"] + (pd.Timedelta(days=k * 366) - (pd.Timestamp(year=int(y), month=1, day=1) - base))
    g["exit"] = g["entry"] + pd.to_timedelta(g["weeks"] * 7, unit="D")
    return g[["entry", "exit", "gross"]]


def cac_year_returns(idx):
    c = idx["Close"].astype(float)
    r = c.pct_change().dropna()
    return {int(y): g.to_numpy() for y, g in r.groupby(r.index.year)}


def cac_path(years, dr):
    x = np.concatenate([dr[y] for y in years if y in dr])
    eq = np.cumprod(1 + x)
    peak = np.maximum.accumulate(eq)
    return (eq[-1]) ** (1 / YEARS) - 1, float((1 - eq / peak).max())


def report(df, cac, rng):
    pct = robust.pct
    S = to_tr(df[df["kind"] == "S"])
    H = to_tr(df[df["kind"] == "H"])
    years_ok = sorted(set(S["year"]) & (set(cac) if cac else set(S["year"])))
    gS = {y: S[S["year"] == y] for y in years_ok}
    gH = {y: H[H["year"] == y] for y in years_ok}
    res = {(k, w): {"cagr": [], "mdd": [], "end": [], "taken": [], "fee": []} for k in CONFIGS for w in ("S+H4", "hasard")}
    cres = {"cagr": [], "mdd": []}
    for _ in range(N_PATHS):
        ys = rng.choice(years_ok, size=YEARS, replace=True)
        ps = pd.concat([shifted(gS[y], k, y) for k, y in enumerate(ys)])
        hs = []
        for k, y in enumerate(ys):
            n = len(gS[y])
            if len(gH[y]) and n:
                hs.append(shifted(gH[y].sample(n=n, replace=True, random_state=int(rng.integers(1 << 30))), k, y))
        ph = pd.concat(hs) if hs else None
        seed = int(rng.integers(1 << 30))
        for k, (lines, size) in CONFIGS.items():
            for w, p in (("S+H4", ps), ("hasard", ph)):
                if p is None:
                    continue
                e, mdd, taken, fees = replay(p, lines, size, np.random.default_rng(seed))
                r = res[(k, w)]
                r["end"].append(e); r["cagr"].append((max(e, 1.0) / CAP0) ** (1 / YEARS) - 1); r["mdd"].append(mdd); r["taken"].append(taken / YEARS); r["fee"].append(fees / YEARS)
        if cac:
            a, b = cac_path(ys, cac)
            cres["cagr"].append(a); cres["mdd"].append(b)
    L = [f"# S+H4, hasard et CAC 40 selon le nombre de lignes — capital {CAP0:.0f} €, {N_PATHS} futurs de {YEARS} ans (années tirées au hasard)", "",
         f"Base : {len(S)} trades S+H4 et {len(H)} entrées au hasard. Frais : 1,99 € par ordre jusqu'à 500 €. **CAGR** = rendement annuel moyen composé ; **creux max** = plus forte baisse depuis un sommet (capital réalisé pour les actions ; "
         "séance par séance pour le CAC 40) ; **perdants** = part des futurs finissant sous le capital de départ. Le hasard utilise les mêmes règles de sortie : l'écart S+H4 − hasard est l'apport réel du signal.", "",
         "| configuration | stratégie | CAGR médian | CAGR 5 % pires | CAGR 5 % meilleurs | creux max médian | creux max (95e centile) | futurs perdants | trades / an | frais / an |", "|---|---|---|---|---|---|---|---|---|---|"]
    for k in CONFIGS:
        for w in ("S+H4", "hasard"):
            r = res[(k, w)]
            if not r["cagr"]:
                continue
            c, m, e = np.array(r["cagr"]), np.array(r["mdd"]), np.array(r["end"])
            L.append(f"| {k} | **{w}** | **{pct(np.median(c))}** | {pct(np.percentile(c, 5))} | {pct(np.percentile(c, 95))} | {pct(np.median(m), 0)} | {pct(np.percentile(m, 95), 0)} | {pct((e < CAP0).mean(), 0)} | {np.mean(r['taken']):.0f} | {np.mean(r['fee']):.0f} € |")
    if cres["cagr"]:
        c, m = np.array(cres["cagr"]), np.array(cres["mdd"])
        L.append(f"| CAC 40 acheté et conservé | indice | **{pct(np.median(c))}** | {pct(np.percentile(c, 5))} | {pct(np.percentile(c, 95))} | {pct(np.median(m), 0)} | {pct(np.percentile(m, 95), 0)} | {pct((c < 0).mean(), 0)} | – | – |")
    L += ["", "Lecture : plus de lignes réduit-il l'écart entre le résultat médian et la moyenne et le creux, sans que les frais ne le mangent ? S+H4 bat-il le hasard au niveau du portefeuille ? "
          "Le CAC 40 (hors dividendes) sert de repère simple, pas de comparaison parfaite : le portefeuille d'actions n'est investi qu'à 80 % et ses gains sont tirés par peu de trades. Les années tirées au hasard incluent les crises et les rebonds de l'historique ; le futur peut être différent."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fee", type=float, default=0.398)
    ap.add_argument("--h4", type=float, default=0.30)
    ap.add_argument("--out", default="sizing_report.md")
    args = ap.parse_args()
    args.rand = 5
    global fo_cut_fee
    fo_cut_fee = args.fee / 100.0
    rng = np.random.default_rng(2026)
    df, n = fo_cut.collect(args, rng)
    idx = sc.download(["^FCHI"], "max", "1d").get("^FCHI")
    cac = cac_year_returns(idx) if idx is not None and len(idx) > 1000 else None
    text = report(df, cac, rng)
    Path(args.out).write_text(text, encoding="utf-8")
    df.to_csv("sizing_trades.csv", index=False)
    print(text)


if __name__ == "__main__":
    main()
