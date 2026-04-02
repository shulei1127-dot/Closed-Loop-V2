async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || data.error_message || `Request failed: ${response.status}`);
  }
  return data;
}

function renderFeedback(kind, title, payload) {
  const panel = document.getElementById("feedback-panel");
  if (!panel) return;
  panel.className = `feedback-panel ${kind}`;
  panel.innerHTML = `
    <h3>${title}</h3>
    <pre>${JSON.stringify(payload, null, 2)}</pre>
  `;
}

function scheduleRefresh(delayMs = 1200) {
  window.setTimeout(() => {
    window.location.reload();
  }, delayMs);
}

async function handleSync(button) {
  const moduleCode = button.dataset.moduleCode;
  renderFeedback("warning", `正在同步 ${moduleCode}...`, { module_code: moduleCode });
  const result = await postJson("/api/sync/run", { module_code: moduleCode, force: false });
  renderFeedback("success", `同步完成：${moduleCode}`, result);
  scheduleRefresh();
}

async function handleSyncRerun(button) {
  const moduleCode = button.dataset.moduleCode;
  renderFeedback("warning", `正在重跑同步 ${moduleCode}...`, { module_code: moduleCode });
  const result = await postJson(`/api/modules/${moduleCode}/sync/rerun`, {});
  renderFeedback("success", `重跑完成：${moduleCode}`, result);
  scheduleRefresh();
}

async function handlePrecheck(button) {
  const taskId = button.dataset.taskId;
  renderFeedback("warning", `正在执行预检查 ${taskId}...`, { task_id: taskId });
  const result = await postJson(`/api/tasks/${taskId}/precheck`, {});
  renderFeedback("success", "预检查结果", result);
}

async function handleExecute(button) {
  const taskId = button.dataset.taskId;
  const dryRun = button.dataset.dryRun === "true";
  renderFeedback("warning", `正在执行 task ${taskId}...`, { task_id: taskId, dry_run: dryRun });
  const result = await postJson(`/api/tasks/${taskId}/execute`, { dry_run: dryRun });
  const status = result.item?.run_status || "unknown";
  const kind = result.item?.manual_required ? "warning" : "success";
  renderFeedback(kind, `执行结果：${status}`, result);
  scheduleRefresh();
}

async function handleTaskRerun(button) {
  const taskId = button.dataset.taskId;
  const dryRun = button.dataset.dryRun === "true";
  renderFeedback("warning", `正在重跑 task ${taskId}...`, { task_id: taskId, dry_run: dryRun });
  const result = await postJson(`/api/tasks/${taskId}/rerun`, { dry_run: dryRun });
  const status = result.item?.run_status || "unknown";
  const kind = result.item?.manual_required ? "warning" : "success";
  renderFeedback(kind, `重跑结果：${status}`, result);
  scheduleRefresh();
}

async function handlePtsCookieUpdate(form) {
  const textarea = form.querySelector("#pts-cookie-input");
  const cookieHeader = textarea?.value?.trim();
  if (!cookieHeader) {
    throw new Error("请先粘贴新的 PTS Cookie");
  }
  renderFeedback("warning", "正在更新 PTS Cookie...", { configured: false });
  const result = await postJson("/api/ops/pts-session", { cookie_header: cookieHeader });
  if (textarea) {
    textarea.value = "";
  }
  const badge = document.getElementById("pts-session-badge");
  const updated = document.getElementById("pts-session-updated");
  if (badge) {
    badge.className = `badge status-${result.configured ? "success" : "warning"}`;
    badge.textContent = result.configured ? "已配置" : "未配置";
  }
  if (updated) {
    updated.textContent = result.updated_at ? `最后更新：${result.updated_at}` : "尚未保存";
  }
  renderFeedback("success", "PTS Cookie 已更新", {
    configured: result.configured,
    updated_at: result.updated_at,
    source: result.source,
  });
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  event.preventDefault();
  try {
    if (button.dataset.action === "sync") {
      await handleSync(button);
      return;
    }
    if (button.dataset.action === "rerun-sync") {
      await handleSyncRerun(button);
      return;
    }
    if (button.dataset.action === "precheck") {
      await handlePrecheck(button);
      return;
    }
    if (button.dataset.action === "execute") {
      await handleExecute(button);
      return;
    }
    if (button.dataset.action === "rerun-task") {
      await handleTaskRerun(button);
    }
  } catch (error) {
    renderFeedback("error", "操作失败", { error: error.message });
  }
});

document.addEventListener("submit", async (event) => {
  const form = event.target.closest("#pts-cookie-form");
  if (!form) return;
  event.preventDefault();
  try {
    await handlePtsCookieUpdate(form);
  } catch (error) {
    renderFeedback("error", "PTS Cookie 更新失败", { error: error.message });
  }
});
