"""Generate synthetic document inputs; never reads or overwrites user files."""

import argparse
import hashlib
import json
import random
import zipfile
from io import BytesIO
from pathlib import Path

import fitz
from docx import Document

SEED = 20261026
TOPICS = ("retrieval", "indexing", "evidence", "leases", "recovery", "storage")


def paragraph(rng: random.Random, number: int) -> str:
    topic = rng.choice(TOPICS)
    measurement = rng.randrange(100, 10000)
    return (f"Section {number}: The {topic} process records observation {measurement}. "
            f"A reviewer checks the source, compares the result, and records the outcome. "
            f"For this example, the {topic} step is evaluated with independent evidence.\n\n")


def generate(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=False)
    rng = random.Random(SEED)
    entries = []
    for name, target in (("small.md", 20_000), ("medium.txt", 1_000_000),
                         ("large.txt", 5_000_000)):
        path = root / name
        size = count = 0
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            while size < target:
                text = paragraph(rng, count)
                stream.write(text)
                size += len(text.encode("utf-8"))
                count += 1
        entries.append({"name": name, "paragraphs": count})
    for name, pages in (("small.pdf", 8), ("medium.pdf", 100), ("large.pdf", 300)):
        pdf = fitz.open()
        for index in range(pages):
            page = pdf.new_page()
            text = f"Document page {index + 1}\n\n" + "".join(
                paragraph(rng, index * 12 + n) for n in range(12))
            page.insert_textbox(fitz.Rect(50, 50, 550, 790), text, fontsize=9)
        pdf.save(root / name, no_new_id=True)
        pdf.close()
        entries.append({"name": name, "pages": pages})
    document = Document()
    for index in range(30):
        document.add_heading(f"Chapter {index + 1}", level=1)
        for offset in range(10):
            document.add_paragraph(paragraph(rng, index * 10 + offset).strip())
    # python-docx writes ZIP members with wall-clock timestamps. Normalize them
    # so independent runs with the same dependencies produce identical bytes.
    archive = BytesIO()
    document.save(archive)
    archive.seek(0)
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(
            root / "headings.docx", "w", compression=zipfile.ZIP_DEFLATED) as target:
        for member in source.infolist():
            info = zipfile.ZipInfo(member.filename, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = member.external_attr
            target.writestr(info, source.read(member.filename))
    entries.append({"name": "headings.docx", "chapters": 30, "paragraphs": 300})
    for entry in entries:
        data = (root / entry["name"]).read_bytes()
        entry.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    (root / "manifest.json").write_text(json.dumps({
        "generator_version": 1, "seed": SEED, "files": entries,
        "note": "Synthetic inputs; hashes describe this generated run.",
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new directory that does not already exist")
    generate(parser.parse_args().output)
