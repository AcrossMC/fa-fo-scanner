#!/usr/bin/env python3
"""
Mesure de fiabilité des probabilités du scanner (Fa, Fo, S haussière, S baissière).

Principe : on rejoue le passé. À des instants précis de la séance (11h, 14h, 15h, heure de Paris), on reconstruit la bougie en
formation telle que le scanner l'aurait vue (avec les données connues à cet instant seulement), on calcule la probabilité avec
le MÊME code que le scanner (analyze_tf), puis on regarde ce qui s'est réellement passé à la clôture de la bougie.
Si les probabilités sont bien calibrées, parmi les cas annoncés « 60 % », environ 60 % doivent s'être réalisés.

Source : historique Yahoo en 1h (environ 2 ans). Unités testées : 4h, 1j, 1 semaine (le 1 mois a trop peu de bougies).
Usage : python backtest.py [--tickers 80] [--out backtest_report.md]
"""
import argparse
import multiprocessing as mp
import os
import sys
import time

import numpy as np
import pandas as pd

import scanner as sc

SNAP_HOURS = (11, 14, 15)          # instants de test (heure de Paris), en heures pleines
WEEK_SNAP = (1, 3)                 # semaine : mardi et jeudi, à 14h
BINS = [(0.0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]
KINDS = (("Fa", "p_fa"), ("Fo", "p_fo"), ("S haussière", "p_s_up"), ("S baissière", "p_s_down"))


def daily_from_hourly(h):
    key = h.index.tz_localize(None).normalize()
    g = h.groupby(key)
    out = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                        "Close": g["Close"].last(), "Volume": g["Volume"].sum()})
    out.index = pd.DatetimeIndex(out.index).tz_localize(sc.PARIS)
    return out


def merge_rows(rows, index_value):
    """Plusieurs lignes OHLC -> une seule bougie (celle en formation)."""
    if not len(rows):
        return None
    r = pd.DataFrame({"Open": [rows["Open"].iloc[0]], "High": [rows["High"].max()], "Low": [rows["Low"].min()],
                      "Close": [rows["Close"].iloc[-1]], "Volume": [rows["Volume"].sum()]}, index=[index_value])
    r.index = pd.DatetimeIndex(r.index)
    return r


def outcomes(df, tf):
    """Ce qui s'est réellement produit sur la dernière bougie (clôturée) de df : (Fa, Fo, S haussière, S baissière) en booléens."""
    done = sc.bar_end(df.index[-1], tf) + pd.Timedelta(hours=1)
    res = sc.analyze_tf(df, tf, done, np.random.default_rng(0))
    if res is None:
        return None
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    hao, hah, hal, hac = sc.heikin_ashi(o, h, l, c)
    sh, sl, scl = (hah, hal, hac) if sc.INDICATORS_ON == "ha" else (h, l, c)
    mm, tk = sc.lines(sh, sl, scl)
    cls = sc.classify(hao, hac, mm, tk)[-1]
    s = res["s"]
    return cls == 1, cls == -1, bool(s["active"] and s["dir"] == "up"), bool(s["active"] and s["dir"] == "down")


