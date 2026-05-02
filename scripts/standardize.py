"""Convert raw scraped CSVs to DT standard format and upload snapshots to B2.

Reads:
  work/raw/vote_events_raw.csv
  work/raw/votes_raw.csv
  work/raw/persons_raw.csv
  work/raw/memberships_raw.csv

Writes:
  work/standard/vote_events.json
  work/standard/votes.csv
  work/standard/persons.csv
  work/standard/organizations.csv
  work/standard/memberships.csv
  analyses/all-members/outputs/all_members.json  (+ .csv)
  analyses/all-groups/outputs/all_groups.json    (+ .csv)
  analyses/current-members/outputs/current_members.json  (+ .csv)
  analyses/current-groups/outputs/current_groups.json    (+ .csv)
  analyses/current-term/outputs/current_term.json

Also uploads each dataset to B2 and updates data/*/latest.json pointers.
"""

import csv
import datetime
import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.upload_b2 import upload_file, write_pointer, prune_snapshots
from scripts.utils_env import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
load_dotenv(_ROOT)

_RAW = _ROOT / "work" / "raw"
_STD = _ROOT / "work" / "standard"
_STD.mkdir(parents=True, exist_ok=True)

_NRSR_ORG_ID = 13
_LEGISLATURE_B2_PREFIX = "legislatures/sk-nrsr-data-2023-202x"
_TERM_START = "2023-10-25"
_PHOTO_URL = "https://www.nrsr.sk/web/dynamic/PoslanecPhoto.aspx?PoslanecID={mp_id}&ImageWidth=140"
_MP_DETAIL_URL = "https://www.nrsr.sk/web/Default.aspx?sid=poslanci/poslanec&PoslanecID={mp_id}"
_VOTE_EVENT_URL = "https://www.nrsr.sk/web/Default.aspx?sid=schodze/hlasovanie/hlasklub&ID={}"


def _nrsr_person_id(mp_id) -> str:
    return f"nrsr:person:{mp_id}"


def _nrsr_org_id(org_id) -> str:
    return f"nrsr:org:{org_id}"


def _nrsr_vote_event_id(ve_id) -> str:
    return f"nrsr:vote-event:{ve_id}"


def _nrsr_membership_id(mp_id, org_id, start) -> str:
    return f"nrsr:membership:{mp_id}:{org_id}:{start or ''}"


# ── vote_events ────────────────────────────────────────────────────────────────

def build_vote_events() -> None:
    raw = pd.read_csv(_RAW / "vote_events_raw.csv", dtype=str).fillna("")
    raw = raw.drop_duplicates("vote_event_id")

    records = []
    for _, row in raw.iterrows():
        ve_id = row["vote_event_id"]
        date = row.get("date", "")
        time_ = row.get("time", "")
        start_date = f"{date}T{time_}" if date and time_ else date
        extras: dict = {}
        if row.get("sitting"):
            extras["sitting_number"] = row["sitting"]
        if row.get("vote_event_number"):
            extras["voting_number"] = row["vote_event_number"]
        record = {
            "id": _nrsr_vote_event_id(ve_id),
            "identifier": ve_id,
            "organization_id": _nrsr_org_id(_NRSR_ORG_ID),
            "start_date": start_date,
            "result": row.get("result", ""),
            "text": row.get("name", ""),
            "extras": extras,
            "sources": [{"url": _VOTE_EVENT_URL.format(ve_id)}],
            "counts": [
                {"option": k, "value": int(row[k]) if row.get(k) else 0}
                for k in ("yes", "no", "abstain", "not voting", "absent")
                if row.get(k)
            ],
        }
        records.append(record)

    out = _STD / "vote_events.json"
    out.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logging.info("Wrote %s (%d records)", out, len(records))

    parquet_path = _STD / "vote_events.parquet"
    table = pa.Table.from_pylist([
        {
            "id": r["id"], "identifier": r["identifier"],
            "organization_id": r["organization_id"],
            "start_date": r["start_date"], "result": r["result"],
            "text": r.get("text", ""),
        }
        for r in records
    ])
    pq.write_table(table, parquet_path)


# ── votes ──────────────────────────────────────────────────────────────────────

