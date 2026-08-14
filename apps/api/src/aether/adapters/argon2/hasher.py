"""Argon2id password hasher (Blueprint §7.4/§7.5): memory 64MB, time cost 3,
a fresh random salt per hash (argon2-cffi's default salt_len=16 bytes) —
these are argon2-cffi's own library defaults, made explicit here so a
future library-default change can never silently weaken them.
"""

from __future__ import annotations

from argon2 import PasswordHasher as _Argon2PasswordHasher
from argon2 import exceptions as argon2_exceptions


class Argon2PasswordHasher:
    def __init__(self) -> None:
        self._impl = _Argon2PasswordHasher(
            time_cost=3,
            memory_cost=65_536,  # KiB = 64 MB
            parallelism=4,
            hash_len=32,
            salt_len=16,
        )

    def hash(self, password: str) -> str:
        return self._impl.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        try:
            self._impl.verify(password_hash, password)
        except argon2_exceptions.VerifyMismatchError:
            return False
        except (argon2_exceptions.VerificationError, argon2_exceptions.InvalidHashError):
            # Malformed/unrecognized hash — treat as a non-match rather
            # than raising, so callers have one failure path to reason
            # about (enumeration-safety at the use-case level relies on
            # this always resolving to True/False, never an exception).
            # InvalidHashError subclasses ValueError, not VerificationError,
            # so it needs its own arm (verified via direct exercise, not
            # assumed from the exception hierarchy).
            return False
        return True
