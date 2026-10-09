# CDSTickets
Displays the ticket availability of current productions by [the Cayman Drama Society](https://www.cds.ky), published here [https://paulofierro.github.io/CDSTickets/](https://paulofierro.github.io/CDSTickets/)

## Layout

- `index.html` — static dashboard. The landing view lists every tracked show; `?show=<slug>` opens the sales dashboard for one.
- `shows.json` — index of tracked shows, derived from the per-show folders by the scraper.
- `shows/<slug>/seats.json` — latest per-performance seat counts for one show.
- `shows/<slug>/history.json` — snapshots of sold counts over time for that show.
- `shows/<slug>/poster.jpg` — the show's artwork, downloaded once from the ticket listing. Drop in your own `poster.jpg` (or `.png`/`.webp`) to override; an existing file is never overwritten.
- `fetch_seats.py` — scrapes every event on the CDS ticket listing, writes each show's `seats.json`, marks shows that have left the listing as closed, and rebuilds `shows.json`.
- `append_history.py` — appends a snapshot to every open show's `history.json`.
- `rebuild_history.py <slug>` — rebuilds a show's `history.json` from the git log of its `seats.json`.

## Running locally

```
python3 -m http.server 8000
```

Then open http://localhost:8000. To refresh the data:

```
python3 fetch_seats.py && python3 append_history.py
```

The GitHub Action in `.github/workflows/update-seats.yml` does the same on a schedule and commits the result.
