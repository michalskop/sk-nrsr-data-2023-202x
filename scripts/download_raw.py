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


def _b2_authorize(key_id: str, app_key: str) -> dict:
    r = requests.get(
        "https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
        auth=(key_id, app_key),
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def _download(download_url: str, auth_token: str, bucket: str, key: str, out_path: Path) -> bool:
    url = f"{download_url}/file/{bucket}/{key}"
    try:
        with requests.get(url, headers={"Authorization": auth_token}, stream=True, timeout=300) as r:
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

    key_id = os.getenv("B2_KEY_ID")
    app_key = os.getenv("B2_APP_KEY")
    bucket = os.getenv("B2_BUCKET")

    if not key_id or not app_key or not bucket:
        logging.info("B2 credentials not set — skipping raw download")
        raise SystemExit(0)

    try:
        auth = _b2_authorize(key_id, app_key)
    except requests.exceptions.RequestException as exc:
        logging.warning("B2 auth failed: %s — skipping raw download", exc)
        raise SystemExit(0)

    download_url = auth["downloadUrl"]
    auth_token = auth["authorizationToken"]

    for name in _RAW_FILES:
        out = _RAW / name
        _download(download_url, auth_token, bucket, f"{_LEGISLATURE_B2_PREFIX}/raw/{name}", out)
