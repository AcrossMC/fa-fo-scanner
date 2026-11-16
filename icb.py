"""
Classification ICB officielle d'Euronext (industrie / supersecteur / secteur / sous-secteur) pour chaque action de l'univers.
Source : la fiche de chaque action sur live.euronext.com (ISIN fourni par Yahoo). Résultat mis en cache dans icb.json
(rafraîchi au plus une fois par mois et par action). Si le site change ou est injoignable, le scanner retombe sur Yahoo.

Usage :  python icb.py --probe        # test : affiche ce qui est lu pour quelques actions (à lancer une fois pour vérifier)
         python icb.py --update       # met à jour icb.json (utilisé par le workflow « Secteurs ICB »)
"""
import argparse
import html
import json
import re
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ICB_FILE = HERE / "icb.json"
URL = "https://live.euronext.com/en/product/equities/{isin}-XPAR"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; fa-fo-scanner)", "Accept-Language": "en"}
LEVELS = ("ind", "sup", "sec", "sub")
LABELS = {"ind": "Industry", "sup": "Supersector", "sec": "Sector", "sub": "Subsector"}
MAX_AGE = 30 * 86400


def to_text(page):
    page = re.sub(r"(?is)<(script|style).*?</\1>", " ", page)
    page = re.sub(r"(?s)<[^>]+>", "\n", page)
    return [x.strip() for x in html.unescape(page).split("\n") if x.strip()]


def parse_icb(page):
    """Lit les 4 niveaux ICB dans le texte de la page : une ligne « ICB Industry » suivie de sa valeur, etc. {} si rien trouvé."""
    lines = to_text(page)
    out = {}
    for i, ln in enumerate(lines):
        m = re.match(r"(?i)^ICB\s*(Industry|Supersector|Super\s*sector|Sector|Subsector|Sub\s*sector)\s*:?\s*(.*)$", ln)
        if not m:
            continue
        key = re.sub(r"\s+", "", m.group(1).lower())
        lvl = {"industry": "ind", "supersector": "sup", "sector": "sec", "subsector": "sub"}[key]
        val = m.group(2).strip() or (lines[i + 1].strip() if i + 1 < len(lines) else "")
        if val and not val.upper().startswith("ICB"):
            out[lvl] = val
    return out


def fetch(isin, session):
    r = session.get(URL.format(isin=isin), headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text


def isin_of(ticker):
    import yfinance as yf
    v = yf.Ticker(ticker).isin
    return v if v and v != "-" else ""


def tickers():
    import scanner as sc
    return list(sc.load_universe())


def probe(n=5):
    sess = requests.Session()
    for t in ["AIR.PA", "STLAP.PA", "BNP.PA", "SU.PA", "ALSTI.PA"][:n]:
        print("==", t)
        try:
            isin = isin_of(t)
            print("ISIN:", isin)
            page = fetch(isin, sess)
            print("page:", len(page), "octets ; ICB lu :", parse_icb(page))
            lines = to_text(page)
            hits = [i for i, ln in enumerate(lines) if "icb" in ln.lower() or "subsector" in ln.lower()]
            for i in hits[:8]:
                print("   ligne", i, ":", " | ".join(lines[max(0, i - 1): i + 3])[:200])
        except Exception as e:
            print("ERREUR :", type(e).__name__, str(e)[:200])


def update(limit=400):
    cache = json.loads(ICB_FILE.read_text(encoding="utf-8")) if ICB_FILE.exists() else {}
    sess = requests.Session()
    now = time.time()
    done = fails = 0
    for t in tickers():
        c = cache.get(t) or {}
        if c.get("sub") and now - c.get("ts", 0) < MAX_AGE:
            continue
        if done >= limit or fails >= 8:
            break
        try:
            isin = c.get("isin") or isin_of(t)
            got = parse_icb(fetch(isin, sess)) if isin else {}
            if got:
                cache[t] = {"isin": isin, **got, "ts": int(now)}
                fails = 0
            else:
                fails += 1
        except Exception:
            fails += 1
        done += 1
        time.sleep(0.7)
    ICB_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    print(f"ICB : {sum(1 for c in cache.values() if c.get('sub'))} actions classées, {done} essais, {fails} échecs consécutifs")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--update", action="store_true")
    a = ap.parse_args()
    probe() if a.probe else update()
