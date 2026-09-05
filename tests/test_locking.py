from pathlib import Path

import pytest

from providency.locking import InstanceAlreadyRunning, InstanceLock


def test_only_one_instance_can_hold_the_lock(tmp_path: Path) -> None:
    first = InstanceLock(tmp_path / "providency.lock")
    second = InstanceLock(tmp_path / "providency.lock")

    first.acquire()
    try:
        with pytest.raises(InstanceAlreadyRunning):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()
