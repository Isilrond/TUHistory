#!/usr/bin/env python3
"""
Tyrant Unleashed - History Timeline Generator
================================================
Reads faction_war / raid / battle_event events from XML files,
downloads the referenced web_picture images, and generates
a single, self-contained HTML timeline page.

Usage:
    python3 generate_timeline.py

Expected in the same folder (or specified via --input):
    faction_wars_fp3.xml
    raids_x42.xml
    battle_events_h52.xml

Output:
    timeline.html            (self-contained HTML file)
    images/<web_picture>      (downloaded images)

If an image can't be downloaded (404, no network, etc.), the entry is
still shown - with a placeholder instead of the image. The script does
not abort because of this.
"""

import argparse
import html
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# Manual corrections for known data errors in the source XMLs (wrong
# start_time/end_time). Key = name (exactly as in the XML, case
# insensitive), value = (start_time, end_time) as Unix timestamp.
# "Insurrection Conquest": end_time was originally BEFORE start_time
# (10/20/2017 - 08/23/2014) - corrected to 10/20/2017 - 10/23/2017.
MANUAL_DATE_OVERRIDES = {
    "insurrection conquest": (1508457600, 1508716800),  # 10/20/2017 - 10/23/2017
    # "Harvest Box": end_time (11/20/2013) was before start_time
    # (12/10/2013) in the source data - a stale leftover value. Based on
    # the surrounding entries (Cyber Box ends 12/04, Pantheon Box starts
    # 12/18, and boxes of this era run about 7 days), corrected to
    # 12/10/2013 - 12/17/2013.
    "harvest box": (1386698400, 1387303200),  # 12/10/2013 - 12/17/2013
}

IMAGE_BASE_URL = "https://cdn.synapsegames.com/unleashed/images/"
XML_BASE_URL = "https://mobile.tyrantonline.com/assets/"
WIKI_API_URL = "https://tyrantunleashed.fandom.com/api.php"
WIKI_IMAGE_BASE_HINT = "https://tyrantunleashed.fandom.com/wiki/"

# (filename, XML element name, display type, badge color, banner tag name)
# The banner tag name defaults to "web_picture"; event_box.xml uses
# "web_banner" instead, so it's specified per source.
SOURCES = [
    ("faction_wars_fp3.xml", "faction_war", "Guild War", "#8e44ad", "web_picture"),
    ("raids_x42.xml", "raid", "Raid", "#c0392b", "web_picture"),
    ("battle_events_h52.xml", "battle_event", "Brawl", "#2980b9", "web_picture"),
    ("conquest.xml", "conquest_event", "Conquest", "#e67e22", "web_picture"),
    ("events.xml", "event", "Main Banner", "#16a085", "web_picture"),
    ("event_box.xml", "event_box", "Event Box", "#f39c12", "web_banner"),
]

# Only used to look up missing Conquest banners by name (see
# build_banner_lookup) - events.xml itself is now also a full-fledged
# timeline source in its own right (see above).
EVENTS_LOOKUP_FILE = "events.xml"


def download_xml_files(target_dir):
    """Downloads all source XMLs fresh from tyrantonline.com.
    On failure (no network, 404, ...) an already-existing local file is
    simply left untouched instead of aborting the script."""
    print("0) Downloading current XML files from tyrantonline.com...")
    filenames = list(dict.fromkeys([s[0] for s in SOURCES] + [EVENTS_LOOKUP_FILE]))
    for filename in filenames:
        url = XML_BASE_URL + filename
        dest = os.path.join(target_dir, filename)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            with open(dest, "wb") as f:
                f.write(data)
            print(f"  OK: {filename} ({len(data)} bytes)")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            if os.path.exists(dest):
                print(f"  [WARNING] Download of {filename} failed ({e}) - using existing local file.")
            else:
                print(f"  [WARNING] Download of {filename} failed ({e}) - no local file available.")


