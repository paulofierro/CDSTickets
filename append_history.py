#!/usr/bin/env python3
"""Append a snapshot of current sales to history.json."""
import json
import os
from datetime import datetime, timezone

SEATS_FILE = "seats.json"
HISTORY_FILE = "history.json"


def main():
    with open(SEATS_FILE) as f:
        seats = json.load(f)

    sold = {
        str(s["id"]): s["seats_sold"]
        for s in seats["shows"]
        if s.get("seats_sold") is not None
    }
    snapshot = {
        "t": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sold": sold,
    }

    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            history = json.load(f)
    else:
        history = {"snapshots": []}

    history.setdefault("snapshots", []).append(snapshot)

    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


if __name__ == "__main__":
    main()
