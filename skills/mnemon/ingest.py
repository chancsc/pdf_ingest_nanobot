#!/usr/bin/env python3
"""Ingest a document (URL or local file) into Mnemon for RAG retrieval.

Usage:
    python ingest.py <url_or_path> [--chunk-words N] [--overlap-words N] [--store NAME]

Designed to run in the background — nanobot should launch it like:
    nohup python /root/.nanobot/workspace/skills/mnemon/ingest.py "https://..." \
        > /tmp/mnemon_ingest.log 2>&1 & echo "PID:$!"

Check progress:
    tail -f /tmp/mnemon_ingest.log
"""
import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


MNEMON = os.environ.get("MNEMON_BIN", "/root/go/bin/mnemon")


def _gdrive_direct(url: str) -> str:
    """Convert a Google Drive share link to a direct download URL."""
    import re as _re
    m = _re.search(r"/file/d/([^/]+)", url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return url


def download_url(url: str) -> Path:
    if "drive.google.com" in url:
        url = _gdrive_direct(url)
    # Derive a local filename from the URL
    name = url.split("?")[0].rstrip("/").split("/")[-1] or "document"
    if "." not in name:
        name += ".pdf"
    dest = Path(tempfile.gettempdir()) / name
    print(f"Downloading: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    # Skip SSL verification for self-signed certs (common on internal servers)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, context=ctx) as resp, open(dest, "wb") as f:
        f.write(resp.read())
    print(f"Saved to {dest} ({dest.stat().st_size / 1024:.0f} KB)")
    return dest


def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            sys.exit("pypdf not installed — activate nano_env or run: pip install pypdf")
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages)
    return path.read_text(encoding="utf-8", errors="replace")


def chunk_text(text: str, chunk_words: int, overlap_words: int) -> list[str]:
    words = text.split()
    chunks = []
    step = max(1, chunk_words - overlap_words)
    for i in range(0, len(words), step):
        chunk = " ".join(words[i : i + chunk_words])
        if chunk.strip():
            chunks.append(chunk.strip())
        if i + chunk_words >= len(words):
            break
    return chunks


def remember(chunk: str, source: str, store: str | None) -> dict:
    cmd = [MNEMON, "remember", chunk, "--cat", "fact", "--imp", "2", "--source", source]
    if store:
        cmd += ["--store", store]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return {"ok": result.returncode == 0, "out": result.stdout.strip(), "err": result.stderr.strip()}


def main():
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Ingest a document (URL or file) into Mnemon for RAG.")
    parser.add_argument("source", help="URL or local file path (PDF, TXT, MD, etc.)")
    parser.add_argument("--chunk-words", type=int, default=150, help="Words per chunk (default: 150)")
    parser.add_argument("--overlap-words", type=int, default=30, help="Overlap between chunks (default: 30)")
    parser.add_argument("--store", default=None, help="Mnemon store name (default: current store)")
    args = parser.parse_args()

    # Download if URL, otherwise resolve local path
    src = args.source.strip()
    if src.startswith("http://") or src.startswith("https://"):
        path = download_url(src)
        downloaded = True
    else:
        path = Path(src).expanduser().resolve()
        downloaded = False
        if not path.exists():
            sys.exit(f"File not found: {path}")

    print(f"Extracting text from {path.name}...")
    text = extract_text(path)
    text = re.sub(r"\n{3,}", "\n\n", text)

    chunks = chunk_text(text, args.chunk_words, args.overlap_words)
    source_tag = f"doc:{path.name}"
    print(f"Ingesting {len(chunks)} chunks (source={source_tag})...")

    ok = 0
    skipped = 0
    failed = 0
    for i, chunk in enumerate(chunks, 1):
        res = remember(chunk, source_tag, args.store)
        if not res["ok"]:
            failed += 1
            print(f"  [{i}/{len(chunks)}] FAILED: {res['err'][:80]}")
        else:
            try:
                action = json.loads(res["out"]).get("action", "stored")
            except Exception:
                action = "stored"
            skipped += action == "skipped"
            ok += action != "skipped"
            print(f"  [{i}/{len(chunks)}] {action}")

    print(f"\nIngest done: {ok} stored, {skipped} skipped (duplicates), {failed} failed")

    # Auto-embed all new chunks immediately
    print("Generating embeddings via Ollama (nomic-embed-text)...")
    result = subprocess.run([MNEMON, "embed", "--all"], capture_output=True, text=True)
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            print(f"Embeddings: {data}")
        except Exception:
            print(result.stdout.strip())
    else:
        print(f"Embed warning: {result.stderr.strip()}")

    # Clean up temp download
    if downloaded:
        path.unlink(missing_ok=True)

    print("Ready — use 'mnemon recall <query>' to search this document.")


if __name__ == "__main__":
    main()