def build_banner_lookup(input_dir):
    """Builds a name->web_picture lookup dict from events.xml.
    Used to assign a banner to Conquest events (which have no web_picture
    of their own) in case events.xml happens to still have one for the
    matching name."""
    path = os.path.join(input_dir, EVENTS_LOOKUP_FILE)
    lookup = {}
    if not os.path.exists(path):
        return lookup
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return lookup
    for el in tree.getroot().findall("event"):
        name = (el.findtext("name") or "").strip()
        web_picture = (el.findtext("web_picture") or "").strip()
        if name and web_picture:
            lookup[name.lower()] = web_picture
    return lookup


def _conquest_name_slug(name):
    """'Conquest of Acheron' -> 'acheron'; 'Ascendant Conquest' -> 'ascendant'."""
    lower = name.lower()
    core = re.sub(r"^conquest of\s+", "", lower)
    core = re.sub(r"\s+conquest$", "", core)
    core = re.sub(r"[^a-z0-9]+", "_", core).strip("_")
    if not core:
        core = re.sub(r"[^a-z0-9]+", "_", lower).strip("_")
    return core


def probe_cdn_banner(name, images_dir):
    """Tries whether a Conquest banner exists directly on the game CDN
    under the usual naming convention (same host as all other banners) -
    e.g. 'Glacial Conquest' -> 'glacial_conquest_banner.jpg'. This is more
    reliable than the wiki, since it's the game's own native naming
    convention. Downloads it directly on success and returns the local
    filename, otherwise None."""
    core = _conquest_name_slug(name)
    candidates = [
        f"{core}_conquest_banner.jpg",
        f"conquest_{core}_banner.jpg",
        f"{core}_conquest_banner.png",
        f"conquest_{core}_banner.png",
    ]

    for filename in candidates:
        url = IMAGE_BASE_URL + filename
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    continue
        except Exception:
            continue
        # Hit - now actually download it.
        dest = os.path.join(images_dir, filename)
        try:
            if not os.path.exists(dest):
                img_req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(img_req, timeout=20) as resp:
                    img_data = resp.read()
                with open(dest, "wb") as f:
                    f.write(img_data)
                print(f"    [CDN] '{name}': found and downloaded -> {dest}")
            else:
                print(f"    [CDN] '{name}': file already exists -> {dest}")
            return filename
        except Exception as e:
            print(f"    [WARN] CDN download for '{name}' failed ({url}): {e}")
            return None
        finally:
            time.sleep(0.1)

    return None


def _wiki_imageinfo_lookup(filename):
    """Checks via MediaWiki 'imageinfo' whether File:<filename> exists, and
    returns (url, filename) on success - otherwise (None, None).
    This is the pattern from find_missing_assets.py that already proved
    to work: an exact existence check of a file instead of guessing what's
    linked on an article page."""
    import json

    api_url = (
        f"{WIKI_API_URL}?action=query&titles={urllib.parse.quote('File:' + filename)}"
        f"&prop=imageinfo&iiprop=url&format=json"
    )
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            if page.get("pageid", -1) == -1:
                continue  # page doesn't exist
            imageinfo = page.get("imageinfo", [])
            if imageinfo:
                img_url = imageinfo[0].get("url", "")
                if img_url:
                    return img_url, filename
    except Exception:
        pass
    return None, None


