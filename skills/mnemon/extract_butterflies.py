#!/usr/bin/env python3
"""Extract butterfly species from a PDF and index into mnemon.

Each page yields one mnemon fact containing: scientific name, common name,
description, and image path — all in one chunk so a single recall returns
everything. Image path is embedded as [image: /path] for the nanobot to pick up.

Usage:
    python extract_butterflies.py <url_or_path> [options]

Options:
    --start-page N      First page to extract (1-based, default: 7)
    --end-page N        Last page to extract (1-based, default: 86)
    --output FILE       CSV output file for review (default: butterflies.csv)
    --append            Append to existing CSV instead of overwriting
    --images-dir DIR    Directory to save butterfly images (default: butterfly_images)
    --dpi N             Fallback render resolution if no embedded image (default: 150)
    --store NAME        Mnemon store name (default: butterflies)
    --no-index          Skip mnemon indexing (extract only)
"""
import argparse
import csv
import os
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


MNEMON = os.environ.get("MNEMON_BIN", "/root/go/bin/mnemon")
DEFAULT_STORE = "butterflies"


def gdrive_direct(url: str) -> str:
    m = re.search(r"/file/d/([^/]+)", url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return url


def download_pdf(url: str) -> Path:
    if "drive.google.com" in url:
        url = gdrive_direct(url)
    name = url.split("?")[0].rstrip("/").split("/")[-1] or "document"
    if "." not in name:
        name += ".pdf"
    dest = Path(tempfile.gettempdir()) / name
    print(f"Downloading: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, context=ctx) as resp, open(dest, "wb") as f:
        f.write(resp.read())
    print(f"Saved to {dest} ({dest.stat().st_size / 1024:.0f} KB)")
    return dest


def extract_page_text(reader, page_index: int) -> str:
    if page_index >= len(reader.pages):
        return ""
    return reader.pages[page_index].extract_text() or ""


def parse_page(text: str) -> dict:
    """Page layout:
        Line 1: species index number (e.g. "2")
        Line 2: scientific name with author
        Line 3: common name (optional, absent if next line starts with "Length")
        "Length of forewing: ..." line
        Description text
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Skip leading number-only lines
    idx = 0
    while idx < len(lines) and lines[idx].isdigit():
        idx += 1

    if idx >= len(lines):
        return {"scientific_name": None, "common_name": None, "description": None}

    # Scientific name — strip all parenthetical groups (subgenus + author)
    scientific_name = re.sub(r"\s*\([^)]+\)", "", lines[idx]).strip()
    idx += 1

    # Common name (optional)
    common_name = None
    if idx < len(lines) and not lines[idx].startswith("Length"):
        common_name = lines[idx]
        idx += 1

    # Skip the "Length of forewing: ..." measurement line
    if idx < len(lines) and lines[idx].startswith("Length"):
        idx += 1

    # Remaining lines = description
    description = " ".join(lines[idx:]).strip() or None

    return {"scientific_name": scientific_name, "common_name": common_name, "description": description}


def extract_page_image(pdf_path: Path, page_index: int, dest: Path, dpi: int) -> bool:
    """Extract the largest embedded image from a PDF page.
    Falls back to rendering the full page if no embedded image found.
    """
    try:
        import fitz
    except ImportError:
        sys.exit("pymupdf not installed — run: pip install pymupdf")
    doc = fitz.open(str(pdf_path))
    if page_index >= len(doc):
        doc.close()
        return False
    page = doc[page_index]
    images = page.get_images(full=True)
    if images:
        best = max(images, key=lambda img: img[2] * img[3])
        img_data = doc.extract_image(best[0])
        dest.write_bytes(img_data["image"])
        doc.close()
        return True
    # Fallback: render full page
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    pix.save(str(dest))
    doc.close()
    return True


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_")


def mnemon_remember(fact: str, entities: str, source: str, store: str) -> str:
    cmd = [MNEMON, "remember", fact, "--cat", "fact", "--imp", "3",
           "--entities", entities, "--source", source, "--store", store]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return f"FAILED: {result.stderr[:80]}"
    import json
    try:
        return json.loads(result.stdout).get("action", "stored")
    except Exception:
        return "stored"


def main():
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Extract butterfly species from PDF and index into mnemon.")
    parser.add_argument("source", help="URL or local path to PDF")
    parser.add_argument("--start-page", type=int, default=7)
    parser.add_argument("--end-page", type=int, default=86)
    parser.add_argument("--output", default="butterflies.csv")
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--images-dir", default="butterfly_images")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--no-index", action="store_true")
    args = parser.parse_args()

    src = args.source.strip()
    downloaded = False
    if src.startswith("http://") or src.startswith("https://"):
        pdf_path = download_pdf(src)
        downloaded = True
        source_tag = Path(pdf_path).stem
    else:
        pdf_path = Path(src).expanduser().resolve()
        if not pdf_path.exists():
            sys.exit(f"File not found: {pdf_path}")
        source_tag = pdf_path.stem

    images_dir = Path(args.images_dir).resolve()
    images_dir.mkdir(parents=True, exist_ok=True)

    try:
        from pypdf import PdfReader
    except ImportError:
        sys.exit("pypdf not installed — run: pip install pypdf")
    reader = PdfReader(str(pdf_path))

    # Ensure mnemon store exists
    if not args.no_index:
        subprocess.run([MNEMON, "store", "create", args.store],
                       capture_output=True, text=True)

    start_idx = args.start_page - 1
    end_idx = args.end_page - 1
    total = end_idx - start_idx + 1
    print(f"Extracting pages {args.start_page}–{args.end_page} ({total} pages) → store: {args.store}")

    results = []
    for i, page_idx in enumerate(range(start_idx, end_idx + 1), 1):
        page_num = page_idx + 1
        text = extract_page_text(reader, page_idx)
        record = parse_page(text)
        record["page"] = page_num

        sci = record["scientific_name"]
        common = record["common_name"] or ""

        # Save embedded image
        image_path = ""
        if sci:
            fname = safe_filename(sci) + ".png"
            dest = images_dir / fname
            if extract_page_image(pdf_path, page_idx, dest, args.dpi):
                image_path = str(dest)
        record["image_path"] = image_path

        label = (sci or "—") + (f" / {common}" if common else "")
        print(f"  [{i}/{total}] page {page_num}: {label} {'🖼' if image_path else '✗'}", end="")

        # Index a minimal image-mapping fact into mnemon
        # Just: species name + image path — kept short and unique to avoid dedup
        if not args.no_index and sci and image_path:
            fact = f"{sci}"
            if common:
                fact += f" ({common})"
            fact += f" [image: {image_path}] [page {page_num}]"

            entities = sci + (f",{common}" if common else "")
            action = mnemon_remember(fact, entities, source_tag, args.store)
            print(f" [{action}]")
        else:
            print()

        results.append(record)

    # Write CSV (for review/export)
    out_path = Path(args.output)
    mode = "a" if args.append and out_path.exists() else "w"
    with open(out_path, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["page", "scientific_name", "common_name", "image_path", "description"])
        if mode == "w":
            writer.writeheader()
        for r in results:
            writer.writerow({k: r.get(k) or "" for k in ["page", "scientific_name", "common_name", "image_path", "description"]})

    found = sum(1 for r in results if r.get("scientific_name"))
    print(f"\nDone: {found}/{total} species → {out_path}")

    if downloaded:
        pdf_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
