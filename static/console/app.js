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

async function getJson(url) {
  const response = await fetch(url, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
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

function scheduleRefresh(delayMs = 1200, extraQuery = {}) {
  window.setTimeout(() => {
    const url = new URL(window.location.href);
    Object.entries(extraQuery || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === "") {
        url.searchParams.delete(key);
      } else {
        url.searchParams.set(key, String(value));
      }
    });
    url.searchParams.set("_ts", Date.now().toString());
    window.location.assign(url.toString());
  }, delayMs);
}

async function pollBatchStatus(batchId, options = {}) {
  const intervalMs = Number(options.intervalMs || 2000);
  const extraQuery = options.extraQuery || {};
  async function tick() {
    const result = await getJson(`/api/tasks/batches/${batchId}`);
    const item = result.item || {};
    const title = item.done
      ? "后台批量执行完成"
      : `后台执行中：queued=${item.queued_count || 0}, running=${item.running_count || 0}`;
    renderFeedback(item.done ? "success" : "warning", title, result);
    if (item.done) {
      scheduleRefresh(800, extraQuery);
      return;
    }
    window.setTimeout(async () => {
      try {
        await tick();
      } catch (error) {
        renderFeedback("error", "批次状态查询失败", { batch_id: batchId, error: error.message });
      }
    }, intervalMs);
  }
  await tick();
}

function getInspectionSyncMonths() {
  const select = document.getElementById("inspection-sync-months");
  if (!select) return [];
  return Array.from(select.selectedOptions || [])
    .map((option) => option.value.trim())
    .filter(Boolean);
}

function getVisitOwnerFilter() {
  const select = document.getElementById("visit-owner-filter");
  if (!select) return "";
  const value = String(select.value || "").trim();
  if (!value || value === "all" || value === "全部") {
    return "";
  }
  return value;
}

