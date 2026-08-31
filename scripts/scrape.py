#!/usr/bin/env python3
"""
Scrape Polestar software-update release notes and regenerate index.html +
per-model data.json / feed.xml.

Two data sources, chosen per model via its `source` field:

- "html" (Polestar 2): the manual page server-renders a Remix context blob that
  contains a `releaseNotes.content.body` structure. We fetch the HTML, isolate that
  object by balanced-brace scanning, and walk it into a flat list of {version,
  notes[]}. P2 is not exposed by the JSON API, so it stays HTML-scraped.
- "api" (Polestar 3): the manual pages are backed by a public, unauthenticated JSON
  API. We resolve the en-GB release-notes document and read structured segments,
  each carrying a `cmsSoftwareVersion` YYWW build-week code.

Polestar publishes no true release *date* in either source. For API models we derive
an approximate "released" date from the build-week code (Monday of that ISO week);
for all models we also persist a stable per-version `first_seen` date as a fallback.

No browser / heavy deps required — just urllib from the stdlib.
"""
import json, re, sys, urllib.request, pathlib, html as _html, datetime

# Each model: slug used in filenames/URLs, human label, data source, and its manual
# URL. API models also carry a numeric `model_code`. Add a new model here (e.g.
# polestar-4, model_code 814) and everything else follows.
MODELS = [
    {
        "slug": "polestar-2",
        "label": "Polestar 2",
        "source": "html",
        "manual_url": "https://www.polestar.com/uk/manual/polestar-2/2027/software-updates/",
    },
    {
        "slug": "polestar-3",
        "label": "Polestar 3",
        "source": "api",
        "model_code": "359",
        "manual_url": "https://www.polestar.com/uk/manual/polestar-3/2025/software-updates/",
    },
]

# Public, unauthenticated Polestar release-notes JSON API (backs the manual pages).
API_BASE = "https://support-car-content.polestar.volvo.care"

# Lists every model and the software versions Polestar has *registered* (incl. builds
# not yet published as release notes). Used to derive the "in the pipeline" list.
AVAILABLE_MODELS_PATH = "/api/car-content/available-car-models"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"

SITE_URL = "https://alexmicky19.github.io/polestar-updates/"

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"
DATA_DIR = ROOT / "data"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read().decode("utf-8", "replace")


def _find_matching_brace(page: str, start: int) -> int:
    """Index of the '}' matching the '{' at `start`, respecting quoted strings/escapes."""
    depth, in_str, esc = 0, False, False
    for k in range(start, len(page)):
        c = page[k]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return k
    raise SystemExit("unbalanced braces scanning releaseNotes")


def extract_release_notes_object(page: str) -> dict:
    """Find `"releaseNotes":{...}` and return it as a parsed dict via balanced braces."""
    i = page.find('"releaseNotes":')
    if i == -1:
        raise SystemExit("could not find releaseNotes in page")
    # The blob lives inside the Remix context which is itself a JSON string, so the
    # HTML we downloaded has it JSON-escaped once (\" and \\n). Scan on the raw text
    # counting braces while respecting escaped quotes.
    j = page.find("{", i)
    end = _find_matching_brace(page, j)
    return decode_escaped_json(page[j:end + 1])


def decode_escaped_json(raw: str) -> dict:
    """The object text is escaped as it appeared inside a JSON string. Unescape then parse."""
    # First try parsing directly (in case it's already clean).
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Otherwise it's escaped: wrap in quotes and let json decode the escapes, then parse.
    unescaped = json.loads('"' + raw + '"')
    return json.loads(unescaped)


def _emit(notes: list, text) -> None:
    """Append a stripped, non-empty string to notes; ignore anything else."""
    if isinstance(text, str) and text.strip():
        notes.append(text.strip())


# Node types that open a sub-heading context for their children (title -> "### ").
_SUB_TYPES = {"subSegment", "note"}
# Node types whose children are either leaf text or nested content.
_TEXT_TYPES = {"paragraph", "listItem"}


def _walk(node, sub: bool, notes: list) -> None:
    """Recursively flatten a release-notes node into `notes`."""
    if isinstance(node, list):
        for n in node:
            _walk(n, sub, notes)
        return
    if not isinstance(node, dict):
        _emit(notes, node)
        return

    t, ch = node.get("type"), node.get("children")
    if t == "title":
        # sub-segment / note title -> heading; top segment title handled by caller
        if sub and isinstance(ch, str):
            notes.append("### " + ch.strip())
    elif t in _SUB_TYPES:
        _walk(ch, True, notes)
    elif t in _TEXT_TYPES and isinstance(ch, str):
        _emit(notes, ch)
    elif ch is not None:
        # text type with nested children, list wrappers, or any other container
        _walk(ch, sub, notes)


