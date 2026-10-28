// POST SSE 流式解析（技能规范：fetch + ReadableStream，禁用 EventSource——它只支持 GET）
//
// ⚠️ 缓冲区拼接机制（核心避坑）：
// 底层 TCP 可能把一个完整事件拆成多个 chunk 送来，也可能一个 chunk 里挤了多个事件。
// 因此绝不逐 chunk 直接 JSON.parse —— 必须把字节流累进 buffer，只在出现完整事件边界
// "\n\n" 时才切出一个事件块去解析，残尾（半个事件/半个 JSON）留待下一个 chunk 拼接。
// 另按 SSE 规范防御两种边缘：\r\n 换行、data: 后的内容跨多行（多个 data: 行按行拼接）。
//
// 解析器抽成 createSSEParser() 导出：生产（streamSSE）与调试页（test_sse.html 的
// 纯前端模拟）走同一条解析路径 —— 调试通过即生产可用。
import { tokenStore } from "./api.js";

/**
 * 创建 SSE 事件解析器（缓冲拼接 + 事件块分发）
 * @param {object} [handlers] 同 streamSSE 的回调（onDelta/onDone/onError/onInterrupt/onRaw）
 * @returns {{ push: (chunk: string) => void, end: () => void }}
 */
export function createSSEParser({ onDelta, onDone, onError, onInterrupt, onRaw } = {}) {
  let buffer = "";

  /** 解析并分发一个完整事件块（不含结尾空行） */
  const dispatch = (block) => {
    onRaw?.(block);
    let event = "message";
    const dataLines = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
      // 其余行（注释 : 开头、id、retry 等）按协议忽略
    }
    if (event === "message" && dataLines.length === 0) return; // 空事件跳过
    // SSE 规范：data 字段跨多行时是多个 data: 行，按 \n 拼接还原
    const rawData = dataLines.join("\n");
    let data = {};
    try {
      data = rawData ? JSON.parse(rawData) : {};
    } catch {
      onError?.("收到无法解析的数据块");
      return;
    }
    if (event === "delta") onDelta?.(data.text ?? "");
    else if (event === "done") onDone?.(data);
    else if (event === "error") onError?.(data.message ?? "未知错误");
    else if (event === "interrupt") onInterrupt?.(data);
  };

  return {
    /** 喂入任意长度的字节块（可半个事件、可多个事件挤在一起） */
    push(chunk) {
      buffer += chunk.replace(/\r\n/g, "\n"); // 防御 CRLF 换行
      let idx;
      // 只有出现完整边界 "\n\n" 才处理；残尾永远留在 buffer 等下一个 chunk
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        dispatch(block);
      }
    },
    /** 流结束兜底：服务端漏发结尾空行时把残尾当完整事件处理 */
    end() {
      if (buffer.trim()) dispatch(buffer);
      buffer = "";
    },
  };
}

/**
 * 发起 POST SSE 流式请求并逐事件分发
 * @param {string} path   接口路径（相对路径或完整 URL），如 /api/resume/evaluate
 * @param {object} body   请求体（JSON 序列化）
 * @param {object} [handlers]
 * @param {(text: string) => void} [handlers.onDelta]      增量文本
 * @param {(data: object) => void} [handlers.onDone]       完成事件（工作流 data={}；Bot 含 conversation_id）
 * @param {(message: string) => void} [handlers.onError]   错误事件（含非 200 响应的兜底）
 * @param {(data: object) => void} [handlers.onInterrupt]  工作流中断事件（本期保留）
 * @param {(raw: string) => void} [handlers.onRaw]         原始事件块（调试页逐行核对协议用）
 * @param {AbortSignal} [handlers.signal]                  中断信号（停止生成/关闭页面）
 */
export async function streamSSE(path, body, handlers = {}) {
  const { signal, ...callbacks } = handlers;
  const { onError } = callbacks;

  let resp;
  try {
    resp = await fetch(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${tokenStore.get()}`,
      },
      body: JSON.stringify(body ?? {}),
      signal,
    });
  } catch (err) {
    if (err.name !== "AbortError") onError?.("网络异常，请确认后端服务已启动");
    return;
  }

  // 技能规则 4：所有响应都是流。非 200（如 401/400 JSON）也统一转成 error 事件
  if (!resp.ok || !(resp.headers.get("content-type") || "").includes("text/event-stream")) {
    let msg = `请求失败（HTTP ${resp.status}）`;
    try {
      const j = await resp.json();
      msg = j.detail || msg;
      if (Array.isArray(msg)) {
        // 兼容 pydantic 422 数组（与 api.js 的 extractErrorDetail 同策略）
        msg = msg.map((d) => String(d.msg || "").replace("Value error, ", "")).join("；");
      }
    } catch { /* 保持默认消息 */ }
    onError?.(typeof msg === "string" ? msg : `请求失败（HTTP ${resp.status}）`);
    return;
  }

  const parser = createSSEParser(callbacks);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
    parser.end();
  } catch (err) {
    if (err.name !== "AbortError") onError?.("连接中断，请重试");
  }
}
