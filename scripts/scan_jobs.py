"""Virginia Job Tracker - raccolta quotidiana degli annunci.

Fonti in config/sources.json:
  "aziende": basta il nome e l'URL della pagina carriere. La piattaforma
             (Greenhouse, Lever, Ashby, Workday, SmartRecruiters) viene
             riconosciuta automaticamente dall'URL.
  "pagine":  portali senza API pubblica. Lo script controlla se la pagina
             cambia e segnala i nuovi link pertinenti.
Le vecchie sezioni "greenhouse", "lever" e "ashby" continuano a funzionare.
"""
import hashlib
import html
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/sources.json"
DATA = ROOT / "data/jobs.json"
PAGES = ROOT / "data/pages.json"
REPORT = ROOT / "data/sources_report.json"
NOW_DT = datetime.now(timezone.utc)
NOW = NOW_DT.isoformat(timespec="seconds")
NEW_DAYS = 7
TIMEOUT = 30
HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
           "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"}

# Parole chiave. Basta una nel TITOLO, oppure almeno due "forti" nella descrizione.
TERMS = [
    "securities", "custody", "settlement", "corporate actions", "corporate action",
    "back office", "back-office", "middle office", "shareholder", "issuer",
    "corporate governance", "governance", "compliance", "regulatory", "market abuse",
    "operations", "business analyst", "business analysis", "process analyst",
    "process improvement", "process excellence", "risk", "controls", "fund operations",
    "investment operations", "project manager", "pmo", "asset servicing",
    "affari societari", "titoli", "operativo", "organizzazione",
]
STRONG = [
    "securities", "custody", "settlement", "corporate action", "back office",
    "back-office", "middle office", "shareholder", "issuer", "market abuse",
    "fund operations", "investment operations", "asset servicing", "affari societari",
    "corporate governance", "insider", "general meeting", "assemblee",
]
FORBIDDEN = re.compile(
    r"\b(intern|internship|stage|stagista|stageur|tirocinio|apprentice|apprendistato|trainee|"
    r"graduate program(me)?|categori[ae] protett[ae]|cat\. ?prot)\b|68/99", re.I)
PAGE_TERMS = ["specialist", "specialista", "analyst", "analista", "officer", "manager",
              "register", "agm", "addetto", "operations", "titoli", "back office", "custody"]
GEO = ["torino", "turin", "piemonte", "milano", "milan", "lombardia", "liguria",
       "genova", "italy", "italia", "biella", "novara", "vercelli", "asti", "alba",
       "cuneo", "alessandria", "ivrea", "pinerolo", "moncalieri", "sesto san giovanni",
       "basiglio", "remote", "remoto"]
GEO_RE = re.compile(r"\b(" + "|".join(re.escape(g) for g in GEO if g not in ("remote", "remoto")) + r")\b")
WORKDAY_QUERIES = ["Milan", "Turin", "Italy"]
WORKDAY_PAUSE = 2.5   # secondi tra una richiesta e l'altra, per non essere bloccati

session = requests.Session()
session.headers.update(HEADERS)


def read_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def load_config():
    """Legge config/sources.json perdonando le virgole in più; segnala gli altri errori."""
    try:
        text = CONFIG.read_text(encoding="utf-8-sig")
    except OSError:
        return {}, "Il file config/sources.json non esiste."
    for attempt in (text, re.sub(r",(\s*[\]}])", r"\1", text)):
        try:
            return json.loads(attempt), ""
        except json.JSONDecodeError as exc:
            error = exc
    return {}, (f"Il file config/sources.json contiene un errore alla riga {error.lineno}: "
                "controlla virgole, parentesi e virgolette in quel punto (puoi incollarlo su jsonlint.com).")


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def plain(text):
    """Toglie l'HTML (anche quando arriva già 'escapato', come da Greenhouse)."""
    text = html.unescape(html.unescape(str(text or "")))
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def low(value):
    return plain(value).lower()


def relevance(job):
    title, desc, loc = low(job.get("title")), low(job.get("description")), low(job.get("location"))
    if FORBIDDEN.search(title):
        return False
    in_title = any(t in title for t in TERMS)
    strong_desc = len({t for t in STRONG if t in desc})
    if not (in_title or strong_desc >= 2):
        return False
    if GEO_RE.search(loc):
        return True
    remote = "remot" in loc or "remot" in low(job.get("work_mode"))
    return remote and loc.replace("remote", "").replace("remoto", "").strip(" ,-") == ""


