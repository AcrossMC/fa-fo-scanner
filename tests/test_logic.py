"""
Tests hors ligne (données synthétiques) : python tests/test_logic.py
Vérifie les calculs, le calendrier, les états, les probabilités, les alertes et le fichier data.json.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scanner as sc  # noqa: E402

P = sc.PARIS


# ----------------------------- générateurs synthétiques -----------------------------
def walk(n, seed, drift=0.0, vol=0.01, start=100.0):
    rng = np.random.default_rng(seed)
    return start * np.exp(np.cumsum(rng.normal(drift, vol, n)))


def ohlc_from_close(close, seed):
    rng = np.random.default_rng(seed + 999)
    o = np.concatenate([[close[0]], close[:-1]])
    spread = np.abs(rng.normal(0, 0.004, len(close))) * close
    h = np.maximum(o, close) + spread
    l = np.minimum(o, close) - spread
    return o, h, l, close


def make_daily(now, seed, drift=0.0, vol=0.012, volume=1e6, n=1250):
    days = pd.bdate_range(end=now.tz_localize(None).normalize(), periods=n)
    idx = days.tz_localize(P)
    o, h, l, c = ohlc_from_close(walk(n, seed, drift, vol), seed)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": np.full(n, volume)}, index=idx)


def make_hourly(now_eff, seed, drift=0.0, vol=0.004, volume=1e5):
    days = pd.bdate_range(end=now_eff.tz_localize(None).normalize(), periods=62)
    stamps = [d.tz_localize(P) + pd.Timedelta(hours=h) for d in days for h in range(9, 18)]
    stamps = [t for t in stamps if t <= now_eff]
    n = len(stamps)
    o, h, l, c = ohlc_from_close(walk(n, seed, drift, vol), seed)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": np.full(n, volume)}, index=pd.DatetimeIndex(stamps))


def fake_fetch_factory(now, specs):
    """specs: {ticker: (seed, drift, volume)}"""
    now_eff = now - pd.Timedelta(minutes=sc.DATA_DELAY_MIN)

    def fetch(tickers, period, interval):
        out = {}
        for t in tickers:
            if t not in specs:
                continue
            seed, drift, vol = specs[t]
            out[t] = make_daily(now, seed, drift, volume=vol) if interval == "1d" else make_hourly(now_eff, seed, drift, volume=vol / 10)
        return out
    return fetch


# ------------------------------------- tests -------------------------------------
def test_heikin_ashi():
    o = np.array([10., 11, 12, 11]); h = np.array([12., 13, 14, 12]); l = np.array([9., 10, 11, 9]); c = np.array([11., 12, 13, 10])
    hao, hah, hal, hac = sc.heikin_ashi(o, h, l, c)
    assert np.isclose(hac[1], (11 + 13 + 10 + 12) / 4)
    assert np.isclose(hao[0], 10.5) and np.isclose(hao[1], (hao[0] + hac[0]) / 2)
    assert (hah >= np.maximum(hao, hac)).all() and (hal <= np.minimum(hao, hac)).all()
    print("OK heikin_ashi")


def test_body_modes():
    mm, tk = np.array([11.0] * 4), np.array([11.5] * 4)
    hao = np.array([10.0, 11.8, 9.0, 11.2]); hac = np.array([12.0, 12.5, 10.5, 11.4])   # corps : 10-12 | 11.8-12.5 | 9-10.5 | 11.2-11.4
    old = sc.BODY_MODE
    sc.BODY_MODE = "edge"
    assert sc.classify(hao, hac, mm, tk).tolist() == [0, 1, -1, 0]     # 1 : couvre toute la zone => aucun signal
    sc.BODY_MODE = "full"
    assert sc.classify(hao, hac, mm, tk).tolist() == [0, 1, -1, 0]
    hao2 = np.array([11.2, 10.8]); hac2 = np.array([12.0, 11.3])
    sc.BODY_MODE = "edge"
    assert sc.classify(hao2, hac2, mm[:2], tk[:2]).tolist() == [1, -1]  # haut 12 > 11.5 => Fa ; bas 10.8 < 11 => Fo
    sc.BODY_MODE = "full"
    assert sc.classify(hao2, hac2, mm[:2], tk[:2]).tolist() == [0, 0]   # mode strict : le corps entier doit être au-delà
    sc.BODY_MODE = old
    print("OK body_modes")


def test_persistence():
    st = sc.persist(np.array([0, 1, 0, 0, -1, 0, 1, 0]))
    assert st.tolist() == [0, 1, 1, 1, -1, -1, 1, 1]               # Fa dure jusqu'au Fo, Fo jusqu'au Fa suivant
    print("OK persistence")


def test_lines_and_classify():
    n = 40
    c = np.linspace(100, 140, n); o = c - 1; h = c + 0.5; l = o - 0.5
    hao, hah, hal, hac = sc.heikin_ashi(o, h, l, c)
    mm, tk = sc.lines(hah, hal, hac)
    assert np.isclose(mm[-1], hac[-7:].mean())
    assert np.isclose(tk[-1], (hah[-9:].max() + hal[-9:].min()) / 2)
    st = sc.classify(hao, hac, mm, tk)
    assert st[-1] == 1 and st[:8].tolist() == [0] * 8                  # tendance haussière => Fa, pas de signal sans historique
    st_dn = sc.classify(*(lambda a: (a[0], a[3], *sc.lines(a[1], a[2], a[3])))(sc.heikin_ashi(*(x[::-1].copy() for x in (o, h, l, c)))))
    assert st_dn[-1] == -1                                              # tendance baissière => Fo
    print("OK lines_and_classify")


def ts(s):
    return pd.Timestamp(s, tz=P)


def test_calendar():
    assert sc.bar_end(ts("2026-11-11 10:00"), "1h") == ts("2026-11-11 11:00")
    assert sc.bar_end(ts("2026-11-11 17:00"), "1h") == ts("2026-11-11 17:35")
    assert sc.bar_end(ts("2026-11-11 09:00"), "4h") == ts("2026-11-11 13:00")
    assert sc.bar_end(ts("2026-11-11 13:00"), "4h") == ts("2026-11-11 17:35")
    assert sc.bar_end(ts("2026-11-11 00:00"), "1d") == ts("2026-11-11 17:35")
    assert sc.bar_end(ts("2026-11-09 00:00"), "1w") == ts("2026-11-13 17:35")
    assert sc.bar_end(ts("2026-11-01 00:00"), "1mo") == ts("2026-11-30 17:35")
    assert sc.bar_end(ts("2026-10-01 00:00"), "1mo") == ts("2026-10-30 17:35") or sc.bar_end(ts("2026-10-01 00:00"), "1mo") == ts("2026-10-30 17:35")
    # fraction restante
    assert np.isclose(sc.remaining_fraction(ts("2026-11-11 10:00"), "1h", ts("2026-11-11 10:30")), 0.5)
    assert np.isclose(sc.remaining_fraction(ts("2026-11-11 00:00"), "1d", ts("2026-11-11 09:00")), 1.0)
    assert sc.remaining_fraction(ts("2026-11-11 00:00"), "1d", ts("2026-11-11 17:35")) == 0.0
    w = sc.remaining_fraction(ts("2026-11-09 00:00"), "1w", ts("2026-11-11 13:00"))     # mercredi 13h : un peu plus de la moitié écoulée
    assert 0.40 < w < 0.55, w
    m = sc.remaining_fraction(ts("2026-11-01 00:00"), "1mo", ts("2026-11-11 13:00"))
    assert 0.5 < m < 0.7, m
    print("OK calendar")


def test_aggregate():
    now = ts("2026-11-11 15:30")
    h = make_hourly(now, 1)
    a = sc.aggregate(h, "4h")
    assert set(a.index.hour) <= {9, 13}
    day = h[h.index.normalize() == ts("2026-11-10 00:00")]
    b = a.loc[ts("2026-11-10 13:00")]
    assert np.isclose(b["High"], day[day.index.hour >= 13]["High"].max())          # la bougie de 17h appartient bien au bloc 13h
    assert np.isclose(b["Volume"], day[day.index.hour >= 13]["Volume"].sum())
    d = make_daily(ts("2026-11-11 12:00"), 2)
    w = sc.aggregate(d, "1w"); m = sc.aggregate(d, "1mo")
    assert (w.index.weekday == 0).all() and (m.index.day == 1).all()
    print("OK aggregate")


def test_forming_and_closed():
    # Série haussière clôturée puis bougie en formation : on vérifie closed vs forming et les probabilités
    now = ts("2026-11-11 19:00")                       # marché fermé
    d = make_daily(now, 3, drift=0.004, vol=0.004)
    rng = np.random.default_rng(0)
    res = sc.analyze_tf(d, "1d", now - pd.Timedelta(minutes=15), rng)
    assert res["forming"] is False and res["p_fa"] is None
    res_mid = sc.analyze_tf(d, "1d", ts("2026-11-11 12:00"), rng)       # même série, mais la dernière bougie est « en formation »
    assert res_mid["forming"] is True and 0.0 <= res_mid["p_fa"] <= 1.0

    # Forte hausse : probabilité de Fa élevée ; forte baisse : probabilité de Fo élevée
    up = make_daily(ts("2026-11-11 12:00"), 4, drift=0.006, vol=0.002)
    dn = make_daily(ts("2026-11-11 12:00"), 5, drift=-0.006, vol=0.002)
    r_up = sc.analyze_tf(up, "1d", ts("2026-11-11 12:00"), rng)
    r_dn = sc.analyze_tf(dn, "1d", ts("2026-11-11 12:00"), rng)
    assert r_up["state"] == "fa" and r_up["p_fa"] > 0.8, r_up
    assert r_dn["state"] == "fo" and r_dn["p_fo"] > 0.8 and r_dn["p_fa"] < 0.05, r_dn
    # fin de bougie : plus rien à simuler => résultat quasi déterministe
    r_end = sc.analyze_tf(up, "1d", ts("2026-11-11 17:34"), rng)
    assert r_end["p_fa"] in (0.0, 1.0)
    for r in (r_up, r_dn, r_end, res_mid):
        assert all(0.0 <= r[k] <= 1.0 for k in ("p_s_up", "p_s_down"))
    assert r_up["p_s_up"] == 0.0                                  # pas de bougies baissières avant : S haussière impossible
    assert r_dn["p_s_down"] == 0.0                                # pas de bougies vertes avant : S baissière impossible
    print("OK forming_and_closed  (hausse p_fa=%.2f | baisse p_fo=%.2f)" % (r_up["p_fa"], r_dn["p_fo"]))


def test_event_detection():
    """Retournement synthétique : baisse puis hausse => un événement Fa doit apparaître exactement à la bougie du passage."""
    n = 120
    c = np.concatenate([np.linspace(130, 90, 60), np.linspace(90, 140, 60)])
    days = pd.bdate_range(end="2026-11-10", periods=n).tz_localize(P)
    o, h, l, c = ohlc_from_close(c, 7)
    df = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1e6}, index=days)
    rng = np.random.default_rng(0)
    # on remonte dans le temps jusqu'à trouver la bougie où l'événement est le plus récent
    found = None
    for cut in range(len(df), 40, -1):
        sub = df.iloc[:cut]
        r = sc.analyze_tf(sub, "1d", sub.index[-1] + pd.Timedelta(days=1), rng)
        if r and any(k == "Fa" for k, _ in r["events"]):
            found = (cut, r)
            break
    assert found, "aucun événement Fa détecté"
    cut, r = found
    kinds = [k for k, _ in r["events"]]
    assert "Fa" in kinds and r["state"] == "fa"
    # la bougie signalée est bien celle où l'état passe de non-Fa à Fa
    hao, hah, hal, hac = sc.heikin_ashi(*(df.iloc[:cut][k].to_numpy(float) for k in ("Open", "High", "Low", "Close")))
    mm, tk = sc.lines(hah, hal, hac); st = sc.classify(hao, hac, mm, tk)
    k_bar = [t for kd, t in r["events"] if kd == "Fa"][-1]
    k = list(df.iloc[:cut].index.map(lambda x: x.isoformat())).index(k_bar)
    assert st[k] == 1 and st[k - 1] != 1
    print("OK event_detection")


def test_notify_filter():
    ev = lambda kind: [{"ticker": "AIR.PA", "tf": tf, "kind": kind, "bar": "2026-11-11T10:00:00+01:00"} for tf in sc.TF_ORDER]
    tfs = lambda kind, held=frozenset(): sorted(e["tf"] for _, e in sc.select_new(ev(kind), {}, held))
    # actions non détenues : plus aucune notification de VALIDATION (seulement « en cours », voir test_pending)
    assert tfs("Fa") == tfs("Fo") == tfs("S+") == tfs("S-") == []
    # actions détenues : Fa, Fo, S+ et S- sur toutes les unités de temps
    h = frozenset({"AIR.PA"})
    assert tfs("Fa", h) == tfs("Fo", h) == tfs("S+", h) == tfs("S-", h) == sorted(sc.TF_ORDER)
    # les positions passent avant les autres actions
    mix = [{"ticker": "MC.PA", "tf": "1mo", "kind": "Fa", "bar": "2026-11-11T10:00:00+01:00"},
           {"ticker": "AIR.PA", "tf": "1d", "kind": "Fa", "bar": "2026-11-11T10:00:00+01:00"}]
    assert [e["ticker"] for _, e in sc.select_new(mix, {}, h)] == ["AIR.PA"]           # MC.PA non détenue : plus de notification de validation
    mix_p = [{"ticker": "MC.PA", "tf": "1mo", "kind": "Fa~", "bar": "b", "p": 0.5}, {"ticker": "AIR.PA", "tf": "1d", "kind": "Fa~", "bar": "b", "p": 0.5}]
    assert [e["ticker"] for _, e in sc.select_new(mix_p, {}, h)] == ["AIR.PA", "MC.PA"]   # les positions passent avant
    assert sc.parse_holdings("air.pa, MC;OR.PA\n") == frozenset({"AIR.PA", "MC.PA", "OR.PA"}) and sc.parse_holdings("") == frozenset()
    print("OK notify_filter")


def crafted(trend, n_trend=30, doji=True):
    """Série journalière : tendance puis une bougie HA dont ouverture = clôture (sans corps)."""
    c = np.linspace(130, 90, n_trend) if trend == "down" else np.linspace(90, 130, n_trend)
    days = pd.bdate_range(end="2026-11-10", periods=n_trend + 1).tz_localize(P)
    o, h, l, c = ohlc_from_close(c, 11)
    hao, hah, hal, hac = sc.heikin_ashi(o, h, l, c)
    ho = (hao[-1] + hac[-1]) / 2                                       # ouverture HA de la bougie ajoutée
    d = 0.8
    o2, h2, l2, c2 = (ho, ho + d, ho - d, ho) if doji else (ho, ho + 0.5, ho - 3.5, ho - 3.0)
    df = pd.DataFrame({"Open": np.append(o, o2), "High": np.append(h, h2), "Low": np.append(l, l2),
                       "Close": np.append(c, c2), "Volume": 1e6}, index=days)
    return df


def test_s_config():
    rng = np.random.default_rng(0)
    after = ts("2026-11-11 19:00")
    # S haussière (🟩) : sans corps après des bougies baissières
    r = sc.analyze_tf(crafted("down"), "1d", after, rng)
    assert r["s"]["active"] and r["s"]["dir"] == "up" and r["s"]["n"] >= 3, r["s"]
    assert any(k == "S+" for k, _ in r["events"]) and not any(k == "S-" for k, _ in r["events"])
    # S baissière (🟥) : sans corps après des bougies vertes
    r2 = sc.analyze_tf(crafted("up"), "1d", after, rng)
    assert r2["s"]["active"] and r2["s"]["dir"] == "down" and r2["s"]["n"] >= 3, r2["s"]
    assert any(k == "S-" for k, _ in r2["events"]) and not any(k == "S+" for k, _ in r2["events"])
    for tr in ("down", "up"):                                                               # bougie avec un vrai corps : pas de S
        assert not sc.analyze_tf(crafted(tr, doji=False), "1d", after, rng)["s"]["active"]
    old = sc.S_MIN_RUN
    sc.S_MIN_RUN = 99
    assert not sc.analyze_tf(crafted("down"), "1d", after, rng)["s"]["active"]             # seuil respecté
    sc.S_MIN_RUN = old
    # règle des mèches : les 3 bougies juste avant la bougie sans corps doivent avoir peu de mèche haute (S+) / basse (S-)
    def wick_df(direction, bad_back=None, n=30):
        """Bougies déjà « HA » (heikin_ashi neutralisé) : n bougies de même sens puis une bougie sans corps.
        bad_back = k : la k-ième bougie avant la bougie sans corps reçoit une grande mèche du côté testé."""
        o, h, l, c = [], [], [], []
        for i in range(n):
            if direction == "down":
                op, cl = 200 - i, 199 - i
                hi, lo = op, cl - 0.05
            else:
                op, cl = 100 + i, 101 + i
                hi, lo = cl + 0.05, op
            if bad_back is not None and i == n - bad_back:
                if direction == "down": hi = op + 0.6
                else: lo = op - 0.6
            o.append(op); h.append(hi); l.append(lo); c.append(cl)
        ref = c[-1]
        o.append(ref); c.append(ref); h.append(ref + 0.5); l.append(ref - 0.5)
        days = pd.bdate_range(end="2026-11-10", periods=n + 1).tz_localize(P)
        return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1e6}, index=days)

    real_ha = sc.heikin_ashi
    sc.heikin_ashi = lambda o, h, l, c: (o, h, l, c)
    try:
        for direction, sd in (("down", "up"), ("up", "down")):
            ok = sc.analyze_tf(wick_df(direction), "1d", after, rng)["s"]
            assert ok["active"] and ok["dir"] == sd, (direction, ok)
            for back in (1, 2, 3):                                              # mèche sur l'une des 3 dernières : pas de S
                assert not sc.analyze_tf(wick_df(direction, bad_back=back), "1d", after, rng)["s"]["active"], (direction, back)
            assert sc.analyze_tf(wick_df(direction, bad_back=6), "1d", after, rng)["s"]["active"]   # bougie plus ancienne : libre
        old_w = sc.S_WICK_MAX
        sc.S_WICK_MAX = 1.0                                                      # tolérance maximale : plus de contrainte
        assert sc.analyze_tf(wick_df("down", bad_back=1), "1d", after, rng)["s"]["active"]
        sc.S_WICK_MAX = old_w
    finally:
        sc.heikin_ashi = real_ha
    # notifications S : carrés vert / rouge
    st_up = {"name": "Airbus", "price": 171.5, "chg": 1.2, "tf": {"4h": {"state": "fo", "s": {"active": True, "dir": "up", "n": 5, "bar": "b"}}}}
    t, m, _, prio = sc.build_notification({"ticker": "AIR.PA", "tf": "4h", "kind": "S+", "bar": "b"}, st_up, "")
    assert t == "🟩 S4h · AIR" and "S haussière 4h" in m and "après 5 bougies baissières" in m and prio == 3, (t, m)
    st_dn = {"name": "Airbus", "price": 171.5, "chg": 1.2, "tf": {"1h": {"state": "fa", "s": {"active": True, "dir": "down", "n": 4, "bar": "b"}}}}
    t, m, _, prio = sc.build_notification({"ticker": "AIR.PA", "tf": "1h", "kind": "S-", "bar": "b"}, st_dn, "", held=True)
    assert t == "⭐ 🟥 S1h · AIR" and m.startswith("POSITION DÉTENUE") and "après 4 bougies vertes" in m and prio == 5, (t, m)
    assert "🟥S" in m                                                                         # le résumé affiche le carré rouge
    print("OK s_config")


def test_notifications():
    legacy = sc.NOTIFY
    sc.NOTIFY = {"Fa": ["1d", "1w", "1mo"], "Fo": ["1d", "1w", "1mo"], "S+": ["4h", "1d", "1w", "1mo"], "S-": []}   # mécanique des alertes de validation
    try:
        _notifications_body()
    finally:
        sc.NOTIFY = legacy
    print("OK notifications")


def _notifications_body():
    events = [
        {"ticker": "AIR.PA", "tf": "1d", "kind": "Fa", "bar": "2026-11-11T00:00:00+01:00"},
        {"ticker": "AIR.PA", "tf": "1d", "kind": "Fo", "bar": "2026-11-10T00:00:00+01:00"},
    ]
    data = {"stocks": {"AIR.PA": {"name": "Airbus", "price": 171.5, "chg": 1.2, "tf": {
        "1h": {"state": "fa"}, "4h": {"state": "none"}, "1d": {"state": "fo"}, "1w": {"state": "fa"}, "1mo": {"state": "none"}}}}}
    sent = []
    push = lambda t, m, c, priority=3: sent.append((t, m, c, priority))
    url = "https://x.github.io/r/"
    # premier passage : on mémorise sans notifier
    state, n = sc.deliver(events, data, {}, push, url, seeding=True)
    assert n == 0 and not sent and len(state) == 2
    # passage suivant avec les mêmes événements : rien à envoyer
    state2, n2 = sc.deliver(events, data, state, push, url, seeding=False)
    assert n2 == 0
    # nouvel événement : une alerte « 🟢 Fa1j · AIR », puis plus rien au passage suivant
    ev2 = events + [{"ticker": "AIR.PA", "tf": "1d", "kind": "Fa", "bar": "2026-11-12T00:00:00+01:00"}]
    state3, n3 = sc.deliver(ev2, data, state2, push, url, seeding=False)
    assert n3 == 1 and sent[0][0] == "🟢 Fa1j · AIR" and sent[0][3] == 3, sent
    assert sent[0][2] == "https://x.github.io/r/#AIR.PA@1d"
    assert "1h ✅" in sent[0][1] and "1j 🔻" in sent[0][1] and "Airbus" in sent[0][1] and "POSITION" not in sent[0][1]
    _, n4 = sc.deliver(ev2, data, state3, push, url, seeding=False)
    assert n4 == 0
    # Fo : rouge
    t, _, _, _ = sc.build_notification({"ticker": "AIR.PA", "tf": "1d", "kind": "Fo", "bar": "x"}, data["stocks"]["AIR.PA"], "")
    assert t == "🔴 Fo1j · AIR"
    # action détenue : notifications ⭐, priorité haute (5 pour Fo), et Fo 1h qui ne notifiait pas avant
    sent.clear()
    ev_h = [{"ticker": "AIR.PA", "tf": "1h", "kind": "Fo", "bar": "2026-11-12T10:00:00+01:00"},
            {"ticker": "AIR.PA", "tf": "1h", "kind": "Fa", "bar": "2026-11-12T09:00:00+01:00"}]
    _, n_h = sc.deliver(ev_h, data, {}, push, url, seeding=False, holdings=frozenset({"AIR.PA"}))
    assert n_h == 2 and all(t.startswith("⭐") for t, *_ in sent)
    assert {t: p for t, _, _, p in sent} == {"⭐ 🔴 Fo1h · AIR": 5, "⭐ 🟢 Fa1h · AIR": 4}, sent
    _, n_nh = sc.deliver(ev_h, data, {}, push, url, seeding=False)                       # non détenue : rien en 1h
    assert n_nh == 0
    # plafond d'envois par passage
    many = [{"ticker": f"T{i}.PA", "tf": "1d", "kind": "Fa", "bar": "2026-11-11T00:00:00+01:00"} for i in range(sc.MAX_PUSH_PER_RUN + 7)]
    data_many = {"stocks": {e["ticker"]: data["stocks"]["AIR.PA"] for e in many}}
    sent.clear()
    _, n5 = sc.deliver(many, data_many, {}, push, "https://x/", seeding=False)
    assert n5 == sc.MAX_PUSH_PER_RUN and len(sent) == sc.MAX_PUSH_PER_RUN + 1 and "7 autres" in sent[-1][0]


def test_pending():
    bar = "2026-11-12T10:00:00+01:00"
    mk = lambda tf, kind, p, t="AIR.PA": {"ticker": t, "tf": tf, "kind": kind, "bar": bar, "p": p, "over": False}
    h = frozenset({"AIR.PA"})
    sel = lambda evs, state=None, held=frozenset(): [(k, e) for k, e in sc.select_new(evs, state or {}, held)]
    # non détenue : Fa~ et S+~ en 4h/1j/1sem/1mois seulement ; jamais le 1h, jamais Fo~ ni S-~
    evs = [mk(tf, k, 0.4) for tf in sc.TF_ORDER for k in ("Fa~", "Fo~", "S+~", "S-~")]
    got = sorted((e["tf"], e["kind"]) for _, e in sel(evs))
    assert got == sorted((tf, k) for tf in ("4h", "1d", "1w", "1mo") for k in ("Fa~", "S+~")), got
    # détenue : tout, 1h compris
    assert len(sel(evs, held=h)) == 5 * 4
    # seuils : 30 % (autres unités), 50 % pour le 1h des actions détenues
    assert sc.threshold("4h", False) == 0.30 and sc.threshold("1h", True) == 0.50 and sc.threshold("1d", True) == 0.30
    # une alerte au seuil, une 2e au-dessus de 60 %, puis plus rien pour la même bougie
    sent = []
    push = lambda t, m, c, priority=3: sent.append((t, m, c, priority))
    data = {"stocks": {"AIR.PA": {"name": "Airbus", "price": 171.5, "chg": 1.2, "tf": {"4h": {"state": "none"}, "1d": {"state": "fo"}}}}}
    st, n = sc.deliver([mk("1d", "Fa~", 0.42)], data, {}, push, "https://x/", False)
    assert n == 1 and sent[0][0] == "🟢 Fa1j 42% · AIR" and sent[0][3] == 3 and "EN COURS" in sent[0][1], sent
    st, n = sc.deliver([mk("1d", "Fa~", 0.47)], data, st, push, "https://x/", False)
    assert n == 0                                                       # même niveau : pas de répétition
    st, n = sc.deliver([mk("1d", "Fa~", 0.65)], data, st, push, "https://x/", False)
    assert n == 1 and sent[1][0] == "🟢 Fa1j 65% · AIR" and "supérieure à 60" in sent[1][1] and sent[1][3] == 4
    st, n = sc.deliver([mk("1d", "Fa~", 0.35)], data, st, push, "https://x/", False)
    assert n == 0                                                       # la probabilité redescend : rien
    # nouvelle bougie : nouvelle alerte possible
    e2 = mk("1d", "Fa~", 0.4); e2["bar"] = "2026-11-13T10:00:00+01:00"
    st, n = sc.deliver([e2], data, st, push, "https://x/", False)
    assert n == 1
    # S haussière en cours et alertes de danger détenues
    sent.clear()
    st, n = sc.deliver([mk("4h", "S+~", 0.35, "AIR.PA")], data, {}, push, "https://x/", False)
    assert sent[0][0] == "🟩 S4h 35% · AIR"
    sent.clear()
    sc.deliver([mk("1d", "Fo~", 0.55), mk("4h", "S-~", 0.5)], data, {}, push, "https://x/", False, holdings=h)
    assert {t: p for t, _, _, p in sent} == {"⭐ 🔴 Fo1j 55% · AIR": 5, "⭐ 🟥 S4h 50% · AIR": 5}, sent
    assert all(m.startswith("POSITION DÉTENUE") for _, m, _, _ in sent)
    # premier passage : mémorisé sans notifier
    sent.clear()
    st, n = sc.deliver([mk("1d", "Fa~", 0.5)], data, {}, push, "https://x/", True)
    assert n == 0 and not sent and st
    # state.json : les vieilles alertes « en cours » sont purgées
    old = {"AIR.PA|1d|Fa~|2020-01-01T10:00:00+01:00": 1, "AIR.PA|1d|Fa": "2026-11-11T00:00:00+01:00"}
    assert list(sc.prune_state(old)) == ["AIR.PA|1d|Fa"]
    print("OK pending")


def test_holdings_always_scanned():
    now = ts("2026-11-11 10:40")
    specs = {"AAA.PA": (1, 0.0, 5e6), "ILLIQ.PA": (2, 0.0, 10.0), "HORS.PA": (3, 0.0, 5e6)}
    fetch = fake_fetch_factory(now, specs)
    base, _ = sc.scan({"AAA.PA": "Alpha"}, fetch, now)
    assert set(base["stocks"]) == {"AAA.PA"}
    held, _ = sc.scan({"AAA.PA": "Alpha", "ILLIQ.PA": "Illiquide"}, fetch, now, holdings=frozenset({"ILLIQ.PA", "HORS.PA"}))
    assert set(held["stocks"]) == {"AAA.PA", "ILLIQ.PA", "HORS.PA"}                  # illiquide ou hors liste : analysée quand même
    assert held["stocks"]["HORS.PA"]["name"] == "HORS"
    print("OK holdings_always_scanned")


def test_end_to_end(tmp: Path):
    specs = {}
    for i in range(36):
        drift = [0.0015, -0.0015, 0.0, 0.0008, -0.0008, 0.003][i % 6]
        specs[f"T{i:02d}.PA"] = (100 + i, drift, 5e6)
    specs["ILLIQ.PA"] = (999, 0.0, 10.0)                                   # trop peu liquide
    uni = {t: f"Société {t[:-3]}" for t in list(specs) + ["ABSENT.PA"]}     # ABSENT : pas de données

    for label, now in (("séance 10h40", ts("2026-11-11 10:40")), ("après clôture 18h05", ts("2026-11-11 18:05")), ("samedi", ts("2026-11-14 11:00"))):
        data, events = sc.scan(uni, fake_fetch_factory(now, specs), now)
        m = data["meta"]
        assert m["analysed"] == 36 and m["illiquid"] == 1 and m["no_data"] == ["ABSENT.PA"], m
        json.dumps(data)                                                    # sérialisable
        counts = {tf: {"fa": 0, "fo": 0, "pending": 0, "forming": 0} for tf in sc.TF_ORDER}
        for s in data["stocks"].values():
            assert set(s["tf"]) <= set(sc.TF_ORDER) and "1d" in s["tf"]
            assert all("active" in r["s"] and r["s"]["dir"] in (None, "up", "down") for r in s["tf"].values())
            for tf, r in s["tf"].items():
                assert r["state"] in ("fa", "fo", "none")
                if r["forming"]:
                    counts[tf]["forming"] += 1
                    assert r["p_fa"] is None or 0 <= r["p_fa"] <= 1
                else:
                    assert r["p_fa"] is None
                counts[tf]["fa"] += r["state"] == "fa"; counts[tf]["fo"] += r["state"] == "fo"
                counts[tf]["pending"] += bool(r["forming"] and r["state"] != "fa" and (r["p_fa"] or 0) >= sc.PROB_MIN)
        pend_ev = [e for e in events if e["kind"].endswith("~")]
        assert all(e["p"] >= sc.PROB_MIN for e in pend_ev) and all(e["kind"] in ("Fa~", "Fo~", "S+~", "S-~") for e in pend_ev)
        assert not any(e["kind"] == "Fa~" and data["stocks"][e["ticker"]]["tf"][e["tf"]]["state"] == "fa" for e in pend_ev)
        print(f"  [{label}] événements={len(events)} dont en cours={len(pend_ev)}")
        for tf in sc.TF_ORDER:
            print(f"     {tf:>4}: {counts[tf]}")
        if label.startswith("séance"):
            assert counts["1h"]["forming"] == 36 and counts["1d"]["forming"] == 36 and counts["4h"]["forming"] == 36
            demo = data
        if label.startswith("après"):
            assert counts["1h"]["forming"] == 0 and counts["4h"]["forming"] == 0 and counts["1d"]["forming"] == 0
            assert counts["1w"]["forming"] == 36 and counts["1mo"]["forming"] == 36          # semaine et mois toujours en cours
        if label == "samedi":
            assert counts["1w"]["forming"] == 0                                              # la semaine est clôturée
    (tmp / "data.json").write_text(json.dumps(demo, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("OK end_to_end")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp")
    out.mkdir(parents=True, exist_ok=True)
    test_heikin_ashi()
    test_body_modes()
    test_persistence()
    test_lines_and_classify()
    test_calendar()
    test_aggregate()
    test_forming_and_closed()
    test_event_detection()
    test_notify_filter()
    test_s_config()
    test_notifications()
    test_pending()
    test_holdings_always_scanned()
    test_end_to_end(out)
    print("\nTOUS LES TESTS PASSENT")
