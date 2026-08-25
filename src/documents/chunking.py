"""Heading-aware parent/child chunking for private research documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import ceil
from typing import Sequence

from src.config import DocumentConfig
from src.state import new_id

from .conversion import BlockLocator, ConvertedDocument, ExtractedImage
from .models import DocumentChunk, DocumentImage, DocumentParent, VisionStatus


class DocumentChunkingError(RuntimeError):
    """A converted document did not contain usable text for retrieval."""


@dataclass(frozen=True)
class PreparedImage:
    """A stored image plus source hints used to attach it to its best physical parent."""

    image: DocumentImage
    locator: BlockLocator
    source_image: ExtractedImage | None = None


@dataclass(frozen=True)
class ChunkingResult:
    parents: tuple[DocumentParent, ...]
    chunks: tuple[DocumentChunk, ...]
    images: tuple[DocumentImage, ...]


@dataclass(frozen=True)
class _Fragment:
    text: str
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True)
class _Section:
    heading_path: tuple[str, ...]
    logical_path: tuple[str, ...]
    fragments: tuple[_Fragment, ...]


@dataclass(frozen=True)
class _ParentContext:
    parent: DocumentParent
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None


_PAGE_MARKER = re.compile(r"^\s*<!--\s*(?:page|page_number)\s*[:=]?\s*(\d+)\s*-->\s*$", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_TOKEN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?。！？])\s+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def _token_count(text: str) -> int:
    return len(_tokens(text))


def _page_bounds(fragments: Sequence[_Fragment]) -> tuple[int | None, int | None]:
    pages = [page for fragment in fragments for page in (fragment.page_start, fragment.page_end) if page is not None]
    return (min(pages), max(pages)) if pages else (None, None)


def _paragraphs(lines: Sequence[tuple[str, int | None]]) -> list[_Fragment]:
    """Keep Markdown tables and lists intact while grouping normal prose by blank lines."""

    paragraphs: list[_Fragment] = []
    current: list[str] = []
    pages: list[int] = []

    def flush() -> None:
        if current:
            text = "\n".join(current).strip()
            if text:
                paragraphs.append(_Fragment(text=text, page_start=min(pages) if pages else None, page_end=max(pages) if pages else None))
        current.clear()
        pages.clear()

    for line, page in lines:
        if not line.strip():
            flush()
            continue
        current.append(line)
        if page is not None:
            pages.append(page)
    flush()
    return paragraphs


def _parse_sections(markdown: str, title: str) -> list[_Section]:
    """Use H2 as the logical boundary, with H3+ retained as semantic split points."""

    title_path = (title,)
    stack: dict[int, str] = {1: title}
    sections: list[_Section] = []
    current_lines: list[tuple[str, int | None]] = []
    current_path: tuple[str, ...] = title_path
    current_logical_path: tuple[str, ...] = title_path
    page: int | None = None

    def flush() -> None:
        fragments = _paragraphs(current_lines)
        if fragments:
            sections.append(
                _Section(heading_path=current_path, logical_path=current_logical_path, fragments=tuple(fragments))
            )
        current_lines.clear()

    for raw_line in markdown.splitlines():
        marker = _PAGE_MARKER.match(raw_line)
        if marker:
            page = int(marker.group(1))
            continue
        heading = _HEADING.match(raw_line)
        if heading:
            level = len(heading.group(1))
            value = heading.group(2).strip()
            flush()
            for existing_level in tuple(stack):
                if existing_level >= level:
                    del stack[existing_level]
            stack[level] = value
            if level == 1:
                # The uploaded filename remains the stable root title. An H1 enriches the
                # visible heading path but cannot change a document's identity.
                current_path = title_path + (value,)
                current_logical_path = title_path
                # A title is source context, not a standalone retrieval unit. If there is
                # content before an H2 it will inherit this path on following normal lines.
                continue
            else:
                path = title_path + tuple(stack[item] for item in sorted(stack))
                current_path = path
                logical_values = tuple(stack[item] for item in sorted(stack) if item <= 2)
                current_logical_path = title_path + logical_values if logical_values else title_path
            current_lines.append((raw_line, page))
            continue
        current_lines.append((raw_line, page))
    flush()
    return sections


def _split_text_to_bound(text: str, max_tokens: int) -> list[str]:
    if _token_count(text) <= max_tokens:
        return [text]
    sentences = [sentence.strip() for sentence in _SENTENCE_BREAK.split(text) if sentence.strip()]
    if len(sentences) <= 1:
        words = _tokens(text)
        return [" ".join(words[index : index + max_tokens]) for index in range(0, len(words), max_tokens)]
    result: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for sentence in sentences:
        sentence_tokens = _token_count(sentence)
        if current and current_tokens + sentence_tokens > max_tokens:
            result.append(" ".join(current))
            current = []
            current_tokens = 0
        if sentence_tokens > max_tokens:
            result.extend(_split_text_to_bound(sentence, max_tokens))
        else:
            current.append(sentence)
            current_tokens += sentence_tokens
    if current:
        result.append(" ".join(current))
    return result


def _physical_fragments(section: _Section, target_tokens: int) -> list[list[_Fragment]]:
    atoms: list[_Fragment] = []
    for fragment in section.fragments:
        for part in _split_text_to_bound(fragment.text, target_tokens):
            atoms.append(_Fragment(part, fragment.page_start, fragment.page_end))
    groups: list[list[_Fragment]] = []
    current: list[_Fragment] = []
    current_tokens = 0
    for atom in atoms:
        atom_tokens = _token_count(atom.text)
        if current and current_tokens + atom_tokens > target_tokens:
            groups.append(current)
            current = []
            current_tokens = 0
        current.append(atom)
        current_tokens += atom_tokens
    if current:
        groups.append(current)
    return groups


def _child_fragments(parent_fragments: Sequence[_Fragment], *, target_tokens: int, overlap_ratio: float) -> list[_Fragment]:
    """Create semantically grouped children with an explicit 10--15% token overlap."""

    child_target = min(220, max(120, target_tokens // 3))
    source_atoms: list[_Fragment] = []
    for fragment in parent_fragments:
        for part in _split_text_to_bound(fragment.text, child_target):
            source_atoms.append(_Fragment(part, fragment.page_start, fragment.page_end))
    children: list[_Fragment] = []
    current: list[_Fragment] = []
    current_tokens = 0
    previous_text = ""

    def flush() -> None:
        nonlocal current, current_tokens, previous_text
        if not current:
            return
        page_start, page_end = _page_bounds(current)
        text = "\n\n".join(fragment.text for fragment in current).strip()
        if previous_text:
            overlap_count = max(1, ceil(_token_count(previous_text) * overlap_ratio))
            overlap = " ".join(_tokens(previous_text)[-overlap_count:])
            text = f"{overlap}\n\n{text}"
        children.append(_Fragment(text=text, page_start=page_start, page_end=page_end))
        previous_text = text
        current = []
        current_tokens = 0

    for atom in source_atoms:
        atom_tokens = _token_count(atom.text)
        if current and current_tokens + atom_tokens > child_target:
            flush()
        current.append(atom)
        current_tokens += atom_tokens
    flush()
    return children


def _locator(heading_path: Sequence[str], page_start: int | None, page_end: int | None) -> str:
    heading = " > ".join(heading_path)
    if page_start is None:
        return f"section: {heading}"
    pages = str(page_start) if page_start == page_end else f"{page_start}-{page_end}"
    return f"pages: {pages}; section: {heading}"


def _image_parent(image: PreparedImage, parents: Sequence[_ParentContext]) -> _ParentContext | None:
    best: tuple[int, _ParentContext] | None = None
    image_path = image.locator.heading_path
    image_page = image.locator.page_start
    for context in parents:
        score = 0
        if image_path and (
            tuple(context.heading_path[: len(image_path)]) == image_path
            or tuple(context.heading_path[-len(image_path) :]) == image_path
        ):
            score += 4
        if image_page is not None and context.page_start is not None and context.page_start <= image_page <= (context.page_end or context.page_start):
            score += 3
        if best is None or score > best[0]:
            best = (score, context)
    return best[1] if best and best[0] > 0 else (parents[0] if parents else None)


def _vision_text(image: DocumentImage) -> str | None:
    if image.vision_status != VisionStatus.SUCCEEDED:
        return None
    description = str(image.vision_metadata.get("description", "")).strip()
    if not description:
        return None
    parts = ["Visual enhancement (non-source): " + description]
    caption = image.caption or str(image.vision_metadata.get("caption_completion", "")).strip()
    if caption:
        parts.append("Figure caption context: " + caption)
    return "\n".join(parts)


def chunk_document(
    converted: ConvertedDocument,
    *,
    version_id: str,
    config: DocumentConfig,
    images: Sequence[PreparedImage] = (),
) -> ChunkingResult:
    """Build logical H2 parents, physical 400--600-token parents, and overlapping children."""

    sections = _parse_sections(converted.markdown, converted.title)
    if not sections:
        raise DocumentChunkingError("converted document has no textual content")

    parents: list[DocumentParent] = []
    chunks: list[DocumentChunk] = []
    contexts: list[_ParentContext] = []
    physical_index = 0
    for section in sections:
        for fragment_group in _physical_fragments(section, config.parent_target_tokens):
            parent_text = "\n\n".join(fragment.text for fragment in fragment_group).strip()
            if not parent_text:
                continue
            page_start, page_end = _page_bounds(fragment_group)
            parent = DocumentParent(
                parent_id=new_id("parent"),
                version_id=version_id,
                logical_heading_path=" > ".join(section.heading_path),
                physical_index=physical_index,
                text=parent_text,
                locator=_locator(section.heading_path, page_start, page_end),
            )
            parents.append(parent)
            context = _ParentContext(parent, section.heading_path, page_start, page_end)
            contexts.append(context)
            for child in _child_fragments(
                fragment_group,
                target_tokens=config.parent_target_tokens,
                overlap_ratio=config.child_overlap_ratio,
            ):
                chunks.append(
                    DocumentChunk(
                        chunk_id=new_id("chunk"),
                        parent_id=parent.parent_id,
                        kind="text",
                        text=child.text,
                        page_start=child.page_start,
                        page_end=child.page_end,
                    )
                )
            physical_index += 1
    if not parents or not chunks:
        raise DocumentChunkingError("converted document has no chunkable text")

    persisted_images: list[DocumentImage] = []
    for prepared in images:
        context = _image_parent(prepared, contexts)
        image = prepared.image.model_copy(update={"parent_id": context.parent.parent_id if context else None})
        persisted_images.append(image)
        visual_text = _vision_text(image)
        if visual_text and context:
            chunks.append(
                DocumentChunk(
                    chunk_id=new_id("chunk"),
                    parent_id=context.parent.parent_id,
                    kind="vision",
                    text=visual_text,
                    page_start=image.page,
                    page_end=image.page,
                )
            )
    return ChunkingResult(parents=tuple(parents), chunks=tuple(chunks), images=tuple(persisted_images))
