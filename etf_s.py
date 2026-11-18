"""
S hebdomadaire sur des INDICES (proxys d'ETF capitalisants éligibles au PEA) : une entrée en S avec une sortie fait-elle mieux
qu'une position gardée sans sortie (achat-conservation), qu'une entrée au hasard ou qu'un investissement programmé (DCA) ?

Règles fixées AVANT les résultats
  Données : CAC 40 (^FCHI), Euro Stoxx 50 (^STOXX50E), STOXX Europe 600 (^STOXX), S&P 500 (^GSPC) et Nasdaq-100 (^NDX) convertis en euros (EURUSD=X),
            car les ETF PEA correspondants ont un historique trop court. Fenêtre : du 1er janvier 2000 à aujourd'hui, bougies hebdomadaires.
  Dividendes : les ETF éligibles au PEA sont capitalisants ; les indices de cours les ignorent. On ajoute un rendement annuel fixe pendant que la position est ouverte
            (CAC 3,0 %, Euro Stoxx 50 3,3 %, STOXX 600 3,2 %, S&P 500 1,6 %, Nasdaq-100 0,8 %). --div 0 les supprime.
  Frais : --fee % par ordre (achat et vente), le même pour toutes les stratégies. Liquidités : rendement --cash % par an (0 par défaut, comme un PEA).
  Entrées (2) : « S » (S hebdo haussière) ; « S+creux » (S quand l'indice est à plus de 10 % sous son plus haut de 52 semaines). Un seul trade à la fois, tout le capital.
  Sorties (5), toutes avec le SL 1,5 ATR de l'étude : « SL seul » (aucune cible) ; « Donchian-2 » (2e niveau de plus hauts 13/26/52/104 sem., à défaut le seul niveau disponible) ;
            « Fo hebdo » (clôture de la 1re bougie Fo APRÈS une bougie Fa survenue depuis l'entrée) ; « Suiveur 3 ATR » (stop remontant à 3 ATR sous le plus haut de clôture) ; « 52 semaines » (sortie après un an).
  Références : A) achat-conservation dès le début de la fenêtre ; B) achat-conservation à une date de départ tirée au hasard (parmi les 5 premières années) ;
            C) DCA : 1 achat par mois sur toute la fenêtre (le capital non encore investi reste en liquidités) ;
            D) HASARD : mêmes sorties et mêmes conditions, mais entrées tirées au sort dans les semaines éligibles, avec le même nombre de trades attendu (300 tirages).
  Critères : une variante « bat l'achat-conservation » si son rendement annualisé est supérieur à celui de l'achat-conservation sur au moins 3 indices sur 5 ET si elle se situe
            au-dessus du 90e centile du hasard sur au moins 3 indices sur 5. Elle est « défensive » si le rapport rendement / creux maximal est supérieur sur au moins 3 indices sur 5
            ET si son rendement annualisé reste d'au moins 70 % de celui de l'achat-conservation sur au moins 3 indices sur 5.
Limites : très peu de trades par indice (la plupart des variantes en ont moins de 15) ; un seul chemin historique par indice ; creux mesuré sur clôtures hebdomadaires ;
          dividendes forfaitaires ; une seule fenêtre de 26 ans, dont plusieurs krachs ; les cinq indices sont corrélés (ce ne sont pas cinq preuves indépendantes).
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import profit
import robust
import scanner as sc
import sr

INDICES = {
    "^FCHI": ("CAC 40", 0.030, False),
    "^STOXX50E": ("Euro Stoxx 50", 0.033, False),
    "^STOXX": ("STOXX Europe 600", 0.032, False),
    "^GSPC": ("S&P 500 (en €)", 0.016, True),
    "^NDX": ("Nasdaq-100 (en €)", 0.008, True),
}
ENTRIES = ["S", "S+creux"]
EXITS = ["SL seul", "Donchian-2", "Fo hebdo", "Suiveur 3 ATR", "52 semaines"]
START_YEAR = 2000
DD_MIN = 0.10
N_RAND = 300
WEEKS_Y = 52.1775


# ------------------------------------------------------------------ données

def to_eur(df, fx):
    fxc = fx["Close"].copy()
    fxc.index = fxc.index.tz_localize(None).normalize()
    fxc = fxc[~fxc.index.duplicated(keep="last")]
    day = df.index.tz_localize(None).normalize()
    rate = fxc.reindex(fxc.index.union(day)).ffill().reindex(day).to_numpy()
    out = df.copy()
    for c in ("Open", "High", "Low", "Close"):
        out[c] = out[c].to_numpy() / rate
    return out.dropna(subset=["Open", "High", "Low", "Close"])


def prepare(df_daily, now):
    wk = sc.aggregate(df_daily, "1w")
    A = profit.prep(wk, "1w", now)
    if A is None:
        return None
    S = sr.sr_prep(A, "1w")
    years = np.array([e.year for e in A["ends"]])
    k0 = max(105, int(np.argmax(years >= START_YEAR)))
    A["k0"] = k0
    A["S"] = S
    A["dd"] = np.full(A["n"], np.nan)
    h52 = pd.Series(A["h"]).rolling(53, min_periods=53).max().to_numpy()
    A["dd"] = A["c"] / h52 - 1
    return A


# ------------------------------------------------------------------ sorties

def trade_exit(A, k, exit_name):
    """Renvoie (j, px) : bougie et prix de sortie, ou (None, None) si la position reste ouverte, ou False si le SL n'est pas valide."""
    entry = A["c"][k]
    if k + 1 >= A["n"]:
        return False
    sl = profit.sl_levels(A, k, {}, entry)["atr1.5"]
    if not np.isfinite(sl) or sl >= entry * 0.9995:
        return False
    o, h, l, c = A["o"], A["h"], A["l"], A["c"]
    tp = None
    if exit_name == "Donchian-2":
        don = sr.levels_at(A, A["S"], "1w", k, entry)["Donchian"]
        tp = don[1] if len(don) >= 2 else (don[0] if don else None)
    hc, seen_fa = entry, False
    for j in range(k + 1, A["n"]):
        stop = sl
        if exit_name == "Suiveur 3 ATR" and np.isfinite(A["atr"][j - 1]):
            stop = max(sl, hc - 3 * A["atr"][j - 1])
        if l[j] <= stop:
            return j, min(o[j], stop)
        if tp is not None and h[j] >= tp:
            return j, max(o[j], tp)
        if exit_name == "Fo hebdo":
            seen_fa = seen_fa or A["st"][j] == 1
            if seen_fa and A["st"][j] == -1:
                return j, c[j]
        if exit_name == "52 semaines" and j >= k + 52:
            return j, c[j]
        hc = max(hc, c[j])
    return None, None


