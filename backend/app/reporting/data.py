"""Collect everything a report/export needs from the stored simulation (single source of values)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app import MODEL_VERSION, __version__
from app.database.models import (
    ClimateDatasetRecord,
    OptimizationRun,
    Project,
    SensitivityResult,
    SimulationInput,
    SimulationRun,
)

REFERENCES = [
    "ASHRAE (2017). ASHRAE Handbook—Fundamentals, Ch. 1 Psychrometrics; Ch. 16 Ventilation and Infiltration; Ch. 24 Airflow Around Buildings.",
    "ASHRAE TC 9.9 (2021). Thermal Guidelines for Data Processing Environments, 5th ed.",
    "Incropera, F.P., DeWitt, D.P. et al. Fundamentals of Heat and Mass Transfer (conduction shape factors, Table 4.1; properties, Table A.3).",
    "Kusuda, T. & Achenbach, P.R. (1965). Earth temperature and thermal diffusivity at selected stations in the United States. ASHRAE Trans. 71.",
    "Swami, M.V. & Chandra, S. (1988). Correlations for pressure distribution on buildings and calculation of natural-ventilation airflow. ASHRAE Trans. 94.",
    "Haaland, S.E. (1983). Simple and explicit formulas for the friction factor in turbulent pipe flow. J. Fluids Eng. 105.",
    "Gnielinski, V. (1976). New equations for heat and mass transfer in turbulent pipe and channel flow. Int. Chem. Eng. 16.",
    "Berdahl, P. & Martin, M. (1984). Emissivity of clear skies. Solar Energy 32; Walton, G.N. (1983) cloud-cover correction (NBSIR 83-2655).",
    "Stull, R. (2011). Wet-bulb temperature from relative humidity and air temperature. J. Appl. Meteor. Climatol. 50.",
    "Wilcox, S. & Marion, W. (2008). Users Manual for TMY3 Data Sets. NREL/TP-581-43156.",
    "Hersbach, H. et al. (2020). The ERA5 global reanalysis. Q. J. R. Meteorol. Soc. 146.",
    "Holmgren, W., Hansen, C., Mikofski, M. (2018). pvlib python. J. Open Source Software 3(29) (solar position, NREL SPA).",
    "The Green Grid (2012/2016). PUE: a comprehensive examination of the metric (WP#49); WUE (WP#35).",
]

LIMITATIONS = [
    "Preliminary design and decision-support tool; not a substitute for detailed mechanical design, CFD, geotechnical investigation, electrical, fire or structural engineering, or commissioning.",
    "Natural-ventilation results come from a lumped zonal airflow network; they cannot resolve rack-level recirculation. CFD is required before relying on stack-driven airflow.",
    "Earth-air heat exchanger results depend strongly on soil conductivity, moisture and groundwater; default soil properties are assumptions until site testing is done.",
    "Chiller performance without manufacturer curves uses a Carnot-fraction model calibrated to the rated COP (preliminary).",
    "Typical-year (TMY) weather represents typical conditions and understates extremes; design checks should also use extreme-year or design-day data.",
    "Warming offsets are sensitivity tests, not climate projections.",
    "Cost and embodied-carbon results appear only where the engineer supplied unit costs or emission factors; otherwise they are reported as data gaps.",
    "Grid carbon intensity is an average factor unless a marginal dataset is supplied; carbon claims state the factor and boundary used.",
    "The platform has not been validated for construction and is not certified for regulatory compliance.",
]


@dataclass
class ReportData:
    project: Project
    run: SimulationRun
    inputs: SimulationInput
    dataset: ClimateDatasetRecord
    results: dict
    sensitivity: list[SensitivityResult] = field(default_factory=list)
    optimisations: list[OptimizationRun] = field(default_factory=list)
    model_version: str = MODEL_VERSION
    app_version: str = __version__

    def hourly(self, scenario_id: str) -> pd.DataFrame:
        return pd.read_parquet(Path(self.results[scenario_id].hourly_path))

    @property
    def scenario_ids(self) -> list[str]:
        return sorted(self.results)

    @property
    def baseline_id(self) -> str:
        return "S0" if "S0" in self.results else self.scenario_ids[0]

    @property
    def best_id(self) -> str:
        comp = self.run.comparison or {}
        feas = {k: v for k, v in comp.items() if v["objective"] not in (None, float("-inf")) and k != "S8"}
        if not feas:
            return self.baseline_id
        return max(feas, key=lambda k: feas[k]["objective"])


def collect(s: Session, run_id: str) -> ReportData:
    from app.services.simulations import get_run

    run = get_run(s, run_id)
    if run.status != "succeeded":
        from app.core.errors import ValidationFailure

        raise ValidationFailure(f"simulation {run_id} is {run.status}; reports need a completed simulation")
    project = s.get(Project, run.project_id)
    inputs = s.query(SimulationInput).filter_by(run_id=run_id).one()
    ds = s.get(ClimateDatasetRecord, run.dataset_id)
    sens = s.query(SensitivityResult).filter_by(simulation_id=run_id, status="succeeded").all()
    opts = s.query(OptimizationRun).filter_by(project_id=project.id, status="succeeded").all()
    return ReportData(project, run, inputs, ds, {r.scenario_id: r for r in run.results}, sens, opts)
