#!/usr/bin/env python3
"""letters_feed.py — generate an RSS 2.0 feed of shareholder letters.

Standard library only. Python 3.9+.

Usage:
    python3 letters_feed.py                     # live run, writes feed.xml
    python3 letters_feed.py --offline fixtures  # no network, uses fixtures/
    python3 letters_feed.py --output my.xml --config companies.json
"""

import argparse
import html as _html
import json
import re
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

TIMEOUT = 15  # seconds, contract C13
USER_AGENT = "Mozilla/5.0 (compatible; letters-feed/1.0; personal RSS generator)"


def warn(msg):
    print(f"WARNING: {msg}", file=sys.stderr)


def die(msg, code=2):
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------- network ---

def http_exists(url, offline_dir=None):
    """True if URL responds < 400. Offline mode: check fixtures/probe_urls.json."""
    if offline_dir is not None:
        probe_file = Path(offline_dir) / "probe_urls.json"
        if not probe_file.exists():
            return False
        return url in json.loads(probe_file.read_text())
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status < 400
    except urllib.error.HTTPError as e:
        if e.code == 405:  # HEAD not allowed; retry tiny GET
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       "Range": "bytes=0-0"})
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                    return resp.status < 400
            except Exception:
                return False
        return False
    except Exception:
        return False


def http_get(url, offline_dir=None, fixture_name=None):
    """Return page text. Offline mode: read fixtures/<fixture_name>."""
    if offline_dir is not None:
        fixture = Path(offline_dir) / fixture_name
        return fixture.read_text(encoding="utf-8")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


# --------------------------------------------------------------- adapters ---

def discover_probe(company, now, offline_dir):
    """URL-pattern probing for JS-heavy sites with predictable PDF/report URLs.

    Config keys: url_template ({year}/{next_year} placeholders),
    letter_title (may contain {year}), start_year.
    """
    cfg = company["probe"]
    found = []
    for year in range(cfg["start_year"], now.year + 1):
        url = cfg["url_template"].format(year=year, next_year=year + 1)
        if http_exists(url, offline_dir):
            found.append({
                "company": company["name"],
                "company_id": company["id"],
                "title": cfg["letter_title"].format(year=year),
                "year": year,
                "link": url,
                "date": None,  # unknown -> discovery date (contract C24)
            })
    return found


def discover_parse(company, now, offline_dir):
    """Regex-parse a plain-HTML index page.

    Config keys: index_url, item_regex (groups: link, title),
    optional date_regex + date_format, optional strip_prefix,
    optional link_base for relative URLs.
    """
    cfg = company["parse"]
    html = http_get(cfg["index_url"], offline_dir,
                    fixture_name=f"{company['id']}_index.html")
    found = []
    for m in re.finditer(cfg["item_regex"], html, re.IGNORECASE | re.DOTALL):
        link, raw_title = m.group(1).strip(), _html.unescape(re.sub(r"\s+", " ", m.group(2)).strip())
        if cfg.get("link_base") and not link.startswith("http"):
            link = cfg["link_base"].rstrip("/") + "/" + link.lstrip("/")
        title = raw_title
        if cfg.get("strip_prefix"):
            title = re.sub(cfg["strip_prefix"], "", title).strip()
        date = None
        if cfg.get("date_regex"):
            after = html[m.end():m.end() + 300]
            dm = re.search(cfg["date_regex"], after)
            if dm:
                try:
                    date = datetime.strptime(dm.group(1), cfg["date_format"]) \
                                   .replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
        ym = re.search(r"(19|20)\d{2}", title)
        year = int(ym.group(0)) if ym else (date.year if date else now.year)
        found.append({
            "company": company["name"],
            "company_id": company["id"],
            "title": title,
            "year": year,
            "link": link,
            "date": date.isoformat() if date else None,
        })
    if not found:
        raise RuntimeError("index page yielded zero letters (page layout may have changed)")
    return found


ADAPTERS = {"probe": discover_probe, "parse_index": discover_parse}


# ------------------------------------------------------------------ state ---

