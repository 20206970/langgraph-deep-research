"""Session management for multi-turn conversations"""

import time
from typing import Optional

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """Single chat message"""
    role: str  # "user" | "assistant"
    content: str
    message_type: str = "text"  # "text" | "research_report" | "task_plan"
    tasks: Optional[list] = None


class SessionState(BaseModel):
    """Session state with conversation history"""
    id: str
    messages: list[ChatMessage] = Field(default_factory=list)
    current_topic: Optional[str] = None
    last_tasks: Optional[list] = None
    last_report: Optional[str] = None
    created_at: str = ""


# In-memory session store: session_id -> SessionState
_sessions: dict[str, SessionState] = {}

# Per-session short-term memory instances: session_id -> ConversationSummaryBufferMemory
_session_memories: dict = {}


def create_session() -> SessionState:
    """Create a new session"""
    session_id = f"sess_{int(time.time() * 1000)}"
    session = SessionState(
        id=session_id,
        created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    _sessions[session_id] = session
    return session


def get_session(session_id: str) -> Optional[SessionState]:
    """Get session by ID"""
    return _sessions.get(session_id)


def add_message(session_id: str, msg: ChatMessage) -> None:
    """Add a message to session history"""
    session = _sessions.get(session_id)
    if session is None:
        return
    session.messages.append(msg)

    # Track latest research state
    if msg.role == "assistant":
        if msg.message_type == "research_report":
            session.last_report = msg.content
            session.current_topic = _infer_topic_from_messages(session.messages)
        elif msg.message_type == "task_plan" and msg.tasks:
            session.last_tasks = msg.tasks


def _infer_topic_from_messages(messages: list[ChatMessage]) -> str:
    """Get the topic from the most recent user message that triggered a research"""
    for msg in reversed(messages):
        if msg.role == "user" and msg.content:
            return msg.content
    return ""


def delete_session(session_id: str) -> bool:
    """Delete a session and its memory"""
    if session_id in _sessions:
        del _sessions[session_id]
    if session_id in _session_memories:
        del _session_memories[session_id]
    return True


def get_session_memory(session_id: str):
    """Get the short-term memory instance for a session"""
    return _session_memories.get(session_id)


def set_session_memory(session_id: str, memory) -> None:
    """Store a short-term memory instance for a session"""
    _session_memories[session_id] = memory
