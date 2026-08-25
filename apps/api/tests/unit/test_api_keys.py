from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from aether.app.api_keys.create_api_key import CreateApiKey, CreateApiKeyCommand
from aether.app.api_keys.list_api_keys import ListApiKeys, ListApiKeysCommand
from aether.app.api_keys.revoke_api_key import RevokeApiKey, RevokeApiKeyCommand
from aether.app.api_keys.verify_api_key import VerifyApiKey
from aether.app.auth.api_keys import generate_api_key, is_api_key
from aether.app.auth.tokens import hash_token
from aether.domain.entities import ApiKeyScope
from aether.domain.errors import InvalidApiKeyError
from tests.unit.fakes.auth import FakeClock, FakeIdGenerator
from tests.unit.fakes.workspaces import FakeApiKeyRepository, FakeAuditLog

pytestmark = pytest.mark.unit

WORKSPACE = UUID(int=1)
OTHER_WORKSPACE = UUID(int=2)
CREATOR = UUID(int=10)


def test_generate_api_key_has_expected_format_and_hash() -> None:
    generated = generate_api_key(env="dev")

    assert generated.raw_key.startswith("aeth_dev_")
    assert len(generated.prefix) == 8
    assert generated.raw_key[len("aeth_dev_") :].startswith(generated.prefix)
    assert generated.secret_hash == hash_token(generated.raw_key)
    # The hash must never equal the raw key itself (sanity check that
    # hashing actually happened, not a no-op passthrough).
    assert generated.secret_hash != generated.raw_key


def test_generate_api_key_is_unique_across_calls() -> None:
    a = generate_api_key(env="dev")
    b = generate_api_key(env="dev")

    assert a.raw_key != b.raw_key
    assert a.prefix != b.prefix


def test_is_api_key_distinguishes_from_a_jwt() -> None:
    assert is_api_key("aeth_dev_abc12345somesecret") is True
    assert is_api_key("eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiIxIn0.sig") is False
    assert is_api_key("") is False


async def test_create_api_key_persists_hash_never_raw_and_records_audit_event() -> None:
    api_keys = FakeApiKeyRepository()
    audit_log = FakeAuditLog()
    use_case = CreateApiKey(
        api_keys=api_keys, audit_log=audit_log, ids=FakeIdGenerator(), env="dev"
    )

    result = await use_case.execute(
        CreateApiKeyCommand(
            workspace_id=WORKSPACE,
            name="CI bot",
            scopes=frozenset({ApiKeyScope.CHAT_WRITE}),
            created_by=CREATOR,
            expires_at=None,
        )
    )

    assert result.raw_key.startswith("aeth_dev_")
    assert result.api_key.secret_hash != result.raw_key
    assert result.api_key.secret_hash == hash_token(result.raw_key)
    assert result.api_key.workspace_id == WORKSPACE
    assert result.api_key.scopes == frozenset({ApiKeyScope.CHAT_WRITE})
    assert audit_log.recorded[0].action == "api_key.created"
    assert audit_log.recorded[0].actor_user_id == CREATOR


async def test_list_api_keys_only_returns_the_requested_workspace() -> None:
    api_keys = FakeApiKeyRepository()
    create = CreateApiKey(
        api_keys=api_keys, audit_log=FakeAuditLog(), ids=FakeIdGenerator(), env="dev"
    )
    await create.execute(
        CreateApiKeyCommand(
            workspace_id=WORKSPACE,
            name="key-a",
            scopes=frozenset({ApiKeyScope.KB_READ}),
            created_by=CREATOR,
            expires_at=None,
        )
    )
    await create.execute(
        CreateApiKeyCommand(
            workspace_id=OTHER_WORKSPACE,
            name="key-b",
            scopes=frozenset({ApiKeyScope.KB_READ}),
            created_by=CREATOR,
            expires_at=None,
        )
    )
    use_case = ListApiKeys(api_keys=api_keys)

    result = await use_case.execute(ListApiKeysCommand(workspace_id=WORKSPACE))

    assert len(result) == 1
    assert result[0].name == "key-a"


async def test_revoke_api_key_sets_revoked_at_and_records_audit_event() -> None:
    api_keys = FakeApiKeyRepository()
    audit_log = FakeAuditLog()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    create_result = await CreateApiKey(
        api_keys=api_keys, audit_log=FakeAuditLog(), ids=FakeIdGenerator(), env="dev"
    ).execute(
        CreateApiKeyCommand(
            workspace_id=WORKSPACE,
            name="key-a",
            scopes=frozenset({ApiKeyScope.CHAT_WRITE}),
            created_by=CREATOR,
            expires_at=None,
        )
    )
    use_case = RevokeApiKey(
        api_keys=api_keys, audit_log=audit_log, clock=clock, ids=FakeIdGenerator()
    )

    await use_case.execute(
        RevokeApiKeyCommand(
            workspace_id=WORKSPACE, key_id=create_result.api_key.id, actor_user_id=CREATOR
        )
    )

    stored = await api_keys.get_by_prefix(create_result.api_key.prefix)
    assert stored is not None
    assert stored.revoked_at == clock.now()
    assert audit_log.recorded[0].action == "api_key.revoked"


