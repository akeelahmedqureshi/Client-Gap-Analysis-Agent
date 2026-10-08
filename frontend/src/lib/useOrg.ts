import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { OrgSettings } from "./types";

/** Organization governance settings, including whether the current user may export. */
export function useOrgSettings() {
  return useQuery({ queryKey: ["org-settings"], queryFn: () => api.get<OrgSettings>("/api/org/settings") });
}

export function useCanExport(): boolean {
  return !!useOrgSettings().data?.can_export;
}
