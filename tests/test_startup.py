from pathlib import Path

from providency.startup import StartupProgress


def test_startup_progress_is_based_on_explicit_process_checkpoints(tmp_path: Path) -> None:
    progress = StartupProgress(tmp_path)
    progress.begin('launch-one')
    assert progress.read()['stage'] == 'STARTING_UI'
    progress.advance('UI_READY')
    assert progress.read()['progress'] < 1
    progress.advance('STARTING_ENGINE')
    assert progress.read()['stage'] == 'STARTING_ENGINE'
    assert progress.read()['progress'] < 1
    progress.advance('READY')
    assert progress.read()['progress'] == 1


def test_cancel_request_cannot_cancel_a_later_launch(tmp_path: Path) -> None:
    progress = StartupProgress(tmp_path)
    progress.begin('old')
    progress.cancel('old')
    assert progress.cancelled()
    progress.begin('new')
    assert not progress.cancelled()
    progress.cancel('old')
    assert not progress.cancelled()


def test_startup_failure_keeps_last_progress_and_a_human_message(tmp_path: Path) -> None:
    progress = StartupProgress(tmp_path)
    progress.begin('test')
    progress.advance('STARTING_ENGINE')
    before = progress.read()['progress']
    progress.fail('Não foi possível iniciar o motor local.')
    status = progress.read()
    assert status['stage'] == 'ERROR'
    assert status['progress'] == before
    assert status['message'] == 'Não foi possível iniciar o motor local.'
