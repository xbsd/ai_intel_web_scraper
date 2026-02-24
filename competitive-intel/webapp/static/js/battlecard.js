/**
 * Battle Card Generator — Frontend Module
 *
 * Handles the Battle Cards tab: form management, SSE-streamed generation,
 * client name disambiguation, report preview in iframe, and multi-mode PDF export.
 */

(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  let currentReport = null; // holds the last generated BattleCardReport
  let currentReportHtml = null; // holds the rendered HTML string (combined mode)
  let confirmedClient = null; // holds the confirmed ClientMatch object
  let lookupTimer = null; // debounce timer for client name lookup

  // ── Initialize ──
  document.addEventListener("DOMContentLoaded", () => {
    loadCompetitors();
    bindBattleCardEvents();
  });

  function bindBattleCardEvents() {
    const genBtn = $("#bc-btn-generate");
    if (genBtn) genBtn.addEventListener("click", generateBattleCard);

    const previewBtn = $("#bc-btn-preview-html");
    if (previewBtn) previewBtn.addEventListener("click", previewHtmlInNewTab);

    // Client name disambiguation
    const clientInput = $("#bc-client-name");
    if (clientInput) {
      clientInput.addEventListener("input", onClientNameInput);
      clientInput.addEventListener("focus", onClientNameInput);
    }

    const changeBtn = $("#bc-client-change-btn");
    if (changeBtn) changeBtn.addEventListener("click", clearConfirmedClient);

    // Export dropdown
    const exportTrigger = $("#bc-export-trigger");
    if (exportTrigger) exportTrigger.addEventListener("click", toggleExportDropdown);

    // Export options
    const exportOptions = $$(".bc-export-option");
    exportOptions.forEach((btn) => {
      btn.addEventListener("click", () => {
        const mode = btn.dataset.mode;
        hideExportDropdown();
        exportPdf(mode);
      });
    });

    // Close dropdown on outside click
    document.addEventListener("click", (e) => {
      const dropdown = $("#bc-export-dropdown");
      const trigger = $("#bc-export-trigger");
      if (dropdown && dropdown.style.display !== "none") {
        if (!dropdown.contains(e.target) && !trigger.contains(e.target)) {
          hideExportDropdown();
        }
      }
      // Also close client lookup dropdown on outside click
      const lookupDd = $("#bc-client-lookup-dropdown");
      const lookupWrap = $(".bc-client-lookup-wrap");
      if (lookupDd && lookupDd.style.display !== "none") {
        if (lookupWrap && !lookupWrap.contains(e.target)) {
          lookupDd.style.display = "none";
        }
      }
    });
  }

  // ── Load competitors for the multi-select ──
  async function loadCompetitors() {
    const container = $("#bc-competitor-select");
    if (!container) return;

    try {
      const res = await fetch("/api/battlecard/competitors");
      if (!res.ok) throw new Error("Failed to load competitors");
      const data = await res.json();
      const competitors = data.competitors || [];

      if (competitors.length === 0) {
        container.innerHTML = '<div class="bc-competitor-select__empty">No competitors configured</div>';
        return;
      }

      container.innerHTML = competitors.map(c => `
        <label class="bc-competitor-chip">
          <input type="checkbox" class="bc-competitor-chip__input" value="${c.id}" name="bc-competitor">
          <span class="bc-competitor-chip__label">${escapeHtml(c.name)}</span>
        </label>
      `).join("");

      // Select first competitor by default
      const firstCheckbox = container.querySelector("input[type=checkbox]");
      if (firstCheckbox) firstCheckbox.checked = true;

    } catch (e) {
      container.innerHTML = `<div class="bc-competitor-select__empty" style="color:var(--negative)">${escapeHtml(e.message)}</div>`;
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // Client Name Disambiguation
  // ═══════════════════════════════════════════════════════════════

  function onClientNameInput() {
    const input = $("#bc-client-name");
    if (!input) return;

    const query = input.value.trim();
    if (query.length < 2) {
      hideClientDropdown();
      return;
    }

    // If client is already confirmed with this name, skip
    if (confirmedClient && confirmedClient.name === query) return;

    // Debounce: wait 600ms after typing stops
    clearTimeout(lookupTimer);
    lookupTimer = setTimeout(() => lookupClientName(query), 600);
  }

  async function lookupClientName(query) {
    const spinner = $("#bc-client-lookup-spinner");
    const dropdown = $("#bc-client-lookup-dropdown");
    if (!dropdown) return;

    if (spinner) spinner.style.display = "";

    try {
      const res = await fetch("/api/battlecard/client-lookup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });

      if (!res.ok) throw new Error("Lookup failed");
      const data = await res.json();
      const matches = data.matches || [];

      if (matches.length === 0) {
        dropdown.innerHTML = '<div class="bc-lookup-empty">No matches found — name will be used as entered</div>';
        dropdown.style.display = "";
        return;
      }

      dropdown.innerHTML = matches.map((m, idx) => `
        <button class="bc-lookup-match" data-idx="${idx}" type="button">
          <div class="bc-lookup-match__name">${escapeHtml(m.name)}</div>
          <div class="bc-lookup-match__desc">${escapeHtml(m.description || "")}</div>
          <div class="bc-lookup-match__meta">
            ${m.industry ? `<span>${escapeHtml(m.industry)}</span>` : ""}
            ${m.headquarters ? `<span>${escapeHtml(m.headquarters)}</span>` : ""}
            ${m.ticker ? `<span>${escapeHtml(m.ticker)}</span>` : ""}
            ${m.employees ? `<span>${escapeHtml(m.employees)} employees</span>` : ""}
          </div>
          ${m.relevance ? `<div class="bc-lookup-match__relevance">${escapeHtml(m.relevance)}</div>` : ""}
        </button>
      `).join("");

      // Add "Use as entered" option
      dropdown.innerHTML += `
        <button class="bc-lookup-match bc-lookup-match--custom" data-idx="-1" type="button">
          <div class="bc-lookup-match__name">Use "${escapeHtml(query)}" as entered</div>
          <div class="bc-lookup-match__desc">Skip disambiguation and use the exact name</div>
        </button>
      `;

      dropdown.style.display = "";

      // Bind click handlers
      dropdown.querySelectorAll(".bc-lookup-match").forEach((btn) => {
        btn.addEventListener("click", () => {
          const idx = parseInt(btn.dataset.idx, 10);
          if (idx >= 0 && matches[idx]) {
            selectClient(matches[idx]);
          } else {
            selectClient({ name: query, description: "", industry: "", headquarters: "", ticker: "", employees: "", relevance: "", logo_url: "" });
          }
          dropdown.style.display = "none";
        });
      });

      // Store matches for access
      dropdown._matches = matches;

    } catch (e) {
      dropdown.innerHTML = `<div class="bc-lookup-empty" style="color:var(--negative)">Lookup failed — name will be used as entered</div>`;
      dropdown.style.display = "";
    } finally {
      if (spinner) spinner.style.display = "none";
    }
  }

  function selectClient(match) {
    confirmedClient = match;

    const input = $("#bc-client-name");
    const confirmed = $("#bc-client-confirmed");
    const nameEl = $("#bc-client-confirmed-name");
    const detailEl = $("#bc-client-confirmed-detail");

    if (input) {
      input.value = match.name;
      input.style.display = "none";
    }

    if (nameEl) nameEl.textContent = match.name;
    if (detailEl) {
      const parts = [match.industry, match.headquarters, match.ticker].filter(Boolean);
      detailEl.textContent = parts.join(" | ");
    }
    if (confirmed) confirmed.style.display = "";

    hideClientDropdown();
  }

  function clearConfirmedClient() {
    confirmedClient = null;
    const input = $("#bc-client-name");
    const confirmed = $("#bc-client-confirmed");
    if (input) {
      input.style.display = "";
      input.focus();
    }
    if (confirmed) confirmed.style.display = "none";
  }

  function hideClientDropdown() {
    const dropdown = $("#bc-client-lookup-dropdown");
    if (dropdown) dropdown.style.display = "none";
    const spinner = $("#bc-client-lookup-spinner");
    if (spinner) spinner.style.display = "none";
  }

  // ═══════════════════════════════════════════════════════════════
  // Battle Card Generation
  // ═══════════════════════════════════════════════════════════════

  async function generateBattleCard() {
    const btn = $("#bc-btn-generate");
    if (!btn || btn.disabled) return;

    // Collect form data
    const competitors = Array.from($$('input[name="bc-competitor"]:checked')).map(cb => cb.value);
    if (competitors.length === 0) {
      showToast("Please select at least one competitor", "error");
      return;
    }

    const agents = Array.from($$(".bc-agent-check:checked")).map(cb => cb.value);
    if (agents.length === 0) {
      showToast("Please select at least one intelligence agent", "error");
      return;
    }

    const request = {
      client_name: ($("#bc-client-name") || {}).value || "",
      client_industry: ($("#bc-industry") || {}).value || "",
      use_case: ($("#bc-use-case") || {}).value || "general",
      competitors: competitors,
      confirmed_client: confirmedClient || null,
      include_chat_context: ($("#bc-include-chat") || {}).checked || false,
      session_id: window.__appState ? window.__appState.sessionId : null,
      call_notes: ($("#bc-call-notes") || {}).value || "",
      client_emails: ($("#bc-client-emails") || {}).value || "",
      agents: agents,
      tone: ($("#bc-tone") || {}).value || "highly_technical",
      username: window.__appState ? window.__appState.username : null,
    };

    // UI: show progress, hide empty/report
    btn.disabled = true;
    btn.innerHTML = '<span class="bc-btn-spinner"></span> Generating...';
    showProgress(true);
    hideReport();

    try {
      const response = await fetch("/api/battlecard/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(err.detail || "Generation failed");
      }

      // Read SSE stream
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        let eventType = null;
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            eventType = line.substring(7).trim();
          } else if (line.startsWith("data: ") && eventType) {
            try {
              const data = JSON.parse(line.substring(6));
              handleSSEEvent(eventType, data);
            } catch (e) {
              // Skip unparseable lines
            }
            eventType = null;
          }
        }
      }

    } catch (e) {
      showToast("Battle card generation failed: " + e.message, "error");
      showProgress(false);
    }

    btn.disabled = false;
    btn.innerHTML = `
      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" width="18" height="18">
        <path d="M13 2l5 5-5 5"/>
        <path d="M18 7H6a4 4 0 00-4 4v5"/>
      </svg>
      Generate Battle Card
    `;
  }

  function handleSSEEvent(type, data) {
    switch (type) {
      case "status":
        updateProgress(data);
        break;
      case "report":
        currentReport = data;
        renderReport(data);
        showProgress(false);
        break;
      case "error":
        showToast("Error: " + (data.detail || "Unknown error"), "error");
        showProgress(false);
        break;
      case "done":
        // Generation complete
        break;
    }
  }

  // ── Progress UI ──
  function showProgress(show) {
    const el = $("#bc-progress");
    const empty = $("#bc-report-empty");
    if (el) el.style.display = show ? "" : "none";
    if (empty) empty.style.display = show ? "none" : (currentReport ? "none" : "");
  }

  function updateProgress(data) {
    const fill = $("#bc-progress-fill");
    const status = $("#bc-progress-status");
    if (fill && data.progress !== undefined) {
      fill.style.width = (data.progress * 100).toFixed(0) + "%";
    }
    if (status && data.message) {
      status.textContent = data.message;
    }
  }

  function hideReport() {
    const frame = $("#bc-report-frame");
    const empty = $("#bc-report-empty");
    if (frame) frame.style.display = "none";
    if (empty) empty.style.display = "none";
  }

  // ── Render Report ──
  async function renderReport(reportData) {
    try {
      // Send report data to backend for HTML rendering (combined mode)
      const payload = { ...reportData, export_mode: "combined" };
      const res = await fetch("/api/battlecard/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) throw new Error("Failed to render report");
      currentReportHtml = await res.text();

      // Display in iframe
      const iframe = $("#bc-report-iframe");
      const frame = $("#bc-report-frame");
      const empty = $("#bc-report-empty");

      if (frame) frame.style.display = "";
      if (empty) empty.style.display = "none";

      if (iframe) {
        iframe.srcdoc = currentReportHtml;
      }

      // Update meta info
      const meta = $("#bc-report-meta");
      if (meta) {
        const agents = reportData.agents_used ? reportData.agents_used.length : 0;
        const sources = reportData.sources_count || 0;
        const time = reportData.generation_time_ms || 0;
        meta.textContent = `${agents} agents | ${sources} sources | ${(time / 1000).toFixed(1)}s`;
      }

      showToast("Battle card generated successfully", "success");

    } catch (e) {
      showToast("Failed to render report: " + e.message, "error");
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // Export — 3 modes
  // ═══════════════════════════════════════════════════════════════

  function toggleExportDropdown() {
    const dropdown = $("#bc-export-dropdown");
    if (!dropdown) return;
    if (!currentReport) {
      showToast("No report generated yet", "error");
      return;
    }
    dropdown.style.display = dropdown.style.display === "none" ? "" : "none";
  }

  function hideExportDropdown() {
    const dropdown = $("#bc-export-dropdown");
    if (dropdown) dropdown.style.display = "none";
  }

  function previewHtmlInNewTab() {
    if (!currentReportHtml) {
      showToast("No report generated yet", "error");
      return;
    }
    const blob = new Blob([currentReportHtml], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank");
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  async function exportPdf(mode) {
    if (!currentReport) {
      showToast("No report generated yet", "error");
      return;
    }

    const modeLabels = {
      client_tearsheet: "Client Tear Sheet",
      sales_confidential: "Sales Confidential",
      combined: "Combined KX Battle Card",
    };

    showToast(`Preparing ${modeLabels[mode] || "export"}...`, "info");

    try {
      // Request rendered HTML for the specific mode
      const payload = { ...currentReport, export_mode: mode };
      const res = await fetch("/api/battlecard/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) throw new Error("Failed to render export");
      const exportHtml = await res.text();

      // Open in new window and trigger print
      const printWindow = window.open("", "_blank");
      if (!printWindow) {
        showToast("Popup blocked — please allow popups for PDF export", "error");
        return;
      }
      printWindow.document.write(exportHtml);
      printWindow.document.close();
      printWindow.onload = function () {
        setTimeout(() => {
          printWindow.print();
        }, 500);
      };
    } catch (e) {
      showToast("Export failed: " + e.message, "error");
    }
  }

  // ── Utilities ──
  function escapeHtml(str) {
    if (!str) return "";
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function showToast(message, type) {
    if (window.__appShowToast) {
      window.__appShowToast(message, type);
    } else {
      // Fallback
      const toast = document.getElementById("toast");
      if (toast) {
        toast.textContent = message;
        toast.className = `toast toast--${type} toast--visible`;
        setTimeout(() => toast.classList.remove("toast--visible"), 3500);
      }
    }
  }

})();
