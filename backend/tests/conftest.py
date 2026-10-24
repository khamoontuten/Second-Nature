from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("SN_DATABASE_URL", "sqlite://")

REPO = Path(__file__).resolve().parents[2]
WEATHER = REPO / "data" / "reference" / "weather"

SITES = {
    "ashburn": ("USA_VA_Sterling-Washington.Dulles.Intl.AP.724030_TMY3.epw.gz", 38.98, -77.47, 82.0, "America/New_York"),
    "phoenix": ("USA_AZ_Phoenix-Sky.Harbor.Intl.AP.722780_TMY3.epw.gz", 33.45, -111.98, 337.0, "America/Phoenix"),
    "miami": ("USA_FL_Miami.Intl.AP.722020_TMY3.epw.gz", 25.82, -80.30, 11.0, "America/New_York"),
    "chicago": ("USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw.gz", 41.98, -87.92, 201.0, "America/Chicago"),
}


@pytest.fixture(scope="session")
def datasets():
    from app.climate.providers.epw import EPWProvider

    cache = {}

    def get(site: str):
        if site not in cache:
            f, *_ = SITES[site]
            cache[site] = EPWProvider(WEATHER / f).fetch()
        return cache[site]

    return get


def site_config(site: str, **overrides):
    from app.schemas.config import ProjectConfig

    f, lat, lon, el, tz = SITES[site]
    d = dict(site=dict(name=site, latitude=lat, longitude=lon, elevation_m=el, timezone=tz),
             grid=dict(emission_factor_kg_per_kwh=0.4, source="test assumption"))
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(d.get(k), dict):
            d[k] = {**d[k], **v}
        else:
            d[k] = v
    return ProjectConfig(**d)


@pytest.fixture
def make_config():
    return site_config
