# Polestar Software Updates — Unofficial Tracker

A single-page tracker of every released **Polestar 2** and **Polestar 3** software
version and its official release notes, styled after the
[Polestar 4 updates tracker](https://jaybizzle.github.io/polestar4-updates).

**Not affiliated with or endorsed by Polestar.** Version notes © Polestar.

## What it shows

- A **model toggle** (Polestar 2 / Polestar 3) — one page, switch between models.
- The latest documented software version per model.
- The full release history, newest first, each version expandable to its
  official release notes (model-year sub-sections preserved).
- Live search across versions and notes, plus expand/collapse-all.
- An **RSS feed per model** (`feed-polestar-2.xml`, `feed-polestar-3.xml`).

## RSS feeds

Each model has its own feed, linked from the page footer and `<head>`. An item's
`pubDate` is the version's **released** date when one is available (Polestar 3 —
derived from the build-week code, see below), otherwise the date the scraper
**first observed** the version (`first_seen`, used for Polestar 2). Both are
persisted per version in `data/<model>.json` so dates stay stable across runs.

## Sources & how dates are derived

Polestar publishes **no explicit release date** for software in either source, so
the tracker uses the best signal each source offers:

- **Polestar 2** — scraped from the owner's manual
  ([Software updates, UK view](https://www.polestar.com/uk/manual/polestar-2/2027/software-updates/)),
  which embeds the release notes as a Remix context blob
  (`releaseNotes.content.body`). Polestar 2 is **not** exposed by the JSON API, so
  it stays HTML-scraped and its dates show as **"First tracked"** — when this
  tracker first saw the version.
- **Polestar 3** — read from Polestar's public, unauthenticated release-notes JSON
  API (`https://support-car-content.polestar.volvo.care`, the source behind the
  [Polestar 3 manual page](https://www.polestar.com/uk/manual/polestar-3/2025/software-updates/)).
  Each version carries a `cmsSoftwareVersion` **build-week code** in `YYWW` form
  (e.g. `26380` → 2026, ISO week 38). The tracker decodes that to the **Monday of
  that ISO week** and shows it as **"Released ~"** (approximate — the code can lead
  the actual rollout, which still varies by market and model year). Versions with no
  usable code fall back to "First tracked".

The API has no true date field — the week code is the closest available signal, so
the tracker cannot compute exact "days between releases" or predict the next update.

## Adding another model

Everything is driven by the `MODELS` list at the top of `scripts/scrape.py`. Each
entry has a `slug`, `label`, `manual_url`, and a `source`:

- `"source": "html"` — HTML-scrape the `manual_url` (like Polestar 2).
- `"source": "api"` — read the JSON API; also set `"model_code"` (Polestar 3 = 359,
  Polestar 4 = 814, Polestar 4 SUV = 815, Polestar 5 = 824).

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

The scraper uses only the Python standard library — no dependencies to install.

## License

Code: MIT. Release note text is © Polestar and reproduced here for reference.
