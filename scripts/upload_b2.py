"""Upload a local file to Backblaze B2 and update data/<dataset>/latest.json pointer.

Usage (called from standardize.py or run_pipeline.py, not directly):
  python scripts/upload_b2.py --local work/standard/votes.parquet \
      --remote legislatures/sk-nrsr-data-2023-202x/votes/snapshots/votes-20240101.parquet \
      --pointer data/votes/latest.json \
      --prune-prefix legislatures/sk-nrsr-data-2023-202x/votes/snapshots/
"""

import argparse
import datetime
import hashlib
import json
import logging
import os
from pathlib import Path

import requests

from scripts.utils_env import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _b2_env() -> tuple[str, str, str] | None:
    load_dotenv(_REPO_ROOT)
    key_id = os.getenv("B2_KEY_ID")
    app_key = os.getenv("B2_APP_KEY")
    bucket = os.getenv("B2_BUCKET")
    if not key_id or not app_key or not bucket:
        return None
    return key_id, app_key, bucket


def _b2_authorize(key_id: str, app_key: str) -> dict:
    r = requests.get(
        "https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
        auth=(key_id, app_key),
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def _b2_get_upload_url(api_url: str, auth_token: str, bucket_id: str) -> dict:
    r = requests.post(
        f"{api_url}/b2api/v2/b2_get_upload_url",
        headers={"Authorization": auth_token},
        json={"bucketId": bucket_id},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def _b2_list_file_names(
    api_url: str,
    auth_token: str,
    bucket_id: str,
    *,
    prefix: str,
    start_file_name: str | None = None,
    max_file_count: int = 1000,
) -> dict:
    payload: dict = {"bucketId": bucket_id, "prefix": prefix, "maxFileCount": max_file_count}
    if start_file_name:
        payload["startFileName"] = start_file_name
    r = requests.post(
        f"{api_url}/b2api/v2/b2_list_file_names",
        headers={"Authorization": auth_token},
        json=payload,
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def _b2_delete_file_version(api_url: str, auth_token: str, *, file_name: str, file_id: str) -> dict:
    r = requests.post(
        f"{api_url}/b2api/v2/b2_delete_file_version",
        headers={"Authorization": auth_token},
        json={"fileName": file_name, "fileId": file_id},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def _b2_list_buckets(api_url: str, auth_token: str, account_id: str) -> dict:
    r = requests.post(
        f"{api_url}/b2api/v2/b2_list_buckets",
        headers={"Authorization": auth_token},
        json={"accountId": account_id},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def upload_file(local_path: Path, remote_name: str) -> str:
    """Upload local_path to B2 at remote_name. Returns the public URL."""
    env = _b2_env()
    if env is None:
        logging.info("B2 env vars not set; skipping upload")
        return ""

    key_id, app_key, bucket_name = env
    auth = _b2_authorize(key_id, app_key)
    api_url = auth["apiUrl"]
    auth_token = auth["authorizationToken"]
    account_id = auth["accountId"]

    bucket_id = os.getenv("B2_BUCKET_ID")
    if not bucket_id:
        result = _b2_list_buckets(api_url, auth_token, account_id)
        for b in result.get("buckets", []):
            if b.get("bucketName") == bucket_name:
                bucket_id = b.get("bucketId")
                break
        if not bucket_id:
            raise ValueError(f"B2 bucket not found: {bucket_name}")

    upload_info = _b2_get_upload_url(api_url, auth_token, bucket_id)
    upload_url = upload_info["uploadUrl"]
    upload_auth = upload_info["authorizationToken"]

    sha1 = _sha1(local_path)
    with open(local_path, "rb") as f:
        r = requests.post(
            upload_url,
            headers={
                "Authorization": upload_auth,
                "X-Bz-File-Name": remote_name,
                "Content-Type": "b2/x-auto",
                "X-Bz-Content-Sha1": sha1,
            },
            data=f,
            timeout=300,
        )
    r.raise_for_status()
    logging.info("Uploaded %s -> b2://%s/%s", local_path, bucket_name, remote_name)
    return f"https://f000.backblazeb2.com/file/{bucket_name}/{remote_name}"


def prune_snapshots(prefix: str, *, keep: int = 5) -> None:
    env = _b2_env()
    if env is None:
        return

    key_id, app_key, bucket_name = env
    auth = _b2_authorize(key_id, app_key)
    api_url = auth["apiUrl"]
    auth_token = auth["authorizationToken"]
    account_id = auth["accountId"]

    bucket_id = os.getenv("B2_BUCKET_ID")
    if not bucket_id:
        result = _b2_list_buckets(api_url, auth_token, account_id)
        for b in result.get("buckets", []):
            if b.get("bucketName") == bucket_name:
                bucket_id = b.get("bucketId")
                break
        if not bucket_id:
            raise ValueError(f"B2 bucket not found: {bucket_name}")

    files: list[dict] = []
    start: str | None = None
    while True:
        page = _b2_list_file_names(api_url, auth_token, bucket_id, prefix=prefix, start_file_name=start)
        items = page.get("files", []) or []
        files.extend(items)
        start = page.get("nextFileName")
        if not start:
            break

    if len(files) <= keep:
        return

    files_sorted = sorted(
        files,
        key=lambda f: (-(int(f.get("uploadTimestamp") or 0)), str(f.get("fileName") or "")),
    )
    for f in files_sorted[keep:]:
        fn = f.get("fileName")
        fid = f.get("fileId")
        if fn and fid:
            _b2_delete_file_version(api_url, auth_token, file_name=fn, file_id=fid)
            logging.info("Deleted old snapshot b2://%s/%s", bucket_name, fn)


def write_pointer(pointer_path: Path, *, bucket: str, remote_name: str) -> None:
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer = {
        "locations": [
            {
                "provider": "b2",
                "bucket": bucket,
                "key": remote_name,
                "updated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        ]
    }
    pointer_path.write_text(json.dumps(pointer, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logging.info("Updated pointer %s", pointer_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", required=True)
    parser.add_argument("--remote", required=True)
    parser.add_argument("--pointer", default=None, help="data/<dataset>/latest.json to update")
    parser.add_argument("--prune-prefix", default=None)
    parser.add_argument("--keep", type=int, default=5)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    url = upload_file(Path(args.local), args.remote)
    if args.prune_prefix:
        prune_snapshots(args.prune_prefix, keep=args.keep)
    if args.pointer and url:
        env = _b2_env()
        if env:
            write_pointer(Path(args.pointer), bucket=env[2], remote_name=args.remote)