def build_votes() -> None:
    raw = pd.read_csv(_RAW / "votes_raw.csv", dtype=str).fillna("")
    raw = raw.drop_duplicates(["vote_event_id", "voter_id"])
    # drop "not member" rows — person was not yet/no longer a member
    raw = raw[raw["option"] != "not member"]

    out_csv = _STD / "votes.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["vote_event_id", "voter_id", "option"])
        w.writeheader()
        for _, row in raw.iterrows():
            w.writerow({
                "vote_event_id": _nrsr_vote_event_id(row["vote_event_id"]),
                "voter_id": _nrsr_person_id(row["voter_id"]),
                "option": row["option"],
            })
    logging.info("Wrote %s (%d rows)", out_csv, len(raw))

    table = pa.Table.from_pandas(pd.DataFrame({
        "vote_event_id": raw["vote_event_id"].map(_nrsr_vote_event_id),
        "voter_id": raw["voter_id"].map(_nrsr_person_id),
        "option": raw["option"],
    }))
    pq.write_table(table, _STD / "votes.parquet")


# ── persons, organizations, memberships ───────────────────────────────────────

def build_persons_and_memberships() -> None:
    persons_raw = pd.read_csv(_RAW / "persons_raw.csv", dtype=str).fillna("")
    memberships_raw = pd.read_csv(_RAW / "memberships_raw.csv", dtype=str).fillna("")

    # ── organizations ──────────────────────────────────────────────────────────
    parliament_row = {
        "id": _nrsr_org_id(_NRSR_ORG_ID),
        "identifier": str(_NRSR_ORG_ID),
        "name": "Národná rada Slovenskej republiky",
        "classification": "legislature",
    }

    clubs = memberships_raw["club_name"].dropna().unique()
    club_rows = []
    for i, club in enumerate(sorted(set(clubs))):
        club_rows.append({
            "id": f"nrsr:org:club:{i+1}",
            "identifier": club,
            "name": club,
            "classification": "group",
        })
    club_id_map = {r["name"]: r["id"] for r in club_rows}

    orgs_df = pd.DataFrame([parliament_row] + club_rows)
    orgs_csv = _STD / "organizations.csv"
    orgs_df.to_csv(orgs_csv, index=False)
    logging.info("Wrote %s (%d orgs)", orgs_csv, len(orgs_df))

    # ── persons ───────────────────────────────────────────────────────────────
    persons_out = []
    for _, p in persons_raw.iterrows():
        mp_id = p["mp_id"]
        persons_out.append({
            "id": _nrsr_person_id(mp_id),
            "identifier": mp_id,
            "given_name": p.get("given_name", ""),
            "family_name": p.get("family_name", ""),
            "image": _PHOTO_URL.format(mp_id=mp_id),
            "identifiers": json.dumps([{"scheme": "nrsr", "identifier": mp_id}]),
            "sources": json.dumps([{"url": _MP_DETAIL_URL.format(mp_id=mp_id)}]),
        })
    persons_df = pd.DataFrame(persons_out)
    persons_df.to_csv(_STD / "persons.csv", index=False)
    logging.info("Wrote %s (%d persons)", _STD / "persons.csv", len(persons_df))

    # ── memberships ───────────────────────────────────────────────────────────
    memb_out = []
    for _, m in memberships_raw.iterrows():
        mp_id = m["mp_id"]
        club = m.get("club_name", "")
        org_id = club_id_map.get(club, f"nrsr:org:club:unknown")
        start = m.get("start_date", "") or _TERM_START
        memb_out.append({
            "id": _nrsr_membership_id(mp_id, org_id, start),
            "person_id": _nrsr_person_id(mp_id),
            "organization_id": org_id,
            "role": "member",
            "start_date": start,
            "end_date": m.get("end_date", "") or None,
        })
        # also add legislature membership
        memb_out.append({
            "id": _nrsr_membership_id(mp_id, _nrsr_org_id(_NRSR_ORG_ID), start),
            "person_id": _nrsr_person_id(mp_id),
            "organization_id": _nrsr_org_id(_NRSR_ORG_ID),
            "role": "member",
            "start_date": start,
            "end_date": None,
        })
    memb_df = pd.DataFrame(memb_out).drop_duplicates("id")
    memb_df.to_csv(_STD / "memberships.csv", index=False)
    logging.info("Wrote %s (%d memberships)", _STD / "memberships.csv", len(memb_df))

    # ── analysis outputs: all-members, current-members ─────────────────────────
    _write_members_analyses(persons_raw, persons_df, memberships_raw, club_id_map)

    # ── analysis outputs: all-groups, current-groups ──────────────────────────
    _write_groups_analyses(club_rows)

    # ── analysis outputs: current-term ───────────────────────────────────────
    _write_current_term()


