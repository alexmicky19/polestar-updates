#!/usr/bin/env python3
"""
Scrape Polestar software-update release notes and regenerate index.html +
per-model data.json / feed.xml.

Two data sources, chosen per model via its `source` field:

- "html" (Polestar 2): the manual page server-renders a Remix context blob that
  contains a `releaseNotes.content.body` structure. We fetch the HTML, isolate that
  object by balanced-brace scanning, and walk it into a flat list of {version,
  notes[]}. P2 is not exposed by the JSON API, so it stays HTML-scraped.
- "api" (Polestar 3 and Polestar 4): the manual page is backed by a public JSON
  API. We resolve the en-GB release-notes document and read structured segments,
  including market/config validity gates. The public manual is only a last-resort
  fallback when the API cannot be fetched or fails validation.

Polestar publishes no true release *date* in either source. The `cmsSoftwareVersion`
build-week code is *not* a release date — it's when the build was registered, and it
leads the actual public rollout by 1-3 weeks (so trusting it produces future-dated,
not-yet-visible "releases"). We therefore date every version by `first_seen` — the
date this tracker first observed it — the same approach as the Polestar 4 tracker.

No browser / heavy deps required — just urllib from the stdlib.
"""
import json, re, sys, urllib.request, urllib.error, pathlib, html as _html, datetime, socket, time
from urllib.parse import urlparse

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
    {
        "slug": "polestar-4",
        "label": "Polestar 4",
        "source": "api",
        "model_code": "814",
        "manual_url": "https://www.polestar.com/uk/manual/polestar-4/2026/software-updates/",
    },
    {
        "slug": "polestar-5",
        "label": "Polestar 5",
        "source": "html",
        "manual_url": "https://www.polestar.com/uk/manual/polestar-5/2026/software-updates/",
    },
]

# Public, unauthenticated Volvo/Polestar release-notes API. The reference P4
# implementation uses this hostname. Keep it as the single verified host until
# an alternative has been proven to serve the same API; do not guess hostnames.
API_BASES = ("https://support-car-content.polestar.volvo.care",)
API_BASE = API_BASES[0]

# Lists every model and the software versions Polestar has *registered* (incl. builds
# not yet published as release notes). Used to derive the "in the pipeline" list.
AVAILABLE_MODELS_PATH = "/api/car-content/available-car-models"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"

SITE_URL = "https://alexmicky19.github.io/polestar-updates/"

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"
DATA_DIR = ROOT / "data"


def fetch(url: str, retries: int = 4, timeout: int = 45) -> str:
    """Fetch a URL with bounded retries for transient DNS, network and server errors.

    DNS failures on hosted runners can be brief. Retry those (and HTTP 429/5xx)
    with exponential backoff, but fail immediately for permanent HTTP errors.
    """
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code != 429 and not 500 <= exc.code < 600:
                raise
            error = exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            error = exc

        if attempt == retries - 1:
            raise error

        delay = 5 * (2 ** attempt)
        print(
            f"  Request failed ({attempt + 1}/{retries}): {error}. "
            f"Retrying in {delay}s...",
            file=sys.stderr,
        )
        time.sleep(delay)

    raise RuntimeError(f"Request failed after {retries} attempts: {url}")


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
    """GET JSON from the verified Polestar car-content API host."""
    if path_or_url.startswith("http"):
        return json.loads(fetch(path_or_url))
    path = "/" + path_or_url.lstrip("/")
    errors = []
    for base in API_BASES:
        try:
            return json.loads(fetch(base + path))
        except Exception as exc:
            errors.append(f"{base}: {exc}")
    raise RuntimeError("Polestar API request failed: " + " | ".join(errors))


def positive_gates(expr) -> list:
    """Return positive market/config atoms; negated-only gates are broad."""
    if expr is None or expr == "true":
        return []
    atoms = re.sub(r"[()]", " ", str(expr)).replace("|", "&").split("&")
    return [atom.strip() for atom in atoms
            if atom.strip() and not atom.strip().startswith("!")]


