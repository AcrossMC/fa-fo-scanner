"""
Momentum relatif entre indices (proxys d'ETF éligibles au PEA) : bat-il l'achat-conservation équipondéré, et le hasard ?

Règles fixées AVANT les résultats
  Univers (4) : CAC 40, STOXX Europe 600, S&P 500 (en €), Nasdaq-100 (en €) (mêmes données que etf_s.py : Yahoo, conversion EURUSD=X, dividendes forfaitaires
                CAC 3,0 %, STOXX 600 3,2 %, S&P 500 1,6 %, Nasdaq-100 0,8 % ajoutés au rendement de cours ; --div 0 les supprime).
  Calendrier : décision à la clôture de chaque fin de mois, appliquée au rendement du mois suivant (aucun regard vers l'avenir). Départ : 12 mois après le début commun des données.
  Stratégies (5), sans réglage ultérieur :
    R6, R12  : on détient l'indice dont le rendement sur 6 / 12 mois est le meilleur (momentum relatif), toujours investi.
    D6, D12  : momentum relatif + absolu (« dual momentum ») : le meilleur indice, seulement si son rendement sur 6 / 12 mois est > 0 ; sinon liquidités.
    T10      : tendance : on détient à parts égales les indices dont le cours est au-dessus de sa moyenne mobile de 10 mois ; le reste en liquidités.
  Références : achat-conservation de chaque indice ; achat-conservation équipondéré des 4 indices (rééquilibré chaque mois, sans frais de rééquilibrage : avantage donné à la référence).
  Hasard (1000 tirages) : mêmes périodes, mêmes frais, MÊME FRÉQUENCE DE CHANGEMENTS que la stratégie (l'état est conservé d'un mois à l'autre sauf tirage) ;
                R : indice tiré au sort ; D : indice et liquidités au sort (même part de liquidités) ; T : chaque indice présent au sort avec sa fréquence de présence.
  Frais : --fee % sur chaque montant acheté ou vendu (changement d'indice = vente + achat). Liquidités : rendement --cash % par an (0 par défaut, comme un PEA).
  Critères : une stratégie « bat l'achat-conservation équipondéré » si (1) son rendement annualisé est supérieur, (2) l'écart a un IC 95 % (blocs de 6 mois) > 0,
             (3) elle dépasse le 90e centile du hasard, et (4) l'écart est > 0 avant 2016 comme à partir de 2016.
             Elle est « défensive » si son rapport rendement / creux maximal est supérieur ET son creux maximal est plus faible ET son rendement annualisé est d'au moins 90 % de celui de la référence.
Limites : environ 20 ans, un seul chemin historique, 4 indices fortement corrélés ; 5 stratégies testées (un « bat » isolé reste possible par hasard) ; creux mesuré sur clôtures mensuelles ;
          dividendes forfaitaires ; frais de rééquilibrage de la référence ignorés.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import etf_s
import robust
import scanner as sc
import profit

UNIV = ["^FCHI", "^STOXX", "^GSPC", "^NDX"]
STRATS = ["R6", "R12", "D6", "D12", "T10"]
SPLIT = pd.Timestamp("2016-01-01")
N_RAND = 1000
BLOCK = 6


def monthly_prices(raw, fx):
    cols = {}
    for t in UNIV:
        nm, y, usd = etf_s.INDICES[t]
        df = raw.get(t)
        if df is None or len(df) < 1000:
            continue
        df, _ = profit.clean_series(df)
        if usd:
            df = etf_s.to_eur(df, fx)
        s = df["Close"].copy()
        s.index = s.index.tz_localize(None)
        cols[nm] = s.resample("ME").last()
    P = pd.DataFrame(cols).dropna()
    return P


def weights(P, Rtr, strat):
    """Poids cibles décidés à la fin de chaque mois t (colonne = indice) ; ligne t s'applique au mois t+1."""
    n, m = P.shape
    W = np.zeros((n, m))
    TR = np.cumprod(1 + Rtr.to_numpy(), axis=0)
    if strat[0] in "RD":
        L = int(strat[1:])
        for t in range(L, n):
            mom = TR[t] / TR[t - L] - 1
            b = int(np.argmax(mom))
            if strat[0] == "R" or mom[b] > 0:
                W[t, b] = 1.0
    else:
        L = int(strat[1:])
        ma = P.rolling(L).mean().to_numpy()
        up = P.to_numpy() > ma
        up &= np.isfinite(ma)
        cnt = up.sum(1)
        for t in range(n):
            if cnt[t]:
                W[t] = up[t] / cnt[t]
    return W


