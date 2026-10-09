#!/usr/bin/env python3
"""Rebuild a show's history.json from the git log of its seats.json.

Recovery / backfill tool. Normal operation appends to each show's
history.json via append_history.py on each scheduled run; this script is the
escape hatch for when a history.json gets corrupted, deleted, or drifts out
of sync — git is the source of truth.

Usage: python3 rebuild_history.py <slug> [<slug> ...]

Walks every commit that touched shows/<slug>/seats.json (oldest first,
following renames — In The Heights lived at the repo root as seats.json
before shows were split into folders), extracts the seats_sold map at each
commit, and writes one snapshot per commit using the committer timestamp
(normalised to UTC). Overwrites that show's history.json.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

SHOWS_DIR = "shows"

# Where a show's seats.json lived before shows were split into folders. Used
# when the folder path has no git history yet (the move is uncommitted) so
# the rebuild can still read the old path. --follow covers it once committed.
LEGACY_PATHS = {
    "in-the-heights": "seats.json",
}


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def commits(path):
    """Yield (sha, iso_timestamp, path_at_that_commit), oldest first."""
    out = run(
        "git", "log", "--reverse", "--follow", "--name-only",
        "--format=%x00%H%x09%cI", "--", path,
    )
    for record in out.split("\x00")[1:]:
        lines = [l for l in record.splitlines() if l.strip()]
        if not lines:
            continue
        sha, _, iso = lines[0].partition("\t")
        # With --follow and a single pathspec, the one listed name is the
        # file's path as of this commit.
        path_then = lines[1] if len(lines) > 1 else path
        yield sha, iso, path_then


def seats_at(sha, path):
    try:
        blob = run("git", "show", f"{sha}:{path}")
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


def rebuild(slug):
    seats_file = os.path.join(SHOWS_DIR, slug, "seats.json")
    history_file = os.path.join(SHOWS_DIR, slug, "history.json")

    log = list(commits(seats_file))
    if not log and slug in LEGACY_PATHS:
        log = list(commits(LEGACY_PATHS[slug]))

    snapshots = []
    for sha, iso, path in log:
        seats = seats_at(sha, path)
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

    with open(history_file, "w") as f:
        json.dump({"snapshots": snapshots}, f, indent=2)
    print(f"{slug}: wrote {len(snapshots)} snapshots to {history_file}", file=sys.stderr)


def main(argv):
    if not argv:
        sys.exit(__doc__.strip())
    for slug in argv:
        if not os.path.isfile(os.path.join(SHOWS_DIR, slug, "seats.json")):
            sys.exit(f"No seats.json for show '{slug}'")
        rebuild(slug)


if __name__ == "__main__":
    main(sys.argv[1:])