# ------------------------------------------------------------------ équité

def run_trades(A, trades, fee, y, cash):
    """Courbe d'équité hebdomadaire (départ 1.0 à k0). trades = [(k_in, j_out|None, px|None)] sans chevauchement."""
    n, k0 = A["n"], A["k0"]
    E = np.ones(n)
    g = (1 + y) ** (1 / WEEKS_Y)
    gc = (1 + cash) ** (1 / WEEKS_Y)
    eq, pos = 1.0, k0
    for (k, j, px) in trades:
        for w in range(pos, k + 1):                      # liquidités jusqu'à l'entrée (clôture de k)
            E[w] = eq
            eq *= gc if w < k else 1.0
        eq_in = eq * (1 - fee)
        entry = A["c"][k]
        last = j if j is not None else n - 1
        for w in range(k + 1, last + 1):
            price = px if (j is not None and w == j) else A["c"][w]
            E[w] = eq_in * (price / entry) * g ** (w - k)
        if j is None:
            return E, True
        eq = E[j] * (1 - fee)
        pos = j + 1
        if pos >= n:
            E[j] = eq
            return E, False
    for w in range(pos, n):
        E[w] = eq
        eq *= gc
    return E, False


def metrics(E, k0):
    n = len(E)
    yrs = (n - 1 - k0) / WEEKS_Y
    seg = E[k0:]
    final = float(seg[-1])
    cagr = final ** (1 / yrs) - 1 if final > 0 and yrs > 0 else np.nan
    dd = float((seg / np.maximum.accumulate(seg) - 1).min())
    return {"final": final, "cagr": cagr, "dd": dd, "calmar": cagr / abs(dd) if dd < 0 and np.isfinite(cagr) else np.nan}


def buy_hold(A, fee, y, start=None):
    k0 = A["k0"] if start is None else start
    g = (1 + y) ** (1 / WEEKS_Y)
    w = np.arange(A["n"])
    E = np.ones(A["n"])
    E[k0:] = (1 - fee) * A["c"][k0:] / A["c"][k0] * g ** (w[k0:] - k0)
    return E


