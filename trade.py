"""
Gestion d'un trade S+H4 hebdomadaire (SL 1,5 ATR, objectif Donchian-2) : peut-on mieux faire que « SL et objectif fixes » ?

Partie 1 — Survie : à 2, 4, 8 et 13 semaines après l'entrée, pour les trades encore ouverts, selon le rendement du moment (et l'état Fo hebdo de l'action) :
          part qui finit à l'objectif / au stop, résultat final moyen si on garde, et résultat si on vendait tout de suite. Répond à « ce trade peut-il encore se relever ? ».
Partie 2 — Variantes de gestion, fixées avant les résultats, comparées à la référence sur les MÊMES entrées (écart apparié ; apprentissage jusqu'en 2015 IC 99 %, test 2016+ IC 95 %) :
          sorties dans le temps (8 / 13 / 26 semaines, toujours ou seulement si le trade est en perte) ; stop remonté à l'entrée (après +10 %, +20 %, ou à mi-chemin de l'objectif) ;
          stop suiveur (plus bas des 4 dernières semaines, activé après +10 % ou +20 %) ; sortie en deux fois (½ Donchian-1, ½ Donchian-2) ; stop scindé (½ stop de base, ½ stop 3 ATR).
Frais : --fee par ordre (1,99 € sur 500 € = 0,398 %). Chaque ordre de vente supplémentaire coûte un ordre de plus (sorties scindées : frais plus élevés, déjà comptés).
"""
import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

import eu
import profit
import robust
import scanner as sc
import sr

WEEKS = (2, 4, 8, 13)
BUCKETS = (("< -10 %", -9, -0.10), ("-10 % à -5 %", -0.10, -0.05), ("-5 % à 0 %", -0.05, 0.0), ("0 à +10 %", 0.0, 0.10), ("> +10 %", 0.10, 9))
VARIANTS = ["Référence", "Sortie à 8 sem.", "Sortie à 13 sem.", "Sortie à 26 sem.", "8 sem. si en perte", "13 sem. si en perte", "26 sem. si en perte",
            "Stop à l'entrée après +10 %", "Stop à l'entrée après +20 %", "Stop à l'entrée à mi-chemin", "Stop suiveur dès +10 %", "Stop suiveur dès +20 %",
            "½ Donchian-1 + ½ Donchian-2", "Stop scindé ½ base + ½ 3 ATR"]
SPLIT_YEAR = 2016
MAX_BARS = 600


def manage(A, k, entry, legs, be=None, trail=None, tstop=None):
    """legs : [{frac, sl, tp}]. Renvoie (rendement brut, nb d'ordres de vente, barre de sortie, type du dernier événement) ou None si non résolu."""
    o, h, l, c, n = A["o"], A["h"], A["l"], A["c"], A["n"]
    live = [dict(g) for g in legs]
    gross, exits_at, kind, hh = 0.0, set(), "", entry
    for j in range(k + 1, min(n, k + 1 + MAX_BARS)):
        for g in list(live):
            if l[j] <= g["sl"]:
                gross += g["frac"] * (min(o[j], g["sl"]) / entry - 1)
                live.remove(g); exits_at.add(j); kind = "SL"
            elif g.get("tp") and h[j] >= g["tp"]:
                gross += g["frac"] * (max(o[j], g["tp"]) / entry - 1)
                live.remove(g); exits_at.add(j); kind = "TP"
        if not live:
            return gross, len(exits_at), j, kind
        hh = max(hh, h[j])
        for g in live:
            if be is not None and hh >= entry * (1 + be):
                g["sl"] = max(g["sl"], entry)
            if trail is not None and hh >= entry * (1 + trail):
                g["sl"] = max(g["sl"], l[max(k + 1, j - 3):j + 1].min())
        if tstop is not None and j == k + tstop[0] and (not tstop[1] or c[j] < entry):
            for g in live:
                gross += g["frac"] * (c[j] / entry - 1)
            exits_at.add(j)
            return gross, len(exits_at), j, "TS"
    return None


def bucket(r):
    return next(b[0] for b in BUCKETS if b[1] <= r < b[2])


