"""Main pipeline: scrape → standardize → upload to B2."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"


def run(script: Path) -> None:
    result = subprocess.run([sys.executable, str(script)], check=True)
    if result.returncode != 0:
        sys.exit(result.returncode)


if __name__ == "__main__":
    run(SCRIPTS / "scrape_persons.py")
    run(SCRIPTS / "scrape_votes.py")
    run(SCRIPTS / "standardize.py")
