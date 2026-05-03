"""Download raw CSVs from B2 before scraping so incremental runs work on fresh checkouts."""

import logging
import os
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parents[1]

import sys
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.utils_env import load_dotenv

load_dotenv(_ROOT)

_LEGISLATURE_B2_PREFIX = "legislatures/sk-nrsr-data-2023-202x"
_RAW = _ROOT / "work" / "raw"

_RAW_FILES = [
    "vote_events_raw.csv",
    "votes_raw.csv",
    "persons_raw.csv",
    "memberships_raw.csv",
]


def _download(bucket: str, key: str, out_path: Path) -> bool:
    url = f"https://f000.backblazeb2.com/file/{bucket}/{key}"
    try:
        with requests.get(url, stream=True, timeout=300) as r:
            if r.status_code == 404:
                logging.info("Not found in B2 (first run?): %s", key)
                return False
            r.raise_for_status()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        logging.info("Downloaded %s -> %s", key, out_path)
        return True
    except requests.exceptions.RequestException as exc:
        logging.warning("Could not download %s: %s — scraper will start from scratch", key, exc)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    bucket = os.getenv("B2_BUCKET")
    if not bucket:
        logging.info("B2_BUCKET not set — skipping raw download")
        raise SystemExit(0)

    for name in _RAW_FILES:
        out = _RAW / name
        if out.exists():
            logging.info("Already present locally, skipping: %s", name)
            continue
        _download(bucket, f"{_LEGISLATURE_B2_PREFIX}/raw/{name}", out)