def _flatten_api_notes(node, out: list, inherited=None) -> None:
    """Flatten rich text into note lines, inheriting market/config validity gates."""
    inherited = inherited or {"validities": None, "features": None}
    if isinstance(node, list):
        for child in node:
            _flatten_api_notes(child, out, inherited)
        return
    if isinstance(node, str):
        text = re.sub(r"\\s+", " ", node).strip()
        if text:
            out.append({"text": text, **inherited})
        return
    if not isinstance(node, dict) or node.get("type") in {"title", "footnote"}:
        return
    gates = {
        "validities": node.get("validities", inherited["validities"]),
        "features": node.get("features", inherited["features"]),
    }
    children = node.get("children") or []
    if node.get("type") in {"paragraph", "listItem", "listIntro"}:
        own, nested = [], []
        for child in children if isinstance(children, list) else [children]:
            if isinstance(child, dict) and child.get("type") in {"unorderedList", "orderedList", "note"}:
                nested.append(child)
            else:
                pieces = []
                _flatten_api_notes(child, pieces, gates)
                own.extend(pieces)
        text = re.sub(r"\\s+", " ", "".join(p["text"] for p in own)).strip()
        if text:
            out.append({"text": text, **gates})
        for child in nested:
            _flatten_api_notes(child, out, gates)
        return
    _flatten_api_notes(children, out, gates)


def parse_api_release_notes(document: dict) -> list:
    """Parse all version segments and retain market/config-specific note flags."""
    body = document.get("releaseNotesDocument", {}).get("body", [])
    updates, current = [], None
    for segment in body:
        if not isinstance(segment, dict):
            continue
        children = segment.get("children") or []
        title_node = next((n for n in children if isinstance(n, dict) and n.get("type") == "title"), None)
        title_children = title_node.get("children", []) if title_node else []
        title = "".join(x for x in title_children if isinstance(x, str))
        match = re.search(r"Software Version\\s+(.+?)\\s*:?\\s*$", title, re.I)
        if match:
            version = match.group(1).strip()
            current = next((u for u in updates if u["version"] == version), None)
            if current is None:
                current = {"version": version, "notes": [], "_gates": {}, "cms_version": None}
                updates.append(current)
        if current is None:
            continue
        inherited = {"validities": segment.get("validities"), "features": segment.get("features")}
        lines = []
        _flatten_api_notes(children, lines, inherited)
        for line in lines:
            note = line["text"]
            if note not in current["notes"]:
                current["notes"].append(note)
            current["_gates"].setdefault(note, []).append(line)
        cms = segment.get("cmsSoftwareVersion")
        if cms is not None:
            try:
                current["cms_version"] = max(int(cms), int(current["cms_version"] or 0))
            except (TypeError, ValueError):
                pass

    for update in updates:
        specific = []
        for note in update["notes"]:
            occurrences = update["_gates"].get(note, [])
            if occurrences and all(
                positive_gates(o.get("validities")) or positive_gates(o.get("features"))
                for o in occurrences
            ):
                specific.append(note)
        del update["_gates"]
        if specific:
            update["market_specific"] = specific
    return updates


def fetch_api_versions(model_code: str) -> tuple[list, int | None]:
    """Fetch cumulative release notes and manifest's latest published build."""
    manifest = fetch_api_json(
        f"/api/car-content/SOFTWARE_RELEASE_NOTES/{model_code}/UNTIL/99.0.0"
    )
    entries = manifest.get("content") or []
    if not entries:
        raise RuntimeError(f"API manifest for model {model_code} has no content entries")
    entry = next(
        (item for item in entries
         if (item.get("language") or item.get("locale") or "").lower() in ("en-gb", "en_gb", "en")),
        entries[0],
    )
    relative_url = entry.get("relativeUrl")
    if not relative_url:
        raise RuntimeError(f"API manifest for model {model_code} has no relativeUrl")
    versions = parse_api_release_notes(fetch_api_json(relative_url))
    space = manifest.get("spaceSoftwareVersion")
    try:
        space = int(space) if space is not None else None
    except (TypeError, ValueError):
        space = None
    return versions, space


def _manual_probe_builds(published_max: int, weeks_ahead: int = 12) -> list[int]:
    """Candidate YYWW0-YYWW2 build IDs from published week through 12 weeks ahead."""
    now = datetime.date.today()
    iso = now.isocalendar()
    end_year, end_week = iso.year % 100, iso.week + weeks_ahead
    if end_week > 52:
        end_year += 1
        end_week -= 52
    yy, ww = published_max // 1000, (published_max // 10) % 100
    candidates = [published_max]
    for _ in range(80):
        if (yy, ww) > (end_year, end_week):
            break
        for suffix in range(3):
            value = yy * 1000 + ww * 10 + suffix
            if value > published_max:
                candidates.append(value)
        ww += 1
        if ww > 53:
            yy, ww = yy + 1, 1
    return candidates


