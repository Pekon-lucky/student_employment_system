// 模块 9 面试题生成：双入口（粘贴文本优先 / 上传简历文件），流程复用 initReportPage
import { requireAuth } from "../core/api.js";
import { $, initNav, initReportPage } from "../core/ui.js";

// 路由守卫第二道防线（第一道是 <head> 内联同步脚本）
if (requireAuth()) {
  initNav();

  initReportPage({
    uploadKind: "resume",           // 修改稿仍是简历文件，走 docs 目录
    uploadAccept: ".doc,.docx",
    maxSizeMB: 10,
    ssePath: "/api/questions/generate",
    // 文本优先：有文本就不必再上传文件（省一次往返与一份磁盘占用）
    skipUpload: () => $("#resume-text").value.trim().length > 0,
    buildBody: (up) => {
      const text = $("#resume-text").value.trim();
      if (text) return { resume_text: text };
      if (up) return { file_name: up.file_name, file_id: up.file_id };
      throw new Error("请粘贴简历文本，或选择一份简历文件");
    },
    historyPath: "/api/questions/history",
    historyLabel: (item) =>
      item.input_data.source === "text" ? "文本输入" : (item.input_data.file_name || "文件输入"),
    reportTitle: "面试题生成结果",
  });
}
