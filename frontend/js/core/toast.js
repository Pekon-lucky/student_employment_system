// 全局轻提示 Toast：不阻塞用户操作，duration 毫秒后自动淡出
// 用法：showToast('修改成功，即将重新登录...') / showToast('出错啦', 2000)

let toastTimer = null;

export function showToast(message, duration = 1500) {
  // 复用同一个节点：连续触发时只更新文字并重播动画，避免叠出多层
  let toast = document.querySelector(".toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.className = "toast";
    document.body.appendChild(toast);
  }

  clearTimeout(toastTimer); // 连续调用时取消上一次的淡出计划
  toast.textContent = message;
  toast.classList.remove("hide");
  void toast.offsetWidth; // 强制重绘，保证动画能从头重播
  toast.classList.add("show");

  toastTimer = setTimeout(() => {
    toast.classList.remove("show");
    toast.classList.add("hide"); // 淡出动画播完后停留透明态，等待下次复用
  }, duration);
}
