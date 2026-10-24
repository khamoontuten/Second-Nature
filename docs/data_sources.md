# Data sources, licences and access

Every dataset stored by Second Nature carries a provenance record. It holds the provider, product,
endpoint or file, retrieval time, requested and actual coordinates, distance, elevation difference,
spatial and temporal resolution, time zone, reference period, transformations, licence, citation and
SHA-256. Fallbacks between providers are recorded; a fallback is never silent.

## Climate

| Provider | Product | Access | Licence / terms |
| --- | --- | --- | --- |
| Bundled reference files | NREL TMY3 typical meteorological years (8 US stations) in EPW format, from the EnergyPlus repository | offline, `data/reference/weather/*.epw.gz` with `SHA256SUMS` | NREL/DOE public data; EnergyPlus repository licence (BSD-3-Clause-style). Verify before commercial redistribution. Citation: Wilcox & Marion (2008), NREL/TP-581-43156 |
| Open-Meteo | historical archive (ERA5-based reanalysis grid) | `archive-api.open-meteo.com`, no key | CC BY 4.0; attribution required; free tier for non-commercial use |
| NASA POWER | hourly meteorology (MERRA-2 / GEOS based) | `power.larc.nasa.gov`, no key | NASA open data; acknowledge NASA POWER |
| Copernicus CDS | ERA5 / ERA5-Land hourly | `cds.climate.copernicus.eu`, needs `SN_CDS_API_KEY` (personal access token from your CDS profile, after accepting the dataset licence) | Copernicus licence; attribution required |
| Uploads | EPW, CSV (column mapping + units), NetCDF | file upload with size/type checks | user's responsibility |

The bundled stations are Dulles VA (Ashburn), Phoenix AZ, Las Vegas NV, Miami FL, Tampa FL,
San Francisco CA, Chicago IL and Golden CO.

TMY data are a composite of representative months from different years (generally 1973–2005) and
understate extremes. The platform labels them "typical_year", never as an observed year.

## Grid carbon intensity

* **Manual annual or monthly factor:** must cite its source, e.g. the EPA eGRID subregion output rate.
  Average and marginal factors, and location- and market-based accounting, are labelled.
* **Uploaded hourly series:** aligned to climate hours; unmatched hours are left as NaN.
* **Electricity Maps adapter:** hourly intensity; needs `SN_ELECTRICITY_MAPS_API_KEY`.

Without a factor, operational carbon is reported as *insufficient data*, not zero. The demo project
uses a 0.35 kg/kWh placeholder that is labelled as an assumption.

## Water context

WRI Aqueduct indicators can be imported from CSV or entered manually. The basin-level score is used
only for context and weighting, never as a site permit. Permitted annual and daily withdrawal, when
entered, are hard constraints.

## Soil

Default soil thermal properties come from Incropera *Fundamentals of Heat and Mass Transfer* Table A.3
and are tagged as assumptions. Measured conductivity, density, heat capacity and ground temperature
replace them when entered.

## Costs and space (impact analysis)

* **Unit rates:** supplied per project (`economics.unit_rates`) with source, basis year and range. The
  platform has no built-in price database and invents none.
* **Demo project:** ships *illustrative* rates labelled as not market prices.
* **Layout ratios:** (`layout.*`) are planning assumptions tagged *default-assumption* until edited.

## Hosts blocked while this version was built

The build environment's network policy returned HTTP 403 for:

* `archive-api.open-meteo.com` and `geocoding-api.open-meteo.com`
* `power.larc.nasa.gov`
* `cds.climate.copernicus.eu`
* `nominatim.openstreetmap.org`
* the OpenStreetMap tile servers used by the site map

Those adapters are therefore tested against mocked HTTP responses (`respx`). They are untested against
the live services in this build. On a network that allows these hosts they work without code changes.
In a Claude Code cloud environment, the hosts can be allowed in the environment's network settings.
