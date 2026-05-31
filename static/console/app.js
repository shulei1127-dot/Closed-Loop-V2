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

function truncate(value, maxLen = 120) {
  const s = String(value || "");
  return s.length > maxLen ? s.substring(0, maxLen) + "…" : s;
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
            <div title="${escapeHtml(item.business_explanation || "-")}">${escapeHtml(truncate(item.business_explanation || "-"))}</div>
            <div class="technical-hint" title="${escapeHtml(item.technical_detail || "")}">${escapeHtml(truncate(item.technical_detail || "", 80))}</div>
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
      if (item.latest_sync_status) {
        badge.className = `badge status-${item.latest_sync_status}`;
        badge.textContent = item.latest_sync_status_label;
        badge.style.display = "";
      } else {
        badge.style.display = "none";
      }
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

  await Promise.allSettled([
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

async function loadReviewModuleData(meta) {
  const reviewState = {
    summaryItem: null,
    pendingItems: [],
    pendingPage: 1,
    pendingPageSize: 5,
  };

  function renderReviewSummary() {
    const item = reviewState.summaryItem || {};
    const badge = document.getElementById("review-summary-sync-badge");
    if (badge) {
      if (item.latest_sync_status) {
        badge.className = `badge status-${item.latest_sync_status}`;
        badge.textContent = item.latest_sync_status_label;
        badge.style.display = "";
      } else {
        badge.style.display = "none";
      }
    }
    const passedCount = (item.row_count ?? 0) - (item.planned_tasks ?? 0) - (item.failed_task_count ?? 0) - (item.manual_required_count ?? 0);
    const stats = document.getElementById("review-summary-stats");
    if (stats) {
      stats.innerHTML = `
        <div><dt>最近同步</dt><dd>${escapeHtml(fmtDateTime(item.latest_snapshot_time))}</dd></div>
        <div><dt>最近执行</dt><dd>${escapeHtml(item.latest_execute_status_label || "暂无")}</dd></div>
        <div><dt>待审核任务</dt><dd>${escapeHtml(item.planned_tasks ?? 0)}</dd></div>
        <div><dt>审核通过</dt><dd>${escapeHtml(passedCount)}</dd></div>
        <div><dt>审核失败</dt><dd>${escapeHtml(item.failed_task_count ?? 0)}</dd></div>
        <div><dt>需人工处理</dt><dd>${escapeHtml(item.manual_required_count ?? 0)}</dd></div>
      `;
    }
  }

  function renderReviewPending() {
    const tbody = document.getElementById("review-pending-tbody");
    if (!tbody) return;
    const items = reviewState.pendingItems || [];
    const pageSize = reviewState.pendingPageSize || 5;
    const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
    if (reviewState.pendingPage > totalPages) {
      reviewState.pendingPage = totalPages;
    }
    const currentPage = Math.max(1, reviewState.pendingPage || 1);
    const startIndex = (currentPage - 1) * pageSize;
    const pageItems = items.slice(startIndex, startIndex + pageSize);

    if (!items.length) {
      tbody.innerHTML = "";
      setElementVisible("review-pending-empty", true);
      setElementVisible("review-pending-pagination", false);
      return;
    }

    setElementVisible("review-pending-empty", false);
    setElementVisible("review-pending-pagination", true);
    tbody.innerHTML = pageItems
      .map((item) => {
        const payload = item.planned_payload || {};
        return `
        <tr>
          <td>${escapeHtml(item.customer_name || payload.customer_name || "未知客户")}</td>
          <td>${escapeHtml(payload.project_name || "-")}</td>
          <td>${escapeHtml(payload.delivery_stage || item.visit_type || item.task_type || "-")}</td>
          <td>${escapeHtml(payload.after_sales_leader || item.visit_owner || "未识别")}</td>
          <td>${escapeHtml(item.latest_run_time ? fmtDateTime(item.latest_run_time) : "未执行")}</td>
          <td>
            <span class="badge status-${escapeHtml(item.business_state_tone || item.state_tone || "unknown")}">${escapeHtml(item.business_state_label || item.state_label || "待处理")}</span>
            <div class="technical-hint">技术态：${escapeHtml(item.technical_state_label || "未执行")}</div>
            <div title="${escapeHtml(item.business_explanation || "-")}">${escapeHtml(truncate(item.business_explanation || "-"))}</div>
          </td>
          <td>
            <a href="${escapeHtml(item.detail_url || "#")}">查看任务</a>
            ${item.can_execute ? `<button class="action-button danger" data-action="execute" data-module-code="review" data-task-id="${escapeHtml(item.task_plan_id)}" data-dry-run="false">一键审核</button>` : '<span class="muted">不可自动执行</span>'}
          </td>
        </tr>
      `;
      })
      .join("");

    setElementText("review-pending-page-info", `第 ${currentPage} 页 / 共 ${totalPages} 页`);
    const prevBtn = document.getElementById("review-pending-prev");
    const nextBtn = document.getElementById("review-pending-next");
    if (prevBtn) prevBtn.disabled = currentPage <= 1;
    if (nextBtn) nextBtn.disabled = currentPage >= totalPages;
  }

  await Promise.allSettled([
    loadSection({
      sectionName: "模块状态",
      loader: () => getJson("/api/ops/modules/review/summary"),
      errorId: "review-summary-error",
      retryId: "review-summary-retry",
      onSuccess: (result) => {
        renderDataMeta("review-summary-meta", result.meta);
        reviewState.summaryItem = result.item || {};
        renderReviewSummary();
      },
    }),
    loadSection({
      sectionName: "待审核项目",
      loader: () => getJson("/api/ops/modules/review/pending?limit=200"),
      errorId: "review-pending-error",
      retryId: "review-pending-retry",
      onSuccess: (result) => {
        const items = result.items || [];
        reviewState.pendingItems = items;
        reviewState.pendingPage = 1;
        renderReviewPending();
        const executableCount = items.filter((item) => item.can_execute).length;
        const executeAllBtn = document.getElementById("review-execute-all-btn");
        if (executeAllBtn) {
          executeAllBtn.dataset.totalCount = String(executableCount);
          executeAllBtn.disabled = executableCount === 0;
          executeAllBtn.textContent = `一键审核全部（后台执行 ${executableCount}）`;
        }
        renderReviewSummary();
      },
    }),
  ]);

  const retryBtns = ["review-summary-retry", "review-pending-retry"];
  retryBtns.forEach((id) => {
    const btn = document.getElementById(id);
    if (!btn || btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", async () => {
      await loadReviewModuleData(meta);
    });
  });
  const pendingPrevBtn = document.getElementById("review-pending-prev");
  if (pendingPrevBtn && pendingPrevBtn.dataset.bound !== "1") {
    pendingPrevBtn.dataset.bound = "1";
    pendingPrevBtn.addEventListener("click", () => {
      if (reviewState.pendingPage <= 1) return;
      reviewState.pendingPage -= 1;
      renderReviewPending();
    });
  }
  const pendingNextBtn = document.getElementById("review-pending-next");
  if (pendingNextBtn && pendingNextBtn.dataset.bound !== "1") {
    pendingNextBtn.dataset.bound = "1";
    pendingNextBtn.addEventListener("click", () => {
      const totalPages = Math.max(1, Math.ceil((reviewState.pendingItems || []).length / (reviewState.pendingPageSize || 5)));
      if (reviewState.pendingPage >= totalPages) return;
      reviewState.pendingPage += 1;
      renderReviewPending();
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
    tagMarkItems: [],
    tagMarkPage: 1,
    tagMarkPageSize: 5,
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
    const allItems = proactiveState.pendingItems || [];
    const items = allItems.filter((item) => item.task_type !== "proactive_tag_mark");
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
            <div title="${escapeHtml(item.business_explanation || "-")}">${escapeHtml(truncate(item.business_explanation || "-"))}</div>
            <div class="technical-hint">技术态：${escapeHtml(item.technical_state_label || "未执行")} ${escapeHtml(truncate(item.technical_detail || "", 80))}</div>
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

  function renderProactiveTagMark() {
    const tbody = document.getElementById("proactive-tag-mark-tbody");
    if (!tbody) return;
    const items = proactiveState.tagMarkItems || [];
    const pageSize = proactiveState.tagMarkPageSize || 5;
    const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
    if (proactiveState.tagMarkPage > totalPages) {
      proactiveState.tagMarkPage = totalPages;
    }
    const currentPage = Math.max(1, proactiveState.tagMarkPage || 1);
    const startIndex = (currentPage - 1) * pageSize;
    const pageItems = items.slice(startIndex, startIndex + pageSize);

    if (!items.length) {
      tbody.innerHTML = "";
      setElementVisible("proactive-tag-mark-empty", true);
      setElementVisible("proactive-tag-mark-pagination", false);
      return;
    }

    setElementVisible("proactive-tag-mark-empty", false);
    setElementVisible("proactive-tag-mark-pagination", true);
    tbody.innerHTML = pageItems
      .map((item) => {
        const liaisonStatus = item.planned_payload?.liaison_status || item.normalized_data?.liaison_status || "未知";
        const tagName = item.planned_payload?.tag_name || "-";
        return `
        <tr>
          <td>${escapeHtml(item.customer_name || "未知客户")}</td>
          <td>${escapeHtml(liaisonStatus)}</td>
          <td>${escapeHtml(tagName)}</td>
          <td>${escapeHtml(item.latest_run_time ? fmtDateTime(item.latest_run_time) : "未执行")}</td>
          <td>
            <div title="${escapeHtml(item.business_explanation || "-")}">${escapeHtml(truncate(item.business_explanation || "-"))}</div>
            <div class="technical-hint">技术态：${escapeHtml(item.technical_state_label || "未执行")} ${escapeHtml(truncate(item.technical_detail || "", 80))}</div>
          </td>
          <td>
            <a href="${escapeHtml(item.detail_url || "#")}">查看任务</a>
            <button class="action-button secondary" data-action="precheck" data-task-id="${escapeHtml(item.task_plan_id)}">预检查</button>
            <button class="action-button danger" data-action="execute" data-task-id="${escapeHtml(item.task_plan_id)}" data-dry-run="false">执行</button>
          </td>
        </tr>
      `;
      })
      .join("");

    setElementText("proactive-tag-mark-page-info", `第 ${currentPage} 页 / 共 ${totalPages} 页`);
    const prevBtn = document.getElementById("proactive-tag-mark-prev");
    const nextBtn = document.getElementById("proactive-tag-mark-next");
    if (prevBtn) prevBtn.disabled = currentPage <= 1;
    if (nextBtn) nextBtn.disabled = currentPage >= totalPages;
  }

  function renderProactiveSummary() {
    const item = proactiveState.summaryItem || {};
    const badge = document.getElementById("proactive-summary-sync-badge");
    if (badge) {
      if (item.latest_sync_status) {
        badge.className = `badge status-${item.latest_sync_status}`;
        badge.textContent = item.latest_sync_status_label;
        badge.style.display = "";
      } else {
        badge.style.display = "none";
      }
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
        const items = result.items || [];
        proactiveState.pendingItems = items;
        proactiveState.pendingPage = 1;
        proactiveState.tagMarkItems = items.filter((item) => item.task_type === "proactive_tag_mark");
        proactiveState.tagMarkPage = 1;
        renderProactivePending();
        renderProactiveTagMark();
        const visitCloseItems = items.filter((item) => item.task_type !== "proactive_tag_mark");
        const visitCloseExecutable = visitCloseItems.filter((item) => item.can_execute).length;
        const executeAllBtn = document.getElementById("proactive-execute-all-btn");
        if (executeAllBtn) {
          executeAllBtn.dataset.totalCount = String(visitCloseExecutable);
          executeAllBtn.disabled = visitCloseExecutable === 0;
          executeAllBtn.textContent = `一键执行闭环全部（后台执行 ${visitCloseExecutable}）`;
        }
        const tagMarkExecutable = proactiveState.tagMarkItems.filter((item) => item.can_execute).length;
        const tagMarkExecuteAllBtn = document.getElementById("proactive-tag-mark-execute-all-btn");
        if (tagMarkExecuteAllBtn) {
          tagMarkExecuteAllBtn.dataset.totalCount = String(tagMarkExecutable);
          tagMarkExecuteAllBtn.disabled = tagMarkExecutable === 0;
          tagMarkExecuteAllBtn.textContent = `一键打标全部（后台执行 ${tagMarkExecutable}）`;
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
        proactiveState.recentItems = result.items || [];
        proactiveState.recentPage = 1;
        renderProactiveRecent();
      },
    }),
  ]);

  await refreshProactiveAutoExecuteStats();
  ["proactive-summary-retry", "proactive-pending-retry", "proactive-recent-retry", "proactive-tag-mark-retry"].forEach((id) => {
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
  const tagMarkPrevBtn = document.getElementById("proactive-tag-mark-prev");
  if (tagMarkPrevBtn && tagMarkPrevBtn.dataset.bound !== "1") {
    tagMarkPrevBtn.dataset.bound = "1";
    tagMarkPrevBtn.addEventListener("click", () => {
      if (proactiveState.tagMarkPage <= 1) return;
      proactiveState.tagMarkPage -= 1;
      renderProactiveTagMark();
    });
  }
  const tagMarkNextBtn = document.getElementById("proactive-tag-mark-next");
  if (tagMarkNextBtn && tagMarkNextBtn.dataset.bound !== "1") {
    tagMarkNextBtn.dataset.bound = "1";
    tagMarkNextBtn.addEventListener("click", () => {
      const totalPages = Math.max(1, Math.ceil((proactiveState.tagMarkItems || []).length / (proactiveState.tagMarkPageSize || 5)));
      if (proactiveState.tagMarkPage >= totalPages) return;
      proactiveState.tagMarkPage += 1;
      renderProactiveTagMark();
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
      const runningIndicator = item.sync_running
        ? '<span class="sync-running-indicator" title="正在同步中…"></span>'
        : "";
      const badgeHtml = item.latest_sync_status
        ? `<span class="badge status-${escapeHtml(item.latest_sync_status)}">${escapeHtml(item.latest_sync_status_label)}</span>`
        : "";
      const failedText = (item.failed_task_count ?? 0) > 0
        ? `${item.failed_task_count ?? 0}${(item.retryable_task_count ?? 0) > 0 ? `（可重试 ${item.retryable_task_count}）` : ""}`
        : "0";
      const explanation = item.latest_execute_explanation
        ? `<details class="explanation-details"><summary>查看执行说明</summary><p class="ops-note">${escapeHtml(item.latest_execute_explanation)}</p></details>`
        : "";
      return `
        <article class="card module-card">
          <div class="card-head">
            <div>
              <h4>${escapeHtml(item.module_name || "")}${runningIndicator}</h4>
            </div>
            ${badgeHtml}
          </div>
          <dl class="stats">
            <div><dt>最近同步</dt><dd>${escapeHtml(fmtDateTime(item.latest_snapshot_time))}</dd></div>
            <div><dt>最近执行</dt><dd>${escapeHtml(item.latest_execute_status_label || "暂无")}</dd></div>
            <div><dt>记录数</dt><dd>${escapeHtml(item.row_count ?? 0)}</dd></div>
            <div><dt>待执行</dt><dd>${escapeHtml(item.planned_tasks ?? 0)}</dd></div>
            <div><dt>需人工</dt><dd>${escapeHtml(item.manual_required_count ?? 0)}</dd></div>
            <div><dt>失败</dt><dd>${failedText}</dd></div>
          </dl>
          ${explanation}
          <div class="card-actions">
            <button class="action-button" data-action="sync" data-module-code="${escapeHtml(item.module_code || "")}">立即同步</button>
            <a class="module-entry-link" href="/console/modules/${escapeHtml(item.module_code || "")}">进入模块</a>
          </div>
        </article>
      `;
    })
    .join("");
}

async function loadDashboardData() {
  await Promise.allSettled([
    loadSection({
      sectionName: "模块总览",
      loader: () => getJson("/api/ops/dashboard/summary"),
      errorId: "dashboard-summary-error",
      retryId: "dashboard-summary-retry",
      onSuccess: (result) => {
        renderDashboardSummaryCards(result.items || []);
      },
    }),
  ]);
  ["dashboard-summary-retry"].forEach((id) => {
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
  } else if (moduleCode === "review") {
    loadReviewModuleData(meta);
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
  const visitOwner = moduleCode === "visit" ? getVisitOwnerFilter() : "";
  const proactiveOwner = moduleCode === "proactive" ? getProactiveOwnerFilter() : "";
  const ownerQuery = moduleCode === "visit" ? { visit_owner: visitOwner } : (moduleCode === "proactive" ? { visit_owner: proactiveOwner } : {});
  renderFeedback("warning", `正在同步 ${moduleCode}...`, { module_code: moduleCode, visit_owner: visitOwner || null });
  const result = await postJson("/api/sync/run", { module_code: moduleCode, force: false });
  renderFeedback("success", `同步完成：${moduleCode}`, result);
  scheduleRefresh(1200, ownerQuery);
}

async function handleSyncRerun(button) {
  const moduleCode = button.dataset.moduleCode;
  const visitOwner = moduleCode === "visit" ? getVisitOwnerFilter() : "";
  const proactiveOwner = moduleCode === "proactive" ? getProactiveOwnerFilter() : "";
  const ownerQuery = moduleCode === "visit" ? { visit_owner: visitOwner } : (moduleCode === "proactive" ? { visit_owner: proactiveOwner } : {});
  renderFeedback("warning", `正在重跑同步 ${moduleCode}...`, { module_code: moduleCode, visit_owner: visitOwner || null });
  const result = await postJson(`/api/modules/${moduleCode}/sync/rerun`, {});
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
  const usesAsyncQueue = moduleCode === "visit" || moduleCode === "review";
  if (!usesAsyncQueue) {
    renderFeedback("warning", `正在执行 task ${taskId}...`, { task_id: taskId, dry_run: dryRun });
    const result = await postJson(`/api/tasks/${taskId}/execute`, { dry_run: dryRun });
    const status = result.item?.run_status || "unknown";
    const kind = result.item?.manual_required ? "warning" : "success";
    renderFeedback(kind, `执行结果：${status}`, result);
    scheduleRefresh();
    return;
  }
  renderFeedback("warning", `正在提交后台执行 task ${taskId}...`, { task_id: taskId, dry_run: dryRun });
  const result = await postJson(`/api/tasks/${taskId}/enqueue-execute`, { dry_run: dryRun });
  renderFeedback("warning", "已提交后台执行队列", result);
  await pollBatchStatus(result.batch_id, { extraQuery: {} });
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
  const payload = { module_code: "proactive", dry_run: false, task_types: ["proactive_visit_close"] };
  if (visitOwner) {
    payload.visit_owner = visitOwner;
  }
  const result = await postJson("/api/tasks/batch/enqueue-pending", payload);
  renderFeedback("warning", "主动回访批量任务已入队，后台执行中", result);
  await pollBatchStatus(result.batch_id, { extraQuery: { visit_owner: visitOwner } });
}

async function handleExecuteAllProactiveTagMark(button) {
  const totalCount = Number.parseInt(button.dataset.totalCount || "0", 10);
  if (!window.confirm(`将把全部待打标任务提交到后台队列执行（当前 ${totalCount} 条）。是否继续？`)) {
    return;
  }
  renderFeedback("warning", `正在提交打标批量后台执行...`, { module_code: "proactive", total_count: totalCount });
  const result = await postJson("/api/tasks/batch/enqueue-pending", { module_code: "proactive", dry_run: false, task_types: ["proactive_tag_mark"] });
  renderFeedback("warning", "打标批量任务已入队，后台执行中", result);
  await pollBatchStatus(result.batch_id, { extraQuery: {} });
}

async function handleExecuteAllReview(button) {
  const totalCount = Number.parseInt(button.dataset.totalCount || "0", 10);
  if (!window.confirm(`将把全部待审核任务提交到后台队列执行（当前 ${totalCount} 条）。是否继续？`)) {
    return;
  }
  renderFeedback("warning", `正在提交审核批量后台执行...`, { module_code: "review", total_count: totalCount });
  const result = await postJson("/api/tasks/batch/enqueue-pending", { module_code: "review", dry_run: false });
  renderFeedback("warning", "审核批量任务已入队，后台执行中", result);
  await pollBatchStatus(result.batch_id, { extraQuery: {} });
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
    if (button.dataset.action === "execute-all-proactive-tag-mark") {
      await handleExecuteAllProactiveTagMark(button);
      return;
    }
    if (button.dataset.action === "execute-all-review") {
      await handleExecuteAllReview(button);
      return;
    }
    if (button.dataset.action === "rerun-task") {
      await handleTaskRerun(button);
    }
  } catch (error) {
    renderFeedback("error", "操作失败", { error: error.message });
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
  initDashboardHydration();
  initModulePageHydration();
});
