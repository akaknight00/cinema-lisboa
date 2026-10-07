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


def fetch(url):
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "pt-PT,pt;q=0.9"}, timeout=30)
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


# Para um site sem JSON-LD, escreve-se aqui um parser próprio e regista-se em PARSERS.
# Cada parser recebe (html, src) e devolve uma lista de dicts com date, time, title.
PARSERS = {"jsonld": parse_jsonld}


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
