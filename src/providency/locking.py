from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import BinaryIO, cast


class InstanceAlreadyRunning(RuntimeError):
    pass


def _module_attribute(module: ModuleType, name: str) -> object:
    return getattr(module, name)


def _lock_handle(handle: BinaryIO) -> None:
    if os.name == "nt":
        module = importlib.import_module("msvcrt")
        win_lock = cast(Callable[[int, int, int], None], _module_attribute(module, "locking"))
        mode = cast(int, _module_attribute(module, "LK_NBLCK"))
        win_lock(handle.fileno(), mode, 1)
        return
    module = importlib.import_module("fcntl")
    unix_lock = cast(Callable[[int, int], None], _module_attribute(module, "flock"))
    exclusive = cast(int, _module_attribute(module, "LOCK_EX"))
    nonblocking = cast(int, _module_attribute(module, "LOCK_NB"))
    unix_lock(
        handle.fileno(),
        exclusive | nonblocking,
    )


def _unlock_handle(handle: BinaryIO) -> None:
    if os.name == "nt":
        module = importlib.import_module("msvcrt")
        win_unlock = cast(
            Callable[[int, int, int], None], _module_attribute(module, "locking")
        )
        mode = cast(int, _module_attribute(module, "LK_UNLCK"))
        win_unlock(handle.fileno(), mode, 1)
        return
    module = importlib.import_module("fcntl")
    unix_unlock = cast(Callable[[int, int], None], _module_attribute(module, "flock"))
    mode = cast(int, _module_attribute(module, "LOCK_UN"))
    unix_unlock(handle.fileno(), mode)


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            _lock_handle(handle)
        except OSError as exc:
            handle.close()
            raise InstanceAlreadyRunning("Providency is already running.") from exc
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        self._handle.seek(0)
        _unlock_handle(self._handle)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
