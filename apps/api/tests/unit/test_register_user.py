from __future__ import annotations

import pytest

from aether.app.auth.register_user import RegisterUser, RegisterUserCommand
from aether.domain.errors import EmailAlreadyRegisteredError
from tests.unit.fakes.auth import FakeIdGenerator, FakePasswordHasher, FakeUserRepository

pytestmark = pytest.mark.unit


def _use_case() -> tuple[RegisterUser, FakeUserRepository]:
    users = FakeUserRepository()
    return RegisterUser(users=users, hasher=FakePasswordHasher(), ids=FakeIdGenerator()), users


async def test_register_creates_user_with_hashed_password() -> None:
    use_case, users = _use_case()
    user = await use_case.execute(
        RegisterUserCommand(email="a@example.com", password="s3cret!", display_name="A")
    )
    assert user.email == "a@example.com"
    assert user.password_hash == "hashed:s3cret!"
    assert await users.get_by_email("a@example.com") == user


async def test_register_duplicate_email_raises() -> None:
    use_case, _ = _use_case()
    await use_case.execute(
        RegisterUserCommand(email="a@example.com", password="s3cret!", display_name="A")
    )
    with pytest.raises(EmailAlreadyRegisteredError):
        await use_case.execute(
            RegisterUserCommand(email="a@example.com", password="different", display_name="A2")
        )
