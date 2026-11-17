"""
Deux questions sur la configuration Fa :

  A. Ajouter des objectifs (TP : MM7/20/50/100/200 de la même UT, Tenkan et MM7 baissière des UT supérieures jusqu'à 3 mois) améliore-t-il les
     résultats par rapport à « sortie au Fo seulement » ?  (historique complet, 1j / 1sem / 1mois / 4h)
     Comparaison appariée (même entrée, avec et sans TP) + comparaison à des entrées au hasard EN COURS DE Fa, même année, même règle de sortie.

  B. Entrer AVANT que la Fa se réalise (à 14h, quand la probabilité calculée par le scanner dépasse un seuil) et/ou sortir AVANT le Fo (à 14h, quand la
     probabilité de Fo dépasse le seuil) donne-t-il de meilleurs résultats que d'attendre la clôture ?  (journalier ; historique horaire = 2 ans seulement)
       entrées : E0 = à la clôture de la bougie Fa validée (référence) ; E1 = à 14h dès que p(Fa) >= seuil, puis on garde ;
                 E2 = comme E1 mais on revend à la clôture si la Fa ne s'est finalement pas validée.
       sorties : X0 = à la clôture de la bougie Fo (référence) ; X1 = à 14h dès que p(Fo) >= seuil, sinon à la clôture du Fo.
     Les probabilités sont celles du scanner (simulation + correction), calculées avec les seules données connues à 14h.

Frais : --fee par ordre (en %). Usage : python fa_study.py --fee 0.5 --out fa_study_report.md
"""
import argparse
import json
import multiprocessing as mp
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

import backtest as bt
import profit
import robust
import scanner as sc

PLANS = {
    "Fo seul": [(1.0, None)],
    "TP1 ou Fo": [(1.0, 0)], "TP2 ou Fo": [(1.0, 1)], "TP3 ou Fo": [(1.0, 2)], "TP4 ou Fo": [(1.0, 3)], "TP5 ou Fo": [(1.0, 4)],
    "½ TP1 + ½ Fo": [(0.5, 0), (0.5, None)], "½ TP2 + ½ Fo": [(0.5, 1), (0.5, None)], "½ TP3 + ½ Fo": [(0.5, 2), (0.5, None)],
}
THRESHOLDS = (0.5, 0.6, 0.7, 0.8)


# ============================ Partie A : TP sur Fa ============================

def simulate_fa(A, k, tps, plan):
    """Sortie par TP (si touché) ou par la clôture de la 1re bougie Fo. Renvoie (rendement brut pondéré, bougies) ou None si jamais résolu."""
    entry = A["c"][k]
    done = [False] * len(plan)
    pnl = 0.0
    last = min(A["n"], k + 1 + profit.MAX_BARS)
    for j in range(k + 1, last):
        for i, (frac, idx) in enumerate(plan):
            if done[i] or idx is None or idx >= len(tps):
                continue
            if A["h"][j] >= tps[idx]:
                pnl += frac * (max(A["o"][j], tps[idx]) / entry - 1)
                done[i] = True
        if A["st"][j] == -1:
            for i, (frac, _idx) in enumerate(plan):
                if not done[i]:
                    pnl += frac * (A["c"][j] / entry - 1)
            return pnl, j - k
        if all(done):
            return pnl, j - k
    return None


def part_a(args, rng):
    fee_rt = 2 * args.fee / 100.0
    rows, base = [], []
    stocks = robust.load_stocks(args)
    for t, arrs in stocks:
        for tf in profit.TRADE_TFS:
            A = arrs.get(tf)
            if A is None:
                continue
            years, by = robust.random_year_index(A)
            ongoing = {y: [k for k in ks if A["st"][k] == 1 and A["st"][k - 1] == 1] for y, ks in by.items()}
            for k in range(1, A["n"]):
                if not (A["st"][k] == 1 and A["st"][k - 1] != 1):
                    continue
                tps = profit.tp_levels(A, k, arrs, A["c"][k])
                res = {name: simulate_fa(A, k, tps, plan) for name, plan in PLANS.items()}
                if res["Fo seul"] is None:
                    continue
                row = {"tf": tf, "ticker": t, "time": A["ends"][k], "n_tp": len(tps)}
                for name, r in res.items():
                    if r is not None:
                        row[name] = r[0] - fee_rt
                        row["bars|" + name] = r[1]
                rows.append(row)
                cand = ongoing.get(A["ends"][k].year, [])
                for kk in (rng.choice(cand, size=min(3, len(cand)), replace=False) if cand else []):
                    tps2 = profit.tp_levels(A, kk, arrs, A["c"][kk])
                    brow = {"tf": tf, "ticker": t, "time": A["ends"][kk]}
                    for name, plan in PLANS.items():
                        r = simulate_fa(A, kk, tps2, plan)
                        if r is not None:
                            brow[name] = r[0] - fee_rt
                    base.append(brow)
    return pd.DataFrame(rows), pd.DataFrame(base)


