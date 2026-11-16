#!/usr/bin/env python3
"""
Scanner Fa / Fo sur les actions françaises liquides.

S+  = « S haussière » : bougie HA sans corps (corps ≤ 10 % de sa hauteur) après ≥ 3 bougies HA baissières (🟩)
S-  = « S baissière » : bougie HA sans corps après ≥ 3 bougies HA vertes (🟥)
Actions détenues (variable HOLDINGS) : alertes étendues et notifications ⭐ en priorité haute.

Fa  = « validé »     : le HAUT du corps d'une bougie Heikin Ashi clôturée dépasse la MM7 (ascendante) ET la Tenkan ; dure jusqu'au Fo
Fo  = « invalidé »   : le BAS du corps d'une bougie Heikin Ashi clôturée passe sous la MM7 (descendante) ET la Tenkan ; dure jusqu'au Fa
En cours de validation : la bougie en formation n'est pas encore clôturée ; on estime la probabilité (en %)
                         qu'elle clôture avec la condition Fa remplie (simulation Monte-Carlo, voir simulate()).

Unités de temps : 1h, 4h, 1 jour, 1 semaine, 1 mois (4h reconstruite à partir du 1h : 9h-13h puis 13h-17h35).

Affichage et alertes (actions NON détenues) : uniquement Fa et S haussière EN COURS de validation (probabilité >= PROB_MIN),
en 4h, 1j, 1sem, 1mois ; une Fa déjà validée n'est plus montrée. Fo et S baissière : actions détenues seulement
(toutes unités de temps, le 1h avec un seuil de PROB_MIN_HELD_1H).

Sorties :
  docs/data.json  -> lu par l'application mobile (docs/index.html)
  ntfy.sh         -> notifications push « 🟢 Fa1h · AIR » / « 🔴 Fo1h · AIR » (clic = état des lieux dans l'app)
  state.json      -> mémorise les alertes déjà envoyées (anti-doublons)

Usage :
  python scanner.py              # scan + data.json + notifications
  python scanner.py --no-push    # scan + data.json, sans notification ni modification de state.json
  python scanner.py --test-push  # envoie une notification de test
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ------------------------------- Réglages --------------------------------
MA_SLOPE = True           # Fa exige une MM7 ascendante, Fo une MM7 descendante (False : pas de condition de pente)
MA_PERIOD = 7              # MM7
TENKAN_PERIOD = 9          # Tenkan-sen (Ichimoku) = (plus haut 9 + plus bas 9) / 2
BODY_MODE = "edge"         # "edge": Fa = HAUT du corps au-dessus des deux lignes, Fo = BAS du corps sous les deux lignes
                           # "full": corps ENTIER au-delà des deux lignes | "close": clôture seule
INDICATORS_ON = "ha"       # "ha": MM7/Tenkan calculées sur les bougies Heikin Ashi | "normal": sur bougies classiques
MIN_TURNOVER_EUR = 100_000   # liquidité : volume moyen échangé par jour (20 j) en euros
DATA_DELAY_MIN = 15        # Yahoo diffuse Euronext avec ~15 min de retard

# --- Configurations « en cours de validation » (bougie en formation) ---
PROB_MIN = 0.30            # une action apparaît (et notifie) quand sa probabilité de valider la configuration dépasse ce seuil
PROB_HIGH = 0.60           # 2e notification (une seule par bougie) quand la probabilité dépasse ce seuil
PROB_MIN_HELD_1H = 0.50    # seuil pour le 1h des actions détenues (le 1h n'est affiché QUE pour les actions détenues)
_ALL = ["1h", "4h", "1d", "1w", "1mo"]
VISIBLE_TF = ["4h", "1d", "1w", "1mo"]     # unités de temps affichées / notifiées pour les actions NON détenues
# Types : Fa, Fo, S+ (S haussière : sans corps après des bougies baissières), S- (S baissière : après des bougies vertes).
# Notifications « en cours » (alerte quand la probabilité franchit le seuil) : actions non détenues = Fa et S+ seulement.
NOTIFY_PENDING = {"Fa": VISIBLE_TF, "S+": VISIBLE_TF}
# Actions détenues (variable GitHub HOLDINGS) : tout, toutes unités de temps, notifications ⭐ en priorité haute
NOTIFY_PENDING_HELD = {"Fa": _ALL, "Fo": _ALL, "S+": _ALL, "S-": _ALL}
# Notifications de VALIDATION (bougie clôturée) : plus aucune pour les actions non détenues ; toutes pour les détenues.
NOTIFY = {"Fa": [], "Fo": [], "S+": [], "S-": []}
NOTIFY_HELD = {"Fa": _ALL, "Fo": _ALL, "S+": _ALL, "S-": _ALL}


PROB_NOTIFY = {"4h": 0.60, "1d": 0.60, "1w": 0.60, "1mo": 0.60}   # actions NON détenues : une seule notification par bougie, à 60 % sur toutes les unités
OBS_MIN = {"Fa": 0.40, "S+": 0.40, "Fo": 0.50, "S-": 0.50}   # suivi des « presque alertes » (sans notification) pour ajuster les seuils
PROB_EXIT = 0.60           # positions : alerte Fo (même UT) ou S baissière (UT inférieure) au-dessus de cette probabilité
PROB_SL = 0.90             # positions S : alerte « stop loss bientôt touché » au-dessus de cette probabilité
POS_ALERT_CONFIGS = ("S", "Fa")   # les entrées « Autre » (O) n'ont ni SL, ni TP, ni alerte


def threshold(tf, held):
    """Seuil de génération des événements « en cours ». Positions : bas (30 %), l'alerte exacte est filtrée dans select_new."""
    if held:
        return PROB_MIN
    return PROB_NOTIFY.get(tf, PROB_MIN)
S_MIN_RUN = 3              # S : nombre minimal de bougies HA de même sens consécutives avant la bougie sans corps
S_WICK_MAX = 0.10          # S : « sans mèche » = mèche <= 10 % de la hauteur de la bougie (haute pour S haussière, basse pour S baissière)
AUTO_UNIVERSE = True       # True : à chaque passage, ajoute à universe.csv toutes les actions de Paris listées par Yahoo (puis filtre sur la liquidité)
S_WICK_MIN = 2             # S : dans toute la série de bougies de même sens, au moins S_WICK_MIN sans mèche (haute pour S haussière, basse pour S baissière) ; les autres peuvent en avoir
S_BODY_MAX = 0.10          # S : « sans corps » = corps (|clôture - ouverture|) <= 10 % de la hauteur de la bougie HA
MAX_PUSH_PER_RUN = 30     # au-delà : une seule notification récapitulative (ntfy.sh limite ~250 messages/jour)
MA_TP = (20, 50, 100, 200)  # MM des objectifs (TP), calculées sur les clôtures classiques
MIN_BARS = 25              # historique minimal pour calculer correctement les indicateurs
# Facteur appliqué à la volatilité de la simulation, par unité de temps (réglé d'après les mesures de backtest.py :
# la volatilité « racine du temps » surestime les mouvements restants en intrajournalier ; la semaine et le mois sont proches de 1).
SIGMA_SCALE = {"1h": 0.65, "4h": 0.65, "1d": 0.65, "1w": 0.9, "1mo": 0.9}


