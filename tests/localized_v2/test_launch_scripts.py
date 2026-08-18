from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = REPO_ROOT / "src" / "video_agent" / "localized_v2" / "scripts"


def test_mac_launcher_is_confined_to_v2_runtime_and_ports() -> None:
    source = (SCRIPT_ROOT / "launch_mac.sh").read_text(encoding="utf-8")

    assert "video_agent.localized_v2.launcher_settings" in source
    assert "LOCALIZED_V2_RUNTIME_CONFIG" in source
    assert "CDP_PORT=9322" not in source
    assert "WORKER_PORT=8793" not in source
    assert "video_agent.localized_v2.browser_worker:app" in source
    assert "localized-v2:en-us" in source
    assert "/usr/bin/screen" in source
    assert "nohup" not in source
    assert "browser_profiles/default" not in source
    assert "--remote-debugging-port=9222" not in source
    assert "video_agent.browser_worker.app:app" not in source


def test_mac_stopper_checks_process_identity_before_kill() -> None:
    source = (SCRIPT_ROOT / "stop_mac.sh").read_text(encoding="utf-8")

    assert '"${command}" == *"${marker}"*' in source
    assert "video_agent.localized_v2.browser_worker:app" in source
    assert '"--user-data-dir=${PROFILE_ROOT}"' in source
    assert "video_agent.localized_v2.launcher_settings" in source
    assert "killall" not in source
    assert "pkill" not in source


def test_full_service_launcher_starts_only_v2_entrypoints() -> None:
    source = (SCRIPT_ROOT / "launch_services_mac.sh").read_text(encoding="utf-8")

    assert "launch_mac.sh" in source
    assert "video_agent.localized_v2.dashboard" in source
    assert "video_agent.localized_v2.production_worker" in source
    assert "capabilities-en-us.yaml" in source
    assert 'LOCALIZED_V2_PROVIDER_ENV="${PRIMARY_ROOT}/.env"' in source
    assert "/usr/bin/screen" in source
    assert "nohup" not in source
    assert "video_agent.web" not in source
    assert "render.concurrency" not in source
    assert "video_agent.localized_v2.launcher_settings" in source
    assert "LOCALIZED_V2_RUNTIME_CONFIG" in source
    assert "find_worker_pid" in source
    assert "screen_session_exists" in source
    assert "/usr/bin/screen -ls" in source
    assert "-Q select" not in source
    assert '"${process_name}" == [Pp]ython*' in source
    assert '"${command}" == *" -m video_agent.localized_v2.production_worker"*' in source
    assert 'printf \'%s\\n\' "${worker_pid}" > "${PROCESS_ROOT}/worker.pid"' in source
    assert "DASHBOARD_PORT=8792" not in source
    assert "WORKER_PORT=8793" not in source