function getProactiveOwnerFilter() {
  const select = document.getElementById("proactive-owner-filter");
  if (!select) return "";
  const value = String(select.value || "").trim();
  if (!value || value === "all" || value === "全部") {
    return "";
  }
  return value;
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

function fmtDateTime(value) {
  if (!value) return "暂无";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (n) => `${n}`.padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function setElementVisible(id, visible) {
  const el = document.getElementById(id);
  if (!el) return;
  el.hidden = !visible;
}

function setElementText(id, text) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}

function renderDataMeta(targetId, meta) {
  const el = document.getElementById(targetId);
  if (!el) return;
  if (!meta) {
    el.textContent = "数据时间：未知";
    return;
  }
  const servedAt = fmtDateTime(meta.served_at);
  const source = meta.cached ? "缓存中" : "实时";
  el.textContent = `数据时间：${servedAt}（${source}）`;
}

function renderModuleHealthPanel(prefix, item) {
  const badge = document.getElementById(`${prefix}-health-badge`);
  const summary = document.getElementById(`${prefix}-health-summary`);
  const list = document.getElementById(`${prefix}-health-list`);
  if (badge) {
    badge.className = `badge status-${item?.status || "unknown"}`;
    badge.textContent = item?.status_label || "未知";
  }
  if (summary) {
    summary.textContent = item?.summary || "暂无健康检查摘要";
  }
  if (list) {
    const checks = item?.checks || [];
    if (!checks.length) {
      list.innerHTML = '<li class="muted">暂无健康检查项。</li>';
    } else {
      list.innerHTML = checks
        .map((check) => `
          <li>
            <strong>${escapeHtml(check.label || check.code || "检查项")}</strong>
            <span class="badge status-${escapeHtml(check.status || "unknown")}">${escapeHtml(check.status_label || "未知")}</span>
            <span class="muted">${escapeHtml(check.detail || "")}</span>
          </li>
        `)
        .join("");
    }
  }
}

function renderDashboardHealth(items) {
  const badge = document.getElementById("dashboard-health-badge");
  const summary = document.getElementById("dashboard-health-summary");
  const grid = document.getElementById("dashboard-health-grid");
  if (!badge || !summary || !grid) return;
  const statuses = new Set((items || []).map((item) => item.status || "unknown"));
  let overall = "ok";
  let label = "健康";
  if (statuses.has("failed")) {
    overall = "failed";
    label = "异常";
  } else if (statuses.has("warning")) {
    overall = "warning";
    label = "关注";
  }
  badge.className = `badge status-${overall}`;
  badge.textContent = label;
  summary.textContent = `共 ${items.length || 0} 个模块，展示同步/执行/会话/目录等关键检查项。`;
  if (!items.length) {
    grid.innerHTML = '<article class="health-card"><p class="muted">暂无模块健康信息。</p></article>';
    return;
  }
  grid.innerHTML = items
    .map((item) => `
      <article class="health-card">
        <div class="section-head">
          <h4>${escapeHtml(item.module_name || item.module_code || "模块")}</h4>
          <span class="badge status-${escapeHtml(item.status || "unknown")}">${escapeHtml(item.status_label || "未知")}</span>
        </div>
        <p class="muted">${escapeHtml(item.summary || "-")}</p>
        <ul class="health-check-list">
          ${(item.checks || []).map((check) => `
            <li>
              <strong>${escapeHtml(check.label || check.code || "检查项")}</strong>
              <span class="badge status-${escapeHtml(check.status || "unknown")}">${escapeHtml(check.status_label || "未知")}</span>
              <span class="muted">${escapeHtml(check.detail || "")}</span>
            </li>
          `).join("")}
        </ul>
      </article>
    `)
    .join("");
}

async function loadSection({ sectionName, loader, onSuccess, errorId, retryId }) {
  try {
    setElementVisible(errorId, false);
    setElementVisible(retryId, false);
    const result = await loader();
    onSuccess(result);
  } catch (error) {
    setElementText(errorId, `${sectionName}加载失败：${error.message}`);
    setElementVisible(errorId, true);
    setElementVisible(retryId, true);
  }
}

async function loadVisitModuleData(meta) {
  const owner = (meta.dataset.visitOwner || "").trim();
  const visitState = {
    summaryItem: null,
    filteredCount: null,
    totalCount: null,
    pendingItems: [],
    pendingPage: 1,
    pendingPageSize: 5,
    recentItems: [],
    recentPage: 1,
    recentPageSize: 5,
  };

  function summarizeAutoExecutable(items) {
    const safeItems = Array.isArray(items) ? items : [];
    const executableCount = safeItems.filter((item) => item.can_execute).length;
    const blockedItems = safeItems.filter((item) => !item.can_execute);
    const reasonCounts = new Map();
    blockedItems.forEach((item) => {
      const reason = item.business_state_label || item.state_label || item.technical_state_label || "不可自动执行";
      reasonCounts.set(reason, (reasonCounts.get(reason) || 0) + 1);
    });
    const topReason = [...reasonCounts.entries()]
      .sort((a, b) => b[1] - a[1])[0];
    return {
      totalCount: safeItems.length,
      executableCount,
      blockedCount: blockedItems.length,
      topReasonText: topReason ? `${topReason[0]} (${topReason[1]})` : "无",
    };
  }

  function renderVisitAutoExecuteStats(summary, sourceLabel) {
    const stats = document.getElementById("visit-auto-execute-stats");
    if (stats) {
      stats.innerHTML = `
        <div><dt>18:00自动执行（全量）</dt><dd>${escapeHtml(summary.executableCount)}</dd></div>
        <div><dt>不会自动执行</dt><dd>${escapeHtml(summary.blockedCount)}</dd></div>
        <div><dt>阻塞原因 Top1</dt><dd>${escapeHtml(summary.topReasonText)}</dd></div>
      `;
    }
    const note = document.getElementById("visit-auto-execute-note");
    if (note) {
      note.textContent = `${sourceLabel}待处理 ${summary.totalCount} 个，其中 ${summary.executableCount} 个会在 18:00 自动执行。`;
    }
  }

  async function refreshVisitAutoExecuteStats() {
    try {
      const sourceItems = owner
        ? (await getJson("/api/ops/modules/visit/pending?limit=5000")).items || []
        : (visitState.pendingItems || []);
      renderVisitAutoExecuteStats(summarizeAutoExecutable(sourceItems), "模块全量");
    } catch (error) {
      const note = document.getElementById("visit-auto-execute-note");
      if (note) {
        note.textContent = `自动执行前统计加载失败：${error.message}`;
      }
    }
  }

  function renderVisitPending() {
    const tbody = document.getElementById("visit-pending-tbody");
    if (!tbody) return;
    const items = visitState.pendingItems || [];
    const pageSize = visitState.pendingPageSize || 5;
    const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
    if (visitState.pendingPage > totalPages) {
      visitState.pendingPage = totalPages;
    }
    const currentPage = Math.max(1, visitState.pendingPage || 1);
    const startIndex = (currentPage - 1) * pageSize;
    const pageItems = items.slice(startIndex, startIndex + pageSize);

    if (!items.length) {
      tbody.innerHTML = "";
      setElementVisible("visit-pending-empty", true);
      setElementVisible("visit-pending-pagination", false);
      return;
    }

    setElementVisible("visit-pending-empty", false);
    setElementVisible("visit-pending-pagination", true);
    tbody.innerHTML = pageItems
      .map((item) => `
        <tr>
          <td>${escapeHtml(item.customer_name || "未知客户")}</td>
          <td>${escapeHtml(item.visit_owner || "未识别")}</td>
          <td>${escapeHtml(item.visit_type || item.task_type || "-")}</td>
          <td>${escapeHtml(item.delivery_id || "缺失")}</td>
          <td>
            <span class="badge status-${escapeHtml(item.business_state_tone || item.state_tone || "unknown")}">${escapeHtml(item.business_state_label || item.state_label || "待处理")}</span>
            <div class="technical-hint">技术态：${escapeHtml(item.technical_state_label || "未执行")}</div>
          </td>
          <td>${escapeHtml(item.latest_run_time ? fmtDateTime(item.latest_run_time) : "未执行")}</td>
          <td>
            <div>${escapeHtml(item.business_explanation || "-")}</div>
            <div class="technical-hint">${escapeHtml(item.technical_detail || "")}</div>
          </td>
          <td>
            <a href="${escapeHtml(item.detail_url || "#")}">查看任务</a>
            ${item.can_execute ? `<button class="action-button danger" data-action="execute" data-module-code="visit" data-task-id="${escapeHtml(item.task_plan_id)}" data-dry-run="false">一键创建并闭环</button>` : '<span class="muted">补齐字段后可执行</span>'}
          </td>
        </tr>
      `)
      .join("");

    setElementText("visit-pending-page-info", `第 ${currentPage} 页 / 共 ${totalPages} 页`);
    const prevBtn = document.getElementById("visit-pending-prev");
    const nextBtn = document.getElementById("visit-pending-next");
    if (prevBtn) prevBtn.disabled = currentPage <= 1;
    if (nextBtn) nextBtn.disabled = currentPage >= totalPages;
  }

  function renderVisitRecent() {
    const tbody = document.getElementById("visit-recent-tbody");
    if (!tbody) return;
    const items = visitState.recentItems || [];
    const pageSize = visitState.recentPageSize || 5;
    const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
    if (visitState.recentPage > totalPages) {
      visitState.recentPage = totalPages;
    }
    const currentPage = Math.max(1, visitState.recentPage || 1);
    const startIndex = (currentPage - 1) * pageSize;
    const pageItems = items.slice(startIndex, startIndex + pageSize);

    if (!items.length) {
      tbody.innerHTML = "";
      setElementVisible("visit-recent-empty", true);
      setElementVisible("visit-recent-pagination", false);
      return;
    }

    setElementVisible("visit-recent-empty", false);
    setElementVisible("visit-recent-pagination", true);
    tbody.innerHTML = pageItems
      .map((item) => `
        <tr>
          <td>${escapeHtml(item.customer_name || "未知客户")}</td>
          <td>${escapeHtml(item.visit_type || "未知类型")}</td>
          <td>${escapeHtml(fmtDateTime(item.occurred_at))}</td>
          <td><a href="${escapeHtml(item.final_link)}" target="_blank" rel="noreferrer">${escapeHtml(item.final_link)}</a></td>
        </tr>
      `)
      .join("");

    setElementText("visit-recent-page-info", `第 ${currentPage} 页 / 共 ${totalPages} 页`);
    const prevBtn = document.getElementById("visit-recent-prev");
    const nextBtn = document.getElementById("visit-recent-next");
    if (prevBtn) prevBtn.disabled = currentPage <= 1;
    if (nextBtn) nextBtn.disabled = currentPage >= totalPages;
  }

  function renderVisitSummary() {
    const item = visitState.summaryItem || {};
    const badge = document.getElementById("visit-summary-sync-badge");
    if (badge) {
      badge.className = `badge status-${item.latest_sync_status || "unknown"}`;
      badge.textContent = item.latest_sync_status_label || "未同步";
    }
    const filtered = visitState.filteredCount == null ? "加载中" : String(visitState.filteredCount);
    const total = visitState.totalCount == null ? "加载中" : String(visitState.totalCount);
    const stats = document.getElementById("visit-summary-stats");
    if (stats) {
      stats.innerHTML = `
        <div><dt>最近同步</dt><dd>${escapeHtml(fmtDateTime(item.latest_snapshot_time))}</dd></div>
        <div><dt>最近执行</dt><dd>${escapeHtml(item.latest_execute_status_label || "暂无")}</dd></div>
        <div><dt>待执行（当前筛选）</dt><dd id="visit-summary-filtered">${escapeHtml(filtered)}</dd></div>
        <div><dt>待执行（全量）</dt><dd id="visit-summary-total">${escapeHtml(total)}</dd></div>
        <div><dt>记录数</dt><dd>${escapeHtml(item.row_count ?? 0)}</dd></div>
        <div><dt>失败任务</dt><dd>${escapeHtml(item.failed_task_count ?? 0)}</dd></div>
        <div><dt>需人工处理</dt><dd>${escapeHtml(item.manual_required_count ?? 0)}</dd></div>
      `;
    }
  }

  const loadSummary = () => getJson("/api/ops/modules/visit/summary");
  const loadPending = () => {
    const q = owner ? `?visit_owner=${encodeURIComponent(owner)}&limit=200` : "?limit=200";
    return getJson(`/api/ops/modules/visit/pending${q}`);
  };
  const loadRecent = () => getJson("/api/ops/modules/visit/recent/visit?limit=100");
  const loadOwners = () => getJson("/api/ops/modules/visit/owners");
  const loadHealth = () => getJson("/health/modules/visit");

  await Promise.allSettled([
    loadSection({
      sectionName: "模块健康检查",
      loader: loadHealth,
      errorId: "visit-health-error",
      retryId: "visit-health-retry",
      onSuccess: (result) => {
        renderModuleHealthPanel("visit", result.item);
      },
    }),
    loadSection({
      sectionName: "模块状态",
      loader: loadSummary,
      errorId: "visit-summary-error",
      retryId: "visit-summary-retry",
      onSuccess: (result) => {
        renderDataMeta("visit-summary-meta", result.meta);
        visitState.summaryItem = result.item || {};
        if (Number.isFinite(Number(visitState.summaryItem?.planned_tasks))) {
          visitState.totalCount = Number(visitState.summaryItem.planned_tasks);
        }
        renderVisitSummary();
      },
    }),
    loadSection({
      sectionName: "待执行任务",
      loader: loadPending,
      errorId: "visit-pending-error",
      retryId: "visit-pending-retry",
      onSuccess: (result) => {
        renderDataMeta("visit-pending-meta", result.meta);
        const items = result.items || [];
        visitState.pendingItems = items;
        visitState.pendingPage = 1;
        renderVisitPending();
        const executableCount = items.filter((item) => item.can_execute).length;
        const executeAllBtn = document.getElementById("visit-execute-all-btn");
        if (executeAllBtn) {
          executeAllBtn.dataset.totalCount = String(executableCount);
          executeAllBtn.disabled = executableCount === 0;
          executeAllBtn.textContent = `一键创建并闭环全部（后台执行 ${executableCount}）`;
        }
        visitState.filteredCount = items.length;
        if (Number.isFinite(Number(visitState.summaryItem?.planned_tasks))) {
          visitState.totalCount = Number(visitState.summaryItem.planned_tasks);
        } else if (!owner) {
          visitState.totalCount = items.length;
        }
        renderVisitSummary();
      },
    }),
    loadSection({
      sectionName: "最近闭环",
      loader: loadRecent,
      errorId: "visit-recent-error",
      retryId: "visit-recent-retry",
      onSuccess: (result) => {
        renderDataMeta("visit-recent-meta", result.meta);
        visitState.recentItems = result.items || [];
        visitState.recentPage = 1;
        renderVisitRecent();
      },
    }),
    loadSection({
      sectionName: "回访人列表",
      loader: loadOwners,
      errorId: "visit-summary-error",
      retryId: "visit-summary-retry",
      onSuccess: (result) => {
        const datalist = document.getElementById("visit-owner-options");
        if (!datalist) return;
        const owners = result.items || [];
        datalist.innerHTML = `<option value="全部"></option>${owners.map((owner) => `<option value="${escapeHtml(owner)}"></option>`).join("")}`;
      },
    }),
  ]);

  await refreshVisitAutoExecuteStats();

  const retryMap = [
    ["visit-health-retry", () => loadVisitModuleData(meta)],
    ["visit-summary-retry", () => loadVisitModuleData(meta)],
    ["visit-pending-retry", () => loadVisitModuleData(meta)],
    ["visit-recent-retry", () => loadVisitModuleData(meta)],
  ];
  retryMap.forEach(([id, fn]) => {
    const btn = document.getElementById(id);
    if (!btn || btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", async () => {
      await fn();
    });
  });

  const recentPrevBtn = document.getElementById("visit-recent-prev");
  if (recentPrevBtn && recentPrevBtn.dataset.bound !== "1") {
    recentPrevBtn.dataset.bound = "1";
    recentPrevBtn.addEventListener("click", () => {
      if (visitState.recentPage <= 1) return;
      visitState.recentPage -= 1;
      renderVisitRecent();
    });
  }
  const recentNextBtn = document.getElementById("visit-recent-next");
  if (recentNextBtn && recentNextBtn.dataset.bound !== "1") {
    recentNextBtn.dataset.bound = "1";
    recentNextBtn.addEventListener("click", () => {
      const totalPages = Math.max(1, Math.ceil((visitState.recentItems || []).length / (visitState.recentPageSize || 5)));
      if (visitState.recentPage >= totalPages) return;
      visitState.recentPage += 1;
      renderVisitRecent();
    });
  }
  const pendingPrevBtn = document.getElementById("visit-pending-prev");
  if (pendingPrevBtn && pendingPrevBtn.dataset.bound !== "1") {
    pendingPrevBtn.dataset.bound = "1";
    pendingPrevBtn.addEventListener("click", () => {
      if (visitState.pendingPage <= 1) return;
      visitState.pendingPage -= 1;
      renderVisitPending();
    });
  }
  const pendingNextBtn = document.getElementById("visit-pending-next");
  if (pendingNextBtn && pendingNextBtn.dataset.bound !== "1") {
    pendingNextBtn.dataset.bound = "1";
    pendingNextBtn.addEventListener("click", () => {
      const totalPages = Math.max(1, Math.ceil((visitState.pendingItems || []).length / (visitState.pendingPageSize || 5)));
      if (visitState.pendingPage >= totalPages) return;
      visitState.pendingPage += 1;
      renderVisitPending();
    });
  }
}