def _probe_manual_build(base: str, model_code: str, build: int) -> bool:
    url = f"{base}/api/car-content/USER_MANUAL/{model_code}/AT/{build}"
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def _manual_builds_to_models(manuals: list, model_code: str) -> list:
    by_build = {}
    for manual in manuals:
        try:
            internal = int(manual.get("spaceSoftwareVersion"))
        except (TypeError, ValueError):
            continue
        car = manual.get("carSoftwareVersion")
        label = str(car).strip() if car is not None and str(car) != str(internal) else None
        by_build[internal] = {
            "internalVersion": internal,
            **({"carVersion": label} if label else {}),
        }
    return [{"modelCode": str(model_code), "softwareVersions": sorted(
        by_build.values(), key=lambda item: item["internalVersion"]
    )}]


def fetch_api_pipeline(model_code: str, published_max: int | None) -> list:
    """Use the public models feed, then USER_MANUAL build probing if it is empty."""
    if published_max is None:
        return []
    try:
        doc = fetch_api_json(AVAILABLE_MODELS_PATH)
        models = doc if isinstance(doc, list) else doc.get("models") or doc.get("carModels") or []
        model = next((m for m in models if str(m.get("modelCode")) == str(model_code)), None)
        versions = model.get("softwareVersions", []) if model else []
        if versions:
            return [
                {"internal_version": int(v["internalVersion"]),
                 "version": v.get("carVersion")}
                for v in sorted(versions, key=lambda x: int(x.get("internalVersion", 0)))
                if int(v.get("internalVersion", 0)) > published_max
            ]
    except Exception as exc:
        print(f"Available-models feed unavailable for {model_code}: {exc}", file=sys.stderr)

    try:
        base = API_BASES[0]
        candidates = _manual_probe_builds(published_max)
        with __import__("concurrent.futures").futures.ThreadPoolExecutor(max_workers=8) as pool:
            hits = [b for b, exists in zip(
                candidates, pool.map(lambda b: _probe_manual_build(base, model_code, b), candidates)
            ) if exists]
        manuals = [fetch_api_json(f"/api/car-content/USER_MANUAL/{model_code}/AT/{b}")
                   for b in [*hits, "99.0.0"]]
        models = _manual_builds_to_models(manuals, model_code)
        model = models[0]
        return [
            {"internal_version": int(v["internalVersion"]),
             "version": v.get("carVersion")}
            for v in model["softwareVersions"]
            if int(v["internalVersion"]) > published_max
        ]
    except Exception as exc:
        print(f"USER_MANUAL build probe unavailable for {model_code}: {exc}", file=sys.stderr)
        return []


def attach_api_build_numbers(versions: list, model_code: str, published_max: int | None) -> list:
    """Attach registered YYWW build numbers to release versions by normalized label."""
    if published_max is None:
        return versions
    pipeline = fetch_api_pipeline(model_code, published_max)
    # Pipeline contains only unpublished builds; query the published max separately
    # through USER_MANUAL so its label/build pair can be matched to release notes.
    try:
        latest = fetch_api_json(f"/api/car-content/USER_MANUAL/{model_code}/AT/99.0.0")
        models = _manual_builds_to_models([latest], model_code)
        records = models[0]["softwareVersions"]
    except Exception:
        records = []
    by_key = {
        ".".join(re.findall(r"\\d+", str(v.get("carVersion", "")))): v["internalVersion"]
        for v in records if v.get("carVersion")
    }
    return [
        {**v, **({"internal_version": by_key[".".join(re.findall(r"\\d+", v["version"]))]}
                 if ".".join(re.findall(r"\\d+", v["version"])) in by_key else {})}
        for v in versions
    ]


