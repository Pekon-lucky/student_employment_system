// 模块 7 简历评估：只做配置，流程全部复用 core/ui.js 的 initReportPage
import { requireAuth } from "../core/api.js";
import { initNav, initReportPage } from "../core/ui.js";

// 路由守卫第二道防线（第一道是 <head> 内联同步脚本）
if (requireAuth()) {
  initNav();

  initReportPage({
    uploadKind: "resume",           // /api/files/upload 的 kind → 存 test_files/docs/
    uploadAccept: ".doc,.docx",
    maxSizeMB: 10,
    ssePath: "/api/resume/evaluate",
    buildBody: (up) => {
      if (!up) throw new Error("请先选择简历文件（.doc / .docx）");
      return { file_name: up.file_name, file_id: up.file_id };
    },
    historyPath: "/api/resume/history",
    historyLabel: (item) => item.input_data.file_name || "简历记录",
    reportTitle: "简历评估报告",
  });
}