def collect(args, rng, universe=None, now=None):
    fee = args.fee / 100.0
    universe = universe or {**eu.load_eu(), **sc.load_universe()}
    daily = sc.download(list(universe), "max", "1d")
    for t in list(daily):
        daily[t], _ = profit.clean_series(daily[t])
    turn = {t: sc.turnover(daily[t]) for t in daily}
    ranked = sorted((t for t in daily if turn[t] >= sc.MIN_TURNOVER_EUR), key=lambda t: turn[t], reverse=True)
    now = now or pd.Timestamp.now(tz=sc.PARIS)
    rows, t0 = [], time.time()
    for i, t in enumerate(ranked, 1):
        arrs = {}
        for tf in ("1w", "1mo", "3mo"):
            try:
                a = profit.prep(sc.aggregate(daily[t], tf), tf, now)
            except Exception:
                a = None
            if a is not None:
                arrs[tf] = a
        A = arrs.get("1w")
        if A is None:
            continue
        S = sr.sr_prep(A, "1w")
        c, h, st = A["c"], A["h"], A["st"]
        for k in np.flatnonzero(A["up"]):
            if k < 105 or k + 1 >= A["n"] or c[k] / h[k - 52:k + 1].max() - 1 > -args.h4:
                continue
            entry = c[k]
            L = sr.levels_at(A, S, "1w", k, entry)["Donchian"]
            base, atr = A["hal"][k - 1], A["atr"][k]
            if len(L) < 2 or not np.isfinite(atr):
                continue
            sl = min(base, entry - 1.5 * atr)
            sl3 = min(base, entry - 3 * atr)
            if sl >= entry * 0.9995 or base >= entry * 0.9995:
                continue
            tp1, tp2 = L[0], L[1]
            one = [{"frac": 1.0, "sl": sl, "tp": tp2}]
            runs = {"Référence": dict(legs=one),
                    "Sortie à 8 sem.": dict(legs=one, tstop=(8, False)), "Sortie à 13 sem.": dict(legs=one, tstop=(13, False)), "Sortie à 26 sem.": dict(legs=one, tstop=(26, False)),
                    "8 sem. si en perte": dict(legs=one, tstop=(8, True)), "13 sem. si en perte": dict(legs=one, tstop=(13, True)), "26 sem. si en perte": dict(legs=one, tstop=(26, True)),
                    "Stop à l'entrée après +10 %": dict(legs=one, be=0.10), "Stop à l'entrée après +20 %": dict(legs=one, be=0.20),
                    "Stop à l'entrée à mi-chemin": dict(legs=one, be=(tp2 / entry - 1) / 2),
                    "Stop suiveur dès +10 %": dict(legs=one, trail=0.10), "Stop suiveur dès +20 %": dict(legs=one, trail=0.20),
                    "½ Donchian-1 + ½ Donchian-2": dict(legs=[{"frac": .5, "sl": sl, "tp": tp1}, {"frac": .5, "sl": sl, "tp": tp2}]),
                    "Stop scindé ½ base + ½ 3 ATR": dict(legs=[{"frac": .5, "sl": base, "tp": tp2}, {"frac": .5, "sl": sl3, "tp": tp2}])}
            row = {"ticker": t, "time": A["ends"][k]}
            for name, kw in runs.items():
                r = manage(A, k, entry, **kw)
                if r is not None:
                    row[name] = r[0] - (1 + r[1]) * fee
                    if name == "Référence":
                        row["bars"], row["kind"] = r[2] - k, r[3]
                        for w in WEEKS:
                            if r[2] > k + w:
                                x = c[k + w] / entry - 1
                                row[f"r{w}"], row[f"fo{w}"] = x, bool(st[k + w] == -1)
            if "Référence" in row:
                rows.append(row)
        if i % 25 == 0:
            print(f"  {i}/{len(ranked)} actions, {len(rows)} trades, {int(time.time() - t0)} s", flush=True)
    return pd.DataFrame(rows), len(ranked)