def score(job):
    title, desc, loc = low(job.get("title")), low(job.get("description")), low(job.get("location"))
    text = title + " " + desc
    hits = [t for t in TERMS if t in text]
    points = min(30, len(hits) * 6)
    points += min(30, sum(10 for t in STRONG if t in title))
    if any(x in text for x in ["bank", "banca", "financial", "finance", "securities", "sgr", "sim"]):
        points += 15
    if any(x in text for x in ["corporate", "governance", "compliance", "regulatory"]):
        points += 10
    if any(x in loc for x in ["torino", "turin", "milano", "milan"]):
        points += 15
    if any(x in title for x in ["specialist", "analyst", "officer", "manager", "specialista"]):
        points += 5
    return min(100, points), hits[:8]


def call(method, url, **kwargs):
    """Richiesta con pausa e nuovi tentativi se il sito risponde 'troppe richieste'."""
    for attempt in range(2):
        r = session.request(method, url, timeout=TIMEOUT, **kwargs)
        if r.status_code not in (429, 503):
            r.raise_for_status()
            return r
        time.sleep(10)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------- piattaforme

def detect(url):
    u = urlparse(url)
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
    if "greenhouse.io" in host and parts:
        return "greenhouse", {"token": parts[0]}
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and parts:
        return "lever", {"slug": parts[0], "eu": ".eu." in host}
    if host == "jobs.ashbyhq.com" and parts:
        return "ashby", {"board": parts[0]}
    if host.endswith("myworkdayjobs.com"):
        tenant = host.split(".")[0]
        site = [p for p in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
        if site:
            return "workday", {"base": f"{u.scheme}://{u.netloc}", "tenant": tenant, "site": site[0]}
    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com") and parts:
        return "smartrecruiters", {"company": parts[0]}
    return None, {}


def greenhouse(p, name):
    data = session.get(f"https://boards-api.greenhouse.io/v1/boards/{p['token']}/jobs?content=true",
                       timeout=TIMEOUT)
    data.raise_for_status()
    return [{"title": j.get("title"), "location": (j.get("location") or {}).get("name", ""),
             "url": j.get("absolute_url"), "description": j.get("content", ""),
             "published_at": j.get("updated_at")} for j in data.json().get("jobs", [])]


def lever(p, name):
    host = "api.eu.lever.co" if p.get("eu") else "api.lever.co"
    data = session.get(f"https://{host}/v0/postings/{p['slug']}?mode=json", timeout=TIMEOUT)
    data.raise_for_status()
    rows = []
    for j in data.json():
        cat = j.get("categories") or {}
        rows.append({"title": j.get("text"),
                     "location": cat.get("location") or ", ".join(cat.get("allLocations") or []),
                     "url": j.get("hostedUrl"),
                     "description": " ".join([j.get("descriptionPlain", ""),
                                              (j.get("additional") or {}).get("description", "")]),
                     "published_at": j.get("createdAt")})
    return rows


def ashby(p, name):
    data = session.get(f"https://api.ashbyhq.com/posting-api/job-board/{p['board']}"
                       "?includeCompensation=true", timeout=TIMEOUT)
    data.raise_for_status()
    rows = []
    for j in data.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        places = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        rows.append({"title": j.get("title"), "location": ", ".join(x for x in places if x),
                     "url": j.get("jobUrl"),
                     "description": j.get("descriptionPlain") or j.get("descriptionHtml", ""),
                     "published_at": j.get("publishedAt"),
                     "salary": (j.get("compensation") or {}).get("compensationTierSummary"),
                     "work_mode": "Remoto" if j.get("isRemote") else None,
                     "contract": j.get("employmentType")})
    return rows


def workday(p, name):
    api = f"{p['base']}/wday/cxs/{p['tenant']}/{p['site']}"
    wd_headers = {"Accept": "application/json", "Content-Type": "application/json",
                  "Origin": p["base"], "Referer": f"{p['base']}/{p['site']}"}
    try:  # prima visita la pagina pubblica, come farebbe un browser (imposta i cookie)
        session.get(f"{p['base']}/{p['site']}", timeout=TIMEOUT)
    except requests.RequestException:
        pass
    found = {}
    for query in ([""] if p.get("all") else WORKDAY_QUERIES):
        for offset in range(0, 400, 20):
            time.sleep(WORKDAY_PAUSE)
            r = call("POST", f"{api}/jobs", headers=wd_headers,
                     json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": query})
            postings = r.json().get("jobPostings", [])
            for j in postings:
                if j.get("externalPath"):
                    found[j["externalPath"]] = j
            if len(postings) < 20:
                break
    rows = []
    for path, j in found.items():
        row = {"title": j.get("title"), "location": j.get("locationsText", ""),
               "url": f"{p['base']}/{p['site']}{path}", "description": "",
               "published_at": j.get("postedOn")}
        # La descrizione si scarica solo per gli annunci in zona con titolo pertinente.
        near = any(g in low(row["location"]) for g in GEO) or "locations" in low(row["location"])
        if near and any(t in low(row["title"]) for t in TERMS + STRONG):
            try:
                time.sleep(WORKDAY_PAUSE)
                d = call("GET", f"{api}{path}", headers=wd_headers).json().get("jobPostingInfo", {})
                row["description"] = d.get("jobDescription", "")
                row["location"] = ", ".join(filter(None, [d.get("location")] + (d.get("additionalLocations") or []))) or row["location"]
                row["contract"] = d.get("timeType")
            except (requests.RequestException, ValueError):
                pass
        rows.append(row)
    return rows


def smartrecruiters(p, name):
    rows, offset = [], 0
    while offset < 1000:
        r = session.get(f"https://api.smartrecruiters.com/v1/companies/{p['company']}/postings",
                        params={"limit": 100, "offset": offset}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        for j in data.get("content", []):
            loc = j.get("location") or {}
            rows.append({"title": j.get("name"),
                         "location": ", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")]))
                         + (" remote" if loc.get("remote") else ""),
                         "url": f"https://jobs.smartrecruiters.com/{p['company']}/{j.get('id')}",
                         "description": " ".join([(j.get("function") or {}).get("label", ""),
                                                  (j.get("department") or {}).get("label", "")]),
                         "published_at": j.get("releasedDate"),
                         "contract": (j.get("typeOfEmployment") or {}).get("label")})
        offset += 100
        if offset >= data.get("totalFound", 0):
            break
    return rows


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby,
            "workday": workday, "smartrecruiters": smartrecruiters}
