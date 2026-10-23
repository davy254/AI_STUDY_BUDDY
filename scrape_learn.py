"""
Scrape AZ-900 learning-path content from Microsoft Learn into Markdown files
that ingest.py can load.  UNOFFICIAL study tool, personal / non-commercial use.

Usage:
    python scrape_learn.py                      # reads the AZ-900 certification page -> its 3 learning paths
    python scrape_learn.py PATH_URL [PATH_URL]  # your own learning-path URLs
    python scrape_learn.py MODULE_URL           # single modules also work
    python scrape_learn.py --out notes --force

Output:
    notes/<module-slug>/<unit-slug>.md
    notes/domains.json   (module -> AZ-900 domain, read by ingest.py)
    notes/SOURCE.txt     (attribution)

Then:
    python ingest.py notes --base-url https://learn.microsoft.com/en-us/training/modules

Be polite: one request per second by default, robots.txt respected.
Credit Microsoft Learn as the source of the notes in your app and README.
"""
import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as to_md

BASE = "https://learn.microsoft.com"
USER_AGENT = "az900-study-app-hackathon/0.1 (unofficial study tool; personal use)"

CERT_URL = f"{BASE}/en-us/credentials/certifications/azure-fundamentals/"

# "Training in this course" on the certification page (slug -> expected module count)
KNOWN_PATHS = {
    "microsoft-azure-fundamentals-describe-cloud-concepts": 3,
    "azure-fundamentals-describe-azure-architecture-services": 5,
    "describe-azure-management-governance": 4,
}
DEFAULT_PATHS = [f"{BASE}/en-us/training/paths/{slug}/" for slug in KNOWN_PATHS]

PATH_DOMAIN_HINTS = {
    "cloud-concepts": "cloud_concepts",
    "architecture": "architecture_services",
    "management": "management_governance",
    "governance": "management_governance",
}

SKIP_UNIT_WORDS = ("knowledge-check",)   # Microsoft's own quiz pages: not study notes

MODULE_RE = re.compile(r"/(?:[a-z]{2}-[a-z]{2}/)?(?:training|learn)/modules/([a-z0-9-]+)", re.I)
PATH_RE = re.compile(r"/(?:[a-z]{2}-[a-z]{2}/)?(?:training|learn)/paths/([a-z0-9-]+)", re.I)

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})
_robots = None
_delay = 1.0


def init_robots():
    global _robots
    rp = RobotFileParser(f"{BASE}/robots.txt")
    try:
        rp.read()
        _robots = rp
    except Exception:
        _robots = None


def fetch(url: str):
    """Return (final_url, html). Raises on HTTP errors or robots disallow."""
    if _robots is not None and not _robots.can_fetch(USER_AGENT, url):
        raise PermissionError(f"robots.txt disallows {url}")
    time.sleep(_delay)
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.url, r.text


def domain_from_path_slug(slug: str):
    for hint, domain in PATH_DOMAIN_HINTS.items():
        if hint in slug:
            return domain
    return None


def module_slugs_from_path(final_url: str, html: str):
    soup = BeautifulSoup(html, "html.parser")
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        full = urljoin(final_url, a["href"])
        m = MODULE_RE.search(urlparse(full).path)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(m.group(1))
    return out


def unit_slugs_from_module(final_url: str, html: str):
    soup = BeautifulSoup(html, "html.parser")
    prefix = urlparse(final_url).path.rstrip("/").lower() + "/"
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        full = urljoin(final_url, a["href"]).split("#")[0].split("?")[0]
        path = urlparse(full).path.lower()
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix):].strip("/")
        if rest and "/" not in rest and rest not in seen:
            seen.add(rest)
            out.append(rest)
    return out


def discover_paths():
    """Read the AZ-900 certification page and return its learning-path URLs.
    Always includes KNOWN_PATHS, so a layout change on that page cannot lose them."""
    slugs = list(KNOWN_PATHS)
    try:
        final_url, html = fetch(CERT_URL)
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            m = PATH_RE.search(urlparse(urljoin(final_url, a["href"])).path)
            if m:
                slug = m.group(1)
                if slug not in slugs and domain_from_path_slug(slug):
                    slugs.append(slug)
        print(f"Certification page: {len(slugs)} learning paths")
    except Exception as e:
        print(f"! could not read certification page ({e}); using the built-in list of 3 paths")
    return [f"{BASE}/en-us/training/paths/{slug}/" for slug in slugs]


