"""Gauntlet: CI-runnable evaluation gates for generative AI features.

Gates are YAML-driven suites run against any HTTP endpoint or Python
callable. Nothing here depends on a model vendor, and nothing here is a
compliance certification.
"""

from gauntlet.results import CaseResult, GateResult, RunResult
from gauntlet.targets import Target, TargetResponse

__version__ = "0.1.0"

__all__ = [
    "CaseResult",
    "GateResult",
    "RunResult",
    "Target",
    "TargetResponse",
    "__version__",
]