def dca(A, fee, y, cash):
    n, k0 = A["n"], A["k0"]
    g = (1 + y) ** (1 / WEEKS_Y)
    gc = (1 + cash) ** (1 / WEEKS_Y)
    ends = A["ends"]
    months = np.array([e.year * 12 + e.month for e in ends])
    buy_wk = [k for k in range(k0, n) if k == k0 or months[k] != months[k - 1]]
    amt = 1.0 / len(buy_wk)
    units, cash_left, flows = 0.0, 1.0, []
    E = np.ones(n)
    buys = set(buy_wk)
    for w in range(k0, n):
        if w > k0:
            cash_left *= gc
        tr = A["c"][w] * g ** (w - k0)                    # indice « rendement total » à la semaine w
        if w in buys:
            a = min(amt, cash_left)
            cash_left -= a
            units += a * (1 - fee) / tr
            flows.append((w, a))
        E[w] = cash_left + units * tr
    # rendement annualisé des sommes réellement investies (TRI)
    T = (n - 1 - k0) / WEEKS_Y
    fin = units * A["c"][n - 1] * g ** (n - 1 - k0)
    f = lambda r: sum(a * (1 + r) ** (T - (w - k0) / WEEKS_Y) for w, a in flows) - fin
    lo, hi = -0.9, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    return E, (lo + hi) / 2


# ------------------------------------------------------------------ une variante

def eligible(A, entry_name):
    ks = np.arange(A["k0"], A["n"] - 1)
    base = A["up"][ks] if entry_name in ("S", "S+creux") else np.ones(len(ks), bool)
    return ks, base


def trades_for(A, ks, cache, exit_name):
    """Enchaîne les trades non chevauchants parmi les entrées candidates ks (triées)."""
    out, free = [], -1
    for k in ks:
        if k <= free:
            continue
        if (exit_name, k) not in cache:
            cache[(exit_name, k)] = trade_exit(A, k, exit_name)
        r = cache[(exit_name, k)]
        if r is False:
            continue
        out.append((int(k), r[0], r[1]))
        if r[0] is None:
            break
        free = r[0]
    return out


def study_index(A, fee, y, cash, rng):
    res = {"A": A}
    cache = {}
    ks_all = np.arange(A["k0"], A["n"] - 1)
    dd_ok = np.isfinite(A["dd"][ks_all]) & (A["dd"][ks_all] <= -DD_MIN)
    res["bh"] = metrics(buy_hold(A, fee, y), A["k0"])
    starts = rng.choice(np.arange(A["k0"], min(A["k0"] + int(5 * WEEKS_Y), A["n"] - 100)), size=100, replace=False)
    ms = []
    for s0 in starts:
        m = metrics(buy_hold(A, fee, y, s0), s0)
        ms.append(m["cagr"])
    res["bh_rand"] = (np.nanmedian(ms), np.nanpercentile(ms, 10), np.nanpercentile(ms, 90))
    Ed, irr = dca(A, fee, y, cash)
    res["dca"] = metrics(Ed, A["k0"])
    res["dca"]["irr"] = irr
    res["var"] = {}
    for en in ENTRIES:
        sig = A["up"][ks_all] & (dd_ok if en == "S+creux" else True)
        ks_sig = ks_all[sig]
        pool = ks_all[dd_ok] if en == "S+creux" else ks_all
        for ex in EXITS:
            tr = trades_for(A, ks_sig, cache, ex)
            E, open_end = run_trades(A, tr, fee, y, cash)
            m = metrics(E, A["k0"])
            rets = [(t[2] if t[1] is not None else A["c"][-1]) / A["c"][t[0]] - 1 for t in tr]
            invested = np.zeros(A["n"], bool)
            for (k, j, px) in tr:
                invested[k + 1:(j if j is not None else A["n"] - 1) + 1] = True
            m.update({"n": len(tr), "win": float(np.mean([r > 0 for r in rets])) if rets else np.nan,
                      "mean_trade": float(np.mean(rets)) if rets else np.nan, "time": float(invested[A["k0"]:].mean()), "trades": tr})
            # hasard : mêmes sorties, entrées au sort dans les semaines éligibles
            flat_weeks = max(int((~invested[A["k0"]:]).sum()), 1)
            p = min(1.0, max(len(tr), 1) / flat_weeks)
            cag = []
            for _ in range(N_RAND):
                pick = pool[rng.random(len(pool)) < p]
                trr = trades_for(A, pick, cache, ex)
                Er, _o = run_trades(A, trr, fee, y, cash)
                cag.append(metrics(Er, A["k0"])["cagr"])
            cag = np.array(cag, dtype=float)
            m["pct"] = float(np.mean(cag[np.isfinite(cag)] <= m["cagr"]) * 100) if np.isfinite(m["cagr"]) else np.nan
            m["rand_med"] = float(np.nanmedian(cag))
            res["var"][(en, ex)] = m
    return res


