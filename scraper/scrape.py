#!/usr/bin/env python3
"""Recolhe sessões de cinema de Lisboa e escreve docs/sessions.json.

Uso:  python scraper/scrape.py [--debug] [--only NOME_DA_FONTE]
--debug guarda o HTML de cada fonte em scraper/debug/ (para inspeção).
Se uma fonte falhar ou vier vazia, mantêm-se as sessões antigas dessa fonte.
"""
import argparse, hashlib, json, re, sys, time, unicodedata
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "scraper" / "sources.json"
OUT = ROOT / "docs" / "sessions.json"
FILMS = ROOT / "docs" / "films.json"
MAX_ENRICH = 60  # páginas de filmes a visitar por execução (as restantes ficam para o dia seguinte)
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
                    g = x["@graph"]
                    stack.extend(g if isinstance(g, list) else [g])
                yield x


def clip(text, n=500):
    """Tira HTML, normaliza espaços e corta a n caracteres (em fim de palavra)."""
    text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()
    if len(text) <= n:
        return text
    return text[: n - 1].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def _img(v):
    if isinstance(v, list):
        v = v[0] if v else ""
    if isinstance(v, dict):
        v = v.get("url", "")
    return v if isinstance(v, str) else ""


def film_key(title):
    t = unicodedata.normalize("NFD", title.lower())
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in t if not unicodedata.combining(c))).strip()


def parse_film_page(html):
    """Devolve (sinopse, cartaz) de uma página de filme."""
    soup = BeautifulSoup(html, "html.parser")
    desc = ""
    for x in iter_jsonld(html):
        if "Movie" in str(x.get("@type")) and isinstance(x.get("description"), str):
            desc = x["description"]
            break
    if not desc:
        for attrs in ({"property": "og:description"}, {"name": "description"}):
            m = soup.find("meta", attrs=attrs)
            if m and m.get("content"):
                desc = m["content"]
                break
    m = soup.find("meta", attrs={"property": "og:image"})
    poster = m.get("content", "") if m else ""
    return (clip(desc) if len(desc) >= 40 else ""), ("" if "logo" in poster else poster)


def parse_jsonld(html, src):
    """Lê eventos com name + startDate (datas locais 'AAAA-MM-DDTHH:MM...')."""
    out = []
    for x in iter_jsonld(html):
        name, start = x.get("name"), x.get("startDate")
        if not (isinstance(name, str) and isinstance(start, str)):
            continue
        if not re.match(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", start):
            continue
        note, title = "", name.strip()
        if " | " in title:  # ex.: "Lado BB | Viva Maria!"
            note, title = [p.strip() for p in title.split(" | ", 1)]
        year = ""
        m = re.match(r"^(.+?)\s*\((\d{4})\)\s*$", title)  # "Título / Original (2025)"
        if m:
            title, year = m.group(1).split(" / ")[0].strip(), m.group(2)
        wp = x.get("workPresented") if isinstance(x.get("workPresented"), dict) else {}
        url = x.get("url") or wp.get("url", "") or ""
        out.append({"date": start[:10], "time": start[11:16], "title": title,
                    "year": year, "note": note, "url": url,
                    "poster": _img(x.get("image")) or _img(wp.get("image")),
                    "summary": clip(x.get("description") or wp.get("description") or ""),
                    "film_url": wp.get("url", "") or ""})
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
    old_films = json.loads(FILMS.read_text(encoding="utf-8")) if FILMS.exists() else {}
    today = date.today().isoformat()
    fresh, ok, report = [], set(), []

    for src in sources:
        name = src["name"]
        if args.only and name != args.only:
            continue
        try:
            time.sleep(2)  # pausa entre pedidos
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
    # cartazes e sinopses: um registo por filme (docs/films.json), em vez de repetido em cada sessão
    films = {k: dict(v) for k, v in old_films.items()}
    for f in fresh:
        e = films.setdefault(film_key(f["title"]), {})
        if f.get("poster") and not e.get("poster"):
            e["poster"] = f["poster"]
        if f.get("summary") and not e.get("summary"):
            e["summary"] = f["summary"]
        if f.get("film_url") and not e.get("page"):
            e["page"] = f["film_url"]
    retry_after = (date.today() - timedelta(days=30)).isoformat()
    todo = [e for e in films.values() if e.get("page") and not e.get("summary") and e.get("tried", "") < retry_after]
    done = 0
    for e in todo[:MAX_ENRICH]:
        e["tried"] = today
        try:
            time.sleep(1.5)
            summary, poster = parse_film_page(fetch(e["page"]))
            if summary:
                e["summary"] = summary
                done += 1
            if poster and not e.get("poster"):
                e["poster"] = poster
        except Exception as ex:
            print(f"::warning::filme {e['page']}: {ex}")
    report.append(f"Sinopses: {done} novas ({max(len(todo) - MAX_ENRICH, 0)} por tentar amanhã)")
    for f in fresh:
        f["film"] = film_key(f["title"])
        for k in ("poster", "summary", "film_url"):
            f.pop(k, None)

    by_id = {s["id"]: s for s in kept + fresh}
    sessions = sorted(by_id.values(), key=lambda s: (s["date"], s["time"], s["venue"]))
    used = {s.get("film") for s in sessions}
    FILMS.write_text(json.dumps({k: v for k, v in films.items() if k in used and v}, ensure_ascii=False, indent=1), encoding="utf-8")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"updated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                               "sessions": sessions}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(report))
    print(f"Total: {len(sessions)} sessões em {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
