from __future__ import annotations

from pathlib import Path

from core_mm.control_plane import ControlCommandStore, validate_command


def test_control_command_store_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    store = ControlCommandStore(db_path)

    command_id = store.submit_command(
        run_id="run-a",
        runtime_root=tmp_path.as_posix(),
        scope="global",
        command_type="pause_trading",
        payload={"reason": "test"},
        requested_by="dashboard",
    )

    commands = store.fetch_pending_commands(runtime_root=tmp_path.as_posix(), active_run_id="run-a")
    assert len(commands) == 1
    assert commands[0].command_id == command_id
    assert commands[0].payload["reason"] == "test"

    store.mark_command(command_id=command_id, status="applied", event_type="applied", result={"ok": True})
    recent = store.list_commands(runtime_root=tmp_path.as_posix(), limit=5)
    assert recent[0].status == "applied"
    assert recent[0].result["ok"] is True


def test_control_command_store_expires_stale_commands(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    store = ControlCommandStore(db_path)
    store.submit_command(
        run_id="run-a",
        runtime_root=tmp_path.as_posix(),
        scope="global",
        command_type="pause_trading",
        payload={},
        requested_by="dashboard",
        requested_at_ms=1_000,
        expires_in_ms=1_000,
    )
    expired = store.expire_stale_commands(runtime_root=tmp_path.as_posix(), active_run_id="run-a", ts_ms=5_000)
    assert expired == 1
    recent = store.list_commands(runtime_root=tmp_path.as_posix(), limit=5)
    assert recent[0].status == "expired"


def test_validate_command_blocks_live_config_patch() -> None:
    errors = validate_command("LIVE", "apply_config_patch", {"patch": {"trade_size": 10}})
    assert "command_not_allowed:apply_config_patch" in errors
