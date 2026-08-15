from __future__ import annotations

from uuid import uuid4

import pytest

from aether.app.chat.cancel_generation import CancelGeneration, CancelGenerationCommand
from aether.app.chat.get_generation_status import GetGenerationStatus, GetGenerationStatusCommand
from aether.domain.errors import GenerationNotFoundError
from aether.ports.streaming import BufferedEvent
from tests.unit.fakes.chat import FakeCancellation, FakeStreamBuffer

pytestmark = pytest.mark.unit


async def test_cancel_generation_publishes_on_the_workspace_scoped_channel() -> None:
    cancellation = FakeCancellation()
    workspace_id, generation_id = uuid4(), uuid4()

    await CancelGeneration(cancellation=cancellation).execute(
        CancelGenerationCommand(workspace_id=workspace_id, generation_id=generation_id)
    )

    assert cancellation.published == [(workspace_id, generation_id)]


async def test_get_generation_status_raises_not_found_for_empty_buffer() -> None:
    buffer = FakeStreamBuffer()
    with pytest.raises(GenerationNotFoundError):
        await GetGenerationStatus(buffer=buffer).execute(
            GetGenerationStatusCommand(workspace_id=uuid4(), generation_id=uuid4())
        )


async def test_get_generation_status_reports_the_latest_buffered_event() -> None:
    buffer = FakeStreamBuffer()
    workspace_id, generation_id = uuid4(), uuid4()
    await buffer.append(
        workspace_id, generation_id, BufferedEvent(seq=0, event_type="meta", data="{}")
    )
    await buffer.append(
        workspace_id, generation_id, BufferedEvent(seq=1, event_type="token", data='{"delta":"a"}')
    )

    view = await GetGenerationStatus(buffer=buffer).execute(
        GetGenerationStatusCommand(workspace_id=workspace_id, generation_id=generation_id)
    )

    assert view.last_seq == 1
    assert view.latest_event_type == "token"
    assert view.is_done is False


async def test_get_generation_status_is_scoped_by_workspace() -> None:
    """A generation buffered under workspace A must be invisible to
    workspace B's status lookup — the same tenant-scoping the buffer key
    itself enforces (ports.streaming.StreamBufferPort's docstring)."""
    buffer = FakeStreamBuffer()
    workspace_a, workspace_b, generation_id = uuid4(), uuid4(), uuid4()
    await buffer.append(
        workspace_a, generation_id, BufferedEvent(seq=0, event_type="meta", data="{}")
    )

    with pytest.raises(GenerationNotFoundError):
        await GetGenerationStatus(buffer=buffer).execute(
            GetGenerationStatusCommand(workspace_id=workspace_b, generation_id=generation_id)
        )
