#!/usr/bin/env python3
"""
Tyrant Unleashed - History Timeline Generator
================================================
Liest faction_war / raid / battle_event Events aus XML-Dateien,
laedt die zugehoerigen web_picture-Bilder herunter und erzeugt
eine einzelne, eigenstaendige HTML-Timeline-Seite.

Nutzung:
    python3 generate_timeline.py

Erwartet im selben Ordner (oder per --input angegeben):
    faction_wars_fp3.xml
    raids_x42.xml
    battle_events_h52.xml

Ausgabe:
    timeline.html            (eigenstaendige HTML-Datei)
    images/<web_picture>      (heruntergeladene Bilder)

Falls ein Bild nicht heruntergeladen werden kann (404, kein Netzwerk,
etc.), wird der Eintrag trotzdem angezeigt - mit einem Platzhalter statt
Bild. Das Skript bricht dabei nicht ab.
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

IMAGE_BASE_URL = "https://cdn.synapsegames.com/unleashed/images/"
XML_BASE_URL = "https://mobile.tyrantonline.com/assets/"
WIKI_API_URL = "https://tyrantunleashed.fandom.com/api.php"
WIKI_IMAGE_BASE_HINT = "https://tyrantunleashed.fandom.com/wiki/"

# (Dateiname, XML-Elementname, Anzeige-Typ, Badge-Farbe)
SOURCES = [
    ("faction_wars_fp3.xml", "faction_war", "Guild War", "#8e44ad"),
    ("raids_x42.xml", "raid", "Raid", "#c0392b"),
    ("battle_events_h52.xml", "battle_event", "Brawl", "#2980b9"),
    ("conquest.xml", "conquest_event", "Conquest", "#e67e22"),
    ("events.xml", "event", "Main Banner", "#16a085"),
]

# Nur zum Nachschlagen von fehlenden Conquest-Bannern per Name genutzt
# (siehe build_banner_lookup) - events.xml selbst ist jetzt zusaetzlich
# auch eine vollwertige Timeline-Quelle (s.o.).
EVENTS_LOOKUP_FILE = "events.xml"


def download_xml_files(target_dir):
    """Laedt alle Quell-XMLs frisch von tyrantonline.com herunter.
    Bei Fehlern (kein Netz, 404, ...) bleibt eine bereits vorhandene lokale
    Datei einfach unangetastet, statt das Skript abzubrechen."""
    print("0) Lade aktuelle XML-Dateien von tyrantonline.com...")
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
            print(f"  OK: {filename} ({len(data)} Bytes)")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            if os.path.exists(dest):
                print(f"  [WARNUNG] Download von {filename} fehlgeschlagen ({e}) - nutze vorhandene lokale Datei.")
            else:
                print(f"  [WARNUNG] Download von {filename} fehlgeschlagen ({e}) - keine lokale Datei vorhanden.")


def build_banner_lookup(input_dir):
    """Baut ein Name->web_picture Nachschlage-Woerterbuch aus events.xml.
    Wird genutzt, um Conquest-Events (die selbst kein web_picture haben)
    ein Banner zuzuordnen, falls events.xml zufaellig noch eins fuer den
    passenden Namen enthaelt."""
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
    """Probiert, ob ein Conquest-Banner unter der ueblichen Namenskonvention
    direkt auf dem Spiele-CDN liegt (gleicher Host wie alle anderen Banner) -
    z.B. 'Glacial Conquest' -> 'glacial_conquest_banner.jpg'. Das ist
    zuverlaessiger als das Wiki, da es die native Namenskonvention des
    Spiels selbst ist. Laedt bei Erfolg direkt herunter und gibt den
    lokalen Dateinamen zurueck, sonst None."""
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

        # Treffer - jetzt wirklich herunterladen.
        dest = os.path.join(images_dir, filename)
        try:
            if not os.path.exists(dest):
                img_req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(img_req, timeout=20) as resp:
                    img_data = resp.read()
                with open(dest, "wb") as f:
                    f.write(img_data)
                print(f"    [CDN] '{name}': gefunden und heruntergeladen -> {dest}")
            else:
                print(f"    [CDN] '{name}': Datei bereits vorhanden -> {dest}")
            return filename
        except Exception as e:
            print(f"    [WARN] CDN-Download fuer '{name}' fehlgeschlagen ({url}): {e}")
            return None
        finally:
            time.sleep(0.1)

    return None


def _wiki_imageinfo_lookup(filename):
    """Prueft per MediaWiki 'imageinfo', ob File:<filename> existiert, und
    gibt bei Erfolg (url, filename) zurueck - sonst (None, None).
    Das ist das Muster aus find_missing_assets.py, das bereits nachweislich
    funktioniert hat: exakte Existenzpruefung einer Datei statt Raten,
    was auf einer Artikelseite verlinkt ist."""
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
                continue  # Seite existiert nicht
            imageinfo = page.get("imageinfo", [])
            if imageinfo:
                img_url = imageinfo[0].get("url", "")
                if img_url:
                    return img_url, filename
    except Exception:
        pass
    return None, None


def fetch_wiki_banner(name, images_dir):
    """Best-Effort-Fallback: sucht ein Banner fuer ein Conquest-Event im
    offiziellen Fandom-Wiki. Zwei Strategien:

    1) Direkte Dateinamen-Kandidaten raten (nach dem Muster des bekannten
       Banners "conquest_acheron_banner.jpg" fuer "Conquest of Acheron")
       und per imageinfo-API pruefen, ob File:<Kandidat> existiert - das
       ist das bereits erprobte Muster aus find_missing_assets.py.
    2) Falls kein Kandidat passt: die Bilder auflisten, die auf der
       Wiki-Artikelseite mit diesem Namen verwendet werden, und daraus
       eine jpg/png-Bannerdatei waehlen.

    In beiden Faellen wird - falls gefunden - ueber Special:FilePath
    heruntergeladen (liefert das Original-Format, nicht Fandoms
    automatische webp-Konvertierung). Gibt den lokalen Dateinamen zurueck,
    oder None bei jedem Fehler - bricht das Skript dabei nicht ab.
    HINWEIS: ungetestet gegen die echte Wiki-API (kein Netzwerkzugriff bei
    der Entwicklung dieses Skripts) - bitte einmal pruefen/anpassen."""
    import json

    # ── Strategie 1: Dateinamen-Kandidaten raten ────────────────────────
    # "Conquest of Acheron" -> "acheron" -> "conquest_acheron_banner.jpg"
    # "Ascendant Conquest"  -> "ascendant" -> "conquest_ascendant_banner.jpg"
    #                                      -> "ascendant_conquest_banner.jpg"
    core = _conquest_name_slug(name)

    filename_candidates = []
    for pattern in (f"conquest_{core}_banner", f"{core}_conquest_banner", f"conquest_{core}"):
        for ext in (".jpg", ".jpeg", ".png"):
            cand = pattern[0].upper() + pattern[1:] + ext  # File:-Namen sind gross geschrieben
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
        print(f"    [WIKI] '{name}': Strategie 1 Treffer -> {chosen_filename}")

    # ── Strategie 2: Fallback - Bilder auf der Artikelseite auflisten ──
    # "redirects=1" folgt automatisch, falls der Name auf eine andere
    # Wiki-Seite umleitet (z.B. leicht abweichende Schreibweise).
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
                # Auch hier die echte URL per imageinfo holen statt
                # Special:FilePath zu raten (wird von Fandom mit 403
                # blockiert).
                chosen_url, chosen_filename = _wiki_imageinfo_lookup(fallback_candidate)
                if chosen_url:
                    print(f"    [WIKI] '{name}': Strategie 2 Treffer -> {chosen_filename}")
                else:
                    print(f"    [WIKI] '{name}': Seite existiert={page_exists}, "
                          f"{len(candidates)} jpg/png auf Seite, aber imageinfo-Lookup fehlgeschlagen")
            else:
                print(f"    [WIKI] '{name}': Seite existiert={page_exists}, "
                      f"keine jpg/png-Bilder auf der Seite gefunden (Kandidaten Strategie 1: {len(filename_candidates)})")
        except Exception as e:
            print(f"    [WARN] Wiki-Fallback fuer '{name}' fehlgeschlagen: {e}")

    if not chosen_url or not chosen_filename:
        return None

    # ── Herunterladen ────────────────────────────────────────────────
    # WICHTIG: die von der imageinfo-API zurueckgelieferte URL direkt
    # verwenden (das ist bereits die echte CDN-Original-Datei) - NICHT
    # eine Special:FilePath-URL neu zusammenbauen, die wird von Fandom
    # mit 403 Forbidden blockiert.
    # Fandom liefert per Content-Negotiation trotz .jpg/.png-Endung oft
    # automatisch WebP aus - "&format=original" (bzw. "?format=original",
    # falls die URL noch keinen Query-String hat) erzwingt das
    # tatsaechliche Originalformat.
    separator = "&" if "?" in chosen_url else "?"
    download_url = f"{chosen_url}{separator}format=original"

    try:
        # Leerzeichen und andere problematische Zeichen im Dateinamen
        # durch Unterstriche ersetzen (manche alten Wiki-Dateien haben
        # noch echte Leerzeichen im Namen, z.B. "Harbinger war banner b.jpg" -
        # das kann beim Verlinken in der HTML Probleme machen).
        safe_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", chosen_filename)
        local_filename = "wiki_" + safe_filename
        dest = os.path.join(images_dir, local_filename)
        if os.path.exists(dest):
            print(f"    [WIKI] '{name}': Datei bereits vorhanden -> {dest}")
            return local_filename
        img_req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(img_req, timeout=20) as resp:
            img_data = resp.read()
            content_type = resp.headers.get("Content-Type", "")
        if "webp" in content_type.lower():
            print(f"    [WARN] '{chosen_filename}' kam trotzdem als WebP zurueck (Content-Type: {content_type}) - uebersprungen.")
            return None
        with open(dest, "wb") as f:
            f.write(img_data)
        print(f"    [WIKI] '{name}': heruntergeladen ({len(img_data)} Bytes) -> {dest}")
        return local_filename
    except Exception as e:
        print(f"    [WARN] Download fuer '{name}' fehlgeschlagen ({download_url}): {e}")
        return None


def parse_events(input_dir, scrape_wiki=False, images_dir=None):
    """Liest alle Quell-XMLs und liefert eine Liste von dicts."""
    events = []
    banner_lookup = build_banner_lookup(input_dir)
    wiki_hits, wiki_misses = 0, 0
    conquest_names_seen = set()

    for filename, tag, type_label, color in SOURCES:
        path = os.path.join(input_dir, filename)
        if not os.path.exists(path):
            print(f"  [WARNUNG] Datei nicht gefunden, wird uebersprungen: {path}")
            continue

        try:
            tree = ET.parse(path)
        except ET.ParseError as e:
            print(f"  [FEHLER] Konnte {filename} nicht parsen: {e}")
            continue

        root = tree.getroot()
        elements = root.findall(tag)
        print(f"  {filename}: {len(elements)} <{tag}>-Eintraege gefunden")

        for el in elements:
            name = (el.findtext("name") or "").strip()
            start_raw = (el.findtext("start_time") or "").strip()
            end_raw = (el.findtext("end_time") or "").strip()
            desc = (el.findtext("desc") or "").strip()
            web_picture = (el.findtext("web_picture") or "").strip()

            # Platzhalter-/Test-Eintraege ohne echtes Datum ueberspringen
            if not name or name.upper() == "UNUSED":
                continue
            # "Guild Brawl"-Eintraege sind Duplikate, die dem eigentlichen
            # Guild War direkt vorausgehen (identisches Banner, keine
            # inhaltliche Ergaenzung) - werden ignoriert.
            if type_label == "Brawl" and name.lower().endswith("guild brawl"):
                continue
            # "Conquest of Acheron" & Co. stehen sowohl in conquest.xml
            # (als Conquest) als auch in events.xml (als Story Event) -
            # hier den Story-Event-Eintrag ueberspringen, da die
            # Conquest-Variante bereits weiter oben erfasst wurde.
            if type_label == "Main Banner" and name.lower() in conquest_names_seen:
                continue
            try:
                start_time = int(start_raw)
                end_time = int(end_raw) if end_raw else start_time
            except ValueError:
                continue
            # Manche Eintraege (z.B. "Null Conquest", oder Vorab-Dubletten
            # wie eine zweite "Harbinger Conquest" mit start_time=1/end_time=2
            # - die echten Werte stehen dann nur im XML-Kommentar) sind
            # Platzhalter/Entwuerfe ohne echtes Datum. Alles vor dem Jahr
            # 2001 (Unix-Timestamp < 1000000000) ist garantiert kein
            # echtes Spiel-Event (das Spiel existiert erst seit 2012/2013)
            # und wird deshalb ignoriert.
            if start_time < 1_000_000_000:
                continue

            # Conquest-Events haben selbst kein web_picture im XML - erst
            # in events.xml nachschlagen (meist veraltet/leer, da die
            # Datei ueberschrieben wird), dann direkt auf dem Spiele-CDN
            # nach der ueblichen Namenskonvention suchen (zuverlaessiger als
            # das Wiki, da native Namenskonvention), erst danach optional
            # im Fandom-Wiki als letzter Fallback.
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
                }
            )

    if scrape_wiki:
        print(f"  Wiki-Banner-Lookup: {wiki_hits} gefunden, {wiki_misses} nicht gefunden")

    events.sort(key=lambda e: e["start_time"])
    return events


def download_images(events, images_dir, delay=0.05):
    """Laedt alle referenzierten web_picture-Dateien herunter (Cache-Verhalten)."""
    os.makedirs(images_dir, exist_ok=True)

    filenames = sorted({e["web_picture"] for e in events if e["web_picture"]})
    print(f"\n  {len(filenames)} eindeutige Bilder zu pruefen/laden...")

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
            print(f"    ... {i}/{len(filenames)} verarbeitet (neu: {ok}, vorhanden: {skipped}, fehlgeschlagen: {failed})")

    print(f"\n  Bild-Download fertig: {ok} neu geladen, {skipped} bereits vorhanden, {failed} fehlgeschlagen.")
    if failed_files:
        print("  Fehlgeschlagene Bilder (Timeline zeigt fuer diese einen Platzhalter):")
        for fn, err in failed_files[:20]:
            print(f"    - {fn}: {err}")
        if len(failed_files) > 20:
            print(f"    ... und {len(failed_files) - 20} weitere")

    return {f for f in filenames if os.path.exists(os.path.join(images_dir, f)) and os.path.getsize(os.path.join(images_dir, f)) > 0}


def fmt_date(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m.%Y")


def build_html(events, available_images, images_relpath="images"):
    rows = []
    for e in events:
        img_ok = e["web_picture"] and e["web_picture"] in available_images
        if img_ok:
            # urllib.parse.quote sorgt dafuer, dass Leerzeichen/Sonderzeichen
            # in Dateinamen (z.B. von manchen Wiki-Bildern) eine gueltige
            # URL ergeben, statt die src stillschweigend kaputtzumachen.
            img_src = images_relpath + "/" + urllib.parse.quote(e["web_picture"])
            img_html = f'<img src="{html.escape(img_src)}" alt="{html.escape(e["name"])}" loading="lazy">'
        else:
            img_html = '<div class="no-image">No banner</div>'

        filename_label = f'<span class="filename-tag">{html.escape(e["web_picture"])}</span>' if e["web_picture"] else ""
        desc_html = f'<p class="desc">{html.escape(e["desc"])}</p>' if e["desc"] else ""

        search_blob = html.escape((e["name"] + " " + e["desc"]).lower())
        rows.append(f"""
        <div class="event" data-type="{html.escape(e['type'])}" data-search="{search_blob}">
          <div class="event-marker" style="background:{e['color']}"></div>
          <div class="event-card">
            <div class="event-image">{img_html}</div>
            <div class="event-body">
              {filename_label}
              <span class="badge" style="background:{e['color']}">{html.escape(e['type'])}</span>
              <h2>{html.escape(e['name'])}</h2>
              <div class="dates">{fmt_date(e['start_time'])} &ndash; {fmt_date(e['end_time'])}</div>
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
<html lang="de">
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
    <input type="text" class="search-box" id="search" placeholder="Suchen...">
    <div class="filters">
      <button class="filter-btn active" data-filter="all">Alle</button>
      {filter_buttons}
    </div>
  </div>
  <div class="timeline">
    {''.join(rows)}
  </div>
  <div class="no-results" id="no-results">Keine Treffer.</div>

