#!/usr/bin/env python3
"""Scrape per-show-date seat availability for the active CDS show."""
import json
import re
import sys
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

BASE = "https://ticket.cds.ky"
LIST_URL = f"{BASE}/event?category=2"
BOOKING_URL = f"{BASE}/booking"
SEATS_URL = f"{BASE}/GetNoOfTickets"
CATEGORY = "2"

# Extra events whose dates should be merged into the main event's show list,
# overriding any matching date. Used when a single performance gets moved to
# a separate listing (e.g. the June 11 Pay-as-you-Can night for In The Heights).
OVERRIDE_EVENT_IDS = [451]

# Phantom seats added on top of the scraped numbers, by date. Used to account
# for bookings the current booking page no longer exposes. June 11 is the PWYC
# night, sold through a separate 120-seat listing (event 451); its +10 covers
# 6 legacy paid bookings on event 442 plus 4 house seats held off the PWYC
# allocation, both counted as sold so the night reflects the full 130-seat house.
PHANTOM_ADJUSTMENTS = {
    "11 Jun, 2026": {"left": 0, "sold": 10},
}


MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def load_existing(path="seats.json"):
    """Return the previously written payload, or None if absent/unreadable."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def show_sort_key(show):
    """Chronological sort key from a "28 May, 2026" date label."""
    m = re.match(r"(\d+)\s+(\w+),?\s+(\d+)", show.get("date", ""))
    if not m:
        return (9999, 99, 99)
    return (int(m.group(3)), MONTHS.get(m.group(2), 99), int(m.group(1)))


def build_opener():
    cj = CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def get(opener, url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with opener.open(req) as r:
        return r.read().decode("utf-8", errors="replace")


def post(opener, url, data, headers=None):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {})
    with opener.open(req) as r:
        return r.read().decode("utf-8", errors="replace")


def extract_csrf(html):
    m = re.search(r'csrf_cds_value\s*=\s*"([^"]+)"', html)
    if not m:
        raise RuntimeError("CSRF token not found")
    return m.group(1)


def extract_event_id(html):
    # Look for the booking form's EventId hidden input
    m = re.search(r'name="EventId"\s+value="(\d+)"', html)
    if not m:
        raise RuntimeError("EventId not found on listing page")
    return m.group(1)


def extract_event_title(html, event_id):
    # Find the title near the form for this EventId
    m = re.search(r'title="([^"]+)"\s+href="[^"]+"\s+class="link">[^<]+</a></h4>', html)
    return m.group(1) if m else None


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


def main():
    opener = build_opener()
    existing = load_existing()

    listing = get(opener, LIST_URL, headers={"User-Agent": "Mozilla/5.0"})
    event_id = extract_event_id(listing)
    title = extract_event_title(listing, event_id)
    print(f"Event: {title} (id={event_id})", file=sys.stderr)

    shows = fetch_event_shows(opener, event_id)
    print(f"Found {len(shows)} dates on main event", file=sys.stderr)

    for override_id in OVERRIDE_EVENT_IDS:
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

    # When a date sells out, the booking AJAX stops returning PaidShowSeat, so
    # seats_sold comes back as None even though every seat is gone. Filling it
    # with the house capacity keeps the card reading "Sold out" (not "0 sold")
    # and stops the velocity/daily-sales stats from cratering. House capacity is
    # read from this scrape's still-selling dates (every performance is the same
    # size); a date's own last-known capacity wins when we have it, so an
    # odd-sized night (e.g. PWYC) doesn't inherit the wrong number. These shows
    # skip the phantom pass below — their capacity already accounts for it.
    healthy_caps = [
        s["seats_left"] + s["seats_sold"]
        for s in shows
        if s["seats_left"] is not None
        and s["seats_sold"] is not None
        and s["seats_left"] > 0
    ]
    house_cap = max(healthy_caps) if healthy_caps else None

    prev_cap = {}
    for old in (existing or {}).get("shows", []):
        pl, ps = old.get("seats_left"), old.get("seats_sold")
        if pl is not None and ps is not None and pl > 0:
            prev_cap[str(old.get("id"))] = prev_cap[old.get("date")] = pl + ps

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

    for show in shows:
        adj = PHANTOM_ADJUSTMENTS.get(show["date"])
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

    shows.sort(key=show_sort_key)

    payload = {
        "event": {"id": int(event_id), "title": title},
        "shows": shows,
    }
    with open("seats.json", "w") as f:
        json.dump(payload, f, indent=2)
    print("Wrote seats.json", file=sys.stderr)


if __name__ == "__main__":
    main()
