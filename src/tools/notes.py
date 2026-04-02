"""Note tools for persistent storage"""

import json
import os
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool


NOTES_DIR = Path("./notes")


def _ensure_notes_dir():
    """确保笔记目录存在"""
    NOTES_DIR.mkdir(parents=True, exist_ok=True)


@tool
def create_note(title: str, content: str, note_type: str = "general", tags: list[str] = None) -> str:
    """
    创建新笔记。

    Args:
        title: 笔记标题
        content: 笔记内容
        note_type: 笔记类型 (general/task/conclusion)，默认 general
        tags: 标签列表

    Returns:
        创建结果，包含笔记ID
    """
    _ensure_notes_dir()

    import time
    note_id = f"note_{int(time.time() * 1000)}"

    note_data = {
        "id": note_id,
        "title": title,
        "content": content,
        "type": note_type,
        "tags": tags or [],
    }

    file_path = NOTES_DIR / f"{note_id}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(note_data, f, ensure_ascii=False, indent=2)

    return f"笔记创建成功\nID: {note_id}\n标题: {title}\n类型: {note_type}"


@tool
def read_note(note_id: str) -> str:
    """
    读取笔记内容。

    Args:
        note_id: 笔记ID

    Returns:
        笔记内容
    """
    file_path = NOTES_DIR / f"{note_id}.json"

    if not file_path.exists():
        return f"笔记不存在: {note_id}"

    with open(file_path, "r", encoding="utf-8") as f:
        note_data = json.load(f)

    return f"标题: {note_data['title']}\n类型: {note_data['type']}\n标签: {', '.join(note_data.get('tags', []))}\n\n内容:\n{note_data['content']}"


@tool
def update_note(note_id: str, title: str = None, content: str = None, tags: list[str] = None) -> str:
    """
    更新笔记内容。

    Args:
        note_id: 笔记ID
        title: 新标题（可选）
        content: 新内容（可选）
        tags: 新标签（可选）

    Returns:
        更新结果
    """
    file_path = NOTES_DIR / f"{note_id}.json"

    if not file_path.exists():
        return f"笔记不存在: {note_id}"

    with open(file_path, "r", encoding="utf-8") as f:
        note_data = json.load(f)

    if title is not None:
        note_data["title"] = title
    if content is not None:
        note_data["content"] = content
    if tags is not None:
        note_data["tags"] = tags

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(note_data, f, ensure_ascii=False, indent=2)

    return f"笔记更新成功\nID: {note_id}"


@tool
def delete_note(note_id: str) -> str:
    """
    删除笔记。

    Args:
        note_id: 笔记ID

    Returns:
        删除结果
    """
    file_path = NOTES_DIR / f"{note_id}.json"

    if not file_path.exists():
        return f"笔记不存在: {note_id}"

    file_path.unlink()
    return f"笔记删除成功\nID: {note_id}"


@tool
def search_notes(query: str, limit: int = 5) -> str:
    """
    搜索笔记。

    Args:
        query: 搜索关键词
        limit: 返回数量限制

    Returns:
        匹配的笔记列表
    """
    _ensure_notes_dir()

    results = []
    for file_path in NOTES_DIR.glob("*.json"):
        with open(file_path, "r", encoding="utf-8") as f:
            note_data = json.load(f)

        # 简单文本匹配
        text = f"{note_data.get('title', '')} {note_data.get('content', '')}".lower()
        if query.lower() in text:
            results.append({
                "id": note_data["id"],
                "title": note_data["title"],
                "type": note_data.get("type", "general"),
            })

    if not results:
        return f"未找到匹配 '{query}' 的笔记"

    output = [f"找到 {len(results)} 条笔记:\n"]
    for note in results[:limit]:
        output.append(f"- {note['title']} (ID: {note['id']}, 类型: {note['type']})")

    return "\n".join(output)