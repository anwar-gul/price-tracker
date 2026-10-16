from __future__ import annotations

from types import SimpleNamespace

from pricetracker import cli


def _crawl_args(**overrides):
    args = dict(
        spider="books", loglevel=None, db=None, max_pages=None, start_url=None,
        username=None, password=None, site=None, sites_file="sites.yaml",
        csrf_selector=None, csrf_input=None, csrf_attribute=None,
        next_selector=None, submit_selector=None, error_selector=None,
    )
    args.update(overrides)
    return SimpleNamespace(**args)


def test_crawl_subprocess_command_has_no_none_args(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli._run_crawl_subprocess(_crawl_args()) == 0
    command = captured["command"]
    assert all(isinstance(part, str) for part in command)
    assert "-L" not in command
    assert "--site" not in command


def test_crawl_subprocess_passes_site_and_login_flags(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    args = _crawl_args(
        site="partner", sites_file="acme.yaml",
        csrf_selector="meta[name=csrf-token]", next_selector="button[name=next]",
    )
    assert cli._run_crawl_subprocess(args) == 0
    command = captured["command"]
    assert command[command.index("--site") + 1] == "partner"
    assert "--sites-file" in command and "acme.yaml" in command
    assert command[command.index("--csrf-selector") + 1] == "meta[name=csrf-token]"
    assert command[command.index("--next-selector") + 1] == "button[name=next]"


def test_lock_prevents_overlapping_watch(tmp_path):
    path = str(tmp_path / "tracker.lock")

    first = cli._acquire_lock(path)
    assert first is not None

    assert cli._acquire_lock(path) is None  # simulated second process

    first.release()
    second = cli._acquire_lock(path)
    assert second is not None
    second.release()


def test_sleep_interruptible_stops_early(monkeypatch):
    cli._STOP = False
    calls = {"n": 0}
    original = cli.time.sleep

    def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] >= 3:
            cli._STOP = True
        original(0)

    monkeypatch.setattr(cli.time, "sleep", fake_sleep)
    cli._sleep_interruptible(60)
    assert calls["n"] == 3  # woke up early instead of sleeping 60s
    cli._STOP = False