async function loadInspectionModuleData(meta) {
  const month = (meta.dataset.inspectionMonth || "").trim();
  const monthQuery = month ? `?month=${encodeURIComponent(month)}` : "";
  const inspectionState = {
    summaryItem: null,
    pendingCount: null,
    pendingItems: [],
    pendingPage: 1,
    pendingPageSize: 5,
  };

  function renderInspectionPending() {
    const tbody = document.getElementById("inspection-pending-tbody");
    if (!tbody) return;
    const items = inspectionState.pendingItems || [];
    const pageSize = inspectionState.pendingPageSize || 5;
    const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
    if (inspectionState.pendingPage > totalPages) {
      inspectionState.pendingPage = totalPages;
    }
    const currentPage = Math.max(1, inspectionState.pendingPage || 1);
    const startIndex = (currentPage - 1) * pageSize;
    const pageItems = items.slice(startIndex, startIndex + pageSize);

    if (!items.length) {
      tbody.innerHTML = "";
      setElementVisible("inspection-pending-empty", true);
      setElementVisible("inspection-pending-pagination", false);
      return;
    }

    setElementVisible("inspection-pending-empty", false);
    setElementVisible("inspection-pending-pagination", true);
    tbody.innerHTML = pageItems
      .map((item) => `
        <tr>
          <td>${escapeHtml(item.inspection_month || month || "-")}</td>
          <td>${escapeHtml(item.customer_name || "未知客户")}</td>
          <td>
            <span class="badge status-${escapeHtml(item.business_state_tone || item.state_tone || "unknown")}">${escapeHtml(item.business_state_label || item.state_label || "待确认")}</span>
            <div class="technical-hint">技术态：${escapeHtml(item.technical_state_label || "未执行")}</div>
          </td>
          <td>${escapeHtml(item.executor_name || "未识别")}</td>
          <td>${item.work_order_link ? `<a href="${escapeHtml(item.work_order_link)}" target="_blank" rel="noreferrer">工单链接</a>` : "无"}</td>
          <td>${escapeHtml(item.report_word_file || "未匹配到 Word 报告")}</td>
          <td>${escapeHtml(item.latest_run_time ? fmtDateTime(item.latest_run_time) : "未执行")}</td>
          <td>
            <div>
              <a href="${escapeHtml(item.detail_url || "#")}">查看任务</a>
              <button class="action-button secondary" data-action="preview" data-task-id="${escapeHtml(item.task_plan_id)}">预览执行</button>
              <button class="action-button danger" data-action="execute" data-task-id="${escapeHtml(item.task_plan_id)}" data-dry-run="false" ${item.can_execute ? "" : "disabled"}>上传报告并闭环</button>
            </div>
            <div class="technical-hint">${escapeHtml(item.technical_detail || "")}</div>
          </td>
        </tr>
      `)
      .join("");

    setElementText("inspection-pending-page-info", `第 ${currentPage} 页 / 共 ${totalPages} 页`);
    const prevBtn = document.getElementById("inspection-pending-prev");
    const nextBtn = document.getElementById("inspection-pending-next");
    if (prevBtn) prevBtn.disabled = currentPage <= 1;
    if (nextBtn) nextBtn.disabled = currentPage >= totalPages;
  }

  function renderInspectionSummary() {
    const item = inspectionState.summaryItem || {};
    const badge = document.getElementById("inspection-summary-sync-badge");
    if (badge) {
      badge.className = `badge status-${item.latest_sync_status || "unknown"}`;
      badge.textContent = item.latest_sync_status_label || "未同步";
    }
    const pendingText = inspectionState.pendingCount == null ? "加载中" : String(inspectionState.pendingCount);
    const stats = document.getElementById("inspection-summary-stats");
    if (stats) {
      stats.innerHTML = `
        <div><dt>最近同步</dt><dd>${escapeHtml(fmtDateTime(item.latest_snapshot_time))}</dd></div>
        <div><dt>最近执行</dt><dd>${escapeHtml(item.latest_execute_status_label || "暂无")}</dd></div>
        <div><dt>当月待闭环</dt><dd id="inspection-summary-pending">${escapeHtml(pendingText)}</dd></div>
        <div><dt>记录数</dt><dd>${escapeHtml(item.row_count ?? 0)}</dd></div>
        <div><dt>失败任务</dt><dd>${escapeHtml(item.failed_task_count ?? 0)}</dd></div>
        <div><dt>需人工处理</dt><dd>${escapeHtml(item.manual_required_count ?? 0)}</dd></div>
      `;
    }
  }

  await Promise.allSettled([
    loadSection({
      sectionName: "模块健康检查",
      loader: () => getJson("/health/modules/inspection"),
      errorId: "inspection-health-error",
      retryId: "inspection-health-retry",
      onSuccess: (result) => {
        renderModuleHealthPanel("inspection", result.item);
      },
    }),
    loadSection({
      sectionName: "模块状态",
      loader: () => getJson("/api/ops/modules/inspection/summary"),
      errorId: "inspection-summary-error",
      retryId: "inspection-summary-retry",
      onSuccess: (result) => {
        renderDataMeta("inspection-summary-meta", result.meta);
        inspectionState.summaryItem = result.item || {};
        renderInspectionSummary();
      },
    }),
    loadSection({
      sectionName: "待闭环任务",
      loader: () => getJson(`/api/ops/modules/inspection/pending${monthQuery ? `${monthQuery}&limit=100` : "?limit=100"}`),
      errorId: "inspection-pending-error",
      retryId: "inspection-pending-retry",
      onSuccess: (result) => {
        renderDataMeta("inspection-pending-meta", result.meta);
        const items = result.items || [];
        inspectionState.pendingItems = items;
        inspectionState.pendingPage = 1;
        renderInspectionPending();
        const executable = items.filter((item) => item.can_execute).length;
        const btn = document.getElementById("inspection-execute-all-btn");
        if (btn) {
          btn.dataset.totalCount = String(executable);
          btn.disabled = executable === 0;
          btn.textContent = `一键上传报告并闭环全部（${executable}）`;
        }
        inspectionState.pendingCount = items.length;
        renderInspectionSummary();
      },
    }),
    loadSection({
      sectionName: "最近闭环",
      loader: () => getJson(`/api/ops/modules/inspection/recent/inspection${monthQuery ? `${monthQuery}&limit=10` : "?limit=10"}`),
      errorId: "inspection-recent-error",
      retryId: "inspection-recent-retry",
      onSuccess: (result) => {
        renderDataMeta("inspection-recent-meta", result.meta);
        const items = result.items || [];
        const tbody = document.getElementById("inspection-recent-tbody");
        if (!tbody) return;
        if (!items.length) {
          tbody.innerHTML = "";
          setElementVisible("inspection-recent-empty", true);
        } else {
          setElementVisible("inspection-recent-empty", false);
          tbody.innerHTML = items
            .map((item) => `
              <tr>
                <td>${escapeHtml(item.inspection_month || month || "-")}</td>
                <td>${escapeHtml(item.customer_name || "未知客户")}</td>
                <td>${escapeHtml(fmtDateTime(item.occurred_at))}</td>
                <td><a href="${escapeHtml(item.final_link)}" target="_blank" rel="noreferrer">${escapeHtml(item.final_link)}</a></td>
                <td>${item.detail_url ? `<a href="${escapeHtml(item.detail_url)}">查看执行记录</a>` : ""}</td>
              </tr>
            `)
            .join("");
        }
      },
    }),
    loadSection({
      sectionName: "已审核工单",
      loader: () => getJson(`/api/ops/modules/inspection/reviewed/inspection${monthQuery ? `${monthQuery}&limit=100` : "?limit=100"}`),
      errorId: "inspection-reviewed-error",
      retryId: "inspection-reviewed-retry",
      onSuccess: (result) => {
        renderDataMeta("inspection-reviewed-meta", result.meta);
        const items = result.items || [];
        const tbody = document.getElementById("inspection-reviewed-tbody");
        if (!tbody) return;
        if (!items.length) {
          tbody.innerHTML = "";
          setElementVisible("inspection-reviewed-empty", true);
        } else {
          setElementVisible("inspection-reviewed-empty", false);
          tbody.innerHTML = items
            .map((item) => `
              <tr>
                <td>${escapeHtml(item.inspection_month || month || "-")}</td>
                <td>${escapeHtml(item.customer_name || "未知客户")}</td>
                <td><span class="badge status-${escapeHtml(item.state_tone || "unknown")}">${escapeHtml(item.state_label || "已审核工单（无需处理）")}</span></td>
                <td>${item.work_order_link ? `<a href="${escapeHtml(item.work_order_link)}" target="_blank" rel="noreferrer">工单链接</a>` : "无"}</td>
                <td>${escapeHtml(item.latest_run_time ? fmtDateTime(item.latest_run_time) : "未执行")}</td>
                <td>${item.detail_url ? `<a href="${escapeHtml(item.detail_url)}">查看任务</a>` : ""}</td>
              </tr>
            `)
            .join("");
        }
      },
    }),
    loadSection({
      sectionName: "无需处理",
      loader: () => getJson(`/api/ops/modules/inspection/no-action/inspection${monthQuery ? `${monthQuery}&limit=100` : "?limit=100"}`),
      errorId: "inspection-no-action-error",
      retryId: "inspection-no-action-retry",
      onSuccess: (result) => {
        renderDataMeta("inspection-no-action-meta", result.meta);
        const items = result.items || [];
        const tbody = document.getElementById("inspection-no-action-tbody");
        if (!tbody) return;
        if (!items.length) {
          tbody.innerHTML = "";
          setElementVisible("inspection-no-action-empty", true);
        } else {
          setElementVisible("inspection-no-action-empty", false);
          tbody.innerHTML = items
            .map((item) => `
              <tr>
                <td>${escapeHtml(item.inspection_month || month || "-")}</td>
                <td>${escapeHtml(item.customer_name || "未知客户")}</td>
                <td><span class="badge status-${escapeHtml(item.state_tone || "unknown")}">${escapeHtml(item.state_label || "无需处理")}</span></td>
                <td>${escapeHtml(item.executor_name || "未识别")}</td>
                <td>${item.work_order_link ? `<a href="${escapeHtml(item.work_order_link)}" target="_blank" rel="noreferrer">工单链接</a>` : "无"}</td>
                <td>${escapeHtml(item.latest_run_time ? fmtDateTime(item.latest_run_time) : "未执行")}</td>
                <td>${item.detail_url ? `<a href="${escapeHtml(item.detail_url)}">查看任务</a>` : ""}</td>
              </tr>
            `)
            .join("");
        }
      },
    }),
  ]);

  ["inspection-health-retry", "inspection-summary-retry", "inspection-pending-retry", "inspection-recent-retry", "inspection-reviewed-retry", "inspection-no-action-retry"].forEach((id) => {
    const btn = document.getElementById(id);
    if (!btn || btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", async () => {
      await loadInspectionModuleData(meta);
    });
  });
  const pendingPrevBtn = document.getElementById("inspection-pending-prev");
  if (pendingPrevBtn && pendingPrevBtn.dataset.bound !== "1") {
    pendingPrevBtn.dataset.bound = "1";
    pendingPrevBtn.addEventListener("click", () => {
      if (inspectionState.pendingPage <= 1) return;
      inspectionState.pendingPage -= 1;
      renderInspectionPending();
    });
  }
  const pendingNextBtn = document.getElementById("inspection-pending-next");
  if (pendingNextBtn && pendingNextBtn.dataset.bound !== "1") {
    pendingNextBtn.dataset.bound = "1";
    pendingNextBtn.addEventListener("click", () => {
      const totalPages = Math.max(1, Math.ceil((inspectionState.pendingItems || []).length / (inspectionState.pendingPageSize || 5)));
      if (inspectionState.pendingPage >= totalPages) return;
      inspectionState.pendingPage += 1;
      renderInspectionPending();
    });
  }
}

