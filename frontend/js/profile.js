// 用户信息页逻辑：路由守卫、资料加载与更新、修改密码、导航
import { request, requireAuth, logout, tokenStore } from "./core/api.js";
import { showToast } from "./core/toast.js";

const $ = (sel) => document.querySelector(sel);

function showMsg(el, text, ok = false) {
  el.textContent = text;
  el.className = `msg ${ok ? "ok" : "error"}`;
}

if (requireAuth()) {
  /* ===== 导航：头像下拉菜单 ===== */
  const dropdown = $("#dropdown");
  $("#avatar").addEventListener("click", (e) => {
    e.stopPropagation();
    dropdown.classList.toggle("show");
  });
  document.addEventListener("click", () => dropdown.classList.remove("show"));
  $("#menu-logout").addEventListener("click", (e) => {
    e.preventDefault();
    logout();
  });

  /* ===== 基本信息：加载 + 更新 ===== */
  async function loadProfile() {
    const { user } = await request("/api/users/me");
    $("#username").value = user.username; // disabled 不可改
    $("#id_card").value = user.id_card;   // disabled 不可改
    $("#phone").value = user.phone || "";
    $("#email").value = user.email || "";
    $("#avatar").textContent = user.username.charAt(0).toUpperCase();
  }

  $("#info-form").addEventListener("submit", async (e) => {
    e.preventDefault(); // 阻止表单默认提交刷新
    showMsg($("#info-msg"), "");
    const btn = $("#info-submit");
    btn.disabled = true;
    try {
      await request("/api/users/me", {
        method: "PUT",
        // phone/email 始终携带输入框当前值：清空后提交即发送 ""，后端用空串覆盖旧值实现真正清空
        body: {
          phone: $("#phone").value.trim(),
          email: $("#email").value.trim(),
        },
      });
      // 修改成功：Toast 提示，1.5 秒后（提示消失的同时）清除登录态并跳转，全程不阻塞
      showToast("修改成功，即将重新登录...", 1500);
      setTimeout(() => {
        tokenStore.clear(); // 等价 localStorage.removeItem("token")
        window.location.replace("login.html"); // replace 不留历史记录，后退键回不来
      }, 1500);
    } catch (err) {
      showMsg($("#info-msg"), err.message);
      btn.disabled = false;
    }
  });

  /* ===== 修改密码（独立表单，调用 PUT /api/users/password） ===== */
  $("#pwd-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    showMsg($("#pwd-msg"), "");
    const newPassword = $("#new_password").value;
    if (newPassword !== $("#new_password2").value) {
      showMsg($("#pwd-msg"), "两次输入的新密码不一致");
      return;
    }
    const btn = $("#pwd-submit");
    btn.disabled = true;
    try {
      await request("/api/users/password", {
        method: "PUT",
        body: {
          old_password: $("#old_password").value,
          new_password: newPassword,
        },
      });
      // 密码修改成功：Toast 提示，1.5 秒后清除登录态并跳转（与资料修改行为一致）
      showToast("密码修改成功，即将重新登录...", 1500);
      setTimeout(() => {
        tokenStore.clear();
        window.location.replace("login.html");
      }, 1500);
    } catch (err) {
      showMsg($("#pwd-msg"), err.message);
      btn.disabled = false;
    }
  });

  loadProfile().catch(() => { /* 401 已在 request 内统一跳转 */ });
}
