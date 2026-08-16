from __future__ import annotations

import pytest

from aether.adapters.echo.generator import NOT_IN_KNOWLEDGE_BASE_REPLY, EchoGenerator
from aether.domain.entities import Message
from aether.ports.chat import GenerationUsage, RetrievedContext, RetrievedContextChunk

pytestmark = pytest.mark.unit


async def _collect_text(
    generator: EchoGenerator,
    *,
    thread_history: list[Message],
    user_content: str,
    context: RetrievedContext | None = None,
) -> str:
    text = ""
    async for chunk in generator.generate(
        thread_history=thread_history, user_content=user_content, context=context
    ):
        if isinstance(chunk, str):
            text += chunk
    return text


async def test_no_context_echoes_user_content_unchanged() -> None:
    """Ordinary, ungrounded turn — unchanged pre-S6 behavior."""
    generator = EchoGenerator()

    text = await _collect_text(generator, thread_history=[], user_content="hello there")

    assert text == "hello there"


async def test_grounded_context_with_chunks_echoes_the_context_not_the_raw_user_content() -> None:
    """ADR-6.4's Gate 2 contract: answer only from context — proven
    here by the reply containing the context, not user_content."""
    generator = EchoGenerator()
    context = RetrievedContext(
        chunks=[
            RetrievedContextChunk(
                content="Acme's pricing starts at $10/mo.",
                document_title="pricing.md",
                section_path="Pricing",
            )
        ]
    )

    text = await _collect_text(
        generator,
        thread_history=[],
        user_content="what does it cost?",
        context=context,
    )

    assert "Acme's pricing starts at $10/mo." in text
    assert "pricing.md" in text
    assert "what does it cost?" not in text


async def test_grounded_context_with_no_chunks_gives_the_canned_refusal() -> None:
    """The specific case Gate 1 (issue #58's other half) short-circuits
    before generation in the real flow (issue #60) — but the generator
    itself must independently honor the protocol if ever handed an
    empty grounded context, not silently fall back to guessing."""
    generator = EchoGenerator()
    context = RetrievedContext(chunks=[])

    text = await _collect_text(
        generator, thread_history=[], user_content="what does it cost?", context=context
    )

    assert text == NOT_IN_KNOWLEDGE_BASE_REPLY


async def test_still_yields_a_final_generation_usage() -> None:
    generator = EchoGenerator()
    chunks = [
        chunk async for chunk in generator.generate(thread_history=[], user_content="hi there")
    ]

    assert isinstance(chunks[-1], GenerationUsage)
