// 登录 / 注册 / 忘记密码 三个页面的表单逻辑
// 一个模块服务三个页面：按页面上的表单 id 自动分发绑定
import { request, tokenStore } from "./core/api.js";
import { showToast } from "./core/toast.js";

const $ = (sel) => document.querySelector(sel);

/** 在表单下方显示提示消息（ok=true 绿色，否则红色） */
function showMsg(el, text, ok = false) {
  if (!el) return;
  el.textContent = text;
  el.className = `msg ${ok ? "ok" : "error"}`;
}

/** 按钮加载态，防止重复提交 */
function setLoading(btn, loading) {
  btn.disabled = loading;
  btn.textContent = loading ? "处理中…" : btn.dataset.label;
}

function armBtn(form) {
  const btn = form.querySelector("button[type=submit]");
  btn.dataset.label = btn.textContent;
  return btn;
}

/* ===== 登录页 ===== */
function bindLogin(form) {
  if (tokenStore.get()) {
    window.location.replace("index.html"); // 已登录用户直接进首页
    return;
  }
  const btn = armBtn(form);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showMsg($("#login-msg"), "");
    setLoading(btn, true);
    try {
      const data = await request("/api/users/login", {
        method: "POST",
        body: {
          username: $("#username").value.trim(),
          password: $("#password").value,
        },
      });
      tokenStore.set(data.access_token); // JWT 存入 localStorage
      window.location.href = "index.html"; // 登录成功 → 跳首页（位于 fetch 成功回调内）
    } catch (err) {
      showMsg($("#login-msg"), err.message);
    } finally {
      setLoading(btn, false);
    }
  });
}

/* ===== 注册页 ===== */
function bindRegister(form) {
  const btn = armBtn(form);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showMsg($("#register-msg"), "");
    if ($("#password").value !== $("#password2").value) {
      showMsg($("#register-msg"), "两次输入的密码不一致");
      return;
    }
    setLoading(btn, true);
    try {
      await request("/api/users/register", {
        method: "POST",
        body: {
          username: $("#username").value.trim(),
          password: $("#password").value,
          id_card: $("#id_card").value.trim(),
          phone: $("#phone").value.trim() || null, // 留空不填
          email: $("#email").value.trim() || null,
        },
      });
      showToast("注册成功，即将进入登录页...", 1500);
      setTimeout(() => { window.location.href = "login.html"; }, 1500);
    } catch (err) {
      showMsg($("#register-msg"), err.message);
      setLoading(btn, false); // 仅失败时恢复按钮；成功后 1.5 秒内不允许重复提交
    }
  });
}

/* ===== 忘记密码页 ===== */
function bindForgot(form) {
  const btn = armBtn(form);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showMsg($("#forgot-msg"), "");
    if ($("#new_password").value !== $("#new_password2").value) {
      showMsg($("#forgot-msg"), "两次输入的新密码不一致");
      return;
    }
    setLoading(btn, true);
    try {
      const data = await request("/api/users/forgot-password", {
        method: "POST",
        body: {
          username: $("#username").value.trim(),
          id_card: $("#id_card").value.trim(),
          new_password: $("#new_password").value,
        },
      });
      showToast("密码重置成功，即将进入登录页...", 1500);
      setTimeout(() => { window.location.href = "login.html"; }, 1500);
    } catch (err) {
      showMsg($("#forgot-msg"), err.message);
      setLoading(btn, false); // 仅失败时恢复按钮
    }
  });
}

/* ===== 按表单 id 分发到对应页面逻辑 ===== */
const loginForm = $("#login-form");
if (loginForm) bindLogin(loginForm);

const registerForm = $("#register-form");
if (registerForm) bindRegister(registerForm);

const forgotForm = $("#forgot-form");
if (forgotForm) bindForgot(forgotForm);