def backtest_ticker(t, hourly, rng, max_days=None, tier=""):
    """Retourne une liste d'échantillons {ticker, tf, snap, kind, p, hit, alertable}."""
    hourly = hourly.dropna(subset=["Open", "High", "Low", "Close"])
    hourly = hourly[hourly.index.tz_localize(None).weekday < 5]
    if len(hourly) < 500:
        return []
    d_full = daily_from_hourly(hourly)
    h4_full = sc.aggregate(hourly, "4h")
    w_full = sc.aggregate(d_full, "1w")
    naive_h = hourly.index.tz_localize(None)
    days = list(d_full.index)
    if max_days:
        days = days[-max_days:]
    out = []

    def sample(tf, snap_frame, final_frame, now, label, kind_hits, fut_low=None):
        if snap_frame is None or len(snap_frame) < sc.MIN_BARS or final_frame is None:
            return
        res = sc.analyze_tf(snap_frame, tf, now, rng)
        if res is None or not res["forming"] or res.get("p_fa") is None:
            return
        sd = res.get("sd_rem")
        if sd and fut_low is not None and np.isfinite(fut_low):              # stop loss : le cours touche-t-il le niveau avant la fin de la bougie ?
            price = float(snap_frame["Close"].iloc[-1])
            for z in (0.05, 0.12, 0.25, 0.4, 0.8, 1.2, 1.6, 2.0):                               # niveaux théoriques à z écarts-types sous le cours
                lvl = price * float(np.exp(-z * sd))
                out.append({"ticker": t, "tf": tf, "snap": label, "kind": "SL (formule)", "p": sc.p_touch(price, lvl, sd),
                            "hit": bool(fut_low <= lvl), "alertable": True, "tier": tier})
            real, mode = sc.position_sl({"c": "S", "tf": tf, "ts": None}, {"tf": {tf: res}})
            if real is not None and real < price:                              # niveau réel d'un stop de S (plus bas de la bougie précédente)
                out.append({"ticker": t, "tf": tf, "snap": label, "kind": "SL (niveau réel)", "p": sc.p_touch(price, real, sd),
                            "hit": bool(fut_low <= real), "alertable": True, "tier": tier})
        for (kname, key), hit in zip(KINDS, kind_hits):
            p = res[key]
            if p is None or (key.startswith("p_s") and p <= 0):
                continue
            out.append({"ticker": t, "tf": tf, "snap": label, "kind": kname, "p": float(p), "hit": bool(hit),
                        "alertable": (res["state"] != "fa") if kname == "Fa" else True, "tier": tier})

    for day in days:
        dn = day.tz_localize(None)
        if dn.weekday() >= 5:
            continue
        # --- 1 jour ---
        prev_d = d_full[d_full.index < day]
        fin_d = d_full[d_full.index <= day]
        oc_d = outcomes(fin_d, "1d") if len(fin_d) >= sc.MIN_BARS else None
        in_day = (naive_h.normalize() == dn)
        for hr in SNAP_HOURS:
            now = day.replace(hour=hr, minute=0)
            ends_ok = in_day & (naive_h + pd.Timedelta(hours=1) <= dn + pd.Timedelta(hours=hr))
            part = merge_rows(hourly[ends_ok], day)
            if part is not None and oc_d is not None:
                lows = hourly["Low"][in_day & (naive_h >= dn + pd.Timedelta(hours=hr))]
                sample("1d", pd.concat([prev_d, part]), fin_d, now, f"{hr}h", oc_d, float(lows.min()) if len(lows) else None)
            # --- 4 heures : bougie du matin (9h-13h) vue à 11h ; bougie de l'après-midi (13h-17h35) vue à 14h et 15h ---
            start = 9 if hr < 13 else 13
            k4 = dn + pd.Timedelta(hours=start)
            idx4 = h4_full.index.tz_localize(None)
            fin_4 = h4_full[idx4 <= k4]
            prev_4 = h4_full[idx4 < k4]
            in4 = in_day & (naive_h >= k4) & (naive_h + pd.Timedelta(hours=1) <= dn + pd.Timedelta(hours=hr))
            part4 = merge_rows(hourly[in4], k4.tz_localize(sc.PARIS))
            if part4 is not None and len(fin_4) >= sc.MIN_BARS and idx4[idx4 <= k4].max() == k4:
                oc4 = outcomes(fin_4, "4h")
                if oc4 is not None:
                    end4 = dn + pd.Timedelta(hours=13 if start == 9 else 24)
                    lows4 = hourly["Low"][in_day & (naive_h >= dn + pd.Timedelta(hours=hr)) & (naive_h < end4)]
                    sample("4h", pd.concat([prev_4, part4]), fin_4, now, f"{hr}h", oc4, float(lows4.min()) if len(lows4) else None)
        # --- 1 semaine : mardi et jeudi à 14h ---
        if dn.weekday() in WEEK_SNAP:
            monday = (dn - pd.Timedelta(days=dn.weekday())).tz_localize(sc.PARIS)
            wk_days = d_full[(d_full.index >= monday) & (d_full.index < day)]
            today_part = merge_rows(hourly[in_day & (naive_h + pd.Timedelta(hours=1) <= dn + pd.Timedelta(hours=14))], day)
            if today_part is not None:
                rows = pd.concat([wk_days, today_part])
                wpart = merge_rows(rows, monday)
                prev_w = w_full[w_full.index < monday]
                fin_w = w_full[w_full.index <= monday]
                if wpart is not None and len(fin_w) >= sc.MIN_BARS and fin_w.index[-1] == monday:
                    ocw = outcomes(fin_w, "1w")
                    if ocw is not None:
                        wk_end = monday.tz_localize(None) + pd.Timedelta(days=5)
                        loww = hourly["Low"][(naive_h >= dn + pd.Timedelta(hours=14)) & (naive_h < wk_end)]
                        sample("1w", pd.concat([prev_w, wpart]), fin_w, day.replace(hour=14, minute=0), "14h", ocw, float(loww.min()) if len(loww) else None)
    return out


