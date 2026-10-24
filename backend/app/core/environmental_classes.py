"""IT-equipment environmental envelopes.

Values follow ASHRAE TC 9.9, *Thermal Guidelines for Data Processing Environments*,
5th edition (2021), as summarised in the TC 9.9 reference card. They are reproduced
here as **configurable engineering constraints**, not as a universal design target:
the engineer selects the class and whether the *recommended* or *allowable* envelope
is the hard constraint, and may override every limit. Verify against the current
edition of the guideline and the equipment manufacturer's specification before use.

Notes:
* The recommended upper humidity limit is 70 % RH in the 5th edition, reduced to
  50 % RH where gaseous contamination (corrosive) levels are elevated. The
  ``high_contamination`` flag applies that reduction.
* Maximum allowable dry-bulb is derated with altitude above 900 m
  (A1/A2: 1 °C per 300 m; A3: 1 °C per 175 m; A4: 1 °C per 125 m).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace


@dataclass(frozen=True)
class Envelope:
    name: str
    t_min_c: float
    t_max_c: float
    dp_min_c: float          # lower dew-point limit
    rh_min_pct: float        # lower relative-humidity limit (applied together with dp_min)
    dp_max_c: float          # upper dew-point limit
    rh_max_pct: float        # upper relative-humidity limit
    altitude_derate_m_per_k: float | None = None  # metres per 1 K derating above 900 m
    source: str = "ASHRAE TC9.9 Thermal Guidelines, 5th ed. (2021)"

    def derated_t_max(self, elevation_m: float) -> float:
        if self.altitude_derate_m_per_k is None or elevation_m <= 900.0:
            return self.t_max_c
        return self.t_max_c - (elevation_m - 900.0) / self.altitude_derate_m_per_k

    def to_dict(self) -> dict:
        return asdict(self)


RECOMMENDED_A = Envelope(
    name="Recommended (A1–A4)", t_min_c=18.0, t_max_c=27.0,
    dp_min_c=-9.0, rh_min_pct=0.0, dp_max_c=15.0, rh_max_pct=70.0,
)
RECOMMENDED_H1 = Envelope(
    name="Recommended (H1)", t_min_c=18.0, t_max_c=22.0,
    dp_min_c=-9.0, rh_min_pct=0.0, dp_max_c=15.0, rh_max_pct=70.0,
)

ALLOWABLE: dict[str, Envelope] = {
    "A1": Envelope("Allowable A1", 15.0, 32.0, -12.0, 8.0, 17.0, 80.0, 300.0),
    "A2": Envelope("Allowable A2", 10.0, 35.0, -12.0, 8.0, 21.0, 80.0, 300.0),
    "A3": Envelope("Allowable A3", 5.0, 40.0, -12.0, 8.0, 24.0, 85.0, 175.0),
    "A4": Envelope("Allowable A4", 5.0, 45.0, -12.0, 8.0, 24.0, 90.0, 125.0),
    "H1": Envelope("Allowable H1", 5.0, 25.0, -12.0, 8.0, 17.0, 80.0, 500.0),
}

CLASSES = tuple(ALLOWABLE.keys())


def get_envelope(equipment_class: str, mode: str = "recommended", high_contamination: bool = False,
                 overrides: dict | None = None) -> Envelope:
    """Return the envelope used as a hard constraint.

    mode: "recommended" or "allowable".
    overrides: dict of Envelope field overrides supplied by the engineer.
    """
    equipment_class = equipment_class.upper()
    if equipment_class not in ALLOWABLE:
        raise ValueError(f"Unknown ASHRAE class {equipment_class!r}; expected one of {CLASSES}")
    if mode == "recommended":
        env = RECOMMENDED_H1 if equipment_class == "H1" else RECOMMENDED_A
        env = replace(env, altitude_derate_m_per_k=ALLOWABLE[equipment_class].altitude_derate_m_per_k)
    elif mode == "allowable":
        env = ALLOWABLE[equipment_class]
    else:
        raise ValueError("mode must be 'recommended' or 'allowable'")
    if high_contamination and mode == "recommended":
        env = replace(env, rh_max_pct=min(env.rh_max_pct, 50.0),
                      name=env.name + " (high gaseous contamination: 50 % RH)")
    if overrides:
        env = replace(env, **{k: v for k, v in overrides.items() if v is not None},
                      name=env.name + " (engineer overrides)", source=env.source + "; modified by user")
    return env


def all_envelopes() -> list[dict]:
    out = [RECOMMENDED_A.to_dict(), RECOMMENDED_H1.to_dict()]
    out += [{"class": k, **v.to_dict()} for k, v in ALLOWABLE.items()]
    return out