async function loadProactiveModuleData(meta) {
  const owner = (meta.dataset.visitOwner || "").trim();
  const proactiveState = {
    summaryItem: null,
    filteredCount: null,
    totalCount: null,
    pendingItems: [],
    pendingPage: 1,
    pendingPageSize: 5,
    recentItems: [],
    recentPage: 1,
    recentPageSize: 5,
  };

  function summarizeAutoExecutable(items) {
    const safeItems = Array.isArray(items) ? items : [];
    const executableCount = safeItems.filter((item) => item.can_execute).length;
    const blockedItems = safeItems.filter((item) => !item.can_execute);
    const reasonCounts = new Map();
    blockedItems.forEach((item) => {
      const reason = item.business_state_label || item.state_label || item.technical_state_label || "不可自动执行";
      reasonCounts.set(reason, (reasonCounts.get(reason) || 0) + 1);
    });
    const topReason = [...reasonCounts.entries()]
      .sort((a, b) => b[1] - a[1])[0];
    return {
      totalCount: safeItems.length,
      executableCount,
      blockedCount: blockedItems.length,
      topReasonText: topReason ? `${topReason[0]} (${topReason[1]})` : "无",
    };
  }

  function renderProactiveAutoExecuteStats(summary, sourceLabel) {
    const stats = document.getElementById("proactive-auto-execute-stats");
    if (stats) {
      stats.innerHTML = `
        <div><dt>18:00自动执行（全量）</dt><dd>${escapeHtml(summary.executableCount)}</dd></div>
        <div><dt>不会自动执行</dt><dd>${escapeHtml(summary.blockedCount)}</dd></div>
        <div><dt>阻塞原因 Top1</dt><dd>${escapeHtml(summary.topReasonText)}</dd></div>
      `;
    }
    const note = document.getElementById("proactive-auto-execute-note");
    if (note) {
      note.textContent = `${sourceLabel}待处理 ${summary.totalCount} 个，其中 ${summary.executableCount} 个会在 18:00 自动执行。`;
    }
  }

  async function refreshProactiveAutoExecuteStats() {
    try {
      const sourceItems = owner
        ? (await getJson("/api/ops/modules/proactive/pending?limit=5000")).items || []
        : (proactiveState.pendingItems || []);
      renderProactiveAutoExecuteStats(summarizeAutoExecutable(sourceItems), "模块全量");
    } catch (error) {
      const note = document.getElementById("proactive-auto-execute-note");
      if (note) {
        note.textContent = `自动执行前统计加载失败：${error.message}`;
      }
    }
  }

  function renderProactivePending() {
    const tbody = document.getElementById("proactive-pending-tbody");
    if (!tbody) return;
    const items = proactiveState.pendingItems || [];
    const pageSize = proactiveState.pendingPageSize || 5;
    const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
    if (proactiveState.pendingPage > totalPages) {
      proactiveState.pendingPage = totalPages;
    }
    const currentPage = Math.max(1, proactiveState.pendingPage || 1);
    const startIndex = (currentPage - 1) * pageSize;
    const pageItems = items.slice(startIndex, startIndex + pageSize);

    if (!items.length) {
      tbody.innerHTML = "";
      setElementVisible("proactive-pending-empty", true);
      setElementVisible("proactive-pending-pagination", false);
      return;
    }

    setElementVisible("proactive-pending-empty", false);
    setElementVisible("proactive-pending-pagination", true);
    tbody.innerHTML = pageItems
      .map((item) => `
        <tr>
          <td>${escapeHtml(item.customer_name || "未知客户")}</td>
          <td>${escapeHtml(item.visit_owner || "未识别")}</td>
          <td>${escapeHtml(item.latest_run_time ? fmtDateTime(item.latest_run_time) : "未执行")}</td>
          <td>
            <div>${escapeHtml(item.business_explanation || "-")}</div>
            <div class="technical-hint">技术态：${escapeHtml(item.technical_state_label || "未执行")} ${escapeHtml(item.technical_detail || "")}</div>
          </td>
          <td>
            <a href="${escapeHtml(item.detail_url || "#")}">查看任务</a>
            <button class="action-button secondary" data-action="precheck" data-task-id="${escapeHtml(item.task_plan_id)}">预检查</button>
            <button class="action-button danger" data-action="execute" data-task-id="${escapeHtml(item.task_plan_id)}" data-dry-run="false">执行</button>
          </td>
        </tr>
      `)
      .join("");

    setElementText("proactive-pending-page-info", `第 ${currentPage} 页 / 共 ${totalPages} 页`);
    const prevBtn = document.getElementById("proactive-pending-prev");
    const nextBtn = document.getElementById("proactive-pending-next");
    if (prevBtn) prevBtn.disabled = currentPage <= 1;
    if (nextBtn) nextBtn.disabled = currentPage >= totalPages;
  }

  function renderProactiveSummary() {
    const item = proactiveState.summaryItem || {};
    const badge = document.getElementById("proactive-summary-sync-badge");
    if (badge) {
      badge.className = `badge status-${item.latest_sync_status || "unknown"}`;
      badge.textContent = item.latest_sync_status_label || "未同步";
    }
    const filtered = proactiveState.filteredCount == null ? "加载中" : String(proactiveState.filteredCount);
    const total = proactiveState.totalCount == null ? "加载中" : String(proactiveState.totalCount);
    const stats = document.getElementById("proactive-summary-stats");
    if (stats) {
      stats.innerHTML = `
        <div><dt>最近同步</dt><dd>${escapeHtml(fmtDateTime(item.latest_snapshot_time))}</dd></div>
        <div><dt>最近执行</dt><dd>${escapeHtml(item.latest_execute_status_label || "暂无")}</dd></div>
        <div><dt>待执行（当前筛选）</dt><dd id="proactive-summary-filtered">${escapeHtml(filtered)}</dd></div>
        <div><dt>待执行（全量）</dt><dd id="proactive-summary-total">${escapeHtml(total)}</dd></div>
        <div><dt>记录数</dt><dd>${escapeHtml(item.row_count ?? 0)}</dd></div>
        <div><dt>失败任务</dt><dd>${escapeHtml(item.failed_task_count ?? 0)}</dd></div>
        <div><dt>需人工处理</dt><dd>${escapeHtml(item.manual_required_count ?? 0)}</dd></div>
      `;
    }
  }

  function renderProactiveRecent() {
    const tbody = document.getElementById("proactive-recent-tbody");
    if (!tbody) return;
    const items = proactiveState.recentItems || [];
    const pageSize = proactiveState.recentPageSize || 5;
    const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
    if (proactiveState.recentPage > totalPages) {
      proactiveState.recentPage = totalPages;
    }
    const currentPage = Math.max(1, proactiveState.recentPage || 1);
    const startIndex = (currentPage - 1) * pageSize;
    const pageItems = items.slice(startIndex, startIndex + pageSize);

    if (!items.length) {
      tbody.innerHTML = "";
      setElementVisible("proactive-recent-empty", true);
      setElementVisible("proactive-recent-pagination", false);
      return;
    }

    setElementVisible("proactive-recent-empty", false);
    setElementVisible("proactive-recent-pagination", true);
    tbody.innerHTML = pageItems
      .map((item) => `
        <tr>
          <td>${escapeHtml(item.customer_name || "未知客户")}</td>
          <td>${escapeHtml(item.visit_type || "客户满意度调研")}</td>
          <td>${escapeHtml(fmtDateTime(item.occurred_at))}</td>
          <td>${item.final_link ? `<a href="${escapeHtml(item.final_link)}" target="_blank" rel="noreferrer">打开回访链接</a>` : '<span class="muted">无</span>'}</td>
        </tr>
      `)
      .join("");

    setElementText("proactive-recent-page-info", `第 ${currentPage} 页 / 共 ${totalPages} 页`);
    const prevBtn = document.getElementById("proactive-recent-prev");
    const nextBtn = document.getElementById("proactive-recent-next");
    if (prevBtn) prevBtn.disabled = currentPage <= 1;
    if (nextBtn) nextBtn.disabled = currentPage >= totalPages;
  }

  const loadPending = () => {
    const q = owner ? `?visit_owner=${encodeURIComponent(owner)}&limit=100` : "?limit=100";
    return getJson(`/api/ops/modules/proactive/pending${q}`);
  };

  await Promise.allSettled([
    loadSection({
      sectionName: "模块健康检查",
      loader: () => getJson("/health/modules/proactive"),
      errorId: "proactive-health-error",
      retryId: "proactive-health-retry",
      onSuccess: (result) => {
        renderModuleHealthPanel("proactive", result.item);
      },
    }),
    loadSection({
      sectionName: "模块状态",
      loader: () => getJson("/api/ops/modules/proactive/summary"),
      errorId: "proactive-summary-error",
      retryId: "proactive-summary-retry",
      onSuccess: (result) => {
        renderDataMeta("proactive-summary-meta", result.meta);
        proactiveState.summaryItem = result.item || {};
        if (proactiveState.totalCount == null && Number.isFinite(Number(proactiveState.summaryItem?.planned_tasks))) {
          proactiveState.totalCount = Number(proactiveState.summaryItem.planned_tasks);
        }
        renderProactiveSummary();
      },
    }),
    loadSection({
      sectionName: "待处理任务",
      loader: loadPending,
      errorId: "proactive-pending-error",
      retryId: "proactive-pending-retry",
      onSuccess: (result) => {
        renderDataMeta("proactive-pending-meta", result.meta);
        const items = result.items || [];
        proactiveState.pendingItems = items;
        proactiveState.pendingPage = 1;
        renderProactivePending();
        const executableCount = items.filter((item) => item.can_execute).length;
        const executeAllBtn = document.getElementById("proactive-execute-all-btn");
        if (executeAllBtn) {
          executeAllBtn.dataset.totalCount = String(executableCount);
          executeAllBtn.disabled = executableCount === 0;
          executeAllBtn.textContent = `一键执行闭环全部（后台执行 ${executableCount}）`;
        }
        proactiveState.filteredCount = items.length;
        if (Number.isFinite(Number(proactiveState.summaryItem?.planned_tasks))) {
          proactiveState.totalCount = Number(proactiveState.summaryItem.planned_tasks);
        } else {
          proactiveState.totalCount = items.length;
        }
        renderProactiveSummary();
      },
    }),
    loadSection({
      sectionName: "回访人列表",
      loader: () => getJson("/api/ops/modules/proactive/owners"),
      errorId: "proactive-summary-error",
      retryId: "proactive-summary-retry",
      onSuccess: (result) => {
        const datalist = document.getElementById("proactive-owner-options");
        if (!datalist) return;
        const owners = result.items || [];
        datalist.innerHTML = `<option value="全部"></option>${owners.map((current) => `<option value="${escapeHtml(current)}"></option>`).join("")}`;
      },
    }),
    loadSection({
      sectionName: "最近闭环回访",
      loader: () => getJson("/api/ops/modules/proactive/recent/visit?limit=100"),
      errorId: "proactive-recent-error",
      retryId: "proactive-recent-retry",
      onSuccess: (result) => {
        renderDataMeta("proactive-recent-meta", result.meta);
        proactiveState.recentItems = result.items || [];
        proactiveState.recentPage = 1;
        renderProactiveRecent();
      },
    }),
  ]);

  await refreshProactiveAutoExecuteStats();
  ["proactive-health-retry", "proactive-summary-retry", "proactive-pending-retry", "proactive-recent-retry"].forEach((id) => {
    const btn = document.getElementById(id);
    if (!btn || btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", async () => {
      await loadProactiveModuleData(meta);
    });
  });
  const pendingPrevBtn = document.getElementById("proactive-pending-prev");
  if (pendingPrevBtn && pendingPrevBtn.dataset.bound !== "1") {
    pendingPrevBtn.dataset.bound = "1";
    pendingPrevBtn.addEventListener("click", () => {
      if (proactiveState.pendingPage <= 1) return;
      proactiveState.pendingPage -= 1;
      renderProactivePending();
    });
  }
  const pendingNextBtn = document.getElementById("proactive-pending-next");
  if (pendingNextBtn && pendingNextBtn.dataset.bound !== "1") {
    pendingNextBtn.dataset.bound = "1";
    pendingNextBtn.addEventListener("click", () => {
      const totalPages = Math.max(1, Math.ceil((proactiveState.pendingItems || []).length / (proactiveState.pendingPageSize || 5)));
      if (proactiveState.pendingPage >= totalPages) return;
      proactiveState.pendingPage += 1;
      renderProactivePending();
    });
  }
  const recentPrevBtn = document.getElementById("proactive-recent-prev");
  if (recentPrevBtn && recentPrevBtn.dataset.bound !== "1") {
    recentPrevBtn.dataset.bound = "1";
    recentPrevBtn.addEventListener("click", () => {
      if (proactiveState.recentPage <= 1) return;
      proactiveState.recentPage -= 1;
      renderProactiveRecent();
    });
  }
  const recentNextBtn = document.getElementById("proactive-recent-next");
  if (recentNextBtn && recentNextBtn.dataset.bound !== "1") {
    recentNextBtn.dataset.bound = "1";
    recentNextBtn.addEventListener("click", () => {
      const totalPages = Math.max(1, Math.ceil((proactiveState.recentItems || []).length / (proactiveState.recentPageSize || 5)));
      if (proactiveState.recentPage >= totalPages) return;
      proactiveState.recentPage += 1;
      renderProactiveRecent();
    });
  }
}