def _write_members_analyses(
    persons_raw: pd.DataFrame,
    persons_df: pd.DataFrame,
    memberships_raw: pd.DataFrame,
    club_id_map: dict,
) -> None:
    # Build current club per person from most recent membership
    latest_club = (
        memberships_raw[memberships_raw["end_date"] == ""]
        .groupby("mp_id")["club_name"]
        .last()
        .to_dict()
    )

    all_members = []
    current_members = []
    for _, p in persons_raw.iterrows():
        mp_id = str(p["mp_id"])
        club = latest_club.get(mp_id, "")
        record = {
            "id": _nrsr_person_id(mp_id),
            "identifier": mp_id,
            "given_name": p.get("given_name", ""),
            "family_name": p.get("family_name", ""),
            "name": f"{p.get('given_name','')} {p.get('family_name','')}".strip(),
            "image": _PHOTO_URL.format(mp_id=mp_id),
            "organizations": [
                {"id": _nrsr_org_id(_NRSR_ORG_ID), "name": "NRSR", "classification": "legislature"},
            ] + ([{"id": club_id_map.get(club, ""), "name": club, "classification": "group"}] if club else []),
        }
        all_members.append(record)
        if str(p.get("in_parliament", "")).lower() in ("true", "1", "yes"):
            current_members.append(record)

    _write_analysis_output(
        _ROOT / "analyses/all-members/outputs",
        "all_members",
        all_members,
    )
    _write_analysis_output(
        _ROOT / "analyses/current-members/outputs",
        "current_members",
        current_members,
    )
    logging.info("all-members: %d, current-members: %d", len(all_members), len(current_members))


def _write_groups_analyses(club_rows: list[dict]) -> None:
    all_groups = [
        {"id": r["id"], "identifier": r["identifier"], "name": r["name"], "classification": "group"}
        for r in club_rows
    ]
    _write_analysis_output(_ROOT / "analyses/all-groups/outputs", "all_groups", all_groups)
    _write_analysis_output(_ROOT / "analyses/current-groups/outputs", "current_groups", all_groups)


def _write_current_term() -> None:
    term = {
        "id": "nrsr:term:9",
        "identifier": "9",
        "name": "IX. volebné obdobie",
        "organization_id": _nrsr_org_id(_NRSR_ORG_ID),
        "start_date": _TERM_START,
        "end_date": None,
    }
    out_dir = _ROOT / "analyses/current-term/outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "current_term.json").write_text(
        json.dumps(term, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _write_analysis_output(out_dir: Path, name: str, records: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{name}.json"
    json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if records:
        pd.DataFrame(records).to_csv(out_dir / f"{name}.csv", index=False)


# ── B2 upload ─────────────────────────────────────────────────────────────────

def _upload_dataset(local: Path, dataset: str, suffix: str = ".parquet") -> None:
    if not local.exists():
        logging.warning("Skipping upload: %s not found", local)
        return
    stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    remote = f"{_LEGISLATURE_B2_PREFIX}/{dataset}/snapshots/{dataset}-{stamp}{suffix}"
    prefix = f"{_LEGISLATURE_B2_PREFIX}/{dataset}/snapshots/"
    pointer = _ROOT / "data" / dataset / "latest.json"

    url = upload_file(local, remote)
    if url:
        prune_snapshots(prefix, keep=5)
        bucket = os.getenv("B2_BUCKET", "")
        if bucket:
            write_pointer(pointer, bucket=bucket, remote_name=remote)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    logging.info("Building vote_events...")
    build_vote_events()

    logging.info("Building votes...")
    build_votes()

    logging.info("Building persons, orgs, memberships...")
    build_persons_and_memberships()

    logging.info("Uploading to B2...")
    _upload_dataset(_STD / "votes.parquet", "votes")
    _upload_dataset(_STD / "vote_events.parquet", "vote-events")
    _upload_dataset(_STD / "persons.csv", "persons", suffix=".csv")
    _upload_dataset(_STD / "memberships.csv", "memberships", suffix=".csv")
    _upload_dataset(_STD / "organizations.csv", "organizations", suffix=".csv")

    logging.info("Standardize complete.")


if __name__ == "__main__":
    main()