# Table de correction des probabilités (brut de la simulation -> fréquence réellement observée), mesurée par backtest.py sur ~380 actions
# liquides et ~2 ans d'historique horaire (1,75 M de cas). Interpolation linéaire entre les points ; monotone croissante.
# Intrajournalier (1h, 4h, 1j) et hebdomadaire séparés ; S hebdomadaire et mensuel : trop peu de cas, non corrigés.
CALIB = {
    ("fa", "short"): ([0, .14, .24, .34, .44, .545, .645, .745, .85, 1.0], [0, .105, .16, .22, .335, .525, .645, .73, .825, .965]),
    ("fo", "short"): ([0, .14, .24, .34, .44, .55, .65, .75, .85, 1.0], [0, .135, .195, .27, .385, .58, .705, .80, .86, .99]),
    ("s", "short"): ([0, .14, .24, .34, .44, .54, .64, .74, .84, .95, 1.0], [0, .12, .235, .335, .44, .55, .61, .67, .75, .80, .82]),
    ("fa", "long"): ([0, .14, .24, .34, .44, .55, .65, .75, .85, 1.0], [0, .13, .19, .30, .42, .53, .70, .81, .82, .97]),
    ("fo", "long"): ([0, .14, .24, .34, .45, .55, .65, .75, .85, 1.0], [0, .10, .17, .27, .39, .56, .68, .79, .86, .99]),
}


def calibrate(kind, tf, p):
    """Probabilité corrigée d'après la fréquence observée (voir CALIB). kind : « fa », « fo » ou « s »."""
    if p is None:
        return None
    pts = CALIB.get((kind, "long" if tf in ("1w", "1mo") else "short"))
    if pts is None or (tf == "1mo"):
        return p
    return float(np.interp(p, pts[0], pts[1]))


def sigma_scale(tf):
    return SIGMA_SCALE[tf] if isinstance(SIGMA_SCALE, dict) else float(SIGMA_SCALE)
SIM_PATHS = 400            # trajectoires Monte-Carlo par bougie en formation
SIM_STEPS = 8
# -------------------------------------------------------------------------

PARIS = "Europe/Paris"
OPEN_MIN = 9 * 60                    # 9h00
CLOSE_MIN = 17 * 60 + 35             # 17h35 (fin de l'enchère de clôture)
SESSION_MIN = CLOSE_MIN - OPEN_MIN

TF_ORDER = ["1h", "4h", "1d", "1w", "1mo"]
TF_LABEL = {"1h": "1h", "4h": "4h", "1d": "1j", "1w": "1sem", "1mo": "1mois"}

HERE = Path(__file__).resolve().parent
UNIVERSE_FILE = HERE / "universe.csv"
DATA_FILE = HERE / "docs" / "data.json"
STATE_FILE = HERE / "state.json"


# ============================ Calcul des indicateurs ============================

def heikin_ashi(o, h, l, c):
    """Bougies Heikin Ashi à partir de tableaux numpy OHLC."""
    n = len(c)
    hac = (o + h + l + c) / 4.0
    hao = np.empty(n)
    hao[0] = (o[0] + c[0]) / 2.0
    for i in range(1, n):
        hao[i] = (hao[i - 1] + hac[i - 1]) / 2.0
    hah = np.maximum.reduce([h, hao, hac])
    hal = np.minimum.reduce([l, hao, hac])
    return hao, hah, hal, hac


def lines(sh, sl, sc):
    """MM7 et Tenkan sur les séries (haut, bas, clôture) fournies."""
    mm = pd.Series(sc).rolling(MA_PERIOD).mean().to_numpy()
    tk = ((pd.Series(sh).rolling(TENKAN_PERIOD).max() + pd.Series(sl).rolling(TENKAN_PERIOD).min()) / 2.0).to_numpy()
    return mm, tk


def body_refs(hao, hac):
    """(repère pour Fa, repère pour Fo) selon BODY_MODE : Fa si repère_fa > les deux lignes, Fo si repère_fo < les deux lignes."""
    if BODY_MODE == "edge":
        return np.maximum(hao, hac), np.minimum(hao, hac)      # haut du corps pour Fa, bas du corps pour Fo
    if BODY_MODE == "full":
        return np.minimum(hao, hac), np.maximum(hao, hac)      # corps entier
    return hac, hac


def persist(raw):
    """Fa et Fo durent : un état ne change que lorsque le signal inverse apparaît (corps entièrement de l'autre côté
    des deux lignes). Entre-temps (corps à cheval sur une ligne, entre les deux lignes), l'état précédent continue."""
    st = np.zeros(len(raw), dtype=int)
    cur = 0
    for i, v in enumerate(raw):
        if v != 0:
            cur = int(v)
        st[i] = cur
    return st


def classify(hao, hac, mm, tk):
    """+1 = Fa, -1 = Fo, 0 = aucun signal sur cette bougie (voir BODY_MODE).
    Si une même bougie remplit les deux conditions (corps qui couvre toute la zone MM7-Tenkan), la couleur du corps tranche :
    bougie verte = Fa, bougie rouge = Fo (corps nul : aucun signal, l'état précédent continue)."""
    up_ref, dn_ref = body_refs(hao, hac)
    top, bot = np.maximum(mm, tk), np.minimum(mm, tk)
    valid = ~np.isnan(top)
    fa = valid & (up_ref > top)
    fo = valid & (dn_ref < bot)
    if MA_SLOPE:                                   # MM7 ascendante pour Fa, descendante pour Fo
        rising = np.zeros(len(mm), dtype=bool)
        falling = np.zeros(len(mm), dtype=bool)
        with np.errstate(invalid="ignore"):
            rising[1:] = mm[1:] > mm[:-1]
            falling[1:] = mm[1:] < mm[:-1]
        fa, fo = fa & rising, fo & falling
    st = np.zeros(len(hac), dtype=int)
    st[fa & ~fo] = 1
    st[fo & ~fa] = -1
    both = fa & fo
    st[both & (hac > hao)] = 1
    st[both & (hac < hao)] = -1
    return st


# ================================ Calendrier ===================================

def at(day, minutes):
    return day.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)


def bar_end(start, tf):
    """Heure de clôture (heure de Paris) de la bougie qui commence à `start`."""
    d = start.normalize()
    if tf == "1h":
        return min(start + pd.Timedelta(hours=1), at(d, CLOSE_MIN))
    if tf == "4h":
        return at(d, 13 * 60) if start.hour < 13 else at(d, CLOSE_MIN)
    if tf == "1d":
        return at(d, CLOSE_MIN)
    if tf == "1w":
        monday = d - pd.Timedelta(days=d.weekday())
        return at((monday + pd.Timedelta(days=4)).normalize(), CLOSE_MIN)
    if tf == "1mo":
        first = d.tz_localize(None).replace(day=1)
        last = first + pd.offsets.BMonthEnd(0)
        return at(last.tz_localize(PARIS), CLOSE_MIN)
    raise ValueError(tf)


def day_frac(now):
    """Part de la séance déjà écoulée (0 → 9h00, 1 → 17h35 ou week-end)."""
    if now.weekday() >= 5:
        return 1.0
    m = now.hour * 60 + now.minute
    return float(np.clip((m - OPEN_MIN) / SESSION_MIN, 0.0, 1.0))


def remaining_fraction(start, tf, now):
    """Part de la bougie qu'il reste à écouler (en temps de bourse)."""
    if tf in ("1h", "4h"):
        end = bar_end(start, tf)
        return float(np.clip((end - now) / (end - start), 0.0, 1.0))
    if tf == "1d":
        return 1.0 - day_frac(now)
    if tf == "1w":
        elapsed = 5.0 if now.weekday() >= 5 else now.weekday() + day_frac(now)
        return float(np.clip(1.0 - elapsed / 5.0, 0.0, 1.0))
    if tf == "1mo":
        first = pd.Timestamp(year=now.year, month=now.month, day=1)
        nxt = first + pd.offsets.MonthBegin(1)
        total = np.busday_count(first.date(), nxt.date())
        done = np.busday_count(first.date(), now.date()) + (day_frac(now) if now.weekday() < 5 else 0.0)
        return float(np.clip(1.0 - done / max(total, 1), 0.0, 1.0))
    raise ValueError(tf)


# ============================ Reconstruction des bougies ============================

