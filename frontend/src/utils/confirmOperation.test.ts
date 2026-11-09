import { beforeEach, expect, it, vi } from "vitest";
import { confirmOperation } from "./confirmOperation";

const confirm = vi.hoisted(() => vi.fn());
vi.mock("element-plus", () => ({ ElMessageBox: { confirm } }));
beforeEach(() => {
  confirm.mockReset();
});
it("确认后允许继续，弹窗按钮明确说明操作", async () => {
  confirm.mockResolvedValue("confirm");
  expect(await confirmOperation("停止当前任务？", "停止任务")).toBe(true);
  expect(confirm).toHaveBeenCalledWith("停止当前任务？", "停止任务", {
    type: "warning",
    confirmButtonText: "停止任务",
    cancelButtonText: "取消",
  });
});
it.each(["cancel", "close"])("%s 时返回 false", async (reason) => {
  confirm.mockRejectedValue(reason);
  expect(await confirmOperation("停止当前任务？", "停止任务")).toBe(false);
});
it("意外错误继续交给页面处理", async () => {
  confirm.mockRejectedValue(new Error("弹窗加载失败"));
  await expect(confirmOperation("停止当前任务？", "停止任务")).rejects.toThrow(
    "弹窗加载失败",
  );
});
