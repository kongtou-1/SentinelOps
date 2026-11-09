import { describe, expect, it } from "vitest";
import { csvText } from "./csv";

describe("业务 CSV 导出", () => {
  it("保留中文、引号、逗号、换行和空值，带 Excel 可识别的 UTF-8 BOM", () => {
    expect(
      csvText(
        [
          { label: "说明", value: (r: { text: string }) => r.text },
          { label: "缺失值", value: () => null },
        ],
        [{ text: '中文,"第一行"\n第二行' }],
      ),
    ).toBe('\uFEFF"说明","缺失值"\r\n"中文,""第一行""\n第二行",""');
  });
  it("阻止用户文本被电子表格解释为公式，数值负数保持原值", () => {
    const columns = [
      { label: "内容", value: (value: string | number) => value },
    ];
    expect(
      csvText(columns, ["=1+1", " +SUM(A1)", "-1", "@SUM(A1)", "\t=1", -12, 0]),
    ).toBe(
      '\uFEFF"内容"\r\n"\'=1+1"\r\n"\' +SUM(A1)"\r\n"\'-1"\r\n"\'@SUM(A1)"\r\n"\'\t=1"\r\n"-12"\r\n"0"',
    );
  });
  it("仅输出明确选择的列，不导出行中的认证、原始证据或调试字段", () => {
    const rows = [
      {
        title: "业务记录",
        password: "test-secret",
        evidence: { authorization: "test-token" },
      },
    ];
    const result = csvText(
      [{ label: "标题", value: (row: { title: string }) => row.title }],
      rows,
    );
    expect(result).toBe('\uFEFF"标题"\r\n"业务记录"');
  });
});
