import type { ListConfig } from "@/types/records";
import { resourcesLists } from "@/features/resources/list.config";
import { predictionsLists } from "@/features/predictions/list.config";
import { alertsLists } from "@/features/alerts/list.config";
import { incidentsLists } from "@/features/incidents/list.config";
import { actionsLists } from "@/features/actions/list.config";
import { reportsLists } from "@/features/reports/list.config";
import { knowledgeLists } from "@/features/knowledge/list.config";
import { systemLists } from "@/features/system/list.config";

export const configs: Record<string, ListConfig> = {
  ...resourcesLists,
  ...predictionsLists,
  ...alertsLists,
  ...incidentsLists,
  ...actionsLists,
  ...reportsLists,
  ...knowledgeLists,
  ...systemLists,
};
