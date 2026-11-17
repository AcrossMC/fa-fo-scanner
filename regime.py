"""
Dernier test discipliné : le S hebdomadaire (SL large, TP éloigné) ne marche-t-il que dans certains états du marché ou de l'action ?

Règles du test, fixées AVANT de regarder les résultats (pour ne pas fabriquer un faux avantage en essayant mille filtres) :
  - une seule stratégie : S haussière hebdomadaire, SL = base avec distance minimale 1,5 x ATR, sortie au TP5 (+ TP4, TP3 et atr1/TP5 en contrôle) ;
  - 5 hypothèses seulement, chacune avec son sens attendu (S est une bougie de rebond : le contexte baissier devrait être favorable) :
      H1 le CAC 40 est sous sa moyenne mobile 200 jours
      H2 le CAC 40 est à plus de 10 % sous son plus haut de 52 semaines
      H3 le CAC 40 est en Fo hebdomadaire (même définition que pour les actions)
      H4 l'action est à plus de 30 % sous son plus haut de 52 semaines
      H5 l'action est en Fo mensuelle
  - apprentissage : entrées jusqu'en 2015 ; test : 2016 et après. Une hypothèse n'est retenue que si l'écart (vrai - faux) est positif ET significatif
    à 99 % en apprentissage ET positif et significatif à 95 % en test (rééchantillonnage par mois). Avec 5 hypothèses, un « hasard heureux » est encore possible : c'est pourquoi le test compte.
  - comparaison avec le hasard : entrées au hasard (même action, même année, même état) avec les mêmes SL / TP.
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

COMBOS = [("atr1.5", "TP5"), ("atr1.5", "TP4"), ("atr1.5", "TP3"), ("atr1", "TP5")]
HYP = {
    "H1": "CAC 40 sous sa MM 200 jours",
    "H2": "CAC 40 à plus de 10 % sous son plus haut 52 sem.",
    "H3": "CAC 40 en Fo hebdomadaire",
    "H4": "action à plus de 30 % sous son plus haut 52 sem.",
    "H5": "action en Fo mensuelle",
}
SPLIT_YEAR = 2016
N_RANDOM = 5
BOOT = 2000


def market_state(idx_df, now):
    cm = idx_df["Close"].to_numpy(dtype=float)
    s = pd.Series(cm)
    M = {"ns": idx_df.index.as_unit("ns").asi8, "c": cm, "ma200": s.rolling(200).mean().to_numpy(), "hi": s.rolling(252).max().to_numpy()}
    M["w"] = profit.prep(sc.aggregate(idx_df, "1w"), "1w", now)
    return M


def flags(A, k, t_ns, arrs, M):
    """Les 5 hypothèses à l'instant t (bougie k de l'action) ; None si non calculable."""
    out = {}
    j = int(np.searchsorted(M["ns"], t_ns, side="right")) - 1
    if j >= 0 and np.isfinite(M["ma200"][j]):
        out["H1"] = bool(M["c"][j] < M["ma200"][j])
    if j >= 0 and np.isfinite(M["hi"][j]):
        out["H2"] = bool(M["c"][j] / M["hi"][j] - 1 <= -0.10)
    W = M["w"]
    jw = profit.asof(W, t_ns) if W is not None else -1
    if jw >= 0 and W["st"][jw] != 0:
        out["H3"] = bool(W["st"][jw] == -1)
    if k >= 52:
        out["H4"] = bool(A["c"][k] / A["h"][k - 52:k + 1].max() - 1 <= -0.30)
    B = arrs.get("1mo")
    jm = profit.asof(B, t_ns) if B is not None else -1
    if jm >= 0 and B["st"][jm] != 0:
        out["H5"] = bool(B["st"][jm] == -1)
    return out


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
    for t in list(daily):
        daily[t], _ = profit.clean_series(daily[t])
    idx_df = sc.download(["^FCHI"], "max", "1d").get("^FCHI")
    if idx_df is None or len(idx_df) < 1000:
        raise SystemExit("Indice CAC 40 (^FCHI) indisponible sur Yahoo : test impossible.")
    now = pd.Timestamp.now(tz=sc.PARIS)
    M = market_state(idx_df, now)
    turn = {t: sc.turnover(daily[t]) for t in daily}
    ranked = sorted((t for t in daily if turn[t] >= sc.MIN_TURNOVER_EUR), key=lambda t: turn[t], reverse=True)
    if args.tickers:
        ranked = ranked[:args.tickers]
    rows, base, t0 = [], [], time.time()
    for i, t in enumerate(ranked, 1):
        frames = {"1d": daily[t], "1w": sc.aggregate(daily[t], "1w"), "1mo": sc.aggregate(daily[t], "1mo"), "3mo": sc.aggregate(daily[t], "3mo")}
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
        pos = {ts: k for k, ts in enumerate(A["ends"])}
        years, by = robust.random_year_index(A)
        s_list, _ = profit.s_trades(t, A, arrs, fee_rt)
        for x in s_list:
            k = pos.get(x["entry_time"])
            if k is None:
                continue
            fl = flags(A, k, A["end_ns"][k], arrs, M)
            for sv, name in COMBOS:
                if (sv, name) in x["res"]:
                    rows.append({"combo": f"{sv}|{name}", "ticker": t, "time": x["entry_time"], **fl, "ret": x["res"][(sv, name)][0]})
            cand = by.get(x["entry_time"].year, [])
            for kk in (rng.choice(cand, size=min(N_RANDOM, len(cand)), replace=False) if cand else []):
                entry = A["c"][kk]
                tps, sls = profit.tp_levels(A, kk, arrs, entry), profit.sl_levels(A, kk, arrs, entry)
                f2 = flags(A, kk, A["end_ns"][kk], arrs, M)
                for sv, name in COMBOS:
                    sl = sls[sv]
                    if not np.isfinite(sl) or sl >= entry * 0.9995:
                        continue
                    r = profit.simulate(A, kk, entry, sl, tps, profit.SCHEMES[name])
                    if r is not None:
                        base.append({"combo": f"{sv}|{name}", "ticker": t, "time": A["ends"][kk], **f2, "ret": r[0] - fee_rt})
        if i % 20 == 0:
            print(f"  {i}/{len(ranked)} actions, {len(rows)} signaux S hebdo, {int(time.time() - t0)} s", flush=True)
    return pd.DataFrame(rows), pd.DataFrame(base), len(ranked)


def diff_ci(times, vals, flag, rng, level=95):
    """Écart de moyenne (flag vrai - faux) et IC par rééchantillonnage des mois."""
    months = pd.PeriodIndex(pd.DatetimeIndex(times).tz_localize(None), freq="M")
    df = pd.DataFrame({"m": months, "r": vals, "f": flag})
    g = df.groupby(["m", "f"])["r"].agg(["sum", "count"]).unstack("f").fillna(0.0)
    if True not in g["sum"].columns or False not in g["sum"].columns:
        return np.nan, np.nan, np.nan
    st, ct, sf, cf = g["sum"][True].to_numpy(), g["count"][True].to_numpy(), g["sum"][False].to_numpy(), g["count"][False].to_numpy()
    point = st.sum() / max(ct.sum(), 1) - sf.sum() / max(cf.sum(), 1)
    if len(st) < 6:
        return point, np.nan, np.nan
    idx = rng.integers(0, len(st), size=(BOOT, len(st)))
    with np.errstate(invalid="ignore", divide="ignore"):
        d = st[idx].sum(1) / ct[idx].sum(1) - sf[idx].sum(1) / cf[idx].sum(1)
    d = d[np.isfinite(d)]
    a = (100 - level) / 2
    return point, float(np.percentile(d, a)), float(np.percentile(d, 100 - a))


def report(rows, base, n, fee, rng):
    pct = robust.pct
    L = [f"# S hebdomadaire et état du marché — {n} actions françaises, frais {fee * 100:.2f} % par ordre", "",
         "**Règles fixées avant les résultats** : stratégie unique (S haussière hebdo, SL ≥ 1,5 ATR, TP5) ; 5 hypothèses, sens attendu « contexte baissier favorable » ; apprentissage "
         f"jusqu'en {SPLIT_YEAR - 1}, test à partir de {SPLIT_YEAR}. Une hypothèse est retenue seulement si l'écart (vrai − faux) est > 0 avec IC 99 % > 0 en apprentissage "
         "ET IC 95 % > 0 en test.", "",
         "| combinaison | hypothèse | période | n (vrai / faux) | moyenne si vrai | moyenne si faux | écart | IC | hasard (si vrai) | avantage sur le hasard (vrai) | verdict |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    year = pd.DatetimeIndex(rows["time"]).year
    byear = pd.DatetimeIndex(base["time"]).year if len(base) else None
    for combo in [c[0] + "|" + c[1] for c in COMBOS]:
        d_all = rows[rows["combo"] == combo]
        for h in HYP:
            res = {}
            for per, sel in (("apprentissage", year < SPLIT_YEAR), ("test", year >= SPLIT_YEAR)):
                d = d_all[(sel[rows["combo"] == combo]) & d_all[h].notna()] if h in d_all else d_all.iloc[0:0]
                if len(d) < 30 or d[h].nunique() < 2:
                    res[per] = None
                    continue
                f = d[h].astype(bool).to_numpy()
                lvl = 99 if per == "apprentissage" else 95
                diff, lo, hi = diff_ci(d["time"], d["ret"].to_numpy(), f, rng, lvl)
                bsel = base[(base["combo"] == combo) & base[h].notna() & ((byear < SPLIT_YEAR) if per == "apprentissage" else (byear >= SPLIT_YEAR))] if h in base else base.iloc[0:0]
                hb = bsel[bsel[h].astype(bool)]["ret"].mean() if len(bsel) else np.nan
                mt = d[f]["ret"].mean()
                res[per] = (diff, lo, hi, int(f.sum()), int((~f).sum()), mt, d[~f]["ret"].mean(), hb)
            ok = bool(res.get("apprentissage") and res.get("test") and res["apprentissage"][1] > 0 and res["test"][1] > 0)
            for per in ("apprentissage", "test"):
                r = res.get(per)
                if r is None:
                    continue
                diff, lo, hi, nt, nf, mt, mf, hb = r
                L.append(f"| {combo.replace('|', ' · ')} | {h} {HYP[h]} | {per} | {nt} / {nf} | {pct(mt)} | {pct(mf)} | **{pct(diff)}** | [{pct(lo)} ; {pct(hi)}] | {pct(hb)} | "
                         f"{pct(mt - hb)} | {('**retenue**' if ok else 'non') if per == 'test' else ''} |")
    L += ["", "Lecture : « retenue » = l'écart est positif et significatif dans les deux périodes. Si aucune hypothèse n'est retenue, le S hebdomadaire n'a pas de condition de marché "
          "démontrée qui le rende rentable : l'avantage vu sur l'ensemble des années vient alors des rebonds de marché eux-mêmes."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=int, default=0)
    ap.add_argument("--fee", type=float, default=0.398)
    ap.add_argument("--out", default="regime_report.md")
    args = ap.parse_args()
    rng = np.random.default_rng(2026)
    rows, base, n = collect(args, rng)
    text = report(rows, base, n, args.fee / 100.0, rng)
    Path(args.out).write_text(text, encoding="utf-8")
    rows.to_csv("regime_trades.csv", index=False)
    print(text)


if __name__ == "__main__":
    main()