def norm_index(df):
    idx = pd.DatetimeIndex(df.index)
    idx = idx.tz_localize(PARIS) if idx.tz is None else idx.tz_convert(PARIS)
    out = df.copy()
    out.index = idx
    return out


def aggregate(df, tf):
    """Reconstruit des bougies 4h / semaine / mois à partir du 1h ou du jour."""
    naive = df.index.tz_localize(None)
    if tf == "4h":
        key = naive.normalize() + pd.to_timedelta(np.where(naive.hour < 13, 9, 13), unit="h")
    elif tf == "1w":
        key = naive.normalize() - pd.to_timedelta(naive.weekday, unit="D")
    elif tf == "1mo":
        key = naive.normalize() - pd.to_timedelta(naive.day - 1, unit="D")
    else:
        raise ValueError(tf)
    g = df.groupby(key)
    out = pd.DataFrame({
        "Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
        "Close": g["Close"].last(), "Volume": g["Volume"].sum(),
    })
    out.index = pd.DatetimeIndex(out.index).tz_localize(PARIS)
    return out


# ================================== Simulation ==================================

def simulate(o, h, l, c, hao, hah, hal, hac, sh, sl, sc, remaining, sigma, rng, bear_run=0, bull_run=0):
    """
    Probabilités (p_fa, p_fo, p_s_up, p_s_down) que la bougie en formation (dernier indice) clôture en
    Fa / Fo / S haussière / S baissière (S : corps <= S_BODY_MAX x hauteur, après assez de bougies de même sens).
    Marche aléatoire log-normale sur le temps restant, avec la volatilité récente de l'unité de temps ;
    pour chaque trajectoire on recalcule la bougie HA finale, la MM7 et la Tenkan. C'est une estimation
    statistique, pas une prévision.
    """
    n = len(c) - 1
    steps = rng.normal(0.0, sigma * np.sqrt(max(remaining, 1e-9) / SIM_STEPS), size=(SIM_PATHS, SIM_STEPS))
    path = c[n] * np.exp(np.cumsum(steps, axis=1))
    hi_s = np.maximum(h[n], path.max(axis=1))
    lo_s = np.minimum(l[n], path.min(axis=1))
    cl_s = path[:, -1]
    hac_s = (o[n] + hi_s + lo_s + cl_s) / 4.0
    hah_s = np.maximum.reduce([hi_s, np.full(SIM_PATHS, hao[n]), hac_s])
    hal_s = np.minimum.reduce([lo_s, np.full(SIM_PATHS, hao[n]), hac_s])
    if INDICATORS_ON == "ha":
        s_h, s_l, s_c = hah_s, hal_s, hac_s
    else:
        s_h, s_l, s_c = hi_s, lo_s, cl_s
    mm = (sc[n - (MA_PERIOD - 1):n].sum() + s_c) / MA_PERIOD
    tk = (np.maximum(sh[n - (TENKAN_PERIOD - 1):n].max(), s_h) + np.minimum(sl[n - (TENKAN_PERIOD - 1):n].min(), s_l)) / 2.0
    up_ref, dn_ref = body_refs(np.full(SIM_PATHS, hao[n]), hac_s)
    fa_raw = up_ref > np.maximum(mm, tk)
    fo_raw = dn_ref < np.minimum(mm, tk)
    if MA_SLOPE:
        mm_prev = sc[n - MA_PERIOD:n].sum() / MA_PERIOD       # MM7 de la dernière bougie clôturée
        fa_raw, fo_raw = fa_raw & (mm > mm_prev), fo_raw & (mm < mm_prev)
    both = fa_raw & fo_raw
    green, red = hac_s > hao[n], hac_s < hao[n]
    fa, fo = (fa_raw & ~fo_raw) | (both & green), (fo_raw & ~fa_raw) | (both & red)
    hh = hah_s - hal_s
    nobody = (hh > 0) & (np.abs(hac_s - hao[n]) <= S_BODY_MAX * hh)
    p_nobody = float(nobody.mean())
    return (float(fa.mean()), float(fo.mean()),
            p_nobody if bear_run >= S_MIN_RUN else 0.0, p_nobody if bull_run >= S_MIN_RUN else 0.0)


# ================================ Analyse d'une UT ================================

def fmt_px(v):
    return f"{v:.{4 if abs(v) < 1 else 3 if abs(v) < 10 else 2}f}"


def px(v, ref=None):
    """Arrondi d'un prix à un nombre de décimales adapté à son niveau (pas de cotation Euronext approché : < 1 € : 4 ; < 10 € : 3 ; sinon 2)."""
    r = abs(ref if ref is not None else v)
    return round(float(v), 4 if r < 1 else 3 if r < 10 else 2)


def levels(df, o, c, hao, hac, hal, mm, tk, nc, j, cur):
    """Niveaux utiles à l'application pour les stops et objectifs (SL / TP) d'une position, sur cette unité de temps.
    ma : MM7 (Heikin Ashi, comme la configuration) et MM20 / 50 / 100 / 200 des clôtures classiques ; tk / mm7_dn : Tenkan et pente
    de la MM7 (pour « Fo dans l'UT supérieure ») ; low_prev : plus bas (mèche ou corps) de la dernière bougie HA clôturée ;
    fa_body_low : bas du corps de la 1re bougie de la Fa en cours (sinon None)."""
    close = df["Close"].to_numpy(dtype=float)
    ma = {"7": px(mm[-1])}
    for n in MA_TP:
        ma[str(n)] = px(close[-n:].mean()) if len(close) >= n else None
    first = j if cur == 1 else None
    return {
        "ma": ma,
        "tk": px(tk[-1]),
        "mm7_dn": bool(len(mm) > 1 and mm[-1] < mm[-2]),
        "low_prev": px(hal[nc - 1]),
        "fa_body_low": px(min(hao[first], hac[first])) if first is not None else None,
    }


