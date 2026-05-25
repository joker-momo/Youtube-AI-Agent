import os

# Set sharding generation to 0 by default for all test runs.
# Individual tests can override this value using monkeypatch.setenv.
os.environ["SCENES_SHARDED_GENERATION"] = "0"
