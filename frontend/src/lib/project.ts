import { useParams } from "react-router-dom";
import { useActiveSimulationId, useProject, useSimulation } from "../api/hooks";

/** Project, active simulation and dataset for the current page. */
export function useProjectPage() {
  const { projectId } = useParams();
  const project = useProject(projectId);
  const simId = useActiveSimulationId(project.data);
  const sim = useSimulation(simId);
  const results = sim.data?.status === "succeeded" ? sim.data : undefined;
  const datasetId = results?.dataset_id ?? project.data?.default_dataset_id ?? null;
  const tz = project.data?.config.site.timezone as string | undefined;
  const elevation = project.data?.config.site.elevation_m as number | undefined;
  return { projectId: projectId!, project, simId, sim, results, datasetId, tz, elevation };
}

/** Scenario that scored best on the weighted objective (excluding the user-defined S8). */
export function bestScenario(comparison?: Record<string, { objective: number | null }>): string | undefined {
  if (!comparison) return undefined;
  const ids = Object.keys(comparison).filter((k) => k !== "S8" && comparison[k].objective !== null && Number.isFinite(comparison[k].objective!));
  return ids.sort((a, b) => (comparison[b].objective ?? -Infinity) - (comparison[a].objective ?? -Infinity))[0];
}