def fetch_wiki_banner(name, images_dir):
    """Best-effort fallback: looks for a banner for a Conquest event on the
    official Fandom wiki. Two strategies:

    1) Guess direct filename candidates (following the pattern of the
       known banner "conquest_acheron_banner.jpg" for "Conquest of
       Acheron") and check via the imageinfo API whether File:<candidate>
       exists - this is the pattern already proven in
       find_missing_assets.py.
    2) If no candidate matches: list the images used on the wiki article
       page with this name, and pick a jpg/png banner file from those.

    In both cases, if found, downloads via Special:FilePath (returns the
    original format, not Fandom's automatic webp conversion). Returns the
    local filename, or None on any failure - does not abort the script.
    NOTE: untested against the real wiki API (no network access while
    developing this script) - please verify/adjust once."""
    import json

    # ── Strategy 1: guess filename candidates ───────────────────────────
    # "Conquest of Acheron" -> "acheron" -> "conquest_acheron_banner.jpg"
    # "Ascendant Conquest"  -> "ascendant" -> "conquest_ascendant_banner.jpg"
    #                                      -> "ascendant_conquest_banner.jpg"
    core = _conquest_name_slug(name)

    filename_candidates = []
    for pattern in (f"conquest_{core}_banner", f"{core}_conquest_banner", f"conquest_{core}"):
        for ext in (".jpg", ".jpeg", ".png"):
            cand = pattern[0].upper() + pattern[1:] + ext  # File: names are capitalized
            if cand not in filename_candidates:
                filename_candidates.append(cand)

    chosen_url, chosen_filename = None, None
    for cand in filename_candidates:
        url, fname = _wiki_imageinfo_lookup(cand)
        if url:
            chosen_url, chosen_filename = url, fname
            break
        time.sleep(0.1)

    if chosen_url:
        print(f"    [WIKI] '{name}': strategy 1 hit -> {chosen_filename}")

    # ── Strategy 2: fallback - list images on the article page ──────────
    # "redirects=1" automatically follows if the name redirects to another
    # wiki page (e.g. slightly different spelling).
    if not chosen_url:
        page_title = name.replace(" ", "_")
        api_url = (
            f"{WIKI_API_URL}?action=query&titles={urllib.parse.quote(page_title)}"
            f"&prop=images&imlimit=50&redirects=1&format=json"
        )
        try:
            req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            pages = data.get("query", {}).get("pages", {})
            page_exists = any(p.get("pageid", -1) != -1 for p in pages.values())
            candidates = []
            for page in pages.values():
                for img in page.get("images", []):
                    title = img.get("title", "")
                    fname = title.split(":", 1)[-1] if ":" in title else title
                    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
                    if ext in ("jpg", "jpeg", "png"):
                        candidates.append(fname)
            banner_candidates = [c for c in candidates if "banner" in c.lower()]
            fallback_candidate = (banner_candidates or candidates or [None])[0]
            if fallback_candidate:
                # Here too, fetch the real URL via imageinfo instead of
                # guessing Special:FilePath (which Fandom blocks with 403).
                chosen_url, chosen_filename = _wiki_imageinfo_lookup(fallback_candidate)
                if chosen_url:
                    print(f"    [WIKI] '{name}': strategy 2 hit -> {chosen_filename}")
                else:
                    print(f"    [WIKI] '{name}': page exists={page_exists}, "
                          f"{len(candidates)} jpg/png on page, but imageinfo lookup failed")
            else:
                print(f"    [WIKI] '{name}': page exists={page_exists}, "
                      f"no jpg/png images found on the page (strategy 1 candidates: {len(filename_candidates)})")
        except Exception as e:
            print(f"    [WARN] Wiki fallback for '{name}' failed: {e}")

    if not chosen_url or not chosen_filename:
        return None

    # ── Download ─────────────────────────────────────────────────────
    # IMPORTANT: use the URL returned by the imageinfo API directly (that's
    # already the real original CDN file) - do NOT rebuild a
    # Special:FilePath URL, Fandom blocks that with 403 Forbidden.
    # Fandom often serves WebP automatically via content negotiation
    # despite the .jpg/.png extension - "&format=original" (or
    # "?format=original" if the URL has no query string yet) forces the
    # actual original format.
    separator = "&" if "?" in chosen_url else "?"
    download_url = f"{chosen_url}{separator}format=original"

    try:
        # Replace spaces and other problematic characters in the filename
        # with underscores (some old wiki files still have real spaces in
        # the name, e.g. "Harbinger war banner b.jpg" - that can cause
        # problems when linking it in the HTML).
        safe_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", chosen_filename)
        local_filename = "wiki_" + safe_filename
        dest = os.path.join(images_dir, local_filename)
        if os.path.exists(dest):
            print(f"    [WIKI] '{name}': file already exists -> {dest}")
            return local_filename
        img_req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(img_req, timeout=20) as resp:
            img_data = resp.read()
            content_type = resp.headers.get("Content-Type", "")
        if "webp" in content_type.lower():
            print(f"    [WARN] '{chosen_filename}' still came back as WebP (Content-Type: {content_type}) - skipped.")
            return None
        with open(dest, "wb") as f:
            f.write(img_data)
        print(f"    [WIKI] '{name}': downloaded ({len(img_data)} bytes) -> {dest}")
        return local_filename
    except Exception as e:
        print(f"    [WARN] Download for '{name}' failed ({download_url}): {e}")
        return None