def run(W, R, fee, cash, start):
    """Rendements mensuels nets de la stratégie, mois start+1 ... fin. W[t] décidé à la fin du mois t."""
    n = R.shape[0]
    out, prev = [], np.zeros(W.shape[1])
    for t in range(start, n - 1):
        w = W[t]
        turn = np.abs(w - prev).sum()
        r = float(w @ R[t + 1]) + (1 - w.sum()) * ((1 + cash) ** (1 / 12) - 1) - fee * turn
        out.append(r)
        prev = w
    return np.array(out)


def stats(r):
    eq = np.cumprod(1 + r)
    yrs = len(r) / 12
    cagr = eq[-1] ** (1 / yrs) - 1 if eq[-1] > 0 else np.nan
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    vol = float(np.std(r, ddof=1) * np.sqrt(12))
    return {"cagr": cagr, "dd": dd, "vol": vol, "calmar": cagr / abs(dd) if dd < 0 else np.nan, "final": float(eq[-1])}


def boot_gap(r, ref, rng, reps=2000):
    d = r - ref
    n = len(d)
    nb = int(np.ceil(n / BLOCK))
    idx = (rng.integers(0, n - BLOCK + 1, size=(reps, nb))[:, :, None] + np.arange(BLOCK)).reshape(reps, -1)[:, :n]
    g = np.prod(1 + r[idx], 1) ** (12 / n) - np.prod(1 + ref[idx], 1) ** (12 / n)
    return float(np.percentile(g, 2.5)), float(np.percentile(g, 97.5))


def random_dist(strat, R, fee, cash, start, Wstrat, rng):
    """Hasard à rotation comparable : à chaque mois l'état est conservé, sauf tirage (même fréquence de changements que la stratégie)."""
    n, m = R.shape
    T = n - 1 - start
    gc = (1 + cash) ** (1 / 12) - 1
    Wm = Wstrat.astype(bool)
    Rn = R[start + 1:start + 1 + T]
    if strat[0] in "RD":
        chg = float((np.abs(np.diff(Wstrat, axis=0)).sum(1) > 1e-12).mean())
        frac_cash = float((Wstrat.sum(1) == 0).mean()) if strat[0] == "D" else 0.0
        same = frac_cash ** 2 + (1 - frac_cash) ** 2 / m                    # probabilité qu'un tirage redonne le même état
        redraw = rng.random((N_RAND, T)) < min(1.0, chg / max(1 - same, 1e-9))
        redraw[:, 0] = True
        new_idx = rng.integers(0, m, (N_RAND, T))
        new_cash = rng.random((N_RAND, T)) < frac_cash
        idx = np.zeros((N_RAND, T), int)
        cs = np.zeros((N_RAND, T), bool)
        for t in range(T):
            if t == 0:
                idx[:, 0], cs[:, 0] = new_idx[:, 0], new_cash[:, 0]
            else:
                idx[:, t] = np.where(redraw[:, t], new_idx[:, t], idx[:, t - 1])
                cs[:, t] = np.where(redraw[:, t], new_cash[:, t], cs[:, t - 1])
        W = np.zeros((N_RAND, T, m))
        np.put_along_axis(W, idx[:, :, None], (~cs)[:, :, None].astype(float), axis=2)
    else:
        q = np.abs(np.diff(Wm.astype(int), axis=0)).mean(0)
        p_in = Wm.mean(0)
        same = p_in ** 2 + (1 - p_in) ** 2
        redraw = rng.random((N_RAND, T, m)) < np.minimum(1.0, q / np.maximum(1 - same, 1e-9))
        redraw[:, 0, :] = True
        new = rng.random((N_RAND, T, m)) < p_in
        up = np.zeros((N_RAND, T, m), bool)
        up[:, 0] = new[:, 0]
        for t in range(1, T):
            up[:, t] = np.where(redraw[:, t], new[:, t], up[:, t - 1])
        c = up.sum(2)
        W = np.where(c[:, :, None] > 0, up / np.maximum(c, 1)[:, :, None], 0.0)
    prev = np.concatenate([np.zeros((N_RAND, 1, m)), W[:, :-1]], axis=1)
    turn = np.abs(W - prev).sum(2)
    r = (W * Rn[None]).sum(2) + (1 - W.sum(2)) * gc - fee * turn
    return np.prod(1 + r, axis=1) ** (12 / T) - 1