def summarize(df, label):
    """Tableau de calibration par tranche de probabilité."""
    lines = [f"| Tranche annoncée | Cas | Annoncé en moyenne | Réalisé | Écart |", "|---|---:|---:|---:|---:|"]
    for lo, hi in BINS:
        s = df[(df.p >= lo) & (df.p < hi)]
        if len(s) < 20:
            if len(s):
                lines.append(f"| {int(lo * 100)}-{min(int(hi * 100), 100)} % | {len(s)} | | | (trop peu de cas) |")
            continue
        pm, obs = s.p.mean(), s.hit.mean()
        lines.append(f"| {int(lo * 100)}-{min(int(hi * 100), 100)} % | {len(s)} | {pm * 100:.0f} % | {obs * 100:.0f} % | {(obs - pm) * 100:+.0f} pts |")
    return lines


def report(records, n_tickers):
    df = pd.DataFrame(records)
    if df.empty:
        return "# Fiabilité des probabilités\n\nAucun échantillon : pas assez de données horaires.\n"
    L = ["# Fiabilité des probabilités du scanner", "",
         f"{len(df)} cas, {n_tickers} actions, historique horaire Yahoo (environ 2 ans). Instants testés : 11h, 14h, 15h (heure de Paris).", "",
         "**Comment lire** : « Annoncé » = probabilité calculée par le scanner à l'instant du test ; « Réalisé » = part des cas où la configuration "
         "était réellement présente à la clôture de la bougie. Écart positif = le scanner était trop prudent, négatif = trop optimiste.", "",
         "**Limites** : des cas voisins (même jour, mêmes marchés) ne sont pas indépendants, donc la précision réelle est moins bonne que ne le suggère "
         "le nombre de cas ; en dessous d'environ 100 cas par tranche, les écarts de quelques points sont du bruit.", ""]
    L += ["## Les seuils d'alerte actuels (60 % et plus)", "",
          "| Unité | Configuration | Cas ≥ 60 % | Annoncé en moyenne | Réalisé | Écart |", "|---|---|---:|---:|---:|---:|"]
    for tf in ("4h", "1d", "1w"):
        for kind, _ in KINDS:
            s = df[(df.tf == tf) & (df.kind == kind) & (df.p >= 0.6) & df.alertable]
            if len(s) >= 20:
                L.append(f"| {tf} | {kind} | {len(s)} | {s.p.mean() * 100:.0f} % | {s.hit.mean() * 100:.0f} % | {(s.hit.mean() - s.p.mean()) * 100:+.0f} pts |")
            elif len(s):
                L.append(f"| {tf} | {kind} | {len(s)} | | | trop peu de cas |")
    L.append("")
    if "tier" in df and df.tier.nunique() > 1:
        L += ["## Selon la liquidité de l'action (volume moyen en € par jour)", "",
              "Cas annoncés entre 30 et 60 % (là où l'écart est le plus grand) et alertes à 60 % et plus.", "",
              "| Unité | Configuration | Liquidité | Cas 30-60 % | Annoncé | Réalisé | Cas ≥ 60 % | Annoncé | Réalisé |", "|---|---|---|---:|---:|---:|---:|---:|---:|"]
        for tf in ("4h", "1d"):
            for kind, _ in KINDS[:3:2]:
                for tier in ("100 k€ - 1 M€", "1 - 10 M€", "plus de 10 M€"):
                    b = df[(df.tf == tf) & (df.kind == kind) & (df.tier == tier) & df.alertable]
                    m, h = b[(b.p >= .3) & (b.p < .6)], b[b.p >= .6]
                    if len(m) >= 20 and len(h) >= 20:
                        L.append(f"| {tf} | {kind} | {tier} | {len(m)} | {m.p.mean() * 100:.0f} % | {m.hit.mean() * 100:.0f} % | {len(h)} | {h.p.mean() * 100:.0f} % | {h.hit.mean() * 100:.0f} % |")
        L.append("")
    for tf in ("4h", "1d", "1w"):
        for kind in ("SL (formule)", "SL (niveau réel)"):
            s = df[(df.tf == tf) & (df.kind == kind)]
            if len(s) < 50:
                continue
            brier = float(((s.p - s.hit.astype(float)) ** 2).mean())
            L += [f"## Stop loss : {kind} en {tf}", "",
                  f"{len(s)} cas · fréquence réelle globale {s.hit.mean() * 100:.0f} % · score de Brier {brier:.3f}. "
                  "« Réalisé » = le cours a touché le niveau entre l'instant du test et la clôture de la bougie.", ""]
            L += summarize(s, kind)
            hi = s[s.p >= 0.9]
            if len(hi) >= 20:
                L += ["", f"**Alertes SL à 90 % et plus : {len(hi)} cas, annoncé {hi.p.mean() * 100:.0f} %, réalisé {hi.hit.mean() * 100:.0f} %.**"]
            L.append("")
    for tf in ("4h", "1d", "1w"):
        for kind, _ in KINDS:
            s = df[(df.tf == tf) & (df.kind == kind)]
            if kind == "Fa":
                s = s[s.alertable]                       # comme les alertes : actions pas déjà en Fa
            if len(s) < 50:
                continue
            brier = float(((s.p - s.hit.astype(float)) ** 2).mean())
            base = float(s.hit.mean())
            ref = base * (1 - base)
            L += [f"## {kind} en {tf}", "",
                  f"{len(s)} cas · fréquence réelle globale {base * 100:.0f} % · score de Brier {brier:.3f} (référence « toujours la fréquence moyenne » : {ref:.3f} ; plus bas = mieux)", ""]
            L += summarize(s, kind)
            L.append("")
    return "\n".join(L)