def parse_events(input_dir, scrape_wiki=False, images_dir=None):
    """Reads all source XMLs and returns a list of dicts."""
    events = []
    banner_lookup = build_banner_lookup(input_dir)
    wiki_hits, wiki_misses = 0, 0
    conquest_names_seen = set()

    for filename, tag, type_label, color, banner_tag in SOURCES:
        path = os.path.join(input_dir, filename)
        if not os.path.exists(path):
            print(f"  [WARNING] File not found, skipping: {path}")
            continue

        try:
            tree = ET.parse(path)
        except ET.ParseError as e:
            print(f"  [ERROR] Could not parse {filename}: {e}")
            continue

        root = tree.getroot()
        elements = root.findall(tag)
        print(f"  {filename}: {len(elements)} <{tag}> entries found")

        for el in elements:
            name = (el.findtext("name") or "").strip()
            start_raw = (el.findtext("start_time") or "").strip()
            end_raw = (el.findtext("end_time") or "").strip()
            desc = (el.findtext("desc") or "").strip()
            # Event Box descriptions are just generic placeholder text
            # ("This is a description.", etc.) with no real information -
            # dropped entirely.
            if type_label == "Event Box":
                desc = ""
            web_picture = (el.findtext(banner_tag) or "").strip()

            # Skip placeholder/test entries without a real date
            if not name or name.upper() == "UNUSED":
                continue
            # "Conquest of Acheron" & Co. appear both in conquest.xml (as
            # Conquest) and in events.xml (as Main Banner) - skip the
            # Main Banner entry here since the Conquest variant was
            # already captured above.
            if type_label == "Main Banner" and name.lower() in conquest_names_seen:
                continue
            # "Gold Bonus" mini-events never had a banner (confirmed) -
            # ignored entirely instead of being listed as "No banner".
            if type_label == "Main Banner" and "gold bonus" in name.lower():
                continue
            try:
                start_time = int(start_raw)
                end_time = int(end_raw) if end_raw else start_time
            except ValueError:
                continue
            # Manual correction of known data errors (see
            # MANUAL_DATE_OVERRIDES above) - e.g. Insurrection Conquest,
            # where end_time was originally before start_time.
            if name.lower() in MANUAL_DATE_OVERRIDES:
                start_time, end_time = MANUAL_DATE_OVERRIDES[name.lower()]
            # "...Guild Brawl"/"...Placements" entries are usually
            # one-day lead-in rounds right before the actual Guild War
            # (identical banner, no additional content) - but NOT all
            # "Guild Brawl" entries are that: many are standalone 3-day
            # brawls and should stay. So filter only by duration
            # (< 1.5 days = lead-in round), not by name alone.
            duration_days = (end_time - start_time) / 86400
            if type_label == "Brawl" and duration_days < 1.5 and (
                name.lower().endswith("guild brawl") or name.lower().endswith("placements")
            ):
                continue
            # Some entries (e.g. "Null Conquest", or draft duplicates like
            # a second "Harbinger Conquest" with start_time=1/end_time=2 -
            # the real values are then only in the XML comment) are
            # placeholders/drafts without a real date. Anything before the
            # year 2001 (Unix timestamp < 1000000000) is guaranteed not a
            # real game event (the game has only existed since 2012/2013)
            # and is therefore ignored.
            if start_time < 1_000_000_000:
                continue

            # Conquest events have no web_picture of their own in the XML -
            # first look it up in events.xml (usually outdated/empty,
            # since the file gets overwritten), then search directly on
            # the game CDN using the usual naming convention (more
            # reliable than the wiki, since it's the native naming
            # convention), and only then optionally fall back to the
            # Fandom wiki as a last resort.
            if not web_picture and type_label == "Conquest":
                web_picture = banner_lookup.get(name.lower(), "")
                if not web_picture and images_dir:
                    found = probe_cdn_banner(name, images_dir)
                    if found:
                        web_picture = found
                if not web_picture and scrape_wiki and images_dir:
                    found = fetch_wiki_banner(name, images_dir)
                    if found:
                        web_picture = found
                        wiki_hits += 1
                    else:
                        wiki_misses += 1

            if type_label == "Conquest":
                conquest_names_seen.add(name.lower())

            events.append(
                {
                    "id": (el.findtext("id") or "").strip(),
                    "type": type_label,
                    "color": color,
                    "name": name,
                    "desc": desc,
                    "web_picture": web_picture,
                    "start_time": start_time,
                    "end_time": end_time,
                    "sub_items": None,  # optionally filled by group_generic_banners()
                }
            )

    if scrape_wiki:
        print(f"  Wiki banner lookup: {wiki_hits} found, {wiki_misses} not found")

    events.sort(key=lambda e: e["start_time"])
    events = group_generic_banners(events)
    return events


