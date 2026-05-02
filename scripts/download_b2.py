import json
import logging
from pathlib import Path

import requests


def _public_b2_url(bucket: str, key: str) -> str:
    key = key.lstrip("/")
    return f"https://f000.backblazeb2.com/file/{bucket}/{key}"


def download_public_b2_file(*, bucket: str, key: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    url = _public_b2_url(bucket, key)
    logging.info("Downloading %s -> %s", url, out_path)
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    return out_path


def download_latest_from_pointer(*, pointer_path: Path, out_path: Path) -> Path:
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    locations = pointer.get("locations") or []
    if not locations:
        raise ValueError(f"{pointer_path} has empty locations; cannot download")
    loc = locations[0]
    if loc.get("provider") != "b2":
        raise ValueError(f"{pointer_path}: unsupported provider {loc.get('provider')}")
    bucket = loc.get("bucket")
    key = loc.get("key")
    if not bucket or not key:
        raise ValueError(f"{pointer_path}: missing bucket/key in first location")
    return download_public_b2_file(bucket=bucket, key=key, out_path=out_path)