def walk_notes(children) -> list:
    """Flatten a segment's children into a list of note strings.
    Sub-segment titles are prefixed with '### ' (the UI renders them as sub-headings)."""
    notes: list = []
    _walk(children, False, notes)
    return notes


def parse_versions(rn: dict) -> list:
    body = rn.get("content", {}).get("body", [])
    versions = []
    for seg in body:
        children = seg.get("children")
        # A version segment starts with a title "Updates in software version PX.Y.Z"
        title = None
        if isinstance(children, list):
            for n in children:
                if isinstance(n, dict) and n.get("type") == "title" and isinstance(n.get("children"), str):
                    title = n["children"]
                    break
        if not title or "software version" not in title.lower():
            continue
        ver = re.sub(r"^Updates in software version\s*", "", title, flags=re.I).strip()
        # notes = everything except the top-level title
        rest = [n for n in children if not (isinstance(n, dict) and n.get("type") == "title" and n.get("children") == title)]
        notes = walk_notes(rest)
        versions.append({"version": ver, "notes": notes})
    return versions


def fetch_api_json(path_or_url: str) -> dict:
    """GET JSON from the API. Accepts an absolute URL or an API path (with or
    without a leading slash); follows redirects (the content host 303-redirects to
    an internal mirror)."""
    if path_or_url.startswith("http"):
        url = path_or_url
    else:
        url = API_BASE + "/" + path_or_url.lstrip("/")
    return json.loads(fetch(url))


def week_code_to_date(code) -> str | None:
    """Decode a Polestar YYWW build-week code to the Monday of that ISO week.

    The code is `cmsSoftwareVersion` / `internalVersion`, e.g. 26380 -> year 2026,
    week 38 -> 2026-09-14. Returns an ISO date string, or None for a missing/bad
    code (there is no real release date in the source, so this is best-effort)."""
    try:
        n = int(code)
    except (TypeError, ValueError):
        return None
    year = 2000 + n // 1000
    week = (n % 1000) // 10
    if not (1 <= week <= 53):
        return None
    try:
        return datetime.date.fromisocalendar(year, week, 1).isoformat()
    except ValueError:
        return None


def _seg_version(seg: dict) -> str | None:
    """Resolve a segment's version. Prefer the explicit `softwareVersion`; fall back
    to the title text ("Updates in Software Version PX.Y.Z"), which some segments use
    instead of the field. Returns a normalised `PX.Y.Z` string or None."""
    sw = seg.get("softwareVersion")
    if sw:
        return sw if sw.upper().startswith("P") else "P" + sw
    children = seg.get("children")
    if isinstance(children, list):
        for n in children:
            if isinstance(n, dict) and n.get("type") == "title" and isinstance(n.get("children"), str):
                m = re.search(r"software version\s*(P?\d[\d.]*)", n["children"], flags=re.I)
                if m:
                    v = m.group(1)
                    return v if v.upper().startswith("P") else "P" + v
    return None


def fetch_api_versions(model_code: str):
    """Resolve a model's en-GB release-notes document from the JSON API and return
    ``(versions, space_software_version)`` where versions is a list of
    {version, notes[], cms_version, released?}. Segments that share a version
    (market/config splits) are merged. Ordering is left to the shared sort in
    scrape_model. ``space_software_version`` is the manifest's newest published
    build code (the "in the pipeline" threshold), or None. P2 is not in the API —
    only API models use this."""
    manifest = fetch_api_json(f"/api/car-content/SOFTWARE_RELEASE_NOTES/{model_code}/UNTIL/99.0.0")
    content = manifest.get("content", [])
    if not content:
        raise SystemExit(f"API manifest for model {model_code} had no content entries")
    # Prefer the British-English document; fall back to the first available.
    entry = next(
        (c for c in content
         if (c.get("locale") or c.get("language") or "").lower() in ("en-gb", "en_gb", "en")),
        content[0])
    rel = entry.get("relativeUrl")
    if not rel:
        raise SystemExit(f"API manifest entry for model {model_code} had no relativeUrl")

    doc = fetch_api_json(rel)
    body = doc.get("releaseNotesDocument", {}).get("body", [])
    merged: dict = {}  # version -> {version, notes[], cms_version}
    order: list = []   # preserve first-seen order of versions
    for seg in body:
        # Version segments are tagged release-notes; the version is in the field or title.
        if seg.get("subtype") != "release-notes":
            continue
        version = _seg_version(seg)
        if not version:
            continue
        children = seg.get("children")
        # Drop the leading title node (same convention as the HTML parse_versions).
        if isinstance(children, list):
            rest = [n for n in children if not (isinstance(n, dict) and n.get("type") == "title")]
        else:
            rest = children
        notes = walk_notes(rest)
        cms = seg.get("cmsSoftwareVersion")
        if version in merged:
            # Same version split across segments: append new notes, keep the highest cms.
            m = merged[version]
            m["notes"].extend(n for n in notes if n not in m["notes"])
            if cms and (m.get("cms_version") is None or cms > m["cms_version"]):
                m["cms_version"] = cms
        else:
            merged[version] = {"version": version, "notes": list(notes), "cms_version": cms}
            order.append(version)

    versions = []
    for ver in order:
        v = merged[ver]
        released = week_code_to_date(v.get("cms_version"))
        if released:
            v["released"] = released
        versions.append(v)
    if not versions:
        raise SystemExit(f"API returned zero release-notes segments for model {model_code}")
    # The newest published build code — anything registered above this is "upcoming".
    space = manifest.get("spaceSoftwareVersion")
    try:
        space = int(space) if space is not None else None
    except (TypeError, ValueError):
        space = None
    return versions, space


