"""
S hebdomadaire : la configuration bat-elle toujours le hasard sur d'AUTRES places européennes ?

Échantillon indépendant : Amsterdam (.AS), Bruxelles (.BR), Allemagne (.DE), Milan (.MI), Madrid (.MC), Lisbonne (.LS), Helsinki (.HE), Vienne (.VI),
avec Paris (même code) comme témoin. Liste : eu_universe.csv (codes Yahoo ; les codes introuvables sont ignorés).
Règles fixées AVANT les résultats :
  - deux sorties seulement, SL = base avec distance minimale 1,5 ATR : « MM5 » (règle actuelle de l'application) et « Donchian-2 » (2e niveau parmi les plus hauts de 13/26/52/104 semaines) ;
  - hasard = entrées tirées au sort (même action, même année) avec les mêmes SL / TP ;
  - la configuration « tient » si, sur l'ensemble hors Paris, l'avantage sur le hasard est > 0 avec IC 95 % > 0 ET si la moyenne nette est > 0,
    ET si l'avantage est > 0 dans au moins la moitié des places ET si l'avantage est > 0 en 2016+ (test) comme avant 2016.
Marges d'erreur : rééchantillonnage par mois. Frais identiques pour toutes les places (--fee) : les frais réels d'un PEA hors France peuvent être plus élevés.
"""
import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

import profit
import regime
import robust
import scanner as sc
import sr

EU_FILE = sc.HERE / "eu_universe.csv"
MARKETS = {".AS": "Amsterdam", ".BR": "Bruxelles", ".DE": "Allemagne", ".MI": "Milan", ".MC": "Madrid", ".LS": "Lisbonne", ".HE": "Helsinki", ".VI": "Vienne", ".PA": "Paris (témoin)"}
COMBOS = ["MM5", "Donchian-2"]
N_RANDOM = 5
SPLIT_YEAR = 2016


def load_eu():
    import csv
    with open(EU_FILE, encoding="utf-8") as f:
        rows = [r for r in csv.reader(f) if r and not r[0].startswith("#") and r[0] != "ticker"]
    return {r[0].strip(): r[1].strip() for r in rows}


def market_of(t):
    return MARKETS.get("." + t.rsplit(".", 1)[-1], "autre")


def one(A, arrs, k, S, fee_rt):
    entry = A["c"][k]
    if k < 105 or k + 1 >= A["n"]:
        return None
    tps = profit.tp_levels(A, k, arrs, entry)
    sl = profit.sl_levels(A, k, arrs, entry)["atr1.5"]
    if not np.isfinite(sl) or sl >= entry * 0.9995:
        return None
    L = sr.levels_at(A, S, "1w", k, entry)
    out = {}
    for name, lv, idx in (("MM5", tps, 4), ("Donchian-2", L["Donchian"], 1)):
        r = profit.simulate(A, k, entry, sl, lv, [(1.0, ("tp", idx))])
        if r is not None:
            out[name] = r[0] - fee_rt
    return out


def collect(args, rng, universe=None, now=None):
    fee_rt = 2 * args.fee / 100.0
    universe = universe or {**load_eu(), **{t: n for t, n in sc.load_universe().items()}}
    daily = sc.download(list(universe), "max", "1d")
    for t in list(daily):
        daily[t], _ = profit.clean_series(daily[t])
    turn = {t: sc.turnover(daily[t]) for t in daily}
    ranked = sorted((t for t in daily if turn[t] >= sc.MIN_TURNOVER_EUR), key=lambda t: turn[t], reverse=True)
    now = now or pd.Timestamp.now(tz=sc.PARIS)
    rows, t0 = [], time.time()
    for i, t in enumerate(ranked, 1):
        frames = {"1w": sc.aggregate(daily[t], "1w"), "1mo": sc.aggregate(daily[t], "1mo"), "3mo": sc.aggregate(daily[t], "3mo")}
        arrs = {}
        for tf, df in frames.items():
            try:
                a = profit.prep(df, tf, now)
            except Exception:
                a = None
            if a is not None:
                arrs[tf] = a
        A = arrs.get("1w")
        if A is None:
            continue
        S = sr.sr_prep(A, "1w")
        years, by = robust.random_year_index(A)
        mk = market_of(t)
        for k in np.flatnonzero(A["up"]):
            r = one(A, arrs, k, S, fee_rt)
            if not r:
                continue
            for name, v in r.items():
                rows.append({"market": mk, "ticker": t, "combo": name, "kind": "S", "time": A["ends"][k], "ret": v})
            cand = [kk for kk in by.get(A["ends"][k].year, []) if kk >= 105]
            for kk in (rng.choice(cand, size=min(N_RANDOM, len(cand)), replace=False) if cand else []):
                r2 = one(A, arrs, kk, S, fee_rt)
                for name, v in (r2 or {}).items():
                    rows.append({"market": mk, "ticker": t, "combo": name, "kind": "H", "time": A["ends"][kk], "ret": v})
        if i % 25 == 0:
            print(f"  {i}/{len(ranked)} actions, {len(rows)} lignes, {int(time.time() - t0)} s", flush=True)
    return pd.DataFrame(rows), len(ranked)


