// 模块 7/8/9 公共 UI + 聊天组件（PRD 7.3 DRY 核心）
// 依赖：页面需引入 marked.js CDN（全局 window.marked）；本模块不直接操作业务数据。
import { logout, request } from "./api.js";
import { streamSSE } from "./sse.js";
import { showToast } from "./toast.js";

/** HTML 转义：用户输入/文件名等不可信内容一律先转义再入 DOM（防注入，PRD 7.4） */
export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

/** Markdown 渲染：AI 输出（受控内容）走 marked；marked 未加载时退化为转义纯文本 */
export function renderMarkdown(text) {
  return window.marked ? window.marked.parse(String(text ?? "")) : escapeHtml(text);
}

/** 简易选择器 */
export const $ = (sel) => document.querySelector(sel);

/**
 * 初始化顶部导航头像菜单（业务页面共用，避免 4 个页面重复绑定）
 * @returns {Promise<object|null>} 当前用户对象（含 id/username）；失败返回 null
 */
export async function initNav() {
  const avatar = $("#avatar");
  const dropdown = $("#dropdown");
  if (!avatar || !dropdown) return null;

  avatar.addEventListener("click", (e) => {
    e.stopPropagation(); // 避免被 document 点击监听立即收起
    dropdown.classList.toggle("show");
  });
  document.addEventListener("click", () => dropdown.classList.remove("show"));

  const logoutLink = $("#menu-logout");
  if (logoutLink) {
    logoutLink.addEventListener("click", (e) => {
      e.preventDefault();
      logout();
    });
  }

  try {
    const { user } = await request("/api/users/me");
    avatar.textContent = user.username.charAt(0).toUpperCase();
    return user;
  } catch {
    return null; // 401 已由 request 统一跳转登录页
  }
}

// ---------------------------------------------------------------------------
// 聊天组件（模块 10 / 调试页共用）
// ---------------------------------------------------------------------------

/**
 * 追加一条聊天气泡
 * @param {HTMLElement} listEl 消息列表容器
 * @param {"user"|"ai"} role   user 右侧纯文本转义；ai 左侧 Markdown 渲染
 * @param {string} content     消息内容
 * @param {{streaming?: boolean}} [opts] streaming=true 注入三点打字指示器（pitfalls #7）
 * @returns {HTMLElement} 气泡元素（供 renderMarkdownStream 流式更新）
 */
export function appendChatMessage(listEl, role, content, { streaming = false } = {}) {
  const bubble = document.createElement("div");
  bubble.className = `chat-msg ${role === "user" ? "msg-user" : "msg-ai"}`;
  const inner = document.createElement("div");
  inner.className = "chat-bubble";
  if (role === "user") {
    inner.textContent = content; // textContent 天然转义
  } else if (streaming) {
    inner.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
  } else {
    inner.innerHTML = renderMarkdown(content);
  }
  bubble.appendChild(inner);
  listEl.appendChild(bubble);
  listEl.scrollTop = listEl.scrollHeight;
  return bubble;
}

/**
 * 流式渲染：delta 追加后整体重渲（实现最简、体验一致，PRD 7.3）
 * 自动移除气泡内的打字指示器；渲染后滚动到底部。
 * @param {HTMLElement} bubble appendChatMessage 返回的气泡元素
 * @param {string} acc 已累计的完整 Markdown 文本
 */
export function renderMarkdownStream(bubble, acc) {
  const inner = bubble.querySelector(".chat-bubble");
  inner.querySelector(".typing")?.remove();
  inner.innerHTML = renderMarkdown(acc);
  const list = bubble.closest(".chat-list");
  if (list) list.scrollTop = list.scrollHeight;
}

// ---------------------------------------------------------------------------
// 模块 7/8/9 报告页通用流程
// ---------------------------------------------------------------------------

/**
 * 一份配置跑通"选文件 → 上传 → SSE 流式打字机 → 落库刷新历史"全流程（PRD 7.3）
 *
 * 页面只需按约定放好 DOM 并传配置：
 *   initReportPage({
 *     uploadKind: "resume",                 // /api/files/upload 的 kind
 *     uploadAccept: ".doc,.docx",           // 文件选择器过滤
 *     maxSizeMB: 10,
 *     ssePath: "/api/resume/evaluate",      // 工作流 SSE 端点
 *     buildBody: (up) => ({ file_name: up.file_name, file_id: up.file_id }),
 *                                           // up=null 表示纯文本模式（模块 9）
 *     skipUpload: () => true,               // 可选：不需要上传时跳过（模块 9 文本模式）
 *     historyPath: "/api/resume/history",
 *     historyLabel: (item) => item.input_data.file_name || item.created_at,
 *     reportTitle: "简历评估报告",
 *   });
 *
 * 约定的 DOM id：#file-input #file-name #start-btn #report-panel #history-list
 */
