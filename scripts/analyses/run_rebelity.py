"""Run the rebelity analysis for sk-nrsr-data-2023-202x.

Usage:
  python scripts/analyses/run_rebelity.py \\
      --script /tmp/legislature-data-analyses/rebelity/rebelity.py \\
      --flourish-script /tmp/legislature-data-analyses/rebelity/outputs/output_flourish_table.py
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

_DEFINITION  = _REPO_ROOT / "analyses/rebelity/rebelity_definition.json"
_VOTES       = _REPO_ROOT / "work/standard/votes.csv"
_VOTE_EVENTS = _REPO_ROOT / "work/standard/vote_events.json"
_PERSONS     = _REPO_ROOT / "analyses/all-members/outputs/all_members.json"
_OUTPUT_JSON = _REPO_ROOT / "analyses/rebelity/outputs/rebelity.json"
_OUTPUT_CSV  = _REPO_ROOT / "analyses/rebelity/outputs/rebelity_flourish_table.csv"
_WORK_DIR    = _REPO_ROOT / "work/b2-cache"


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--flourish-script", required=True, dest="flourish_script")
    parser.add_argument("--definition", default=str(_DEFINITION))
    parser.add_argument("--votes", default=str(_VOTES))
    parser.add_argument("--vote-events", dest="vote_events", default=str(_VOTE_EVENTS))
    parser.add_argument("--persons", default=str(_PERSONS))
    parser.add_argument("--output", default=str(_OUTPUT_JSON))
    parser.add_argument("--flourish-output", dest="flourish_output", default=str(_OUTPUT_CSV))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _WORK_DIR.mkdir(parents=True, exist_ok=True)

    _ensure_votes_csv(Path(args.votes))
    _ensure_vote_events_json(Path(args.vote_events))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run([
        sys.executable, args.script,
        "--definition", args.definition,
        "--votes", args.votes,
        "--vote_events", args.vote_events,
        "--persons", args.persons,
        "--output", str(output),
    ], check=True)

    flourish_output = Path(args.flourish_output)
    flourish_output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        sys.executable, args.flourish_script,
        "--input", str(output),
        "--output", str(flourish_output),
    ], check=True)

    logging.info("Rebelity analysis done. Output: %s", output)


if __name__ == "__main__":
    main()
