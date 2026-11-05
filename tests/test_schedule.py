"""`ghti schedule`: the daily task, printed and never installed.

What these tests defend, in order of what would hurt a user most:
1. the command never installs anything and never writes a file;
2. the printed command works without the shell's PATH, because a scheduled task
   has none;
3. when the secrets are only in environment variables the page says so first,
   instead of handing over a task that would fail every night in silence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ghti.cli import main
from ghti.config import config_dir
from ghti.errors import ConfigError
from ghti.schedule import PLATFORMS, build, parse_at, quote, runner, this_platform


def files_in(folder: Path) -> set[str]:
    return {p.name for p in folder.rglob("*")} if folder.exists() else set()


def test_writes_nothing_and_installs_nothing(capsys):
    before = files_in(config_dir())

    assert main(["schedule"]) == 0

    assert files_in(config_dir()) == before
    out = capsys.readouterr().out
    assert "Nothing below has been done" in out


@pytest.mark.parametrize("name", PLATFORMS)
def test_every_platform_says_how_to_undo_it(name, capsys):
    assert main(["schedule", "--os", name]) == 0
    out = capsys.readouterr().out
    assert "sync --days 3" in out
    if name == "windows":
        assert "schtasks /Create" in out and "schtasks /Delete" in out
    else:
        assert "crontab -e" in out and "delete that line" in out


def test_command_is_absolute_so_a_task_without_path_still_works(capsys):
    assert main(["schedule"]) == 0
    out = capsys.readouterr().out
    first = runner()[0]
    assert Path(first).is_absolute()
    assert first in out
    # the bare word, which is exactly what fails at 07:30, is never the command
    for line in out.splitlines():
        stripped = line.strip().strip('"').strip("'")
        assert not stripped.startswith("ghti sync")


def test_the_time_and_the_days_end_up_in_the_task(capsys):
    assert main(["schedule", "--os", "linux", "--at", "6:05", "--days", "7"]) == 0
    out = capsys.readouterr().out
    assert "5 6 * * *" in out
    assert "sync --days 7" in out
    assert "A daily run at 06:05" in out


def test_a_bad_time_is_refused(capsys):
    assert main(["schedule", "--at", "25:00"]) == 2
    assert "--at wants a time like 07:30" in capsys.readouterr().err


def test_zero_days_is_refused():
    assert main(["schedule", "--days", "0"]) == 2


def test_secrets_only_in_the_environment_are_warned_about(settings, capsys):
    """`settings` puts them in env vars: a night task would not see them."""
    assert main(["schedule"]) == 0
    out = capsys.readouterr().out
    assert "would make every night fail" in out
    assert "config.env" in out


def test_secrets_in_the_file_mean_no_warning(capsys):
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.env").write_text(
        "GOOGLE_CLIENT_ID=123-test.apps.googleusercontent.com\n"
        "GOOGLE_CLIENT_SECRET=zzz-client-secret-XYZ\n"
        "INTERVALS_API_KEY=zzz-intervals-key-ABC\n",
        encoding="utf-8",
    )

    assert main(["schedule"]) == 0

    out = capsys.readouterr().out
    assert "would make every night fail" not in out
    # a page about scheduling never has to show a secret to do its job
    assert "zzz-client-secret-XYZ" not in out
    assert "zzz-intervals-key-ABC" not in out


def test_another_system_says_the_paths_are_not_its_own(capsys):
    other = next(name for name in PLATFORMS if name != this_platform())
    assert main(["schedule", "--os", other]) == 0
    assert "not of the" in capsys.readouterr().out


def test_the_log_is_in_the_config_folder_by_default():
    assert build().log.parent == config_dir()
    assert build(log="D:/altro/mio.log").log == Path("D:/altro/mio.log")


def test_quoting_matches_the_shell_that_will_run_it():
    parts = (r"C:\two words\python.exe", "-m", "ghti")
    assert quote(parts, "windows") == r'"C:\two words\python.exe" -m ghti'
    assert quote(parts, "linux") == r"'C:\two words\python.exe' -m ghti"


def test_parse_at_and_platform_names():
    assert parse_at("00:00") == (0, 0)
    assert parse_at(" 23:59 ") == (23, 59)
    with pytest.raises(ConfigError):
        parse_at("7.30")
    assert this_platform("win32") == "windows"
    assert this_platform("darwin") == "macos"
    assert this_platform("freebsd13") == "linux"
