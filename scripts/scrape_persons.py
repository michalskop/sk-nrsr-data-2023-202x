"""Scrape MP list and membership data from nrsr.sk.

Outputs:
  work/raw/persons_raw.csv   — one row per MP (mp_id, given_name, family_name, …)
  work/raw/memberships_raw.csv — one row per membership span (mp_id, club, start, end)

Run from the sk-nrsr-data-2023-202x/ directory:
  python scripts/scrape_persons.py
"""

import csv
import logging
import re
import time
from pathlib import Path

import requests_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_ROOT = Path(__file__).resolve().parents[1]
_RAW = _ROOT / "work" / "raw"
_RAW.mkdir(parents=True, exist_ok=True)

_MP_LIST_URL = "https://www.nrsr.sk/web/Default.aspx?sid=poslanci/zoznam_pos"
_MP_DETAIL_URL = "https://www.nrsr.sk/web/Default.aspx?sid=poslanci/poslanec&PoslanecID={mp_id}"
_DELAY = 0.5

_PERSONS_COLS = [
    "mp_id", "given_name", "family_name", "title", "born_on",
    "email", "municipality", "region", "in_parliament",
]
_MEMBERSHIPS_COLS = ["mp_id", "club_name", "start_date", "end_date"]


def _get_mp_ids(session: requests_html.HTMLSession) -> list[str]:
    r = session.get(_MP_LIST_URL)
    mp_ids = []
    for a in r.html.find("a"):
        href = a.attrs.get("href", "")
        m = re.search(r"PoslanecID=(\d+)", href)
        if m:
            mp_ids.append(m.group(1))
    return list(dict.fromkeys(mp_ids))  # deduplicate, preserve order


def _scrape_mp(session: requests_html.HTMLSession, mp_id: str) -> tuple[dict, list[dict]]:
    url = _MP_DETAIL_URL.format(mp_id=mp_id)
    r = session.get(url)

    person: dict = {"mp_id": mp_id, "in_parliament": False}
    memberships: list[dict] = []

    try:
        name_el = r.html.find(".personal_data .mp_main_title", first=True)
        if name_el:
            full = name_el.text.strip()
            parts = full.split()
            # best-effort: given_name = first word(s) that look like names, family_name = last
            person["given_name"] = parts[0] if parts else ""
            person["family_name"] = parts[-1] if len(parts) > 1 else ""
        else:
            # fallback: look for h1 with MP name
            h1 = r.html.find("h1", first=True)
            if h1:
                parts = h1.text.strip().split()
                person["given_name"] = parts[0] if parts else ""
                person["family_name"] = parts[-1] if len(parts) > 1 else ""
    except Exception:
        pass

    try:
        for row in r.html.find(".personal_data tr"):
            cells = row.find("td")
            if len(cells) < 2:
                continue
            label = cells[0].text.strip().lower()
            value = cells[1].text.strip()
            if "titul" in label:
                person["title"] = value
            elif "naroden" in label or "born" in label:
                person["born_on"] = value
            elif "e-mail" in label:
                person["email"] = value
            elif "obec" in label or "municipality" in label:
                person["municipality"] = value
            elif "kraj" in label or "region" in label:
                person["region"] = value
    except Exception:
        pass

    # active mandate check: page title or mandate section
    try:
        mandate_els = r.html.find(".mandate_active")
        person["in_parliament"] = len(mandate_els) > 0
    except Exception:
        pass

    # club memberships
    try:
        for row in r.html.find(".club_table tr, #_sectionLayoutContainer__members tr"):
            cells = row.find("td")
            if len(cells) < 2:
                continue
            club_name = cells[0].text.strip()
            dates = cells[1].text.strip() if len(cells) > 1 else ""
            start_date = end_date = None
            date_match = re.findall(r"\d{2}\.\d{2}\.\d{4}", dates)
            if date_match:
                def _reformat(d: str) -> str:
                    dd, mm, yyyy = d.split(".")
                    return f"{yyyy}-{mm}-{dd}"
                start_date = _reformat(date_match[0])
                end_date = _reformat(date_match[1]) if len(date_match) > 1 else None
            if club_name:
                memberships.append({
                    "mp_id": mp_id,
                    "club_name": club_name,
                    "start_date": start_date or "",
                    "end_date": end_date or "",
                })
    except Exception:
        pass

    return person, memberships


def main() -> None:
    session = requests_html.HTMLSession()

    logging.info("Fetching MP list from %s", _MP_LIST_URL)
    mp_ids = _get_mp_ids(session)
    logging.info("Found %d MP IDs", len(mp_ids))

    persons_out = _RAW / "persons_raw.csv"
    memberships_out = _RAW / "memberships_raw.csv"

    with open(persons_out, "w", newline="", encoding="utf-8") as pf, \
         open(memberships_out, "w", newline="", encoding="utf-8") as mf:
        pw = csv.DictWriter(pf, fieldnames=_PERSONS_COLS, extrasaction="ignore")
        mw = csv.DictWriter(mf, fieldnames=_MEMBERSHIPS_COLS, extrasaction="ignore")
        pw.writeheader()
        mw.writeheader()

        for i, mp_id in enumerate(mp_ids):
            if i % 20 == 0:
                logging.info("Scraping MP %d/%d (id=%s)", i + 1, len(mp_ids), mp_id)
            try:
                person, memberships = _scrape_mp(session, mp_id)
                pw.writerow(person)
                for m in memberships:
                    mw.writerow(m)
            except Exception as e:
                logging.warning("Failed to scrape mp_id=%s: %s", mp_id, e)
            time.sleep(_DELAY)

    logging.info("Wrote %s and %s", persons_out, memberships_out)


if __name__ == "__main__":
    main()
