#!/usr/bin/env python3
"""Rebuild history.json from the git log of seats.json.

Recovery / backfill tool. Normal operation appends to history.json via
append_history.py on each scheduled run; this script is the escape hatch
for when history.json gets corrupted, deleted, or drifts out of sync —
git is the source of truth.

Walks every commit that touched seats.json (oldest first), extracts the
seats_sold map at that commit, and writes one snapshot per commit using
the committer timestamp (normalised to UTC). Overwrites history.json.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone

SEATS_FILE = "seats.json"
HISTORY_FILE = "history.json"


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def commits():
    out = run("git", "log", "--reverse", "--format=%H%x09%cI", "--", SEATS_FILE)
    for line in out.splitlines():
        sha, _, iso = line.partition("\t")
        yield sha, iso


def seats_at(sha):
    try:
        blob = run("git", "show", f"{sha}:{SEATS_FILE}")
    except subprocess.CalledProcessError:
        return None
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


def to_utc_z(iso):
    return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def main():
    snapshots = []
    for sha, iso in commits():
        seats = seats_at(sha)
        if not seats or "shows" not in seats:
            continue
        sold = {
            str(s["id"]): s["seats_sold"]
            for s in seats["shows"]
            if s.get("seats_sold") is not None
        }
        if not sold:
            continue
        snapshots.append({"t": to_utc_z(iso), "sold": sold})

    with open(HISTORY_FILE, "w") as f:
        json.dump({"snapshots": snapshots}, f, indent=2)
    print(f"Wrote {len(snapshots)} snapshots to {HISTORY_FILE}", file=sys.stderr)


if __name__ == "__main__":
    main()
