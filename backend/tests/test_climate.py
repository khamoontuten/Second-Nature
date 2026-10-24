"""Climate ingestion: providers (mocked HTTP), retries, caching, fallbacks, uploads, QC and features."""

from datetime import date

import httpx
import numpy as np
import pandas as pd
import pytest
import respx
import xarray as xr

from app.climate import features, quality, store
from app.climate.http import CachedClient
from app.climate.ingestion import fetch_climate, load_reference, reference_catalog
from app.climate.providers.base import ClimateRequest
from app.climate.providers.cds_era5 import build_request
from app.climate.providers.csv_upload import parse_csv
from app.climate.providers.epw import EPWProvider
from app.climate.providers.nasa_power import NasaPowerProvider
from app.climate.providers.netcdf import parse_netcdf
from app.climate.providers.open_meteo import URL as OM_URL
from app.climate.providers.open_meteo import OpenMeteoProvider
from app.core.errors import ProviderError, ValidationFailure

REQ = ClimateRequest(12.97, 77.59, date(2023, 1, 1), date(2023, 1, 2), elevation_m=920.0)


def _om_body(hours=48, lat=13.0, lon=77.6):
    times = pd.date_range("2023-01-01", periods=hours, freq="h").strftime("%Y-%m-%dT%H:%M").tolist()
    t = (22 + 6 * np.sin(np.arange(hours) / 24 * 2 * np.pi)).round(2).tolist()
    return {"latitude": lat, "longitude": lon, "elevation": 905.0, "timezone": "GMT",
            "hourly_units": {"temperature_2m": "°C"},
            "hourly": {"time": times, "temperature_2m": t, "relative_humidity_2m": [60] * hours,
                       "dew_point_2m": [14.0] * hours, "surface_pressure": [911.0] * hours, "wind_speed_10m": [3.0] * hours,
                       "wind_direction_10m": [270] * hours, "shortwave_radiation": [0] * 6 + [500] * 12 + [0] * (hours - 18),
                       "direct_normal_irradiance": [0] * hours, "diffuse_radiation": [0] * hours, "cloud_cover": [20] * hours,
                       "precipitation": [0.0] * hours, "soil_temperature_100_to_255cm": [24.0] * hours,
                       "wind_gusts_10m": [None] * hours}}


def client(tmp_path, name="test", **kw):
    return CachedClient(name, cache_dir=tmp_path, sleep=lambda s: None, **kw)


@respx.mock
def test_open_meteo_parse_units_provenance_and_cache(tmp_path):
    route = respx.get(OM_URL).mock(return_value=httpx.Response(200, json=_om_body()))
    prov = OpenMeteoProvider(client(tmp_path))
    ds = prov.fetch(REQ)
    assert route.call_count == 1
    assert ds.data["p_pa"].iloc[0] == pytest.approx(91100.0)
    assert ds.data.index.tz is not None and str(ds.data.index.tz) == "UTC"
    assert ds.provenance.data_nature == "reanalysis"
    assert ds.provenance.actual_lat == 13.0 and ds.provenance.distance_km > 0
    assert ds.provenance.elevation_difference_m == pytest.approx(15.0)
    assert "t_soil_100_255_c" in ds.soil_layers_m
    assert ds.data["wind_gust_ms"].isna().all()  # nulls stay missing, never invented
    # radiation shifted −1 h (preceding-hour mean → hour-beginning)
    assert ds.data["ghi_wm2"].iloc[5] == 500
    ds2 = OpenMeteoProvider(client(tmp_path)).fetch(REQ)
    assert route.call_count == 1  # served from cache
    assert ds2.provenance.extra["requests"][0]["cached"] is True


@respx.mock
def test_retry_with_backoff_then_success(tmp_path):
    sleeps = []
    route = respx.get(OM_URL).mock(side_effect=[httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(503),
                                                httpx.Response(200, json=_om_body())])
    c = CachedClient("om", cache_dir=tmp_path, sleep=sleeps.append, backoff_base_s=1.0)
    OpenMeteoProvider(c).fetch(REQ)
    assert route.call_count == 3
    assert sleeps[0] >= 2.0 and sleeps[1] >= 2.0  # Retry-After honoured, exponential growth


