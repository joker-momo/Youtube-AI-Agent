from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlsplit

from video_agent.localized_v2.runtime import load_runtime_settings


def launcher_fields(config_path: Path, repo_root: Path) -> tuple[str, ...]:
    settings = load_runtime_settings(config_path, repo_root=repo_root)
    worker = urlsplit(settings.browser_worker_url)
    cdp = urlsplit(settings.browser_cdp_url)
    return (
        str(settings.root),
        settings.host,
        str(settings.port),
        settings.browser_worker_url,
        str(worker.port),
        settings.browser_cdp_url,
        str(cdp.port),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("repo_root", type=Path)
    args = parser.parse_args()
    print("\t".join(launcher_fields(args.config, args.repo_root)))


if __name__ == "__main__":
    main()