async def test_verify_api_key_accepts_a_valid_unrevoked_unexpired_key() -> None:
    api_keys = FakeApiKeyRepository()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    create_result = await CreateApiKey(
        api_keys=api_keys, audit_log=FakeAuditLog(), ids=FakeIdGenerator(), env="dev"
    ).execute(
        CreateApiKeyCommand(
            workspace_id=WORKSPACE,
            name="key-a",
            scopes=frozenset({ApiKeyScope.CHAT_WRITE, ApiKeyScope.KB_READ}),
            created_by=CREATOR,
            expires_at=None,
        )
    )
    use_case = VerifyApiKey(api_keys=api_keys, clock=clock)

    principal = await use_case.execute(create_result.raw_key)

    assert principal.workspace_id == WORKSPACE
    assert principal.api_key_id == create_result.api_key.id
    assert principal.scopes == frozenset({ApiKeyScope.CHAT_WRITE, ApiKeyScope.KB_READ})
    # touch_last_used must have run.
    stored = await api_keys.get_by_prefix(create_result.api_key.prefix)
    assert stored is not None
    assert stored.last_used_at == clock.now()


async def test_verify_api_key_rejects_malformed_credential() -> None:
    use_case = VerifyApiKey(
        api_keys=FakeApiKeyRepository(), clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    )
    with pytest.raises(InvalidApiKeyError):
        await use_case.execute("not-an-api-key")


async def test_verify_api_key_rejects_unknown_prefix() -> None:
    use_case = VerifyApiKey(
        api_keys=FakeApiKeyRepository(), clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    )
    with pytest.raises(InvalidApiKeyError):
        await use_case.execute("aeth_dev_neverissued00" + "x" * 32)


async def test_verify_api_key_rejects_a_tampered_secret() -> None:
    api_keys = FakeApiKeyRepository()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    create_result = await CreateApiKey(
        api_keys=api_keys, audit_log=FakeAuditLog(), ids=FakeIdGenerator(), env="dev"
    ).execute(
        CreateApiKeyCommand(
            workspace_id=WORKSPACE,
            name="key-a",
            scopes=frozenset({ApiKeyScope.CHAT_WRITE}),
            created_by=CREATOR,
            expires_at=None,
        )
    )
    tampered = create_result.raw_key[:-1] + ("a" if create_result.raw_key[-1] != "a" else "b")
    use_case = VerifyApiKey(api_keys=api_keys, clock=clock)

    with pytest.raises(InvalidApiKeyError):
        await use_case.execute(tampered)


async def test_verify_api_key_rejects_a_revoked_key() -> None:
    api_keys = FakeApiKeyRepository()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    create_result = await CreateApiKey(
        api_keys=api_keys, audit_log=FakeAuditLog(), ids=FakeIdGenerator(), env="dev"
    ).execute(
        CreateApiKeyCommand(
            workspace_id=WORKSPACE,
            name="key-a",
            scopes=frozenset({ApiKeyScope.CHAT_WRITE}),
            created_by=CREATOR,
            expires_at=None,
        )
    )
    await api_keys.revoke(WORKSPACE, create_result.api_key.id, revoked_at=clock.now())
    use_case = VerifyApiKey(api_keys=api_keys, clock=clock)

    with pytest.raises(InvalidApiKeyError):
        await use_case.execute(create_result.raw_key)


async def test_verify_api_key_rejects_an_expired_key() -> None:
    api_keys = FakeApiKeyRepository()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    create_result = await CreateApiKey(
        api_keys=api_keys, audit_log=FakeAuditLog(), ids=FakeIdGenerator(), env="dev"
    ).execute(
        CreateApiKeyCommand(
            workspace_id=WORKSPACE,
            name="key-a",
            scopes=frozenset({ApiKeyScope.CHAT_WRITE}),
            created_by=CREATOR,
            expires_at=clock.now() + timedelta(seconds=1),
        )
    )
    clock.advance(timedelta(seconds=2))
    use_case = VerifyApiKey(api_keys=api_keys, clock=clock)

    with pytest.raises(InvalidApiKeyError):
        await use_case.execute(create_result.raw_key)
