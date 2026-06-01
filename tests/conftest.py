import os

# Set sharding generation to 0 by default for all test runs.
# Individual tests can override this value using monkeypatch.setenv.
os.environ["SCENES_SHARDED_GENERATION"] = "0"

# Tests must never send real Telegram messages/files, even when the local
# shell has production TELEGRAM_* credentials exported.
os.environ["VIDEO_AGENT_DISABLE_TELEGRAM"] = "1"
