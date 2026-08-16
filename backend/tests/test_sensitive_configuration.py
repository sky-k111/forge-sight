from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_local_environment_files_are_ignored_and_not_tracked():
    ignored = subprocess.run(
        ["git", "check-ignore", "backend/.env"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    assert ignored.stdout.strip() == "backend/.env"
    assert not any(Path(path).name == ".env" for path in tracked)


def test_example_environment_contains_placeholders_not_credentials():
    values = {}
    for raw_line in (ROOT / "backend" / ".env.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value

    assert values["EMAIL_CODE_SECRET"] == "CHANGE_ME"
    assert values["AUTH_TOKEN_SECRET"] == "CHANGE_ME"
    for key, value in values.items():
        if any(marker in key for marker in ("PASSWORD", "SECRET", "API_KEY")):
            assert value in {"", "CHANGE_ME"}
