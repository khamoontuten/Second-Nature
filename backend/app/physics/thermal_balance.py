"""Energy-balance closure checks.

For each time step the heat entering the conditioned boundary must equal the heat leaving it
plus the change in storage:

    Σ sources (IT, losses in space, fan heat, envelope gains, infiltration)
      = Σ removal paths (coil/plant, economiser exhaust, EAHE to ground, TES discharge) + ΔE_stored

The residual is reported per hour and summarised; a run fails closure if the maximum
relative residual exceeds the tolerance.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class ClosureReport:
    residual_w: np.ndarray
    max_abs_residual_w: float
    max_relative_residual: float
    annual_residual_kwh: float
    tolerance: float
    passed: bool

    def to_dict(self) -> dict:
        return {
            "max_abs_residual_w": self.max_abs_residual_w,
            "max_relative_residual": self.max_relative_residual,
            "annual_residual_kwh": self.annual_residual_kwh,
            "tolerance": self.tolerance,
            "passed": self.passed,
        }


def closure(sources: dict[str, np.ndarray], sinks: dict[str, np.ndarray], storage_change_w: np.ndarray | None = None,
            tolerance: float = 0.01, dt_s: float = 3600.0) -> ClosureReport:
    src = sum(np.asarray(v, dtype=float) for v in sources.values())
    snk = sum(np.asarray(v, dtype=float) for v in sinks.values())
    if storage_change_w is not None:
        snk = snk + np.asarray(storage_change_w, dtype=float)
    resid = np.asarray(src - snk, dtype=float)
    scale = np.maximum(np.abs(np.asarray(src, dtype=float)), 1.0)
    rel = np.abs(resid) / scale
    max_rel = float(np.max(rel)) if rel.size else 0.0
    return ClosureReport(resid, float(np.max(np.abs(resid))) if resid.size else 0.0, max_rel,
                         float(np.sum(resid) * dt_s / 3.6e6), tolerance, max_rel <= tolerance)
