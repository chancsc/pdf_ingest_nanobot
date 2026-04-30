#!/usr/bin/env python3
"""Extract butterfly species names and images from PDF pages.

Iterates pages 7–86, extracts text (scientific + common name) and renders
each page as a PNG image saved to an images directory.

Usage:
    python extract_butterflies.py <url_or_path> [options]

Options:
    --start-page N      First page to extract (1-based, default: 7)
    --end-page N        Last page to extract (1-based, default: 86)
    --output FILE       CSV output file (default: butterflies.csv)
    --images-dir DIR    Directory to save butterfly images (default: butterfly_images)
    --dpi N             Image render resolution (default: 150)
    --store NAME        Mnemon store to index into (optional)
    --index             Index results into mnemon after extraction
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


def parse_names(text: str) -> dict:
    """Page layout:
        Line 1: species index number (e.g. "2")
        Line 2: scientific name with author, e.g. "Graphium procles (Grose-Smith)"
        Line 3: common name (optional) — absent when line starts with "Length"
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Skip leading number-only lines
    idx = 0
    while idx < len(lines) and lines[idx].isdigit():
        idx += 1

    if idx >= len(lines):
        return {"scientific_name": None, "common_name": None}

    # Strip all parenthetical groups (subgenus + author)
    raw_sci = lines[idx]
    scientific_name = re.sub(r"\s*\([^)]+\)", "", raw_sci).strip()

    common_name = None
    if idx + 1 < len(lines) and not lines[idx + 1].startswith("Length"):
        common_name = lines[idx + 1]

    return {"scientific_name": scientific_name, "common_name": common_name}


def render_page_image(pdf_path: Path, page_index: int, dest: Path, dpi: int) -> bool:
    """Render a PDF page to PNG using pymupdf. Returns True on success."""
    try:
        import fitz
    except ImportError:
        sys.exit("pymupdf not installed — run: pip install pymupdf")
    doc = fitz.open(str(pdf_path))
    if page_index >= len(doc):
        doc.close()
        return False
    page = doc[page_index]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    pix.save(str(dest))
    doc.close()
    return True


def safe_filename(scientific_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", scientific_name).strip("_")


def mnemon_index(record: dict, source: str, store: str | None) -> None:
    sci = record.get("scientific_name") or ""
    common = record.get("common_name") or ""
    image_path = record.get("image_path") or ""
    page = record.get("page", "?")
    if not sci:
        return

    fact = f"Butterfly species: scientific name '{sci}'"
    if common:
        fact += f", common name '{common}'"
    if image_path:
        fact += f", image: {image_path}"
    fact += f" (page {page})"

    cmd = [MNEMON, "remember", fact, "--cat", "fact", "--imp", "3",
           "--entities", sci, "--source", source]
    if store:
        cmd += ["--store", store]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [mnemon] failed: {result.stderr[:80]}")


def main():
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Extract butterfly species names and images from PDF.")
    parser.add_argument("source", help="URL or local path to PDF")
    parser.add_argument("--start-page", type=int, default=7)
    parser.add_argument("--end-page", type=int, default=86)
    parser.add_argument("--output", default="butterflies.csv")
    parser.add_argument("--images-dir", default="butterfly_images")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--store", default=None)
    parser.add_argument("--index", action="store_true")
    args = parser.parse_args()

    src = args.source.strip()
    downloaded = False
    if src.startswith("http://") or src.startswith("https://"):
        pdf_path = download_pdf(src)
        downloaded = True
        source_tag = "butterfly_pdf"
    else:
        pdf_path = Path(src).expanduser().resolve()
        if not pdf_path.exists():
            sys.exit(f"File not found: {pdf_path}")
        source_tag = pdf_path.name

    images_dir = Path(args.images_dir).resolve()
    images_dir.mkdir(parents=True, exist_ok=True)

    try:
        from pypdf import PdfReader
    except ImportError:
        sys.exit("pypdf not installed — run: pip install pypdf")
    reader = PdfReader(str(pdf_path))

    start_idx = args.start_page - 1
    end_idx = args.end_page - 1
    total = end_idx - start_idx + 1
    print(f"Extracting pages {args.start_page}–{args.end_page} ({total} pages)")
    print(f"Saving images to: {images_dir}")

    results = []
    for i, page_idx in enumerate(range(start_idx, end_idx + 1), 1):
        page_num = page_idx + 1
        text = extract_page_text(reader, page_idx)
        record = parse_names(text)
        record["page"] = page_num

        sci = record["scientific_name"]
        common = record["common_name"] or ""

        # Save page image
        image_path = ""
        if sci:
            fname = safe_filename(sci) + ".png"
            dest = images_dir / fname
            if render_page_image(pdf_path, page_idx, dest, args.dpi):
                image_path = str(dest)
        record["image_path"] = image_path

        label = (sci or "—") + (f" / {common}" if common else "")
        img_ok = "🖼" if image_path else "✗"
        print(f"  [{i}/{total}] page {page_num}: {label} {img_ok}")

        results.append(record)
        if args.index and sci:
            mnemon_index(record, source_tag, args.store)

    # Write CSV
    out_path = Path(args.output)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["page", "scientific_name", "common_name", "image_path"])
        writer.writeheader()
        for r in results:
            writer.writerow({
                "page": r["page"],
                "scientific_name": r.get("scientific_name") or "",
                "common_name": r.get("common_name") or "",
                "image_path": r.get("image_path") or "",
            })

    found = sum(1 for r in results if r.get("scientific_name"))
    images = sum(1 for r in results if r.get("image_path"))
    print(f"\nDone: {found}/{total} species, {images} images saved → {out_path}")

    if downloaded:
        pdf_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
