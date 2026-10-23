"""
Ingest AZ-900 notes (Markdown) into Supabase pgvector.  UNOFFICIAL study tool.

Usage:
    python ingest.py ./notes
    python ingest.py ./notes --base-url https://learn.microsoft.com/training/modules

Notes folder can be:
  * the AZ-900 modules cloned from the MicrosoftDocs/learn GitHub repo, or
  * your own notes exported as .md files.

Embeddings run locally (free) with gte-small, the same model Supabase uses
in Edge Functions, so query vectors and stored vectors match (384 dims).
"""
import argparse
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from supabase import create_client

load_dotenv()

MAX_CHARS = 1800     # ~400 tokens
MIN_CHARS = 200      # drop tiny fragments
BATCH = 50

# Rough keyword -> AZ-900 domain mapping. Edit to match your folder names.
DOMAIN_KEYWORDS = {
    "cloud_concepts": [
        "cloud-concepts", "cloud-computing", "shared-responsibility", "iaas", "paas",
        "saas", "benefits-of-cloud", "consumption", "capex", "high-availability",
        "scalability", "elasticity", "reliability", "predictability", "governance-cloud",
    ],
    "architecture_services": [
        "architecture", "region", "availability-zone", "resource-group", "subscription",
        "management-group", "compute", "virtual-machine", "container", "app-service",
        "functions", "networking", "vnet", "vpn", "expressroute", "dns", "storage",
        "blob", "disk", "database", "cosmos", "sql", "identity", "entra", "active-directory",
        "conditional-access", "rbac", "zero-trust", "defense-in-depth", "defender",
    ],
    "management_governance": [
        "cost", "pricing", "calculator", "tco", "tag", "policy", "blueprint", "lock",
        "purview", "service-trust", "portal", "cloud-shell", "cli", "powershell",
        "arc", "arm", "bicep", "iac", "monitor", "advisor", "service-health",
    ],
}


def guess_domain(text: str) -> str | None:
    t = text.lower()
    scores = {d: sum(t.count(k) for k in kws) for d, kws in DOMAIN_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


def clean_markdown(raw: str) -> str:
    raw = re.sub(r"\A---\n.*?\n---\n", "", raw, flags=re.S)        # YAML front matter
    raw = re.sub(r"^\s*\[!INCLUDE.*$", "", raw, flags=re.M)        # Learn include directives
    raw = re.sub(r"<!--.*?-->", "", raw, flags=re.S)               # HTML comments
    raw = re.sub(r"^\s*:::image.*?:::\s*$", "", raw, flags=re.M | re.S)
    return raw.strip()


def split_by_heading(md: str):
    """Yield (heading, body) sections."""
    parts = re.split(r"^(#{1,3})\s+(.+)$", md, flags=re.M)
    # parts: [preamble, hashes, title, body, hashes, title, body, ...]
    if parts[0].strip():
        yield ("", parts[0].strip())
    for i in range(1, len(parts), 3):
        heading, body = parts[i + 1].strip(), parts[i + 2].strip()
        if body:
            yield (heading, body)


def split_long(text: str, limit: int = MAX_CHARS):
    """Split on paragraph boundaries so chunks stay under the limit."""
    if len(text) <= limit:
        yield text
        return
    buf = ""
    for para in re.split(r"\n\s*\n", text):
        if buf and len(buf) + len(para) + 2 > limit:
            yield buf.strip()
            buf = ""
        buf += para + "\n\n"
    if buf.strip():
        yield buf.strip()


def build_chunks(path: Path, root: Path, base_url: str | None, domain_map: dict | None = None):
    md = clean_markdown(path.read_text(encoding="utf-8", errors="ignore"))
    rel = path.relative_to(root)
    module = rel.parts[0] if len(rel.parts) > 1 else "general"
    unit = path.stem.replace("-", " ").replace("_", " ")
    source_url = f"{base_url.rstrip('/')}/{rel.with_suffix('').as_posix()}" if base_url else None

    # domain from path first (more reliable), then from content
    domain = (domain_map or {}).get(module) or guess_domain(rel.as_posix()) or guess_domain(md[:3000])

    idx = 0
    for heading, body in split_by_heading(md):
        for piece in split_long(body):
            if len(piece) < MIN_CHARS:
                continue
            # prepend heading so the embedding carries the context
            content = f"{heading}\n\n{piece}" if heading else piece
            yield {
                "content": content,
                "module": module,
                "unit": unit,
                "heading": heading or None,
                "chunk_index": idx,
                "source_file": rel.as_posix(),
                "source_url": source_url,
                "domain": domain,
            }
            idx += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("notes_dir")
    ap.add_argument("--base-url", default=None, help="URL prefix used to build source links")
    args = ap.parse_args()

    root = Path(args.notes_dir).resolve()
    files = sorted(root.rglob("*.md"))
    if not files:
        raise SystemExit(f"No .md files found in {root}")

    # module -> domain map written by scrape_learn.py (optional)
    dmap_file = root / "domains.json"
    domain_map = json.loads(dmap_file.read_text()) if dmap_file.exists() else {}

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    print("Loading embedding model (first run downloads ~130 MB)...")
    model = SentenceTransformer("thenlper/gte-small")

    total = 0
    for f in files:
        chunks = list(build_chunks(f, root, args.base_url, domain_map))
        if not chunks:
            continue
        vecs = model.encode([c["content"] for c in chunks], normalize_embeddings=True)
        for c, v in zip(chunks, vecs):
            c["embedding"] = v.tolist()

        # idempotent: replace anything previously ingested from this file
        sb.table("chunks").delete().eq("source_file", chunks[0]["source_file"]).execute()
        for i in range(0, len(chunks), BATCH):
            sb.table("chunks").insert(chunks[i:i + BATCH]).execute()

        total += len(chunks)
        print(f"  {f.relative_to(root)}: {len(chunks)} chunks")

    print(f"\nDone. {total} chunks from {len(files)} files.")
    print("Check domain tagging:  select domain, count(*) from chunks group by domain;")


if __name__ == "__main__":
    main()
