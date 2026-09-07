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
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

IMAGE_BASE_URL = "https://cdn.synapsegames.com/unleashed/images/"
XML_BASE_URL = "https://mobile.tyrantonline.com/assets/"

# (Dateiname, XML-Elementname, Anzeige-Typ, Badge-Farbe)
SOURCES = [
    ("faction_wars_fp3.xml", "faction_war", "Guild War", "#8e44ad"),
    ("raids_x42.xml", "raid", "Raid", "#c0392b"),
    ("battle_events_h52.xml", "battle_event", "Brawl", "#2980b9"),
]


def download_xml_files(target_dir):
    """Laedt die drei Quell-XMLs frisch von tyrantonline.com herunter.
    Bei Fehlern (kein Netz, 404, ...) bleibt eine bereits vorhandene lokale
    Datei einfach unangetastet, statt das Skript abzubrechen."""
    print("0) Lade aktuelle XML-Dateien von tyrantonline.com...")
    for filename, _tag, _label, _color in SOURCES:
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


def parse_events(input_dir):
    """Liest alle drei XML-Dateien und liefert eine Liste von dicts."""
    events = []
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
            try:
                start_time = int(start_raw)
                end_time = int(end_raw) if end_raw else start_time
            except ValueError:
                continue
            if start_time <= 0:
                continue

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
            img_html = f'<img src="{html.escape(images_relpath + "/" + e["web_picture"])}" alt="{html.escape(e["name"])}" loading="lazy">'
        else:
            img_html = '<div class="no-image">Kein Bild</div>'

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
    const query = searchBox.value.trim().toLowerCase();

    let visibleCount = 0;
    eventsEls.forEach(ev => {{
      const type = ev.dataset.type;
      const matchesType = showAll || activeFilters.includes(type);
      const matchesSearch = !query || ev.dataset.search.includes(query);
      const visible = matchesType && matchesSearch;
      ev.classList.toggle('hidden', !visible);
      if (visible) visibleCount++;
    }});
    noResults.classList.toggle('visible', visibleCount === 0);
  }}

  buttons.forEach(btn => {{
    btn.addEventListener('click', () => {{
      const filter = btn.dataset.filter;
      if (filter === 'all') {{
        buttons.forEach(b => b.classList.toggle('active', b.dataset.filter === 'all'));
      }} else {{
        document.querySelector('[data-filter="all"]').classList.remove('active');
        btn.classList.toggle('active');
      }}
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
    args = parser.parse_args()

    print(f"Arbeitsordner (Skript-Speicherort): {args.input}")

    if not args.no_xml_download:
        download_xml_files(args.input)
    else:
        print("0) XML-Download uebersprungen (--no-xml-download)")

    print("1) Lese XML-Dateien ein...")
    events = parse_events(args.input)
    print(f"  -> {len(events)} gueltige Events insgesamt (chronologisch sortiert)")

    if not events:
        print("Keine Events gefunden - Abbruch.")
        sys.exit(1)

    images_dir = os.path.join(os.path.dirname(os.path.abspath(args.output)) or ".", args.images_dir)
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
    print(f"Bilder liegen in: {images_dir}")


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
