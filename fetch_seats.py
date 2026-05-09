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


def main():
    opener = build_opener()

    listing = get(opener, LIST_URL, headers={"User-Agent": "Mozilla/5.0"})
    csrf = extract_csrf(listing)
    event_id = extract_event_id(listing)
    title = extract_event_title(listing, event_id)
    print(f"Event: {title} (id={event_id})", file=sys.stderr)

    booking = post(
        opener,
        BOOKING_URL,
        {"csrf_srm": csrf, "EventId": event_id, "EventRegType": "Y"},
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": LIST_URL,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    csrf = extract_csrf(booking)  # CSRF rotates per page load
    dates = extract_dates(booking)
    print(f"Found {len(dates)} dates", file=sys.stderr)

    results = []
    for date_id, label in dates:
        date_part, time_part = parse_label(label)
        resp = post(
            opener,
            SEATS_URL,
            {
                "csrf_srm": csrf,
                "EventID": event_id,
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
        entry = {
            "id": int(date_id),
            "date": date_part,
            "time": time_part,
            "seats_left": seats_left,
            "seats_sold": seats_sold,
        }
        results.append(entry)
        print(
            f"  {date_part} {time_part}: left={seats_left}, sold={seats_sold}",
            file=sys.stderr,
        )

    payload = {
        "event": {"id": int(event_id), "title": title},
        "shows": results,
    }
    with open("seats.json", "w") as f:
        json.dump(payload, f, indent=2)
    print("Wrote seats.json", file=sys.stderr)


if __name__ == "__main__":
    main()