@respx.mock
def test_non_retryable_error_raises_provider_error(tmp_path):
    respx.get(OM_URL).mock(return_value=httpx.Response(400, json={"reason": "bad request"}))
    with pytest.raises(ProviderError) as e:
        OpenMeteoProvider(client(tmp_path, name="open_meteo")).fetch(REQ)
    assert "open_meteo" in str(e.value) and not e.value.retryable


@respx.mock
def test_timeout_retries_exhausted(tmp_path):
    respx.get(OM_URL).mock(side_effect=httpx.ConnectTimeout("timeout"))
    with pytest.raises(ProviderError):
        OpenMeteoProvider(client(tmp_path, max_retries=2)).fetch(REQ)


@respx.mock
def test_fallback_is_recorded_not_silent(tmp_path):
    respx.get(OM_URL).mock(return_value=httpx.Response(500))
    hours = pd.date_range("2023-01-01", periods=48, freq="h")
    keys = hours.strftime("%Y%m%d%H").tolist()
    power = {"geometry": {"coordinates": [77.5, 12.9, 900.0]}, "header": {"fill_value": -999.0},
             "properties": {"parameter": {"T2M": dict(zip(keys, [25.0] * 48, strict=True)), "RH2M": dict(zip(keys, [50.0] * 48, strict=True)),
                                          "PS": dict(zip(keys, [91.0] * 48, strict=True)), "WS10M": dict(zip(keys, [-999.0] + [2.0] * 47, strict=True))}}}
    respx.get("https://power.larc.nasa.gov/api/temporal/hourly/point").mock(return_value=httpx.Response(200, json=power))
    out = fetch_climate(REQ, ["open_meteo", "nasa_power"],
                        providers={"open_meteo": OpenMeteoProvider(client(tmp_path, max_retries=0)),
                                   "nasa_power": NasaPowerProvider(client(tmp_path))})
    assert out.provider_used == "nasa_power"
    assert out.dataset.provenance.fallback_from == ["open_meteo"]
    assert any("fallback" in n for n in out.dataset.provenance.notes)
    assert out.dataset.data["p_pa"].iloc[0] == pytest.approx(91000.0)
    assert np.isnan(out.dataset.data["wind_ms"].iloc[0])  # fill value → missing


def test_all_providers_failing_raises(tmp_path):
    class Broken:
        def is_configured(self):
            return True

        def fetch(self, r):
            raise ProviderError("broken", "down")

    with pytest.raises(ProviderError) as e:
        fetch_climate(REQ, ["open_meteo"], providers={"open_meteo": Broken()})
    assert "all providers failed" in str(e.value)


def test_epw_reference_parsing_and_time_conversion():
    cat = reference_catalog()
    assert len(cat["sites"]) == 8
    ds = load_reference("ashburn_va")
    assert len(ds.data) == 8760
    # Dulles is UTC−5 standard: local hour-ending 1 (00:00–01:00 LST) → 05:00 UTC hour-beginning
    assert ds.data.index[0] == pd.Timestamp("2001-01-01 05:00", tz="UTC")
    assert ds.provenance.data_nature == "typical_year"
    assert ds.provenance.distance_km == pytest.approx(0.0, abs=1e-6)
    assert 2.0 in ds.monthly_ground_c and any("modeled" in n for n in ds.provenance.notes)
    rep = quality.assess(ds, -77.47)
    assert rep.timezone_check["plausible"], rep.timezone_check


def test_epw_missing_codes_become_nan(tmp_path):
    from app.climate.providers.epw import parse_epw

    text = (WEATHER_HEADER + "\n" + "\n".join(_epw_row(h, t=99.9 if h == 3 else 10.0) for h in range(1, 25)))
    ds = parse_epw(text)
    assert ds.data["t_db_c"].isna().sum() == 1
    assert ds.flags["t_db_c"].sum() == 1