def fetch_api_pipeline(model_code: str, published_max) -> list:
    """Return builds Polestar has *registered* but not yet published notes for:
    entries in `available-car-models` whose YYWW `internalVersion` is greater than
    the newest published build (`published_max`). Best-effort — returns [] on any
    miss so a pipeline hiccup never aborts the scrape. Each item is
    {internal_version, version} sorted by internal_version ascending; `version` is
    the `carVersion` (P-prefixed) or None when Polestar hasn't named the build yet."""
    if published_max is None:
        return []
    try:
        doc = fetch_api_json(AVAILABLE_MODELS_PATH)
    except Exception:
        return []
    models = doc if isinstance(doc, list) else doc.get("models") or doc.get("carModels") or []
    if not isinstance(models, list):
        return []
    entry = next((m for m in models
                  if isinstance(m, dict) and str(m.get("modelCode")) == str(model_code)), None)
    if not entry:
        return []
    swvs = entry.get("softwareVersions")
    if not isinstance(swvs, list):
        return []
    upcoming = []
    for sw in swvs:
        if not isinstance(sw, dict):
            continue
        try:
            iv = int(sw.get("internalVersion"))
        except (TypeError, ValueError):
            continue
        if iv <= published_max:
            continue
        car = sw.get("carVersion")
        version = None
        if isinstance(car, str) and car.strip() and car.strip().lower() not in ("none", "0.0.0"):
            c = car.strip()
            version = c if c.upper().startswith("P") else "P" + c
        upcoming.append({"internal_version": iv, "version": version})
    upcoming.sort(key=lambda u: u["internal_version"])
    return upcoming


def version_key(v: str):
    """Sort key: strip leading P, compare numerically (newest first when reversed)."""
    parts = re.sub(r"^P", "", v, flags=re.I).split(".")
    return [int(p) if p.isdigit() else 0 for p in parts]


def load_first_seen(data_path: pathlib.Path) -> dict:
    """Read the previously persisted first-seen dates so pubDates stay stable."""
    if not data_path.exists():
        return {}
    try:
        prev = json.loads(data_path.read_text(encoding="utf-8"))
        return {v["version"]: v["first_seen"] for v in prev if v.get("first_seen")}
    except Exception:
        return {}


def rss_date(iso: str) -> str:
    """YYYY-MM-DD -> RFC 822 date (RSS pubDate), at 00:00:00 GMT."""
    d = datetime.date.fromisoformat(iso)
    dt = datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc)
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def build_feed(model: dict, versions: list) -> str:
    """One <item> per version, newest first. pubDate is the derived release date
    when available (API models), else the stable first_seen date."""
    def esc(s):
        return _html.escape(s, quote=True)

    feed_url = SITE_URL + f"feed-{model['slug']}.xml"
    items = []
    for v in versions:
        desc = "\n".join(("• " + n) if not n.startswith("### ") else ("\n" + n[4:] + ":")
                         for n in v["notes"]).strip()
        link = SITE_URL + "#" + esc(model["slug"] + "-" + v["version"].replace(".", "-"))
        pub = v.get("released") or v["first_seen"]
        items.append(
            "    <item>\n"
            f"      <title>{esc(model['label'])} software {esc(v['version'])}</title>\n"
            f"      <link>{link}</link>\n"
            f"      <guid isPermaLink=\"false\">{esc(model['slug'])}-{esc(v['version'])}</guid>\n"
            f"      <pubDate>{rss_date(pub)}</pubDate>\n"
            f"      <description>{esc(desc)}</description>\n"
            "    </item>"
        )
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        f"    <title>{esc(model['label'])} Software Updates (Unofficial)</title>\n"
        f"    <link>{SITE_URL}</link>\n"
        f'    <atom:link href="{feed_url}" rel="self" type="application/rss+xml"/>\n'
        f"    <description>New {esc(model['label'])} software versions and their release notes, "
        "sourced from Polestar's owner's manual. Unofficial.</description>\n"
        "    <language>en-GB</language>\n"
        f"    <lastBuildDate>{now}</lastBuildDate>\n"
        + "\n".join(items) + "\n"
        "  </channel>\n"
        "</rss>\n"
    )


