# Strategy impact analysis

Page **5 · Impact analysis** and the `app/impacts` package let an engineer evaluate every implemented
cooling strategy on its own, combine any compatible set, and compare the outcome with one conventional
baseline. The comparison covers energy, water, carbon, capital cost, space and thermal feasibility.

## 1. Audit findings that shaped the design

| Area | Before this work | Decision |
| --- | --- | --- |
| Baseline | Scenario S0 = project configuration with every strategy off (`derive(cfg, ALL_OFF)` inside `run_scenarios`) | Formalised as `app.simulation.scenarios.baseline_config()`; used by both the scenario runner and the impact engine |
| Strategy catalogue | Library A–I (text) + candidate patches for the hybrid search; direct evaporative cooling and full containment were modelled but not exposed as candidates | 14 measures, each defined by the configuration switch the engine already understands (`app/impacts/catalogue.py`) |
| Combination logic | Greedy forward search in S7 only | Arbitrary user selection, simulated as one configuration |
| Cost | Per-strategy lump sums only; missing → "data gap" | Quantity take-off × unit rates with low/base/high, source and basis year; lump sums still honoured |
| Space | EAHE/chimney land estimate inside the optimiser | Separate categories (building, IT, indoor M&E, outdoor yard, roof, facade zone, underground) |
| Plant sizing | Same rule for every scenario; design hour, no passive credit | Unchanged; CAPEX for the plant therefore changes only through modelled design-airflow/load changes |

## 2. Data flow

```
UI selection (strategy ids + parameter overrides)
  → POST /api/impact-studies  {kind: baseline | individual | combination | pairwise}
  → catalogue.select(): unknown ids / out-of-range parameters rejected (422)
  → background job (app.jobs) → app.impacts.engine.run_study()
       baseline = baseline_config(project config at the stored version)
       for each scenario:
         catalogue.resolve()      dependencies added (e.g. stack → full containment), exclusive groups checked
         derive(baseline, patch)  same climate record, IT profile, class, redundancy, sizing rule
         compatibility_issues()   conflicts → infeasible, not simulated
         run_simulation()         hourly physics, controller, plant, water, carbon (hourly grid series if supplied)
         quantities.takeoff()     configuration geometry + simulated sizing/water
         costing.estimate()       CAPEX lines, contingency, missing rates
         costing.opex()           tariffs × simulated energy/water, user maintenance
         space.estimate()         categories, roof/site/buried-land checks
         hard constraints         unmet load, inlet envelope, water caps, roof/site/land → infeasible
         metrics.compare()        absolute, Δ, % (safe), status label, assessment
       combination: + attribution (individual Δ, Σ, combined Δ, non-additive remainder)
       pairwise:   + every pair simulated → interaction I = Δ_AB − Δ_A − Δ_B and class
  → stored in impact_studies (config version, config hash, dataset SHA-256, model version)
  → GET /api/impact-studies/{id}; export ?format=json|csv|xlsx
```

Identical configurations inside one study are simulated once (memoised by configuration hash), and the
EAHE/envelope physics caches are shared, so a full individual study of the 14 measures takes about
10 s on the demonstration project.

## 3. Supported strategies

| Id | Strategy | Category | Requires | Exclusive group |
| --- | --- | --- | --- | --- |
| `containment_full` | Full hot/cold-aisle containment | conventional enabling measure | — | — |
| `economiser_direct` | Direct air-side economiser | conventional enabling measure | — | economiser |
| `economiser_indirect` | Indirect air-side economiser | conventional enabling measure | — | economiser |
| `stack` | Termite-mound-inspired stack ventilation (A) | biomimetic | `containment_full` | — |
| `solar_chimney` | Passive solar chimney (E) | biomimetic | `stack` | — |
| `envelope` | Mollusc-shell-inspired layered envelope (B) | biomimetic | — | — |
| `facade_fixed` | Fixed fish-scale shading (C) | biomimetic | — | facade |
| `facade_kinetic` | Fish-scale-inspired kinetic facade (C) | biomimetic | — | facade |
| `eahe` | Prairie-dog-burrow-inspired earth-air heat exchanger (D) | biomimetic | — | — |
| `storage` | Thermal-mass buffering / chilled-water storage (F) | biomimetic | chilled-water plant | — |
| `radiative` | Radiative sky-cooling panels (G) | biomimetic | chilled-water plant, roof area | — |
| `iec` | Indirect evaporative cooling (H) | biomimetic | water | evaporative |
| `evap_direct` | Direct evaporative cooling (H) | biomimetic | water | evaporative |
| `evap_precool` | Evaporative condenser pre-cooling (H) | biomimetic | air-cooled condensers, water | evaporative |

