"""
Les gros chocs d'information (publications de résultats, annonces) sont-ils suivis d'une dérive exploitable ?

Pourquoi pas seulement les dates de résultats : l'historique des dates de résultats de Yahoo est court et incomplet pour les actions européennes.
On détecte donc les chocs dans les cours eux-mêmes (écart d'ouverture fort ET volume très supérieur à la normale), puis on repère, quand c'est possible,
ceux qui coïncident avec une date de résultats connue. L'étude porte sur ~400 actions liquides (Paris + autres places européennes), toute l'histoire disponible.

Règles fixées AVANT les résultats
  Choc haussier : écart d'ouverture (ouverture du jour D / clôture de D-1 - 1) >= +5 % ET volume de D >= 3 x la moyenne des 20 séances précédentes.
  Choc baissier : même chose avec un écart <= -5 %.
  Entrée : ouverture de D+1 (on attend la clôture du jour du choc ; aucun regard vers l'avenir). Sortie : clôture de D+h, h = 20 ou 60 séances. Pas de stop, pas de réglage.
  Quatre tests (long seulement, comme dans un PEA) : « acheter après un choc haussier » (dérive) et « acheter après un choc baissier » (rebond), pour h = 20 et h = 60.
  Un choc n'est pas retenu s'il tombe moins de h séances après un choc déjà retenu sur la même action (pas de chevauchement).
  Hasard : 5 entrées au sort par choc, même action, même année, même horizon h (jours sans choc), mêmes frais.
  Frais : --fee % par ordre (0,398 % = 1,99 € pour 500 €), soit 2 x par aller-retour.
  Un test est retenu si : la moyenne nette est > 0 ; l'avantage sur le hasard est > 0 avec IC 99 % > 0 avant 2016 ET IC 95 % > 0 à partir de 2016 ;
  l'avantage est > 0 à Paris comme hors Paris, et > 0 dans au moins la moitié des places.
  Avec 4 tests, un « retenu » isolé peut venir du hasard ; il doit se confirmer en réel.
Complément (non utilisé pour le verdict) : part des chocs qui coïncident avec une date de résultats Yahoo (la veille ou le jour D) et résultats selon la coïncidence.
Limites : biais du survivant ; les chocs sont des entrées « réalistes » seulement si l'on peut acheter à l'ouverture suivante au prix observé (liquidité, écarts d'exécution non modélisés) ;
          les dates de résultats de Yahoo sont incomplètes.
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

GAP = 0.05
VOL_X = 3.0
HORIZONS = (20, 60)
DIRS = ("hausse", "baisse")
N_RANDOM = 5
SPLIT_YEAR = 2016


def shocks(df):
    """Indices (positions) des chocs haussiers et baissiers dans un DataFrame journalier."""
    o, c, v = df["Open"].to_numpy(float), df["Close"].to_numpy(float), df["Volume"].to_numpy(float)
    n = len(c)
    gap = np.full(n, np.nan)
    gap[1:] = o[1:] / c[:-1] - 1
    vavg = pd.Series(v).rolling(20).mean().shift(1).to_numpy()
    ok = np.isfinite(gap) & np.isfinite(vavg) & (vavg > 0) & (v >= VOL_X * vavg)
    up = np.flatnonzero(ok & (gap >= GAP))
    dn = np.flatnonzero(ok & (gap <= -GAP))
    return {"hausse": up, "baisse": dn}


def hold_ret(df_o, df_c, d, h):
    """Rendement brut ouverture D+1 -> clôture D+h, ou None si l'historique s'arrête avant."""
    if d + h >= len(df_c) or d + 1 >= len(df_o):
        return None
    if not (np.isfinite(df_o[d + 1]) and df_o[d + 1] > 0):
        return None
    return df_c[d + h] / df_o[d + 1] - 1


def earnings_dates(ticker):
    """Dates de résultats connues de Yahoo (jours calendaires) ; ensemble vide en cas d'échec."""
    try:
        import yfinance as yf
        e = yf.Ticker(ticker).get_earnings_dates(limit=100)
        if e is None or not len(e):
            return set()
        idx = pd.DatetimeIndex(e.index)
        idx = idx.tz_localize(None) if idx.tz is None else idx.tz_convert("Europe/Paris").tz_localize(None)
        return set(idx.normalize())
    except Exception:
        return set()