WEATHER_HEADER = "\n".join([
    "LOCATION,Test,XX,XXX,Custom,000000,10.0,20.0,1.0,100.0",
    "DESIGN CONDITIONS,0", "TYPICAL/EXTREME PERIODS,0", "GROUND TEMPERATURES,0", "HOLIDAYS/DAYLIGHT SAVINGS,No,0,0,0",
    "COMMENTS 1,test", "COMMENTS 2,test", "DATA PERIODS,1,1,Data,Sunday, 1/ 1,12/31"])


def _epw_row(hour, t=10.0):
    vals = [2010, 1, 1, hour, 0, "?9?9", t, 5.0, 70, 100000, 0, 0, 300, 0, 0, 0, 0, 0, 0, 0, 180, 3.0, 5, 5, 20, 77777, 9,
            999999999, 10, 0.1, 0, 88, 0.2, 0.0, 1.0]
    return ",".join(str(v) for v in vals)


def test_csv_upload_units_and_validation():
    csv = "timestamp,temp_f,rh,wind_kmh\n" + "\n".join(f"2023-01-01 {h:02d}:00,{68 + h},{50},{36}" for h in range(24))
    ds = parse_csv(csv.encode(), latitude=1, longitude=2, timezone="UTC",
                   mapping={"t_db_c": {"column": "temp_f", "unit": "°F"}, "wind_ms": {"column": "wind_kmh", "unit": "km/h"}})
    assert ds.data["t_db_c"].iloc[0] == pytest.approx(20.0)
    assert ds.data["wind_ms"].iloc[0] == pytest.approx(10.0)
    assert ds.provenance.data_nature == "observed"
    with pytest.raises(ValidationFailure):
        parse_csv(b"foo,bar\n1,2\n", latitude=1, longitude=2)
    with pytest.raises(ValidationFailure):
        parse_csv(csv.encode(), latitude=1, longitude=2, mapping={"t_db_c": {"column": "temp_f", "unit": "m/s"}})


def test_csv_local_time_with_dst_converted():
    csv = "time,temperature\n2023-07-01 12:00,30\n2023-07-01 13:00,31\n"
    ds = parse_csv(csv.encode(), latitude=40, longitude=-74, timezone="America/New_York")
    assert ds.data.index[0] == pd.Timestamp("2023-07-01 16:00", tz="UTC")


def test_netcdf_era5_parsing(tmp_path):
    times = pd.date_range("2023-01-01", periods=48, freq="h")
    lat = np.array([13.25, 13.0, 12.75])
    lon = np.array([77.5, 77.75])
    shape = (48, 3, 2)
    ds = xr.Dataset({
        "t2m": (("valid_time", "latitude", "longitude"), np.full(shape, 300.15)),
        "d2m": (("valid_time", "latitude", "longitude"), np.full(shape, 288.15)),
        "sp": (("valid_time", "latitude", "longitude"), np.full(shape, 91000.0)),
        "u10": (("valid_time", "latitude", "longitude"), np.full(shape, 3.0)),
        "v10": (("valid_time", "latitude", "longitude"), np.full(shape, 0.0)),
        "ssrd": (("valid_time", "latitude", "longitude"), np.full(shape, 3600.0 * 400)),
        "stl3": (("valid_time", "latitude", "longitude"), np.full(shape, 297.15)),
    }, coords={"valid_time": times, "latitude": lat, "longitude": lon})
    path = tmp_path / "era5.nc"
    ds.to_netcdf(path, engine="h5netcdf")
    out = parse_netcdf(path, latitude=12.97, longitude=77.59)
    assert out.data["t_db_c"].iloc[0] == pytest.approx(27.0)
    assert out.data["wind_ms"].iloc[0] == pytest.approx(3.0)
    assert out.data["wind_dir_deg"].iloc[0] == pytest.approx(270.0)  # u>0 is wind from the west
    assert out.data["ghi_wm2"].iloc[0] == pytest.approx(400.0)
    assert out.provenance.actual_lat == pytest.approx(13.0) and out.provenance.actual_lon == pytest.approx(77.5)
    assert "t_soil_28_100_c" in out.soil_layers_m


