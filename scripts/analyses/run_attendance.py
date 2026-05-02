"""Run the attendance analysis for sk-nrsr-data-2023-202x.

Usage:
  python scripts/analyses/run_attendance.py \\
      --script /tmp/legislature-data-analyses/attendance/attendance.py \\
      --flourish-script /tmp/legislature-data-analyses/attendance/outputs/output_flourish_table.py \\
      [--use-current-members]
"""

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.download_b2 import download_latest_from_pointer

_DEFINITION   = _REPO_ROOT / "analyses/attendance/attendance_definition.json"
_VOTES        = _REPO_ROOT / "work/standard/votes.csv"
_VOTE_EVENTS  = _REPO_ROOT / "work/standard/vote_events.json"
_PERSONS      = _REPO_ROOT / "analyses/all-members/outputs/all_members.csv"
_CUR_MEMBERS  = _REPO_ROOT / "analyses/current-members/outputs/current_members.csv"
_OUTPUT_JSON  = _REPO_ROOT / "analyses/attendance/outputs/attendance.json"
_OUTPUT_CSV   = _REPO_ROOT / "analyses/attendance/outputs/attendance_flourish_table.csv"
_WORK_DIR     = _REPO_ROOT / "work/b2-cache"


def _ensure_votes_csv(path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    import pyarrow.parquet as pq
    parquet = _WORK_DIR / "votes.latest.parquet"
    download_latest_from_pointer(pointer_path=_REPO_ROOT / "data/votes/latest.json", out_path=parquet)
    table = pq.read_table(parquet)
    path.parent.mkdir(parents=True, exist_ok=True)
    table.to_pandas().to_csv(path, index=False)


def _ensure_vote_events_json(path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    import pyarrow.parquet as pq
    parquet = _WORK_DIR / "vote_events.latest.parquet"
    download_latest_from_pointer(
        pointer_path=_REPO_ROOT / "data/vote-events/latest.json", out_path=parquet
    )
    table = pq.read_table(parquet)
    records = table.to_pylist()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _filter_votes_for_persons(*, votes_in: Path, persons_csv: Path, votes_out: Path) -> None:
    import csv as _csv
    ids: set[str] = set()
    with open(persons_csv, newline="", encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            pid = (row.get("id") or "").strip()
            if pid:
                ids.add(pid)
    if not ids:
        raise ValueError(f"No person ids found in {persons_csv}")
    votes_out.parent.mkdir(parents=True, exist_ok=True)
    with open(votes_in, newline="", encoding="utf-8") as fin, \
         open(votes_out, "w", newline="", encoding="utf-8") as fout:
        reader = _csv.DictReader(fin)
        writer = _csv.DictWriter(fout, fieldnames=reader.fieldnames or [])
        writer.writeheader()
        for row in reader:
            if (row.get("voter_id") or "").strip() in ids:
                writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--flourish-script", required=True, dest="flourish_script")
    parser.add_argument("--definition", default=str(_DEFINITION))
    parser.add_argument("--votes", default=str(_VOTES))
    parser.add_argument("--vote-events", dest="vote_events", default=str(_VOTE_EVENTS))
    parser.add_argument("--persons", default=str(_PERSONS))
    parser.add_argument("--current-members", dest="current_members", default=str(_CUR_MEMBERS))
    parser.add_argument("--use-current-members", action="store_true")
    parser.add_argument("--output", default=str(_OUTPUT_JSON))
    parser.add_argument("--flourish-output", dest="flourish_output", default=str(_OUTPUT_CSV))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _WORK_DIR.mkdir(parents=True, exist_ok=True)

    votes = Path(args.votes)
    vote_events = Path(args.vote_events)
    _ensure_votes_csv(votes)
    _ensure_vote_events_json(vote_events)

    persons = Path(args.persons)
    if args.use_current_members:
        cur = Path(args.current_members)
        filtered = _WORK_DIR / "persons.current.csv"
        import csv as _csv
        cur_ids: set[str] = set()
        with open(cur, newline="", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                pid = (row.get("id") or "").strip()
                if pid:
                    cur_ids.add(pid)
        with open(persons, newline="", encoding="utf-8") as fin, \
             open(filtered, "w", newline="", encoding="utf-8") as fout:
            reader = _csv.DictReader(fin)
            writer = _csv.DictWriter(fout, fieldnames=reader.fieldnames or [])
            writer.writeheader()
            for row in reader:
                if (row.get("id") or "").strip() in cur_ids:
                    writer.writerow(row)
        persons = filtered

    votes_for_run = _WORK_DIR / "votes.for_attendance.csv"
    _filter_votes_for_persons(votes_in=votes, persons_csv=persons, votes_out=votes_for_run)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run([
        sys.executable, args.script,
        "--definition", args.definition,
        "--votes", str(votes_for_run),
        "--vote_events", str(vote_events),
        "--persons", str(persons),
        "--output", str(output),
    ], check=True)

    flourish_output = Path(args.flourish_output)
    flourish_output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        sys.executable, args.flourish_script,
        "--input", str(output),
        "--output", str(flourish_output),
    ], check=True)

    logging.info("Attendance analysis done. Output: %s", output)


if __name__ == "__main__":
    main()