def report_a(rows, base, rng):
    L = ["## A. Ajouter des objectifs (TP) aux Fa", "",
         "Sortie = TP touché, sinon clôture de la 1re bougie Fo. Pas de stop loss (règle des Fa). Si le TP demandé n'existe pas (moins de n niveaux au-dessus), "
         "le trade est « Fo seul » : l'échantillon est donc identique pour tous les schémas. **Δ vs Fo seul** = différence moyenne sur les mêmes trades, avec "
         "marge d'erreur à 95 % (rééchantillonnage par mois). « Hasard » = entrées au hasard dans une Fa déjà en cours (même action, UT, année), même règle de sortie.", ""]
    for tf in profit.TRADE_TFS:
        d = rows[rows["tf"] == tf]
        if len(d) < 30:
            continue
        b = base[base["tf"] == tf] if len(base) else base
        L += [f"### {tf} — {len(d)} Fa", "", "| schéma | moyenne | médiane | succès | Δ vs Fo seul [IC 95 %] | hasard | avantage | durée moy. (bougies) |", "|---|---|---|---|---|---|---|---|"]
        ref = d["Fo seul"].to_numpy()
        for name in PLANS:
            if name not in d:
                continue
            r = d[name].to_numpy()
            diff = r - ref
            lo, hi = robust.boot_ci(d["time"], diff, rng) if name != "Fo seul" else (np.nan, np.nan)
            bm = b[name].mean() if name in b and len(b) else np.nan
            bars = d["bars|" + name].mean() if "bars|" + name in d else np.nan
            dtxt = "—" if name == "Fo seul" else f"{robust.pct(diff.mean())} [{robust.pct(lo)} ; {robust.pct(hi)}]"
            L.append(f"| {name} | **{robust.pct(r.mean())}** | {robust.pct(np.median(r))} | {robust.pct((r > 0).mean(), 0)} | {dtxt} | {robust.pct(bm)} | {robust.pct(r.mean() - bm)} | {bars:.1f} |")
        L.append("")
    return L


# ============================ Partie B : entrer / sortir avant ============================

def day_snapshots(t, hourly, seed):
    """Pour chaque jour de séance : prix à 14h, p(Fa), p(Fo) calculées avec les données de 14h, + état à la clôture (tableaux journaliers)."""
    hourly = hourly.dropna(subset=["Open", "High", "Low", "Close"])
    hourly = hourly[hourly.index.tz_localize(None).weekday < 5]
    if len(hourly) < 500:
        return None
    rng = np.random.default_rng(seed)
    d_full = bt.daily_from_hourly(hourly)
    now = pd.Timestamp.now(tz=sc.PARIS)
    A = profit.prep(d_full, "1d", now)
    if A is None:
        return None
    naive_h = hourly.index.tz_localize(None)
    snaps = {}
    for i in range(sc.MIN_BARS, A["n"]):
        day = A["ends"][i].normalize()
        dn = day.tz_localize(None)
        prev_d = d_full[d_full.index < day]
        in_day = naive_h.normalize() == dn
        ends_ok = in_day & (naive_h + pd.Timedelta(hours=1) <= dn + pd.Timedelta(hours=14))
        part = bt.merge_rows(hourly[ends_ok], day)
        if part is None or len(prev_d) < sc.MIN_BARS:
            continue
        res = sc.analyze_tf(pd.concat([prev_d, part]), "1d", day.replace(hour=14, minute=0), rng)
        if res is None or not res["forming"] or res.get("p_fa") is None:
            continue
        snaps[i] = (float(part["Close"].iloc[-1]), float(res["p_fa"]), float(res["p_fo"]), res["state"])
    return A["c"], A["st"], [e for e in A["ends"]], snaps


def _snap_job(args):
    t, hourly, seed = args
    try:
        return t, day_snapshots(t, hourly, seed)
    except Exception:
        return t, None


def run_modes(c, st, ends, snaps, thr, entry_mode, exit_mode, fee_rt):
    """Machine à états journalière. Renvoie la liste (date d'entrée, rendement net, jours)."""
    out = []
    pos = None            # (prix d'entrée, indice du jour d'entrée, validée ?)
    n = len(c)
    for i in range(1, n):
        s = snaps.get(i)
        if pos is None:
            if entry_mode == "E0":
                if st[i] == 1 and st[i - 1] != 1:
                    pos = (c[i], i, True)
            elif s is not None and st[i - 1] != 1 and s[3] != "fa" and s[1] >= thr:
                pos = (s[0], i, False)
                if entry_mode == "E2" and st[i] != 1:                 # la Fa ne s'est pas validée à la clôture : on revend
                    out.append((ends[i], c[i] / s[0] - 1 - fee_rt, 0))
                    pos = None
            continue
        entry, i0, _ = pos
        if i == i0:
            continue
        if exit_mode == "X1" and s is not None and s[3] != "fo" and s[2] >= thr:
            out.append((ends[i0], s[0] / entry - 1 - fee_rt, i - i0))
            pos = None
            continue
        if st[i] == -1:
            out.append((ends[i0], c[i] / entry - 1 - fee_rt, i - i0))
            pos = None
    return out