def collect(args, rng, universe=None, with_earnings=True):
    fee_rt = 2 * args.fee / 100.0
    universe = universe or {**eu.load_eu(), **sc.load_universe()}
    daily = sc.download(list(universe), "max", "1d")
    for t in list(daily):
        daily[t], _ = profit.clean_series(daily[t])
    turn = {t: sc.turnover(daily[t]) for t in daily}
    ranked = sorted((t for t in daily if turn[t] >= sc.MIN_TURNOVER_EUR), key=lambda t: turn[t], reverse=True)
    rows, t0 = [], time.time()
    cover = 0
    for i, t in enumerate(ranked, 1):
        df = daily[t]
        if len(df) < 300:
            continue
        o, c = df["Open"].to_numpy(float), df["Close"].to_numpy(float)
        days = pd.DatetimeIndex(df.index.tz_localize(None)).normalize()
        years = days.year.to_numpy()
        sh = shocks(df)
        mk = eu.market_of(t)
        ed = earnings_dates(t) if with_earnings else set()
        cover += bool(ed)
        shock_pos = set(sh["hausse"]) | set(sh["baisse"])
        by_year = {}
        for k in range(21, len(c) - 1):
            if k not in shock_pos:
                by_year.setdefault(years[k], []).append(k)
        for h in HORIZONS:
            for dr in DIRS:
                last = -10 ** 9
                for d in sh[dr]:
                    if d - last < h:
                        continue
                    r = hold_ret(o, c, d, h)
                    if r is None:
                        continue
                    last = d
                    earn = bool(ed) and (days[d] in ed or (days[d] - pd.Timedelta(days=1)) in ed)
                    rows.append({"dir": dr, "h": h, "market": mk, "ticker": t, "kind": "S", "time": days[d], "ret": r - fee_rt, "earn": earn, "has_ed": bool(ed)})
                    cand = [kk for kk in by_year.get(years[d], []) if kk + h < len(c)]
                    for kk in (rng.choice(cand, size=min(N_RANDOM, len(cand)), replace=False) if cand else []):
                        r2 = hold_ret(o, c, kk, h)
                        if r2 is not None:
                            rows.append({"dir": dr, "h": h, "market": mk, "ticker": t, "kind": "H", "time": days[kk], "ret": r2 - fee_rt, "earn": False, "has_ed": bool(ed)})
        if i % 25 == 0:
            print(f"  {i}/{len(ranked)} actions, {len(rows)} lignes, {int(time.time() - t0)} s", flush=True)
    return pd.DataFrame(rows), len(ranked), cover


def per(df, lab):
    yr = pd.DatetimeIndex(df["time"]).year
    return df[yr < SPLIT_YEAR] if lab == "avant" else df[yr >= SPLIT_YEAR] if lab == "après" else df