def group_generic_banners(events, min_group_size=4):
    """Collapses events that share a single (generically reused) banner
    with many other events of the same type into ONE card: image shown
    once, all names+dates listed below as a compact list.
    The priority is the image first, dates second - a long chain of
    almost identical cards with the same generic banner (e.g.
    "generic_brawl_banner.jpg" or "war_banner_long.jpg" from 2019 onward)
    adds no visual value otherwise.
    Grouped per (type, web_picture) combination, only if at least
    min_group_size events share the same banner - a single or few reuses
    (e.g. Guild Brawl phases) remain normal individual cards."""
    from collections import defaultdict

    groups = defaultdict(list)
    for e in events:
        # Group Brawl, Guild War, and Event Box - that's where reuse of a
        # single generic banner across many entries was the reported
        # issue. Raids, for example, should stay individually visible.
        if e["web_picture"] and e["type"] in ("Brawl", "Guild War", "Event Box"):
            groups[(e["type"], e["web_picture"])].append(e)

    to_collapse = {key for key, group in groups.items() if len(group) >= min_group_size}
    if not to_collapse:
        return events

    result = []
    group_cards = []
    seen_keys = set()
    for e in events:
        key = (e["type"], e["web_picture"])
        if key not in to_collapse:
            result.append(e)
            continue
        if key in seen_keys:
            continue  # already inserted as a group
        seen_keys.add(key)
        group = groups[key]
        group_sorted = sorted(group, key=lambda x: x["start_time"])
        group_cards.append(
            {
                "id": "",
                "type": group_sorted[0]["type"],
                "color": group_sorted[0]["color"],
                "name": f"{len(group_sorted)}\u00d7 {group_sorted[0]['type']} (same banner)",
                "desc": "",
                "web_picture": key[1],
                "start_time": group_sorted[0]["start_time"],
                "end_time": group_sorted[-1]["end_time"],
                "sub_items": [
                    (g["name"], g["start_time"], g["end_time"]) for g in group_sorted
                ],
            }
        )

    # Regular events stay chronologically sorted; grouped "same banner"
    # cards are always appended at the very end of the timeline instead of
    # being interleaved by date, sorted among themselves by their earliest
    # date.
    result.sort(key=lambda e: e["start_time"])
    group_cards.sort(key=lambda e: e["start_time"])
    return result + group_cards


def download_images(events, images_dir, delay=0.05):
    """Downloads all referenced web_picture files (cache behavior)."""
    os.makedirs(images_dir, exist_ok=True)

    filenames = sorted({e["web_picture"] for e in events if e["web_picture"]})
    print(f"\n  {len(filenames)} unique images to check/download...")

    ok, failed, skipped = 0, 0, 0
    failed_files = []

    for i, filename in enumerate(filenames, 1):
        dest = os.path.join(images_dir, filename)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            skipped += 1
            continue

        url = IMAGE_BASE_URL + filename
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp, open(dest, "wb") as out:
                out.write(resp.read())
            ok += 1
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            failed += 1
            failed_files.append((filename, str(e)))
            if os.path.exists(dest):
                os.remove(dest)
        if delay:
            time.sleep(delay)

        if i % 25 == 0 or i == len(filenames):
            print(f"    ... {i}/{len(filenames)} processed (new: {ok}, existing: {skipped}, failed: {failed})")

    print(f"\n  Image download done: {ok} newly downloaded, {skipped} already present, {failed} failed.")
    if failed_files:
        print("  Failed images (timeline shows a placeholder for these):")
        for fn, err in failed_files[:20]:
            print(f"    - {fn}: {err}")
        if len(failed_files) > 20:
            print(f"    ... and {len(failed_files) - 20} more")

    return {f for f in filenames if os.path.exists(os.path.join(images_dir, f)) and os.path.getsize(os.path.join(images_dir, f)) > 0}