The adaptive supervisory controller (library strategy I) is part of every evaluation and is not a
toggle. Applicability is decided per project by the engine's own compatibility rules. For example,
storage and radiative panels are not applicable to DX plants, and evaporative measures are not
applicable when permitted water withdrawal is zero.

## 4. Metrics and status labels

| Status | Meaning |
| --- | --- |
| Modelled | computed by an implemented physics or plant model (hourly simulation) |
| Derived | calculated from modelled outputs (ratios, tariffs × energy, grid factor × energy) |
| Estimated | quantity take-off × unit rates, or layout ratios; reported with a range |
| Insufficient data | supported by the model, but an input is missing; no value is substituted |
| Not modelled | the application cannot determine the quantity |
| Not applicable | the metric does not apply to the configuration (e.g. natural airflow without a stack) |
| Infeasible | the scenario violates a hard constraint and is not ranked |

| Group | Metrics | Status |
| --- | --- | --- |
| Energy | cooling, IT, other facility, total facility electricity; peak facility and cooling power | modelled |
| Energy | PUE (annual energy ratio) | derived |
| Water | withdrawal, consumption | modelled |
| Water | WUE (withdrawal and consumption bases) | derived |
| Carbon | operational CO₂e | derived (insufficient data without a grid factor) |
| Carbon | embodied CO₂e of added strategy materials | estimated (needs emission factors; base building excluded) |
| Cost | CAPEX | estimated (needs unit rates) |
| Cost | annual electricity and water cost | derived (needs tariffs) |
| Cost | annual maintenance of added strategies | estimated (needs maintenance input) |
| Space | building footprint, IT white space, indoor M&E, outdoor yard, at-grade total, roof, facade zone | estimated, with derived geometric parts |
| Space | underground field area, excavation volume | derived |
| Thermal | heat removed without refrigeration, residual mechanical cooling, free-cooling hours, peak coil load, installed capacity, unmet and violation hours, maximum inlet temperature | modelled |
| Thermal | natural-airflow sufficiency hours | modelled (stack only) |
| Not modelled | structural reinforcement, fire/smoke control for shafts, acoustic treatment, planning/permitting cost, embodied carbon of the base building | not modelled; listed as exclusions |

**Percentage change** is `(scenario − baseline) / baseline × 100` and is never computed for a zero baseline.
Water and WUE percentages are also withheld when the baseline is below 1 % of a 0.5 L/kWh reference
(for example, an air-cooled baseline that withdraws only ~7 m³/yr for humidification). In those cases
the absolute change is shown with the reason.

**Interaction** for a pair is `I = Δ_AB − Δ_A − Δ_B` (each Δ relative to the baseline, in metric units). It is
classified as additive (|I| ≤ 2 % of |Δ_A| + |Δ_B|), overlapping (both reduce; together less than the sum
but more than the better one), complementary (better than additive) or conflicting (worse than the better
single strategy, or worse than additive). This is a diagnostic of non-additivity, not a causal attribution.

## 5. Cost model (`app/impacts/costing.py`)

`CAPEX = Σ quantity × unit rate × (1 + contingency)`, evaluated separately for the baseline and the scenario.

* Baseline lines: building shell (m² GFA), power train and white-space fit-out (kW IT), mechanical plant
  (installed kW_th including redundancy), towers/dry coolers (kW_th rejected), CRAH (installed m³/s).
* Shared lines, sized once for the largest user:
  * outdoor-air intake (m³/s)
  * water treatment (m³/day peak withdrawal)
  * supervisory controller (one per facility)
  * controls integration (per controlled subsystem)
* Strategy lines:
  * containment (per rack); stack shafts (m³) and outlet dampers (m²); solar collectors (m²)
  * EAHE pipe (m), excavation (m³) and fans (m³/s)
  * insulation and thermal mass (m³); cool roof (m²); fixed or kinetic facade (m²)
  * storage (kWh_th); radiative panels (m²)
  * IEC, DEC and indirect-economiser units (m³/s); pre-cooling media (kW_th)
* **Incremental CAPEX** is computed line by line. Unchanged quantities cancel exactly even when a rate
  is missing, so an incremental estimate can be complete while the baseline total is not. Avoided costs
  appear automatically when a scenario needs less of a line (e.g. full containment lowers design
  airflow, so the CRAH and plant lines shrink).
* Unit rates come only from `economics.unit_rates`. Each rate carries low/base/high, source
  (quoted / measured / reference / assumed), basis year, optional service life and a note. The
  platform supplies no default rates.
