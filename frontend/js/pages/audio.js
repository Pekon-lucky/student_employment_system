// 模块 8 录音分析：只做配置，流程全部复用 core/ui.js 的 initReportPage
import { requireAuth } from "../core/api.js";
import { initNav, initReportPage } from "../core/ui.js";

// 路由守卫第二道防线（第一道是 <head> 内联同步脚本）
if (requireAuth()) {
  initNav();

  initReportPage({
    uploadKind: "audio",            // /api/files/upload 的 kind → 存 test_files/audio/
    uploadAccept: ".mp3,.wav",
    maxSizeMB: 20,
    ssePath: "/api/audio/analyze",
    buildBody: (up) => {
      if (!up) throw new Error("请先选择录音文件（.mp3 / .wav）");
      return { file_name: up.file_name, file_id: up.file_id };
    },
    historyPath: "/api/audio/history",
    historyLabel: (item) => item.input_data.file_name || "录音记录",
    reportTitle: "录音分析报告",
  });
}
