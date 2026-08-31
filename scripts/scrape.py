#!/usr/bin/env python3
"""
Scrape Polestar software-update release notes from the official owner's manuals
and regenerate index.html + per-model data.json / feed.xml.

Data source: each model's manual page server-renders a Remix context blob that
contains a `releaseNotes.content.body` structure. We fetch the HTML, isolate that
object by balanced-brace scanning, walk it into a flat list of {version, notes[]},
and splice the per-model results into index.html's embedded DATA object.

Polestar does not publish release *dates* in the manual (verified for both P2 and
P3), so we persist a stable per-version `first_seen` date instead.

No browser / heavy deps required — just urllib from the stdlib.
"""
import json, re, sys, urllib.request, pathlib, html as _html, datetime

# Each model: slug used in filenames/URLs, human label, and its manual URL.
# Add a new model here (e.g. polestar-4) and everything else follows.
MODELS = [
    {
        "slug": "polestar-2",
        "label": "Polestar 2",
        "manual_url": "https://www.polestar.com/uk/manual/polestar-2/2027/software-updates/",
    },
    {
        "slug": "polestar-3",
        "label": "Polestar 3",
        "manual_url": "https://www.polestar.com/uk/manual/polestar-3/2025/software-updates/",
    },
]

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"

SITE_URL = "https://alexmicky19.github.io/polestar-updates/"

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"
DATA_DIR = ROOT / "data"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read().decode("utf-8", "replace")


def extract_release_notes_object(page: str) -> dict:
    """Find `"releaseNotes":{...}` and return it as a parsed dict via balanced braces."""
    key = '"releaseNotes":'
    i = page.find(key)
    if i == -1:
        raise SystemExit("could not find releaseNotes in page")
    j = page.find("{", i)
    # The blob lives inside the Remix context which is itself a JSON string, so the
    # HTML we downloaded has it JSON-escaped once (\" and \\n). Scan on the raw text
    # counting braces while respecting escaped quotes.
    depth, k, in_str, esc = 0, j, False, False
    while k < len(page):
        c = page[k]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    raw = page[j:k + 1]
                    return decode_escaped_json(raw)
        k += 1
    raise SystemExit("unbalanced braces scanning releaseNotes")


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


def walk_notes(children) -> list:
    """Flatten a segment's children into a list of note strings.
    Sub-segment titles are prefixed with '### ' (the UI renders them as sub-headings)."""
    notes = []

    def rec(node, sub=False):
        if isinstance(node, list):
            for n in node:
                rec(n, sub)
            return
        if not isinstance(node, dict):
            if isinstance(node, str) and node.strip():
                notes.append(node.strip())
            return
        t = node.get("type")
        ch = node.get("children")
        if t == "title":
            # sub-segment / note title -> heading; top segment title handled by caller
            if sub and isinstance(ch, str):
                notes.append("### " + ch.strip())
        elif t == "paragraph":
            if isinstance(ch, str) and ch.strip():
                notes.append(ch.strip())
            else:
                rec(ch, sub)
        elif t == "subSegment":
            rec(ch, True)
        elif t in ("unorderedList", "orderedList"):
            rec(ch, sub)
        elif t == "listItem":
            if isinstance(ch, str) and ch.strip():
                notes.append(ch.strip())
            else:
                rec(ch, sub)
        elif t == "note":
            rec(ch, True)
        else:
            if ch is not None:
                rec(ch, sub)

    rec(children)
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
    """One <item> per version, newest first, pubDate = first_seen date."""
    def esc(s):
        return _html.escape(s, quote=True)

    feed_url = SITE_URL + f"feed-{model['slug']}.xml"
    items = []
    for v in versions:
        desc = "\n".join(("• " + n) if not n.startswith("### ") else ("\n" + n[4:] + ":")
                         for n in v["notes"]).strip()
        link = SITE_URL + "#" + esc(model["slug"] + "-" + v["version"].replace(".", "-"))
        items.append(
            "    <item>\n"
            f"      <title>{esc(model['label'])} software {esc(v['version'])}</title>\n"
            f"      <link>{link}</link>\n"
            f"      <guid isPermaLink=\"false\">{esc(model['slug'])}-{esc(v['version'])}</guid>\n"
            f"      <pubDate>{rss_date(v['first_seen'])}</pubDate>\n"
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


def scrape_model(model: dict, local: str | None) -> list:
    """Return the sorted, first-seen-stamped version list for one model."""
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
    return versions


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

    for model in MODELS:
        versions = scrape_model(model, local_map.get(model["slug"]))
        (DATA_DIR / f"{model['slug']}.json").write_text(
            json.dumps(versions, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        (ROOT / f"feed-{model['slug']}.xml").write_text(build_feed(model, versions), encoding="utf-8")
        all_data[model["slug"]] = versions
        print(f"  {model['slug']}: {len(versions)} versions, latest {versions[0]['version']}")

    # Splice the combined DATA object (keyed by slug) + MODELS metadata into index.html.
    models_meta = [{"slug": m["slug"], "label": m["label"], "manual_url": m["manual_url"]} for m in MODELS]
    out_data = json.dumps(all_data, ensure_ascii=False)
    out_models = json.dumps(models_meta, ensure_ascii=False)

    index = INDEX.read_text(encoding="utf-8")
    new_index, n1 = re.subn(r"const DATA = .*?;\n", "const DATA = " + out_data + ";\n", index, count=1, flags=re.S)
    new_index, n2 = re.subn(r"const MODELS = .*?;\n", "const MODELS = " + out_models + ";\n", new_index, count=1, flags=re.S)
    if n1 != 1 or n2 != 1:
        raise SystemExit("could not locate 'const DATA'/'const MODELS' in index.html")
    new_index = re.sub(r'const SCRAPED = "[^"]*";', f'const SCRAPED = "{today}";', new_index)
    new_index = re.sub(r'Data captured [0-9]{4}-[0-9]{2}-[0-9]{2}', f'Data captured {today}', new_index)

    changed = new_index != index
    if changed:
        INDEX.write_text(new_index, encoding="utf-8")
    print(f"index.html: {'updated' if changed else 'no change'}")


if __name__ == "__main__":
    main()
