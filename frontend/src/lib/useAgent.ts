import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { AgentResult } from "./types";

/** The stored result of one agent of a run. */
export function useAgent(runId: string, agent: string, enabled: boolean) {
  return useQuery({
    queryKey: ["agent", runId, agent],
    queryFn: () => api.get<{ result: AgentResult | null }>(`/api/runs/${runId}/agents/${agent}`),
    enabled,
    select: (d) => d.result,
  });
}
