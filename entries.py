"""
Quel déclencheur d'ENTRÉE va le mieux avec la sortie « plus haut 26-52 semaines » (Donchian-2) en hebdomadaire ?

Règles fixées AVANT les résultats (échantillon : Paris + autres places européennes, ~400 actions liquides ; SL ≥ 1,5 ATR ; sortie Donchian-2 ; frais --fee par ordre) :
  S      : S haussière hebdomadaire (référence).
  S+H4   : S quand l'action est à plus de 30 % sous son plus haut de 52 semaines.
  S+Vol  : S avec un volume de la semaine supérieur à 1,5 fois sa moyenne de 20 semaines.
  S+Série: S après au moins 5 bougies Heikin Ashi rouges consécutives.
  Rebond : première bougie HA verte après au moins 3 rouges, SANS que ce soit une S (pas de test « sans mèche »).
  Fa     : première bougie Fa validée (même SL et même sortie, pour comparer).
Pour chaque déclencheur : moyenne nette, avantage sur le hasard (entrées au sort, même action, même année, mêmes SL / sortie), avant 2016 (IC 99 %) et à partir de 2016 (IC 95 %).
Un FILTRE de S (S+H4, S+Vol, S+Série) n'est retenu que s'il fait mieux que les S qui ne le remplissent pas (écart > 0, IC > 0 en apprentissage ET en test) ;
un autre déclencheur (Rebond, Fa) n'est retenu que s'il bat le hasard dans les deux périodes ET fait au moins aussi bien que S.
"""
import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

import eu
import profit
import regime
import robust
import scanner as sc
import sr

TRIGGERS = ["S", "S+H4", "S+Vol", "S+Série", "Rebond", "Fa"]
FILTERS = ["S+H4", "S+Vol", "S+Série"]
N_RANDOM = 5
SPLIT_YEAR = 2016


def entry_ret(A, arrs, S, k, fee_rt):
    r = eu.one(A, arrs, k, S, fee_rt)
    return None if not r else r.get("Donchian-2")


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
        c, hao, hac, st, vol, vavg, runlen = A["c"], A["hao"], A["hac"], A["st"], A["v"], A["vavg"], A["runlen"]
        red = hac < hao
        n_s = 0
        for k in range(105, A["n"] - 1):
            is_s = bool(A["up"][k])
            bounce = (not is_s) and hac[k] > hao[k] and red[k - 1] and red[k - 2] and red[k - 3]
            fa = st[k] == 1 and st[k - 1] != 1
            if not (is_s or bounce or fa):
                continue
            r = entry_ret(A, arrs, S, k, fee_rt)
            if r is None:
                continue
            h4 = c[k] / A["h"][k - 52:k + 1].max() - 1 <= -0.30
            vo = bool(np.isfinite(vavg[k]) and vavg[k] > 0 and vol[k] / vavg[k] > 1.5)
            serie = bool(runlen[k] >= 5)
            tags = []
            if is_s:
                tags += ["S"] + (["S+H4"] if h4 else []) + (["S+Vol"] if vo else []) + (["S+Série"] if serie else [])
                n_s += 1
            if bounce:
                tags.append("Rebond")
            if fa:
                tags.append("Fa")
            for g in tags:
                rows.append({"trigger": g, "market": eu.market_of(t), "ticker": t, "kind": "S", "time": A["ends"][k], "ret": r, "s_flag": is_s,
                             "H4": h4, "Vol": vo, "Série": serie})
        for _ in range(1):
            cand = [kk for kk in range(105, A["n"] - 1)]
            m = min(len(cand), max(n_s, 1) * N_RANDOM)
            for kk in (rng.choice(cand, size=m, replace=False) if m else []):
                r2 = entry_ret(A, arrs, S, kk, fee_rt)
                if r2 is not None:
                    rows.append({"trigger": "H", "market": eu.market_of(t), "ticker": t, "kind": "H", "time": A["ends"][kk], "ret": r2, "s_flag": False, "H4": False, "Vol": False, "Série": False})
        if i % 25 == 0:
            print(f"  {i}/{len(ranked)} actions, {len(rows)} lignes, {int(time.time() - t0)} s", flush=True)
    return pd.DataFrame(rows), len(ranked)


def per(df, lab):
    yr = pd.DatetimeIndex(df["time"]).year
    return df[yr < SPLIT_YEAR] if lab == "avant" else df[yr >= SPLIT_YEAR] if lab == "après" else df


