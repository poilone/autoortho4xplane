"""The update-check task says "Update available" only for a newer release."""

import os
import sys
import types
from types import SimpleNamespace

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from ui import main_window  # noqa: E402

RELEASES = "https://github.com/ProgrammingDinosaur/autoortho4xplane/releases"


class Recorder:
    def __init__(self):
        self.calls = []

    def complete_task(self, task_id, stage=None):
        self.calls.append(stage)

    def set_update_available(self, version):
        self.calls.append(("banner", version))


def _check(monkeypatch, current, data):
    monkeypatch.setattr(main_window, "__version__", current)
    tasks = Recorder()
    shell = Recorder()
    window = SimpleNamespace(
        task_manager=tasks,
        shell=shell,
        cfg=SimpleNamespace(general=SimpleNamespace()),
        _update_check_manual=False,
        update_status_bar=lambda message: None,
    )
    window._parse_version = types.MethodType(
        main_window.ConfigUI._parse_version, window
    )
    main_window.ConfigUI.on_update_check_result(window, data)
    return tasks.calls, shell.calls


@pytest.mark.parametrize(
    "current", ["3.0.0-fork-batch1-f30a596", "2.5.0"]
)
def test_same_or_older_release_is_up_to_date(monkeypatch, current):
    tasks, banner = _check(monkeypatch, current, ("2.5.0", RELEASES))

    assert tasks == ["Up to date"]
    assert banner == []


def test_newer_release_is_announced(monkeypatch):
    tasks, banner = _check(monkeypatch, "2.4.0", ("2.5.0", RELEASES))

    assert tasks == ["Update available"]
    assert banner == [("banner", "2.5.0")]


def test_unreadable_answer_announces_nothing(monkeypatch):
    tasks, banner = _check(monkeypatch, "3.0.0", ("nightly", RELEASES))

    assert tasks == ["No update information"]
    assert banner == []