def stat(d, rng, level=95):
    s, h = d[d["kind"] == "S"], d[d["kind"] == "H"]
    if len(s) < 30 or len(h) < 30:
        return None
    flag = np.r_[np.ones(len(s), bool), np.zeros(len(h), bool)]
    x = pd.concat([s, h])
    diff, lo, hi = regime.diff_ci(x["time"], x["ret"].to_numpy(), flag, rng, level)
    mlo, mhi = sr.ci(s["time"], s["ret"].to_numpy(), rng, level)
    return {"n": len(s), "mean": s["ret"].mean(), "mlo": mlo, "mhi": mhi, "hasard": h["ret"].mean(), "adv": diff, "alo": lo, "ahi": hi, "nh": len(h)}


def report(df, n, fee, rng):
    pct = robust.pct
    L = [f"# S hebdomadaire sur d'autres places européennes — {n} actions liquides, frais {fee * 100:.2f} % par ordre", "",
         "**Règles fixées avant les résultats** : SL ≥ 1,5 ATR ; sorties « MM5 » (règle de l'application) et « Donchian-2 » (plus haut 26 à 52 semaines) ; hasard = entrées au sort (même action, même année). "
         "La configuration « tient » hors Paris si l'avantage sur le hasard est > 0 avec IC 95 % > 0, la moyenne nette > 0, l'avantage > 0 dans la moitié des places au moins, et > 0 avant comme après 2016.", ""]
    verdicts = []
    for combo in COMBOS:
        d = df[df["combo"] == combo]
        L += [f"## Sortie {combo}", "", "| place | trades S | moyenne nette | IC 95 % | hasard | avantage | IC 95 % de l'avantage |", "|---|---|---|---|---|---|---|"]
        pos = tot = 0
        for mk in list(MARKETS.values()):
            x = stat(d[d["market"] == mk], rng)
            if x is None:
                continue
            if "Paris" not in mk:
                tot += 1
                pos += x["adv"] > 0
            L.append(f"| {mk} | {x['n']} | **{pct(x['mean'])}** | [{pct(x['mlo'])} ; {pct(x['mhi'])}] | {pct(x['hasard'])} | **{pct(x['adv'])}** | [{pct(x['alo'])} ; {pct(x['ahi'])}] |")
        ex = d[~d["market"].str.startswith("Paris")]
        res = {}
        for lab, sel in (("Hors Paris, tout", slice(None)), ("Hors Paris, avant 2016", pd.DatetimeIndex(ex["time"]).year < SPLIT_YEAR), ("Hors Paris, 2016 et après", pd.DatetimeIndex(ex["time"]).year >= SPLIT_YEAR)):
            x = stat(ex[sel] if not isinstance(sel, slice) else ex, rng)
            res[lab] = x
            if x:
                L.append(f"| **{lab}** | {x['n']} | **{pct(x['mean'])}** | [{pct(x['mlo'])} ; {pct(x['mhi'])}] | {pct(x['hasard'])} | **{pct(x['adv'])}** | [{pct(x['alo'])} ; {pct(x['ahi'])}] |")
        t, a, b = res["Hors Paris, tout"], res["Hors Paris, avant 2016"], res["Hors Paris, 2016 et après"]
        ok = bool(t and a and b and t["alo"] > 0 and t["mean"] > 0 and tot and pos / tot >= 0.5 and a["adv"] > 0 and b["adv"] > 0)
        verdicts.append((combo, ok, pos, tot))
        L.append("")
    L += ["## Verdict", ""]
    for combo, ok, pos, tot in verdicts:
        L.append(f"- **{combo}** : {'**tient** hors Paris' if ok else 'ne tient pas (critères non tous remplis)'} — avantage > 0 dans {pos} places sur {tot}.")
    L += ["", "Lecture : si la configuration bat le hasard sur des marchés qui n'ont servi ni à la choisir ni à la régler, la chance de n'avoir trouvé qu'un hasard diminue nettement "
          "(sans disparaître : biais de survie, mêmes rebonds de marché européens). Si elle ne bat pas le hasard, le résultat français était très probablement un accident de l'échantillon."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fee", type=float, default=0.398)
    ap.add_argument("--out", default="eu_report.md")
    args = ap.parse_args()
    rng = np.random.default_rng(2026)
    df, n = collect(args, rng)
    text = report(df, n, args.fee / 100.0, rng)
    Path(args.out).write_text(text, encoding="utf-8")
    df.to_csv("eu_trades.csv", index=False)
    print(text)


if __name__ == "__main__":
    main()