def report(df, n, fee, rng):
    pct = robust.pct
    H = df[df["kind"] == "H"]
    L = [f"# Quel déclencheur d'entrée avec la sortie Donchian-2 ? — {n} actions liquides (Paris + Europe), frais {fee * 100:.2f} % par ordre", "",
         "**Règles fixées avant les résultats** : voir l'en-tête de `entries.py`. Hasard = entrées au sort (même action, même année) avec les mêmes SL (≥ 1,5 ATR) et la même sortie. "
         "Apprentissage jusqu'en 2015 (IC 99 %), test à partir de 2016 (IC 95 %).", "",
         "| déclencheur | période | trades | moyenne nette | IC | hasard | avantage | IC de l'avantage |", "|---|---|---|---|---|---|---|---|"]
    res = {}
    for g in TRIGGERS:
        D = df[df["trigger"] == g]
        for lab, lvl in (("tout", 95), ("avant", 99), ("après", 95)):
            x = eu.stat(pd.concat([per(D, lab), per(H, lab)]), rng, lvl)
            res[(g, lab)] = x
            if x:
                L.append(f"| **{g}** | {lab} | {x['n']} | **{pct(x['mean'])}** | [{pct(x['mlo'])} ; {pct(x['mhi'])}] | {pct(x['hasard'])} | **{pct(x['adv'])}** | [{pct(x['alo'])} ; {pct(x['ahi'])}] |")
    L += ["", "## Filtres de S : font-ils mieux que les S qui ne les remplissent pas ?", "",
          "| filtre | période | S avec le filtre | S sans | moyenne avec | moyenne sans | écart | IC | verdict |", "|---|---|---|---|---|---|---|---|---|"]
    Sd = df[df["trigger"] == "S"]
    verd = {}
    for g, col in (("S+H4", "H4"), ("S+Vol", "Vol"), ("S+Série", "Série")):
        ok = {}
        for lab, lvl in (("avant", 99), ("après", 95)):
            d = per(Sd, lab)
            flag = d[col].to_numpy(bool)
            if flag.sum() < 30 or (~flag).sum() < 30:
                continue
            diff, lo, hi = regime.diff_ci(d["time"], d["ret"].to_numpy(), flag, rng, lvl)
            ok[lab] = lo > 0
            L.append(f"| **{g}** | {lab} | {int(flag.sum())} | {int((~flag).sum())} | {pct(d[flag]['ret'].mean())} | {pct(d[~flag]['ret'].mean())} | **{pct(diff)}** | [{pct(lo)} ; {pct(hi)}] | "
                     f"{('**retenu**' if ok.get('avant') and ok.get('après') else 'non') if lab == 'après' else ''} |")
        verd[g] = bool(ok.get("avant") and ok.get("après"))
    L += ["", "## Autres déclencheurs : battent-ils le hasard et font-ils au moins aussi bien que S ?", "",
          "| déclencheur | avantage avant 2016 | avantage 2016+ | moyenne nette | moyenne nette de S | verdict |", "|---|---|---|---|---|---|"]
    for g in ("Rebond", "Fa"):
        a, b, t, ts = res.get((g, "avant")), res.get((g, "après")), res.get((g, "tout")), res.get(("S", "tout"))
        ok = bool(a and b and a["alo"] > 0 and b["alo"] > 0 and t and ts and t["mean"] >= ts["mean"])
        verd[g] = ok
        if t:
            L.append(f"| **{g}** | {pct(a['adv']) if a else '–'} [{pct(a['alo']) if a else '–'}] | {pct(b['adv']) if b else '–'} [{pct(b['alo']) if b else '–'}] | {pct(t['mean'])} | {pct(ts['mean']) if ts else '–'} | {'**retenu**' if ok else 'non'} |")
    L += ["", "## S+H4 place par place", "",
          "Le filtre tient-il dans chaque place, ou seulement en France ? Critère fixé à l'avance : avantage sur le hasard > 0 dans au moins 6 places sur 9, ET > 0 avec IC 95 % > 0 à la fois à Paris et hors Paris.", "",
          "| place | S+H4 | moyenne nette | hasard | avantage | IC de l'avantage | S sans filtre | écart S+H4 − S sans | IC de l'écart |", "|---|---|---|---|---|---|---|---|---|"]
    Hh, S4 = df[df["kind"] == "H"], df[df["trigger"] == "S+H4"]
    pos = tot = 0
    groups = [(m, df["market"] == m) for m in eu.MARKETS.values()]
    for lab, sel in groups + [("**Hors Paris**", ~df["market"].str.startswith("Paris")), ("**Paris**", df["market"].str.startswith("Paris"))]:
        a, h, sd = S4[sel.reindex(S4.index, fill_value=False)], Hh[sel.reindex(Hh.index, fill_value=False)], Sd[sel.reindex(Sd.index, fill_value=False)]
        x = eu.stat(pd.concat([a, h]), rng)
        if x is None:
            continue
        flag = sd["H4"].to_numpy(bool)
        dd = regime.diff_ci(sd["time"], sd["ret"].to_numpy(), flag, rng, 95) if flag.sum() >= 30 and (~flag).sum() >= 30 else (np.nan, np.nan, np.nan)
        if not lab.startswith("**"):
            tot += 1
            pos += x["adv"] > 0
        L.append(f"| {lab} | {x['n']} | **{pct(x['mean'])}** | {pct(x['hasard'])} | **{pct(x['adv'])}** | [{pct(x['alo'])} ; {pct(x['ahi'])}] | {int((~flag).sum())} | {pct(dd[0])} | [{pct(dd[1])} ; {pct(dd[2])}] |")
        res[("pl", lab)] = x
    xp, xe = res.get(("pl", "**Paris**")), res.get(("pl", "**Hors Paris**"))
    okp = bool(tot and pos >= 6 and xp and xe and xp["alo"] > 0 and xe["alo"] > 0)
    L += ["", f"Avantage > 0 dans {pos} places sur {tot}. Verdict place par place : {'**le filtre tient partout**' if okp else 'critères non tous remplis'}."]
    L += ["", "## Verdict", ""]
    kept = [g for g, v in verd.items() if v]
    L.append("- Retenus : " + (", ".join(kept) if kept else "aucun — S hebdomadaire seule reste le meilleur déclencheur testé."))
    L += ["", "Lecture : avec 6 déclencheurs, un « retenu » isolé reste possible par hasard ; seul un déclencheur positif dans les deux périodes est crédible, et il doit encore se confirmer en réel."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fee", type=float, default=0.398)
    ap.add_argument("--out", default="entries_report.md")
    args = ap.parse_args()
    rng = np.random.default_rng(2026)
    df, n = collect(args, rng)
    text = report(df, n, args.fee / 100.0, rng)
    Path(args.out).write_text(text, encoding="utf-8")
    df.to_csv("entries_trades.csv", index=False)
    print(text)


if __name__ == "__main__":
    main()