def analyse(P, yields, fee, cash, rng):
    m = P.shape[1]
    Rp = P.pct_change().fillna(0.0)
    R = Rp.to_numpy() + np.array([yields[c] for c in P.columns]) / 12
    Rdf = pd.DataFrame(R, index=P.index, columns=P.columns)
    start = 12
    dates = P.index[start + 1:len(P) - 0]
    T = len(P) - 1 - start
    dts = P.index[start + 1:start + 1 + T]
    ew = R[start + 1:start + 1 + T].mean(1)
    ew_s = stats(ew)
    refs = {"Équipondéré (4 indices)": ew_s}
    for j, c in enumerate(P.columns):
        r = R[start + 1:start + 1 + T, j].copy()
        r[0] -= fee
        refs[c] = stats(r)
    res = {}
    first, second = dts < SPLIT, dts >= SPLIT
    for s in STRATS:
        W = weights(P, Rdf, s)
        r = run(W, R, fee, cash, start)
        st = stats(r)
        inv = W[start:len(P) - 1].sum(1)
        st["time"] = float((inv > 0).mean())
        st["turn"] = float(np.abs(np.diff(np.vstack([np.zeros((1, m)), W[start:len(P) - 1]]), axis=0)).sum() / (T / 12))
        st["gap"] = st["cagr"] - ew_s["cagr"]
        lo, hi = boot_gap(r, ew, rng)
        st["lo"], st["hi"] = lo, hi
        st["gap1"] = (np.prod(1 + r[first]) ** (12 / first.sum()) - 1) - (np.prod(1 + ew[first]) ** (12 / first.sum()) - 1)
        st["gap2"] = (np.prod(1 + r[second]) ** (12 / second.sum()) - 1) - (np.prod(1 + ew[second]) ** (12 / second.sum()) - 1)
        dist = random_dist(s, R, fee, cash, start, W[start:len(P) - 1], rng)
        st["pct"] = float(np.mean(dist <= st["cagr"]) * 100)
        st["rand_med"] = float(np.median(dist))
        st["bat"] = bool(st["gap"] > 0 and lo > 0 and st["pct"] >= 90 and st["gap1"] > 0 and st["gap2"] > 0)
        st["def"] = bool(st["calmar"] > ew_s["calmar"] and abs(st["dd"]) < abs(ew_s["dd"]) and st["cagr"] >= 0.9 * ew_s["cagr"])
        res[s] = st
    return refs, res, dts


