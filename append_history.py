#!/usr/bin/env python3
"""Append a snapshot of current sales to each active show's history.json.

Every show lives in shows/<slug>/ with a seats.json written by fetch_seats.py
and a history.json of sold-count snapshots. Shows whose event status is
"closed" are no longer scraped, so their seats.json never changes; appending
to them would only repeat the final numbers, so they are skipped.
"""
import json
import os
import sys
from datetime import datetime, timezone

SHOWS_DIR = "shows"


def snapshot_for(seats):
    sold = {
        str(s["id"]): s["seats_sold"]
        for s in seats["shows"]
        if s.get("seats_sold") is not None
    }
    return {
        "t": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sold": sold,
    }


def append(slug):
    seats_file = os.path.join(SHOWS_DIR, slug, "seats.json")
    history_file = os.path.join(SHOWS_DIR, slug, "history.json")

    with open(seats_file) as f:
        seats = json.load(f)

    if seats.get("event", {}).get("status") == "closed":
        print(f"{slug}: closed, skipping", file=sys.stderr)
        return

    if os.path.exists(history_file):
        with open(history_file) as f:
            history = json.load(f)
    else:
        history = {"snapshots": []}

    snapshots = history.setdefault("snapshots", [])
    snapshot = snapshot_for(seats)
    # A snapshot identical to the last one adds nothing (the chart and
    # velocity stats only look at changes) and would commit a no-op, which
    # happens whenever the ticket site has the listing offline.
    if snapshots and snapshots[-1].get("sold") == snapshot["sold"]:
        print(f"{slug}: unchanged since last snapshot, skipping", file=sys.stderr)
        return
    snapshots.append(snapshot)

    with open(history_file, "w") as f:
        json.dump(history, f, indent=2)
    print(f"{slug}: appended snapshot", file=sys.stderr)


def main():
    if not os.path.isdir(SHOWS_DIR):
        sys.exit(f"{SHOWS_DIR}/ not found; run fetch_seats.py first")
    for slug in sorted(os.listdir(SHOWS_DIR)):
        if os.path.isfile(os.path.join(SHOWS_DIR, slug, "seats.json")):
            append(slug)


if __name__ == "__main__":
    main()