def validate_model_versions(versions: list, cached: list | None = None) -> None:
    """Reject empty or suspiciously truncated source responses before accepting them."""
    if not versions:
        raise RuntimeError("source returned zero software versions")
    empty = next((v for v in versions if not v.get("notes")), None)
    if empty:
        raise RuntimeError(f"version {empty.get('version')} has no parsed notes")
    if cached and len(versions) < max(1, len(cached) // 2):
        raise RuntimeError(
            f"source version count collapsed from {len(cached)} to {len(versions)}"
        )


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
    {version, notes[], cms_version}. Segments that share a version
    (market/config splits) are merged. Ordering is left to the shared sort in
    scrape_model. ``space_software_version`` is the manifest's newest published
    build code (the "in the pipeline" threshold), or None. P2 is not in the API —
    only API models use this. The `cms_version` build-week code is kept for
    traceability only; dates come from `first_seen` (see module docstring)."""
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
        versions.append(merged[ver])
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
    """One <item> per version, newest first. pubDate is the version's stable
    first_seen date (when this tracker first observed it)."""
    def esc(s):
        return _html.escape(s, quote=True)

    feed_url = SITE_URL + f"feed-{model['slug']}.xml"
    items = []
    for v in versions:
        desc = "\n".join(("• " + n) if not n.startswith("### ") else ("\n" + n[4:] + ":")
                         for n in v["notes"]).strip()
        link = SITE_URL + "#" + esc(model["slug"] + "-" + v["version"].replace(".", "-"))
        pub = v["first_seen"]
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
    cached_path = DATA_DIR / f"{model['slug']}.json"
    cached = []
    if cached_path.exists():
        try:
            cached = json.loads(cached_path.read_text(encoding="utf-8"))
        except Exception:
            cached = []

    api_error = None
    if model.get("source") == "api":
        try:
            versions, space = fetch_api_versions(model["model_code"])
            validate_model_versions(versions, cached)
            meta["source_used"] = "api"
            meta["api_model_code"] = model["model_code"]
            meta["space_software_version"] = space
            versions = attach_api_build_numbers(versions, model["model_code"], space)
            meta["upcoming"] = fetch_api_pipeline(model["model_code"], space)
        except Exception as exc:
            api_error = exc
            print(f"  API source failed for {model['slug']}: {exc}", file=sys.stderr)
            versions = None
        # The manual is a last-resort live source, never the first choice for API models.
        if versions is None:
            page = pathlib.Path(local).read_text(encoding="utf-8", errors="replace") if local else fetch(model["manual_url"])
            rn = extract_release_notes_object(page)
            versions = parse_versions(rn)
            validate_model_versions(versions, cached)
            meta["source_used"] = "manual_fallback"
            meta["api_error"] = str(api_error)
            meta["upcoming"] = []
    else:
        page = pathlib.Path(local).read_text(encoding="utf-8", errors="replace") if local else fetch(model["manual_url"])
        rn = extract_release_notes_object(page)
        versions = parse_versions(rn)
        validate_model_versions(versions, cached)
        meta["source_used"] = "manual"
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

    failed_models = []
    successful_models = 0

    # Report DNS status up front. This is diagnostic only: HTML-sourced models
    # can still be refreshed when the API host is unavailable.
    for base in API_BASES:
        host = urlparse(base).hostname
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            print(f"API DNS OK: {host}")
        except OSError as exc:
            print(f"::warning title=Polestar API DNS::{host} did not resolve: {exc}")

    for model in MODELS:
        slug = model["slug"]
        data_path = DATA_DIR / f"{slug}.json"
        meta_path = DATA_DIR / f"{slug}.meta.json"
        try:
            versions, meta = scrape_model(model, local_map.get(slug))
        except (Exception, SystemExit) as exc:
            print(f"ERROR scraping {slug}: {exc}", file=sys.stderr)
            # Keep the last known-good data in the generated index, but do not
            # rewrite this model's files from a failed/partial response.
            try:
                versions = json.loads(data_path.read_text(encoding="utf-8"))
                if not isinstance(versions, list) or not versions:
                    raise ValueError("cached version data is empty or invalid")
                meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            except Exception as cache_error:
                # Keep the model key in the generated page even on its first
                # bootstrap failure; a later successful scrape fills it in.
                versions, meta = [], {}
                print(
                    f"WARNING no cached data for {slug}: {cache_error}",
                    file=sys.stderr,
                )
            else:
                latest = f", latest {versions[0]['version']}" if versions else ""
                print(
                    f"  {slug}: using {len(versions)} cached versions{latest}; "
                    "this run is incomplete",
                    file=sys.stderr,
                )

            all_data[slug] = versions
            all_meta[slug] = meta
            failed_models.append(slug)
            continue

        successful_models += 1
        data_path.write_text(
            json.dumps(versions, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        (ROOT / f"feed-{slug}.xml").write_text(build_feed(model, versions), encoding="utf-8")
        all_data[slug] = versions
        all_meta[slug] = meta
        # Persist meta alongside the data for traceability (the UI reads the inlined
        # META object, not this file). Only meaningful for API models.
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        up = meta.get("upcoming") or []
        print(f"  {slug}: {len(versions)} versions, latest {versions[0]['version']}"
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

    # Publish successful model updates even when another source is temporarily
    # unavailable. Failed models retain their last-known-good cache (or an empty
    # bootstrap list); surface a warning instead of discarding successful work.
    if failed_models:
        message = "Scrape incomplete; preserved cached data for: " + ", ".join(failed_models)
        print(f"::warning title=Partial Polestar scrape::{message}")
        if successful_models == 0:
            raise SystemExit(message)


if __name__ == "__main__":
    main()
