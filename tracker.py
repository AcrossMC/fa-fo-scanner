"""
Suivi automatique des alertes : le scanner note chaque configuration « en cours » (Fa, Fo, S haussière, S baissière) dès qu'elle atteint
un seuil de suivi, puis, une fois la bougie clôturée, enregistre si elle s'est réellement produite. Au bout de quelques semaines,
`docs/stats.json` (affiché dans l'application, réglages ⚙ > Bilan des alertes) compare le pourcentage annoncé et le résultat réel,
et suggère le seuil d'alerte à retenir. Rien à faire à la main.

Les stops loss ne sont pas enregistrés ici : ils n'existent que pour vos positions, et ce journal est public dans le dépôt.
"""
import json
from pathlib import Path

import pandas as pd

import scanner as sc

LOG_FILE = Path("alerts_log.json")
STATS_FILE = Path("docs/stats.json")
KINDS = ("Fa", "S+", "Fo", "S-")
KIND_LABEL = {"Fa": "Fa", "S+": "S haussière", "Fo": "Fo", "S-": "S baissière"}
EDGES = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01]
TARGET = 0.60                  # fiabilité visée pour proposer un seuil
MIN_CASES = 20                 # en dessous : trop peu de cas pour conclure
KEEP_DAYS = 120
LOST_AFTER = pd.Timedelta(days=3)


def entry_key(t, tf, kind, bar):
    return f"{t}|{tf}|{kind}|{bar}"


def load_log(path=LOG_FILE):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_log(log, path=LOG_FILE):
    Path(path).write_text(json.dumps(log, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def update_log(log, events, sent, now):
    """Ajoute / met à jour les configurations en cours vues ce passage ; `sent` : événements réellement notifiés."""
    day = now.strftime("%Y-%m-%d")
    for e in events:
        if not e["kind"].endswith("~"):
            continue
        kind = e["kind"][:-1]
        if kind not in KINDS or e["tf"] not in sc.VISIBLE_TF or e.get("p") is None or e["p"] < sc.OBS_MIN[kind]:
            continue
        k = entry_key(e["ticker"], e["tf"], kind, e["bar"])
        en = log.get(k)
        if en is None:
            en = log[k] = {"p0": e["p"], "pm": e["p"], "d": day}
        if e.get("h4"):
            en["h4"] = 1                                   # S hebdo sur une action à plus de 30 % sous son plus haut 52 sem. (suivi réel du filtre étudié)
        en["pm"] = max(en["pm"], e["p"])
        if "pa" not in en and e["p"] >= sc.PROB_HIGH:
            en["pa"] = e["p"]
    for e in sent:
        if e["kind"].endswith("~") and e["kind"][:-1] in KINDS:
            k = entry_key(e["ticker"], e["tf"], e["kind"][:-1], e["bar"])
            if k in log:
                log[k]["n"] = 1
    return log


def outcome(kind, rec):
    if kind == "Fa":
        return rec["c"] == 1
    if kind == "Fo":
        return rec["c"] == -1
    if kind == "S+":
        return rec["s"] == "up"
    return rec["s"] == "down"


def resolve(log, data, now):
    """Pour chaque configuration dont la bougie est clôturée : réalisée (r=1), non réalisée (r=0) ou perdue (r=-1)."""
    done = 0
    for k, en in log.items():
        if "r" in en:
            continue
        t, tf, kind, bar = k.split("|", 3)
        try:
            start = pd.Timestamp(bar).tz_convert(sc.PARIS)
            end = sc.bar_end(start, tf)
        except Exception:
            en["r"] = -1
            continue
        if end > now:
            continue
        rec = (((data["stocks"].get(t) or {}).get("tf") or {}).get(tf) or {}).get("recent", {}).get(bar)
        if rec is not None:
            en["r"] = 1 if outcome(kind, rec) else 0
            done += 1
        elif now > end + LOST_AFTER:
            en["r"] = -1
    cutoff = (now - pd.Timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for k in [k for k, en in log.items() if "r" in en and en["d"] < cutoff]:
        del log[k]
    return done


def _rate(items):
    n = len(items)
    return {"n": n, "p": round(sum(x["p0"] for x in items) / n, 3) if n else None,
            "h": round(sum(x["r"] for x in items) / n, 3) if n else None}


def compute_stats(log, now):
    rows = []
    dates = [en["d"] for en in log.values()]
    for kind in KINDS:
        for tf in sc.VISIBLE_TF:
            allv = [en for k, en in log.items() if k.split("|")[1] == tf and k.split("|")[2] == kind]
            res = [en for en in allv if en.get("r") in (0, 1)]
            if not allv:
                continue
            row = {"kind": kind, "label": KIND_LABEL[kind], "tf": tf, "logged": len(allv), "resolved": len(res), "all": _rate(res), "bins": [], "ge": []}
            for lo, hi in zip(EDGES[:-1], EDGES[1:]):
                b = [x for x in res if lo <= x["p0"] < hi]
                if b:
                    row["bins"].append({"lo": lo, "hi": min(hi, 1.0), **_rate(b)})
            for t in EDGES[:-1]:
                b = [x for x in res if x["p0"] >= t]
                if b:
                    row["ge"].append({"t": t, **_rate(b)})
            row["sent"] = _rate([x for x in res if x.get("n")])
            sug = next((g["t"] for g in row["ge"] if g["n"] >= MIN_CASES and g["h"] >= TARGET), None)
            row["suggest"] = sug
            a = row["all"]
            if a["n"] < MIN_CASES * 1.5:
                row["verdict"] = "trop tôt"
            elif a["h"] - a["p"] > 0.05:
                row["verdict"] = "plus prudent que la réalité"
            elif a["p"] - a["h"] > 0.05:
                row["verdict"] = "trop optimiste"
            else:
                row["verdict"] = "calibré"
            rows.append(row)
    return {"updated": now.isoformat(), "since": min(dates) if dates else None,
            "days": (now.tz_localize(None).normalize() - pd.Timestamp(min(dates))).days + 1 if dates else 0,
            "target": TARGET, "min_cases": MIN_CASES, "logged": len(log),
            "resolved": sum(1 for en in log.values() if en.get("r") in (0, 1)), "rows": rows}


def run(events, data, sent, now, log_path=LOG_FILE, stats_path=STATS_FILE):
    log = load_log(log_path)
    update_log(log, events, sent, now)
    resolve(log, data, now)
    save_log(log, log_path)
    stats = compute_stats(log, now)
    Path(stats_path).write_text(json.dumps(stats, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return stats