<script>
  const buttons = document.querySelectorAll('.filter-btn');
  const eventsEls = document.querySelectorAll('.event');
  const searchBox = document.getElementById('search');
  const noResults = document.getElementById('no-results');

  function applyFilters() {{
    const activeFilters = Array.from(document.querySelectorAll('.filter-btn.active'))
      .map(b => b.dataset.filter);
    const showAll = activeFilters.includes('all') || activeFilters.length === 0;
    // Suche pro Wort: jedes eingegebene Wort muss irgendwo im Text
    // vorkommen (egal an welcher Stelle, egal in welcher Reihenfolge) -
    // "Acheron" findet "Conquest of Acheron" genauso wie "Acheron Conquest".
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
      // Single-Select: der geklickte Button ist der einzige aktive -
      // Klick auf "Brawl" zeigt NUR Brawls, nicht Brawl+bisherige Auswahl.
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

    parser = argparse.ArgumentParser(description="Erzeugt die TU History Timeline (HTML) aus den XML-Dateien.")
    parser.add_argument("--input", default=script_dir, help="Ordner mit den drei XML-Dateien (Standard: Ordner des Skripts)")
    parser.add_argument("--output", default=os.path.join(script_dir, "timeline.html"), help="Ziel-HTML-Datei (Standard: timeline.html neben dem Skript)")
    parser.add_argument("--images-dir", default="images", help="Ordner fuer heruntergeladene Bilder (Standard: images)")
    parser.add_argument("--no-download", action="store_true", help="Bild-Download ueberspringen (nur HTML neu bauen)")
    parser.add_argument("--no-xml-download", action="store_true", help="Kein erneutes Herunterladen der XML-Dateien - vorhandene lokale Dateien verwenden")
    parser.add_argument("--scrape-wiki", action="store_true", help="Fuer Conquest-Events ohne Banner: Fallback-Suche im offiziellen Fandom-Wiki (best effort)")
    args = parser.parse_args()

    print(f"Arbeitsordner (Skript-Speicherort): {args.input}")

    if not args.no_xml_download:
        download_xml_files(args.input)
    else:
        print("0) XML-Download uebersprungen (--no-xml-download)")

    images_dir = os.path.join(os.path.dirname(os.path.abspath(args.output)) or ".", args.images_dir)
    os.makedirs(images_dir, exist_ok=True)

    print("1) Lese XML-Dateien ein...")
    events = parse_events(args.input, scrape_wiki=args.scrape_wiki, images_dir=images_dir)
    print(f"  -> {len(events)} gueltige Events insgesamt (chronologisch sortiert)")

    if not events:
        print("Keine Events gefunden - Abbruch.")
        sys.exit(1)

    if args.no_download:
        available = {
            f for f in {e["web_picture"] for e in events if e["web_picture"]}
            if os.path.exists(os.path.join(images_dir, f))
        }
        print("\n3) Bild-Download uebersprungen (--no-download)")
    else:
        print("\n3) Lade Bilder herunter...")
        available = download_images(events, images_dir)

    print("\n4) Erzeuge HTML-Timeline...")
    html_out = build_html(events, available, images_relpath=args.images_dir)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html_out)

    print(f"\nFertig! -> {args.output}")
    print(f"Bilder liegen in: {os.path.abspath(images_dir)}")


if __name__ == "__main__":
    # Wenn das Skript per Doppelklick gestartet wird (kein PowerShell/Terminal
    # drumherum), schliesst sich das Konsolenfenster sonst sofort wieder -
    # egal ob alles geklappt hat oder ein Fehler aufgetreten ist. Deshalb hier
    # ein Fehler-Catch mit Traceback und am Ende immer eine Pause.
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
            print("Fertig. Fenster kann geschlossen werden.")
        else:
            print("Es ist ein Fehler aufgetreten (siehe oben).")
        if os.name == "nt":
            # Nativer Windows-Befehl statt input() - haengt zuverlaessig an
            # der Konsole, auch wenn stdin bei einem per Doppelklick
            # gestarteten Prozess nicht sauber verbunden ist.
            os.system("pause")
        else:
            try:
                input("Enter druecken zum Beenden...")
            except EOFError:
                pass
        sys.exit(exit_code)