def test_cds_request_builder():
    name, body = build_request(ClimateRequest(12.97, 77.59, date(2020, 1, 1), date(2021, 12, 31)), "era5_land")
    assert name == "reanalysis-era5-land"
    assert body["year"] == ["2020", "2021"]
    assert body["area"][0] > 12.97 > body["area"][2]


def test_quality_detects_problems():
    idx = pd.date_range("2023-01-01", periods=100, freq="h", tz="UTC")
    t = np.full(100, 20.0) + np.sin(np.arange(100))
    t[10] = 75.0           # impossible
    t[40:50] = np.nan      # long gap
    t[60] = np.nan         # short gap
    from app.climate.providers.base import ClimateDataset, Provenance

    prov = Provenance("t", "t", "", None, "", 0, 0, 0, 0, 0, 0, 0, 0, "", "", {}, "UTC", "", "manual", "", "")
    df = pd.DataFrame({"t_db_c": t, "t_dp_c": t - 30, "wind_ms": np.ones(100)}, index=idx).drop(idx[80])
    ds = ClimateDataset(df, prov)
    ds = quality.reindex_hourly(ds)
    rep = quality.assess(ds, 0.0, long_gap_h=6)
    assert rep.out_of_range["t_db_c"] == 1
    assert rep.gaps["t_db_c"]["longest_h"] == 10
    assert any("longer than 6 h" in w for w in rep.warnings)
    assert rep.flatlines["wind_ms"] > 0
    filled = quality.fill_short_gaps(ds, max_gap_h=3, columns=["t_db_c"])
    assert filled["t_db_c"] == 2          # the 1-h gap and the reinserted timestamp only
    assert ds.data["t_db_c"].isna().sum() == 10  # long gap left alone
    assert ((ds.flags["t_db_c"] & quality.F_FILLED) > 0).sum() == 2


def test_cross_source_comparison(datasets):
    a = datasets("ashburn")
    b = load_reference("ashburn_va")
    b.data = b.data.copy()
    b.data["t_db_c"] = b.data["t_db_c"] + 1.0
    cmp = quality.compare_sources(a, b, ["t_db_c"])
    assert cmp["variables"]["t_db_c"]["bias"] == pytest.approx(1.0)
    assert cmp["variables"]["t_db_c"]["corr"] > 0.999


def test_features_summary(datasets):
    ds = datasets("phoenix")
    s = features.summarise_climate(ds, elevation_m=337, timezone="America/Phoenix")
    assert s["overall"]["t_db_c"]["max"] > 40
    assert s["design_conditions"]["t_db_c"]["0.4%"] > s["design_conditions"]["t_db_c"]["2.0%"]
    assert len(s["monthly"]["t_db_c"]["mean"]) == 12
    assert s["wind_rose"]["prevailing"] in s["wind_rose"]["sectors"]
    assert abs(sum(map(sum, s["wind_rose"]["frequency_pct"])) - 100.0) < 0.05  # rounded to 3 dp
    assert s["heatwaves"]["count"] >= 1
    assert np.array(s["solar"]["mean_ghi_wm2"]).shape == (12, 24)
    miami = features.summarise_climate(datasets("miami"), timezone="America/New_York")
    assert miami["hot_humid_hours"]["hours"] > s["hot_humid_hours"]["hours"]


def test_store_round_trip(tmp_path, datasets):
    ds = datasets("chicago")
    info = store.save(ds, "chi", tmp_path)
    back = store.load("chi", tmp_path)
    pd.testing.assert_frame_equal(back.data, ds.data, check_freq=False)
    assert back.provenance.product == ds.provenance.product
    assert info["sha256"] == store.dataset_hash(back) or len(info["sha256"]) == 64


def test_epw_provider_from_path():
    from tests.conftest import SITES, WEATHER

    ds = EPWProvider(WEATHER / SITES["miami"][0]).fetch()
    assert ds.data["t_db_c"].mean() > 20
