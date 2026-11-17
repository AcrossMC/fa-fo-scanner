"""
Question : ajouter des objectifs de sortie (TP : MM7/20/50/100/200 de la même UT, Tenkan et MM7 baissière des UT supérieures jusqu'à 3 mois) à une entrée Fa
améliore-t-il le résultat par rapport à la sortie « Fo de la même UT » ?

Entrée : clôture de la 1re bougie Fa validée. Pas de stop loss (comme l'application pour Fa). Le TP est un niveau fixé à l'entrée (n-ième niveau au-dessus du cours).
Sortie : au 1er TP touché ou au Fo, selon le schéma. Comparaison :
  - appariée : pour chaque entrée, rendement du schéma moins rendement de « Fo même UT » (marge d'erreur par mois) ;
  - avec le hasard : entrées au hasard (même action, même UT, même année) avec la même règle de sortie.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

import profit
import robust
import scanner as sc

FA_PLANS = {
    "Fo même UT": [(1.0, ("fo",))],
    "TP1 ou Fo": [(1.0, ("tpfo", 0))], "TP2 ou Fo": [(1.0, ("tpfo", 1))], "TP3 ou Fo": [(1.0, ("tpfo", 2))],
    "TP4 ou Fo": [(1.0, ("tpfo", 3))], "TP5 ou Fo": [(1.0, ("tpfo", 4))],
    "½ TP1 + ½ Fo": [(0.5, ("tpfo", 0)), (0.5, ("fo",))],
    "½ TP2 + ½ Fo": [(0.5, ("tpfo", 1)), (0.5, ("fo",))],
}
FA_SCHEMES = list(FA_PLANS)
N_RANDOM = 3


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
    cleaned = {}
    for t in list(daily):
        daily[t], n_cut = profit.clean_series(daily[t])
        if n_cut:
            cleaned[t] = n_cut
    turn = {t: sc.turnover(daily[t]) for t in daily}
    ranked = sorted((t for t in daily if turn[t] >= sc.MIN_TURNOVER_EUR), key=lambda t: turn[t], reverse=True)
    if args.tickers:
        ranked = ranked[:args.tickers]
    hourly = {} if args.no_4h else {t: profit.clean_series(df)[0] for t, df in sc.download(ranked, "2y", "1h").items()}
    now = pd.Timestamp.now(tz=sc.PARIS)
    trades, base, t0 = [], [], time.time()
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
            years, by = robust.random_year_index(A)
            st, c = A["st"], A["c"]
            for k in range(1, A["n"] - 1):
                if not (st[k] == 1 and st[k - 1] != 1):
                    continue
                entry = c[k]
                tps = profit.tp_levels(A, k, arrs, entry)
                row = {"tf": tf, "ticker": t, "time": A["ends"][k]}
                for name in FA_SCHEMES:
                    r = profit.simulate(A, k, entry, 0.0, tps, FA_PLANS[name])
                    if r is not None:
                        row[name] = r[0] - fee_rt
                if "Fo même UT" in row:
                    trades.append(row)
                cand = by.get(A["ends"][k].year, [])
                for kk in (rng.choice(cand, size=min(N_RANDOM, len(cand)), replace=False) if cand else []):
                    e2 = c[kk]
                    tp2 = profit.tp_levels(A, kk, arrs, e2)
                    b = {"tf": tf, "ticker": t, "time": A["ends"][kk]}
                    for name in FA_SCHEMES:
                        r = profit.simulate(A, kk, e2, 0.0, tp2, FA_PLANS[name])
                        if r is not None:
                            b[name] = r[0] - fee_rt
                    base.append(b)
        if i % 20 == 0:
            print(f"  {i}/{len(ranked)} actions, {len(trades)} entrées Fa, {int(time.time() - t0)} s", flush=True)
    return pd.DataFrame(trades), pd.DataFrame(base), len(ranked), cleaned


def analyse(tr, base, rng):
    pct = robust.pct
    L = ["## Fa : sortie par objectifs (TP) ou au Fo ?", "",
         "**Écart avec Fo** = rendement moyen du schéma moins celui de « Fo même UT » sur les MÊMES entrées (appariés). IC = marge d'erreur à 95 % de cet écart "
         "(rééchantillonnage par mois). **Hasard** = mêmes règles de sortie sur des entrées tirées au sort (même action, même UT, même année). "
         "Un TP n'apporte quelque chose que si l'IC de l'écart est entièrement au-dessus de 0.", ""]
    for tf in profit.TRADE_TFS:
        d = tr[tr["tf"] == tf]
        if len(d) < 30:
            continue
        b = base[base["tf"] == tf]
        L += [f"### {tf} — {len(d)} entrées Fa", "",
              "| sortie | entrées | moyenne | médiane | % gagnants | moyenne sans 1 % extrêmes | écart avec Fo | IC 95 % de l'écart | hasard | avantage sur le hasard |", "|---|---|---|---|---|---|---|---|---|---|"]
        for name in FA_SCHEMES:
            if name not in d:
                continue
            x = d[name].dropna()
            if len(x) < 30:
                continue
            both = d[[name, "Fo même UT", "time"]].dropna()
            diff = (both[name] - both["Fo même UT"]).to_numpy()
            lo, hi = robust.boot_ci(both["time"], diff, rng) if name != "Fo même UT" else (np.nan, np.nan)
            hb = b[name].dropna().mean() if name in b else np.nan
            L.append(f"| {name} | {len(x)} | **{pct(x.mean())}** | {pct(x.median())} | {pct((x > 0).mean(), 0)} | {pct(robust.trimmed(x.to_numpy()))} | "
                     f"{pct(diff.mean()) if name != 'Fo même UT' else '–'} | {('[' + pct(lo) + ' ; ' + pct(hi) + ']') if name != 'Fo même UT' else '–'} | {pct(hb)} | {pct(x.mean() - hb)} |")
        L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=int, default=0)
    ap.add_argument("--fee", type=float, default=0.5)
    ap.add_argument("--no-4h", action="store_true")
    ap.add_argument("--out", default="fa_tp_report.md")
    args = ap.parse_args()
    rng = np.random.default_rng(2026)
    tr, base, n, cleaned = collect(args, rng)
    text = f"# Fa avec objectifs (TP) — {n} actions françaises, frais {args.fee:.2f} % par ordre\n\n" + analyse(tr, base, rng)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