def tier_of(v):
    return "100 k€ - 1 M€" if v < 1e6 else "1 - 10 M€" if v < 1e7 else "plus de 10 M€"


def _work(args):
    t, df, max_days, seed, tier = args
    try:
        return t, backtest_ticker(t, df, np.random.default_rng(seed), max_days, tier)
    except Exception as e:
        print(f"  {t} : erreur {e}", file=sys.stderr)
        return t, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=int, default=80, help="nombre d'actions testées (les plus liquides) ; 0 = toutes")
    ap.add_argument("--out", default="backtest_report.md")
    ap.add_argument("--max-days", type=int, default=None, help="limiter le nombre de jours rejoués (test rapide)")
    ap.add_argument("--sigma-scale", type=float, default=0.0, help="facteur unique de volatilité pour toutes les unités ; 0 = réglages du scanner")
    ap.add_argument("--min-turnover", type=float, default=sc.MIN_TURNOVER_EUR, help="liquidité minimale (€/jour), comme le scanner")
    ap.add_argument("--auto", action="store_true", help="ajouter toutes les actions de Paris listées par Yahoo (plus d'échantillons)")
    args = ap.parse_args()
    if args.sigma_scale > 0:
        sc.SIGMA_SCALE = args.sigma_scale
    label = str(args.sigma_scale) if args.sigma_scale > 0 else "réglages du scanner " + str(sc.SIGMA_SCALE)

    universe = sc.load_universe()
    if args.auto:
        universe.update({t: n for t, n in sc.fetch_paris_listing().items() if t not in universe})
    print(f"Téléchargement de l'historique horaire ({len(universe)} actions)…", flush=True)
    hourly = sc.download(list(universe), "2y", "1h")
    turn = {t: sc.turnover(daily_from_hourly(hourly[t])) for t in hourly}
    ranked = sorted((t for t in hourly if turn[t] >= args.min_turnover), key=lambda t: turn[t], reverse=True)
    print(f"{len(ranked)} actions au-dessus de {args.min_turnover:,.0f} €/jour (comme le scanner).".replace(",", " "), flush=True)
    if args.tickers:
        ranked = ranked[:args.tickers]
    print(f"{len(hourly)} actions avec données ; test sur {len(ranked)} actions, facteur de volatilité : {label}.", flush=True)
    records, t0 = [], time.time()
    jobs = [(t, hourly[t], args.max_days, 2026 + i, tier_of(turn[t])) for i, t in enumerate(ranked)]
    with mp.Pool(max(1, os.cpu_count() or 1)) as pool:
        for i, (t, rec) in enumerate(pool.imap_unordered(_work, jobs), 1):
            records += rec
            if i % 10 == 0:
                print(f"  {i}/{len(ranked)} actions, {len(records)} cas, {int(time.time() - t0)} s", flush=True)
    text = report(records, len(ranked))
    text = text.replace("# Fiabilité des probabilités du scanner", f"# Fiabilité des probabilités du scanner (facteur de volatilité : {label})", 1)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