def analyze_tf(df, tf, now_eff, rng):
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    if len(df) < MIN_BARS:
        return None
    idx = df.index
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    hao, hah, hal, hac = heikin_ashi(o, h, l, c)
    sh, sl, sc = (hah, hal, hac) if INDICATORS_ON == "ha" else (h, l, c)
    mm, tk = lines(sh, sl, sc)
    st = persist(classify(hao, hac, mm, tk))

    forming = bar_end(idx[-1], tf) > now_eff
    nc = len(df) - 1 if forming else len(df)          # nombre de bougies clôturées
    if nc < MIN_BARS - 1:
        return None
    stc = st[:nc]
    cur = int(stc[-1])
    j = nc - 1
    while j > 0 and stc[j - 1] == cur:
        j -= 1

    # Configuration S : bougie HA sans corps après plusieurs bougies HA de même sens
    #   S+ (haussière) : après des bougies baissières | S- (baissière) : après des bougies vertes
    rng_h = hah - hal
    nobody = (rng_h > 0) & (np.abs(hac - hao) <= S_BODY_MAX * rng_h)
    bear = hac < hao
    bull = hac > hao
    # bougies « propres » : baissière sans (ou peu de) mèche au-dessus du corps / haussière sans mèche sous le corps
    bear_clean = bear & (rng_h > 0) & ((hah - np.maximum(hao, hac)) <= S_WICK_MAX * rng_h)
    bull_clean = bull & (rng_h > 0) & ((np.minimum(hao, hac) - hal) <= S_WICK_MAX * rng_h)

    def clean_count(k, n, arr):
        """Parmi les n bougies de la série juste avant k, combien respectent la règle de mèche ?"""
        return int(arr[k - n:k].sum()) if n > 0 else 0

    def run(k, arr):
        n = 0
        while k - 1 - n >= 0 and arr[k - 1 - n]:
            n += 1
        return n

    def s_at(k):
        """(sens, n) si la bougie k est une configuration S, sinon (None, 0)."""
        if k < S_MIN_RUN or not nobody[k]:
            return None, 0
        nb, nu = run(k, bear), run(k, bull)
        if nb >= S_MIN_RUN and clean_count(k, nb, bear_clean) >= S_WICK_MIN:
            return "up", nb
        if nu >= S_MIN_RUN and clean_count(k, nu, bull_clean) >= S_WICK_MIN:
            return "down", nu
        return None, 0

    events = []
    for k in (nc - 2, nc - 1):
        if k >= 1 and stc[k] != stc[k - 1] and stc[k] != 0:
            events.append(("Fa" if stc[k] == 1 else "Fo", idx[k].isoformat()))
        d, _n = s_at(k)
        if d:
            events.append(("S+" if d == "up" else "S-", idx[k].isoformat()))
    s_dir, s_n = s_at(nc - 1)
    cls = classify(hao, hac, mm, tk)
    recent = {}                                       # résultat réel des dernières bougies clôturées (sert au suivi des alertes, retiré de data.json)
    for k in range(max(0, nc - 4), nc):
        recent[idx[k].isoformat()] = {"c": int(cls[k]), "s": s_at(k)[0], "lo": float(l[k])}

    res = {
        "s": {"active": s_dir is not None, "dir": s_dir, "n": s_n, "bar": idx[nc - 1].isoformat()},
        "state": {1: "fa", -1: "fo", 0: "none"}[cur],
        "since": idx[j].isoformat(),
        "bars": int(nc - j),
        "forming": bool(forming),
        "bar_forming": idx[-1].isoformat() if forming else None,
        "over": {"fa": bool(forming and cls[-1] == 1),     # la bougie en formation remplit déjà Fa / Fo
                 "fo": bool(forming and cls[-1] == -1)},
        "recent": recent,
        "p_fa": None,
        "p_fo": None,
        "p_s_up": None,
        "p_s_down": None,
        "mm7": px(mm[nc - 1]),
        "tenkan": px(tk[nc - 1]),
        "events": events,
        "buy": int(c[-1] * df["Volume"].iloc[-1]) if c[-1] > o[-1] and np.isfinite(df["Volume"].iloc[-1]) else 0,
        "lv": levels(df, o, c, hao, hac, hal, mm, tk, nc, j, cur),
    }
    if forming:
        rets = np.diff(np.log(c[:nc]))[-100:]
        sigma = float(np.std(rets)) * sigma_scale(tf) if len(rets) > 5 else float("nan")
        if np.isfinite(sigma) and sigma > 0:
            rem = remaining_fraction(idx[-1], tf, now_eff)
            p_fa, p_fo, p_su, p_sd = simulate(o, h, l, c, hao, hah, hal, hac, sh, sl, sc, rem, sigma, rng,
                                              run(nc, bear) if clean_count(nc, run(nc, bear), bear_clean) >= S_WICK_MIN else 0,
                                              run(nc, bull) if clean_count(nc, run(nc, bull), bull_clean) >= S_WICK_MIN else 0)
            res["sd_rem"] = round(sigma * float(np.sqrt(max(rem, 1e-9))), 5)      # écart-type du rendement jusqu'à la fin de la bougie
            p_fa, p_fo = calibrate("fa", tf, p_fa), calibrate("fo", tf, p_fo)
            p_su, p_sd = calibrate("s", tf, p_su), calibrate("s", tf, p_sd)
            res["p_fa"], res["p_fo"] = round(p_fa, 2), round(p_fo, 2)
            res["p_s_up"], res["p_s_down"] = round(p_su, 2), round(p_sd, 2)
    return res


# ================================ Téléchargement ================================

def download(tickers, period, interval, chunk=40):
    import yfinance as yf
    out = {}
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        raw = None
        for attempt in range(2):
            try:
                raw = yf.download(tickers=part, period=period, interval=interval, group_by="ticker",
                                  auto_adjust=False, threads=True, progress=False)
                if raw is not None and len(raw):
                    break
            except Exception as e:  # réseau, limitation de débit…
                print(f"  téléchargement {interval} (essai {attempt + 1}) : {e}", file=sys.stderr)
            time.sleep(5)
        if raw is None or not len(raw):
            continue
        multi = isinstance(raw.columns, pd.MultiIndex)
        for t in part:
            try:
                df = raw[t] if multi else raw
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Open", "High", "Low", "Close"])
            except KeyError:
                continue
            if len(df):
                out[t] = norm_index(df)
    return out


def turnover(df_daily):
    tail = df_daily.tail(20)
    return float((tail["Close"] * tail["Volume"]).mean())


def load_universe():
    with open(UNIVERSE_FILE, encoding="utf-8") as f:
        rows = [r for r in csv.reader(f) if r and not r[0].startswith("#") and r[0] != "ticker"]
    return {r[0].strip(): (r[1].strip() if len(r) > 1 else r[0].strip()) for r in rows}


def fetch_paris_listing(page=250, max_pages=8):
    """Toutes les actions cotées à Paris selon le filtre de Yahoo (écran « exchange = PAR »). {ticker: nom}.
    Renvoie {} en cas d'échec : on s'en tient alors à universe.csv."""
    out = {}
    try:
        import yfinance as yf
        q = yf.EquityQuery("eq", ["exchange", "PAR"])
        for i in range(max_pages):
            res = yf.screen(q, offset=i * page, size=page)
            quotes = res.get("quotes", []) if isinstance(res, dict) else []
            for it in quotes:
                sym = str(it.get("symbol", ""))
                if sym.endswith(".PA") and it.get("quoteType", "EQUITY") == "EQUITY":
                    out[sym] = str(it.get("shortName") or it.get("longName") or sym[:-3])
            total = res.get("total", 0) if isinstance(res, dict) else 0
            if not quotes or (i + 1) * page >= total:
                break
    except Exception as e:
        print(f"  liste Yahoo des actions de Paris indisponible ({e}) : universe.csv seul.", file=sys.stderr)
    return out


# ===================================== Scan =====================================

def lower_tf(tf):
    i = TF_ORDER.index(tf)
    return TF_ORDER[i - 1] if i > 0 else None


def higher_tf(tf):
    i = TF_ORDER.index(tf)
    return TF_ORDER[i + 1] if i + 1 < len(TF_ORDER) else None


def bar_ms(tf):
    return {"1h": 3600e3, "4h": 14400e3, "1d": 86400e3, "1w": 604800e3, "1mo": 2678400e3}[tf]


def position_sl(pos, stock):
    """Stop loss d'une position (même règle que l'application). Retourne (valeur, mode effectif) ; valeur None s'il n'y a pas de SL.
    S : la plus haute de deux valeurs : plus bas de la bougie HA précédente, bas du corps de la 1re bougie de la Fa en cours (UT inférieure).
    Fa, ou S devenue Fa depuis l'entrée : pas de SL."""
    c, tf = pos.get("c"), pos.get("tf")
    if c not in POS_ALERT_CONFIGS or tf not in stock["tf"]:
        return None, None
    r = stock["tf"][tf]
    if not r.get("lv"):
        return None, c
    if c == "S" and r["state"] == "fa" and pos.get("ts") and \
            pd.Timestamp(r["since"]).timestamp() * 1000 + bar_ms(tf) > pos["ts"]:
        return None, "Fa"
    if c == "Fa":
        return None, "Fa"
    best = r["lv"]["low_prev"]
    lo = lower_tf(tf)
    lr = stock["tf"].get(lo) if lo else None
    if lr and lr["state"] == "fa" and lr.get("lv") and lr["lv"].get("fa_body_low") is not None:
        best = max(best, lr["lv"]["fa_body_low"])
    return best, "S"


