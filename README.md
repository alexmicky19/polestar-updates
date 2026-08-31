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

Each model has its own feed, linked from the page footer and `<head>`. Since
Polestar doesn't publish release dates for these models (see below), each item's
`pubDate` is the date the scraper **first observed** that version — persisted per
version in `data/<model>.json` (`first_seen`) so dates stay stable across runs.
All versions that existed when a feed was first generated share that initial date;
anything released afterwards gets a genuine first-seen date when it appears.

## Source & the "no dates" caveat

Data is taken from the Polestar owner's manuals — Software updates (UK view, which
lists all historical versions):

- [Polestar 2](https://www.polestar.com/uk/manual/polestar-2/2027/software-updates/) (model-year 2027 view)
- [Polestar 3](https://www.polestar.com/uk/manual/polestar-3/2025/software-updates/) (model-year 2025 view)

Both manuals embed the release notes as a Remix context blob
(`releaseNotes.content.body`) but **do not include release dates** — only version
numbers and notes. (An earlier version of this README claimed newer models are served
through a dated `support-car-content` API; that was not confirmed for the manual
source and is not used here.) So this tracker shows the full changelog and version
history, but cannot compute "days between releases" or predict the next update.

## Adding another model

Everything is driven by the `MODELS` list at the top of `scripts/scrape.py` — each
entry is `{slug, label, manual_url}`. Add a new model (e.g. `polestar-4`) there and
the scraper produces `data/polestar-4.json` + `feed-polestar-4.xml`, the page grows a
new toggle tab, and the notifier workflow covers it automatically.

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