def part_b(args):
    fee_rt = 2 * args.fee / 100.0
    stocks_h, ranked = robust.load_hourly(args)
    jobs = [(t, stocks_h[t], 2026 + i) for i, t in enumerate(ranked) if t in stocks_h]
    data, t0 = {}, time.time()
    with mp.Pool(max(1, os.cpu_count() or 1)) as pool:
        for i, (t, r) in enumerate(pool.imap_unordered(_snap_job, jobs), 1):
            if r is not None:
                data[t] = r
            if i % 20 == 0:
                print(f"  instantanés 14h : {i}/{len(jobs)} actions, {int(time.time() - t0)} s", flush=True)
    rows = []
    for thr in THRESHOLDS:
        for em in ("E0", "E1", "E2"):
            for xm in ("X0", "X1"):
                if em == "E0" and xm == "X0" and thr != THRESHOLDS[0]:
                    continue
                for t, (c, st, ends, snaps) in data.items():
                    for (time_, ret, days) in run_modes(c, st, ends, snaps, thr, em, xm, fee_rt):
                        rows.append({"thr": None if (em, xm) == ("E0", "X0") else thr, "mode": f"{em}/{xm}", "ticker": t, "time": time_, "ret": ret, "days": days})
    return pd.DataFrame(rows), len(data)


def report_b(rows, n, rng):
    L = ["## B. Entrer avant la Fa, sortir avant le Fo (journalier, 14h)", "",
         f"{n} actions, environ 2 ans d'historique horaire (limite Yahoo) : échantillon beaucoup plus petit que A, conclusions à prendre avec prudence. "
         "E0/X0 = référence (clôture). E1 = entrée à 14h dès que p(Fa) ≥ seuil ; E2 = comme E1 mais revente à la clôture si la Fa ne s'est pas validée ; "
         "X1 = sortie à 14h dès que p(Fo) ≥ seuil. Δ = moyenne par trade − référence E0/X0 (marge d'erreur par mois). "
         "« Rendement / jour » tient compte du temps passé en position (une stratégie qui sort plus tôt ne doit pas être jugée seulement par trade).", ""]
    ref = rows[rows["mode"] == "E0/X0"]
    L += ["| mode | seuil | trades | moyenne | médiane | succès | jours moy. | rendement / jour | Δ vs E0/X0 [IC 95 %] |", "|---|---|---|---|---|---|---|---|---|"]

    def line(d, mode, thr):
        r = d["ret"].to_numpy()
        days = max(d["days"].mean(), 0.5)
        if mode == "E0/X0":
            dtxt = "—"
        else:
            lo, hi = robust.boot_ci(d["time"], r, rng)
            dtxt = f"{robust.pct(r.mean() - ref['ret'].mean())} [{robust.pct(lo - ref['ret'].mean())} ; {robust.pct(hi - ref['ret'].mean())}]"
        return (f"| {mode} | {'' if thr is None else int(thr * 100)} | {len(d)} | **{robust.pct(r.mean())}** | {robust.pct(np.median(r))} | "
                f"{robust.pct((r > 0).mean(), 0)} | {d['days'].mean():.1f} | {robust.pct(r.sum() / max(d['days'].sum(), 1), 3)} | {dtxt} |")
    if len(ref):
        L.append(line(ref, "E0/X0", None))
    for mode in ("E0/X1", "E1/X0", "E1/X1", "E2/X0", "E2/X1"):
        for thr in THRESHOLDS:
            d = rows[(rows["mode"] == mode) & (rows["thr"] == thr)]
            if len(d) >= 30:
                L.append(line(d, mode, thr))
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=int, default=0)
    ap.add_argument("--fee", type=float, default=0.5)
    ap.add_argument("--no-4h", action="store_true")
    ap.add_argument("--out", default="fa_study_report.md")
    args = ap.parse_args()
    rng = np.random.default_rng(2026)
    rows_a, base_a = part_a(args, rng)
    text = [f"# Fa : objectifs et entrées / sorties anticipées — frais {args.fee:.2f} % par ordre", ""]
    text += report_a(rows_a, base_a, rng)
    rows_b, n = part_b(args)
    text += report_b(rows_b, n, rng)
    out = "\n".join(text)
    Path(args.out).write_text(out, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
