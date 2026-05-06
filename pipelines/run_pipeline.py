"""Main pipeline: scrape → standardize → upload to B2.

Env vars:
  MAX_NEW_VOTES  — cap new vote events scraped per run (for testing)
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"


def run(script: Path, *extra_args: str) -> None:
    result = subprocess.run([sys.executable, str(script), *extra_args], check=True)
    if result.returncode != 0:
        sys.exit(result.returncode)


if __name__ == "__main__":
    max_new = os.getenv("MAX_NEW_VOTES")
    votes_args = ("--max-new", max_new) if max_new else ()

    run(SCRIPTS / "download_raw.py")  # restore raw CSVs from B2 if not already cached locally
    run(SCRIPTS / "scrape_persons.py")
    run(SCRIPTS / "scrape_votes.py", *votes_args)
    run(SCRIPTS / "standardize.py")
