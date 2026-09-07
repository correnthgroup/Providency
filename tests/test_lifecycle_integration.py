"""Real local processes, isolated data, no Vector login or external messages."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from providency.locking import InstanceLock


def free_ports() -> tuple[int, int]:
    with socket.socket() as first, socket.socket() as second:
        first.bind(('127.0.0.1', 0))
        second.bind(('127.0.0.1', 0))
        return first.getsockname()[1], second.getsockname()[1]


def available(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=0.5) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def wait_for(url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        assert process.poll() is None, 'Launcher exited before becoming ready'
        if available(url):
            return
        time.sleep(0.2)
    raise AssertionError(f'Local service did not start: {url}')


def test_real_launcher_shutdown_releases_both_ports_and_instance_lock(tmp_path: Path) -> None:
    api_port, ui_port = free_ports()
    api_url = f'http://127.0.0.1:{api_port}'
    ui_url = f'http://127.0.0.1:{ui_port}/_stcore/health'
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith('PROVIDENCY_')
    }
    environment.update({
        'PROVIDENCY_DATA_DIR': str(tmp_path),
        'PROVIDENCY_API_PORT': str(api_port),
        'PROVIDENCY_UI_PORT': str(ui_port),
        'PROVIDENCY_EXECUTION_MODE': 'DRY_RUN',
    })
    command = [sys.executable, '-c', (
        'import providency.runtime as r; '
        'r.webbrowser.open = lambda url: True; r.run_launcher()'
    )]
    # Repeat against the same data and ports to prove the second launch is independent.
    for _ in range(2):
        log = (tmp_path / 'lifecycle.log').open('wb')
        process = subprocess.Popen(
            command, env=environment, cwd=Path(__file__).parents[1],
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            wait_for(f'{api_url}/health', process)
            wait_for(ui_url, process)
            request = urllib.request.Request(f'{api_url}/run', method='POST')
            with urllib.request.urlopen(request, timeout=5) as response:
                assert json.load(response)['status'] == 'RUNNING'
            request = urllib.request.Request(
                f'{api_url}/shutdown', method='POST', data=b'{"confirm": true}',
                headers={'Content-Type': 'application/json'},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 202
            assert process.wait(timeout=25) == 0
            assert not available(f'{api_url}/health')
            assert not available(ui_url)
            with InstanceLock(tmp_path / 'providency.lock'):
                pass
        except Exception as exc:
            log.flush()
            exc.add_note((tmp_path / 'lifecycle.log').read_text(encoding='utf-8', errors='replace'))
            raise
        finally:
            if process.poll() is None:
                request = urllib.request.Request(
                    f'{api_url}/shutdown', method='POST', data=b'{"confirm": true}',
                    headers={'Content-Type': 'application/json'},
                )
                try:
                    urllib.request.urlopen(request, timeout=5).close()
                    process.wait(timeout=25)
                except (OSError, subprocess.TimeoutExpired):
                    process.terminate()
                    process.wait(timeout=5)
            log.close()
