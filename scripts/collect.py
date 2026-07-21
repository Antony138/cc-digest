#!/usr/bin/env python3
"""collect.py — deterministic engine for cc-digest (stdlib only).

Subcommands:
  check              decide PUBLISH / SKIP against docs/data/index.json
  collect [--out P]  emit JSON array of changelog blocks newer than last_version
  finalize PATH      validate a digest JSON, update index.json, regenerate rss.xml

Global flag: --root PATH (default: this script's parent's parent), accepted
either before or after the subcommand.

Exit codes: 0 = normal (both PUBLISH and SKIP), 1 = digest validation failure,
2 = environment error (index missing/corrupt, all data sources failed, ...).
"""

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

JST = timezone(timedelta(hours=9))
PRIMARY_URL = "https://code.claude.com/docs/en/changelog.md"
FALLBACK_URL = "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md"
NPM_URL = "https://registry.npmjs.org/@anthropic-ai/claude-code"
USER_AGENT = "cc-digest-collect/1.0 (+https://antony138.github.io/cc-digest/)"
TIMEOUT = 30

SITE_LINK = "https://antony138.github.io/cc-digest/"
RSS_TITLE = "Claude Code Digest — 中文更新摘要"
RSS_DESC = "Claude Code 更新的中文 AI 摘要，每 2-3 天一期。"
RSS_MAX_ITEMS = 20

CATEGORIES = ("新功能", "改进", "修复", "破坏性变更", "安全")

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5,
    "June": 6, "July": 7, "August": 8, "September": 9, "October": 10,
    "November": 11, "December": 12,
}
RFC822_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
RFC822_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def log(msg):
    print(msg, file=sys.stderr)


def die(msg, code=2):
    log("ERROR: " + msg)
    sys.exit(code)


def semver_key(version):
    """Return an int tuple for strict X.Y.Z versions, else None."""
    m = SEMVER_RE.match(version.strip())
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


# ---------------------------------------------------------------- fetching

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def parse_english_date(text):
    """'July 20, 2026' -> '2026-07-20' (locale-independent). None on failure."""
    m = re.match(r"^\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\s*$", text)
    if not m:
        return None
    month = MONTHS.get(m.group(1))
    if not month:
        return None
    return "%04d-%02d-%02d" % (int(m.group(3)), month, int(m.group(2)))


def parse_primary(text):
    """Parse MDX <Update label=.. description=..> blocks with '* ' bullets."""
    entries = []
    seen = set()
    for m in re.finditer(
        r'<Update\s+label="([^"]+)"\s+description="([^"]+)"\s*>(.*?)</Update>',
        text, re.S,
    ):
        version = m.group(1).strip()
        if semver_key(version) is None or version in seen:
            continue
        date = parse_english_date(m.group(2))
        bullets = [b.strip() for b in
                   re.findall(r"^\s*\*\s+(.*\S)\s*$", m.group(3), re.M)]
        if not bullets:
            continue
        seen.add(version)
        entries.append({"version": version, "date": date, "bullets": bullets})
    if not entries:
        raise ValueError("no <Update> blocks parsed from primary source")
    return entries


def parse_fallback(text, npm_time):
    """Parse '## X.Y.Z' headings with '- ' bullets; dates from npm 'time' map."""
    entries = []
    seen = set()
    version = None
    bullets = []

    def flush():
        if version and bullets and version not in seen:
            seen.add(version)
            iso = (npm_time.get(version) or "")[:10]
            entries.append({
                "version": version,
                "date": iso if ISO_DATE_RE.match(iso) else None,
                "bullets": list(bullets),
            })

    for line in text.splitlines():
        h = re.match(r"^##\s+(\S+)\s*$", line)
        if h:
            flush()
            version = h.group(1) if semver_key(h.group(1)) else None
            bullets = []
            continue
        b = re.match(r"^-\s+(.*\S)\s*$", line)
        if b and version:
            bullets.append(b.group(1))
    flush()
    if not entries:
        raise ValueError("no '## X.Y.Z' sections parsed from fallback source")
    return entries