def report(df, n, cover, fee, rng):
    pct = robust.pct
    L = [f"# Chocs d'information (écart d'ouverture >= 5 % avec volume >= 3x) : dérive ou rebond ? — {n} actions liquides, frais {fee * 100:.2f} % par ordre", "",
         "**Règles fixées avant les résultats** : voir l'en-tête de `events.py`. Entrée à l'ouverture suivant le choc, sortie après 20 ou 60 séances, sans stop. Hasard = entrées au sort (même action, même année, même horizon). "
         "Apprentissage jusqu'en 2015 (IC 99 %), test à partir de 2016 (IC 95 %).", "",
         "| test | période | trades | moyenne nette | IC | hasard | avantage | IC de l'avantage |", "|---|---|---|---|---|---|---|---|"]
    res = {}
    for dr in DIRS:
        for h in HORIZONS:
            D = df[(df["dir"] == dr) & (df["h"] == h)]
            for lab, lvl in (("tout", 95), ("avant", 99), ("après", 95)):
                x = eu.stat(per(D, lab), rng, lvl)
                res[(dr, h, lab)] = x
                if x:
                    L.append(f"| **après choc {dr}, {h} séances** | {lab} | {x['n']} | **{pct(x['mean'])}** | [{pct(x['mlo'])} ; {pct(x['mhi'])}] | {pct(x['hasard'])} | **{pct(x['adv'])}** | [{pct(x['alo'])} ; {pct(x['ahi'])}] |")
    L += ["", "## Place par place (tout l'historique)", "",
          "| test | place | trades | moyenne nette | hasard | avantage | IC de l'avantage |", "|---|---|---|---|---|---|---|"]
    verdict = {}
    for dr in DIRS:
        for h in HORIZONS:
            D = df[(df["dir"] == dr) & (df["h"] == h)]
            pos = tot = 0
            pl = {}
            for mk in list(eu.MARKETS.values()) + ["**Hors Paris**", "**Paris**"]:
                if mk == "**Hors Paris**":
                    sel = ~D["market"].str.startswith("Paris")
                elif mk == "**Paris**":
                    sel = D["market"].str.startswith("Paris")
                else:
                    sel = D["market"] == mk
                x = eu.stat(D[sel], rng)
                if x is None:
                    continue
                pl[mk] = x
                if not mk.startswith("**"):
                    tot += 1
                    pos += x["adv"] > 0
                L.append(f"| {dr}, {h} s | {mk} | {x['n']} | {pct(x['mean'])} | {pct(x['hasard'])} | **{pct(x['adv'])}** | [{pct(x['alo'])} ; {pct(x['ahi'])}] |")
            a, b, t_ = res.get((dr, h, "avant")), res.get((dr, h, "après")), res.get((dr, h, "tout"))
            xp, xe = pl.get("**Paris**"), pl.get("**Hors Paris**")
            ok = bool(t_ and t_["mean"] > 0 and a and b and a["alo"] > 0 and b["alo"] > 0 and xp and xe and xp["adv"] > 0 and xe["adv"] > 0 and tot and pos >= tot / 2)
            verdict[(dr, h)] = (ok, pos, tot)
    L += ["", "## Verdict par test", "", "| test | moyenne nette | avantage avant 2016 | avantage 2016+ | places avec avantage > 0 | verdict |", "|---|---|---|---|---|---|"]
    for (dr, h), (ok, pos, tot) in verdict.items():
        a, b, t_ = res.get((dr, h, "avant")), res.get((dr, h, "après")), res.get((dr, h, "tout"))
        L.append(f"| après choc {dr}, {h} séances | {pct(t_['mean']) if t_ else '–'} | {pct(a['adv']) + ' [' + pct(a['alo']) + ']' if a else '–'} | {pct(b['adv']) + ' [' + pct(b['alo']) + ']' if b else '–'} | {pos}/{tot} | {'**retenu**' if ok else 'non'} |")
    S = df[df["kind"] == "S"]
    cov = S[S["has_ed"]]
    L += ["", "## Complément : coïncidence avec une date de résultats Yahoo", "",
          f"Actions avec au moins une date de résultats disponible : {cover} sur {n}. Chocs sur ces actions : {len(cov)} ; dont coïncidant avec une date de résultats : {int(cov['earn'].sum())}.", ""]
    if len(cov) >= 60:
        L += ["| test | avec date de résultats | sans | moyenne avec | moyenne sans | écart | IC |", "|---|---|---|---|---|---|---|"]
        for dr in DIRS:
            for h in HORIZONS:
                d = cov[(cov["dir"] == dr) & (cov["h"] == h)]
                f = d["earn"].to_numpy(bool)
                if f.sum() >= 30 and (~f).sum() >= 30:
                    diff, lo, hi = regime.diff_ci(d["time"], d["ret"].to_numpy(), f, rng, 95)
                    L.append(f"| {dr}, {h} s | {int(f.sum())} | {int((~f).sum())} | {pct(d[f]['ret'].mean())} | {pct(d[~f]['ret'].mean())} | {pct(diff)} | [{pct(lo)} ; {pct(hi)}] |")
    kept = [f"après choc {dr}, {h} séances" for (dr, h), v in verdict.items() if v[0]]
    L += ["", "## Verdict", "", "- Retenus : " + (", ".join(kept) if kept else "aucun — les chocs d'information ne donnent pas de dérive ni de rebond exploitable après frais."), "",
          "Lecture : même un test retenu doit se confirmer en réel (achat à l'ouverture suivant un choc = prix parfois déjà très déplacé, liquidité limitée) ; "
          "sur 4 tests, un seul « retenu » peut venir du hasard."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fee", type=float, default=0.398)
    ap.add_argument("--out", default="events_report.md")
    ap.add_argument("--no-earnings", action="store_true", help="ne pas interroger Yahoo pour les dates de résultats")
    args = ap.parse_args()
    rng = np.random.default_rng(2026)
    df, n, cover = collect(args, rng, with_earnings=not args.no_earnings)
    text = report(df, n, cover, args.fee / 100.0, rng)
    Path(args.out).write_text(text, encoding="utf-8")
    df.to_csv("events_trades.csv", index=False)
    print(text)


if __name__ == "__main__":
    main()
