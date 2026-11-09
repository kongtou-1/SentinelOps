import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "@/api/client";
import { readKnowledgeValidityPage } from "./api";

vi.mock("@/api/client", async (original) => ({
  ...(await original<typeof import("@/api/client")>()),
  request: vi.fn(),
}));

let documents: { id: string }[];
let skills: { id: string }[];
const records = (prefix: string, count: number) =>
  Array.from({ length: count }, (_, id) => ({ id: `${prefix}-${id}` }));

beforeEach(() => {
  vi.clearAllMocks();
  documents = records("doc", 25);
  skills = records("skill", 30);
  vi.mocked(request).mockImplementation(async (path) => {
    const [resource, search] = path.split("?");
    const params = new URLSearchParams(search);
    const offset = Number(params.get("offset"));
    const limit = Number(params.get("limit"));
    const rows = resource === "/knowledge" ? documents : skills;
    return {
      data: rows.slice(offset, offset + limit),
      total: rows.length,
    } as Awaited<ReturnType<typeof request>>;
  });
});

describe("知识有效期分页", () => {
  it("跨文档和 Skill 边界翻页，记录不重复、不遗漏，且只请求当前页", async () => {
    const first = await readKnowledgeValidityPage(1, 20);
    const second = await readKnowledgeValidityPage(2, 20);
    const third = await readKnowledgeValidityPage(3, 20);
    expect([first.data.length, second.data.length, third.data.length]).toEqual([
      20, 20, 15,
    ]);
    expect([first.total, second.total, third.total]).toEqual([55, 55, 55]);
    expect(second.data.map((r) => r.kind)).toEqual([
      ...Array(5).fill("document"),
      ...Array(15).fill("skill"),
    ]);
    expect(
      [...first.data, ...second.data, ...third.data].map((r) => r.id),
    ).toEqual([...documents, ...skills].map((r) => r.id));
    expect(vi.mocked(request).mock.calls.map(([path]) => path)).toEqual([
      "/knowledge?offset=0&limit=20",
      "/skills?offset=0&limit=1",
      "/knowledge?offset=20&limit=20",
      "/skills?offset=0&limit=15",
      "/knowledge?offset=40&limit=20",
      "/skills?offset=15&limit=20",
    ]);
  });

  it("文档恰好满页时，下一页从第一条 Skill 开始", async () => {
    documents = records("doc", 20);
    skills = records("skill", 7);
    const result = await readKnowledgeValidityPage(2, 20);
    expect(result.data.map((r) => r.id)).toEqual(skills.map((r) => r.id));
    expect(result.total).toBe(27);
  });

  it("没有文档时仍可翻阅 Skill，并正确返回空列表", async () => {
    documents = [];
    const result = await readKnowledgeValidityPage(2, 20);
    expect(result.data).toHaveLength(10);
    expect(result.data[0]?.id).toBe("skill-20");
    expect(result.total).toBe(30);
    skills = [];
    expect(await readKnowledgeValidityPage(1, 20)).toEqual({
      data: [],
      total: 0,
    });
  });

  it.each([20, 50, 100])(
    "每页 %i 条时，末页及超出范围的页码仍返回正确总数",
    async (size) => {
      skills = [];
      const lastPage = Math.ceil(documents.length / size);
      const last = await readKnowledgeValidityPage(lastPage, size);
      expect(last.total).toBe(25);
      expect(last.data.map((r) => r.id)).toEqual(
        documents.slice((lastPage - 1) * size).map((r) => r.id),
      );
      expect(await readKnowledgeValidityPage(lastPage + 1, size)).toEqual({
        data: [],
        total: 25,
      });
    },
  );

  it("Skill 读取失败时不把不完整的数据作为完整分页返回", async () => {
    vi.mocked(request)
      .mockResolvedValueOnce({ data: documents.slice(0, 20), total: 25 } as any)
      .mockRejectedValueOnce(new Error("读取失败"));
    await expect(readKnowledgeValidityPage(1, 20)).rejects.toThrow("读取失败");
  });
});
