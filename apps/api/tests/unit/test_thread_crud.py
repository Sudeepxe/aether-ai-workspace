from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from aether.app.threads.create_thread import CreateThread, CreateThreadCommand
from aether.app.threads.delete_thread import DeleteThread, DeleteThreadCommand
from aether.app.threads.get_thread import GetThread, GetThreadCommand
from aether.app.threads.list_threads import ListThreads, ListThreadsCommand
from aether.app.threads.update_thread import UpdateThread, UpdateThreadCommand
from aether.domain.errors import ThreadNotFoundError
from tests.unit.fakes.auth import FakeClock, FakeIdGenerator
from tests.unit.fakes.chat import FakeThreadRepository

pytestmark = pytest.mark.unit


async def test_create_and_get_thread() -> None:
    threads = FakeThreadRepository()
    ids = FakeIdGenerator()
    workspace_id, user_id = uuid4(), uuid4()

    created = await CreateThread(threads=threads, ids=ids).execute(
        CreateThreadCommand(workspace_id=workspace_id, created_by=user_id, title="First thread")
    )
    fetched = await GetThread(threads=threads).execute(
        GetThreadCommand(workspace_id=workspace_id, thread_id=created.id)
    )

    assert fetched.title == "First thread"
    assert fetched.created_by == user_id


async def test_get_thread_raises_not_found_for_unknown_id() -> None:
    threads = FakeThreadRepository()
    with pytest.raises(ThreadNotFoundError):
        await GetThread(threads=threads).execute(
            GetThreadCommand(workspace_id=uuid4(), thread_id=uuid4())
        )


async def test_get_thread_raises_not_found_across_workspaces() -> None:
    """A thread from workspace A must be invisible under workspace B's
    id — the app-layer equivalent of the RLS canary, proven without a
    database."""
    threads = FakeThreadRepository()
    ids = FakeIdGenerator()
    workspace_a, workspace_b = uuid4(), uuid4()
    created = await CreateThread(threads=threads, ids=ids).execute(
        CreateThreadCommand(workspace_id=workspace_a, created_by=uuid4(), title=None)
    )
    with pytest.raises(ThreadNotFoundError):
        await GetThread(threads=threads).execute(
            GetThreadCommand(workspace_id=workspace_b, thread_id=created.id)
        )


async def test_update_thread_title() -> None:
    threads = FakeThreadRepository()
    ids = FakeIdGenerator()
    workspace_id = uuid4()
    created = await CreateThread(threads=threads, ids=ids).execute(
        CreateThreadCommand(workspace_id=workspace_id, created_by=uuid4(), title="Old")
    )
    updated = await UpdateThread(threads=threads).execute(
        UpdateThreadCommand(workspace_id=workspace_id, thread_id=created.id, title="New")
    )
    assert updated.title == "New"


async def test_delete_thread_makes_it_invisible() -> None:
    threads = FakeThreadRepository()
    ids = FakeIdGenerator()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    workspace_id = uuid4()
    created = await CreateThread(threads=threads, ids=ids).execute(
        CreateThreadCommand(workspace_id=workspace_id, created_by=uuid4(), title=None)
    )
    await DeleteThread(threads=threads, clock=clock).execute(
        DeleteThreadCommand(workspace_id=workspace_id, thread_id=created.id)
    )
    with pytest.raises(ThreadNotFoundError):
        await GetThread(threads=threads).execute(
            GetThreadCommand(workspace_id=workspace_id, thread_id=created.id)
        )


async def test_delete_thread_is_idempotent_for_unknown_id() -> None:
    threads = FakeThreadRepository()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    await DeleteThread(threads=threads, clock=clock).execute(
        DeleteThreadCommand(workspace_id=uuid4(), thread_id=uuid4())
    )  # must not raise


async def test_list_threads_orders_newest_first_and_paginates() -> None:
    threads = FakeThreadRepository()
    ids = FakeIdGenerator()
    workspace_id = uuid4()
    create = CreateThread(threads=threads, ids=ids)
    for i in range(5):
        await create.execute(
            CreateThreadCommand(workspace_id=workspace_id, created_by=uuid4(), title=f"t{i}")
        )

    first_page = await ListThreads(threads=threads).execute(
        ListThreadsCommand(workspace_id=workspace_id, after=None, limit=3)
    )
    assert [t.title for t in first_page] == ["t4", "t3", "t2"]

    second_page = await ListThreads(threads=threads).execute(
        ListThreadsCommand(
            workspace_id=workspace_id,
            after=(first_page[-1].created_at, first_page[-1].id),
            limit=3,
        )
    )
    assert [t.title for t in second_page] == ["t1", "t0"]
