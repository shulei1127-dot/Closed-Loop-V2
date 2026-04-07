async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
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
    const url = new URL(window.location.href);
    url.searchParams.set("_ts", Date.now().toString());
    window.location.assign(url.toString());
  }, delayMs);
}

function getInspectionSyncMonths() {
  const select = document.getElementById("inspection-sync-months");
  if (!select) return [];
  return Array.from(select.selectedOptions || [])
    .map((option) => option.value.trim())
    .filter(Boolean);
}

const prefetchedUrls = new Set();

function shouldPrefetchLink(link) {
  if (!link) return false;
  if (link.target && link.target !== "_self") return false;
  const href = link.getAttribute("href") || "";
  if (!href || href.startsWith("#")) return false;
  try {
    const url = new URL(link.href, window.location.origin);
    if (url.origin !== window.location.origin) return false;
    return (
      url.pathname === "/console"
      || url.pathname.startsWith("/console/modules/")
      || url.pathname.startsWith("/console/tasks")
      || url.pathname.startsWith("/console/inspection-links")
      || url.pathname.startsWith("/console/visit-links")
    );
  } catch {
    return false;
  }
}

function prefetchLink(link) {
  if (!shouldPrefetchLink(link)) return;
  const url = new URL(link.href, window.location.origin).toString();
  if (prefetchedUrls.has(url)) return;
  prefetchedUrls.add(url);
  window.fetch(url, {
    method: "GET",
    credentials: "same-origin",
    cache: "no-store",
    headers: { "X-Codex-Prefetch": "1" },
  }).catch(() => {
    prefetchedUrls.delete(url);
  });
}

async function handleSync(button) {
  const moduleCode = button.dataset.moduleCode;
  const syncMonths = moduleCode === "inspection" ? getInspectionSyncMonths() : [];
  renderFeedback("warning", `正在同步 ${moduleCode}...`, { module_code: moduleCode, sync_months: syncMonths });
  const result = await postJson("/api/sync/run", { module_code: moduleCode, force: false, sync_months: syncMonths });
  renderFeedback("success", `同步完成：${moduleCode}`, result);
  scheduleRefresh();
}

async function handleSyncRerun(button) {
  const moduleCode = button.dataset.moduleCode;
  const syncMonths = moduleCode === "inspection" ? getInspectionSyncMonths() : [];
  renderFeedback("warning", `正在重跑同步 ${moduleCode}...`, { module_code: moduleCode, sync_months: syncMonths });
  const result = await postJson(`/api/modules/${moduleCode}/sync/rerun`, { sync_months: syncMonths });
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

async function handleExecuteAllVisit(button) {
  const totalCount = Number.parseInt(button.dataset.totalCount || "0", 10);
  if (!window.confirm(`将按顺序创建并闭环全部待执行回访任务（当前 ${totalCount} 条）。是否继续？`)) {
    return;
  }
  renderFeedback("warning", "正在批量创建并闭环待执行回访...", { module_code: "visit", total_count: totalCount });
  const result = await postJson("/api/tasks/batch/execute-pending", { module_code: "visit", dry_run: false });
  renderFeedback("success", "批量执行完成", result);
  scheduleRefresh(1800);
}

async function handleExecuteAllInspection(button) {
  const totalCount = Number.parseInt(button.dataset.totalCount || "0", 10);
  const month = button.dataset.month || "";
  if (!window.confirm(`将按顺序上传 Word 报告并闭环 ${month || "当前筛选"} 的全部巡检任务（当前 ${totalCount} 条）。是否继续？`)) {
    return;
  }
  renderFeedback("warning", "正在批量上传报告并闭环巡检任务...", { module_code: "inspection", total_count: totalCount, month });
  const result = await postJson("/api/tasks/batch/execute-pending", { module_code: "inspection", month, dry_run: false });
  renderFeedback("success", "巡检批量执行完成", result);
  scheduleRefresh(2200);
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
  const navLink = event.target.closest("a");
  if (navLink && shouldPrefetchLink(navLink) && !event.defaultPrevented) {
    document.body.classList.add("page-loading");
  }
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
    if (button.dataset.action === "execute-all-visit") {
      await handleExecuteAllVisit(button);
      return;
    }
    if (button.dataset.action === "execute-all-inspection") {
      await handleExecuteAllInspection(button);
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

document.addEventListener("mouseover", (event) => {
  const link = event.target.closest("a");
  if (!link) return;
  prefetchLink(link);
});

document.addEventListener("focusin", (event) => {
  const link = event.target.closest("a");
  if (!link) return;
  prefetchLink(link);
});

window.addEventListener("load", () => {
  document
    .querySelectorAll("nav a, .module-entry-link, .ghost-link")
    .forEach((link) => prefetchLink(link));
});
