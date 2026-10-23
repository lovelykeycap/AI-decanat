from __future__ import annotations

import secrets
import threading
import time
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from decanat.config import write_private


class AdminAuth:
    def __init__(self, root: Path, clock=time.monotonic):
        self.path = root / "admin.hash"
        self.hasher = PasswordHasher()
        self.clock = clock
        self.tokens: dict[str, float] = {}
        self.failures: list[float] = []
        self._lock = threading.RLock()

    @property
    def configured(self) -> bool:
        return self.path.exists()

    def set_password(self, password: str) -> None:
        if len(password) < 10 or len(password) > 256:
            raise ValueError("Пароль должен содержать от 10 до 256 символов")
        with self._lock:
            write_private(self.path, self.hasher.hash(password))
            self.tokens.clear()

    def login(self, password: str) -> str | None:
        with self._lock:
            return self._login(password)

    def _login(self, password: str) -> str | None:
        now = self.clock()
        self.failures = [t for t in self.failures if t > now - 300]
        if len(self.failures) >= 5:
            raise ValueError("Слишком много попыток. Повторите через пять минут")
        if not self.configured:
            return None
        try:
            valid = self.hasher.verify(self.path.read_text(), password)
        except VerificationError:
            valid = False
        if not valid:
            self.failures.append(now)
            return None
        self.failures.clear()
        token = secrets.token_urlsafe(32)
        self.tokens[token] = now + 1800
        return token

    def check(self, token: str) -> bool:
        with self._lock:
            now = self.clock()
            self.tokens = {key: expiry for key, expiry in self.tokens.items() if expiry > now}
            return token in self.tokens

    def logout(self, token: str) -> None:
        with self._lock:
            self.tokens.pop(token, None)
