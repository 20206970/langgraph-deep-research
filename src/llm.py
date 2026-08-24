"""Custom ChatOpenAI with reasoning_content preservation for DeepSeek-style models.

DeepSeek v4 (and similar reasoning models) return `reasoning_content` in assistant
responses and REQUIRE it to be passed back verbatim on subsequent multi-turn calls.
LangChain's default ChatOpenAI strips this field in both directions, causing 400 errors
on retries and multi-turn conversations (including ReAct agent tool calling loops).

This module provides ChatOpenAIWithReasoning, which:
1. Preserves `reasoning_content` from API responses into AIMessage.additional_kwargs
2. Re-injects `reasoning_content` from additional_kwargs back into API request dicts
"""

from typing import Any

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI


class ChatOpenAIWithReasoning(ChatOpenAI):
    """ChatOpenAI subclass that preserves reasoning_content for thinking models."""

    def _create_chat_result(self, response, generation_info=None):
        chat_result = super()._create_chat_result(response, generation_info)

        # Extract reasoning_content from the raw response object.
        # We must use the raw object because model_dump() may exclude extra fields
        # that aren't part of the OpenAI SDK's Pydantic model definition.
        if hasattr(response, "choices") and chat_result.generations:
            for i, choice in enumerate(response.choices):
                if i >= len(chat_result.generations):
                    break
                if hasattr(choice, "message"):
                    rc = getattr(choice.message, "reasoning_content", None)
                    if rc:
                        chat_result.generations[i].message.additional_kwargs[
                            "reasoning_content"
                        ] = rc

        return chat_result

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        # Re-inject reasoning_content from AIMessage.additional_kwargs into the
        # API message dicts.  We correlate by index after re-converting input.
        if "messages" in payload:
            messages = self._convert_input(input_).to_messages()
            for i, msg_dict in enumerate(payload["messages"]):
                if msg_dict.get("role") != "assistant":
                    continue
                if i >= len(messages):
                    break
                msg = messages[i]
                if isinstance(msg, AIMessage):
                    rc = msg.additional_kwargs.get("reasoning_content")
                    if rc:
                        msg_dict["reasoning_content"] = rc

        return payload


def create_llm(**overrides) -> ChatOpenAIWithReasoning:
    """Create a ChatOpenAIWithReasoning instance from the current config.

    Args:
        **overrides: Optional keyword arguments to override config values
                     (e.g., temperature=0.5).

    Returns:
        ChatOpenAIWithReasoning instance configured from environment.
    """
    from src.config import get_config

    config = get_config()
    return ChatOpenAIWithReasoning(
        model=overrides.get("model", config.llm.model),
        base_url=overrides.get("base_url", config.llm.base_url),
        api_key=overrides.get("api_key", config.llm.api_key),
        temperature=overrides.get("temperature", config.llm.temperature),
    )
