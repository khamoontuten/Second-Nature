/** API types (subset of the backend schemas used by the UI). Units follow the backend: SI, MWh, m³, t CO₂e. */

export type SourceTag = "user" | "default-assumption" | "measured" | "quoted" | "derived" | "retrieved";

export interface Assumption {
  path: string;
  value: unknown;
  source: SourceTag;
  description: string;
}

export interface ProjectSummary {
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string | null;
  current_version_no: number;
  default_dataset_id: string | null;
  is_demo: boolean;
  site: { name: string; latitude: number; longitude: number; elevation_m: number; timezone: string; country: string } | null;
}

export interface Project extends ProjectSummary {
  config: ProjectConfig;
  config_hash: string;
  assumptions: Assumption[];
  versions: { version_no: number; config_hash: string; note: string; created_at: string }[];
  datasets: { id: string; provider: string; product: string; data_nature: string; n_hours: number; start_utc: string; end_utc: string; distance_km: number | null }[];
  simulations: { id: string; status: string; created_at: string; scenarios: string[]; model_version: string }[];
  compatibility: { level: string; strategies: string[]; message: string }[];
}

export type ProjectConfig = Record<string, any> & {
  name: string;
  site: Record<string, any>;
  it: Record<string, any>;
  building: Record<string, any>;
  baseline: Record<string, any>;
  strategies: Record<string, Record<string, any>>;
  grid: Record<string, any>;
  economics: Record<string, any>;
};

export interface Job {
  id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  message: string;
  error: string | null;
  result: Record<string, string> | null;
}

export interface Stats { mean: number; median: number; min: number; max: number; p05: number; p50: number; p95: number; p99: number; n: number }

export interface ClimateSummary {
  dataset_id: string;
  provider: string;
  product: string;
  data_nature: string;
  provenance: Record<string, any>;
  quality: Record<string, any> | null;
  summary: {
    period: { start_utc: string; end_utc: string; hours: number; years: number[]; data_nature: string };
    completeness_pct: Record<string, number>;
    overall: Record<string, Stats>;
    monthly: Record<string, Record<"mean" | "median" | "min" | "max", number[]>>;
    design_conditions: Record<string, Record<string, number>>;
    free_cooling_hours_below_threshold: { threshold_c: number; hours: number; longest_run_h: number };
    hot_hours: { threshold_c: number; hours: number; longest_run_h: number };
    hot_humid_hours: { definition: string; hours: number; longest_run_h: number };
    heatwaves: { threshold_c: number; definition: string; count: number; max_duration_days: number; events: { start: string; days: number }[] };
    wind_rose: { sectors: string[]; speed_bins_ms: number[]; frequency_pct: number[][]; prevailing: string; calm_pct: number };
    solar: { months: number[]; hours: number[]; mean_ghi_wm2: number[][]; annual_kwh_m2: number } | null;
    rainfall: { monthly_mm: number[]; annual_mm: number; max_hourly_mm: number } | null;
    histograms: Record<string, { edges: number[]; counts: number[] }>;
    soil_layers: Record<string, any>;
    monthly_ground_temperatures_epw_modeled: Record<string, number[]>;
  };
}

export interface Series { index: string[]; columns: Record<string, (number | string | boolean | null)[]> }

export interface ScenarioRow {
  id: string;
  label: string;
  description: string;
  strategies: string[];
  objective: number | null;
  metrics: Record<string, number | null>;
  delta_abs: Record<string, number | null>;
  delta_pct: Record<string, number | null>;
  flags: { level: string; code: string; message: string }[];
  compatibility: { level: string; message: string }[];
  lifecycle?: Record<string, any>;
}

export interface ScenarioResult {
  scenario_id: string;
  label: string;
  strategies: string[];
  summary: Record<string, any>;
  diagnostics: Record<string, any>;
  flags: { level: string; code: string; message: string }[];
  inputs_hash: string;
  hourly_sha256: string;
}

export interface SimulationResults {
  id: string;
  project_id: string;
  dataset_id: string;
  status: string;
  job_id: string | null;
  model_version: string;
  config_hash: string;
  dataset_sha256: string;
  scenarios: string[];
  created_at: string;
  error: string | null;
  comparison?: Record<string, ScenarioRow>;
  recommendation?: Recommendation | null;
  hybrid_search?: any[];
  weights?: Record<string, number>;
  scenario_results?: ScenarioResult[];
  climate_summary?: ClimateSummary["summary"];
}

export interface Recommendation {
  reference: string;
  reference_metrics: Record<string, number | null>;
  weights: Record<string, number>;
  ranking: string[];
  method: string;
  candidates: Record<string, any>;
}

export interface Strategy {
  id: string;
  key: string;
  name: string;
  organism: string;
  category: string;
  biological_inspiration: string;
  physical_mechanism: string;
  architectural_translation: string[];
  required_climate: string;
  required_geometry: string;
  materials: string;
  auxiliary_electricity: string;
  water: string;
  model: string;
  operating_constraints: string;
  failure_modes: string[];
  maintenance: string;
  constructability: string;
  confidence: string;
  limitations: string;
}
