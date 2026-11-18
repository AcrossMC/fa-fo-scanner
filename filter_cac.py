"""
Un filtre de marché (CAC 40) améliore-t-il S+H4 hebdo (sortie Donchian-2, SL 1,5 ATR) ?

Règles fixées AVANT les résultats :
  Contexte lu sur le CAC 40 à la clôture de la bougie hebdo d'entrée (même contexte pour toutes les places : le CAC sert de baromètre de la zone euro).
  F1 : CAC 40 au-dessus de sa MM 200 jours.
  F2 : CAC 40 sans baisse de plus de 3 % sur 20 séances.
  Pour chaque filtre, on teste les deux sens (« garder si vrai » et « garder si faux ») : 4 tests au total.
  Un sens est retenu seulement si :
    - l'écart de moyenne nette (S+H4 gardés − S+H4 écartés) est > 0 avec IC 99 % > 0 avant 2016 ET IC 95 % > 0 à partir de 2016 ;
    - l'avantage sur le hasard (même contexte) des S+H4 gardés est > 0 avec IC 95 % > 0 ;
    - l'écart est > 0 à Paris comme hors Paris.
  Avec 4 tests, un « retenu » isolé reste possible par hasard : il doit se confirmer dans le suivi réel.
Aussi affichés : S+H4 par état du CAC (MM200, tendance 20 jours), part des signaux gardés par chaque filtre.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import entries
import eu
import market
import regime
import robust
import scanner as sc

SPLIT_YEAR = 2016
TREND_LIMIT = -0.03

FILTERS = {
    "F1 : CAC au-dessus de sa MM200": "ma_up",
    "F2 : CAC sans baisse > 3 % sur 20 séances": "not_falling",
}


def add_context(df, M):
    """Ajoute les colonnes de contexte CAC (NaN/False + 'ok' = False quand le contexte est inconnu)."""
    ns = pd.DatetimeIndex(df["time"]).as_unit("ns").asi8
    j = np.searchsorted(M["ns"], ns, side="right") - 1
    jj = np.clip(j, 0, len(M["c"]) - 1)
    ma, r20, c = M["ma200"][jj], M["r20"][jj], M["c"][jj]
    ok = (j >= 20) & np.isfinite(ma) & np.isfinite(r20)
    out = df.copy()
    out["ctx_ok"] = ok
    out["ma_up"] = ok & (c >= ma)
    out["not_falling"] = ok & (r20 > TREND_LIMIT)
    out["trend"] = np.select([~ok, r20 < -0.03, r20 > 0.03], ["inconnu", "baisse (< -3 %)", "hausse (> +3 %)"], "stable")
    return out[out["ctx_ok"]].copy()


def per(df, lab):
    yr = pd.DatetimeIndex(df["time"]).year
    return df[yr < SPLIT_YEAR] if lab == "avant" else df[yr >= SPLIT_YEAR] if lab == "après" else df


def report(df, n, fee, rng):
    pct = robust.pct
    S4 = df[df["trigger"] == "S+H4"]
    H = df[df["kind"] == "H"]
    L = [f"# Filtre CAC 40 sur S+H4 hebdo — {n} actions liquides (Paris + Europe), frais {fee * 100:.2f} % par ordre", "",
         "**Règles fixées avant les résultats** : voir l'en-tête de `filter_cac.py`. Sortie Donchian-2, SL ≥ 1,5 ATR, hasard = entrées au sort (même action, même année) lues dans le même état du CAC. "
         "Apprentissage jusqu'en 2015 (IC 99 %), test à partir de 2016 (IC 95 %).", "",
         f"S+H4 avec contexte CAC connu : {len(S4)} trades (hasard : {len(H)}).", "",
         "## S+H4 selon l'état du CAC 40", "",
         "| état du CAC | période | trades | moyenne nette | IC | hasard (même état) | avantage | IC de l'avantage |", "|---|---|---|---|---|---|---|---|"]
    states = [("CAC > MM200", "ma_up", True), ("CAC < MM200", "ma_up", False),
              ("CAC en baisse (< -3 % / 20 j)", "trend", "baisse (< -3 %)"), ("CAC stable", "trend", "stable"), ("CAC en hausse (> +3 % / 20 j)", "trend", "hausse (> +3 %)")]
    for name, col, val in states:
        for lab, lvl in (("tout", 95), ("avant", 99), ("après", 95)):
            x = eu.stat(pd.concat([per(S4[S4[col] == val], lab), per(H[H[col] == val], lab)]), rng, lvl)
            if x:
                L.append(f"| **{name}** | {lab} | {x['n']} | **{pct(x['mean'])}** | [{pct(x['mlo'])} ; {pct(x['mhi'])}] | {pct(x['hasard'])} | **{pct(x['adv'])}** | [{pct(x['alo'])} ; {pct(x['ahi'])}] |")
    L += ["", "## Test des filtres (4 tests : 2 filtres x 2 sens)", "",
          "| filtre | sens | période | gardés | écartés | moyenne gardés | moyenne écartés | écart | IC | verdict (période) |", "|---|---|---|---|---|---|---|---|---|---|"]
    verdicts, shares = {}, {}
    paris = S4["market"].str.startswith("Paris").to_numpy()
    for fname, col in FILTERS.items():
        for sens, keep_true in (("garder si vrai", True), ("garder si faux", False)):
            flag_all = S4[col].to_numpy(bool) if keep_true else ~S4[col].to_numpy(bool)
            ok = {}
            for lab, lvl in (("avant", 99), ("après", 95)):
                d = per(S4, lab)
                flag = d[col].to_numpy(bool) if keep_true else ~d[col].to_numpy(bool)
                if flag.sum() < 30 or (~flag).sum() < 30:
                    ok[lab] = False
                    continue
                diff, lo, hi = regime.diff_ci(d["time"], d["ret"].to_numpy(), flag, rng, lvl)
                ok[lab] = bool(lo > 0)
                L.append(f"| {fname} | {sens} | {lab} | {int(flag.sum())} | {int((~flag).sum())} | {pct(d[flag]['ret'].mean())} | {pct(d[~flag]['ret'].mean())} | **{pct(diff)}** | [{pct(lo)} ; {pct(hi)}] | {'IC > 0' if lo > 0 else 'non'} |")
            # avantage sur le hasard des gardés
            keepS = S4[flag_all]
            hv = H[col].to_numpy(bool) if keep_true else ~H[col].to_numpy(bool)
            x = eu.stat(pd.concat([keepS, H[hv]]), rng, 95)
            adv_ok = bool(x and x["alo"] > 0)
            # Paris / hors Paris
            pp = []
            for sel in (paris, ~paris):
                dd = S4[sel]
                fl = flag_all[sel]
                if fl.sum() < 30 or (~fl).sum() < 30:
                    pp.append(False)
                    continue
                diff = regime.diff_ci(dd["time"], dd["ret"].to_numpy(), fl, rng, 95)[0]
                pp.append(bool(diff > 0))
            ret = bool(ok.get("avant") and ok.get("après") and adv_ok and all(pp))
            verdicts[(fname, sens)] = (ret, ok, adv_ok, pp)
            shares[(fname, sens)] = float(flag_all.mean())
    L += ["", "## Verdict par test", "",
          "| filtre | sens | part des S+H4 gardés | écart IC > 0 avant | écart IC > 0 après | avantage sur le hasard (gardés) IC > 0 | écart > 0 Paris | écart > 0 hors Paris | verdict |", "|---|---|---|---|---|---|---|---|---|"]
    for (fname, sens), (ret, ok, adv_ok, pp) in verdicts.items():
        L.append(f"| {fname} | {sens} | {shares[(fname, sens)] * 100:.0f} % | {'oui' if ok.get('avant') else 'non'} | {'oui' if ok.get('après') else 'non'} | "
                 f"{'oui' if adv_ok else 'non'} | {'oui' if pp[0] else 'non'} | {'oui' if pp[1] else 'non'} | {'**retenu**' if ret else 'non'} |")
    kept = [f"{f} ({s})" for (f, s), v in verdicts.items() if v[0]]
    L += ["", "## Verdict", "", "- Filtre retenu : " + (", ".join(kept) if kept else "aucun — S+H4 reste inchangé, sans filtre de marché."), "",
          "Lecture : un filtre qui ne garde qu'une partie des signaux réduit le nombre de trades (moins de capital investi, plus de liquidités qui ne rapportent rien dans nos simulations) ; "
          "même retenu, il doit encore être confirmé par le suivi réel et re-simulé au niveau du portefeuille avant d'être intégré à l'application."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fee", type=float, default=0.398)
    ap.add_argument("--out", default="filter_cac_report.md")
    ap.add_argument("--csv", default="", help="entries_trades.csv d'une exécution précédente (évite de tout recalculer)")
    args = ap.parse_args()
    rng = np.random.default_rng(2026)
    if args.csv:
        df = pd.read_csv(args.csv)
        df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
        n = int(df["ticker"].nunique())
    else:
        df, n = entries.collect(args, rng)
    idx = sc.download(["^FCHI"], "max", "1d").get("^FCHI")
    if idx is None or len(idx) < 1000:
        raise SystemExit("Indice CAC 40 (^FCHI) indisponible sur Yahoo : test impossible.")
    M = market.cac_prep(idx)
    df = add_context(df, M)
    text = report(df, n, args.fee / 100.0, rng)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
