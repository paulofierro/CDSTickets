#!/usr/bin/env python3
"""Scrape per-show-date seat availability for the active CDS show."""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

BASE = "https://ticket.cds.ky"
LIST_URL = f"{BASE}/event?category=2"
BOOKING_URL = f"{BASE}/booking"
SEATS_URL = f"{BASE}/GetNoOfTickets"
CATEGORY = "2"

# Every show lives in its own folder: shows/<slug>/seats.json and
# shows/<slug>/history.json. shows.json at the repo root is an index derived
# from those folders so the dashboard can list shows with one request.
SHOWS_DIR = "shows"
INDEX_FILE = "shows.json"

# Each show's artwork is kept in its folder as poster.<ext> (poster.jpg by
# default) so it survives the ticket site dropping the listing. The scraper
# downloads it once; a file already in the folder is never overwritten, so a
# hand-picked poster can simply be dropped in.
POSTER_BASENAME = "poster"
POSTER_EXTS = ("jpg", "jpeg", "png", "webp", "gif")

# Extra events whose dates should be merged into a main event's show list,
# overriding any matching date, keyed by the main event's id. Used when a
# single performance gets moved to a separate listing (e.g. the June 11
# Pay-as-you-Can night for In The Heights, event 442, sold via event 451).
OVERRIDE_EVENT_IDS = {
    442: [451],
}

# Phantom seats added on top of the scraped numbers, keyed by main event id and
# then by date. Used to account for bookings the current booking page no longer
# exposes. June 11 is the PWYC night, sold through a separate 120-seat listing
# (event 451); its +10 covers 6 legacy paid bookings on event 442 plus 4 house
# seats held off the PWYC allocation, both counted as sold so the night reflects
# the full 130-seat house.
PHANTOM_ADJUSTMENTS = {
    442: {
        "11 Jun, 2026": {"left": 0, "sold": 10},
    },
}

# Performances the booking page no longer offers and that were never scraped
# while they were on sale, keyed by main event id. A past date drops out of
# the dropdown, so a run whose tracking started after opening night is missing
# its first performances. Entries here are merged in as closed shows whenever
# the scrape doesn't already return that date. Mockingbird opened Thursday
# 8 Oct 2026, the day before tracking began, and sold 40 of its 130 seats;
# its id fits the 1964–1974 run. The same 40 is baked into that show's
# history.json so the daily chart and the card agree.
MANUAL_SHOWS = {
    464: [
        {
            "id": 1963,
            "date": "08 Oct, 2026",
            "time": "7:30 PM - 10:00 PM",
            "seats_left": 90,
            "seats_sold": 40,
        },
    ],
}


MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# The ticketing site occasionally times out or returns a 5xx from CI runners.
# Each request gets an explicit timeout (so a stall fails fast instead of
# hanging until the OS gives up ~2 min later) and a few retries with
# exponential backoff.
TIMEOUT = 30        # seconds per request attempt
MAX_ATTEMPTS = 4    # total tries before giving up
BACKOFF_BASE = 2    # seconds; doubles each retry (2s, 4s, 8s)


def load_json(path):
    """Return the parsed JSON at path, or None if absent/unreadable."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def show_dir(slug):
    return os.path.join(SHOWS_DIR, slug)


def seats_path(slug):
    return os.path.join(show_dir(slug), "seats.json")


def find_poster(slug):
    """Return the relative path of this show's local poster, or None."""
    for ext in POSTER_EXTS:
        path = os.path.join(show_dir(slug), f"{POSTER_BASENAME}.{ext}")
        if os.path.isfile(path):
            return path
    return None


def poster_name(slug):
    """Poster filename relative to the show folder; poster.jpg by default.

    seats.json always points at the folder's own poster so the artwork can be
    dropped in later without touching the data. shows.json resolves it from
    the repo root for the dashboard.
    """
    found = find_poster(slug)
    return os.path.basename(found) if found else f"{POSTER_BASENAME}.jpg"