def extract_unit(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("h1")
    title = title_tag.get_text(" ", strip=True) if title_tag else ""

    main = soup.select_one("#unit-inner-section") or soup.find("main") or soup.body
    if main is None:
        return ""

    for tag in main.find_all(["script", "style", "nav", "form", "button", "svg", "iframe", "noscript"]):
        tag.decompose()

    text = to_md(str(main), heading_style="ATX", strip=["img"])
    text = re.sub(r"^\s*(Next unit|Previous unit)\s*:.*$", "", text, flags=re.M | re.I)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if title and not text.lstrip().startswith("# "):
        text = f"# {title}\n\n{text}"
    return text


def main():
    global _delay
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*", help="learning-path or module URLs (default: the 3 AZ-900 paths)")
    ap.add_argument("--out", default="notes")
    ap.add_argument("--force", action="store_true", help="re-download files that already exist")
    ap.add_argument("--no-discover", action="store_true",
                    help="skip reading the certification page; use the built-in 3 paths")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    args = ap.parse_args()
    _delay = args.delay

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    init_robots()

    # 1. collect modules (and their domain) ------------------------------------
    modules = {}   # slug -> domain (or None)
    if args.urls:
        targets = args.urls
    elif args.no_discover:
        targets = DEFAULT_PATHS
    else:
        targets = discover_paths()

    for url in targets:
        if PATH_RE.search(url):
            path_slug = PATH_RE.search(url).group(1)
            domain = domain_from_path_slug(path_slug)
            try:
                final_url, html = fetch(url)
            except Exception as e:
                print(f"! could not fetch path {url}: {e}")
                continue
            slugs = module_slugs_from_path(final_url, html)
            expected = KNOWN_PATHS.get(path_slug)
            note = f" (expected {expected})" if expected and expected != len(slugs) else ""
            print(f"Path {path_slug}: {len(slugs)} modules{note}")
            for s in slugs:
                modules.setdefault(s, domain)
        elif MODULE_RE.search(url):
            modules.setdefault(MODULE_RE.search(url).group(1), None)
        else:
            print(f"! not a Learn path or module URL, skipped: {url}")

    if not modules:
        print("\nNo modules found. Open a learning path in your browser, copy the module URLs, "
              "and pass them directly:  python scrape_learn.py MODULE_URL MODULE_URL ...")
        return

    # 2. scrape each module's units ----------------------------------------------
    saved = 0
    for slug, domain in modules.items():
        module_url = f"{BASE}/en-us/training/modules/{slug}/"
        try:
            final_url, html = fetch(module_url)
        except Exception as e:
            print(f"! module {slug}: {e}")
            continue
        units = [u for u in unit_slugs_from_module(final_url, html)
                 if not any(w in u for w in SKIP_UNIT_WORDS)]
        print(f"Module {slug}: {len(units)} units")

        for unit in units:
            dest = out / slug / f"{unit}.md"
            if dest.exists() and not args.force:
                continue
            try:
                _, unit_html = fetch(f"{final_url.rstrip('/')}/{unit}/")
                text = extract_unit(unit_html)
            except Exception as e:
                print(f"  ! {unit}: {e}")
                continue
            if len(text) < 100:
                print(f"  ! {unit}: almost empty, skipped (page layout may have changed)")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text + "\n", encoding="utf-8")
            saved += 1
            print(f"  saved {dest}")

    # 3. metadata for ingest.py ---------------------------------------------------
    dmap_file = out / "domains.json"
    dmap = json.loads(dmap_file.read_text()) if dmap_file.exists() else {}
    dmap.update({s: d for s, d in modules.items() if d})
    dmap_file.write_text(json.dumps(dmap, indent=2))

    (out / "SOURCE.txt").write_text(
        "Notes in this folder were collected from Microsoft Learn (learn.microsoft.com) "
        "for personal, non-commercial study. Content (c) Microsoft. "
        "This is an UNOFFICIAL study tool and is not affiliated with or endorsed by Microsoft.\n"
    )

    print(f"\nDone. {saved} new files in {out}/")
    print(f"Next: python ingest.py {out} --base-url {BASE}/en-us/training/modules")


if __name__ == "__main__":
    main()
