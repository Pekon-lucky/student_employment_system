// 模块 10 模拟面试：知识库闭环（上传/解绑）+ Bot 多轮流式聊天
// 协议：delta 追加 / done 带 conversation_id（多轮策略 A）/ error 提示
import { request, requireAuth } from "../core/api.js";
import { streamSSE } from "../core/sse.js";
import { appendChatMessage, $, escapeHtml, initNav, renderMarkdownStream } from "../core/ui.js";
import { showToast } from "../core/toast.js";

// 两类知识库的 DOM 映射与状态字段（kind 与后端 /api/knowledge/{kind} 一致）
const KB = {
  resume: {
    label: "简历",
    file: "#resume-file",
    uploadBtn: "#resume-upload",
    removeBtn: "#resume-remove",
    name: "#resume-name",
    badge: "#resume-badge",
    nameField: "resume_file_name",
    statusField: "resume_sync_status",
    placeholder: "未绑定文件（.doc / .docx）",
  },
  questions: {
    label: "面试题",
    file: "#questions-file",
    uploadBtn: "#questions-upload",
    removeBtn: "#questions-remove",
    name: "#questions-name",
    badge: "#questions-badge",
    nameField: "questions_file_name",
    statusField: "questions_sync_status",
    placeholder: "未绑定文件（.md）",
  },
};

const STATUS_TEXT = { none: "未上传", syncing: "同步中", success: "已同步", failed: "同步失败" };

/** 更新状态徽标：文案 + 配色类 */
function setBadge(el, status) {
  el.textContent = STATUS_TEXT[status] || STATUS_TEXT.none;
  el.className = `sync-badge ${status && status !== "none" ? status : ""}`.trim();
}

/** 用后端返回的 knowledge 状态刷新整个知识库区 */
function renderKnowledge(st) {
  for (const cfg of Object.values(KB)) {
    setBadge($(cfg.badge), st[cfg.statusField]);
    $(cfg.name).textContent = st[cfg.nameField] || cfg.placeholder;
  }
  // 两类都未绑定时显示常驻提示条（非阻塞）
  const ready = !!st.resume_file_name || !!st.questions_file_name;
  $("#kb-tip").classList.toggle("hide", ready);
}

if (requireAuth()) {
  (async function init() {
    const user = await initNav();
    const uid = user?.id ?? "anon";
    const CID_KEY = `interview_cid_${uid}`;

    // ------------------------------------------------------------------
    // 知识库：状态加载 + 上传闭环 + 解除绑定
    // ------------------------------------------------------------------
    async function refreshKnowledge() {
      try {
        renderKnowledge(await request("/api/knowledge"));
      } catch (e) {
        showToast(e.message, 2000);
      }
    }

    for (const [kind, cfg] of Object.entries(KB)) {
      const input = $(cfg.file);

      $(cfg.uploadBtn).addEventListener("click", () => input.click());

      input.addEventListener("change", async () => {
        const file = input.files[0];
        if (!file) return;
        // 前置校验（扩展名 + 大小，与后端白名单一致）
        const ext = "." + file.name.split(".").pop().toLowerCase();
        const allow = kind === "resume" ? [".doc", ".docx"] : [".md"];
        if (!allow.includes(ext)) {
          input.value = "";
          return showToast(`${cfg.label}知识库仅支持 ${allow.join(" / ")}`, 2000);
        }
        if (file.size > (kind === "resume" ? 10 : 2) * 1024 * 1024) {
          input.value = "";
          return showToast("文件超过大小限制（简历 10MB / 面试题 2MB）", 2000);
        }

        setBadge($(cfg.badge), "syncing"); // 先亮「同步中」，闭环完成后再落 success/failed
        const fd = new FormData();
        fd.append("file", file);
        try {
          const st = await request(`/api/knowledge/${kind}`, { method: "POST", body: fd });
          renderKnowledge(st);
          showToast(`${cfg.label}知识库同步成功`, 1500);
        } catch (e) {
          showToast(e.message, 2500);
          await refreshKnowledge(); // 失败时以后端为准（failed / none）
        } finally {
          input.value = ""; // 允许重复选择同一文件
        }
      });

      $(cfg.removeBtn).addEventListener("click", async () => {
        $(cfg.removeBtn).disabled = true;
        try {
          renderKnowledge(await request(`/api/knowledge/${kind}`, { method: "DELETE" }));
          showToast(`已解除${cfg.label}绑定`, 1500);
        } catch (e) {
          showToast(e.message, 2500);
        } finally {
          $(cfg.removeBtn).disabled = false;
        }
      });
    }

    // ------------------------------------------------------------------
    // 聊天：多轮对话（conversation_id 存 localStorage，按用户隔离）
    // ------------------------------------------------------------------
    const listEl = $("#chat-list");
    const inputEl = $("#chat-input");
    const sendBtn = $("#chat-send");
    const GREETING = "你好！我是你的模拟面试官。可以先做个自我介绍，我会结合你上传的简历和面试题提问、追问并点评。";

    let cid = localStorage.getItem(CID_KEY) || "";
    let busy = false;

    /** 追加 AI 气泡并把流式增量逐段渲染进去；返回累计文本 */
    function startAiReply() {
      const bubble = appendChatMessage(listEl, "ai", "", { streaming: true });
      const acc = [];
      return {
        bubble,
        push(text) {
          acc.push(text);
          renderMarkdownStream(bubble, acc.join(""));
        },
        text: () => acc.join(""),
        fail(msg) {
          // 无任何增量时用错误提示替换打字指示器，避免一直转圈
          if (!acc.length) {
            bubble.querySelector(".chat-bubble").innerHTML =
              `<span style="color:#b91c1c;">⚠️ ${escapeHtml(msg)}</span>`;
          }
        },
      };
    }

    async function sendMessage() {
      const text = inputEl.value.trim();
      if (!text) return showToast("请输入内容", 1500);
      if (busy) return showToast("面试官还在回复中，请稍候…", 1500);

      busy = true;
      sendBtn.disabled = true;
      inputEl.value = "";
      appendChatMessage(listEl, "user", text);

      const reply = startAiReply();
      await streamSSE("/api/interview/chat", { message: text, conversation_id: cid }, {
        onDelta: (t) => reply.push(t),
        onDone: (data) => {
          // 多轮策略 A：保存 Coze 下发的会话指针，下一轮带回
          if (data && data.conversation_id) {
            cid = data.conversation_id;
            localStorage.setItem(CID_KEY, cid);
          }
        },
        onError: (msg) => {
          showToast(msg, 2500);
          reply.fail(msg);
        },
      });

      busy = false;
      sendBtn.disabled = false;
      inputEl.focus();
    }

    sendBtn.addEventListener("click", sendMessage);
    inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.isComposing) sendMessage(); // isComposing：中文输入法选词时不误发
    });

    // 清空：仅清前端当前会话状态（消息 + conversation_id），不动后端/Coze 数据（PRD 4.4）
    $("#chat-clear").addEventListener("click", () => {
      listEl.innerHTML = "";
      cid = "";
      localStorage.removeItem(CID_KEY);
      appendChatMessage(listEl, "ai", GREETING);
      showToast("已清空当前会话（仅前端）", 1500);
      inputEl.focus();
    });

    // 初始态
    appendChatMessage(listEl, "ai", GREETING);
    await refreshKnowledge();
  })();
}