def p_touch(price, level, sd):
    """Probabilité (marche aléatoire sans dérive) que le cours touche `level` (< price) avant la fin de la bougie : 2 x Phi(ln(level/price)/sd)."""
    if price <= level:
        return 1.0
    if not sd or sd <= 0:
        return 0.0
    z = math.log(level / price) / sd
    return min(1.0, 2.0 * 0.5 * (1.0 + math.erf(z / math.sqrt(2.0))))


def scan(universe, fetch, now, min_turnover=MIN_TURNOVER_EUR, holdings=frozenset(), positions=None):
    """Retourne (data, events). `fetch(tickers, period, interval)` -> {ticker: DataFrame}.
    Les actions détenues sont toujours analysées, même hors de universe.csv ou sous le seuil de liquidité."""
    now_eff = now - pd.Timedelta(minutes=DATA_DELAY_MIN)
    universe = dict(universe)
    for t in holdings:
        universe.setdefault(t, short(t))
    tickers = list(universe)
    daily = fetch(tickers, "5y", "1d")
    no_data = [t for t in tickers if t not in daily]
    liquid = [t for t in tickers if t in daily and (t in holdings or turnover(daily[t]) >= min_turnover)]
    hourly = fetch(liquid, "3mo", "1h") if liquid else {}
    held_liquid = [t for t in liquid if t in holdings]
    if held_liquid:                                           # historique plus long pour la MM100 / MM200 en 4h des positions
        hourly.update(fetch(held_liquid, "2y", "1h"))
    rng = np.random.default_rng(12345)

    stocks, events = {}, []
    for t in liquid:
        frames = {"1d": daily[t], "1w": aggregate(daily[t], "1w"), "1mo": aggregate(daily[t], "1mo")}
        if t in hourly and len(hourly[t]):
            frames["1h"] = hourly[t]
            frames["4h"] = aggregate(hourly[t], "4h")
        tfs = {}
        for tf in TF_ORDER:
            if tf not in frames:
                continue
            try:
                res = analyze_tf(frames[tf], tf, now_eff, rng)
            except Exception as e:
                print(f"{t} {tf}: erreur {e}", file=sys.stderr)
                continue
            if res is None:
                continue
            for kind, ts in res.pop("events"):
                events.append({"ticker": t, "tf": tf, "kind": kind, "bar": ts})
            if res["forming"]:                         # configurations en cours de validation (suffixe « ~ »)
                held = t in holdings
                for kind, p, ok in (("Fa", res["p_fa"], res["state"] != "fa"),
                                    ("Fo", res["p_fo"], res["state"] != "fo"),
                                    ("S+", res["p_s_up"], True),
                                    ("S-", res["p_s_down"], True)):
                    if p is not None and ok and p >= threshold(tf, held):
                        events.append({"ticker": t, "tf": tf, "kind": kind + "~", "bar": res["bar_forming"], "p": p,
                                       "over": res["over"]["fa"] if kind == "Fa" else res["over"]["fo"] if kind == "Fo" else False})
                    elif p is not None and ok and not held and tf in VISIBLE_TF and OBS_MIN[kind] <= p < threshold(tf, held):
                        events.append({"ticker": t, "tf": tf, "kind": kind + "~", "bar": res["bar_forming"], "p": p, "obs": True})   # suivi seulement, pas de notification
            tfs[tf] = res
        if not tfs:
            continue
        closes = daily[t]["Close"]
        price = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) > 1 else price
        stocks[t] = {"name": universe[t], "price": px(price), "chg": round((price / prev - 1) * 100, 2), "tf": tfs}
        for pos in (positions or {}).get(t, []):             # alerte « stop loss bientôt touché » (positions S)
            sl, mode = position_sl(pos, stocks[t])
            r = tfs.get(pos.get("tf"))
            if sl is not None and mode == "S" and r and r["forming"] and r.get("sd_rem"):
                p = p_touch(price, sl, r["sd_rem"])
                if p >= PROB_MIN:
                    events.append({"ticker": t, "tf": pos["tf"], "kind": "SL~", "bar": r["bar_forming"], "p": round(p, 2), "sl": px(sl, price)})

    data = {
        "meta": {
            "updated": now.isoformat(),
            "delay_min": DATA_DELAY_MIN,
            "prob_min": PROB_MIN,
            "prob_min_held_1h": PROB_MIN_HELD_1H,
            "prob_high": PROB_HIGH,
            "visible_tf": VISIBLE_TF,
            "labels": TF_LABEL,
            "universe": len(tickers),
            "analysed": len(stocks),
            "illiquid": len(tickers) - len(no_data) - len(liquid),
            "no_data": no_data,
        },
        "stocks": stocks,
    }
    return data, events


# ================================ Notifications ================================

SYM = {"fa": "✅", "fo": "🔻", "none": "▫️"}
S_SQ = {"up": " 🟩S", "down": " 🟥S"}


def short(ticker):
    return ticker[:-3] if ticker.endswith(".PA") else ticker


def summary_line(stock):
    return " · ".join(
        f"{TF_LABEL[tf]} {SYM[stock['tf'][tf]['state']]}{S_SQ.get(stock['tf'][tf].get('s', {}).get('dir'), '')}"
        for tf in TF_ORDER if tf in stock["tf"])


def position_alert(e, plist):
    """Alerte de sortie d'une position S / Fa : Fo sur la même UT (> 60 %), S baissière sur l'UT inférieure (> 60 %),
    stop loss bientôt touché (> 90 %). Les entrées « Autre » ne déclenchent rien. Retourne True si l'événement (en cours ou validé) compte."""
    base = e["kind"].rstrip("~")
    p = e.get("p")
    for pos in plist:
        if pos.get("c") not in POS_ALERT_CONFIGS or not pos.get("tf"):
            continue
        if base == "Fo" and e["tf"] == pos["tf"] and (p is None or p >= PROB_EXIT):
            return True
        if base == "S-" and e["tf"] == lower_tf(pos["tf"]) and (p is None or p >= PROB_EXIT):
            return True
        if base == "SL" and e["tf"] == pos["tf"] and p is not None and p >= PROB_SL:
            return True
    return False


def select_new(events, state, holdings=frozenset(), positions=None):
    """Événements plus récents que ceux déjà notifiés. `positions` : {ticker: [positions]} ; une action détenue sans détail de position
    garde les anciennes règles (tout, toutes unités)."""
    new = []
    for e in events:
        if e.get("obs"):
            continue
        held = e["ticker"] in holdings
        if held and positions is not None:
            if not position_alert(e, positions.get(e["ticker"], [])):
                continue
            if e["kind"].endswith("~"):
                key = f"{e['ticker']}|{e['tf']}|{e['kind']}|{e['bar']}"
                e["level"] = 2
                if e["level"] > int(state.get(key, 0)):
                    new.append((key, e))
            else:
                key = f"{e['ticker']}|{e['tf']}|{e['kind']}"
                last = state.get(key)
                if last is None or pd.Timestamp(last if isinstance(last, str) else 0) < pd.Timestamp(e["bar"]):
                    new.append((key, e))
            continue
        if e["kind"].endswith("~"):          # « en cours » : 1 alerte au seuil, 1 de plus si > PROB_HIGH, par bougie
            base = e["kind"][:-1]
            rules = NOTIFY_PENDING_HELD if held else NOTIFY_PENDING
            if e["tf"] not in rules.get(base, []):
                continue
            key = f"{e['ticker']}|{e['tf']}|{e['kind']}|{e['bar']}"
            e["level"] = 2 if e["p"] >= PROB_HIGH else 1
            if e["level"] > int(state.get(key, 0)):
                new.append((key, e))
            continue
        rules = NOTIFY_HELD if held else NOTIFY
        if e["tf"] not in rules.get(e["kind"], []):
            continue
        key = f"{e['ticker']}|{e['tf']}|{e['kind']}"
        last = state.get(key)
        if last is None or pd.Timestamp(last if isinstance(last, str) else 0) < pd.Timestamp(e["bar"]):
            new.append((key, e))
    order = {tf: i for i, tf in enumerate(reversed(TF_ORDER))}      # positions détenues d'abord, puis grandes unités de temps
    new.sort(key=lambda ke: (ke[1]["ticker"] not in holdings, order[ke[1]["tf"]]))
    return new


