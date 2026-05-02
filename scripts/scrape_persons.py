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

_MP_LIST_URL = "https://www.nrsr.sk/web/Default.aspx?sid=poslanci/zoznam_abc"
_MP_DETAIL_URL = "https://www.nrsr.sk/web/Default.aspx?sid=poslanci/poslanec&PoslanecID={mp_id}&CisObdobia=9"
_DELAY = 0.5

_PERSONS_COLS = [
    "mp_id", "given_name", "family_name", "title", "born_on",
    "email", "municipality", "region", "in_parliament",
]
_MEMBERSHIPS_COLS = ["mp_id", "club_name", "start_date", "end_date"]


def _get_mp_ids(session: requests_html.HTMLSession) -> tuple[list[str], set[str]]:
    """Return (all_ids, current_ids). zoznam_abc lists only current MPs."""
    r = session.get(_MP_LIST_URL)
    mp_ids = []
    for a in r.html.find("a"):
        href = a.attrs.get("href", "")
        m = re.search(r"PoslanecID=(\d+)", href)
        if m:
            mp_ids.append(m.group(1))
    current = set(dict.fromkeys(mp_ids))
    return list(current), current


# Slovak pre-nominal academic titles to strip from h1 name
_PRENOMINAL_TITLES = {
    "prof.", "doc.", "mgr.", "ing.", "judr.", "mudr.", "phdr.", "rndr.",
    "paedr.", "thdr.", "bc.", "mvdr.", "pharm.dr.", "dipl.",
}


def _parse_name(full: str) -> tuple[str, str, str]:
    """Parse 'MUDr. Vladimír Baláž, PhD.' → (given_name, family_name, title)."""
    # Split off post-nominal titles (after comma)
    if "," in full:
        name_part, _, post = full.partition(",")
        title_after = post.strip()
    else:
        name_part = full
        title_after = ""

    tokens = name_part.strip().split()
    pre_titles = []
    name_tokens = []
    for tok in tokens:
        if tok.lower() in _PRENOMINAL_TITLES:
            pre_titles.append(tok)
        else:
            name_tokens.append(tok)

    title = " ".join(pre_titles) + (f", {title_after}" if title_after else "")
    given = name_tokens[0] if name_tokens else ""
    family = name_tokens[-1] if len(name_tokens) > 1 else name_tokens[0] if name_tokens else ""
    return given, family, title.strip(", ")


def _scrape_mp(session: requests_html.HTMLSession, mp_id: str) -> tuple[dict, list[dict]]:
    url = _MP_DETAIL_URL.format(mp_id=mp_id)
    r = session.get(url)

    person: dict = {"mp_id": mp_id, "in_parliament": False}
    memberships: list[dict] = []

    try:
        h1 = r.html.find("h1", first=True)
        if h1:
            given, family, title = _parse_name(h1.text.strip())
            person["given_name"] = given
            person["family_name"] = family
            person["title"] = title
    except Exception:
        pass

    try:
        for row in r.html.find("tr"):
            cells = row.find("td")
            if len(cells) < 2:
                continue
            label = cells[0].text.strip().lower()
            value = cells[1].text.strip()
            if "e-mail" in label:
                person["email"] = value
            elif "naroden" in label:
                person["born_on"] = value
            elif "obec" in label:
                person["municipality"] = value
            elif "kraj" in label:
                person["region"] = value
    except Exception:
        pass

    # in_parliament is set by caller based on whether the ID appeared in zoznam_abc

    # club memberships — structure: <h2>Členstvo</h2><ul><li>Klub X (role)</li>...</ul>
    # Extract only the <ul> immediately following ctlClenstvoLabel; filter to Klub rows only.
    try:
        m = re.search(
            r"ctlClenstvoLabel[^>]*>Členstvo</span></h2>\s*<ul>(.*?)</ul>",
            r.html.html,
            re.DOTALL,
        )
        if m:
            for li_m in re.finditer(r"<li>(.*?)</li>", m.group(1), re.DOTALL):
                text = re.sub(r"<[^>]+>", "", li_m.group(1)).strip()
                # keep only club rows (skip committees, committees start with "Výbor" etc.)
                if not text.startswith("Klub"):
                    continue
                club_name = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
                if club_name:
                    memberships.append({
                        "mp_id": mp_id,
                        "club_name": club_name,
                        "start_date": "",
                        "end_date": "",
                    })
    except Exception:
        pass

    return person, memberships


def main() -> None:
    session = requests_html.HTMLSession()

    logging.info("Fetching MP list from %s", _MP_LIST_URL)
    mp_ids, current_ids = _get_mp_ids(session)
    logging.info("Found %d MP IDs (%d current)", len(mp_ids), len(current_ids))

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
                person["in_parliament"] = mp_id in current_ids
                pw.writerow(person)
                for m in memberships:
                    mw.writerow(m)
            except Exception as e:
                logging.warning("Failed to scrape mp_id=%s: %s", mp_id, e)
            time.sleep(_DELAY)

    logging.info("Wrote %s and %s", persons_out, memberships_out)


if __name__ == "__main__":
    main()