# ------------------------------------------------------------------ rapport

def verdicts(results):
    out = {}
    names = list(results)
    for key in [(en, ex) for en in ENTRIES for ex in EXITS]:
        a = b = c = d = 0
        for nm in names:
            r = results[nm]
            m, bh = r["var"][key], r["bh"]
            a += bool(np.isfinite(m["cagr"]) and m["cagr"] > bh["cagr"])
            b += bool(np.isfinite(m["pct"]) and m["pct"] >= 90)
            c += bool(np.isfinite(m["calmar"]) and m["calmar"] > bh["calmar"])
            d += bool(np.isfinite(m["cagr"]) and m["cagr"] >= 0.7 * bh["cagr"])
        out[key] = {"a": a, "b": b, "c": c, "d": d, "bat": a >= 3 and b >= 3, "def": c >= 3 and d >= 3}
    return out


def report(results, fee, y_scale, cash, sens=None):
    pct = robust.pct
    names = list(results)
    L = [f"# S hebdo sur indices (proxys d'ETF PEA) contre achat-conservation, hasard et DCA — frais {fee * 100:.2f} % par ordre", "",
         f"**Règles fixées avant les résultats** : voir l'en-tête de `etf_s.py`. Fenêtre : {START_YEAR} à aujourd'hui ; dividendes forfaitaires x{y_scale:g} ; liquidités à {cash * 100:.1f} % ; "
         f"hasard = {N_RAND} tirages avec les mêmes sorties. Les S+creux concernent les indices à plus de {DD_MIN * 100:.0f} % sous leur plus haut de 52 semaines.", "",
         "## Références", "",
         "| indice | années | achat-conservation : rendement annualisé | creux max | départ au hasard (médiane ; 10-90 %) | DCA : multiple du capital | DCA : creux | DCA : TRI des sommes investies | S hebdo : nombre de signaux |",
         "|---|---|---|---|---|---|---|---|---|"]
    for nm in names:
        r = results[nm]
        A = r["A"]
        yrs = (A["n"] - 1 - A["k0"]) / WEEKS_Y
        bh, dc, br = r["bh"], r["dca"], r["bh_rand"]
        nsig = int(A["up"][A["k0"]:].sum())
        L.append(f"| {nm} | {yrs:.1f} | **{pct(bh['cagr'])}** | {pct(bh['dd'], 0)} | {pct(br[0])} ; [{pct(br[1])} ; {pct(br[2])}] | x{dc['final']:.2f} | {pct(dc['dd'], 0)} | {pct(dc['irr'])} | {nsig} |")
    L += ["", "## Stratégies : rendement annualisé par indice (nombre de trades, part du temps investi, centile du hasard)", "",
          "| entrée | sortie | " + " | ".join(names) + " |", "|---|---|" + "---|" * len(names)]
    for en in ENTRIES:
        for ex in EXITS:
            cells = []
            for nm in names:
                m, bh = results[nm]["var"][(en, ex)], results[nm]["bh"]
                cells.append(f"{pct(m['cagr'])} ({m['n']} ; {m['time'] * 100:.0f} % ; p{m['pct']:.0f})" if np.isfinite(m["cagr"]) else "–")
            L.append(f"| {en} | {ex} | " + " | ".join(cells) + " |")
    L += ["", "Pour comparer : achat-conservation = " + " ; ".join(f"{nm} {pct(results[nm]['bh']['cagr'])}" for nm in names) + ".", "",
          "## Verdict par variante", "",
          "| entrée | sortie | rendement > achat-conservation | ≥ 90e centile du hasard | rapport rendement/creux supérieur | rendement ≥ 70 % de l'achat-conservation | rendement moyen d'un trade | **verdict** |",
          "|---|---|---|---|---|---|---|---|"]
    V = verdicts(results)
    for key, v in V.items():
        mt = np.nanmean([results[nm]["var"][key]["mean_trade"] for nm in names])
        lab = "**bat l'achat-conservation**" if v["bat"] else "**défensive**" if v["def"] else "non"
        L.append(f"| {key[0]} | {key[1]} | {v['a']}/{len(names)} | {v['b']}/{len(names)} | {v['c']}/{len(names)} | {v['d']}/{len(names)} | {pct(mt)} | {lab} |")
    if sens:
        L += ["", "## Sensibilité aux frais (écart moyen de rendement annualisé avec l'achat-conservation, sur les indices)", "",
              "| entrée | sortie | " + " | ".join(f"frais {f:g} %" for f in sens["fees"]) + " |", "|---|---|" + "---|" * len(sens["fees"])]
        for key in V:
            L.append(f"| {key[0]} | {key[1]} | " + " | ".join(pct(sens["gap"][f][key]) for f in sens["fees"]) + " |")
    ok = [f"{k[0]} + {k[1]}" for k, v in V.items() if v["bat"]]
    de = [f"{k[0]} + {k[1]}" for k, v in V.items() if v["def"] and not v["bat"]]
    L += ["", "## Verdict", "",
          "- Variantes qui battent l'achat-conservation : " + (", ".join(ok) if ok else "aucune."),
          "- Variantes défensives (moins de risque pour un rendement d'au moins 70 %) : " + (", ".join(de) if de else "aucune."), "",
          "Lecture : les indices ont monté sur la période et l'achat-conservation profite de chaque jour investi. Une stratégie qui sort du marché ne gagne que si ses sorties évitent assez de baisses "
          "pour compenser les hausses manquées et les frais. Avec peu de trades par indice et des indices corrélés, un résultat favorable isolé ne prouve rien ; "
          "ce test cherche surtout à savoir si l'idée vaut la peine d'un suivi réel."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fee", type=float, default=0.25, help="frais par ordre en %")
    ap.add_argument("--cash", type=float, default=0.0, help="rendement annuel des liquidités en %")
    ap.add_argument("--div", type=float, default=1.0, help="facteur appliqué aux dividendes forfaitaires (0 = aucun)")
    ap.add_argument("--out", default="etf_s_report.md")
    args = ap.parse_args()
    rng = np.random.default_rng(2026)
    raw = sc.download(list(INDICES) + ["EURUSD=X"], "max", "1d")
    fx = raw.get("EURUSD=X")
    if fx is None:
        raise SystemExit("EURUSD=X indisponible sur Yahoo : test impossible.")
    now = pd.Timestamp.now(tz=sc.PARIS)
    A_by = {}
    for t, (nm, y, usd) in INDICES.items():
        df = raw.get(t)
        if df is None or len(df) < 1500:
            print(f"  {t} : indisponible, ignoré")
            continue
        df, _ = profit.clean_series(df)
        if usd:
            df = to_eur(df, fx)
        A = prepare(df, now)
        if A is not None:
            A_by[nm] = (A, y * args.div)
    if len(A_by) < 3:
        raise SystemExit("Moins de 3 indices disponibles : test impossible.")
    fee, cash = args.fee / 100.0, args.cash / 100.0
    results = {nm: study_index(A, fee, y, cash, rng) for nm, (A, y) in A_by.items()}
    gap = {}
    fees = [0.05, 0.25, 0.5]
    for f in fees:
        rs = results if abs(f - args.fee) < 1e-9 else {nm: study_index(A, f / 100.0, y, cash, np.random.default_rng(7)) for nm, (A, y) in A_by.items()}
        gap[f] = {key: float(np.nanmean([rs[nm]["var"][key]["cagr"] - rs[nm]["bh"]["cagr"] for nm in rs])) for key in [(en, ex) for en in ENTRIES for ex in EXITS]}
    text = report(results, fee, args.div, cash, {"fees": fees, "gap": gap})
    Path(args.out).write_text(text, encoding="utf-8")
    rows = []
    for nm, r in results.items():
        A = r["A"]
        for key, m in r["var"].items():
            for (k, j, px) in m["trades"]:
                rows.append({"indice": nm, "entrée": key[0], "sortie": key[1], "date_entrée": A["ends"][k], "prix_entrée": A["c"][k],
                             "date_sortie": A["ends"][j] if j is not None else None, "prix_sortie": px})
    pd.DataFrame(rows).to_csv("etf_s_trades.csv", index=False)
    print(text)


if __name__ == "__main__":
    main()
