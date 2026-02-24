/**
 * Battle Card Generator — Frontend Module
 *
 * Handles the Battle Cards tab: form management, SSE-streamed generation,
 * report preview in iframe, and PDF export.
 */

(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  let currentReport = null; // holds the last generated BattleCardReport
  let currentReportHtml = null; // holds the rendered HTML string

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

    const pdfBtn = $("#bc-btn-export-pdf");
    if (pdfBtn) pdfBtn.addEventListener("click", exportPdf);
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

  // ── Generate Battle Card ──
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
      // Send report data to backend for HTML rendering
      const res = await fetch("/api/battlecard/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(reportData),
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

  // ── Preview / Export ──
  function previewHtmlInNewTab() {
    if (!currentReportHtml) {
      showToast("No report generated yet", "error");
      return;
    }
    const blob = new Blob([currentReportHtml], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank");
    // Clean up after a delay
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  function exportPdf() {
    if (!currentReportHtml) {
      showToast("No report generated yet", "error");
      return;
    }

    // Open the HTML in a new window and trigger print
    const printWindow = window.open("", "_blank");
    if (!printWindow) {
      showToast("Popup blocked — please allow popups for PDF export", "error");
      return;
    }
    printWindow.document.write(currentReportHtml);
    printWindow.document.close();
    printWindow.onload = function () {
      setTimeout(() => {
        printWindow.print();
      }, 500);
    };
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
