"""Physical constants (SI). Sources are given per constant."""

# Standard gravity, m/s² (CGPM 1901 conventional value).
G = 9.80665

# Stefan–Boltzmann constant, W/(m²·K⁴) (CODATA 2018, exact).
SIGMA = 5.670374419e-8

# Zero Celsius in kelvin.
T0_K = 273.15

# Standard sea-level atmospheric pressure, Pa.
P_STD = 101325.0

# Gas constant of dry air, J/(kg·K) (ASHRAE Handbook—Fundamentals 2017, Ch.1: 287.042).
R_DA = 287.042

# Ratio of molecular masses water/dry air used in the humidity-ratio equation (ASHRAE 2017 eq. 20).
EPS_W = 0.621945

# Specific heat of dry air at constant pressure, J/(kg·K) (ASHRAE 2017 Ch.1, eq. 30 coefficient).
CP_DA = 1006.0

# Specific heat of water vapour, J/(kg·K) (ASHRAE 2017 Ch.1, eq. 30 coefficient).
CP_V = 1860.0

# Latent heat of vaporisation at 0 °C used in the moist-air enthalpy equation, J/kg (ASHRAE 2017 eq. 30).
H_FG0 = 2_501_000.0

# Specific heat of liquid water, J/(kg·K) (≈ value at 20–30 °C).
CP_W = 4186.0

# Density of liquid water, kg/m³ (≈ value at 20 °C).
RHO_W = 998.2

# Seconds per hour.
S_PER_H = 3600.0

# Joules per kWh.
J_PER_KWH = 3.6e6

# Dynamic viscosity of air at ~20 °C, Pa·s (Incropera, Table A.4, interpolated).
MU_AIR = 1.81e-5

# Thermal conductivity of air at ~20 °C, W/(m·K) (Incropera, Table A.4).
K_AIR = 0.0257

# Prandtl number of air at ~20 °C (Incropera, Table A.4).
PR_AIR = 0.71


def latent_heat_vaporisation(t_c):
    """Latent heat of vaporisation of water, J/kg, as a linear fit valid 0–60 °C.

    h_fg ≈ 2 501 000 − 2 370·t  (fits steam-table values within ~0.2 % over 0–60 °C).
    """
    return 2_501_000.0 - 2370.0 * t_c