def fetch_changelog():
    """Return changelog entries [{version, date, bullets}, ...] newest-first.

    Tries the primary MDX source, then the GitHub CHANGELOG + npm registry
    dates. Exits 2 if every source fails.
    """
    try:
        entries = parse_primary(fetch(PRIMARY_URL))
        entries.sort(key=lambda e: semver_key(e["version"]), reverse=True)
        return entries
    except Exception as exc:  # noqa: BLE001 - deliberate blanket fallback
        log("primary source failed (%s: %s); trying fallback" %
            (type(exc).__name__, exc))
    try:
        text = fetch(FALLBACK_URL)
        npm_time = {}
        try:
            npm_time = json.loads(fetch(NPM_URL)).get("time", {})
        except Exception as exc:  # noqa: BLE001
            log("npm registry fetch failed (%s: %s); dates unavailable" %
                (type(exc).__name__, exc))
        entries = parse_fallback(text, npm_time)
        entries.sort(key=lambda e: semver_key(e["version"]), reverse=True)
        return entries
    except Exception as exc:  # noqa: BLE001
        die("all changelog sources failed; last error: %s: %s" %
            (type(exc).__name__, exc))


# ---------------------------------------------------------------- index

def index_path(root):
    return root / "docs" / "data" / "index.json"


def load_index(root, bootstrap=False):
    path = index_path(root)
    if not path.is_file():
        if bootstrap:
            # 首次 finalize 时自举一个空索引；check/collect 则必须已有索引，
            # 否则「新版本」无从界定（会误判成要总结全部历史）。
            return {"last_version": "0.0.0",
                    "last_digest_date": "1970-01-01",
                    "digests": []}
        die("index not found: %s (run `finalize` on a first digest to create it)"
            % path)
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        die("index unreadable/corrupt at %s: %s" % (path, exc))
    if not isinstance(index, dict):
        die("index root must be an object: %s" % path)
    if semver_key(str(index.get("last_version", ""))) is None:
        die("index.last_version is not a valid X.Y.Z semver: %r"
            % index.get("last_version"))
    if not ISO_DATE_RE.match(str(index.get("last_digest_date", ""))):
        die("index.last_digest_date is not YYYY-MM-DD: %r"
            % index.get("last_digest_date"))
    if not isinstance(index.get("digests"), list):
        die("index.digests must be a list")
    return index


def new_entries(entries, last_version):
    last = semver_key(last_version)
    fresh = [e for e in entries if semver_key(e["version"]) > last]
    fresh.sort(key=lambda e: semver_key(e["version"]), reverse=True)
    return fresh


# ---------------------------------------------------------------- check

def cmd_check(root):
    index = load_index(root)
    entries = fetch_changelog()
    fresh = new_entries(entries, index["last_version"])
    today = datetime.now(JST).date()
    last = datetime.strptime(index["last_digest_date"], "%Y-%m-%d").date()
    days_since = (today - last).days
    if not fresh:
        print("SKIP reason=no-new-versions")
    elif days_since >= 2:
        print("PUBLISH new=%d days_since=%d versions=%s"
              % (len(fresh), days_since,
                 ",".join(e["version"] for e in fresh)))
    else:
        print("SKIP reason=too-soon days_since=%d pending=%d"
              % (days_since, len(fresh)))
    return 0


# ---------------------------------------------------------------- collect

def cmd_collect(root, out):
    index = load_index(root)
    fresh = new_entries(fetch_changelog(), index["last_version"])
    text = json.dumps(fresh, ensure_ascii=False, indent=1) + "\n"
    if out:
        Path(out).write_text(text, encoding="utf-8")
        log("wrote %d version blocks to %s" % (len(fresh), out))
    else:
        sys.stdout.write(text)
    return 0


# ---------------------------------------------------------------- finalize

