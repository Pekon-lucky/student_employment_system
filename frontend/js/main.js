// 首页逻辑：路由守卫、欢迎语、头像下拉菜单、信息完整度模态框
import { request, requireAuth, logout } from "./core/api.js";

const $ = (sel) => document.querySelector(sel);

// 路由守卫第二道防线（第一道是 <head> 内联同步脚本，先于 DOM 渲染执行）
// 模块脚本本身是延迟执行的，若只在这里守卫，未登录用户会先看到页面内容
if (requireAuth()) {
  /* ===== 头像下拉菜单 ===== */
  const dropdown = $("#dropdown");
  $("#avatar").addEventListener("click", (e) => {
    e.stopPropagation(); // 避免被下面的 document 点击监听立即收起
    dropdown.classList.toggle("show");
  });
  document.addEventListener("click", () => dropdown.classList.remove("show"));
  $("#menu-logout").addEventListener("click", (e) => {
    e.preventDefault();
    logout();
  });
  // "用户信息"菜单项是 <a href="profile.html">，浏览器直接跳转，无需绑定

  /* ===== 信息完整度模态框 ===== */
  $("#modal-confirm").addEventListener("click", () => {
    window.location.href = "profile.html"; // 确定按钮 → 用户信息页
  });
  $("#modal-close").addEventListener("click", () => $("#profile-modal").classList.remove("show"));

  (async function init() {
    // 加载当前用户信息（401 已在 request 内统一拦截跳转）
    try {
      const { user } = await request("/api/users/me");
      $("#avatar").textContent = user.username.charAt(0).toUpperCase();
      $("#welcome").textContent = `你好，${user.username}！`;
      $("#profile-hint").textContent =
        user.phone && user.email ? "" : "提示：完善手机号与邮箱，便于接收求职提醒";
    } catch {
      return;
    }

    // 信息不完整 → 弹出模态框引导去 profile.html 录入
    try {
      const status = await request("/api/users/profile-status");
      if (!status.complete) $("#profile-modal").classList.add("show");
    } catch { /* 同上 */ }
  })();
}
