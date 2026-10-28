// 统一请求封装：自动携带 Bearer Token、解析 JSON、401 统一拦截跳转
// 说明：前端由后端同源托管，直接用相对路径请求即可

export const tokenStore = {
  get: () => localStorage.getItem("token"),
  set: (t) => localStorage.setItem("token", t),
  clear: () => localStorage.removeItem("token"),
};

/** 路由守卫：需要登录的页面（index/profile）加载时调用，无 Token 强制跳回登录页 */
export function requireAuth() {
  // 用 replace 而非 href：不把"未登录访问受保护页"留在历史记录，防止浏览器后退又回来
  if (!tokenStore.get()) {
    window.location.replace("login.html");
    return false;
  }
  return true;
}

/** 退出登录：清除 Token 并跳转登录页 */
export function logout() {
  tokenStore.clear();
  window.location.replace("login.html");
}

/** 解析后端 detail：支持字符串（HTTPException）与数组（pydantic 422 校验错误） */
function extractErrorDetail(detail) {
  if (!detail) return "请求失败";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const field = (d.loc || []).slice(1).join(".");
        const msg = String(d.msg).replace("Value error, ", "");
        return field ? `${field}：${msg}` : msg;
      })
      .join("；");
  }
  return JSON.stringify(detail);
}

/**
 * 统一请求函数
 * @param {string} path 接口路径，如 /api/users/login
 * @param {object} [options]
 * @param {string}   [options.method] 请求方法，默认 GET
 * @param {object}   [options.body]   请求体：JSON 对象自动序列化；FormData 直接透传
 *                                    （multipart 交由浏览器生成 boundary，不手动设 Content-Type）
 * @param {boolean}  [options.skipAuthRedirect] 为 true 时 401 不跳转登录页
 * @returns {Promise<object>} 后端返回的 JSON 数据
 */
export async function request(path, { method = "GET", body, skipAuthRedirect = false } = {}) {
  const isForm = body instanceof FormData;
  const headers = isForm ? {} : { "Content-Type": "application/json" };
  const t = tokenStore.get();
  if (t) headers.Authorization = `Bearer ${t}`;

  let resp;
  try {
    resp = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
    });
  } catch {
    throw new Error("网络异常，请确认后端服务已启动");
  }

  // 401 统一拦截：登录失效 → 清除 Token 回登录页
  if (resp.status === 401 && !skipAuthRedirect) {
    tokenStore.clear();
    location.replace("login.html");
    throw new Error("登录已失效，请重新登录");
  }

  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(extractErrorDetail(data.detail));
  return data;
}