* The **demonstration project** ships illustrative order-of-magnitude rates (USD, 2026 basis), each
  labelled "ILLUSTRATIVE DEMO VALUE — not a market price, quote or cost-database value". They exist only
  so the workflow can be exercised and must be replaced with local quotes.
* **OPEX** = simulated electricity × tariff + water × tariff + user-supplied maintenance of the added
  strategies. Baseline plant maintenance is common to all scenarios and excluded.
* **Payback and NPV** are reported only when all of these are known: incremental CAPEX, tariff,
  maintenance of every added strategy, discount rate and horizon. Replacement of components with a
  service life shorter than the horizon is included. Otherwise the result is "insufficient data",
  naming what is missing.
* Exclusions: land, design and permitting fees, IT equipment, financing, escalation beyond the rate
  basis year, and structural/fire/planning items not listed per line.

## 6. Space model (`app/impacts/space.py`)

* Configured geometry is used exactly (building length × width, chimney cross-sections, panel and
  collector areas, EAHE field = pipes × spacing × length).
* Ratio-based items come from `ProjectConfig.layout` (all editable, default-assumption tagged) with a
  ±30 % range:
  * white space per rack; electrical rooms per kW IT; CRAH gallery per m³/s
  * chiller room or yard per kW_th; air-handling units per m³/s; outdoor-air plenum per m³/s
  * TES tank height and clearance; water treatment per m³/day
* Categories are never summed across boundaries of different kind. The at-grade site footprint is the
  building footprint plus the outdoor equipment yard. Roof area, the facade projection zone and the
  underground field are reported separately; an underground field is not treated as free surface.
* Checks:
  * roof equipment larger than the roof → infeasible
  * at-grade footprint larger than the site area → infeasible
  * underground field larger than the land available for buried infrastructure → infeasible
  * IT + M&E area implied by the ratios larger than the building's gross floor area → warning
    (inconsistent assumptions)

## 7. Reproducing the demonstration

```bash
# backend running (see README), then:
curl -X POST "http://127.0.0.1:8000/api/demo?site_id=ashburn_va&run=false"
```

1. Open **5 · Impact analysis**, review **A · Baseline**, click **Run baseline**.
2. In **C · Combination builder**, switch on *Termite-mound-inspired stack ventilation* (full containment
   is added as a dependency) → **Run combination** → read **D**.
3. **Reset to baseline**, switch on *Prairie-dog-burrow-inspired earth-air heat exchanger* → run.
4. **Run individual impact study** (A) to see every strategy side by side in **B**.
5. Switch both on → run → compare in **D** and **E** (each alone, Σ of individual changes, simulated
   combination, non-additive remainder).
6. Change *Pipe length* to 120 m → run again; the underground area doubles and CAPEX rises.
7. Select 3–4 strategies → **Pairwise interactions** → matrix in **E**.
8. **F · Export**: XLSX / CSV / JSON for any study.

`frontend/e2e/impacts.mjs` automates these steps through the UI and checks every displayed number
against the stored API result.

## 8. Findings from the demonstration (Ashburn TMY3, illustrative assumptions)

These are outputs of this model for this demo input set. They are not general claims about the
strategies.

* Stack ventilation (with the full containment it requires) reduced facility electricity by 11.3 %.
  Indirect evaporative cooling gave a similar saving but added about 7,260 m³/yr of water withdrawal.
* EAHE alone saved 3.3 %, but **nothing on top of the stack**. The 836 hours left on mechanical cooling
  with the stack have a mean dew point of 21 °C, and 99.6 % of them have a dew point above the 15 °C
  limit, which the EAHE cannot condition. Its standalone benefit (2,778 h at a mean 19.9 °C) falls in
  hours the stack already covers. The pair is therefore classed "overlapping".
* The envelope strategy slightly **increased** cooling energy (+21.5 MWh/yr). For an internally
  dominated building, better insulation also reduces heat loss to cool outdoor air.
* Interaction through a shared dependency: full containment (required by the stack) lowers design
  airflow from 421 to 369 m³/s. In stack + IEC the IEC units are therefore sized smaller, so the
  at-grade footprint change is about 160 m² less than the sum of the two individual changes.
* A controller limitation is visible: the minimum-run-time rule can hold an outdoor-air mode for one
  hour after the dew point jumps, so the coil dehumidifies outdoor air. This raised the peak coil load
  in some hybrids above the baseline peak (≈13 h/yr, 0.6 MWh/yr in S7), still within installed capacity.
