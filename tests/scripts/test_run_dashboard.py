from pathlib import Path

from scripts.run_dashboard import resolve_dashboard_db_path


def test_resolve_dashboard_db_path_prefers_explicit_override(tmp_path: Path) -> None:
    explicit = tmp_path / "custom.db"
    explicit.write_text("", encoding="utf-8")

    resolved = resolve_dashboard_db_path(tmp_path, explicit.as_posix())

    assert resolved == explicit.resolve()


def test_resolve_dashboard_db_path_picks_latest_runtime_db(tmp_path: Path) -> None:
    older = tmp_path / "tmp" / "core_mm_runs" / "older-run" / "runtime.db"
    newer = tmp_path / "tmp" / "core_mm_runs" / "newer-run" / "runtime.db"
    older.parent.mkdir(parents=True)
    newer.parent.mkdir(parents=True)
    older.write_text("", encoding="utf-8")
    newer.write_text("", encoding="utf-8")
    older.touch()
    newer.touch()

    resolved = resolve_dashboard_db_path(tmp_path, None)

    assert resolved == newer.resolve()


def test_resolve_dashboard_db_path_prefers_fresh_running_runtime(tmp_path: Path) -> None:
    older = tmp_path / "tmp" / "core_mm_runs" / "older-run"
    newer = tmp_path / "tmp" / "core_mm_runs" / "newer-run"
    older_db = older / "runtime.db"
    newer_db = newer / "runtime.db"
    older_db.parent.mkdir(parents=True)
    newer_db.parent.mkdir(parents=True)
    older_db.write_text("", encoding="utf-8")
    newer_db.write_text("", encoding="utf-8")
    (older / "meta").mkdir()
    (newer / "meta").mkdir()
    (older / "meta" / "status.json").write_text('{"stage":"complete","updated_at_ms":1000}', encoding="utf-8")
    (newer / "meta" / "status.json").write_text('{"stage":"running","updated_at_ms":2000}', encoding="utf-8")

    resolved = resolve_dashboard_db_path(tmp_path, None)

    assert resolved == newer_db.resolve()


def test_resolve_dashboard_db_path_falls_back_to_repo_runtime_db(tmp_path: Path) -> None:
    resolved = resolve_dashboard_db_path(tmp_path, None)

    assert resolved == (tmp_path / "runtime.db").resolve()