def validate_digest(digest, expected_date):
    """Return a list of human-readable violations (empty when valid)."""
    v = []
    if not isinstance(digest, dict):
        return ["digest root must be a JSON object"]

    for key in ("date", "version_range", "tldr", "highlights", "versions"):
        if key not in digest:
            v.append("missing required key: %s" % key)
    if v:
        return v

    date = digest["date"]
    if not (isinstance(date, str) and ISO_DATE_RE.match(date)):
        v.append("date must be a YYYY-MM-DD string, got %r" % (date,))
    else:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            v.append("date is not a real calendar date: %r" % date)
        if date != expected_date:
            v.append("date %r does not match filename date %r"
                     % (date, expected_date))

    if not (isinstance(digest["tldr"], str) and digest["tldr"].strip()):
        v.append("tldr must be a non-empty string")

    # versions: newest-first, non-empty, non-empty string bullets
    versions = digest["versions"]
    if not (isinstance(versions, list) and versions):
        v.append("versions must be a non-empty list")
        versions = []
    keys = []
    for i, entry in enumerate(versions):
        where = "versions[%d]" % i
        if not isinstance(entry, dict):
            v.append("%s must be an object" % where)
            continue
        ver = entry.get("version")
        key = semver_key(ver) if isinstance(ver, str) else None
        if key is None:
            v.append("%s.version must be an X.Y.Z string, got %r"
                     % (where, ver))
        else:
            keys.append(key)
        d = entry.get("date")
        if d is not None and not (isinstance(d, str) and ISO_DATE_RE.match(d)):
            # null 合法：collect 在日期源不可用时会输出 date: null，前端可容忍
            v.append("%s.date must be a YYYY-MM-DD string or null, got %r"
                     % (where, d))
        bullets = entry.get("bullets")
        if not (isinstance(bullets, list) and bullets
                and all(isinstance(b, str) and b.strip() for b in bullets)):
            v.append("%s.bullets must be a non-empty list of non-empty strings"
                     % where)
    if len(keys) == len(versions) and keys != sorted(keys, reverse=True):
        v.append("versions must be sorted newest-first by semver")
    if len(set(keys)) != len(keys):
        v.append("versions contains duplicate version numbers")

    # version_range consistency with versions
    vr = digest["version_range"]
    if not isinstance(vr, dict):
        v.append("version_range must be an object")
    else:
        for key in ("from", "to", "count"):
            if key not in vr:
                v.append("version_range missing key: %s" % key)
        if versions and all(k in vr for k in ("from", "to", "count")):
            newest = versions[0].get("version") if isinstance(versions[0], dict) else None
            oldest = versions[-1].get("version") if isinstance(versions[-1], dict) else None
            if vr["from"] != oldest:
                v.append("version_range.from %r != oldest version %r"
                         % (vr["from"], oldest))
            if vr["to"] != newest:
                v.append("version_range.to %r != newest version %r"
                         % (vr["to"], newest))
            if vr["count"] != len(versions):
                v.append("version_range.count %r != len(versions) %d"
                         % (vr["count"], len(versions)))

    # highlights: 3-6, category enum, action string-or-null
    highlights = digest["highlights"]
    if not isinstance(highlights, list):
        v.append("highlights must be a list")
        highlights = []
    if not 3 <= len(highlights) <= 6:
        v.append("highlights must contain 3-6 items, got %d" % len(highlights))
    for i, h in enumerate(highlights):
        where = "highlights[%d]" % i
        if not isinstance(h, dict):
            v.append("%s must be an object" % where)
            continue
        for key in ("title", "category", "detail", "action", "versions"):
            if key not in h:
                v.append("%s missing key: %s" % (where, key))
        if "title" in h and not (isinstance(h["title"], str) and h["title"].strip()):
            v.append("%s.title must be a non-empty string" % where)
        if "category" in h and h["category"] not in CATEGORIES:
            v.append("%s.category %r not in %s"
                     % (where, h.get("category"), "/".join(CATEGORIES)))
        if "detail" in h and not (isinstance(h["detail"], str) and h["detail"].strip()):
            v.append("%s.detail must be a non-empty string" % where)
        if "action" in h and not (h["action"] is None or isinstance(h["action"], str)):
            v.append("%s.action must be a string or null, got %r"
                     % (where, h["action"]))
        hv = h.get("versions")
        if "versions" in h and not (
            isinstance(hv, list) and hv
            and all(isinstance(x, str) and x.strip() for x in hv)
        ):
            v.append("%s.versions must be a non-empty list of strings" % where)

    return v


def rfc822_date(iso_date):
    d = datetime.strptime(iso_date, "%Y-%m-%d")
    return "%s, %02d %s %d 07:00:00 +0900" % (
        RFC822_DAYS[d.weekday()], d.day, RFC822_MONTHS[d.month - 1], d.year)