export function initReportPage(cfg) {
  const fileInput = $("#file-input");
  const fileNameEl = $("#file-name");
  const startBtn = $("#start-btn");
  const reportPanel = $("#report-panel");
  const historyList = $("#history-list");

  if (fileInput) {
    fileInput.accept = cfg.uploadAccept || "";
    fileInput.addEventListener("change", () => {
      const f = fileInput.files[0];
      if (fileNameEl) fileNameEl.textContent = f ? f.name : "未选择文件";
    });
  }

  /** 渲染左侧历史列表；点击条目回看详情（渲染 result_data 的 markdown） */
  function refreshHistory() {
    request(cfg.historyPath)
      .then((items) => renderHistoryList(historyList, items, (item) => {
        const data = item.result_data || {};
        const text = data.report ?? data.questions_markdown ?? "";
        reportPanel.innerHTML = `<div class="report-body">${renderMarkdown(text)}</div>`;
      }, cfg.historyLabel))
      .catch((e) => showToast(e.message, 2000));
  }

  startBtn.addEventListener("click", async () => {
    if (startBtn.disabled) return;
    let uploaded = null;
    try {
      const file = fileInput?.files[0];
      const skipUpload = cfg.skipUpload?.() === true;
      if (file && !skipUpload) {
        // 前置校验（扩展名 + 大小，PRD 10 风险 4：平台限制前端先挡一道）
        const ext = "." + file.name.split(".").pop().toLowerCase();
        const accepts = (cfg.uploadAccept || "").split(",").map((s) => s.trim().toLowerCase());
        if (accepts.length && !accepts.includes(ext)) {
          return showToast(`仅支持 ${accepts.join(" / ")} 文件`, 2000);
        }
        if (cfg.maxSizeMB && file.size > cfg.maxSizeMB * 1024 * 1024) {
          return showToast(`文件不能超过 ${cfg.maxSizeMB}MB`, 2000);
        }
        const fd = new FormData();
        fd.append("kind", cfg.uploadKind);
        fd.append("file", file);
        uploaded = await request("/api/files/upload", { method: "POST", body: fd });
      }
      const body = await cfg.buildBody(uploaded); // 抛错 = 业务校验失败（如模块 9 两入口都空）
      startBtn.disabled = true;
      reportPanel.innerHTML =
        `<p class="report-title">${escapeHtml(cfg.reportTitle)}</p><div class="report-body"></div>`;
      const bodyEl = reportPanel.querySelector(".report-body");
      const acc = [];
      await streamSSE(cfg.ssePath, body, {
        onDelta: (t) => {
          acc.push(t);
          bodyEl.innerHTML = renderMarkdown(acc.join(""));
        },
        onDone: () => showToast("分析完成", 1500),
        onError: (m) => showToast(m, 2500),
      });
    } catch (e) {
      showToast(e.message, 2500);
    } finally {
      startBtn.disabled = false;
      refreshHistory();
    }
  });

  refreshHistory();
}

/**
 * 渲染历史列表（PRD 7.3）：条目 = 标签 + 时间；点击回看
 * @param {HTMLElement} el     列表容器
 * @param {Array<{id,input_data,result_data,created_at}>} items 历史记录（后端已倒序）
 * @param {(item: object) => void} onView 点击条目回调
 * @param {(item: object) => string} [labelOf] 条目标签（默认文件名或时间）
 */
export function renderHistoryList(el, items, onView, labelOf) {
  el.innerHTML = "";
  if (!items.length) {
    el.innerHTML = '<p class="history-empty">暂无历史记录</p>';
    return;
  }
  for (const item of items) {
    const div = document.createElement("div");
    div.className = "history-item";
    const label = (labelOf ? labelOf(item) : item.input_data.file_name) || item.created_at;
    div.title = label;
    div.innerHTML = `
      <div class="history-label">${escapeHtml(label)}</div>
      <div class="history-time">${escapeHtml(item.created_at)}</div>`;
    div.addEventListener("click", () => {
      el.querySelectorAll(".history-item").forEach((n) => n.classList.remove("active"));
      div.classList.add("active");
      onView(item);
    });
    el.appendChild(div);
  }
}
