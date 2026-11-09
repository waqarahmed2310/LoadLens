"""LoadLens: a parcel preload simulator, misload detector and zone optimizer.

All data is synthetic. See README.md for an overview.
"""

from .config import ScenarioConfig
from .pipeline import RunResult, ScenarioResult, run_scenario

__all__ = ["ScenarioConfig", "RunResult", "ScenarioResult", "run_scenario"]
__version__ = "1.0.0"
