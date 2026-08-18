"""Independent multilingual V2 sidecar.

Legacy modules must never import this package. Only the localized V2 dashboard,
API, and worker are entry points.
"""

from video_agent.localized_v2.contracts import CONTRACT_VERSION

__all__ = ["CONTRACT_VERSION"]
