import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/sources.json"
DATA = ROOT / "data/jobs.json"
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")
TIMEOUT = 25

TERMS = [
    "securities", "custody", "settlement", "corporate actions", "corporate action",
    "back office", "middle office", "shareholder", "issuer", "corporate governance",
    "governance", "compliance", "regulatory", "market abuse", "operations",
    "business analyst", "business analysis", "process analyst", "process improvement",
    "process excellence", "risk", "controls", "fund operations", "investment operations",
    "project manager", "pmo", "banking", "financial services"
]
FORBIDDEN = [
    "internship", "intern ", "intern-", "stage", "tirocinio", "apprentice",
    "categorie protette", "categoria protetta", "l. 68/99", "legge 68/99",
    "art. 1 legge 68", "articolo 1 legge 68"
]
GEO = ["torino", "turin", "piemonte", "milano", "milan", "lombardia",
       "liguria", "genova", "remote", "remoto", "italy", "italia"]

def read_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback

def clean(value):
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()

def relevance(job):
    text = clean(job.get("title")) + " " + clean(job.get("description"))
    location = clean(job.get("location"))
    if any(term in text for term in FORBIDDEN):
        return False
    if not any(term in text for term in TERMS):
        return False
    return any(term in location for term in GEO) or any(term in text for term in ["remote", "remoto", "italy", "italia"])

def score(job):
    text = clean(job.get("title")) + " " + clean(job.get("description"))
    location = clean(job.get("location"))
    hits = [term for term in TERMS if term in text]
    points = min(45, len(hits) * 9)
    if any(x in text for x in ["bank", "banca", "financial", "finance", "securities"]):
        points += 20
    if any(x in text for x in ["corporate", "governance", "compliance", "regulatory"]):
        points += 10
    if any(x in location for x in GEO):
        points += 15
    if any(x in text for x in ["specialist", "analyst", "officer", "manager"]):
        points += 10
    return min(100, points), hits[:8]

def get(url):
    response = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "VirginiaJobTracker/1.0"})
    response.raise_for_status()
    return response.json()

def greenhouse(token, company):
    payload = get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    rows = []
    for item in payload.get("jobs", []):
        rows.append({
            "title": item.get("title"), "company": company,
            "location": (item.get("location") or {}).get("name", ""),
            "url": item.get("absolute_url"), "description": item.get("content", ""),
            "published_at": item.get("updated_at"), "source": "Greenhouse"
        })
    return rows

def lever(slug, company):
    payload = get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    rows = []
    for item in payload:
        categories = item.get("categories") or {}
        location = categories.get("location") or ", ".join(categories.get("allLocations") or [])
        additional = item.get("additional") or {}
        rows.append({
            "title": item.get("text"), "company": company, "location": location,
            "url": item.get("hostedUrl"),
            "description": " ".join([item.get("descriptionPlain", ""), additional.get("description", "")]),
            "published_at": item.get("createdAt"), "source": "Lever"
        })
    return rows

def main():
    config = read_json(CONFIG, {})
    previous = read_json(DATA, [])
    archive = {item["id"]: item for item in previous if item.get("id")}
    errors, scanned, added = [], 0, 0

    sources = []
    sources += [("Greenhouse", x) for x in config.get("greenhouse", [])]
    sources += [("Lever", x) for x in config.get("lever", [])]

    for provider, company_data in sources:
        name = company_data.get("name") or company_data.get("token") or company_data.get("slug") or "Company"
        try:
            if provider == "Greenhouse" and company_data.get("token"):
                jobs = greenhouse(company_data["token"], name)
            elif provider == "Lever" and company_data.get("slug"):
                jobs = lever(company_data["slug"], name)
            else:
                continue
            scanned += len(jobs)
            for job in jobs:
                if not job.get("url") or not relevance(job):
                    continue
                identifier = hashlib.sha256(job["url"].encode()).hexdigest()[:16]
                points, matched = score(job)
                old = archive.get(identifier, {})
                archive[identifier] = {
                    "id": identifier, "title": job.get("title") or "Titolo da verificare",
                    "company": name, "location": job.get("location") or "Località da verificare",
                    "url": job["url"], "description": re.sub(r"<[^>]+>", " ", job.get("description") or "")[:1800],
                    "published_at": job.get("published_at"), "source": provider,
                    "first_seen": old.get("first_seen", NOW), "last_seen": NOW,
                    "score": points, "matched_terms": matched,
                    "salary": "RAL da verificare", "contract": "Da verificare",
                    "work_mode": "Da verificare", "status": old.get("status", "Nuova")
                }
                added += 1
        except Exception as exc:
            errors.append(f"{provider} / {name}: {type(exc).__name__}: {exc}")

    result = sorted(archive.values(), key=lambda x: (x.get("score", 0), x.get("first_seen", "")), reverse=True)
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Annunci analizzati: {scanned}; pertinenti rilevati oggi: {added}; totale archivio: {len(result)}")
    for error in errors:
        print("AVVISO:", error)

if __name__ == "__main__":
    main()
