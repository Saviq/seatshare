import subprocess
from pathlib import Path
from xml.etree import ElementTree

import yaml

from server.config import ROOT
from tests.conftest import ClientFactory


def test_pages_and_local_assets(client_factory: ClientFactory) -> None:
    client = client_factory(None)
    for path in ["/", "/new", "/mine", "/requests"]:
        response = client.get(path)
        assert response.status_code == 200
        assert 'src="/static/js/app.js?v=50"' in response.text
        assert 'href="/static/css/app.css?v=34"' in response.text
        assert '<meta name="color-scheme" content="light dark">' in response.text
        assert '<body>\n    <script src="/static/js/theme.js?v=3"></script>' in response.text
        assert 'class="site-account"' in response.text
        assert '<a class="p-button" href="/auth/login">Sign in with GitHub</a>' in response.text
        assert 'id="theme-toggle" class="p-button" type="button" data-theme="auto"' in response.text
        assert 'aria-label="Theme: Auto. Switch to Dark theme"' in response.text
        for theme in ["light", "dark", "auto"]:
            assert f'class="theme-icon theme-icon--{theme}"' in response.text
        assert 'aria-label="Seat Share home"' in response.text
        assert 'src="/static/images/seat-share.svg"' in response.text
        assert '<a href="/mine">My spots</a>' in response.text
        assert "My tables" not in response.text
        assert '<input type="text" name="name"' in response.text
        assert '<select name="bookable" aria-describedby="bookable-help">' in response.text
        assert '<input type="text" name="bookable_name" maxlength="100" required>' in response.text
        assert '<input type="text" name="bookable_area" maxlength="100">' in response.text
        assert "bookable-combo" not in response.text
        assert "<datalist" not in response.text
        assert '<select name="bookable_type" required>' in response.text
        assert '<option value="restaurant">Restaurant</option>' in response.text
        assert '<option value="excursion">Excursion</option>' in response.text
        assert '<option value="sports">Sports</option>' in response.text
        assert 'name="duration_hours"' in response.text
        assert 'step="any" placeholder="2"' in response.text
        assert '<a href="/requests">' not in response.text
        assert "http" not in response.text
    for path in [
        "/static/js/app.js",
        "/static/js/api.js",
        "/static/js/theme.js",
        "/static/css/vanilla.min.css",
        "/static/images/seat-share.svg",
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/healthz").json() == {"status": "ok"}
    assert "@font-face" not in (ROOT / "web/css/vanilla.min.css").read_text().split("\n", 1)[-1]


def test_legacy_requests_page_redirects_to_my_seats(client_factory: ClientFactory) -> None:
    response = client_factory(None).get("/requests", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/mine"


def test_brand_icon_is_svg(client_factory: ClientFactory) -> None:
    response = client_factory(None).get("/static/images/seat-share.svg")
    assert response.headers["content-type"].startswith("image/svg+xml")
    icon = ElementTree.fromstring(response.content)
    assert icon.tag == "{http://www.w3.org/2000/svg}svg"
    assert icon.attrib["viewBox"] == "0 0 64 64"


def test_workshop_test_action_forwards_arguments_and_failure(tmp_path: Path) -> None:
    workshop = yaml.safe_load((ROOT / ".workshop/dev.yaml").read_text())
    action = workshop["actions"]["tests"]
    executable = tmp_path / "uv"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 17\n')
    executable.chmod(0o755)
    result = subprocess.run(
        ["bash", "-eu", "-c", action, "tests", "-k", "two words", "tests/example.py"],
        env={"PATH": str(tmp_path) + ":/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 17
    assert result.stdout.splitlines() == [
        "run",
        "--locked",
        "pytest",
        "-k",
        "two words",
        "tests/example.py",
    ]


def test_dev_action_is_separate_and_reload_enabled() -> None:
    action = yaml.safe_load((ROOT / ".workshop/dev.yaml").read_text())["actions"]["dev"]
    assert "DB_PATH=.local/dev.db" in action
    assert "--reload" in action
    assert "server.main:create_app" in action
    assert "--factory" in action
    assert "--env-file .env" in action


def test_dev_action_arguments_and_database_override(tmp_path: Path) -> None:
    action = yaml.safe_load((ROOT / ".workshop/dev.yaml").read_text())["actions"]["dev"]
    executable = tmp_path / "uv"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$DB_PATH" "$@"\n')
    executable.chmod(0o755)
    hostname = tmp_path / "hostname"
    hostname.write_text('#!/bin/sh\n[ "$1" = -f ] && echo dev.example.test\n')
    hostname.chmod(0o755)
    result = subprocess.run(
        ["bash", "-eu", "-c", action, "dev", "--reload-delay", "0.5"],
        env={"PATH": str(tmp_path) + ":/usr/bin:/bin", "DB_PATH": "/data/production.db"},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    lines = result.stdout.splitlines()
    assert lines[0] == "Seat Share development server: http://dev.example.test:8080"
    assert lines[1] == ".local/dev.db"
    assert lines[lines.index("--host") + 1] == "dev.example.test"
    assert lines[-2:] == ["--reload-delay", "0.5"]
    assert (tmp_path / ".local").is_dir()


def test_seed_demo_action_arguments_and_database_override(tmp_path: Path) -> None:
    action = yaml.safe_load((ROOT / ".workshop/dev.yaml").read_text())["actions"]["seed-demo"]
    executable = tmp_path / "uv"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$DB_PATH" "$APP_ENV" "$@"\nexit 17\n')
    executable.chmod(0o755)
    result = subprocess.run(
        ["bash", "-eu", "-c", action, "seed-demo", "--timezone", "Europe/London", "alice", "bob"],
        env={
            "PATH": str(tmp_path) + ":/usr/bin:/bin",
            "DB_PATH": "/data/production.db",
            "APP_ENV": "production",
        },
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 17
    assert result.stdout.splitlines() == [
        ".local/dev.db",
        "development",
        "run",
        "--locked",
        "--env-file",
        ".env",
        "python",
        "-m",
        "server.seed_demo",
        "--timezone",
        "Europe/London",
        "alice",
        "bob",
    ]
    assert (tmp_path / ".local").is_dir()


def test_pre_commit_uses_locked_uv_tools() -> None:
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    hooks = config["repos"][0]["hooks"]
    assert {hook["id"] for hook in hooks} == {"ruff", "ruff-format", "mypy"}
    assert all(hook["entry"].startswith("uv run --locked ") for hook in hooks)