def ntfy_post(topic, title, message, click=None, priority=3, tags=None):
    body = {"topic": topic, "title": title, "message": message, "priority": priority}
    if tags:
        body["tags"] = tags
    if click:
        body["click"] = click
    r = requests.post("https://ntfy.sh/", json=body, timeout=15)
    r.raise_for_status()


def build_notification(e, stock, app_url, held=False):
    """(titre, message, lien, priorité). Les actions détenues : ⭐ + « POSITION » + priorité haute (5 = urgente pour les signaux baissiers)."""
    lab = TF_LABEL[e["tf"]]
    kind = e["kind"]
    pending = kind.endswith("~")
    kind = kind.rstrip("~")
    icon = {"Fa": "🟢", "Fo": "🔴", "S+": "🟩", "S-": "🟥", "SL": "🛑"}[kind]
    name = "S" if kind.startswith("S") else kind
    if pending and kind == "SL":
        pct = int(round(e["p"] * 100))
        title = f"⭐ 🛑 SL {lab} {pct}% · {short(e['ticker'])}"
        message = (f"POSITION DÉTENUE\n{stock['name']} — {fmt_px(stock['price'])} €\n"
                   f"{pct} % de chances que le cours touche le stop loss ({fmt_px(e['sl'])} €) avant la fin de la bougie {lab}.\n{summary_line(stock)}")
        click = f"{app_url.rstrip('/')}/#{e['ticker']}@{e['tf']}" if app_url else None
        return title, message, click, 5
    if pending:
        pct = int(round(e["p"] * 100))
        title = f"{icon} {name}{lab} {pct}% · {short(e['ticker'])}"
        if kind == "Fa":
            cond = "haut du corps au-dessus de la MM7 (ascendante) et de la Tenkan" if BODY_MODE == "edge" else "corps au-dessus de la MM7 et de la Tenkan"
        elif kind == "Fo":
            cond = "bas du corps sous la MM7 (descendante) et la Tenkan" if BODY_MODE == "edge" else "corps sous la MM7 et la Tenkan"
        else:
            cond = f"bougie sans corps après {'des bougies baissières, dont au moins 2 sans mèche haute' if kind == 'S+' else 'des bougies vertes, dont au moins 2 sans mèche basse'}"
        what = f"EN COURS : {pct} % de chances que la bougie {lab} clôture en {name} ({cond})"
        if e.get("over"):
            what += "\nLa bougie remplit déjà la condition en ce moment."
        if e.get("level") == 2:
            what += f"\nProbabilité supérieure à {int(PROB_HIGH * 100)} %."
        message = f"{stock['name']} — {fmt_px(stock['price'])} €\n{what}\n{summary_line(stock)}"
        priority = 4 if e.get("level") == 2 else 3
        if held:
            title = f"⭐ {title}"
            message = "POSITION DÉTENUE\n" + message
            priority = 5 if kind in ("Fo", "S-") else 4
        click = f"{app_url.rstrip('/')}/#{e['ticker']}@{e['tf']}" if app_url else None
        return title, message, click, priority
    title = f"{icon} {name}{lab} · {short(e['ticker'])}"
    if kind in ("S+", "S-"):
        s = stock["tf"].get(e["tf"], {}).get("s", {})
        n = s.get("n") if s.get("active") and s.get("bar") == e["bar"] else None
        nature = "bougies baissières" if kind == "S+" else "bougies vertes"
        what = (f"S {'haussière' if kind == 'S+' else 'baissière'} {lab} : bougie Heikin Ashi sans corps"
                + (f" après {n} {nature}" if n else f" après des {nature}"))
    else:
        part = ("Haut du corps" if kind == "Fa" else "Bas du corps") if BODY_MODE == "edge" else "Corps"
        what = f"{part} Heikin Ashi {lab} {'au-dessus' if kind == 'Fa' else 'sous'} de la MM7 et de la Tenkan"
    message = f"{stock['name']} — {fmt_px(stock['price'])} €\n{what}\n{summary_line(stock)}"
    priority = 3
    if held:
        title = f"⭐ {title}"
        message = "POSITION DÉTENUE\n" + message
        priority = 5 if kind in ("Fo", "S-") else 4
    click = f"{app_url.rstrip('/')}/#{e['ticker']}@{e['tf']}" if app_url else None
    return title, message, click, priority


def prune_state(state, keep_days=45):
    """Supprime de state.json les alertes « en cours » anciennes (clé = ticker|tf|kind~|bougie) pour qu'il reste petit."""
    limit = pd.Timestamp.now(tz=PARIS) - pd.Timedelta(days=keep_days)
    out = {}
    for k, v in state.items():
        parts = k.split("|")
        if len(parts) == 4:
            try:
                if pd.Timestamp(parts[3]) < limit:
                    continue
            except Exception:
                pass
        out[k] = v
    return out


def deliver(events, data, state, push, app_url, seeding, holdings=frozenset(), positions=None, sent_log=None):
    """Envoie les nouvelles alertes ; retourne (nouvel état, nombre envoyé)."""
    new = select_new(events, state, holdings, positions)
    state = prune_state(state)

    def mark(key, e):
        state[key] = e["level"] if e["kind"].endswith("~") else e["bar"]

    if seeding:                                  # premier passage : on mémorise sans notifier
        for key, e in new:
            mark(key, e)
        return state, 0
    sent = 0
    for key, e in new[:MAX_PUSH_PER_RUN]:
        title, message, click, priority = build_notification(e, data["stocks"][e["ticker"]], app_url, e["ticker"] in holdings)
        try:
            hm = pd.Timestamp(data["meta"]["updated"]).tz_convert(PARIS).strftime("%H:%M")
            message += f"\nAnalyse de {hm} (cours différés de {DATA_DELAY_MIN} min)"
        except Exception:
            pass
        try:
            push(title, message, click, priority)
            mark(key, e)
            sent += 1
            if sent_log is not None:
                sent_log.append(e)
            time.sleep(0.4)
        except Exception as ex:
            print(f"Échec d'envoi {title} : {ex}", file=sys.stderr)
    rest = new[MAX_PUSH_PER_RUN:]
    if rest:
        try:
            push(f"⚠️ {len(rest)} autres signaux", "Ouvrez l'application pour la liste complète.", app_url or None, 3)
            for key, e in rest:
                mark(key, e)
        except Exception as ex:
            print(f"Échec du récapitulatif : {ex}", file=sys.stderr)
    return state, sent


# ====================================== CLI ======================================

def default_app_url():
    if os.environ.get("APP_URL"):
        return os.environ["APP_URL"]
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}/"
    return ""


def parse_holdings(raw):
    """Variable HOLDINGS : positions séparées par des virgules, « AIR.PA:S:4h:171.5,MC.PA:Fa:1d » ou simplement « AIR.PA,MC.PA ».
    Le scanner n'a besoin que des tickers ; la configuration, l'unité de temps et le prix d'entrée servent à l'application."""
    out = set()
    for t in raw.replace(";", ",").replace("\n", ",").split(","):
        t = t.strip().upper().split(":")[0]
        if re.fullmatch(r"[A-Z0-9]+(\.[A-Z]+)?", t):             # ignore « - » (liste vide) et les valeurs invalides
            out.add(t if "." in t else t + ".PA")
    return frozenset(out)


