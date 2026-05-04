"""Scrape vote events and individual votes from nrsr.sk for the IX term (2023–).

Outputs (incremental — appends new rows on each run):
  work/raw/vote_events_raw.csv
  work/raw/votes_raw.csv

Run from the sk-nrsr-data-2023-202x/ directory:
  python scripts/scrape_votes.py [--start N] [--end N]

--start defaults to the config value (51426, first IX term vote).
--end   defaults to auto-detection via binary search on nrsr.sk.
"""

import argparse
import csv
import datetime
import logging
import re
import time
from pathlib import Path

import requests

import requests_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_ROOT = Path(__file__).resolve().parents[1]
_RAW = _ROOT / "work" / "raw"
_RAW.mkdir(parents=True, exist_ok=True)

_ORG_ID = 13
_VOTE_EVENT_URL = "https://www.nrsr.sk/web/Default.aspx?sid=schodze/hlasovanie/hlasklub&ID={}"
_DELAY = 0.5
_CHECKPOINT_EVERY = 50

_OPTION_MAP = {
    "Z": "yes",
    "P": "no",
    "?": "abstain",
    "N": "not voting",
    "0": "absent",
    "-": "not member",
}

_VOTE_EVENTS_COLS = [
    "vote_event_id", "org_id", "sitting", "date", "time",
    "vote_event_number", "name", "result",
    "present", "voted", "yes", "no", "abstain", "not voting", "absent",
]
_VOTES_COLS = ["vote_event_id", "voter_id", "option", "group"]


def _load_existing_ids(path: Path) -> set[int]:
    if not path.exists():
        return set()
    ids: set[int] = set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ids.add(int(row["vote_event_id"]))
            except (KeyError, ValueError):
                pass
    return ids


def _detect_max_id(session: requests_html.HTMLSession, start: int) -> int:
    """Binary search for the highest valid vote_event_id on nrsr.sk."""
    lo, hi = start, start + 20000
    # First, verify hi is actually beyond the end
    while True:
        r = session.get(_VOTE_EVENT_URL.format(hi))
        if _is_valid_vote_page(r):
            hi *= 2
        else:
            break
    # Binary search
    while lo < hi - 1:
        mid = (lo + hi) // 2
        r = session.get(_VOTE_EVENT_URL.format(mid))
        time.sleep(_DELAY)
        if _is_valid_vote_page(r):
            lo = mid
        else:
            hi = mid
    logging.info("Detected max vote_event_id: %d", lo)
    return lo


def _is_valid_vote_page(r: requests_html.HTMLResponse) -> bool:
    try:
        return bool(r.html.find(".voting_stats_summary_full", first=True))
    except Exception:
        return False


def _scrape_vote_event(
    session: requests_html.HTMLSession,
    vote_event_id: int,
) -> tuple[dict | None, list[dict]]:
    url = _VOTE_EVENT_URL.format(vote_event_id)
    for attempt in range(4):
        try:
            r = session.get(url)
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            if attempt == 3:
                raise
            wait = 10 * 2 ** attempt
            logging.warning("Request failed (%s), retrying in %ds...", exc, wait)
            time.sleep(wait)

    if not _is_valid_vote_page(r):
        return None, []

    ve: dict = {"vote_event_id": vote_event_id, "org_id": _ORG_ID}
    votes: list[dict] = []

    try:
        sitting_el = r.html.find("#_sectionLayoutContainer_ctl01_ctl00__schodzaLink", first=True)
        if sitting_el:
            ve["sitting"] = re.findall(r"\d+", sitting_el.text.split("\n")[0])[0]

        table = r.html.find(".voting_stats_summary_full", first=True)

        dt_text = table.find(".grid_4")[1].find("span", first=True).text
        dt_obj = datetime.datetime.strptime(dt_text, "%d. %m. %Y %H:%M")
        ve["date"] = dt_obj.date().isoformat()
        ve["time"] = dt_obj.time().isoformat()

        ve["vote_event_number"] = table.find(".grid_4")[2].find("span", first=True).text
        ve["name"] = table.find(".grid_12", first=True).find("span", first=True).text

        result_text = table.find(
            "#_sectionLayoutContainer_ctl01_ctl00__votingResultCell", first=True
        ).find("span", first=True).text
        ve["result"] = "pass" if result_text == "Návrh prešiel" else "fail"

        try:
            numbers = table.find(
                "#_sectionLayoutContainer_ctl01_ctl00__resultsTablePanel", first=True
            )
            spans = numbers.find("span")
            for key, idx in [
                ("present", 0), ("voted", 1), ("yes", 2),
                ("no", 3), ("abstain", 4), ("not voting", 5), ("absent", 6),
            ]:
                ve[key] = spans[idx].text
        except Exception:
            pass

    except Exception as e:
        logging.debug("vote_event_id=%d metadata error: %s", vote_event_id, e)
        return None, []

    try:
        vtable = r.html.find("#_sectionLayoutContainer_ctl01__resultsTable", first=True)
        group_name = ""
        for cell in vtable.find("td"):
            try:
                _ = cell.attrs["class"]
                group_name = cell.text
            except KeyError:
                try:
                    a = cell.find("a", first=True)
                    voter_id = re.findall(r"ID=(\d+)", a.attrs["href"])[0]
                    option_raw = re.findall(r"\[(.*)\]", cell.text)[0]
                    option = _OPTION_MAP.get(option_raw, option_raw)
                    votes.append({
                        "vote_event_id": vote_event_id,
                        "voter_id": voter_id,
                        "option": option,
                        "group": group_name,
                    })
                except Exception:
                    pass
    except Exception:
        pass

    return ve, votes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=51426,
                        help="First vote_event_id to scrape (IX term start)")
    parser.add_argument("--end", type=int, default=None,
                        help="Last vote_event_id (auto-detected if omitted)")
    args = parser.parse_args()

    session = requests_html.HTMLSession()

    ve_path = _RAW / "vote_events_raw.csv"
    v_path = _RAW / "votes_raw.csv"

    existing_ids = _load_existing_ids(ve_path)
    logging.info("Already have %d vote events", len(existing_ids))

    if args.end is None:
        start_for_detect = max(existing_ids, default=args.start)
        end_id = _detect_max_id(session, start_for_detect)
    else:
        end_id = args.end

    ids_to_fetch = [i for i in range(args.start, end_id + 1) if i not in existing_ids]
    logging.info("Will scrape %d vote events (%d–%d)", len(ids_to_fetch), args.start, end_id)

    ve_file_exists = ve_path.exists()
    v_file_exists = v_path.exists()

    with open(ve_path, "a", newline="", encoding="utf-8") as vef, \
         open(v_path, "a", newline="", encoding="utf-8") as vf:
        vew = csv.DictWriter(vef, fieldnames=_VOTE_EVENTS_COLS, extrasaction="ignore")
        vw = csv.DictWriter(vf, fieldnames=_VOTES_COLS, extrasaction="ignore")
        if not ve_file_exists:
            vew.writeheader()
        if not v_file_exists:
            vw.writeheader()

        for i, ve_id in enumerate(ids_to_fetch):
            if i % _CHECKPOINT_EVERY == 0 and i > 0:
                logging.info("Progress: %d/%d (vote_event_id=%d)", i, len(ids_to_fetch), ve_id)

            ve, votes = _scrape_vote_event(session, ve_id)
            if ve is not None:
                vew.writerow(ve)
                for v in votes:
                    vw.writerow(v)
                vef.flush()
                vf.flush()

            time.sleep(_DELAY)

    logging.info("Done. vote_events: %s, votes: %s", ve_path, v_path)


if __name__ == "__main__":
    main()