function renderDashboardSummaryCards(items) {
  const grid = document.getElementById("dashboard-summary-grid");
  if (!grid) return;
  if (!items.length) {
    grid.innerHTML = '<p class="muted">暂无模块数据。</p>';
    return;
  }
  grid.innerHTML = items
    .map((item) => {
      const scheduleText = item.schedule_enabled
        ? `${item.schedule_type || ""}:${item.schedule_value || ""}`
        : "未配置";
      const explanation = item.latest_execute_explanation
        ? `<p class="ops-note">${escapeHtml(item.latest_execute_explanation)}</p>`
        : "";
      return `
        <article class="card module-card">
          <div class="card-head">
            <div>
              <h4>${escapeHtml(item.module_name || "")}</h4>
              <p class="muted">${escapeHtml(item.module_code || "")}</p>
            </div>
            <span class="badge status-${escapeHtml(item.latest_sync_status || "unknown")}">${escapeHtml(item.latest_sync_status_label || "未同步")}</span>
          </div>
          <dl class="stats">
            <div><dt>最近同步</dt><dd>${escapeHtml(fmtDateTime(item.latest_snapshot_time))}</dd></div>
            <div><dt>最近执行</dt><dd>${escapeHtml(item.latest_execute_status_label || "暂无")}</dd></div>
            <div><dt>记录数</dt><dd>${escapeHtml(item.row_count ?? 0)}</dd></div>
            <div><dt>待执行</dt><dd>${escapeHtml(item.planned_tasks ?? 0)}</dd></div>
            <div><dt>已跳过</dt><dd>${escapeHtml(item.skipped_tasks ?? 0)}</dd></div>
            <div><dt>需人工处理</dt><dd>${escapeHtml(item.manual_required_count ?? 0)}</dd></div>
            <div><dt>失败任务</dt><dd>${escapeHtml(item.failed_task_count ?? 0)}</dd></div>
            <div><dt>可重试</dt><dd>${escapeHtml(item.retryable_task_count ?? 0)}</dd></div>
            <div><dt>调度</dt><dd>${escapeHtml(scheduleText)}</dd></div>
            <div><dt>运行中</dt><dd>${item.sync_running ? "是" : "否"}</dd></div>
          </dl>
          ${explanation}
          <div class="card-actions">
            <button class="action-button" data-action="sync" data-module-code="${escapeHtml(item.module_code || "")}">立即同步</button>
            <button class="action-button secondary" data-action="rerun-sync" data-module-code="${escapeHtml(item.module_code || "")}">重跑同步</button>
            <a class="module-entry-link" href="/console/modules/${escapeHtml(item.module_code || "")}">进入模块</a>
            <a href="/console/snapshots?module_code=${escapeHtml(item.module_code || "")}">查看快照</a>
            <a href="/console/tasks?module_code=${escapeHtml(item.module_code || "")}">查看任务</a>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderDashboardFailures(items) {
  const list = document.getElementById("dashboard-failures-list");
  if (!list) return;
  if (!items.length) {
    list.innerHTML = "";
    setElementVisible("dashboard-failures-empty", true);
    return;
  }
  setElementVisible("dashboard-failures-empty", false);
  list.innerHTML = items
    .map((item) => `
      <li>
        <strong>${escapeHtml(item.module_code || "")}</strong>
        <span class="badge status-${escapeHtml(item.status_tone || "unknown")}">${escapeHtml(item.display_status || "未知状态")}</span>
        <span>${escapeHtml(item.customer_name || item.title || "未命名")}</span>
        <span class="muted">${escapeHtml(item.business_explanation || "-")}</span>
        ${item.retryable ? '<span class="badge status-warning">可重试</span>' : ""}
        ${
          item.task_plan_id
            ? `<a href="${escapeHtml(item.detail_url || "#")}">查看详情</a>
               <button class="action-button secondary" data-action="rerun-task" data-task-id="${escapeHtml(item.task_plan_id)}" data-dry-run="false">重跑任务</button>`
            : (item.module_code ? `<button class="action-button secondary" data-action="rerun-sync" data-module-code="${escapeHtml(item.module_code)}">重跑同步</button>` : "")
        }
      </li>
    `)
    .join("");
}

function renderDashboardManualRequired(items) {
  const tbody = document.getElementById("dashboard-manual-tbody");
  if (!tbody) return;
  if (!items.length) {
    tbody.innerHTML = "";
    setElementVisible("dashboard-manual-empty", true);
    return;
  }
  setElementVisible("dashboard-manual-empty", false);
  tbody.innerHTML = items
    .map((item) => `
      <tr>
        <td>${escapeHtml(item.module_code || "")}</td>
        <td>${escapeHtml(item.customer_name || "未知客户")}</td>
        <td>${escapeHtml(item.task_plan_id || "-")}</td>
        <td><span class="badge status-${escapeHtml(item.status_tone || "unknown")}">${escapeHtml(item.display_status || "未知状态")}</span></td>
        <td>${escapeHtml(item.business_explanation || "-")}</td>
        <td>${escapeHtml(fmtDateTime(item.occurred_at))}</td>
        <td>
          ${item.detail_url ? `<a href="${escapeHtml(item.detail_url)}">查看详情</a>` : ""}
          ${item.task_plan_id ? `<button class="action-button secondary" data-action="rerun-task" data-task-id="${escapeHtml(item.task_plan_id)}" data-dry-run="false">重跑</button>` : ""}
        </td>
      </tr>
    `)
    .join("");
}

async function loadDashboardData() {
  await Promise.allSettled([
    loadSection({
      sectionName: "模块健康检查",
      loader: () => getJson("/health/modules"),
      errorId: "dashboard-health-error",
      retryId: "dashboard-health-retry",
      onSuccess: (result) => {
        renderDashboardHealth(result.items || []);
      },
    }),
    loadSection({
      sectionName: "模块总览",
      loader: () => getJson("/api/ops/dashboard/summary"),
      errorId: "dashboard-summary-error",
      retryId: "dashboard-summary-retry",
      onSuccess: (result) => {
        renderDataMeta("dashboard-summary-meta", result.meta);
        renderDashboardSummaryCards(result.items || []);
      },
    }),
    loadSection({
      sectionName: "失败任务",
      loader: () => getJson("/api/ops/dashboard/failures?limit=10"),
      errorId: "dashboard-failures-error",
      retryId: "dashboard-failures-retry",
      onSuccess: (result) => {
        renderDataMeta("dashboard-failures-meta", result.meta);
        renderDashboardFailures(result.items || []);
      },
    }),
    loadSection({
      sectionName: "人工处理清单",
      loader: () => getJson("/api/ops/dashboard/manual-required?limit=10"),
      errorId: "dashboard-manual-error",
      retryId: "dashboard-manual-retry",
      onSuccess: (result) => {
        renderDataMeta("dashboard-manual-meta", result.meta);
        renderDashboardManualRequired(result.items || []);
      },
    }),
  ]);
  ["dashboard-health-retry", "dashboard-summary-retry", "dashboard-failures-retry", "dashboard-manual-retry"].forEach((id) => {
    const btn = document.getElementById(id);
    if (!btn || btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", async () => {
      await loadDashboardData();
    });
  });
}

function initModulePageHydration() {
  const meta = document.getElementById("module-view-meta");
  if (!meta) return;
  const moduleCode = (meta.dataset.moduleCode || "").trim();
  if (moduleCode === "visit") {
    loadVisitModuleData(meta);
  } else if (moduleCode === "inspection") {
    loadInspectionModuleData(meta);
  } else if (moduleCode === "proactive") {
    loadProactiveModuleData(meta);
  }
}

function initDashboardHydration() {
  const meta = document.getElementById("dashboard-view-meta");
  if (!meta) return;
  loadDashboardData();
}

async function handleSync(button) {
  const moduleCode = button.dataset.moduleCode;
  const syncMonths = moduleCode === "inspection" ? getInspectionSyncMonths() : [];
  const visitOwner = moduleCode === "visit" ? getVisitOwnerFilter() : "";
  const proactiveOwner = moduleCode === "proactive" ? getProactiveOwnerFilter() : "";
  const ownerQuery = moduleCode === "visit" ? { visit_owner: visitOwner } : (moduleCode === "proactive" ? { visit_owner: proactiveOwner } : {});
  renderFeedback("warning", `正在同步 ${moduleCode}...`, { module_code: moduleCode, sync_months: syncMonths, visit_owner: visitOwner || null });
  const result = await postJson("/api/sync/run", { module_code: moduleCode, force: false, sync_months: syncMonths });
  renderFeedback("success", `同步完成：${moduleCode}`, result);
  scheduleRefresh(1200, ownerQuery);
}

async function handleSyncRerun(button) {
  const moduleCode = button.dataset.moduleCode;
  const syncMonths = moduleCode === "inspection" ? getInspectionSyncMonths() : [];
  const visitOwner = moduleCode === "visit" ? getVisitOwnerFilter() : "";
  const proactiveOwner = moduleCode === "proactive" ? getProactiveOwnerFilter() : "";
  const ownerQuery = moduleCode === "visit" ? { visit_owner: visitOwner } : (moduleCode === "proactive" ? { visit_owner: proactiveOwner } : {});
  renderFeedback("warning", `正在重跑同步 ${moduleCode}...`, { module_code: moduleCode, sync_months: syncMonths, visit_owner: visitOwner || null });
  const result = await postJson(`/api/modules/${moduleCode}/sync/rerun`, { sync_months: syncMonths });
  renderFeedback("success", `重跑完成：${moduleCode}`, result);
  scheduleRefresh(1200, ownerQuery);
}

async function handlePrecheck(button) {
  const taskId = button.dataset.taskId;
  renderFeedback("warning", `正在执行预检查 ${taskId}...`, { task_id: taskId });
  const result = await postJson(`/api/tasks/${taskId}/precheck`, {});
  renderFeedback("success", "预检查结果", result);
}

async function handlePreview(button) {
  const taskId = button.dataset.taskId;
  renderFeedback("warning", `正在生成执行预览 ${taskId}...`, { task_id: taskId });
  const result = await postJson(`/api/tasks/${taskId}/preview`, {});
  const preview = result?.item?.result_payload?.preview || {};
  const ready = preview.ready === true;
  renderFeedback(ready ? "success" : "warning", "执行预览结果", result);
}

async function handleExecute(button) {
  const taskId = button.dataset.taskId;
  const dryRun = button.dataset.dryRun === "true";
  const moduleCode = (button.dataset.moduleCode || "").trim();
  const inVisitPage = window.location.pathname.startsWith("/console/modules/visit");
  if (moduleCode !== "visit" && !inVisitPage) {
    renderFeedback("warning", `正在执行 task ${taskId}...`, { task_id: taskId, dry_run: dryRun });
    const result = await postJson(`/api/tasks/${taskId}/execute`, { dry_run: dryRun });
    const status = result.item?.run_status || "unknown";
    const kind = result.item?.manual_required ? "warning" : "success";
    renderFeedback(kind, `执行结果：${status}`, result);
    scheduleRefresh();
    return;
  }
  const visitOwner = getVisitOwnerFilter();
  renderFeedback("warning", `正在提交后台执行 task ${taskId}...`, { task_id: taskId, dry_run: dryRun });
  const result = await postJson(`/api/tasks/${taskId}/enqueue-execute`, { dry_run: dryRun });
  renderFeedback("warning", "已提交后台执行队列", result);
  await pollBatchStatus(result.batch_id, { extraQuery: { visit_owner: visitOwner } });
}

async function handleExecuteAllVisit(button) {
  const totalCount = Number.parseInt(button.dataset.totalCount || "0", 10);
  const visitOwner = getVisitOwnerFilter() || button.dataset.visitOwner || "";
  const ownerLabel = visitOwner ? `（回访人：${visitOwner}）` : "（全部回访人）";
  if (!window.confirm(`将把全部待执行回访任务提交到后台队列执行（当前 ${totalCount} 条）${ownerLabel}。是否继续？`)) {
    return;
  }
  renderFeedback("warning", `正在提交批量后台执行${ownerLabel}...`, { module_code: "visit", total_count: totalCount, visit_owner: visitOwner || null });
  const payload = { module_code: "visit", dry_run: false };
  if (visitOwner) {
    payload.visit_owner = visitOwner;
  }
  const result = await postJson("/api/tasks/batch/enqueue-pending", payload);
  renderFeedback("warning", "批量任务已入队，后台执行中", result);
  await pollBatchStatus(result.batch_id, { extraQuery: { visit_owner: visitOwner } });
}

async function handleExecuteAllProactive(button) {
  const totalCount = Number.parseInt(button.dataset.totalCount || "0", 10);
  const visitOwner = getProactiveOwnerFilter() || button.dataset.visitOwner || "";
  const ownerLabel = visitOwner ? `（回访人：${visitOwner}）` : "（全部回访人）";
  if (!window.confirm(`将把全部待处理主动回访任务提交到后台队列执行（当前 ${totalCount} 条）${ownerLabel}。是否继续？`)) {
    return;
  }
  renderFeedback("warning", `正在提交主动回访批量后台执行${ownerLabel}...`, { module_code: "proactive", total_count: totalCount, visit_owner: visitOwner || null });
  const payload = { module_code: "proactive", dry_run: false };
  if (visitOwner) {
    payload.visit_owner = visitOwner;
  }
  const result = await postJson("/api/tasks/batch/enqueue-pending", payload);
  renderFeedback("warning", "主动回访批量任务已入队，后台执行中", result);
  await pollBatchStatus(result.batch_id, { extraQuery: { visit_owner: visitOwner } });
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

async function loadExtensionStatus() {
  try {
    const result = await getJson("/extension/status");
    const badge = document.getElementById("pts-extension-badge");
    const detail = document.getElementById("pts-extension-detail");
    if (badge) {
      const state = result.extension_connection?.status || "disconnected";
      const classMap = {
        connected: "success",
        idle: "warning",
        disconnected: "unknown",
      };
      badge.className = `badge status-${classMap[state] || "unknown"}`;
      badge.textContent = result.extension_connection?.label || "浏览器扩展未连接";
    }
    if (detail) {
      const session = result.pts_session || {};
      const extensionDetail = result.extension_connection?.detail || "尚未收到浏览器扩展心跳。";
      const sessionDetail = session.configured
        ? `当前 PTS 会话来源：${session.source || "env_file"}。`
        : "当前还没有可用 PTS 会话。";
      detail.textContent = `${extensionDetail} ${sessionDetail}`;
    }
  } catch (_error) {
    // Ignore extension status failures so manual cookie flow remains available.
  }
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
    if (button.dataset.action === "preview") {
      await handlePreview(button);
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
    if (button.dataset.action === "execute-all-proactive") {
      await handleExecuteAllProactive(button);
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

document.addEventListener("DOMContentLoaded", async () => {
  await loadExtensionStatus();
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
  initDashboardHydration();
  initModulePageHydration();
});