LABELS = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby",
          "workday": "Workday", "smartrecruiters": "SmartRecruiters"}


def company_sources(config):
    """Unisce il formato nuovo ("aziende") con quello vecchio."""
    out = []
    for item in config.get("aziende", []):
        platform, params = detect(item.get("url", ""))
        if item.get("tutti_gli_annunci"):
            params["all"] = True
        out.append((item.get("name") or item.get("url"), item.get("url", ""), platform, params))
    for item in config.get("greenhouse", []):
        out.append((item.get("name"), item.get("careers_url", ""), "greenhouse", {"token": item.get("token")}))
    for item in config.get("lever", []):
        out.append((item.get("name"), item.get("careers_url", ""), "lever", {"slug": item.get("slug")}))
    for item in config.get("ashby", []):
        out.append((item.get("name"), item.get("careers_url", ""), "ashby", {"board": item.get("board")}))
    seen, unique = set(), []
    for src in out:
        key = (src[2], json.dumps(src[3], sort_keys=True))
        if key not in seen:
            seen.add(key)
            unique.append(src)
    return unique


# ---------------------------------------------------------------- pagine sorvegliate

def page_snapshot(url):
    r = session.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    body = r.text
    links = {}
    for href, text in re.findall(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', body):
        label = plain(text)
        if 4 <= len(label) <= 160:
            links[label] = urljoin(url, html.unescape(href))
    text = plain(body)
    return hashlib.sha256(text.encode()).hexdigest(), links, len(text)


def check_pages(config, previous):
    old = {p["url"]: p for p in previous}
    result = []
    for item in config.get("pagine", []):
        url, name = item.get("url", ""), item.get("name") or item.get("url")
        before = old.get(url, {})
        entry = {"name": name, "url": url, "note": item.get("note", ""),
                 "last_checked": NOW, "last_changed": before.get("last_changed"),
                 "fingerprint": before.get("fingerprint"), "known_links": before.get("known_links", []),
                 "new_links": before.get("new_links", []), "status": "ok", "error": ""}
        try:
            digest, links, size = page_snapshot(url)
            if size < 400:
                entry["status"] = "vuota"
                entry["error"] = ("La pagina carica gli annunci con JavaScript: lo script vede solo "
                                  "una pagina vuota. Usa il link per controllarla a mano.")
            if before.get("fingerprint") and digest != before["fingerprint"]:
                entry["last_changed"] = NOW
                known = set(before.get("known_links", []))
                fresh = [{"text": t, "url": u, "found": NOW} for t, u in links.items()
                         if t not in known and any(k in t.lower() for k in TERMS + STRONG + PAGE_TERMS)
                         and not FORBIDDEN.search(t)]
                entry["new_links"] = (fresh + before.get("new_links", []))[:25]
            elif not before.get("fingerprint"):
                entry["last_changed"] = NOW
            entry["fingerprint"] = digest
            entry["known_links"] = sorted(links)[:800]
        except Exception as exc:  # noqa: BLE001
            entry["status"] = "errore"
            entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
        result.append(entry)
    return result


# ---------------------------------------------------------------- principale

def main():
    config, config_error = load_config()
    archive = {j["id"]: j for j in read_json(DATA, []) if j.get("id")}
    report, ok_companies, scanned, today = [], set(), 0, 0
    if config_error:
        report.append({"name": "File di configurazione", "url": "config/sources.json",
                       "platform": "config/sources.json", "status": "errore", "scanned": 0,
                       "relevant": 0, "error": config_error, "checked": NOW})
        print("ERRORE:", config_error)
        # Con la configurazione illeggibile non si tocca nulla: restano i dati dell'ultima scansione buona.
        write_json(REPORT, {"updated": NOW, "sources": report})
        return

    sources = company_sources(config)
    configured = {src[0] for src in sources}
    for name, url, platform, params in sources:
        row = {"name": name, "url": url, "platform": LABELS.get(platform, "non riconosciuta"),
               "status": "ok", "scanned": 0, "relevant": 0, "error": "", "checked": NOW}
        if platform not in FETCHERS:
            row["status"] = "errore"
            row["error"] = ("URL non riconosciuto come Greenhouse, Lever, Ashby, Workday o "
                            "SmartRecruiters. Spostalo nella sezione \"pagine\".")
            report.append(row)
            continue
        try:
            jobs = FETCHERS[platform](params, name)
        except Exception as exc:  # noqa: BLE001
            row["status"] = "errore"
            row["error"] = f"{type(exc).__name__}: {exc}"[:300]
            report.append(row)
            continue
        ok_companies.add(name)
        row["scanned"] = len(jobs)
        scanned += len(jobs)
        for job in jobs:
            if not job.get("url") or not relevance(job):
                continue
            ident = hashlib.sha256(job["url"].encode()).hexdigest()[:16]
            points, matched = score(job)
            old = archive.get(ident, {})
            archive[ident] = {
                "id": ident, "title": plain(job.get("title")) or "Titolo da verificare",
                "company": name, "location": plain(job.get("location")) or "Località da verificare",
                "url": job["url"], "description": plain(job.get("description"))[:1800],
                "published_at": job.get("published_at"), "source": LABELS[platform],
                "first_seen": old.get("first_seen", NOW), "last_seen": NOW, "online": True,
                "score": points, "matched_terms": matched,
                "salary": job.get("salary") or "RAL da verificare",
                "contract": job.get("contract") or "Contratto da verificare",
                "work_mode": job.get("work_mode") or "Modalità da verificare",
            }
            row["relevant"] += 1
            today += 1
        report.append(row)

    cutoff = NOW_DT - timedelta(days=NEW_DAYS)
    result = []
    for job in archive.values():
        if job.get("company") not in configured:   # azienda tolta da config/sources.json
            continue
        job["description"] = plain(job.get("description"))[:1800]
        if not relevance(job):          # ripulisce l'archivio con i criteri aggiornati
            continue
        if job["company"] in ok_companies and job.get("last_seen") != NOW:
            job["online"] = False       # la fonte risponde ma l'annuncio non c'è più
        try:
            first = datetime.fromisoformat(job.get("first_seen", NOW))
        except ValueError:
            first = NOW_DT
        job["status"] = "Nuova" if first >= cutoff else "In archivio"
        result.append(job)
    result.sort(key=lambda x: (x.get("online", True), x.get("score", 0), x.get("first_seen", "")),
                reverse=True)

    pages = check_pages(config, read_json(PAGES, []))
    write_json(DATA, result)
    write_json(PAGES, pages)
    write_json(REPORT, {"updated": NOW, "sources": report})

    print(f"Annunci analizzati: {scanned}; pertinenti oggi: {today}; in archivio: {len(result)}")
    for row in report:
        print(f"[{row['status']}] {row['name']} ({row['platform']}): "
              f"{row['scanned']} letti, {row['relevant']} pertinenti {row['error']}")
    for page in pages:
        print(f"[pagina {page['status']}] {page['name']} {page['error']}")


if __name__ == "__main__":
    main()
