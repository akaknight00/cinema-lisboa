#!/usr/bin/env python3
"""Recolhe sessões de cinema de Lisboa e escreve docs/sessions.json.

Uso:  python scraper/scrape.py [--debug] [--only NOME_DA_FONTE]
--debug guarda o HTML de cada fonte em scraper/debug/ (para inspeção).
Se uma fonte falhar ou vier vazia, mantêm-se as sessões antigas dessa fonte.
"""
import argparse, hashlib, json, re, sys
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "scraper" / "sources.json"
OUT = ROOT / "docs" / "sessions.json"
DEBUG_DIR = ROOT / "scraper" / "debug"
UA = "cinema-lisboa-pessoal/0.1 (projeto pessoal; 1 pedido por fonte por dia)"


SESSION = requests.Session()  # guarda cookies (evita ciclos de redirecionamento)


def fetch(url):
    r = SESSION.get(url, headers={"User-Agent": UA, "Accept-Language": "pt-PT,pt;q=0.9"}, timeout=30)
    r.raise_for_status()
    return r.text


def iter_jsonld(html):
    """Gera todos os objetos JSON-LD (incluindo listas e @graph) de uma página."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except json.JSONDecodeError:
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                if "@graph" in x:
                    stack.extend(x["@graph"])
                yield x


def parse_jsonld(html, src):
    """Lê eventos com name + startDate (datas locais 'AAAA-MM-DDTHH:MM...')."""
    out = []
    for x in iter_jsonld(html):
        name, start = x.get("name"), x.get("startDate")
        if not (isinstance(name, str) and isinstance(start, str)):
            continue
        if not re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", start):
            continue
        note, title = "", name.strip()
        if " | " in title:  # ex.: "Lado BB | Viva Maria!"
            note, title = [p.strip() for p in title.split(" | ", 1)]
        out.append({"date": start[:10], "time": start[11:16], "title": title,
                    "note": note, "url": x.get("url", "") or ""})
    return out


MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
         "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}
DAY_RE = re.compile(r"^(?:seg|ter|qua|qui|sex|s[áa]b|dom)\w*\.?\s+(\d{1,2})\s+([A-Za-zçÇ]{3})", re.I)
TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
FILM_RE = re.compile(r"^(.+?)\s*\((\d{4})\)\s*$")


def parse_filmspot(html, src):
    """filmSPOT: títulos 'Nome (ano)' seguidos de linhas 'Qua 30 Set: 15:20 · 18:20'."""
    lines = [l.strip() for l in BeautifulSoup(html, "html.parser").get_text("\n").split("\n") if l.strip()]
    today, out = date.today(), []
    title = year = day = None

    def add(text):
        for m in TIME_RE.finditer(text):
            out.append({"date": day.isoformat(), "time": f"{int(m.group(1)):02d}:{m.group(2)}",
                        "title": title, "year": year, "note": "", "url": src["url"]})

    for line in lines:
        m = DAY_RE.match(line)
        mon = MESES.get(m.group(2)[:3].lower()) if m else None
        if m and mon and title:
            try:
                day = date(today.year, mon, int(m.group(1)))
            except ValueError:
                day = None
                continue
            if (today - day).days > 180:
                day = day.replace(year=today.year + 1)
            add(line[m.end():])
            continue
        f = FILM_RE.match(line)
        if f and not TIME_RE.search(line):
            title, year, day = f.group(1).split(" / ")[0].strip(), f.group(2), None
            continue
        if day and title and re.fullmatch(r"[\d:\s·,•|/-]+", line):
            add(line)
    return out


# Para um site sem JSON-LD, escreve-se aqui um parser próprio e regista-se em PARSERS.
# Cada parser recebe (html, src) e devolve uma lista de dicts com date, time, title.
PARSERS = {"jsonld": parse_jsonld, "filmspot": parse_filmspot}


def make_id(s):
    key = f'{s["venue"]}|{s["date"]}|{s["time"]}|{s["title"]}'
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args()

    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    old = json.loads(OUT.read_text(encoding="utf-8")).get("sessions", []) if OUT.exists() else []
    today = date.today().isoformat()
    fresh, ok, report = [], set(), []

    for src in sources:
        name = src["name"]
        if args.only and name != args.only:
            continue
        try:
            html = fetch(src["url"])
            if args.debug:
                DEBUG_DIR.mkdir(exist_ok=True)
                (DEBUG_DIR / f"{name}.html").write_text(html, encoding="utf-8")
            found = PARSERS[src["parser"]](html, src)
            found = [f for f in found if f["date"] >= today]
            if not found:
                raise ValueError("0 sessões futuras encontradas")
            for f in found:
                f.update(venue=src["venue"], source=name)
                f["id"] = make_id(f)
            fresh += found
            ok.add(name)
            report.append(f"OK    {name}: {len(found)} sessões")
        except Exception as e:  # uma fonte má não pode estragar as outras
            report.append(f"FALHA {name}: {e}")
            print(f"::warning::{name}: {e}")

    kept = [s for s in old if s.get("source") not in ok and s["date"] >= today]
    by_id = {s["id"]: s for s in kept + fresh}
    sessions = sorted(by_id.values(), key=lambda s: (s["date"], s["time"], s["venue"]))

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"updated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                               "sessions": sessions}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(report))
    print(f"Total: {len(sessions)} sessões em {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
