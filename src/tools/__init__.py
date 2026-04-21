"""Tools for LangGraph Deep Research"""
from .search import search_web, search_papers
from .notes import create_note, read_note, update_note, delete_note, search_notes

__all__ = [
    "search_web",
    "search_papers",
    "create_note",
    "read_note",
    "update_note",
    "delete_note",
    "search_notes",
]