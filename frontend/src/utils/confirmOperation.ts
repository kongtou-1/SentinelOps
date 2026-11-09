import { ElMessageBox } from "element-plus";

export async function confirmOperation(message: string, title: string) {
  try {
    await ElMessageBox.confirm(message, title, {
      type: "warning",
      confirmButtonText: title,
      cancelButtonText: "取消",
    });
    return true;
  } catch (e) {
    if (e === "cancel" || e === "close") return false;
    throw e;
  }
}
