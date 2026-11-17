"""
Les résultats de Fa et de S dépendent-ils du CAC 40 ?

Deux questions, règles fixées avant les résultats :
  A. Part du marché : sur la durée de chaque trade, combien le CAC 40 a-t-il fait, et que reste-t-il une fois ce mouvement retiré (rendement de l'action - rendement du CAC) ?
     Si l'excès est nul, la configuration ne fait que suivre le marché.
  B. Contexte à l'entrée : résultats selon la tendance du CAC sur les 20 dernières séances (baisse < -3 %, stable, hausse > +3 %) et selon sa position par rapport à sa MM 200 jours.
Trades : Fa (entrée à la validation, sortie au Fo, comme l'application) et S haussière (SL 1,5 ATR, sortie au TP5), en 1 jour et en hebdo. Frais déduits.
Comparaison avec le hasard pour S (entrées au hasard, même action, même année, mêmes SL/TP) dans chaque contexte.
Marges d'erreur : rééchantillonnage par mois.
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

TFS = ("1d", "1w")
N_RANDOM = 3
BOOT = 2000
TREND_BUCKETS = (("baisse (< -3 %)", -9, -0.03), ("stable", -0.03, 0.03), ("hausse (> +3 %)", 0.03, 9))


def cac_prep(df):
    c = df["Close"].to_numpy(dtype=float)
    s = pd.Series(c)
    return {"ns": df.index.as_unit("ns").asi8, "c": c, "ma200": s.rolling(200).mean().to_numpy(),
            "r20": (s / s.shift(20) - 1).to_numpy()}


def cac_at(M, t_ns):
    return int(np.searchsorted(M["ns"], t_ns, side="right")) - 1


def ctx(M, t_ns):
    j = cac_at(M, t_ns)
    if j < 20 or not np.isfinite(M["ma200"][j]):
        return None
    r = M["r20"][j]
    b = next(n for n, lo, hi in TREND_BUCKETS if lo <= r < hi) if np.isfinite(r) else None
    return {"trend": b, "ma": "CAC au-dessus MM200" if M["c"][j] >= M["ma200"][j] else "CAC sous MM200"}


def cac_ret(M, t0, t1):
    j0, j1 = cac_at(M, t0), cac_at(M, t1)
    return M["c"][j1] / M["c"][j0] - 1 if j0 >= 0 and j1 > j0 else np.nan


def s_exit(A, k, entry, sl, tp):
    """Même règle que profit.simulate (SL d'abord, TP fixé à l'entrée) mais renvoie aussi la bougie de sortie."""
    for j in range(k + 1, min(A["n"], k + 1 + profit.MAX_BARS)):
        if A["l"][j] <= sl:
            return min(A["o"][j], sl) / entry - 1, j
        if A["h"][j] >= tp:
            return max(A["o"][j], tp) / entry - 1, j
    return None


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
    idx = sc.download(["^FCHI"], "max", "1d").get("^FCHI")
    if idx is None or len(idx) < 1000:
        raise SystemExit("Indice CAC 40 (^FCHI) indisponible sur Yahoo : test impossible.")
    M = cac_prep(idx)
    now = pd.Timestamp.now(tz=sc.PARIS)
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
        for tf in TFS:
            A = arrs.get(tf)
            if A is None:
                continue
            ens = A["end_ns"]
            st, c = A["st"], A["c"]
            years, by = robust.random_year_index(A)
            # ---- Fa : entrée à la validation, sortie au Fo ----
            for k in range(1, A["n"]):
                if not (st[k] == 1 and st[k - 1] != 1):
                    continue
                m = k + 1
                while m < A["n"] and st[m] != -1:
                    m += 1
                if m >= A["n"]:
                    continue
                cx = ctx(M, ens[k])
                mk = cac_ret(M, ens[k], ens[m])
                if cx is None or not np.isfinite(mk):
                    continue
                rows.append({"cfg": "Fa", "tf": tf, "ticker": t, "time": A["ends"][k], **cx, "ret": c[m] / c[k] - 1 - fee_rt, "cac": mk})
            # ---- S : SL 1,5 ATR, TP5 ----
            for k in np.flatnonzero(A["up"]):
                if k < 2 or k + 1 >= A["n"]:
                    continue
                entry = c[k]
                tps, sls = profit.tp_levels(A, k, arrs, entry), profit.sl_levels(A, k, arrs, entry)
                sl = sls["atr1.5"]
                if len(tps) < 5 or not np.isfinite(sl) or sl >= entry * 0.9995:
                    continue
                r = s_exit(A, k, entry, sl, tps[4])
                cx = ctx(M, ens[k])
                if r is None or cx is None:
                    continue
                mk = cac_ret(M, ens[k], ens[r[1]])
                if np.isfinite(mk):
                    rows.append({"cfg": "S", "tf": tf, "ticker": t, "time": A["ends"][k], **cx, "ret": r[0] - fee_rt, "cac": mk})
                cand = by.get(A["ends"][k].year, [])
                for kk in (rng.choice(cand, size=min(N_RANDOM, len(cand)), replace=False) if cand else []):
                    e2 = c[kk]
                    tp2, sl2 = profit.tp_levels(A, kk, arrs, e2), profit.sl_levels(A, kk, arrs, e2)["atr1.5"]
                    if len(tp2) < 5 or not np.isfinite(sl2) or sl2 >= e2 * 0.9995:
                        continue
                    r2 = s_exit(A, kk, e2, sl2, tp2[4])
                    c2 = ctx(M, ens[kk])
                    if r2 is None or c2 is None:
                        continue
                    mk2 = cac_ret(M, ens[kk], ens[r2[1]])
                    if np.isfinite(mk2):
                        base.append({"cfg": "S", "tf": tf, "ticker": t, "time": A["ends"][kk], **c2, "ret": r2[0] - fee_rt, "cac": mk2})
        if i % 20 == 0:
            print(f"  {i}/{len(ranked)} actions, {len(rows)} trades, {int(time.time() - t0)} s", flush=True)
    return pd.DataFrame(rows), pd.DataFrame(base), len(ranked)


def mean_ci(times, vals, rng, level=95):
    months = pd.PeriodIndex(pd.DatetimeIndex(times).tz_localize(None), freq="M")
    g = pd.DataFrame({"m": months, "r": vals}).groupby("m")["r"].agg(["sum", "count"])
    s, c = g["sum"].to_numpy(), g["count"].to_numpy()
    if len(s) < 6:
        return np.nan, np.nan
    idx = rng.integers(0, len(s), size=(BOOT, len(s)))
    m = s[idx].sum(1) / c[idx].sum(1)
    a = (100 - level) / 2
    return float(np.percentile(m, a)), float(np.percentile(m, 100 - a))


def report(rows, base, n, fee, rng):
    pct = robust.pct
    L = [f"# Fa, S et CAC 40 — {n} actions françaises, frais {fee * 100:.2f} % par ordre", "",
         "**A. Part du marché.** *CAC pendant le trade* = variation du CAC 40 entre l'entrée et la sortie de chaque trade. *Excès* = rendement de l'action moins celui du CAC sur la même période "
         "(IC 95 % par rééchantillonnage des mois). *Corrélation* = entre le rendement du trade et celui du CAC sur la période. Si l'excès n'est pas nettement positif, "
         "la configuration ne fait que suivre le marché.", "",
         "| config | UT | trades | rendement moyen | CAC pendant le trade | excès moyen | IC 95 % de l'excès | corrélation |", "|---|---|---|---|---|---|---|---|"]
    for cfg in ("Fa", "S"):
        for tf in TFS:
            d = rows[(rows["cfg"] == cfg) & (rows["tf"] == tf)]
            if len(d) < 50:
                continue
            ex = (d["ret"] - d["cac"]).to_numpy()
            lo, hi = mean_ci(d["time"], ex, rng)
            L.append(f"| {cfg} | {tf} | {len(d)} | **{pct(d['ret'].mean())}** | {pct(d['cac'].mean())} | **{pct(ex.mean())}** | [{pct(lo)} ; {pct(hi)}] | {np.corrcoef(d['ret'], d['cac'])[0, 1]:.2f} |")
    L += ["", "**B. Contexte du CAC à l'entrée.** Pour S, « hasard » = entrées au hasard (même action, même année) avec les mêmes SL / TP dans le même contexte ; « avantage » = S moins hasard. "
          "Une configuration n'est « utile dans ce contexte » que si son excès sur le CAC a un IC 95 % > 0 (et, pour S, si elle bat le hasard).", "",
          "| config | UT | contexte du CAC | trades | rendement moyen | IC 95 % | excès sur le CAC | IC 95 % de l'excès | hasard | avantage sur le hasard |", "|---|---|---|---|---|---|---|---|---|---|"]
    for cfg in ("Fa", "S"):
        for tf in TFS:
            for col, labels in (("trend", [b[0] for b in TREND_BUCKETS]), ("ma", ["CAC au-dessus MM200", "CAC sous MM200"])):
                for lab in labels:
                    d = rows[(rows["cfg"] == cfg) & (rows["tf"] == tf) & (rows[col] == lab)]
                    if len(d) < 50:
                        continue
                    lo, hi = mean_ci(d["time"], d["ret"].to_numpy(), rng)
                    ex = (d["ret"] - d["cac"]).to_numpy()
                    elo, ehi = mean_ci(d["time"], ex, rng)
                    hb = np.nan
                    if cfg == "S" and len(base):
                        b = base[(base["cfg"] == "S") & (base["tf"] == tf) & (base[col] == lab)]
                        hb = b["ret"].mean() if len(b) >= 30 else np.nan
                    L.append(f"| {cfg} | {tf} | {lab} | {len(d)} | **{pct(d['ret'].mean())}** | [{pct(lo)} ; {pct(hi)}] | {pct(ex.mean())} | [{pct(elo)} ; {pct(ehi)}] | "
                             f"{pct(hb) if cfg == 'S' else '–'} | {pct(d['ret'].mean() - hb) if cfg == 'S' else '–'} |")
    L += ["", "Lecture : le CAC 40 est seulement un indicateur de contexte (les actions suivies sont françaises, donc très corrélées). Aucun contexte n'est retenu comme règle "
          "sans excès > 0 net et stable ; ces tableaux servent à voir si un filtre « marché » vaut la peine d'être testé à part avec apprentissage / test."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=int, default=0)
    ap.add_argument("--fee", type=float, default=0.398)
    ap.add_argument("--out", default="market_report.md")
    args = ap.parse_args()
    rng = np.random.default_rng(2026)
    rows, base, n = collect(args, rng)
    text = report(rows, base, n, args.fee / 100.0, rng)
    Path(args.out).write_text(text, encoding="utf-8")
    rows.to_csv("market_trades.csv", index=False)
    print(text)


if __name__ == "__main__":
    main()
