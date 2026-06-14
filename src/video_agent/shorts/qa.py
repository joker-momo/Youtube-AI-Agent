"""Short QA facade.

Implementation split into qa_common, qa_normalize, qa_product_scores and
qa_runners. Re-exported via * so `from ...shorts.qa import X`, `qa.X`
attribute access and monkeypatch on this module keep working.
"""

from __future__ import annotations

from video_agent.shorts.qa_common import *  # noqa: F401,F403
from video_agent.shorts.qa_normalize import *  # noqa: F401,F403
from video_agent.shorts.qa_product_scores import *  # noqa: F401,F403
from video_agent.shorts.qa_runners import *  # noqa: F401,F403
