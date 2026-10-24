import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { ClimateSummary, Job, Project, ProjectSummary, Series, SimulationResults, Strategy } from "../types/api";
import { api } from "./client";

export const useProjects = () => useQuery({ queryKey: ["projects"], queryFn: () => api.get<ProjectSummary[]>("/api/projects") });

export const useProject = (id?: string) =>
  useQuery({ queryKey: ["project", id], queryFn: () => api.get<Project>(`/api/projects/${id}`), enabled: !!id });

export const useStrategies = () =>
  useQuery({ queryKey: ["strategies"], queryFn: () => api.get<{ strategies: Strategy[]; compatibility: any[] }>("/api/strategies"), staleTime: Infinity });

export const useReferenceSites = () =>
  useQuery({ queryKey: ["reference-sites"], queryFn: () => api.get<{ sites: any[]; description: string; licence: string }>("/api/climate/reference-sites"), staleTime: Infinity });

export const useProviders = () => useQuery({ queryKey: ["providers"], queryFn: () => api.get<any[]>("/api/climate/providers"), staleTime: 60_000 });

export const useClimateSummary = (datasetId?: string | null, tz?: string, elevation?: number) =>
  useQuery({
    queryKey: ["climate-summary", datasetId, tz, elevation],
    queryFn: () => api.get<ClimateSummary>(`/api/climate/${datasetId}/summary`, { timezone: tz, elevation_m: elevation }),
    enabled: !!datasetId,
    staleTime: Infinity,
  });

export const useClimateSeries = (datasetId?: string | null, variables?: string, step = 1, elevation?: number) =>
  useQuery({
    queryKey: ["climate-series", datasetId, variables, step],
    queryFn: () => api.get<Series>(`/api/climate/${datasetId}/timeseries`, { variables, step, elevation_m: elevation }),
    enabled: !!datasetId,
    staleTime: Infinity,
  });

export const useSimulation = (id?: string | null) =>
  useQuery({
    queryKey: ["simulation", id],
    queryFn: () => api.get<SimulationResults>(`/api/simulations/${id}/results`),
    enabled: !!id,
    staleTime: Infinity,
  });

export const useHourly = (simId?: string | null, scenario?: string, columns?: string, step = 1) =>
  useQuery({
    queryKey: ["hourly", simId, scenario, columns, step],
    queryFn: () => api.get<Series>(`/api/simulations/${simId}/scenarios/${scenario}/hourly`, { columns, step }),
    enabled: !!simId && !!scenario,
    staleTime: Infinity,
  });

/** Poll a background job until it finishes. */
export function useJob(jobId?: string | null) {
  return useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.get<Job>(`/api/jobs/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (q) => (q.state.data && ["succeeded", "failed", "cancelled"].includes(q.state.data.status) ? false : 800),
  });
}

/** The simulation the pages show: chosen via ?sim=… else the latest succeeded run of the project. */
export function useActiveSimulationId(project?: Project) {
  const [simId, setSimId] = useState<string | null>(null);
  useEffect(() => {
    const fromUrl = new URLSearchParams(window.location.search).get("sim");
    if (fromUrl) setSimId(fromUrl);
    else setSimId(project?.simulations.find((s) => s.status === "succeeded")?.id ?? null);
  }, [project]);
  return simId;
}

export function usePatchProject(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { patch: Record<string, unknown>; note?: string; default_dataset_id?: string }) => api.patch<Project>(`/api/projects/${id}`, body),
    onSuccess: (p) => {
      qc.setQueryData(["project", id], (old: Project | undefined) => (old ? { ...old, ...p } : old));
      qc.invalidateQueries({ queryKey: ["project", id] });
    },
  });
}
