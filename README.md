# Polestar Software Updates — Unofficial Tracker

A single-page tracker of every released **Polestar 2**, **Polestar 3**, **Polestar 4** and **Polestar 5** software
version and its official release notes, styled after the
[Polestar 4 updates tracker](https://jaybizzle.github.io/polestar4-updates).

**Not affiliated with or endorsed by Polestar.** Version notes © Polestar.

## What it shows

- A **model toggle** (Polestar 2 / 3 / 4 / 5) — one page, switch between models.
- The latest documented software version per model.
- The full release history, newest first, each version expandable to its
  official release notes (model-year sub-sections preserved).
- Live search across versions and notes, plus expand/collapse-all.
- An **RSS feed per model** (`feed-polestar-2.xml`, `feed-polestar-3.xml`, `feed-polestar-4.xml`, `feed-polestar-5.xml`).

## RSS feeds

Each model has its own feed, linked from the page footer and `<head>`. An item's
`pubDate` is the date the scraper **first observed** the version (`first_seen`),
persisted per version in `data/<model>.json` so dates stay stable across runs.

## Sources & how dates are derived

Polestar publishes **no explicit release date** for software in either source, so
every version is dated by **"First tracked"** — when this tracker first observed it.

- **Polestar 2** — scraped from the owner's manual
  ([Software updates, UK view](https://www.polestar.com/uk/manual/polestar-2/2027/software-updates/)),
  which embeds the release notes as a Remix context blob
  (`releaseNotes.content.body`). Polestar 2 is **not** exposed by the JSON API, so
  it stays HTML-scraped.
- **Polestar 3** — read from Polestar's public, unauthenticated release-notes JSON
  API. The scraper tries the current Volvo support host
  (`support-car-content.volvo.care`) first and retains the former Polestar-prefixed
  hostname as a fallback. Each version carries a `cmsSoftwareVersion` **build-week
  code** in `YYWW` form (e.g. `26380` → 2026, ISO week 38). This is when the build
  was **registered**, not released, so it is shown for traceability only.
- **Polestar 4 and 5** — scraped directly from their official
  [Polestar 4](https://www.polestar.com/uk/manual/polestar-4/2026/software-updates/) and
  [Polestar 5](https://www.polestar.com/uk/manual/polestar-5/2026/software-updates/)
  software-update manual pages. This avoids depending on the legacy API hostname
  for these models and uses the release history Polestar publishes to owners.

Neither source has a true release-date field, so `first_seen` is the honest signal —
the same approach as the reference
[Polestar 4 tracker](https://jaybizzle.github.io/polestar4-updates).

### Build codes, cadence & forecast (API-sourced models)

For API-sourced models the tracker mirrors the three signals the reference
[Polestar 4 tracker](https://jaybizzle.github.io/polestar4-updates) shows. These apply to
**Polestar 3, 4 and 5** — Polestar 2 is HTML-scraped, has no build codes and no registered
version list, so its rows and banner are unchanged.

- **Build code on every version** — the raw `YYWW` code shown for traceability, e.g.
  `build 26380 · 2026 wk 38`.
- **Days between versions** — the gap in days from the previous (next-older) observed
  release (e.g. `· 28 days after previous`).
- **Predicted next update** — the banner replaces "Latest released version" with a
  statistical estimate: the **median** of the gaps between past observed releases, added
  to the latest release date, with a likely window (the 25th–75th-percentile gap, the
  middle 50% of history) and an overdue / due-in badge. It is an estimate from past
  cadence, not an announced date.
- **In the pipeline** — builds Polestar has *registered* in `available-car-models`
  but not yet published notes for (`internalVersion` greater than the manifest's
  `spaceSoftwareVersion`). This section is hidden when empty — which is the case
  whenever the newest registered build is already the newest published one.

## Adding another model

Everything is driven by the `MODELS` list at the top of `scripts/scrape.py`. Each
entry has a `slug`, `label`, `manual_url`, and a `source`:

- `"source": "html"` — HTML-scrape the `manual_url` (like Polestar 2).
- `"source": "api"` — read the JSON API; also set `"model_code"` (Polestar 3 = 359).
  Use the official manual's HTML source for models whose software history is published
  directly on the manual page (currently Polestar 2, 4 and 5).

Add a new model there and the scraper produces `data/<slug>.json` +
`feed-<slug>.xml`, the page grows a new toggle tab, and the notifier workflow covers
it automatically.

## Updating the data

Version data lives in `data/<model>.json`, is mirrored into the `DATA` object embedded
in `index.html`, and drives the per-model feeds. A GitHub Action refreshes it:

- **`.github/workflows/update-data.yml`** runs `scripts/scrape.py` every Monday
  (and on-demand via the Actions tab → *Update Polestar software data* → *Run workflow*).
- The scraper fetches each model's manual, parses the embedded release-notes data,
  regenerates `index.html` + `data/*.json` + `feed-*.xml`, and commits only if something
  changed. GitHub Pages then redeploys automatically.
- When a **genuinely new version** (not a reworded existing one) appears, the workflow
  opens a **GitHub issue** per new version so the maintainer is notified by email. The
  first run after a new model is added is treated as a bootstrap import and does not
  open issues for the backfilled history.

To refresh manually on your machine:

```sh
python3 scripts/scrape.py        # fetches all models live + rewrites index.html
# test against saved HTML (per model, repeatable):
python3 scripts/scrape.py --local polestar-2 path/to/p2.html --local polestar-3 path/to/p3.html
```

The scraper uses only the Python standard library — no dependencies to install. If one
model's source is unavailable, it preserves that model's last-known-good data and still
publishes successful updates for the other models. GitHub Actions marks partial scrapes
with a warning; the workflow fails only when every model source is unavailable.

## License

Code: MIT. Release note text is © Polestar and reproduced here for reference.
