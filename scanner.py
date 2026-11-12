#!/usr/bin/env python3
"""
Scanner Fa / Fo sur les actions françaises liquides.

S+  = « S haussière » : bougie HA sans corps (corps ≤ 10 % de sa hauteur) après ≥ 3 bougies HA baissières (🟩)
S-  = « S baissière » : bougie HA sans corps après ≥ 3 bougies HA vertes (🟥)
Actions détenues (variable HOLDINGS) : alertes étendues et notifications ⭐ en priorité haute.

Fa  = « validé »     : le CORPS de la dernière bougie Heikin Ashi CLÔTURÉE est au-dessus de la MM7 ET de la Tenkan
Fo  = « invalidé »   : le CORPS de la dernière bougie Heikin Ashi CLÔTURÉE est sous la MM7 ET la Tenkan
En cours de validation : la bougie en formation n'est pas encore clôturée ; on estime la probabilité (en %)
                         qu'elle clôture avec la condition Fa remplie (simulation Monte-Carlo, voir simulate()).

Unités de temps : 1h, 4h, 1 jour, 1 semaine, 1 mois (4h reconstruite à partir du 1h : 9h-13h puis 13h-17h35).

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
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ------------------------------- Réglages --------------------------------
MA_PERIOD = 7              # MM7
TENKAN_PERIOD = 9          # Tenkan-sen (Ichimoku) = (plus haut 9 + plus bas 9) / 2
BODY_MODE = "full"         # "full": ouverture ET clôture HA au-delà des deux lignes | "close": clôture seule
INDICATORS_ON = "ha"       # "ha": MM7/Tenkan calculées sur les bougies Heikin Ashi | "normal": sur bougies classiques
MIN_TURNOVER_EUR = 2_000_000   # liquidité : volume moyen échangé par jour (20 j) en euros
DATA_DELAY_MIN = 15        # Yahoo diffuse Euronext avec ~15 min de retard
PROB_MIN = 0.20            # seuil d'affichage de l'onglet « En cours » dans l'application
# Notifications par type de configuration : Fa, Fo, S+ (S haussière : sans corps après des bougies baissières),
# S- (S baissière : sans corps après des bougies haussières). Valeurs : unités de temps qui notifient.
NOTIFY = {"Fa": ["1d", "1w", "1mo"], "Fo": ["1d", "1w", "1mo"], "S+": ["4h", "1d", "1w", "1mo"], "S-": []}
# Actions détenues (variable GitHub HOLDINGS) : alertes étendues, notifications ⭐ en priorité haute
_ALL = ["1h", "4h", "1d", "1w", "1mo"]
NOTIFY_HELD = {"Fa": _ALL, "Fo": _ALL, "S+": _ALL, "S-": _ALL}
S_MIN_RUN = 3              # S : nombre minimal de bougies HA de même sens consécutives avant la bougie sans corps
S_BODY_MAX = 0.10          # S : « sans corps » = corps (|clôture - ouverture|) <= 10 % de la hauteur de la bougie HA
MAX_PUSH_PER_RUN = 30     # au-delà : une seule notification récapitulative (ntfy.sh limite ~250 messages/jour)
MIN_BARS = 25              # historique minimal pour calculer correctement les indicateurs
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


def body_bounds(hao, hac):
    if BODY_MODE == "full":
        return np.minimum(hao, hac), np.maximum(hao, hac)
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
    """+1 = Fa (corps au-dessus des deux lignes), -1 = Fo (corps sous les deux lignes), 0 = neutre."""
    lo, hi = body_bounds(hao, hac)
    top, bot = np.maximum(mm, tk), np.minimum(mm, tk)
    valid = ~np.isnan(top)
    st = np.zeros(len(hac), dtype=int)
    st[valid & (lo > top)] = 1
    st[valid & (hi < bot)] = -1
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
    lo_b, hi_b = body_bounds(np.full(SIM_PATHS, hao[n]), hac_s)
    fa = lo_b > np.maximum(mm, tk)
    fo = hi_b < np.minimum(mm, tk)
    hh = hah_s - hal_s
    nobody = (hh > 0) & (np.abs(hac_s - hao[n]) <= S_BODY_MAX * hh)
    p_nobody = float(nobody.mean())
    return (float(fa.mean()), float(fo.mean()),
            p_nobody if bear_run >= S_MIN_RUN else 0.0, p_nobody if bull_run >= S_MIN_RUN else 0.0)


# ================================ Analyse d'une UT ================================

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
        if nb >= S_MIN_RUN:
            return "up", nb
        if nu >= S_MIN_RUN:
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

    res = {
        "s": {"active": s_dir is not None, "dir": s_dir, "n": s_n, "bar": idx[nc - 1].isoformat()},
        "state": {1: "fa", -1: "fo", 0: "none"}[cur],
        "since": idx[j].isoformat(),
        "bars": int(nc - j),
        "forming": bool(forming),
        "p_fa": None,
        "p_fo": None,
        "p_s_up": None,
        "p_s_down": None,
        "mm7": round(float(mm[nc - 1]), 3),
        "tenkan": round(float(tk[nc - 1]), 3),
        "events": events,
    }
    if forming:
        rets = np.diff(np.log(c[:nc]))[-100:]
        sigma = float(np.std(rets)) if len(rets) > 5 else float("nan")
        if np.isfinite(sigma) and sigma > 0:
            rem = remaining_fraction(idx[-1], tf, now_eff)
            p_fa, p_fo, p_su, p_sd = simulate(o, h, l, c, hao, hah, hal, hac, sh, sl, sc, rem, sigma, rng,
                                              run(nc, bear), run(nc, bull))
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


# ===================================== Scan =====================================

def scan(universe, fetch, now, min_turnover=MIN_TURNOVER_EUR, holdings=frozenset()):
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
            tfs[tf] = res
        if not tfs:
            continue
        closes = daily[t]["Close"]
        price = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) > 1 else price
        stocks[t] = {"name": universe[t], "price": round(price, 2), "chg": round((price / prev - 1) * 100, 2), "tf": tfs}

    data = {
        "meta": {
            "updated": now.isoformat(),
            "delay_min": DATA_DELAY_MIN,
            "prob_min": PROB_MIN,
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


def select_new(events, state, holdings=frozenset()):
    """Événements plus récents que ceux déjà notifiés."""
    new = []
    for e in events:
        rules = NOTIFY_HELD if e["ticker"] in holdings else NOTIFY
        if e["tf"] not in rules.get(e["kind"], []):
            continue
        key = f"{e['ticker']}|{e['tf']}|{e['kind']}"
        last = state.get(key)
        if last is None or pd.Timestamp(e["bar"]) > pd.Timestamp(last):
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
    icon = {"Fa": "🟢", "Fo": "🔴", "S+": "🟩", "S-": "🟥"}[kind]
    name = "S" if kind.startswith("S") else kind
    title = f"{icon} {name}{lab} · {short(e['ticker'])}"
    if kind in ("S+", "S-"):
        s = stock["tf"].get(e["tf"], {}).get("s", {})
        n = s.get("n") if s.get("active") and s.get("bar") == e["bar"] else None
        nature = "bougies baissières" if kind == "S+" else "bougies vertes"
        what = (f"S {'haussière' if kind == 'S+' else 'baissière'} {lab} : bougie Heikin Ashi sans corps"
                + (f" après {n} {nature}" if n else f" après des {nature}"))
    else:
        what = f"Corps Heikin Ashi {lab} {'au-dessus' if kind == 'Fa' else 'sous'} de la MM7 et de la Tenkan"
    message = f"{stock['name']} — {stock['price']:.2f} €\n{what}\n{summary_line(stock)}"
    priority = 3
    if held:
        title = f"⭐ {title}"
        message = "POSITION DÉTENUE\n" + message
        priority = 5 if kind in ("Fo", "S-") else 4
    click = f"{app_url.rstrip('/')}/#{e['ticker']}" if app_url else None
    return title, message, click, priority


def deliver(events, data, state, push, app_url, seeding, holdings=frozenset()):
    """Envoie les nouvelles alertes ; retourne (nouvel état, nombre envoyé)."""
    new = select_new(events, state, holdings)
    state = dict(state)
    if seeding:                                  # premier passage : on mémorise sans notifier
        for key, e in new:
            state[key] = e["bar"]
        return state, 0
    sent = 0
    for key, e in new[:MAX_PUSH_PER_RUN]:
        title, message, click, priority = build_notification(e, data["stocks"][e["ticker"]], app_url, e["ticker"] in holdings)
        try:
            push(title, message, click, priority)
            state[key] = e["bar"]
            sent += 1
            time.sleep(0.4)
        except Exception as ex:
            print(f"Échec d'envoi {title} : {ex}", file=sys.stderr)
    rest = new[MAX_PUSH_PER_RUN:]
    if rest:
        try:
            push(f"⚠️ {len(rest)} autres signaux", "Ouvrez l'application pour la liste complète.", app_url or None, 3)
            for key, e in rest:
                state[key] = e["bar"]
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
    """Variable HOLDINGS : tickers séparés par des virgules (ex. « AIR.PA,MC.PA »)."""
    out = set()
    for t in raw.replace(";", ",").replace("\n", ",").split(","):
        t = t.strip().upper()
        if re.fullmatch(r"[A-Z0-9]+(\.[A-Z]+)?", t):             # ignore « - » (liste vide) et les valeurs invalides
            out.add(t if "." in t else t + ".PA")
    return frozenset(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true", help="n'envoie rien et ne touche pas à state.json")
    ap.add_argument("--test-push", action="store_true", help="envoie une notification de test puis quitte")
    ap.add_argument("--min-turnover", type=float, default=MIN_TURNOVER_EUR, help="liquidité minimale (€/jour)")
    args = ap.parse_args()

    topic = os.environ.get("NTFY_TOPIC", "").strip()
    app_url = default_app_url()
    holdings = parse_holdings(os.environ.get("HOLDINGS", ""))

    if args.test_push:
        if not topic:
            sys.exit("NTFY_TOPIC non défini.")
        ntfy_post(topic, "🟢 Fa1h · TEST", "Si vous lisez ceci, les notifications fonctionnent.", app_url or None)
        print("Notification de test envoyée.")
        return

    now = pd.Timestamp.now(tz=PARIS)
    universe = load_universe()
    data, events = scan(universe, download, now, args.min_turnover, holdings)

    m = data["meta"]
    print(f"{m['analysed']} actions analysées sur {m['universe']} "
          f"({m['illiquid']} trop peu liquides, {len(m['no_data'])} sans données).")
    if m["no_data"]:
        print("Sans données (ticker à corriger dans universe.csv ?) : " + ", ".join(m["no_data"]), file=sys.stderr)
    if not data["stocks"]:
        sys.exit("Aucune donnée exploitable : data.json non modifié.")

    DATA_FILE.parent.mkdir(exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(DATA_FILE)

    if args.no_push:
        print(f"{len(events)} événement(s) Fa/Fo récents détectés (aucun envoi, mode --no-push).")
        return
    if not topic:
        print("NTFY_TOPIC non défini : aucune notification envoyée.", file=sys.stderr)
        return

    seeding = not STATE_FILE.exists()
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if not seeding else {}
    state, sent = deliver(events, data, state, lambda t, msg, click, prio=3: ntfy_post(topic, t, msg, click, prio), app_url, seeding, holdings)
    STATE_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print("Premier passage : état initial mémorisé, aucune notification envoyée." if seeding
          else f"{sent} notification(s) envoyée(s)." + (f" Actions détenues : {', '.join(sorted(holdings))}." if holdings else ""))


if __name__ == "__main__":
    main()
