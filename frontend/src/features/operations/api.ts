import { query, request } from "@/api/client";
import type { Row } from "@/types/records";

export async function readKnowledgeValidityPage(
  page: number,
  pageSize: number,
): Promise<{ data: Row[]; total: number }> {
  const offset = (page - 1) * pageSize;
  const docs = await request<Row[]>(
    "/knowledge" + query({ offset, limit: pageSize }),
  );
  const documentTotal = docs.total ?? docs.data.length;
  const remaining = pageSize - docs.data.length;
  const skills = await request<Row[]>(
    "/skills" +
      query({
        offset: Math.max(0, offset - documentTotal),
        // A full document page still needs the Skill total for the pager.
        limit: remaining || 1,
      }),
  );
  return {
    data: [
      ...docs.data.map((r) => ({ ...r, kind: "document" })),
      ...(remaining ? skills.data : []).map((r) => ({ ...r, kind: "skill" })),
    ],
    total: documentTotal + (skills.total ?? skills.data.length),
  };
}
