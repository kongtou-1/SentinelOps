import { describe, expect, it } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import PageMetrics from "./PageMetrics.vue";

describe("标题关键指标的真实值与不可用状态", () => {
  it("真实零显示为零，缺失及非有限数值显示为横线", async () => {
    const html = await renderToString(
      createSSRApp(PageMetrics, {
        items: [
          { label: "失败", value: 0 },
          { label: "未知", value: null },
          { label: "无效", value: NaN },
        ],
      }),
    );
    expect(html).toMatch(/>0<\/dd>/);
    expect(html.match(/>—<\/dd>/g)).toHaveLength(2);
  });
  it.each([{ ready: false }, { error: true }])(
    "加载前或失败时不显示旧统计：%j",
    async (state) => {
      const html = await renderToString(
        createSSRApp(PageMetrics, {
          ...state,
          items: [{ label: "旧统计", value: 137 }],
        }),
      );
      expect(html).not.toContain(">137</dd>");
      expect(html).toMatch(/>—<\/dd>/);
    },
  );
});