def scrape_model(model: dict, local: str | None):
    """Return ``(versions, meta)`` for one model: the sorted, first-seen-stamped
    version list plus a per-model meta dict.

    Dispatches on model['source']: HTML models parse the manual page's Remix blob
    (meta is empty); API models read the JSON release-notes document, carry a derived
    release date, and populate meta with `space_software_version` and the `upcoming`
    pipeline (builds registered but not yet published)."""
    meta: dict = {}
    if model.get("source") == "api":
        versions, space = fetch_api_versions(model["model_code"])
        meta["space_software_version"] = space
        meta["upcoming"] = fetch_api_pipeline(model["model_code"], space)
    else:
        page = pathlib.Path(local).read_text(encoding="utf-8", errors="replace") if local else fetch(model["manual_url"])
        rn = extract_release_notes_object(page)
        versions = parse_versions(rn)
    if not versions:
        raise SystemExit(f"parsed zero versions for {model['slug']} — aborting so we don't wipe good data")

    versions.sort(key=lambda v: version_key(v["version"]), reverse=True)
    data_path = DATA_DIR / f"{model['slug']}.json"
    seen = load_first_seen(data_path)
    today = datetime.date.today().isoformat()
    for v in versions:
        v["first_seen"] = seen.get(v["version"], today)
    return versions, meta


def main():
    # Optional per-model local override for testing: --local <slug> <path>
    local_map = {}
    args = sys.argv[1:]
    while args and args[0] == "--local":
        local_map[args[1]] = args[2]
        args = args[3:]

    DATA_DIR.mkdir(exist_ok=True)
    today = datetime.date.today().isoformat()
    all_data = {}  # slug -> versions, for the embedded DATA object
    all_meta = {}  # slug -> meta (space_software_version, upcoming), for META object

    for model in MODELS:
        versions, meta = scrape_model(model, local_map.get(model["slug"]))
        (DATA_DIR / f"{model['slug']}.json").write_text(
            json.dumps(versions, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        (ROOT / f"feed-{model['slug']}.xml").write_text(build_feed(model, versions), encoding="utf-8")
        all_data[model["slug"]] = versions
        all_meta[model["slug"]] = meta
        # Persist meta alongside the data for traceability (the UI reads the inlined
        # META object, not this file). Only meaningful for API models.
        (DATA_DIR / f"{model['slug']}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        up = meta.get("upcoming") or []
        print(f"  {model['slug']}: {len(versions)} versions, latest {versions[0]['version']}"
              + (f", {len(up)} upcoming" if model.get("source") == "api" else ""))

    # Splice the combined DATA object (keyed by slug) + META + MODELS metadata into index.html.
    models_meta = [{"slug": m["slug"], "label": m["label"], "manual_url": m["manual_url"]} for m in MODELS]
    out_data = json.dumps(all_data, ensure_ascii=False)
    out_meta = json.dumps(all_meta, ensure_ascii=False)
    out_models = json.dumps(models_meta, ensure_ascii=False)

    index = INDEX.read_text(encoding="utf-8")
    new_index, n1 = re.subn(r"const DATA = .*?;\n", "const DATA = " + out_data + ";\n", index, count=1, flags=re.S)
    new_index, nm = re.subn(r"const META = .*?;\n", "const META = " + out_meta + ";\n", new_index, count=1, flags=re.S)
    new_index, n2 = re.subn(r"const MODELS = .*?;\n", "const MODELS = " + out_models + ";\n", new_index, count=1, flags=re.S)
    if n1 != 1 or nm != 1 or n2 != 1:
        raise SystemExit("could not locate 'const DATA'/'const META'/'const MODELS' in index.html")
    new_index = re.sub(r'const SCRAPED = "[^"]*";', f'const SCRAPED = "{today}";', new_index)
    new_index = re.sub(r'Data captured \d{4}-\d{2}-\d{2}', f'Data captured {today}', new_index)

    changed = new_index != index
    if changed:
        INDEX.write_text(new_index, encoding="utf-8")
    print(f"index.html: {'updated' if changed else 'no change'}")


if __name__ == "__main__":
    main()
