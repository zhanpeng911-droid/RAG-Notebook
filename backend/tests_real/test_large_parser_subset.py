"""Medium generated document parser/anchor subset, not end-to-end acceptance."""
import hashlib
import random
import time

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.services.source_anchor_service import build_anchor_candidates, hash_quote
from app.utils.config import chroma_config
from app.utils.file_handler import txt_loader_sync


def test_medium_text_parser_and_anchors(tmp_path):
    randomizer = random.Random(20261027)
    themes = ["storage consistency", "task recovery", "document parsing", "source evidence"]
    paragraphs = []
    while sum(map(len, paragraphs)) < 1_100_000:
        index = len(paragraphs)
        theme = randomizer.choice(themes)
        observations = [randomizer.randrange(1, 1_000_000) for _ in range(10)]
        paragraphs.append(
            f"Section {index}: {theme}. This synthetic technical record describes "
            f"a controlled experiment, its assumptions, and the measurements "
            f"needed to trace an observed result to its source. Trial values "
            f"{', '.join(map(str, observations))}; interpretation depends on the "
            f"scenario and does not represent measurements of a real system.\n\n"
        )
    payload = "".join(paragraphs)
    path = tmp_path / "generated-medium.txt"
    path.write_text(payload, encoding="utf-8")
    started = time.perf_counter()
    documents = txt_loader_sync(str(path))
    parsed = time.perf_counter()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chroma_config["chunk_by_extension"]["txt"]["chunk_size"],
        chunk_overlap=chroma_config["chunk_by_extension"]["txt"]["chunk_overlap"],
        separators=chroma_config["separators"],
    )
    chunks = splitter.split_documents(documents)
    split = time.perf_counter()
    anchors = build_anchor_candidates(documents, chunks)
    finished = time.perf_counter()
    assert len(documents) == 1 and documents[0].page_content == payload
    assert len(anchors) == len(chunks) and len(anchors) > 1000
    for anchor in anchors:
        assert anchor.page_no is None
        assert anchor.char_start is not None and anchor.char_end is not None
        assert payload[anchor.char_start:anchor.char_end] == anchor.quote_text
        assert anchor.quote_hash == hash_quote(anchor.quote_text)
    print(f"parser subset bytes={path.stat().st_size} sha256="
          f"{hashlib.sha256(path.read_bytes()).hexdigest()} paragraphs={len(paragraphs)} "
          f"chunks={len(chunks)} parse_s={parsed-started:.3f} "
          f"split_s={split-parsed:.3f} anchors_s={finished-split:.3f}")