def report(df, n, fee, rng):
    pct = robust.pct
    L = [f"# Gestion d'un trade S+H4 hebdo (SL 1,5 ATR, objectif Donchian-2) — {len(df)} trades, {n} actions, frais {fee * 100:.2f} % par ordre", "",
         "## 1. Un trade encore ouvert peut-il se relever ?", "",
         "Pour les trades encore ouverts à N semaines (ni stop ni objectif atteints), selon le rendement du moment. **Garder** = résultat final moyen net si on laisse courir (stop et objectif d'origine) ; "
         "**Vendre maintenant** = rendement net du moment après frais de vente (une vente en plus). Garder n'est préférable que si « Garder » > « Vendre maintenant ».", "",
         "| semaines | rendement du moment | trades ouverts | finissent à l'objectif | finissent au stop | Garder (net) | Vendre maintenant (net) | écart garder − vendre |", "|---|---|---|---|---|---|---|---|"]
    for w in WEEKS:
        if f"r{w}" not in df:
            continue
        d = df[df[f"r{w}"].notna()]
        for b in BUCKETS:
            x = d[(d[f"r{w}"] >= b[1]) & (d[f"r{w}"] < b[2])]
            if len(x) < 30:
                continue
            sell = (x[f"r{w}"] - fee).mean()
            keep = x["Référence"].mean()
            L.append(f"| {w} | {b[0]} | {len(x)} | {pct((x['kind'] == 'TP').mean(), 0)} | {pct((x['kind'] == 'SL').mean(), 0)} | **{pct(keep)}** | {pct(sell)} | **{pct(keep - sell)}** |")
    L += ["", "Même lecture selon l'état hebdomadaire de l'action au moment du point (Fo = bas du corps HA sous la Tenkan et MM7 descendante) :", "",
          "| semaines | état | trades ouverts | finissent à l'objectif | finissent au stop | Garder (net) | Vendre maintenant (net) | écart |", "|---|---|---|---|---|---|---|---|"]
    for w in WEEKS:
        if f"fo{w}" not in df:
            continue
        d = df[df[f"r{w}"].notna()]
        for lab, v in (("en Fo", True), ("pas en Fo", False)):
            x = d[d[f"fo{w}"] == v]
            if len(x) < 30:
                continue
            sell = (x[f"r{w}"] - fee).mean()
            keep = x["Référence"].mean()
            L.append(f"| {w} | {lab} | {len(x)} | {pct((x['kind'] == 'TP').mean(), 0)} | {pct((x['kind'] == 'SL').mean(), 0)} | **{pct(keep)}** | {pct(sell)} | **{pct(keep - sell)}** |")
    L += ["", "## 2. Variantes de gestion (comparées à la référence sur les mêmes entrées)", "",
          "Retenue = écart > 0 avec IC > 0 en apprentissage (jusqu'en 2015, IC 99 %) ET en test (2016+, IC 95 %).", "",
          "| variante | trades | moyenne nette | médiane | durée moyenne (sem.) | écart avec la référence : apprentissage [IC 99 %] | test [IC 95 %] | verdict |", "|---|---|---|---|---|---|---|---|"]
    for v in VARIANTS:
        if v not in df:
            continue
        if v == "Référence":
            L.append(f"| **Référence** | {int(df[v].notna().sum())} | **{pct(df[v].mean())}** | {pct(df[v].median())} | {df['bars'].mean():.1f} | – | – | référence |")
            continue
        r = sr.compare(df, v, "Référence", rng)
        f = lambda z: f"{pct(z[0])} [{pct(z[1])} ; {pct(z[2])}]" if z else "–"
        L.append(f"| {v} | {r['n']} | {pct(r['mean'])} | {pct(df[v].median())} | – | {f(r.get('tr'))} | {f(r.get('te'))} | {'**retenue**' if r['ok'] else 'non'} |")
    L += ["", "Lecture : une gestion « active » n'est utile que si elle bat la règle simple dans les deux périodes. Les sorties scindées paient un ordre de vente de plus (1,99 €). "
          "Même quand une variante ne gagne pas plus en moyenne, elle peut réduire la durée des trades ou la taille des pertes : à pondérer avec la médiane et la durée, mais pas avec un chiffre de profit."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fee", type=float, default=0.398)
    ap.add_argument("--h4", type=float, default=0.30)
    ap.add_argument("--out", default="trade_report.md")
    args = ap.parse_args()
    rng = np.random.default_rng(2026)
    df, n = collect(args, rng)
    text = report(df, n, args.fee / 100.0, rng)
    Path(args.out).write_text(text, encoding="utf-8")
    df.to_csv("trade_trades.csv", index=False)
    print(text)


if __name__ == "__main__":
    main()
