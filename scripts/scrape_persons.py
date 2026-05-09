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
from datetime import datetime
from pathlib import Path

import requests_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_ROOT = Path(__file__).resolve().parents[1]
_RAW = _ROOT / "work" / "raw"
_RAW.mkdir(parents=True, exist_ok=True)

_MP_LIST_URL = "https://www.nrsr.sk/web/Default.aspx?sid=poslanci/zoznam_abc"
_MP_CHANGES_URL = "https://www.nrsr.sk/web/Default.aspx?sid=poslanci/zmeny&CisObdobia=9"
_MP_DETAIL_URL = "https://www.nrsr.sk/web/Default.aspx?sid=poslanci/poslanec&PoslanecID={mp_id}&CisObdobia=9"
_DELAY = 0.5

_PERSONS_COLS = [
    "mp_id", "given_name", "family_name", "title", "born_on",
    "email", "municipality", "region", "in_parliament", "left_on",
]
_MEMBERSHIPS_COLS = ["mp_id", "club_name", "start_date", "end_date"]


def _get_mp_ids(session: requests_html.HTMLSession) -> tuple[list[str], set[str], dict[str, str], dict[str, str]]:
    """Return (all_ids, current_ids, list_name_map, departure_dates).

    all_ids includes current MPs plus former MPs who left mid-term (from zmeny page + votes CSVs).
    list_name_map maps mp_id → raw anchor text from zoznam_abc (e.g. 'Remišová Veronika').
    departure_dates maps mp_id → ISO departure date (e.g. '2025-03-22') for former MPs.
    """
    r = session.get(_MP_LIST_URL)
    mp_ids = []
    list_name_map: dict[str, str] = {}
    for a in r.html.find("a"):
        href = a.attrs.get("href", "")
        m = re.search(r"PoslanecID=(\d+)", href)
        if m:
            mp_id = m.group(1)
            mp_ids.append(mp_id)
            text = a.text.strip()
            if text:
                list_name_map[mp_id] = text
    current = set(dict.fromkeys(mp_ids))

    # Fetch mid-term changes page — captures former MPs and their departure dates
    r2 = session.get(_MP_CHANGES_URL)
    departure_dates: dict[str, str] = {}
    # Each row: <td>DD. M. YYYY</td><td><a ...PoslanecID=ID...>Name</a> (-)</td>
    for row_m in re.finditer(
        r"<td>(\d+\.\s*\d+\.\s*\d+)</td>\s*<td>(.*?)</td>",
        r2.html.html, re.DOTALL
    ):
        cell = row_m.group(2)
        # Only departure rows have "(-)"
        if "(-)" not in cell and "(−)" not in cell:
            continue
        id_m = re.search(r"PoslanecID=(\d+)", cell)
        if not id_m:
            continue
        mp_id = id_m.group(1)
        date_str = row_m.group(1).replace(" ", "")
        try:
            d = datetime.strptime(date_str, "%d.%m.%Y")
            departure_dates[mp_id] = d.strftime("%Y-%m-%d")
        except ValueError:
            pass
        if mp_id not in current:
            mp_ids.append(mp_id)

    # Also collect voter IDs from cached votes CSVs — catches former MPs not listed in zmeny
    seen = set(mp_ids)
    for votes_file in sorted(_RAW.glob("votes*.csv")):
        try:
            with open(votes_file, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    vid = (row.get("voter_id") or "").strip()
                    if vid and vid not in seen:
                        mp_ids.append(vid)
                        seen.add(vid)
        except Exception as e:
            logging.warning("Could not read %s for voter IDs: %s", votes_file, e)

    all_ids = list(dict.fromkeys(mp_ids))
    logging.info("Found %d current + %d former MP IDs", len(current), len(all_ids) - len(current))
    return all_ids, current, list_name_map, departure_dates


# Slovak pre-nominal academic titles to strip from h1 name
_PRENOMINAL_TITLES = {
    "prof.", "doc.", "mgr.", "ing.", "judr.", "mudr.", "phdr.", "rndr.",
    "paedr.", "paeddr.", "thdr.", "bc.", "mvdr.", "pharm.dr.", "dipl.",
    "art.", "artd.", "rsdr.", "gen.", "v.", "dis.",
}

# Latin conjunctions that appear between titles (e.g. "Mgr. et Mgr.") — not name tokens
_TITLE_CONJUNCTIONS = {"et"}


def _parse_name(full: str) -> tuple[str, str, str]:
    """Parse 'MUDr. Vladimír Baláž, PhD.' → (given_name, family_name, title).

    Handles 'Mgr., Mgr. Dagmar Kramplová' where the first comma separates two
    pre-nominal titles rather than the name from post-nominal titles.
    """
    if "," in full:
        name_part, _, post = full.partition(",")
        title_after = post.strip()
    else:
        name_part = full
        title_after = ""

    tokens = name_part.strip().split()
    pre_titles = []
    name_tokens = []
    consuming_titles = True
    for tok in tokens:
        if tok.lower() in _PRENOMINAL_TITLES:
            pre_titles.append(tok)
        elif consuming_titles and tok.lower() in _TITLE_CONJUNCTIONS:
            # "et" between pre-nominal titles (e.g. "Mgr. et Mgr.") — skip
            pass
        else:
            consuming_titles = False
            name_tokens.append(tok)

    # If the first comma split off only pre-nominal titles (no name found),
    # the real name is in title_after — parse it too.
    if not name_tokens and title_after:
        extra = title_after.split()
        extra_pre, extra_name = [], []
        consuming_titles = True
        for tok in extra:
            if tok.lower() in _PRENOMINAL_TITLES:
                extra_pre.append(tok)
            elif consuming_titles and tok.lower() in _TITLE_CONJUNCTIONS:
                pass
            else:
                consuming_titles = False
                extra_name.append(tok)
        pre_titles.extend(extra_pre)
        name_tokens = extra_name
        title_after = ""

    title = " ".join(pre_titles) + (f", {title_after}" if title_after else "")
    given = name_tokens[0] if name_tokens else ""
    family = name_tokens[-1] if len(name_tokens) > 1 else name_tokens[0] if name_tokens else ""
    return given, family, title.strip(", ")


_H1_PREFIXES = [
    ("Meno ",          "given_name"),
    ("Priezvisko ",    "family_name"),
    ("Titul ",         "title"),
    ("Narodený(á) ",   "born_on"),
    ("Narodený ",      "born_on"),
    ("Bydlisko ",      "municipality"),
    ("Kraj ",          "region"),
]


def _scrape_mp(session: requests_html.HTMLSession, mp_id: str) -> tuple[dict, list[dict]]:
    url = _MP_DETAIL_URL.format(mp_id=mp_id)
    r = session.get(url)

    person: dict = {"mp_id": mp_id, "in_parliament": False}
    memberships: list[dict] = []

    try:
        h1 = r.html.find("h1", first=True)
        if h1:
            lines = [ln.strip() for ln in h1.text.strip().splitlines() if ln.strip()]

            # First line is the formatted title+name (e.g. "Mgr. Martina Bajo Holečková")
            if lines:
                given, family, title = _parse_name(lines[0])
                person["given_name"] = given
                person["family_name"] = family
                person["title"] = title

            # Remaining lines: "Label Value" pairs — override h1 parse with explicit fields
            for line in lines[1:]:
                lower = line.lower()
                if "e-mail" in lower and " " in line:
                    person["email"] = line.split(None, 1)[1].strip()
                    continue
                for prefix, field in _H1_PREFIXES:
                    if line.startswith(prefix):
                        person[field] = line[len(prefix):].strip()
                        break
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
    mp_ids, current_ids, list_name_map, departure_dates = _get_mp_ids(session)
    logging.info("Found %d MP IDs (%d current)", len(mp_ids), len(current_ids))

    persons_out = _RAW / "persons_raw.csv"
    memberships_out = _RAW / "memberships_raw.csv"

    with open(persons_out, "w", newline="", encoding="utf-8") as pf, \
         open(memberships_out, "w", newline="", encoding="utf-8") as mf:
        pw = csv.DictWriter(pf, fieldnames=_PERSONS_COLS, extrasaction="ignore")
        mw = csv.DictWriter(mf, fieldnames=_MEMBERSHIPS_COLS, extrasaction="ignore")
        pw.writeheader()
        mw.writeheader()

        current_with_club: set[str] = set()

        for i, mp_id in enumerate(mp_ids):
            if i % 20 == 0:
                logging.info("Scraping MP %d/%d (id=%s)", i + 1, len(mp_ids), mp_id)
            try:
                person, memberships = _scrape_mp(session, mp_id)
                person["in_parliament"] = mp_id in current_ids
                person["left_on"] = departure_dates.get(mp_id, "")
                # Fallback: if given_name looks wrong, use the list-page anchor text
                # List format: "Remišová Veronika" (family first, given last)
                given = person.get("given_name", "")
                family = person.get("family_name", "")
                name_looks_bad = (
                    not given
                    or given == family
                    or given.lower() in _PRENOMINAL_TITLES
                    or (len(given) <= 2 and given.endswith("."))
                )
                if name_looks_bad and mp_id in list_name_map:
                    parts = list_name_map[mp_id].split()
                    if len(parts) >= 2:
                        person["family_name"] = parts[0].rstrip(",")
                        person["given_name"] = " ".join(parts[1:])
                        logging.info(
                            "Name fallback mp_id=%s: '%s %s' → '%s %s'",
                            mp_id, given, family, person["given_name"], person["family_name"],
                        )
                pw.writerow(person)
                for m in memberships:
                    mw.writerow(m)
                if memberships and mp_id in current_ids:
                    current_with_club.add(mp_id)
            except Exception as e:
                logging.warning("Failed to scrape mp_id=%s: %s", mp_id, e)
            time.sleep(_DELAY)

        # current MPs with no club → "Nezávislí"
        for mp_id in sorted(current_ids - current_with_club):
            mw.writerow({"mp_id": mp_id, "club_name": "Nezávislí", "start_date": "", "end_date": ""})
            logging.info("Assigned Nezávislí to current MP %s (no club found)", mp_id)

    logging.info("Wrote %s and %s", persons_out, memberships_out)


if __name__ == "__main__":
    main()
