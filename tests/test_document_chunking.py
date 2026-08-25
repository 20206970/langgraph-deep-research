import re
from math import ceil

from src.config import DocumentConfig
from src.documents.chunking import PreparedImage, chunk_document
from src.documents.conversion import BlockLocator, ConvertedDocument
from src.documents.models import DocumentImage, VisionStatus


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_]+|[^\s]", text)


def test_chunking_preserves_heading_paths_pages_tables_and_child_overlap():
    long_paragraphs = "\n\n".join(" ".join(f"token_{index}_{offset}" for offset in range(110)) for index in range(6))
    converted = ConvertedDocument(
        markdown=(
            "# Paper Title\n\n<!-- page: 1 -->\n## Methods\n\n"
            "| Metric | Score |\n| --- | --- |\n| F1 | 0.91 |\n\n"
            f"{long_paragraphs}\n\n<!-- page: 2 -->\n### Training\n\nShort training details.\n"
        ),
        title="uploaded-paper",
        converter_fingerprint="test",
    )

    result = chunk_document(converted, version_id="ver_1", config=DocumentConfig(parent_target_tokens=500, child_overlap_ratio=0.12))
    text_chunks = [chunk for chunk in result.chunks if chunk.kind == "text"]

    assert len(result.parents) >= 3
    assert all(parent.logical_heading_path.startswith("uploaded-paper > Paper Title") for parent in result.parents)
    assert any("Methods" in parent.logical_heading_path for parent in result.parents)
    assert any(chunk.page_start == 1 for chunk in text_chunks)
    assert any(chunk.page_end == 2 for chunk in text_chunks)
    assert any("| Metric | Score |" in chunk.text for chunk in text_chunks)

    first_parent = result.parents[0]
    siblings = [chunk for chunk in text_chunks if chunk.parent_id == first_parent.parent_id]
    assert len(siblings) >= 2
    overlap_size = max(1, ceil(len(_tokens(siblings[0].text)) * 0.12))
    assert _tokens(siblings[1].text)[:overlap_size] == _tokens(siblings[0].text)[-overlap_size:]


def test_visual_chunks_are_explicitly_marked_non_source_and_attached_to_parent():
    converted = ConvertedDocument(
        markdown="# Paper\n\n<!-- page: 3 -->\n## Results\n\nTextual result discussion.",
        title="paper",
        converter_fingerprint="test",
    )
    image = DocumentImage(
        image_id="img_1",
        version_id="ver_1",
        page=3,
        path="owner/doc/ver/images/img_1.png",
        caption="Figure 2",
        vision_status=VisionStatus.SUCCEEDED,
        vision_metadata={"description": "A bar chart compares three methods."},
    )

    result = chunk_document(
        converted,
        version_id="ver_1",
        config=DocumentConfig(),
        images=[PreparedImage(image=image, locator=BlockLocator(heading_path=("Paper", "Results"), page_start=3, page_end=3))],
    )

    visual = next(chunk for chunk in result.chunks if chunk.kind == "vision")
    persisted_image = result.images[0]
    assert visual.text.startswith("Visual enhancement (non-source):")
    assert persisted_image.parent_id == visual.parent_id
    assert visual.page_start == 3