def fmt_date(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m.%Y")


def build_html(events, available_images, images_relpath="images"):
    rows = []
    for e in events:
        img_ok = e["web_picture"] and e["web_picture"] in available_images
        if img_ok:
            # urllib.parse.quote makes sure spaces/special characters in
            # filenames (e.g. from some wiki images) result in a valid
            # URL, instead of silently breaking the src.
            img_src = images_relpath + "/" + urllib.parse.quote(e["web_picture"])
            img_html = f'<img src="{html.escape(img_src)}" alt="{html.escape(e["name"])}" loading="lazy">'
        else:
            img_html = '<div class="no-image">No banner</div>'

        filename_label = f'<span class="filename-tag">{html.escape(e["web_picture"])}</span>' if e["web_picture"] else ""
        desc_html = f'<p class="desc">{html.escape(e["desc"])}</p>' if e["desc"] else ""

        if e.get("sub_items"):
            # Grouped card: dates as a compact, scrollable list instead of
            # a single date range (the image takes priority).
            sub_rows = "\n".join(
                f'<li><span class="sub-name">{html.escape(n)}</span>'
                f'<span class="sub-dates">{fmt_date(st)} &ndash; {fmt_date(et)}</span></li>'
                for n, st, et in e["sub_items"]
            )
            dates_html = f'<ul class="sub-list">{sub_rows}</ul>'
        else:
            dates_html = f'<div class="dates">{fmt_date(e["start_time"])} &ndash; {fmt_date(e["end_time"])}</div>'

        search_blob = html.escape(
            (e["name"] + " " + e["desc"] + " " + " ".join(n for n, _, _ in (e.get("sub_items") or []))).lower()
        )
        rows.append(f"""
        <div class="event" data-type="{html.escape(e['type'])}" data-search="{search_blob}">
          <div class="event-marker" style="background:{e['color']}"></div>
          <div class="event-card">
            <div class="event-image">{img_html}</div>
            <div class="event-body">
              {filename_label}
              <span class="badge" style="background:{e['color']}">{html.escape(e['type'])}</span>
              <h2>{html.escape(e['name'])}</h2>
              {dates_html}
              {desc_html}
            </div>
          </div>
        </div>""")

    types = sorted({e["type"] for e in events})
    filter_buttons = "\n".join(
        f'<button class="filter-btn active" data-filter="{html.escape(t)}">{html.escape(t)}</button>'
        for t in types
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tyrant Unleashed &ndash; History Timeline</title>
<style>
  :root {{
    --bg: #0f1216;
    --panel: #1a1f26;
    --border: #2b323c;
    --text: #e7e9ec;
    --muted: #9aa4b2;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 40px 16px 80px;
  }}
  h1 {{
    text-align: center;
    font-size: 2rem;
    margin-bottom: 4px;
  }}
  .subtitle {{
    text-align: center;
    color: var(--muted);
    margin-bottom: 24px;
  }}
  .controls {{
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
    margin-bottom: 40px;
  }}
  .search-box {{
    background: var(--panel);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 7px 14px;
    border-radius: 999px;
    font-size: 0.85rem;
    min-width: 220px;
    outline: none;
  }}
  .search-box::placeholder {{ color: var(--muted); }}
  .filters {{
    display: flex;
    justify-content: center;
    gap: 8px;
    flex-wrap: wrap;
  }}
  .filter-btn {{
    background: var(--panel);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 6px 14px;
    border-radius: 999px;
    cursor: pointer;
    font-size: 0.85rem;
    opacity: 0.6;
  }}
  .filter-btn.active {{ opacity: 1; }}
  .no-results {{
    text-align: center;
    color: var(--muted);
    margin-top: 20px;
    display: none;
  }}
  .no-results.visible {{ display: block; }}
  .timeline {{
    position: relative;
    max-width: 900px;
    margin: 0 auto;
  }}
  .timeline::before {{
    content: "";
    position: absolute;
    left: 20px;
    top: 0;
    bottom: 0;
    width: 2px;
    background: var(--border);
  }}
  .event {{
    position: relative;
    padding-left: 56px;
    margin-bottom: 28px;
  }}
  .event-marker {{
    position: absolute;
    left: 14px;
    top: 6px;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    border: 2px solid var(--bg);
  }}
  .event-card {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
  }}
  .event-image {{
    width: 100%;
    background: #000;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .event-image img {{
    width: 100%;
    height: auto;
    object-fit: contain;
    display: block;
  }}
  .no-image {{
    color: var(--muted);
    font-size: 0.8rem;
    padding: 16px;
  }}
  .event-body {{
    position: relative;
    padding: 14px 18px;
  }}
  .filename-tag {{
    position: absolute;
    top: 8px;
    right: 10px;
    color: var(--muted);
    font-size: 0.65rem;
    font-family: "Courier New", monospace;
    pointer-events: none;
  }}
  .badge {{
    display: inline-block;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 2px 8px;
    border-radius: 4px;
    margin-bottom: 6px;
  }}
  .event-body h2 {{
    margin: 4px 0 4px;
    font-size: 1.15rem;
  }}
  .dates {{
    color: var(--muted);
    font-size: 0.8rem;
    margin-bottom: 8px;
  }}
  .sub-list {{
    list-style: none;
    margin: 0 0 8px;
    padding: 0;
    border: 1px solid var(--border);
    border-radius: 6px;
  }}
  .sub-list li {{
    display: flex;
    justify-content: space-between;
    gap: 10px;
    padding: 4px 8px;
    font-size: 0.78rem;
    border-bottom: 1px solid var(--border);
  }}
  .sub-list li:last-child {{ border-bottom: none; }}
  .sub-name {{ color: #cfd4da; }}
  .sub-dates {{ color: var(--muted); white-space: nowrap; }}
  .desc {{
    font-size: 0.9rem;
    line-height: 1.4;
    color: #cfd4da;
    margin: 0;
  }}
  .event.hidden {{ display: none; }}
</style>
</head>
<body>
  <h1>Tyrant Unleashed &ndash; History Timeline</h1>
  <div class="subtitle">{len(events)} Events</div>
  <div class="controls">
    <input type="text" class="search-box" id="search" placeholder="Search...">
    <div class="filters">
      <button class="filter-btn active" data-filter="all">All</button>
      {filter_buttons}
    </div>
  </div>
  <div class="timeline">
    {''.join(rows)}
  </div>
  <div class="no-results" id="no-results">No results.</div>

<script>
  const buttons = document.querySelectorAll('.filter-btn');
  const eventsEls = document.querySelectorAll('.event');
  const searchBox = document.getElementById('search');
  const noResults = document.getElementById('no-results');

  function applyFilters() {{
    const activeFilters = Array.from(document.querySelectorAll('.filter-btn.active'))
      .map(b => b.dataset.filter);
    const showAll = activeFilters.includes('all') || activeFilters.length === 0;
    // Word-based search: every word entered must appear somewhere in the
    // text (regardless of position or order) -
    // "Acheron" finds "Conquest of Acheron" just as well as "Acheron Conquest".
    const queryWords = searchBox.value.trim().toLowerCase().split(/\\s+/).filter(Boolean);

    let visibleCount = 0;
    eventsEls.forEach(ev => {{
      const type = ev.dataset.type;
      const matchesType = showAll || activeFilters.includes(type);
      const matchesSearch = queryWords.length === 0 ||
        queryWords.every(word => ev.dataset.search.includes(word));
      const visible = matchesType && matchesSearch;
      ev.classList.toggle('hidden', !visible);
      if (visible) visibleCount++;
    }});
    noResults.classList.toggle('visible', visibleCount === 0);
  }}

  buttons.forEach(btn => {{
    btn.addEventListener('click', () => {{
      // Single-select: the clicked button is the only active one -
      // clicking "Brawl" shows ONLY Brawls, not Brawl + previous selection.
      buttons.forEach(b => b.classList.toggle('active', b === btn));
      applyFilters();
    }});
  }});

  searchBox.addEventListener('input', applyFilters);
</script>
</body>
</html>
"""


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    parser = argparse.ArgumentParser(description="Generates the TU History Timeline (HTML) from the XML files.")
    parser.add_argument("--input", default=script_dir, help="Folder with the source XML files (default: script's own folder)")
    parser.add_argument("--output", default=os.path.join(script_dir, "timeline.html"), help="Target HTML file (default: timeline.html next to the script)")
    parser.add_argument("--images-dir", default="images", help="Folder for downloaded images (default: images)")
    parser.add_argument("--no-download", action="store_true", help="Skip image download (only rebuild the HTML)")
    parser.add_argument("--no-xml-download", action="store_true", help="Don't re-download the XML files - use existing local files")
    parser.add_argument("--scrape-wiki", action="store_true", help="For Conquest events without a banner: fallback search on the official Fandom wiki (best effort)")
    args = parser.parse_args()

    print(f"Working folder (script location): {args.input}")

    if not args.no_xml_download:
        download_xml_files(args.input)
    else:
        print("0) XML download skipped (--no-xml-download)")

    images_dir = os.path.join(os.path.dirname(os.path.abspath(args.output)) or ".", args.images_dir)
    os.makedirs(images_dir, exist_ok=True)

    print("1) Reading XML files...")
    events = parse_events(args.input, scrape_wiki=args.scrape_wiki, images_dir=images_dir)
    print(f"  -> {len(events)} valid events in total (sorted chronologically)")

    if not events:
        print("No events found - aborting.")
        sys.exit(1)

    if args.no_download:
        available = {
            f for f in {e["web_picture"] for e in events if e["web_picture"]}
            if os.path.exists(os.path.join(images_dir, f))
        }
        print("\n3) Image download skipped (--no-download)")
    else:
        print("\n3) Downloading images...")
        available = download_images(events, images_dir)

    print("\n4) Generating HTML timeline...")
    html_out = build_html(events, available, images_relpath=args.images_dir)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html_out)

    print(f"\nDone! -> {args.output}")
    print(f"Images are located in: {os.path.abspath(images_dir)}")

    # ── Overview: which events (still) have no banner? ─────────────────
    missing = [
        e for e in events
        if not (e["web_picture"] and e["web_picture"] in available)
    ]
    print(f"\n{'='*60}")
    print(f"Events without a banner: {len(missing)} of {len(events)}")
    if missing:
        by_type = {}
        for e in missing:
            by_type.setdefault(e["type"], []).append(e)
        for type_label in sorted(by_type):
            group = by_type[type_label]
            print(f"\n  {type_label} ({len(group)}):")
            for e in group:
                print(f"    - {e['name']} ({fmt_date(e['start_time'])} - {fmt_date(e['end_time'])})")
    else:
        print("  All events have a banner.")


if __name__ == "__main__":
    # If the script is started by double-clicking (no PowerShell/terminal
    # around it), the console window otherwise closes again immediately -
    # regardless of whether everything worked or an error occurred. Hence
    # an error catch with traceback here, and always a pause at the end.
    exit_code = 0
    try:
        main()
    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else 1
    except Exception:
        import traceback
        traceback.print_exc()
        exit_code = 1
    finally:
        print("\n----------------------------------------")
        if exit_code == 0:
            print("Done. You can close this window.")
        else:
            print("An error occurred (see above).")
        if os.name == "nt":
            # Native Windows command instead of input() - reliably attaches
            # to the console, even if stdin isn't properly connected for a
            # process started via double-click.
            os.system("pause")
        else:
            try:
                input("Press Enter to exit...")
            except EOFError:
                pass
        sys.exit(exit_code)