def load_json(path, what):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        die(f"{what} not found: {path}")
    except json.JSONDecodeError as e:
        die(f"{what} is not valid JSON ({path}): {e}")


def _estimate_pub_date(year, now):
    """Letters are typically published Feb-Apr of the following year.
    Estimate Mar 1 of year+1, capped at discovery time (never in the future)."""
    est = datetime(year + 1, 3, 1, 12, tzinfo=timezone.utc)
    return min(est, now).isoformat()


def merge_letters(state, letters, source, now):
    """Append-only merge into state dict keyed by link (guid). Contract C17."""
    added = 0
    for L in letters:
        guid = L["link"]
        if guid in state:
            continue
        state[guid] = {
            "company": L["company"],
            "company_id": L["company_id"],
            "title": L["title"],
            "year": L["year"],
            "link": L["link"],
            "pub_date": L.get("date") or _estimate_pub_date(L["year"], now),
            "discovered": now.isoformat(),
            "source": source,
        }
        added += 1
    return added


# -------------------------------------------------------------------- rss ---

def build_feed(feed_cfg, letters, now):
    rss = ET.Element("rss", version="2.0")
    ch = ET.SubElement(rss, "channel")
    for tag in ("title", "link", "description"):
        ET.SubElement(ch, tag).text = feed_cfg[tag]
    ET.SubElement(ch, "lastBuildDate").text = format_datetime(now)  # C23

    items = sorted(letters.values(), key=lambda x: x["pub_date"], reverse=True)
    for it in items:
        item = ET.SubElement(ch, "item")
        # Contract C19: "{Company} — {Letter title} ({year})"
        ET.SubElement(item, "title").text = f"{it['company']} — {it['title']} ({it['year']})"
        ET.SubElement(item, "link").text = it["link"]
        guid = ET.SubElement(item, "guid", isPermaLink="true")
        guid.text = it["link"]
        pd = datetime.fromisoformat(it["pub_date"])
        if pd.tzinfo is None:
            pd = pd.replace(tzinfo=timezone.utc)
        ET.SubElement(item, "pubDate").text = format_datetime(pd)
        ET.SubElement(item, "description").text = \
            f"{it['company']} shareholder letter: {it['title']}."
    return rss


# ------------------------------------------------------------------- main ---

def main():
    ap = argparse.ArgumentParser(description="Generate shareholder-letters RSS feed.")
    ap.add_argument("--config", default="companies.json")
    ap.add_argument("--seed", default="seed_letters.json")
    ap.add_argument("--state", default="letters.json")
    ap.add_argument("--output", default="feed.xml")
    ap.add_argument("--offline", metavar="FIXTURES_DIR", default=None,
                    help="no network; probe/parse against fixture files")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    config = load_json(args.config, "companies.json")
    seed = load_json(args.seed, "seed file")

    # load state (append-only across runs, contract C17)
    state = {}
    if Path(args.state).exists():
        state = load_json(args.state, "state file")

    n_seed = merge_letters(
        state,
        [{"company": s["company"], "company_id": s["company_id"],
          "title": s["title"], "year": s["year"], "link": s["link"],
          "date": s.get("date")} for s in seed["letters"]],
        "seed", now)

    n_disc = 0
    for company in config["companies"]:
        strategy = company.get("strategy")
        adapter = ADAPTERS.get(strategy)
        if adapter is None:
            warn(f"source '{company.get('name', '?')}' failed: unknown strategy '{strategy}'")
            continue
        try:
            letters = adapter(company, now, args.offline)
            n_disc += merge_letters(state, letters, strategy, now)
        except Exception as e:  # one dead source must not kill the feed (C12)
            warn(f"source '{company['name']}' failed: {e}")

    Path(args.state).write_text(json.dumps(state, indent=2), encoding="utf-8")

    rss = build_feed(config["feed"], state, now)
    ET.indent(rss)
    Path(args.output).write_bytes(
        b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="utf-8"))

    print(f"feed.xml written: {len(state)} letters "
          f"({n_seed} new from seed, {n_disc} newly discovered)")


if __name__ == "__main__":
    main()
