import pytest

from app.code_paths import InvalidPathError, resolve_within, validate_path


def test_validate_path_accepts_allowed_file():
    assert validate_path("app/chat_service.py") == "app/chat_service.py"


def test_validate_path_accepts_bare_top_level_dir_name():
    # Ordner-Eintraege kommen ohne trailing slash zurueck (z.B. von der
    # GitHub-API) - "app" muss genauso erlaubt sein wie "app/irgendwas.py".
    assert validate_path("app") == "app"


def test_validate_path_accepts_empty_root():
    assert validate_path("") == ""


def test_validate_path_rejects_disallowed_prefix():
    with pytest.raises(InvalidPathError):
        validate_path("secrets/keys.txt")


def test_validate_path_rejects_traversal():
    with pytest.raises(InvalidPathError):
        validate_path("app/../.env")


def test_validate_path_rejects_env_file():
    with pytest.raises(InvalidPathError):
        validate_path("app/.env")


def test_validate_path_rejects_git_dir():
    with pytest.raises(InvalidPathError):
        validate_path(".git/config")


def test_resolve_within_returns_path_under_root(tmp_path):
    (tmp_path / "app").mkdir()
    resolved = resolve_within(tmp_path, "app/chat_service.py")
    assert resolved == (tmp_path / "app" / "chat_service.py").resolve()


def test_resolve_within_rejects_absolute_path(tmp_path):
    with pytest.raises(InvalidPathError):
        resolve_within(tmp_path, "/etc/passwd")


def test_resolve_within_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside_target"
    outside.mkdir(exist_ok=True)
    (tmp_path / "app").mkdir()
    symlink_path = tmp_path / "app" / "escape"
    try:
        symlink_path.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks nicht erstellbar in dieser Umgebung (z.B. Windows ohne Admin-Rechte).")
    with pytest.raises(InvalidPathError):
        resolve_within(tmp_path, "app/escape/file.py")