def report(P, refs, res, fee, div, cash, sens, dts):
    pct = robust.pct
    L = [f"# Momentum relatif entre indices (proxys d'ETF PEA) — frais {fee * 100:.2f} % par ordre", "",
         f"**Règles fixées avant les résultats** : voir l'en-tête de `momentum.py`. Univers : {', '.join(P.columns)}. Période de test : {dts[0].strftime('%m/%Y')} à {dts[-1].strftime('%m/%Y')} "
         f"({len(dts)} mois). Dividendes forfaitaires x{div:g} ; liquidités à {cash * 100:.1f} % ; hasard = {N_RAND} tirages.", "",
         "## Références", "", "| référence | rendement annualisé | volatilité | creux max | rendement / creux |", "|---|---|---|---|---|"]
    for k, v in refs.items():
        L.append(f"| {k} | **{pct(v['cagr'])}** | {pct(v['vol'])} | {pct(v['dd'], 0)} | {v['calmar']:.2f} |")
    L += ["", "## Stratégies", "",
          "| stratégie | rendement annualisé | écart avec l'équipondéré | IC 95 % de l'écart | centile du hasard (médiane du hasard) | écart avant 2016 | écart 2016+ | creux max | rendement / creux | temps investi | rotation par an | verdict |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    ew = refs["Équipondéré (4 indices)"]
    for s, v in res.items():
        lab = "**bat l'équipondéré**" if v["bat"] else "**défensive**" if v["def"] else "non"
        L.append(f"| **{s}** | **{pct(v['cagr'])}** | {pct(v['gap'])} | [{pct(v['lo'])} ; {pct(v['hi'])}] | p{v['pct']:.0f} ({pct(v['rand_med'])}) | {pct(v['gap1'])} | {pct(v['gap2'])} | "
                 f"{pct(v['dd'], 0)} | {v['calmar']:.2f} | {v['time'] * 100:.0f} % | {v['turn']:.1f} | {lab} |")
    if sens:
        L += ["", "## Sensibilité aux frais (écart de rendement annualisé avec l'équipondéré)", "",
              "| stratégie | " + " | ".join(f"frais {f:g} %" for f in sens["fees"]) + " |", "|---|" + "---|" * len(sens["fees"])]
        for s in STRATS:
            L.append(f"| {s} | " + " | ".join(pct(sens["gap"][f][s]) for f in sens["fees"]) + " |")
    bat = [s for s, v in res.items() if v["bat"]]
    dfn = [s for s, v in res.items() if v["def"] and not v["bat"]]
    L += ["", "## Verdict", "", "- Battent l'achat-conservation équipondéré : " + (", ".join(bat) if bat else "aucune."),
          "- Défensives (creux plus faible, rapport rendement/creux supérieur, au moins 90 % du rendement) : " + (", ".join(dfn) if dfn else "aucune."), "",
          "Lecture : avec 5 stratégies, un « bat » isolé peut venir du hasard ; il ne vaudrait que s'il se confirme avant et après 2016 et sur un suivi réel. "
          "Ces indices sont corrélés et la période ne contient que quelques grandes baisses : une stratégie « défensive » doit son avantage à très peu d'épisodes."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fee", type=float, default=0.25)
    ap.add_argument("--cash", type=float, default=0.0)
    ap.add_argument("--div", type=float, default=1.0)
    ap.add_argument("--out", default="momentum_report.md")
    args = ap.parse_args()
    rng = np.random.default_rng(2026)
    raw = sc.download(UNIV + ["EURUSD=X"], "max", "1d")
    fx = raw.get("EURUSD=X")
    if fx is None:
        raise SystemExit("EURUSD=X indisponible sur Yahoo : test impossible.")
    P = monthly_prices(raw, fx)
    if P.shape[1] < 3 or len(P) < 60:
        raise SystemExit("Pas assez d'indices ou d'historique commun : test impossible.")
    yields = {etf_s.INDICES[t][0]: etf_s.INDICES[t][1] * args.div for t in UNIV}
    fee, cash = args.fee / 100.0, args.cash / 100.0
    refs, res, dts = analyse(P, yields, fee, cash, rng)
    fees = [0.05, 0.25, 0.5]
    gap = {}
    for f in fees:
        gap[f] = {s: (res[s]["gap"] if abs(f - args.fee) < 1e-9 else analyse(P, yields, f / 100.0, cash, np.random.default_rng(7))[1][s]["gap"]) for s in STRATS}
    text = report(P, refs, res, fee, args.div, cash, {"fees": fees, "gap": gap}, dts)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