def download_poster(opener, slug, url):
    """Save the listing's poster into the show folder if none is there yet."""
    if not url or find_poster(slug):
        return find_poster(slug)
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lstrip(".").lower()
    if ext not in POSTER_EXTS:
        ext = "jpg"
    path = os.path.join(show_dir(slug), f"{POSTER_BASENAME}.{ext}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _open_with_retry(opener, req) as r:
            data = r.read()
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"Poster download failed for {slug}: {e}", file=sys.stderr)
        return None
    os.makedirs(show_dir(slug), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    print(f"Saved poster {path} ({len(data)} bytes)", file=sys.stderr)
    return path


def show_sort_key(show):
    """Chronological sort key from a "28 May, 2026" date label."""
    m = re.match(r"(\d+)\s+(\w+),?\s+(\d+)", show.get("date", ""))
    if not m:
        return (9999, 99, 99)
    return (int(m.group(3)), MONTHS.get(m.group(2), 99), int(m.group(1)))


def build_opener():
    cj = CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def _open_with_retry(opener, req):
    """Open a request, retrying transient timeouts and 5xx with backoff."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return opener.open(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            # 4xx responses won't fix themselves; only retry server errors.
            if e.code < 500 or attempt == MAX_ATTEMPTS:
                raise
            err = e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == MAX_ATTEMPTS:
                raise
            err = e
        wait = BACKOFF_BASE * (2 ** (attempt - 1))
        print(
            f"Request to {req.full_url} failed ({err}); "
            f"attempt {attempt}/{MAX_ATTEMPTS}, retrying in {wait}s",
            file=sys.stderr,
        )
        time.sleep(wait)


def get(opener, url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with _open_with_retry(opener, req) as r:
        return r.read().decode("utf-8", errors="replace")


def post(opener, url, data, headers=None):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {})
    with _open_with_retry(opener, req) as r:
        return r.read().decode("utf-8", errors="replace")


def extract_csrf(html):
    m = re.search(r'csrf_cds_value\s*=\s*"([^"]+)"', html)
    if not m:
        raise RuntimeError("CSRF token not found")
    return m.group(1)


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "show"


def extract_events(html):
    """Return one dict per event listed on the category page.

    Each listing is a <div class="news_post"> block holding the title link,
    poster image, start/end dates and a booking form with the EventId.
    """
    events = []
    blocks = re.split(r'<div class="news_post">', html)[1:]
    if not blocks:
        # Markup differs from what we expect; fall back to slicing the page
        # at each booking form so we still find every EventId.
        blocks = re.split(r'(?=<form[^>]+name="frmEvent-\d+")', html)[1:]
    for block in blocks:
        m = re.search(r'name="EventId"\s+value="(\d+)"', block)
        if not m:
            continue
        event_id = int(m.group(1))
        title_m = re.search(
            r'<h4><a title="([^"]+)"\s+href="([^"]+)"\s+class="link">', block
        )
        title = title_m.group(1).strip() if title_m else f"Event {event_id}"
        url = title_m.group(2).strip() if title_m else LIST_URL
        img_m = re.search(r'<img[^>]+src="([^"]+)"', block)
        path = urllib.parse.urlparse(url).path.strip("/")
        events.append({
            "id": event_id,
            "title": title,
            "url": url,
            "image": img_m.group(1) if img_m else None,
            "slug": slugify(path.rsplit("/", 1)[-1] if path else title),
        })
    if not events:
        describe_page(html)
        # The site shows this between runs, and sometimes pulls a listing
        # mid-run (seen on show day). That's not a scraper failure: return
        # nothing and let main() leave the data as it is.
        if re.search(r"Currently no events are available", html, re.IGNORECASE):
            print("Listing page reports no events available", file=sys.stderr)
            return []
        raise RuntimeError("No events found on listing page")
    return events


def describe_page(html):
    """Log what the listing page looked like so a runner failure is debuggable."""
    title = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    print(
        f"Listing page: {len(html)} bytes, title={title.group(1).strip() if title else None!r}, "
        f"news_post={html.count('news_post')}, EventId={html.count('name=\"EventId\"')}",
        file=sys.stderr,
    )
    print(f"Listing text: {text[:600]!r}", file=sys.stderr)


def extract_dates(booking_html):
    """Return list of (id, raw_label) for each date option."""
    # <option value="1790" data-subtext="">28 May, 2026 ( 7:30 PM - 10:00 PM )</option>
    pattern = re.compile(
        r'<option value="(\d+)" data-subtext="">([^<]+)</option>'
    )
    return [(m.group(1), m.group(2).strip()) for m in pattern.finditer(booking_html)]


def parse_label(label):
    # "28 May, 2026 ( 7:30 PM - 10:00 PM )"
    m = re.match(r"^(.*?)\s*\(\s*(.*?)\s*\)\s*$", label)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return label, None


def parse_seats(html):
    """Return (seats_left, seats_sold) from the AJAX HTML response."""
    sold = None
    m = re.search(r'name="PaidShowSeat"[^>]*value="(\d+)"', html)
    if m:
        sold = int(m.group(1))

    # Visible message: "Only 94 Seats Are Left"
    m = re.search(r"Only\s+(\d+)\s+Seats?\s+Are\s+Left", html, re.IGNORECASE)
    if m:
        return int(m.group(1)), sold

    # Sold-out signal
    if re.search(r'name="bookingfull"[^>]*value="yes"', html, re.IGNORECASE) or \
       re.search(r"sold\s*out", html, re.IGNORECASE):
        return 0, sold

    # Fallback: HidTotalSeat - PaidShowSeat
    total_m = re.search(r'name="HidTotalSeat"[^>]*value="(\d+)"', html)
    if total_m and sold is not None:
        return int(total_m.group(1)) - sold, sold

    return None, sold


def fetch_event_shows(opener, event_id):
    """Scrape the booking page for one event and return its list of shows."""
    listing = get(opener, LIST_URL, headers={"User-Agent": "Mozilla/5.0"})
    csrf = extract_csrf(listing)

    booking = post(
        opener,
        BOOKING_URL,
        {"csrf_srm": csrf, "EventId": str(event_id), "EventRegType": "Y"},
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": LIST_URL,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    csrf = extract_csrf(booking)  # CSRF rotates per page load
    dates = extract_dates(booking)

    shows = []
    for date_id, label in dates:
        date_part, time_part = parse_label(label)
        resp = post(
            opener,
            SEATS_URL,
            {
                "csrf_srm": csrf,
                "EventID": str(event_id),
                "EventDate": date_id,
                "catdata": CATEGORY,
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": BOOKING_URL,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        seats_left, seats_sold = parse_seats(resp)
        shows.append({
            "id": int(date_id),
            "date": date_part,
            "time": time_part,
            "seats_left": seats_left,
            "seats_sold": seats_sold,
        })
        print(
            f"  {date_part} {time_part}: left={seats_left}, sold={seats_sold}",
            file=sys.stderr,
        )
    return shows


def scrape_event(opener, event, existing):
    """Scrape one listed event and return its seats.json payload."""
    event_id = event["id"]
    print(f"Event: {event['title']} (id={event_id}, slug={event['slug']})", file=sys.stderr)

    shows = fetch_event_shows(opener, event_id)
    print(f"Found {len(shows)} dates on main event", file=sys.stderr)

    for override_id in OVERRIDE_EVENT_IDS.get(event_id, []):
        print(f"Override event: id={override_id}", file=sys.stderr)
        for override_show in fetch_event_shows(opener, override_id):
            idx = next(
                (i for i, s in enumerate(shows) if s["date"] == override_show["date"]),
                None,
            )
            if idx is not None:
                print(
                    f"    replaces {override_show['date']} (was id={shows[idx]['id']}, now id={override_show['id']})",
                    file=sys.stderr,
                )
                shows[idx] = override_show
            else:
                shows.append(override_show)

    scraped_dates = {s["date"] for s in shows}
    for manual in MANUAL_SHOWS.get(event_id, []):
        if manual["date"] in scraped_dates:
            continue
        print(f"Manual show {manual['date']} (id={manual['id']}): not on booking page, adding as closed", file=sys.stderr)
        shows.append({**manual, "closed": True})

    # When a date sells out, the booking AJAX stops returning PaidShowSeat, so
    # seats_sold comes back as None even though every seat is gone. Filling it
    # with the house capacity keeps the card reading "Sold out" (not "0 sold")
    # and stops the velocity/daily-sales stats from cratering. House capacity is
    # read from this scrape's still-selling dates (every performance is the same
    # size); a date's own last-known capacity wins when we have it, so an
    # odd-sized night (e.g. PWYC) doesn't inherit the wrong number. These shows
    # skip the phantom pass below — their capacity already accounts for it.
    prev_cap = {}
    prev_known_sold = []
    for old in (existing or {}).get("shows", []):
        pl, ps = old.get("seats_left"), old.get("seats_sold")
        if ps is not None:
            prev_known_sold.append(ps)
        if pl is not None and ps is not None and pl > 0:
            prev_cap[str(old.get("id"))] = prev_cap[old.get("date")] = pl + ps

    healthy_caps = [
        s["seats_left"] + s["seats_sold"]
        for s in shows
        if s["seats_left"] is not None
        and s["seats_sold"] is not None
        and s["seats_left"] > 0
    ]
    house_cap = max(healthy_caps) if healthy_caps else None
    # When every performance is sold out, no live date has seats_left > 0, so
    # this scrape reveals no house size and house_cap would be None — leaving
    # the carry-forward below unable to backfill sold-out shows (sold reads as
    # 0/None and the stats crater). Fall back to the largest house we've ever
    # recorded so a fully sold-out run still fills in the full house.
    if house_cap is None:
        fallbacks = list(prev_cap.values()) + prev_known_sold
        house_cap = max(fallbacks) if fallbacks else None

    carried_forward = set()
    for show in shows:
        if show["seats_left"] == 0 and show["seats_sold"] is None:
            cap = prev_cap.get(str(show["id"])) or prev_cap.get(show["date"]) or house_cap
            if cap is not None:
                show["seats_sold"] = cap
                carried_forward.add(show["id"])
                print(
                    f"Sold out {show['date']} (id={show['id']}): set "
                    f"sold={cap} (full house)",
                    file=sys.stderr,
                )

    phantoms = PHANTOM_ADJUSTMENTS.get(event_id, {})
    for show in shows:
        adj = phantoms.get(show["date"])
        if not adj or show["id"] in carried_forward:
            continue
        show["seats_left"] = (show["seats_left"] or 0) + adj["left"]
        show["seats_sold"] = (show["seats_sold"] or 0) + adj["sold"]
        print(
            f"Phantom {show['date']}: +{adj['left']} left, +{adj['sold']} sold "
            f"-> left={show['seats_left']}, sold={show['seats_sold']}",
            file=sys.stderr,
        )

    # Once a performance's ticket sales close, it drops out of the booking
    # dropdown and stops being scraped. Keep the last-known data for any date
    # we previously had so closed/past shows stay on the dashboard.
    if existing:
        scraped_dates = {s["date"] for s in shows}
        for old in existing.get("shows", []):
            if old.get("date") not in scraped_dates:
                print(
                    f"Retaining {old['date']} (id={old.get('id')}) from previous "
                    f"data — no longer in booking list",
                    file=sys.stderr,
                )
                old["closed"] = True
                shows.append(old)

    # A performance can change id between runs — e.g. the PWYC night is sold
    # through a separate listing (event 451, with its own date id) and reverts
    # to its main-event id once that listing closes. history.json records sold
    # counts per id, so a switch would split the show's sales curve in two.
    # Carry each date's prior ids forward as `aka` so the dashboard can stitch
    # the full history back into one chart.
    prev_by_date = {}
    for old in (existing or {}).get("shows", []):
        prev_by_date.setdefault(old.get("date"), old)
    for show in shows:
        aka = {int(a) for a in show.get("aka", [])}
        old = prev_by_date.get(show["date"])
        if old:
            aka.update(int(a) for a in old.get("aka", []))
            if old.get("id") is not None and int(old["id"]) != int(show["id"]):
                aka.add(int(old["id"]))
        aka.discard(int(show["id"]))
        if aka:
            show["aka"] = sorted(aka)

    shows.sort(key=show_sort_key)

    sold_out = bool(shows) and all(s["seats_left"] == 0 for s in shows)
    return {
        "event": {
            "id": event_id,
            "title": event["title"],
            "slug": event["slug"],
            "url": event["url"],
            "image": poster_name(event["slug"]),
            "image_url": event.get("image"),
            "status": "sold_out" if sold_out else "on_sale",
        },
        "shows": shows,
    }


def write_json(path, payload):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {path}", file=sys.stderr)


def all_show_slugs():
    if not os.path.isdir(SHOWS_DIR):
        return []
    return sorted(
        d for d in os.listdir(SHOWS_DIR)
        if os.path.isfile(seats_path(d))
    )


def is_past(date_label):
    """True when a "25 Oct, 2026" label is before today in Cayman time (UTC-5)."""
    key = show_sort_key({"date": date_label})
    if key == (9999, 99, 99):
        return False
    today = datetime.now(timezone(timedelta(hours=-5))).date()
    return key < (today.year, today.month, today.day)


def build_index():
    """Derive shows.json from every shows/<slug>/seats.json on disk."""
    entries = []
    for slug in all_show_slugs():
        data = load_json(seats_path(slug))
        if not data or "shows" not in data:
            continue
        event = data.get("event", {})
        shows = sorted(data["shows"], key=show_sort_key)
        left = sum(s.get("seats_left") or 0 for s in shows)
        sold = sum(s.get("seats_sold") or 0 for s in shows)
        entries.append({
            "slug": slug,
            "title": event.get("title") or slug,
            "event_id": event.get("id"),
            "url": event.get("url") or LIST_URL,
            "image": f"{SHOWS_DIR}/{slug}/{os.path.basename(event.get('image') or poster_name(slug))}",
            "status": event.get("status") or "closed",
            "start": shows[0]["date"] if shows else None,
            "end": shows[-1]["date"] if shows else None,
            "performances": len(shows),
            "seats_left": left,
            "seats_sold": sold,
        })
    # Newest run first so the dashboard leads with the current show.
    entries.sort(key=lambda e: show_sort_key({"date": e["start"] or ""}), reverse=True)
    write_json(INDEX_FILE, {"shows": entries})


def main():
    opener = build_opener()

    listing = get(opener, LIST_URL, headers={"User-Agent": "Mozilla/5.0"})
    events = extract_events(listing)
    print(f"Listing has {len(events)} event(s)", file=sys.stderr)
    listing_empty = not events

    # A folder is keyed by slug, but the ticket site's URL slug could change
    # between runs; if an existing folder already holds this event id, keep
    # using it so the show's history stays in one place.
    existing_by_id = {}
    for slug in all_show_slugs():
        data = load_json(seats_path(slug))
        eid = (data or {}).get("event", {}).get("id")
        if eid is not None:
            existing_by_id[int(eid)] = slug

    scraped_slugs = set()
    for event in events:
        event["slug"] = existing_by_id.get(event["id"], event["slug"])
        existing = load_json(seats_path(event["slug"]))
        download_poster(opener, event["slug"], event.get("image"))
        payload = scrape_event(opener, event, existing)
        write_json(seats_path(event["slug"]), payload)
        scraped_slugs.add(event["slug"])

    # Anything we have on disk but the listing no longer offers has finished
    # selling; mark it closed so it stops being appended to and reads right.
    # When the whole listing is empty the site may just have the show offline
    # for a while, so only close a run whose final performance has passed.
    for slug in all_show_slugs():
        if slug in scraped_slugs:
            continue
        path = seats_path(slug)
        data = load_json(path)
        if not data or data.get("event", {}).get("status") == "closed":
            continue
        dates = [s.get("date", "") for s in data.get("shows", [])]
        last = max(dates, key=lambda d: show_sort_key({"date": d}), default="")
        if listing_empty and not is_past(last):
            print(f"{slug}: not listed right now but still running (last date {last}); leaving as is", file=sys.stderr)
            continue
        data.setdefault("event", {})["status"] = "closed"
        print(f"Marking {slug} closed (no longer listed)", file=sys.stderr)
        write_json(path, data)

    build_index()




if __name__ == "__main__":
    main()
