"""Legacy import paths. Older scripts do ``from miniharness.compat import chat``."""

from miniharness.chat.facade import chat
from miniharness.solver.sat import sat_solve
from miniharness.runner import run_tasks

__all__ = ["chat", "sat_solve", "run_tasks"]