def build_rss(index, data_dir):
    items = []
    for meta in index["digests"][:RSS_MAX_ITEMS]:
        date = meta["date"]
        title = "%s：%s → %s（%s 个版本）" % (
            date, meta["from"], meta["to"], meta["count"])
        desc_parts = [str(meta.get("tldr", "")).strip()]
        digest_file = data_dir / ("%s.json" % date)
        if digest_file.is_file():
            try:
                digest = json.loads(digest_file.read_text(encoding="utf-8"))
                titles = [h["title"] for h in digest.get("highlights", [])
                          if isinstance(h, dict) and isinstance(h.get("title"), str)]
                if titles:
                    desc_parts.append("亮点：" + "；".join(titles))
            except (json.JSONDecodeError, OSError):
                pass
        description = "\n".join(p for p in desc_parts if p)
        guid = SITE_LINK + "#" + date
        items.append(
            "  <item>\n"
            "   <title>%s</title>\n"
            "   <link>%s</link>\n"
            "   <guid isPermaLink=\"false\">%s</guid>\n"
            "   <pubDate>%s</pubDate>\n"
            "   <description>%s</description>\n"
            "  </item>" % (
                escape(title), escape(SITE_LINK), escape(guid),
                rfc822_date(date), escape(description)))
    build_date = rfc822_date(index["last_digest_date"])
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<rss version=\"2.0\">\n"
        " <channel>\n"
        "  <title>%s</title>\n"
        "  <link>%s</link>\n"
        "  <description>%s</description>\n"
        "  <language>zh-cn</language>\n"
        "  <lastBuildDate>%s</lastBuildDate>\n"
        "%s\n"
        " </channel>\n"
        "</rss>\n" % (
            escape(RSS_TITLE), escape(SITE_LINK), escape(RSS_DESC),
            build_date, "\n".join(items)))


def cmd_finalize(root, digest_path):
    path = Path(digest_path)
    if not path.is_file():
        die("digest file not found: %s" % path)

    stem = path.name[:-5] if path.name.endswith(".json") else path.name
    violations = []
    if not ISO_DATE_RE.match(stem):
        violations.append(
            "filename must be YYYY-MM-DD.json, got %r" % path.name)
    try:
        digest = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        violations.append("digest is not valid JSON: %s" % exc)
        digest = None

    if digest is not None:
        violations.extend(validate_digest(digest, stem))
    if violations:
        for item in violations:
            print("VIOLATION: %s" % item)
        print("finalize aborted: %d violation(s); nothing was modified"
              % len(violations))
        sys.exit(1)

    index = load_index(root, bootstrap=True)

    digests = [d for d in index["digests"]
               if not (isinstance(d, dict) and d.get("date") == digest["date"])]
    digests.append({
        "date": digest["date"],
        "from": digest["version_range"]["from"],
        "to": digest["version_range"]["to"],
        "count": digest["version_range"]["count"],
        "tldr": digest["tldr"],
    })
    digests.sort(key=lambda d: d["date"], reverse=True)
    index["digests"] = digests
    index["last_version"] = max(
        index["last_version"], digest["version_range"]["to"], key=semver_key)
    index["last_digest_date"] = max(
        index["last_digest_date"], digest["date"])

    data_dir = index_path(root).parent
    index_path(root).write_text(
        json.dumps(index, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    rss_file = root / "docs" / "rss.xml"
    rss_file.write_text(build_rss(index, data_dir), encoding="utf-8")

    print("OK date=%s range=%s->%s count=%s digests=%d last_version=%s "
          "rss_items=%d"
          % (digest["date"], digest["version_range"]["from"],
             digest["version_range"]["to"], digest["version_range"]["count"],
             len(digests), index["last_version"],
             min(len(digests), RSS_MAX_ITEMS)))
    return 0


# ---------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="collect.py",
        description="Deterministic changelog engine for cc-digest.")
    parser.add_argument("--root", default=None,
                        help="project root (default: script's parent's parent)")

    # Also accept --root after the subcommand; SUPPRESS keeps the
    # top-level value when the sub-level flag is absent.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", parents=[common],
                   help="decide PUBLISH/SKIP against index.json")
    p_collect = sub.add_parser("collect", parents=[common],
                               help="emit new version blocks as JSON")
    p_collect.add_argument("--out", default=None,
                           help="write JSON here instead of stdout")
    p_finalize = sub.add_parser("finalize", parents=[common],
                                help="validate digest, update index + rss")
    p_finalize.add_argument("path", help="digest JSON file (YYYY-MM-DD.json)")

    args = parser.parse_args(argv)
    root = (Path(args.root).resolve() if args.root
            else Path(__file__).resolve().parent.parent)

    if args.command == "check":
        return cmd_check(root)
    if args.command == "collect":
        return cmd_collect(root, args.out)
    return cmd_finalize(root, args.path)


if __name__ == "__main__":
    sys.exit(main())