def parse_positions(raw):
    """HOLDINGS -> {ticker: [{t, c, tf, e, ts}]}. c : « S », « Fa » ou « O » (Autre) ; ticker simple = « O »."""
    out = {}
    for part in raw.replace(";", ",").replace("\n", ",").split(","):
        f = part.strip().split(":")
        t = f[0].strip().upper()
        if not re.fullmatch(r"[A-Z0-9]+(\.[A-Z]+)?", t):
            continue
        t = t if "." in t else t + ".PA"
        c = f[1] if len(f) > 1 and f[1] in ("S", "Fa", "O") else "O"
        tf = f[2] if len(f) > 2 and f[2] in TF_ORDER else None
        try:
            e = float(f[3]) if len(f) > 3 and f[3] != "" else None
        except ValueError:
            e = None
        try:
            ts = int(f[4]) * 1000 if len(f) > 4 and f[4] != "" else None
        except ValueError:
            ts = None
        out.setdefault(t, []).append({"t": t, "c": c, "tf": tf, "e": e, "ts": ts})
    return out


def choose_mode(now, holdings, env=None):
    """« full » : toutes les actions (toutes les 30 min) | « held » : seulement vos positions (les 15 min intermédiaires).
    Complet aussi : lancement manuel, aucune position, passage de clôture (>= 17h50), ou SCAN_MODE=full/held."""
    env = os.environ if env is None else env
    forced = env.get("SCAN_MODE", "").strip().lower()
    if forced in ("full", "held"):
        return forced
    if env.get("GITHUB_EVENT_NAME") == "workflow_dispatch" or not holdings:
        return "full"
    if now.hour >= 17 and now.minute >= 50:
        return "full"
    return "full" if (now.minute // 15) % 2 == 0 else "held"


SECTOR_FR = {"Technology": "Technologie", "Healthcare": "Santé", "Financial Services": "Finance", "Consumer Cyclical": "Conso cyclique",
             "Consumer Defensive": "Conso défensive", "Industrials": "Industrie", "Basic Materials": "Matériaux", "Energy": "Énergie",
             "Utilities": "Services publics", "Real Estate": "Immobilier", "Communication Services": "Communication"}
SECTORS_FILE = HERE / "sectors.json"
THEMES_FILE = HERE / "themes.csv"

# Industrie Yahoo (anglais, mots-clés en minuscules) -> secteur principal. Premier mot-clé trouvé. Sert aux actions absentes de themes.csv.
INDUSTRY_RULES = [
    ("aerospace", "Aéronautique & Défense"), ("defense", "Aéronautique & Défense"),
    ("auto", "Automobile & Équipementiers"), ("recreational vehicles", "Biens de consommation & Loisirs"),
    ("banks", "Banques"), ("insurance", "Assurance, Gestion d'actifs & Marchés"), ("asset management", "Assurance, Gestion d'actifs & Marchés"),
    ("capital markets", "Assurance, Gestion d'actifs & Marchés"), ("financial", "Assurance, Gestion d'actifs & Marchés"),
    ("credit services", "Assurance, Gestion d'actifs & Marchés"), ("conglomerates", "Assurance, Gestion d'actifs & Marchés"),
    ("biotech", "Biotechnologies"), ("medical care", "Services de santé"), ("pharma", "Pharma & Santé"), ("drug", "Pharma & Santé"),
    ("medical", "Pharma & Santé"), ("diagnostics", "Pharma & Santé"), ("health", "Pharma & Santé"),
    ("solar", "Énergies renouvelables & Hydrogène"), ("renewable", "Énergies renouvelables & Hydrogène"),
    ("utilities", "Services aux collectivités"), ("waste", "Services aux collectivités"),
    ("oil", "Pétrole, Gaz & Services"), ("gas", "Pétrole, Gaz & Services"), ("coal", "Pétrole, Gaz & Services"), ("uranium", "Pétrole, Gaz & Services"),
    ("semiconductor", "Semi-conducteurs & Électronique"), ("electronic", "Semi-conducteurs & Électronique"), ("computer hardware", "Semi-conducteurs & Électronique"),
    ("scientific", "Semi-conducteurs & Électronique"),
    ("software", "Logiciels, IA & Cloud"), ("information technology services", "Services informatiques & Conseil"),
    ("consulting", "Services informatiques & Conseil"), ("data processing", "Services informatiques & Conseil"),
    ("gaming", "Jeux vidéo & Divertissement"), ("entertainment", "Jeux vidéo & Divertissement"), ("gambling", "Tourisme, Hôtels & Loisirs"),
    ("telecom", "Télécoms & Médias"), ("broadcasting", "Télécoms & Médias"), ("publishing", "Télécoms & Médias"), ("advertising", "Télécoms & Médias"),
    ("internet content", "Télécoms & Médias"),
    ("grocery", "Distribution & E-commerce"), ("retail", "Distribution & E-commerce"), ("department", "Distribution & E-commerce"),
    ("beverages", "Agroalimentaire & Boissons"), ("packaged foods", "Agroalimentaire & Boissons"), ("farm", "Agroalimentaire & Boissons"),
    ("confection", "Agroalimentaire & Boissons"), ("food", "Agroalimentaire & Boissons"), ("tobacco", "Agroalimentaire & Boissons"),
    ("luxury", "Luxe, Mode & Cosmétiques"), ("apparel", "Luxe, Mode & Cosmétiques"), ("footwear", "Luxe, Mode & Cosmétiques"),
    ("personal products", "Luxe, Mode & Cosmétiques"), ("household", "Biens de consommation & Loisirs"), ("leisure", "Biens de consommation & Loisirs"),
    ("furnishings", "Biens de consommation & Loisirs"), ("lodging", "Tourisme, Hôtels & Loisirs"), ("resorts", "Tourisme, Hôtels & Loisirs"),
    ("travel", "Tourisme, Hôtels & Loisirs"), ("restaurants", "Tourisme, Hôtels & Loisirs"),
    ("engineering", "Construction & Matériaux"), ("construction", "Construction & Matériaux"), ("building", "Construction & Matériaux"),
    ("infrastructure", "Construction & Matériaux"), ("cement", "Construction & Matériaux"),
    ("reit", "Immobilier & Foncières"), ("real estate", "Immobilier & Foncières"),
    ("electrical", "Industrie & Équipements"), ("machinery", "Industrie & Équipements"), ("industrial", "Industrie & Équipements"), ("tools", "Industrie & Équipements"),
    ("airlines", "Transport & Logistique"), ("railroads", "Transport & Logistique"), ("trucking", "Transport & Logistique"), ("marine", "Transport & Logistique"),
    ("airports", "Transport & Logistique"), ("logistics", "Transport & Logistique"), ("freight", "Transport & Logistique"),
    ("chemicals", "Chimie, Acier & Matériaux"), ("steel", "Chimie, Acier & Matériaux"), ("aluminum", "Chimie, Acier & Matériaux"),
    ("copper", "Chimie, Acier & Matériaux"), ("gold", "Chimie, Acier & Matériaux"), ("packaging", "Chimie, Acier & Matériaux"), ("paper", "Chimie, Acier & Matériaux"),
    ("staffing", "Services aux entreprises"), ("security", "Services aux entreprises"), ("business services", "Services aux entreprises"),
    ("rental", "Services aux entreprises"), ("specialty business", "Services aux entreprises"),
]
# Secteur Yahoo (anglais) -> secteur principal, quand l'industrie ne permet pas de conclure
SECTOR_FALLBACK = {"Technology": "Logiciels, IA & Cloud", "Healthcare": "Pharma & Santé", "Financial Services": "Assurance, Gestion d'actifs & Marchés",
                   "Consumer Cyclical": "Biens de consommation & Loisirs", "Consumer Defensive": "Agroalimentaire & Boissons",
                   "Industrials": "Industrie & Équipements", "Basic Materials": "Chimie, Acier & Matériaux", "Energy": "Pétrole, Gaz & Services",
                   "Utilities": "Services aux collectivités", "Real Estate": "Immobilier & Foncières", "Communication Services": "Télécoms & Médias"}
SECTOR_EN = {v: k for k, v in SECTOR_FR.items()}               # anciens caches : secteur français -> anglais


def load_themes(path=THEMES_FILE):
    """themes.csv -> {ticker: (secteur, [thèmes])}."""
    out = {}
    try:
        lines = [l for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#")]
    except OSError:
        return out
    for row in csv.reader(lines[1:] if lines and lines[0].lower().startswith("ticker") else lines):
        if len(row) >= 2 and row[0].strip():
            t = row[0].strip().upper()
            out[t if "." in t else t + ".PA"] = (row[1].strip() or "Autres", [x.strip() for x in (row[2] if len(row) > 2 else "").split("|") if x.strip()])
    return out


def sector_group(industry, sector):
    """Secteur principal d'après l'industrie Yahoo, à défaut le grand secteur Yahoo."""
    low = (industry or "").lower()
    for kw, group in INDUSTRY_RULES:
        if kw in low:
            return group
    return SECTOR_FALLBACK.get(sector or "", "Autres")


def apply_sectors(data, cache, lookup, limit=60, themes=None):
    """Ajoute secteur principal et thèmes à chaque action. themes.csv a priorité ; sinon industrie Yahoo (cache sectors.json, jusqu'à
    `limit` nouvelles recherches par passage via `lookup(ticker)` -> (secteur, industrie))."""
    themes = load_themes() if themes is None else themes
    todo = [t for t in data["stocks"] if t not in themes and not isinstance(cache.get(t), dict)]
    fails = 0
    for t in todo[:limit]:
        try:
            res = lookup(t)
            fails = 0
        except Exception:
            res, fails = None, fails + 1
            if fails >= 5:                                   # Yahoo limite les requêtes : on réessaiera au prochain passage
                break
        if res is not None:
            sec, ind = res if isinstance(res, tuple) else (res, "")
            cache[t] = {"s": sec or "", "i": ind or ""}
    for t, stock in data["stocks"].items():
        if t in themes:
            stock["sector"], stock["themes"] = themes[t][0], list(themes[t][1])
            continue
        c = cache.get(t)
        if isinstance(c, dict):
            stock["sector"], stock["themes"] = sector_group(c.get("i"), c.get("s")), []
        elif isinstance(c, str):                             # ancien format du cache : secteur français seul
            stock["sector"], stock["themes"] = sector_group("", SECTOR_EN.get(c, c)), []
        else:
            stock["sector"], stock["themes"] = "Autres", []
    return cache


def yahoo_sector(t):
    import yfinance as yf
    info = yf.Ticker(t).info or {}
    return info.get("sector") or "", info.get("industry") or ""


def merge_partial(prev, data):
    """Passage « positions seulement » : met à jour les actions détenues dans le data.json existant, le reste est conservé."""
    merged = dict(prev)
    merged["stocks"] = {**prev.get("stocks", {}), **data["stocks"]}
    meta = dict(prev.get("meta", {}))
    meta["updated"] = data["meta"]["updated"]
    meta["held_updated"] = data["meta"]["updated"]
    meta.setdefault("full_updated", prev.get("meta", {}).get("updated"))
    merged["meta"] = meta
    return merged


def strip_recent(data):
    """data.json public : sans le détail « recent » (résultats des dernières bougies, utilisé seulement par le suivi des alertes)."""
    return {"meta": data["meta"],
            "stocks": {t: {**s, "tf": {tf: {k: v for k, v in r.items() if k != "recent"} for tf, r in s["tf"].items()}}
                       for t, s in data["stocks"].items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true", help="n'envoie rien et ne touche pas à state.json")
    ap.add_argument("--test-push", action="store_true", help="envoie une notification de test puis quitte")
    ap.add_argument("--min-turnover", type=float, default=MIN_TURNOVER_EUR, help="liquidité minimale (€/jour)")
    args = ap.parse_args()

    topic = os.environ.get("NTFY_TOPIC", "").strip()
    app_url = default_app_url()
    holdings = parse_holdings(os.environ.get("HOLDINGS", ""))
    positions = parse_positions(os.environ.get("HOLDINGS", ""))

    if args.test_push:
        if not topic:
            sys.exit("NTFY_TOPIC non défini.")
        ntfy_post(topic, "🟢 Fa1h · TEST", "Si vous lisez ceci, les notifications fonctionnent.", app_url or None)
        print("Notification de test envoyée.")
        return

    now = pd.Timestamp.now(tz=PARIS)
    mode = choose_mode(now, holdings)
    prev = None
    if mode == "held":
        try:
            prev = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            if not prev.get("stocks"):
                prev = None
        except Exception:
            prev = None
        if prev is None:
            mode = "full"
    if mode == "held":
        print(f"Passage rapide : {len(holdings)} position(s) seulement.")
        data, events = scan({}, download, now, args.min_turnover, holdings, positions)
        if data["stocks"]:
            data = merge_partial(prev, data)
        else:
            sys.exit("Aucune donnée pour les positions : data.json non modifié.")
    else:
        universe = load_universe()
        if AUTO_UNIVERSE:
            listed = fetch_paris_listing()
            added = {t: n for t, n in listed.items() if t not in universe}
            universe.update(added)
            print(f"Liste Yahoo des actions de Paris : {len(listed)} valeurs, {len(added)} ajoutées à universe.csv ({len(universe)} au total).")
        data, events = scan(universe, download, now, args.min_turnover, holdings, positions)

    m = data["meta"]
    print(f"{m.get('analysed', len(data['stocks']))} actions analysées sur {m.get('universe', '?')} "
          f"({m.get('illiquid', '?')} trop peu liquides, {len(m.get('no_data', []))} sans données).")
    if m.get("no_data") and mode == "full":
        print(f"Sans données ({len(m['no_data'])}) : " + ", ".join(m["no_data"][:60]) + (" …" if len(m["no_data"]) > 60 else ""), file=sys.stderr)
    if not data["stocks"]:
        sys.exit("Aucune donnée exploitable : data.json non modifié.")

    try:
        cache = json.loads(SECTORS_FILE.read_text(encoding="utf-8")) if SECTORS_FILE.exists() else {}
    except Exception:
        cache = {}
    before = dict(cache)
    cache = apply_sectors(data, cache, yahoo_sector, limit=60 if mode == "full" else 5)
    if cache != before and not args.no_push:
        SECTORS_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")

    DATA_FILE.parent.mkdir(exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(strip_recent(data), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(DATA_FILE)

    if args.no_push:
        print(f"{len(events)} événement(s) Fa/Fo récents détectés (aucun envoi, mode --no-push).")
        return
    if not topic:
        print("NTFY_TOPIC non défini : aucune notification envoyée.", file=sys.stderr)
        return

    seeding = not STATE_FILE.exists()
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if not seeding else {}
    sent_events = []
    state, sent = deliver(events, data, state, lambda t, msg, click, prio=3: ntfy_post(topic, t, msg, click, prio), app_url, seeding, holdings, positions, sent_events)
    STATE_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    try:                                                      # suivi automatique des alertes et de leurs résultats réels (ne doit jamais bloquer le scan)
        import tracker
        st = tracker.run(events, data, sent_events, now)
        print(f"Suivi des alertes : {st['logged']} configurations notées, {st['resolved']} résolues.")
    except Exception as ex:
        print(f"Suivi des alertes : erreur {ex}", file=sys.stderr)
    print("Premier passage : état initial mémorisé, aucune notification envoyée." if seeding
          else f"{sent} notification(s) envoyée(s)." + (f" Actions détenues : {', '.join(sorted(holdings))}." if holdings else ""))


if __name__ == "__main__":
    main()
