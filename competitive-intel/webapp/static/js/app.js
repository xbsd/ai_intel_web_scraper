/**
 * Competitive Intelligence Q&A — Frontend Application
 *
 * Features: SSE streaming, reverse-chronological ordering, executive summary
 * with semantic highlighting, comparison widgets, native citation rendering,
 * persona selector, augmentation toggles, database explorer with drill-down,
 * content upload/ingestion panel, login/session management, progress popup
 * with thinking display, fast mode, token tracking.
 */

(function () {
  "use strict";

  // ── State ──
  const state = {
    loading: false,
    settings: null,
    statusData: null,
    username: null,
    sessionId: null,
    sessionTokens: { total_input: 0, total_output: 0 },
    nativeCitations: [],    // accumulated citation_delta objects during stream
    citationsSources: [],   // source metadata from citations_sources event
  };

  // ── DOM refs ──
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  // ── Initialization ──
  document.addEventListener("DOMContentLoaded", () => {
    bindEvents();
    checkLoginState();
  });

  function bindEvents() {
    $("#query-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submitQuery();
      }
    });
    $("#btn-submit").addEventListener("click", submitQuery);
    $("#btn-open-settings").addEventListener("click", openSettings);
    $("#btn-close-settings").addEventListener("click", closeSettings);
    $("#btn-cancel-settings").addEventListener("click", closeSettings);
    $("#btn-save-settings").addEventListener("click", saveSettings);
    $("#modal-overlay").addEventListener("click", (e) => {
      if (e.target === e.currentTarget) closeSettings();
    });
    $("#settings-provider").addEventListener("change", updateModelDropdown);
    $("#query-input").addEventListener("input", autoResizeTextarea);

    // Explorer
    $("#btn-open-explorer").addEventListener("click", () => openExplorer());
    $("#btn-close-explorer").addEventListener("click", closeExplorer);
    $("#explorer-overlay").addEventListener("click", (e) => {
      if (e.target === e.currentTarget) closeExplorer();
    });

    // Content Ingestion
    $("#btn-open-ingest").addEventListener("click", openIngest);
    $("#btn-close-ingest").addEventListener("click", closeIngest);
    $("#ingest-overlay").addEventListener("click", (e) => {
      if (e.target === e.currentTarget) closeIngest();
    });
    const uploadZone = $("#upload-zone");
    const fileInput = $("#file-input");
    uploadZone.addEventListener("click", () => fileInput.click());
    uploadZone.addEventListener("dragover", (e) => {
      e.preventDefault();
      uploadZone.classList.add("upload-zone--dragover");
    });
    uploadZone.addEventListener("dragleave", () => {
      uploadZone.classList.remove("upload-zone--dragover");
    });
    uploadZone.addEventListener("drop", (e) => {
      e.preventDefault();
      uploadZone.classList.remove("upload-zone--dragover");
      if (e.dataTransfer.files.length) handleFileUpload(e.dataTransfer.files);
    });
    fileInput.addEventListener("change", () => {
      if (fileInput.files.length) handleFileUpload(fileInput.files);
      fileInput.value = "";
    });

    // Login
    $("#btn-login").addEventListener("click", doLogin);
    $("#login-username").addEventListener("keydown", (e) => {
      if (e.key === "Enter") doLogin();
    });

    // Logout
    $("#btn-logout").addEventListener("click", doLogout);

    // Fast mode toggle
    $("#toggle-fast-mode").addEventListener("change", onFastModeToggle);

    // Thinking toggle
    $("#toggle-thinking").addEventListener("change", onThinkingToggle);

    // New session
    $("#btn-new-session").addEventListener("click", startNewSession);

    // History
    $("#btn-open-history").addEventListener("click", openHistory);
    $("#btn-close-history").addEventListener("click", closeHistory);
    $("#history-overlay").addEventListener("click", (e) => {
      if (e.target === e.currentTarget) closeHistory();
    });
    let historySearchTimeout;
    $("#history-search-input").addEventListener("input", (e) => {
      clearTimeout(historySearchTimeout);
      historySearchTimeout = setTimeout(() => {
        const q = e.target.value.trim();
        if (q.length >= 2) {
          searchHistorySessions(q);
        } else {
          loadHistorySessions();
        }
      }, 300);
    });

    // Export
    $("#btn-open-export").addEventListener("click", toggleExportDropdown);
    $("#btn-export-html").addEventListener("click", exportAsHtml);
    $("#btn-export-pdf").addEventListener("click", exportAsPdf);
    document.addEventListener("click", (e) => {
      const dropdown = $("#export-dropdown");
      const btn = $("#btn-open-export");
      if (dropdown && !dropdown.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
        dropdown.classList.remove("export-dropdown--visible");
      }
    });

    // Clear All History
    $("#btn-clear-all-history").addEventListener("click", clearAllHistory);

    // Collapse All / Expand All
    $("#btn-collapse-all").addEventListener("click", collapseAllPairs);
    $("#btn-expand-all").addEventListener("click", expandAllPairs);
  }

  // ═════════════════════════════════════════════════════════════
  // LOGIN / SESSION MANAGEMENT
  // ═════════════════════════════════════════════════════════════

  function checkLoginState() {
    const saved = localStorage.getItem("ci_username");
    if (saved) {
      performLogin(saved);
    } else {
      showLoginOverlay();
    }
  }

  function showLoginOverlay() {
    $("#login-overlay").classList.add("login-overlay--visible");
    setTimeout(() => $("#login-username").focus(), 200);
  }

  function hideLoginOverlay() {
    $("#login-overlay").classList.remove("login-overlay--visible");
  }

  async function doLogin() {
    const input = $("#login-username");
    const username = input.value.trim().toLowerCase().replace(/[^a-z0-9_\-]/g, "");
    if (!username) {
      input.focus();
      return;
    }
    input.value = username;
    await performLogin(username);
  }

  async function performLogin(username) {
    try {
      const data = await api("POST", "/api/login", { username });
      state.username = username;
      localStorage.setItem("ci_username", username);

      // Show UI elements
      hideLoginOverlay();
      $("#nav-username").textContent = username;
      $("#nav-username").style.display = "";
      $("#btn-logout").style.display = "";
      $("#nav-session-tokens").style.display = "";

      // Resume or create session
      if (data.recent_session) {
        state.sessionId = data.recent_session.session_id;
        await loadSessionMessages();
      } else {
        await createNewSession();
      }

      // Load app data
      loadStatus();
      loadSettings();
      updateSessionTokenDisplay();
    } catch (e) {
      console.error("Login failed:", e);
      showLoginOverlay();
      showToast("Login failed: " + e.message, "error");
    }
  }

  function doLogout() {
    state.username = null;
    state.sessionId = null;
    state.sessionTokens = { total_input: 0, total_output: 0 };
    localStorage.removeItem("ci_username");
    $("#nav-username").style.display = "none";
    $("#btn-logout").style.display = "none";
    $("#nav-session-tokens").style.display = "none";
    $("#nav-session-tokens").textContent = "0 tokens";
    clearConversation();
    showLoginOverlay();
  }

  async function createNewSession() {
    try {
      const data = await api("POST", "/api/sessions", { username: state.username });
      state.sessionId = data.session_id;
    } catch (e) {
      console.error("Failed to create session:", e);
    }
  }

  async function loadSessionMessages() {
    if (!state.sessionId) return;
    try {
      const data = await api("GET", `/api/sessions/${state.sessionId}/messages`);
      const messages = data.messages || [];
      if (messages.length > 0) {
        hideEmptyState();
        const conv = $("#conversation");
        // Clear existing messages but keep empty state
        const emptyState = $("#conversation-empty");
        conv.innerHTML = "";
        if (emptyState) conv.appendChild(emptyState);

        // Group messages into Q&A pairs and render in reverse order
        const totalPairs = Math.ceil(messages.length / 2);
        for (let i = 0; i < messages.length; i += 2) {
          const userMsg = messages[i];
          const assistantMsg = messages[i + 1];
          const pairIndex = i / 2; // 0-based

          const pairWrapper = document.createElement("div");
          pairWrapper.className = "qa-pair";
          conv.insertBefore(pairWrapper, conv.firstChild);

          if (userMsg && userMsg.role === "user") {
            addUserMessage(userMsg.content, pairWrapper);
          }
          if (assistantMsg && assistantMsg.role === "assistant") {
            addHistoryMessage(assistantMsg.content, pairWrapper, assistantMsg);
          }

          // Add collapse toggle
          addCollapseToggle(pairWrapper);

          // Auto-collapse all except the most recent pair
          if (pairIndex < totalPairs - 1) {
            pairWrapper.classList.add("qa-pair--collapsed");
            const btn = pairWrapper.querySelector(".qa-collapse-btn");
            if (btn) {
              btn.innerHTML = expandSvg();
              btn.title = "Expand";
            }
          }
        }
        hideEmptyState();
        updateConversationToolbar();
      }
    } catch (e) {
      console.error("Failed to load session messages:", e);
    }
  }

  async function updateSessionTokenDisplay() {
    if (!state.sessionId) return;
    try {
      const data = await api("GET", `/api/sessions/${state.sessionId}/tokens`);
      state.sessionTokens = data;
      const total = (data.total_input_tokens || 0) + (data.total_output_tokens || 0);
      $("#nav-session-tokens").textContent = `${total.toLocaleString()} tokens`;
    } catch (e) {
      // Silently ignore
    }
  }

  function clearConversation() {
    const conv = $("#conversation");
    const emptyState = $("#conversation-empty");
    conv.innerHTML = "";
    if (emptyState) {
      emptyState.style.display = "";
      conv.appendChild(emptyState);
    }
  }

  function addHistoryMessage(content, parent, msgData) {
    const div = document.createElement("div");
    div.className = "message message--assistant";
    let html = renderMarkdown(content);
    html = html.replace(
      /\[(\d+)\]/g,
      '<span class="citation-ref" data-index="$1" title="View source [$1]">$1</span>'
    );

    // Build history metrics badges
    let metricsHtml = '<span class="metric-badge metric-badge--history">history</span>';
    if (msgData) {
      if (msgData.model) metricsHtml += `<span class="metric-badge">${escapeHtml(msgData.model)}</span>`;
      const inp = msgData.tokens_input || 0;
      const out = msgData.tokens_output || 0;
      if (inp || out) metricsHtml += `<span class="metric-badge">${inp.toLocaleString()} in / ${out.toLocaleString()} out</span>`;
    }

    div.innerHTML = `
      <div class="message__header">
        <span class="message__role">Analysis</span>
        <span class="message__model">${msgData && msgData.model ? escapeHtml(msgData.model) : ""}</span>
        <span class="message__timing" style="color:var(--text-tertiary);font-size:var(--text-xs)">history</span>
      </div>
      <div class="executive-summary-slot"></div>
      <div class="message__body">${html}</div>
      <div class="message__metrics">${metricsHtml}</div>
    `;
    parent.appendChild(div);
    renderExecutiveSummary(div, content);
  }

  // ── API Helpers ──
  async function api(method, path, body) {
    const opts = {
      method,
      headers: { "Content-Type": "application/json" },
    };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || "Request failed");
    }
    return res.json();
  }

  // ── Load metadata ──
  async function loadStatus() {
    try {
      const data = await api("GET", "/api/status");
      state.statusData = data;
      populateFilters(data);
      updateVectorStatus(data.vector_store);
    } catch (e) {
      console.error("Failed to load status:", e);
      setConnectionStatus(false);
    }
  }

  function populateFilters(data) {
    const compSelect = $("#filter-competitor");
    compSelect.innerHTML = '<option value="">All Competitors</option>';
    (data.competitors || []).forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = c.name;
      compSelect.appendChild(opt);
    });

    const topicSelect = $("#filter-topic");
    topicSelect.innerHTML = '<option value="">All Topics</option>';
    (data.topics || []).forEach((t) => {
      const opt = document.createElement("option");
      opt.value = t.id;
      opt.textContent = t.name;
      topicSelect.appendChild(opt);
    });

    const srcSelect = $("#filter-source");
    srcSelect.innerHTML = '<option value="">All Sources</option>';
    (data.source_types || []).forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = formatSourceType(s);
      srcSelect.appendChild(opt);
    });
  }

  function updateVectorStatus(vs) {
    const sourceCount = vs?.competitive_intel?.count || 0;
    const compCount = vs?.competitive_comparisons?.count || 0;
    const total = sourceCount + compCount;
    const statusEl = $("#nav-status-text");
    if (total > 0) {
      statusEl.textContent = `${total.toLocaleString()} vectors`;
      setConnectionStatus(true);
    } else {
      statusEl.textContent = "No vectors";
      setConnectionStatus(false);
    }
  }

  function setConnectionStatus(online) {
    const dot = $("#nav-status-dot");
    if (online) {
      dot.classList.remove("top-nav__status-dot--offline");
    } else {
      dot.classList.add("top-nav__status-dot--offline");
    }
  }

  // ── Load settings ──
  async function loadSettings() {
    try {
      const data = await api("GET", "/api/settings");
      state.settings = data;
    } catch (e) {
      console.error("Failed to load settings:", e);
    }
  }

  // ── Fast Mode ──
  function onFastModeToggle() {
    const fastMode = $("#toggle-fast-mode").checked;
    const thinkingToggle = $("#toggle-thinking");

    if (fastMode) {
      showToast("Fast Mode: Claude Haiku 4.5", "success");
      // Disable thinking toggle (fast mode forces thinking off)
      thinkingToggle.checked = false;
      thinkingToggle.disabled = true;
      thinkingToggle.closest(".toggle").classList.add("toggle--disabled");
    } else {
      showToast("Fast Mode disabled", "success");
      // Re-enable thinking toggle
      thinkingToggle.disabled = false;
      thinkingToggle.checked = true;
      thinkingToggle.closest(".toggle").classList.remove("toggle--disabled");
    }
  }

  // ── Thinking Toggle ──
  function onThinkingToggle() {
    const thinking = $("#toggle-thinking").checked;
    showToast(thinking ? "Extended Thinking enabled" : "Extended Thinking disabled", "success");
  }

  // ── New Session ──
  async function startNewSession() {
    await createNewSession();
    clearConversation();
    showToast("New session started", "success");
    updateSessionTokenDisplay();
    updateConversationToolbar();
  }

  // ═════════════════════════════════════════════════════════════
  // PROGRESS POPUP
  // ═════════════════════════════════════════════════════════════

  function showProgressPopup() {
    const popup = $("#progress-popup");
    const steps = $("#progress-steps");
    const thinking = $("#progress-thinking");
    steps.innerHTML = "";
    thinking.style.display = "none";
    $("#progress-thinking-text").textContent = "";
    popup.style.display = "flex";
    requestAnimationFrame(() => popup.classList.add("progress-popup--visible"));
  }

  function hideProgressPopup() {
    const popup = $("#progress-popup");
    popup.classList.remove("progress-popup--visible");
    setTimeout(() => {
      popup.style.display = "none";
    }, 300);
  }

  function addProgressStep(stepId, message) {
    const steps = $("#progress-steps");
    const existing = steps.querySelector(`[data-step="${stepId}"]`);
    if (existing) return;

    const el = document.createElement("div");
    el.className = "progress-step progress-step--active";
    el.dataset.step = stepId;
    el.innerHTML = `<div class="loading-spinner"></div><span>${escapeHtml(message)}</span>`;
    steps.appendChild(el);
  }

  function completeProgressStep(stepId) {
    const steps = $("#progress-steps");
    const el = steps.querySelector(`[data-step="${stepId}"]`);
    if (!el) return;
    el.classList.remove("progress-step--active");
    el.classList.add("progress-step--done");
    const spinner = el.querySelector(".loading-spinner");
    if (spinner) spinner.remove();
    el.insertAdjacentHTML("afterbegin", checkSvg());
  }

  function appendThinkingText(text) {
    const container = $("#progress-thinking");
    const textEl = $("#progress-thinking-text");
    container.style.display = "";
    textEl.textContent += text;
    // Auto-scroll to bottom
    textEl.scrollTop = textEl.scrollHeight;
  }

  // ═════════════════════════════════════════════════════════════
  // SSE STREAMING QUERY
  // ═════════════════════════════════════════════════════════════

  async function submitQuery() {
    const input = $("#query-input");
    const query = input.value.trim();
    if (!query || state.loading) return;

    state.loading = true;
    input.value = "";
    autoResizeTextarea();
    $("#btn-submit").disabled = true;

    // Reset per-query state
    state.nativeCitations = [];
    state.citationsSources = [];

    // Create a wrapper for the Q&A pair, prepended at top for reverse order
    const pairWrapper = document.createElement("div");
    pairWrapper.className = "qa-pair";
    const conv = $("#conversation");
    hideEmptyState();
    conv.insertBefore(pairWrapper, conv.firstChild);

    addUserMessage(query, pairWrapper);
    const messageEl = createStreamingMessage(pairWrapper);

    // Show progress popup
    showProgressPopup();

    try {
      const competitorFilter = $("#filter-competitor").value || null;
      const topicFilter = $("#filter-topic").value || null;
      const sourceFilter = $("#filter-source").value || null;
      const persona = $("#persona-select").value || null;
      const useLlmKnowledge = $("#toggle-llm-knowledge").checked;
      const useWebSearch = $("#toggle-web-search").checked;
      const fastMode = $("#toggle-fast-mode").checked;
      const useThinking = $("#toggle-thinking").checked;

      const response = await fetch("/api/query-stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          competitor_filter: competitorFilter ? [competitorFilter] : null,
          topic_filter: topicFilter ? [topicFilter] : null,
          source_type_filter: sourceFilter ? [sourceFilter] : null,
          n_results: 12,
          persona: persona,
          use_llm_knowledge: useLlmKnowledge,
          use_web_search: useWebSearch,
          fast_mode: fastMode,
          use_thinking: useThinking,
          session_id: state.sessionId,
          username: state.username,
        }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(err.detail || "Request failed");
      }

      await consumeSSEStream(response.body, messageEl);
    } catch (e) {
      hideProgressPopup();
      removeStreamingMessage(messageEl);
      addErrorMessage(e.message);
      showToast(e.message, "error");
    } finally {
      state.loading = false;
      $("#btn-submit").disabled = false;
      input.focus();
      updateSessionTokenDisplay();
    }
  }

  async function consumeSSEStream(body, messageEl) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let fullAnswer = "";
    let popupDismissed = false;
    let streamUsageData = {};
    let streamMetadata = {};

    const answerBody = messageEl.querySelector(".message__body");

    let tokenCount = 0;
    function renderToken() {
      tokenCount++;
      if (tokenCount <= 20 || tokenCount % 3 === 0) {
        renderIncrementalAnswer(answerBody, fullAnswer);
      }
    }

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const rawEvent = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const parsed = parseSSEEvent(rawEvent);
        if (!parsed) continue;

        switch (parsed.event) {
          case "status":
            handleStatusEvent(parsed.data);
            break;

          case "thinking":
            appendThinkingText(parsed.data.text);
            break;

          case "citations_sources":
            state.citationsSources = parsed.data;
            renderCitations(parsed.data);
            break;

          case "citation_delta":
            state.nativeCitations.push(parsed.data);
            break;

          case "token":
            // Dismiss popup on first token
            if (!popupDismissed) {
              popupDismissed = true;
              hideProgressPopup();
            }
            fullAnswer += parsed.data.text;
            renderToken();
            break;

          case "web_search_result":
            // Web search results from server-side tool
            break;

          case "usage":
            streamUsageData = parsed.data;
            renderUsage(parsed.data, streamMetadata);
            break;

          case "followups":
            renderFollowUps(messageEl, parsed.data);
            break;

          case "metadata":
            streamMetadata = parsed.data;
            renderMetadata(parsed.data);
            finalizeStreamingMessage(messageEl, parsed.data);
            break;

          case "done":
            hideProgressPopup();
            renderExecutiveSummary(messageEl, fullAnswer);
            renderIncrementalAnswer(answerBody, fullAnswer, true);
            extractAndRenderComparisons(fullAnswer);
            // Render native citations in right rail if we have them
            if (state.nativeCitations.length > 0) {
              renderNativeCitations();
            }
            // Render inline metrics on the message
            renderMessageMetrics(messageEl, streamUsageData, streamMetadata);
            // Add collapse toggle to the Q&A pair
            const pairEl = messageEl.closest(".qa-pair");
            if (pairEl && !pairEl.querySelector(".qa-collapse-btn")) {
              addCollapseToggle(pairEl);
            }
            updateConversationToolbar();
            break;

          case "error":
            hideProgressPopup();
            throw new Error(parsed.data.detail || "Stream error");
        }
      }
    }
  }

  function handleStatusEvent(data) {
    const step = data.step;
    if (step.endsWith("_done")) {
      const base = step.replace("_done", "");
      completeProgressStep(base);
    } else {
      addProgressStep(step, data.message || step);
    }
  }

  function parseSSEEvent(raw) {
    let event = "message";
    let data = "";
    for (const line of raw.split("\n")) {
      if (line.startsWith("event: ")) {
        event = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        data = line.slice(6);
      }
    }
    try {
      return { event, data: JSON.parse(data) };
    } catch {
      return null;
    }
  }

  // ═════════════════════════════════════════════════════════════
  // STREAMING MESSAGE UI
  // ═════════════════════════════════════════════════════════════

  function createStreamingMessage(parent) {
    const container = parent || $("#conversation");
    const div = document.createElement("div");
    div.className = "message message--assistant message--streaming";
    div.innerHTML = `
      <div class="message__header">
        <span class="message__role">Analysis</span>
        <span class="message__model"></span>
        <span class="message__timing"></span>
      </div>
      <div class="executive-summary-slot"></div>
      <div class="message__body"></div>
      <div class="message__metrics"></div>
    `;
    container.appendChild(div);
    $("#conversation").scrollTop = 0;
    return div;
  }

  function removeStreamingMessage(messageEl) {
    if (messageEl && messageEl.parentNode) {
      messageEl.remove();
    }
  }

  function finalizeStreamingMessage(messageEl, metadata) {
    const model = metadata.llm_model || "";
    const timing = metadata.timings?.total_ms;
    const chunks = metadata.chunks_retrieved || 0;
    messageEl.querySelector(".message__model").textContent = model;
    messageEl.querySelector(".message__timing").textContent =
      `${timing ? formatDuration(timing) : ""} \u00b7 ${chunks} sources`;
    messageEl.classList.remove("message--streaming");
  }

  function stripExecutiveSummary(text) {
    // Remove the **EXECUTIVE SUMMARY**\n...\n--- block from the answer body
    // since it's rendered separately in the styled card above
    return text.replace(/\*\*EXECUTIVE SUMMARY\*\*\s*\n[\s\S]*?(?=\n---)\n---/, "").trim();
  }

  function renderIncrementalAnswer(answerBody, fullText, stripSummary) {
    let text = stripSummary ? stripExecutiveSummary(fullText) : fullText;
    let html = renderMarkdown(text);
    html = html.replace(
      /\[(\d+)\]/g,
      '<span class="citation-ref" data-index="$1" title="View source [$1]">$1</span>'
    );
    answerBody.innerHTML = html;
    answerBody.querySelectorAll(".citation-ref").forEach((el) => {
      el.addEventListener("click", () => {
        highlightCitation(parseInt(el.dataset.index, 10));
      });
    });
  }

  // ═════════════════════════════════════════════════════════════
  // EXECUTIVE SUMMARY
  // ═════════════════════════════════════════════════════════════

  function renderExecutiveSummary(messageEl, fullAnswer) {
    const slot = messageEl.querySelector(".executive-summary-slot");
    if (!slot) return;

    const summaryMatch = fullAnswer.match(
      /\*\*EXECUTIVE SUMMARY\*\*\s*\n([\s\S]*?)(?=\n---)/i
    );
    if (!summaryMatch) return;

    let summaryText = summaryMatch[1].trim();
    let summaryHtml = renderMarkdown(summaryText);

    summaryHtml = summaryHtml.replace(
      /\[(\d+)\]/g,
      '<span class="citation-ref" data-index="$1">$1</span>'
    );

    summaryHtml = applySemanticHighlights(summaryHtml);

    slot.innerHTML = `
      <div class="executive-summary">
        <div class="executive-summary__label">Executive Summary</div>
        <div class="executive-summary__text">${summaryHtml}</div>
      </div>
    `;

    slot.querySelectorAll(".citation-ref").forEach((el) => {
      el.addEventListener("click", () => {
        highlightCitation(parseInt(el.dataset.index, 10));
      });
    });
  }

  function applySemanticHighlights(html) {
    const kxPattern = /kdb\+|KX|kx|sub-millisecond|fastest|superior|advantage|outperform|native|built-in|real-time|in-memory|vectorized|nanosecond|microsecond/gi;
    const weakPattern = /limitation|lacks?|missing|slower|latency|overhead|no support|doesn't support|cannot|workaround|third-party|not available|bottleneck|gap/gi;

    html = html.replace(
      /<strong>(.*?)<\/strong>/g,
      (match, inner) => {
        kxPattern.lastIndex = 0;
        weakPattern.lastIndex = 0;
        if (kxPattern.test(inner)) {
          return `<strong class="highlight--kx">${inner}</strong>`;
        }
        kxPattern.lastIndex = 0;
        weakPattern.lastIndex = 0;
        if (weakPattern.test(inner)) {
          return `<strong class="highlight--competitor">${inner}</strong>`;
        }
        return match;
      }
    );
    return html;
  }

  // ═════════════════════════════════════════════════════════════
  // COMPARISON WIDGETS
  // ═════════════════════════════════════════════════════════════

  function extractAndRenderComparisons(fullAnswer) {
    const section = $("#comparison-section");
    const container = $("#comparison-widgets");
    if (!section || !container) return;

    const kxStrengths = [];
    const competitorWeaknesses = [];

    const boldMatches = [...fullAnswer.matchAll(/\*\*([^*]+)\*\*/g)];
    for (const m of boldMatches) {
      const phrase = m[1].trim();
      if (/^(EXECUTIVE SUMMARY|SOURCES|sources)$/.test(phrase)) continue;
      if (phrase.length < 4 || phrase.length > 80) continue;

      const contextStart = Math.max(0, m.index - 120);
      const contextEnd = Math.min(fullAnswer.length, m.index + phrase.length + 120);
      const context = fullAnswer.slice(contextStart, contextEnd).toLowerCase();
      const lower = phrase.toLowerCase();

      const isKx = /kx|kdb\+|advantage|strength|superior|fast|native|built-in|real-time|in-memory/.test(context) &&
                   !/limitation|weakness|lack|slow|missing|gap/.test(lower);
      const isWeak = /limitation|weakness|lack|slow|missing|gap|overhead|bottleneck|workaround/.test(lower) ||
                     (/competitor|questdb|clickhouse/.test(context) && /limitation|weakness|lack|slow|missing/.test(context));

      if (isKx && !kxStrengths.includes(phrase)) {
        kxStrengths.push(phrase);
      } else if (isWeak && !competitorWeaknesses.includes(phrase)) {
        competitorWeaknesses.push(phrase);
      }
    }

    if (kxStrengths.length === 0 && competitorWeaknesses.length === 0) {
      section.style.display = "none";
      return;
    }

    section.style.display = "";
    $("#differentiators-count").textContent = kxStrengths.length + competitorWeaknesses.length;

    let html = "";
    if (kxStrengths.length > 0) {
      html += `<div class="comparison-group">
        <div class="comparison-group__label comparison-group__label--kx">KX Strengths</div>
        ${kxStrengths.slice(0, 6).map(s =>
          `<div class="comparison-badge comparison-badge--kx">${escapeHtml(s)}</div>`
        ).join("")}
      </div>`;
    }
    if (competitorWeaknesses.length > 0) {
      html += `<div class="comparison-group">
        <div class="comparison-group__label comparison-group__label--competitor">Competitor Gaps</div>
        ${competitorWeaknesses.slice(0, 6).map(s =>
          `<div class="comparison-badge comparison-badge--competitor">${escapeHtml(s)}</div>`
        ).join("")}
      </div>`;
    }
    container.innerHTML = html;
  }

  // ═════════════════════════════════════════════════════════════
  // MESSAGE RENDERING
  // ═════════════════════════════════════════════════════════════

  function addUserMessage(query, parent) {
    const container = parent || $("#conversation");
    const div = document.createElement("div");
    div.className = "message message--user";
    div.innerHTML = `
      <div class="message__header">
        <span class="message__role">Query</span>
      </div>
      <div class="message__body">${escapeHtml(query)}</div>
    `;
    container.appendChild(div);
  }

  function addErrorMessage(message) {
    const conv = $("#conversation");
    const div = document.createElement("div");
    div.className = "message message--assistant message--error";
    div.innerHTML = `
      <div class="message__header">
        <span class="message__role" style="color:var(--negative)">Error</span>
      </div>
      <div class="message__body" style="color:var(--negative)">${escapeHtml(message)}</div>
    `;
    conv.insertBefore(div, conv.firstChild);
    conv.scrollTop = 0;
  }

  function renderFollowUps(messageEl, followUps) {
    if (!followUps || followUps.length === 0) return;
    const existing = messageEl.querySelector(".follow-ups");
    if (existing) existing.remove();

    const div = document.createElement("div");
    div.className = "follow-ups";
    div.innerHTML = followUps.map(q =>
      `<button class="follow-up-btn" onclick="window.__askFollowUp(this)">${escapeHtml(q)}</button>`
    ).join("");
    messageEl.appendChild(div);
  }

  function hideEmptyState() {
    const el = $("#conversation-empty");
    if (el) el.style.display = "none";
  }

  // ── Right Rail: Citations ──
  function renderCitations(citations) {
    const container = $("#citations-list");
    const countEl = $("#citations-count");

    if (!citations || !citations.length) {
      container.innerHTML =
        '<div style="padding:12px 16px;font-size:12px;color:var(--text-tertiary)">No citations for this query</div>';
      countEl.textContent = "0";
      return;
    }

    countEl.textContent = citations.length;
    container.innerHTML = citations
      .map(
        (c) => `
        <div class="citation-card" data-citation-index="${c.index}" id="citation-${c.index}">
          <div class="citation-card__header">
            <span class="citation-card__index">${c.index}</span>
            <span class="citation-card__title">${escapeHtml(c.source_title)}</span>
          </div>
          <div class="citation-card__meta">
            <span class="citation-card__tag">${formatSourceType(c.source_type)}</span>
            <span class="citation-card__tag citation-card__tag--credibility-${c.credibility}">${c.credibility}</span>
            <span class="citation-card__tag">${escapeHtml(c.competitor)}</span>
            ${c.content_date ? `<span class="citation-card__tag">${c.content_date}</span>` : ""}
          </div>
          <div class="citation-card__preview">${escapeHtml(c.text_preview)}</div>
          ${
            c.source_url
              ? `<a class="citation-card__link" href="${escapeHtml(c.source_url)}" target="_blank" rel="noopener">${truncateUrl(c.source_url)}</a>`
              : ""
          }
        </div>
      `
      )
      .join("");
  }

  function renderNativeCitations() {
    // If we received native citation_delta events, update the right rail
    // by grouping cited_text snippets by source
    if (!state.nativeCitations.length) return;

    const container = $("#citations-list");
    const countEl = $("#citations-count");

    // Group by source
    const bySource = {};
    for (const c of state.nativeCitations) {
      const key = c.url || c.source || c.document_title || "unknown";
      if (!bySource[key]) {
        bySource[key] = {
          title: c.document_title || c.title || c.source || key,
          url: c.url || c.source || "",
          type: c.type || "search_result_location",
          snippets: [],
        };
      }
      if (c.cited_text) {
        const text = c.cited_text.substring(0, 200);
        if (!bySource[key].snippets.includes(text)) {
          bySource[key].snippets.push(text);
        }
      }
    }

    const sources = Object.values(bySource);
    if (sources.length === 0) return;

    // Don't override if citationsSources already rendered good data
    if (state.citationsSources.length > 0) return;

    countEl.textContent = sources.length;
    container.innerHTML = sources
      .map(
        (s, i) => `
        <div class="citation-card" id="citation-native-${i + 1}">
          <div class="citation-card__header">
            <span class="citation-card__index">${i + 1}</span>
            <span class="citation-card__title">${escapeHtml(s.title)}</span>
          </div>
          <div class="citation-card__meta">
            <span class="citation-card__tag">${s.type === "web_search_result_location" ? "Web" : "RAG"}</span>
          </div>
          ${s.snippets.map(sn => `<div class="citation-card__preview">"${escapeHtml(sn)}"</div>`).join("")}
          ${s.url && !s.url.startsWith("vectordb://")
            ? `<a class="citation-card__link" href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${truncateUrl(s.url)}</a>`
            : ""
          }
        </div>
      `
      )
      .join("");
  }

  function highlightCitation(index) {
    const card = document.getElementById(`citation-${index}`);
    if (card) {
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      card.style.background = "var(--accent-light)";
      card.style.transition = "background 300ms ease-out";
      setTimeout(() => {
        card.style.background = "";
      }, 2000);
    }
  }

  // ── Right Rail: Usage ──
  function renderUsage(usage, metadata) {
    // Update metadata panel with token breakdown
    const container = $("#metadata-body");
    if (!container) return;

    const existing = container.querySelectorAll(".usage-rows");
    existing.forEach(el => el.remove());

    const rows = [];
    if (usage.input_tokens) rows.push(["Input Tokens", usage.input_tokens.toLocaleString()]);
    if (usage.output_tokens) rows.push(["Output Tokens", usage.output_tokens.toLocaleString()]);
    if (usage.cache_creation_input_tokens) rows.push(["Cache Created", usage.cache_creation_input_tokens.toLocaleString()]);
    if (usage.cache_read_input_tokens) rows.push(["Cache Read", usage.cache_read_input_tokens.toLocaleString()]);

    // Estimated cost
    const model = metadata?.llm_model || "claude-sonnet-4-6";
    const isHaiku = model.includes("haiku");
    const inputCost = (usage.input_tokens || 0) * (isHaiku ? 0.0000008 : 0.000003);
    const outputCost = (usage.output_tokens || 0) * (isHaiku ? 0.000004 : 0.000015);
    const totalCost = inputCost + outputCost;
    if (totalCost > 0) rows.push(["Est. Cost", `$${totalCost.toFixed(4)}`]);

    if (rows.length > 0) {
      const table = container.querySelector(".metadata-table");
      if (table) {
        const tbody = table.querySelector("tbody") || table;
        for (const [k, v] of rows) {
          const tr = document.createElement("tr");
          tr.className = "usage-rows";
          tr.innerHTML = `<td>${k}</td><td>${v}</td>`;
          tbody.appendChild(tr);
        }
      }
    }
  }

  // ── Right Rail: Metadata ──
  function renderMetadata(metadata) {
    const container = $("#metadata-body");
    const timings = metadata.timings || {};
    const analysis = metadata.query_analysis || {};

    const rows = [
      ["Provider", metadata.llm_provider || "-"],
      ["Model", metadata.llm_model || "-"],
      ["Total Time", timings.total_ms ? `${timings.total_ms}ms` : "-"],
      ["Analysis", timings.query_analysis_ms ? `${timings.query_analysis_ms}ms` : "-"],
      ["Retrieval", timings.retrieval_ms ? `${timings.retrieval_ms}ms` : "-"],
      ["Synthesis", timings.synthesis_ms ? `${timings.synthesis_ms}ms` : "-"],
      ["Chunks Found", metadata.chunks_retrieved ?? "-"],
      ["Context", `${metadata.history_messages_included || 0} prior messages`],
      ["Intent", analysis.intent || "-"],
    ];

    container.innerHTML = `
      <table class="metadata-table">
        ${rows.map(([k, v]) => `<tr><td>${k}</td><td>${escapeHtml(String(v))}</td></tr>`).join("")}
      </table>
    `;
  }

  // ═════════════════════════════════════════════════════════════
  // SETTINGS MODAL
  // ═════════════════════════════════════════════════════════════

  function openSettings() {
    const overlay = $("#modal-overlay");
    overlay.classList.add("modal-overlay--visible");
    if (state.settings) {
      $("#settings-provider").value = state.settings.llm_provider || "anthropic";
      updateModelDropdown();
      if (state.settings.llm_model) {
        $("#settings-model").value = state.settings.llm_model;
      }
      updateKeyStatus("openai", state.settings.openai_api_key_set);
      updateKeyStatus("anthropic", state.settings.anthropic_api_key_set);
    }
    $("#settings-openai-key").value = "";
    $("#settings-anthropic-key").value = "";
  }

  function closeSettings() {
    $("#modal-overlay").classList.remove("modal-overlay--visible");
  }

  async function saveSettings() {
    const provider = $("#settings-provider").value;
    const model = $("#settings-model").value;
    const openaiKey = $("#settings-openai-key").value.trim();
    const anthropicKey = $("#settings-anthropic-key").value.trim();

    const body = {
      llm_provider: provider,
      llm_model: model || null,
    };
    if (openaiKey) body.openai_api_key = openaiKey;
    if (anthropicKey) body.anthropic_api_key = anthropicKey;

    try {
      const result = await api("POST", "/api/settings", body);
      state.settings = result.settings;
      closeSettings();
      showToast("Settings saved", "success");
    } catch (e) {
      showToast("Failed to save: " + e.message, "error");
    }
  }

  function updateModelDropdown() {
    const provider = $("#settings-provider").value;
    const modelSelect = $("#settings-model");
    modelSelect.innerHTML = "";
    const providers = state.settings?.available_providers || [];
    const prov = providers.find((p) => p.id === provider);
    if (prov) {
      prov.models.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.textContent = m.name;
        modelSelect.appendChild(opt);
      });
    }
  }

  function updateKeyStatus(provider, isSet) {
    const dot = $(`#key-status-${provider}-dot`);
    const text = $(`#key-status-${provider}-text`);
    if (dot && text) {
      dot.className = `key-status__dot key-status__dot--${isSet ? "set" : "unset"}`;
      text.textContent = isSet ? "Key configured" : "Not configured";
    }
  }

  // ── Follow-up / Example Queries ──
  window.__askFollowUp = function (btn) {
    const query = btn.textContent;
    $("#query-input").value = query;
    submitQuery();
  };

  window.__runExample = function (btn) {
    const query = btn.textContent;
    $("#query-input").value = query;
    submitQuery();
  };

  // ═════════════════════════════════════════════════════════════
  // DATABASE EXPLORER (with drill-down)
  // ═════════════════════════════════════════════════════════════

  async function openExplorer(filterField, filterValue) {
    $("#explorer-overlay").classList.add("modal-overlay--visible");
    const body = $("#explorer-body");
    body.innerHTML = `<div style="padding:24px;text-align:center;color:var(--text-tertiary)">
      <div class="loading-spinner" style="margin:0 auto 12px"></div>
      Loading database statistics...
    </div>`;

    try {
      let url = "/api/db-stats";
      if (filterField && filterValue) {
        url += `?filter_field=${encodeURIComponent(filterField)}&filter_value=${encodeURIComponent(filterValue)}`;
      }
      const res = await fetch(url);
      if (!res.ok) throw new Error("Failed to load stats");
      const data = await res.json();
      renderExplorer(body, data);
    } catch (e) {
      body.innerHTML = `<div style="padding:24px;color:var(--negative)">
        Failed to load stats: ${escapeHtml(e.message)}
      </div>`;
    }
  }

  function closeExplorer() {
    $("#explorer-overlay").classList.remove("modal-overlay--visible");
  }

  function renderExplorer(container, data) {
    const collections = data.collections || {};
    const topicLabels = data.topic_labels || {};
    const compLabels = data.competitor_labels || {};
    const dbPath = data.db_path || "";
    const activeFilter = data.active_filter || null;

    let totalVectors = 0;
    let totalFiltered = 0;
    for (const [name, info] of Object.entries(collections)) {
      totalVectors += info.count || 0;
      if (info.filtered_count !== undefined) totalFiltered += info.filtered_count;
    }

    let html = `<div class="db-explorer">`;

    // Breadcrumb for drill-down
    if (activeFilter) {
      const filterLabel = compLabels[activeFilter.value] || formatSourceType(activeFilter.value) || topicLabels[activeFilter.value] || activeFilter.value;
      html += `<div class="db-breadcrumb">
        <button class="db-breadcrumb__item" onclick="window.__explorerBack()">All Data</button>
        <span class="db-breadcrumb__sep">&rsaquo;</span>
        <span class="db-breadcrumb__current">${escapeHtml(activeFilter.field)}: ${escapeHtml(filterLabel)}</span>
        <span style="margin-left:auto;font-size:var(--text-xs);color:var(--text-tertiary)">${totalFiltered.toLocaleString()} vectors</span>
      </div>`;
    }

    // Overview stat cards
    html += `<div class="db-section">
      <div class="db-section__title">Overview</div>
      <div class="db-stat-grid">
        <div class="db-stat-card">
          <div class="db-stat-card__value">${totalVectors.toLocaleString()}</div>
          <div class="db-stat-card__label">Total Vectors</div>
        </div>
    `;

    for (const [name, info] of Object.entries(collections)) {
      const shortName = name.replace("competitive_", "");
      const displayCount = info.filtered_count !== undefined ? info.filtered_count : info.count || 0;
      html += `<div class="db-stat-card">
        <div class="db-stat-card__value">${displayCount.toLocaleString()}</div>
        <div class="db-stat-card__label">${escapeHtml(shortName)}</div>
      </div>`;
    }
    html += `</div></div>`;

    // Per-collection breakdowns
    for (const [collName, info] of Object.entries(collections)) {
      const breakdowns = info.breakdowns;
      if (!breakdowns || Object.keys(breakdowns).length === 0) continue;

      const shortName = collName.replace("competitive_", "");
      html += `<div class="db-section">
        <div class="db-section__title">${escapeHtml(shortName)} — Breakdowns</div>`;

      if (breakdowns.competitor) {
        html += renderBarChart("By Competitor", breakdowns.competitor, info.count, compLabels, "competitor", activeFilter);
      }
      if (breakdowns.source_type) {
        html += renderBarChart("By Source Type", breakdowns.source_type, info.count, {}, "source_type", activeFilter);
      }
      if (breakdowns.primary_topic) {
        const topEntries = Object.entries(breakdowns.primary_topic).slice(0, 12);
        const topObj = Object.fromEntries(topEntries);
        html += renderBarChart("By Topic (Top 12)", topObj, info.count, topicLabels, "primary_topic", activeFilter);
      }
      if (breakdowns.credibility) {
        html += renderBarChart("By Credibility", breakdowns.credibility, info.count, {}, "credibility", activeFilter);
      }

      if (info.metadata_keys) {
        html += `<div style="margin-top:var(--space-4)">
          <div style="font-size:var(--text-xs);font-weight:600;text-transform:uppercase;letter-spacing:0.08em;color:var(--text-tertiary);margin-bottom:var(--space-2)">Metadata Fields</div>
          <div style="display:flex;flex-wrap:wrap;gap:4px">
            ${info.metadata_keys.map(k => `<span style="font-size:var(--text-xs);font-family:var(--font-mono);background:var(--surface-tint-blue);border:1px solid var(--border-secondary);padding:2px 8px">${escapeHtml(k)}</span>`).join("")}
          </div>
        </div>`;
      }

      html += `</div>`;
    }

    if (dbPath) {
      html += `<div style="margin-top:var(--space-4);padding-top:var(--space-3);border-top:1px solid var(--border-secondary)">
        <span style="font-size:var(--text-xs);color:var(--text-tertiary)">Storage: </span>
        <span style="font-size:var(--text-xs);font-family:var(--font-mono);color:var(--text-secondary)">${escapeHtml(dbPath)}</span>
      </div>`;
    }

    html += `</div>`;
    container.innerHTML = html;
  }

  function renderBarChart(title, counts, total, labelMap, fieldName, activeFilter) {
    const maxVal = Math.max(...Object.values(counts));
    const isClickable = !activeFilter || activeFilter.field !== fieldName;

    let html = `<div style="margin-top:var(--space-4)">
      <div style="font-size:var(--text-xs);font-weight:600;text-transform:uppercase;letter-spacing:0.08em;color:var(--text-tertiary);margin-bottom:var(--space-2)">${escapeHtml(title)}</div>
      <div class="db-bar-chart">`;

    for (const [key, count] of Object.entries(counts)) {
      const label = labelMap[key] || formatSourceType(key) || key;
      const pct = maxVal > 0 ? (count / maxVal) * 100 : 0;
      const clickAttr = isClickable
        ? ` class="db-bar-row db-bar-row--clickable" onclick="window.__explorerDrill('${escapeHtml(fieldName)}','${escapeHtml(key)}')" title="Click to drill down"`
        : ` class="db-bar-row"`;
      html += `<div${clickAttr}>
        <div class="db-bar-row__label">${escapeHtml(label)}</div>
        <div class="db-bar-row__bar"><div class="db-bar-row__fill" style="width:${pct}%"></div></div>
        <div class="db-bar-row__value">${count.toLocaleString()}</div>
      </div>`;
    }

    html += `</div></div>`;
    return html;
  }

  // Global handlers for explorer drill-down
  window.__explorerDrill = function (field, value) {
    openExplorer(field, value);
  };
  window.__explorerBack = function () {
    openExplorer();
  };

  // ═════════════════════════════════════════════════════════════
  // CONTENT INGESTION
  // ═════════════════════════════════════════════════════════════

  function openIngest() {
    $("#ingest-overlay").classList.add("modal-overlay--visible");
    loadUploadList();
  }

  function closeIngest() {
    $("#ingest-overlay").classList.remove("modal-overlay--visible");
  }

  async function loadUploadList() {
    const listContainer = $("#upload-list");
    try {
      const data = await api("GET", "/api/content/list");
      const files = data.files || [];
      if (files.length === 0) {
        listContainer.innerHTML = "";
        return;
      }
      listContainer.innerHTML = `
        <div class="upload-list__title">Uploaded Content (${files.length})</div>
        ${files.map(f => `
          <div class="upload-item">
            <div class="upload-item__icon">${fileIcon(f.extension)}</div>
            <div class="upload-item__info">
              <div class="upload-item__name">${escapeHtml(f.filename)}</div>
              <div class="upload-item__meta">${formatFileSize(f.size_bytes)} &middot; ${formatTimestamp(f.uploaded_at)}</div>
            </div>
            <div class="upload-item__status upload-item__status--${f.status}">${f.status}</div>
          </div>
        `).join("")}
      `;
    } catch (e) {
      listContainer.innerHTML = `<div style="padding:12px;color:var(--negative);font-size:var(--text-xs)">Failed to load: ${escapeHtml(e.message)}</div>`;
    }
  }

  async function handleFileUpload(files) {
    for (const file of files) {
      try {
        const formData = new FormData();
        formData.append("file", file);
        const res = await fetch("/api/content/upload", {
          method: "POST",
          body: formData,
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }));
          throw new Error(err.detail || "Upload failed");
        }
        showToast(`Uploaded: ${file.name}`, "success");
      } catch (e) {
        showToast(`Failed: ${file.name} — ${e.message}`, "error");
      }
    }
    loadUploadList();
  }

  function fileIcon(ext) {
    const icons = {
      ".pdf": "PDF", ".png": "IMG", ".jpg": "IMG", ".jpeg": "IMG",
      ".gif": "IMG", ".webp": "IMG", ".txt": "TXT", ".md": "MD",
      ".csv": "CSV", ".json": "JSON", ".html": "HTML", ".xml": "XML",
      ".doc": "DOC", ".docx": "DOC", ".xls": "XLS", ".xlsx": "XLS",
      ".pptx": "PPT",
    };
    return icons[ext] || "FILE";
  }

  function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1048576).toFixed(1) + " MB";
  }

  function formatTimestamp(iso) {
    try {
      const d = new Date(iso);
      return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      return iso;
    }
  }

  // ═════════════════════════════════════════════════════════════
  // CONVERSATION HISTORY
  // ═════════════════════════════════════════════════════════════

  function openHistory() {
    $("#history-overlay").classList.add("modal-overlay--visible");
    $("#history-search-input").value = "";
    loadHistorySessions();
  }

  function closeHistory() {
    $("#history-overlay").classList.remove("modal-overlay--visible");
  }

  async function loadHistorySessions() {
    const listContainer = $("#history-list");
    if (!state.username) {
      listContainer.innerHTML = '<div class="history-empty">Please log in to view history.</div>';
      return;
    }
    try {
      const data = await api("GET", `/api/sessions?username=${encodeURIComponent(state.username)}`);
      renderHistoryList(listContainer, data.sessions || []);
    } catch (e) {
      listContainer.innerHTML = `<div class="history-empty" style="color:var(--negative)">Failed to load: ${escapeHtml(e.message)}</div>`;
    }
  }

  async function searchHistorySessions(query) {
    const listContainer = $("#history-list");
    if (!state.username) return;
    try {
      const data = await api(
        "GET",
        `/api/sessions/search?q=${encodeURIComponent(query)}&username=${encodeURIComponent(state.username)}`
      );
      renderHistoryList(listContainer, data.sessions || []);
    } catch (e) {
      listContainer.innerHTML = `<div class="history-empty" style="color:var(--negative)">Search failed: ${escapeHtml(e.message)}</div>`;
    }
  }

  function renderHistoryList(container, sessions) {
    if (sessions.length === 0) {
      container.innerHTML = '<div class="history-empty">No sessions found.</div>';
      return;
    }
    container.innerHTML = sessions.map(s => {
      const title = s.title || "Untitled session";
      const date = formatTimestamp(s.created_at);
      const msgs = s.message_count || 0;
      const tokens = (s.total_tokens_input || 0) + (s.total_tokens_output || 0);
      const isActive = s.session_id === state.sessionId;
      return `
        <div class="history-item ${isActive ? 'history-item--active' : ''}" data-session-id="${s.session_id}">
          <div class="history-item__info">
            <div class="history-item__title">${escapeHtml(title)}</div>
            <div class="history-item__meta">
              <span>${date}</span>
              <span>${msgs} messages</span>
              ${tokens > 0 ? `<span>${tokens.toLocaleString()} tokens</span>` : ""}
            </div>
          </div>
          <div class="history-item__actions">
            <button class="history-action-btn history-action-btn--open"
                    onclick="window.__historyOpen('${s.session_id}')">Open</button>
            <button class="history-action-btn"
                    onclick="window.__historyExport('${s.session_id}')">Export</button>
            <button class="history-action-btn history-action-btn--delete"
                    onclick="window.__historyDelete('${s.session_id}')">Delete</button>
          </div>
        </div>
      `;
    }).join("");
  }

  window.__historyOpen = async function (sessionId) {
    state.sessionId = sessionId;
    clearConversation();
    await loadSessionMessages();
    closeHistory();
    updateSessionTokenDisplay();
    showToast("Session loaded", "success");
  };

  window.__historyExport = async function (sessionId) {
    try {
      const data = await api("GET", `/api/sessions/${sessionId}/export`);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const title = (data.session && data.session.title) || sessionId;
      a.download = `session-${title.replace(/[^a-z0-9]/gi, "_").substring(0, 40)}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      showToast("Session exported as JSON", "success");
    } catch (e) {
      showToast("Export failed: " + e.message, "error");
    }
  };

  window.__historyDelete = async function (sessionId) {
    if (sessionId === state.sessionId) {
      if (!confirm("Delete the active session? A new session will be created.")) return;
    } else {
      if (!confirm("Delete this session? This cannot be undone.")) return;
    }
    try {
      await api("DELETE", `/api/sessions/${sessionId}`);
      if (sessionId === state.sessionId) {
        clearConversation();
        await createNewSession();
      }
      showToast("Session deleted", "success");
      loadHistorySessions();
    } catch (e) {
      showToast("Delete failed: " + e.message, "error");
    }
  };

  // ═════════════════════════════════════════════════════════════
  // COLLAPSIBLE Q&A PAIRS
  // ═════════════════════════════════════════════════════════════

  function collapseSvg() {
    return '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="4 6 8 10 12 6"></polyline></svg>';
  }

  function expandSvg() {
    return '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="4 10 8 6 12 10"></polyline></svg>';
  }

  function addCollapseToggle(pairWrapper) {
    if (pairWrapper.querySelector(".qa-collapse-btn")) return;
    const toggle = document.createElement("button");
    toggle.className = "qa-collapse-btn";
    toggle.innerHTML = collapseSvg();
    toggle.title = "Collapse";
    toggle.addEventListener("click", () => {
      const isCollapsed = pairWrapper.classList.toggle("qa-pair--collapsed");
      toggle.innerHTML = isCollapsed ? expandSvg() : collapseSvg();
      toggle.title = isCollapsed ? "Expand" : "Collapse";
    });
    pairWrapper.insertBefore(toggle, pairWrapper.firstChild);
  }

  function collapseAllPairs() {
    $$("#conversation .qa-pair").forEach(pair => {
      pair.classList.add("qa-pair--collapsed");
      const btn = pair.querySelector(".qa-collapse-btn");
      if (btn) { btn.innerHTML = expandSvg(); btn.title = "Expand"; }
    });
  }

  function expandAllPairs() {
    $$("#conversation .qa-pair").forEach(pair => {
      pair.classList.remove("qa-pair--collapsed");
      const btn = pair.querySelector(".qa-collapse-btn");
      if (btn) { btn.innerHTML = collapseSvg(); btn.title = "Collapse"; }
    });
  }

  function updateConversationToolbar() {
    const toolbar = $("#conversation-toolbar");
    const pairs = $$("#conversation .qa-pair");
    if (toolbar) {
      toolbar.style.display = pairs.length > 1 ? "" : "none";
    }
  }

  // ═════════════════════════════════════════════════════════════
  // PER-QUERY INLINE METRICS
  // ═════════════════════════════════════════════════════════════

  function renderMessageMetrics(messageEl, usage, metadata) {
    const container = messageEl.querySelector(".message__metrics");
    if (!container) return;

    const badges = [];
    const model = metadata?.llm_model || "";
    if (model) badges.push(`<span class="metric-badge">${escapeHtml(model)}</span>`);

    const totalMs = metadata?.timings?.total_ms;
    if (totalMs) badges.push(`<span class="metric-badge">${formatDuration(totalMs)}</span>`);

    const chunks = metadata?.chunks_retrieved;
    if (chunks) badges.push(`<span class="metric-badge">${chunks} sources</span>`);

    const inp = usage?.input_tokens || 0;
    const out = usage?.output_tokens || 0;
    if (inp || out) {
      badges.push(`<span class="metric-badge">${inp.toLocaleString()} in / ${out.toLocaleString()} out</span>`);
    }

    const cached = (usage?.cache_read_input_tokens || 0);
    if (cached > 0) {
      badges.push(`<span class="metric-badge metric-badge--cache">${cached.toLocaleString()} cached</span>`);
    }

    // Estimated cost
    const isHaiku = model.includes("haiku");
    const inputCost = inp * (isHaiku ? 0.0000008 : 0.000003);
    const outputCost = out * (isHaiku ? 0.000004 : 0.000015);
    const totalCost = inputCost + outputCost;
    if (totalCost > 0) {
      badges.push(`<span class="metric-badge metric-badge--cost">$${totalCost.toFixed(4)}</span>`);
    }

    const historyMsgs = metadata?.history_messages_included || 0;
    if (historyMsgs > 0) {
      badges.push(`<span class="metric-badge">${historyMsgs} ctx msgs</span>`);
    }

    container.innerHTML = badges.join("");
  }

  // ═════════════════════════════════════════════════════════════
  // CLEAR ALL HISTORY
  // ═════════════════════════════════════════════════════════════

  async function clearAllHistory() {
    if (!state.username) return;
    if (!confirm("Delete all conversation history? This cannot be undone.")) return;
    try {
      await api("DELETE", `/api/sessions?username=${encodeURIComponent(state.username)}`);
      clearConversation();
      await createNewSession();
      updateSessionTokenDisplay();
      updateConversationToolbar();
      loadHistorySessions();
      showToast("All history cleared", "success");
    } catch (e) {
      showToast("Failed to clear history: " + e.message, "error");
    }
  }

  // ═════════════════════════════════════════════════════════════
  // EXPORT CONVERSATION
  // ═════════════════════════════════════════════════════════════

  function toggleExportDropdown() {
    $("#export-dropdown").classList.toggle("export-dropdown--visible");
  }

  function exportAsHtml() {
    $("#export-dropdown").classList.remove("export-dropdown--visible");

    const conversation = $("#conversation");
    const pairs = conversation.querySelectorAll(".qa-pair");
    if (pairs.length === 0) {
      showToast("No conversation to export", "error");
      return;
    }

    const sections = [];
    const pairsArray = Array.from(pairs).reverse(); // chronological order

    for (const pair of pairsArray) {
      const userBody = pair.querySelector(".message--user .message__body");
      const assistantBody = pair.querySelector(".message--assistant .message__body");
      const summarySlot = pair.querySelector(".executive-summary");

      let section = "";
      if (userBody) {
        section += `<div class="query"><h3>Query</h3><p>${userBody.innerHTML}</p></div>`;
      }
      if (summarySlot) {
        section += `<div class="summary">${summarySlot.outerHTML}</div>`;
      }
      if (assistantBody) {
        section += `<div class="answer"><h3>Analysis</h3>${assistantBody.innerHTML}</div>`;
      }
      sections.push(section);
    }

    const timestamp = new Date().toISOString().split("T")[0];

    const htmlDoc = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Competitive Intelligence Export - ${timestamp}</title>
  <style>
    body { font-family: 'DM Sans', 'Helvetica Neue', sans-serif; max-width: 800px; margin: 0 auto; padding: 40px 20px; color: #0F172A; line-height: 1.6; }
    .export-header { border-bottom: 3px solid #6366F1; padding-bottom: 16px; margin-bottom: 32px; }
    .export-header h1 { font-size: 24px; margin: 0; }
    .export-header p { color: #94A3B8; font-size: 13px; margin-top: 4px; }
    .qa-section { margin-bottom: 32px; padding-bottom: 24px; border-bottom: 1px solid #E2E8F0; }
    .query { background: #F1F5F9; padding: 16px; border-left: 3px solid #CBD5E1; margin-bottom: 16px; }
    .query h3, .answer h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #94A3B8; margin: 0 0 8px 0; }
    .answer h3 { color: #6366F1; }
    .summary { margin-bottom: 16px; }
    .executive-summary { background: linear-gradient(135deg, #F0F9F5, #EEF1FB); border: 1px solid rgba(99, 102, 241, 0.12); border-left: 4px solid #14B8A6; padding: 16px 20px; }
    table { border-collapse: collapse; width: 100%; margin: 12px 0; }
    th, td { border: 1px solid #E2E8F0; padding: 6px 12px; font-size: 13px; text-align: left; }
    th { background: #EEF1FB; font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: 0.08em; }
    code { font-family: 'JetBrains Mono', monospace; font-size: 12px; background: #EEF1FB; padding: 1px 5px; border: 1px solid #E2E8F0; }
    pre { background: #F1F5F9; border: 1px solid #E2E8F0; padding: 12px; overflow-x: auto; }
    pre code { background: none; border: none; padding: 0; }
    strong { font-weight: 600; }
    a { color: #6366F1; }
    .citation-ref { font-size: 10px; vertical-align: super; color: #6366F1; border: 1px solid rgba(99,102,241,0.35); padding: 0 4px; }
  </style>
</head>
<body>
  <div class="export-header">
    <h1>Competitive Intelligence Report</h1>
    <p>Exported ${timestamp} | ${pairsArray.length} Q&amp;A exchange${pairsArray.length !== 1 ? "s" : ""}</p>
  </div>
  ${sections.map(s => `<div class="qa-section">${s}</div>`).join("\n")}
</body>
</html>`;

    const blob = new Blob([htmlDoc], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `competitive-intel-${timestamp}.html`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast("Exported as HTML", "success");
  }

  function exportAsPdf() {
    $("#export-dropdown").classList.remove("export-dropdown--visible");

    const pairs = $("#conversation").querySelectorAll(".qa-pair");
    if (pairs.length === 0) {
      showToast("No conversation to export", "error");
      return;
    }

    window.print();
  }

  // ═════════════════════════════════════════════════════════════
  // UTILITIES
  // ═════════════════════════════════════════════════════════════

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function escapeCodeHtml(str) {
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function renderMarkdown(text) {
    // 1. Extract fenced code blocks FIRST (``` ... ```)
    const codeBlocks = [];
    text = text.replace(
      /```(\w*)\n([\s\S]*?)```/g,
      (match, lang, code) => {
        const escaped = escapeCodeHtml(code.replace(/\n$/, ""));
        const langClass = lang ? ` class="language-${lang}"` : "";
        const html = `<pre><code${langClass}>${escaped}</code></pre>`;
        const placeholder = `__CODEBLOCK_${codeBlocks.length}__`;
        codeBlocks.push(html);
        return "\n" + placeholder + "\n";
      }
    );

    // 2. Extract inline code (`code`) to prevent <br> injection
    const inlineCode = [];
    text = text.replace(
      /`([^`\n]+)`/g,
      (match, code) => {
        const escaped = escapeCodeHtml(code);
        const placeholder = `__INLINECODE_${inlineCode.length}__`;
        inlineCode.push(`<code>${escaped}</code>`);
        return placeholder;
      }
    );

    // 3. Extract pipe tables
    const tableBlocks = [];
    text = text.replace(
      /((?:^\|.+\|[ \t]*\n){2,})/gm,
      (match) => {
        const rows = match.trim().split("\n").filter(r => r.trim());
        if (rows.length < 2) return match;

        const sepIdx = rows.findIndex(r => /^\|[\s\-:|]+\|$/.test(r.trim()));
        if (sepIdx < 0) return match;

        const parseRow = (row) =>
          row.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());

        const headerCells = parseRow(rows[sepIdx - 1] || rows[0]);
        const dataRows = rows.slice(sepIdx + 1);

        let tableHtml = '<table class="md-table"><thead><tr>';
        for (const cell of headerCells) {
          tableHtml += `<th>${cell}</th>`;
        }
        tableHtml += "</tr></thead><tbody>";
        for (const row of dataRows) {
          if (/^\|[\s\-:|]+\|$/.test(row.trim())) continue;
          const cells = parseRow(row);
          tableHtml += "<tr>";
          for (const cell of cells) {
            tableHtml += `<td>${cell}</td>`;
          }
          tableHtml += "</tr>";
        }
        tableHtml += "</tbody></table>";

        const placeholder = `__TABLE_${tableBlocks.length}__`;
        tableBlocks.push(tableHtml);
        return "\n" + placeholder + "\n";
      }
    );

    // 4. HTML-escape remaining text
    let html = escapeHtml(text);

    // 5. Markdown transforms (inline code already extracted — no backtick rule here)
    html = html.replace(/^---$/gm, "<hr>");
    html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
    html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
    html = html.replace(/^- (.+)$/gm, "<li>$1</li>");
    html = html.replace(/(<li>.*<\/li>\n?)+/g, "<ul>$&</ul>");
    html = html.replace(/^\d+\. (.+)$/gm, "<li>$1</li>");
    html = html.replace(
      /\[([^\]]+)\]\(([^)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener" style="color:var(--text-link)">$1</a>'
    );

    // 6. Paragraphs and line breaks
    html = html.replace(/\n\n/g, "</p><p>");
    html = "<p>" + html + "</p>";
    html = html.replace(/<p>\s*<\/p>/g, "");
    html = html.replace(/<p>(<h[123]>)/g, "$1");
    html = html.replace(/(<\/h[123]>)<\/p>/g, "$1");
    html = html.replace(/<p>(<ul>)/g, "$1");
    html = html.replace(/(<\/ul>)<\/p>/g, "$1");
    html = html.replace(/<p>(<hr>)<\/p>/g, "$1");
    html = html.replace(/<p>(<hr>)/g, "$1");
    html = html.replace(/<p>(<pre>)/g, "$1");
    html = html.replace(/(<\/pre>)<\/p>/g, "$1");
    html = html.replace(/([^>])\n([^<])/g, "$1<br>$2");

    // 7. Restore placeholders (tables → inline code → code blocks)
    for (let i = 0; i < tableBlocks.length; i++) {
      html = html.replace(`__TABLE_${i}__`, tableBlocks[i]);
    }
    for (let i = 0; i < inlineCode.length; i++) {
      html = html.replace(`__INLINECODE_${i}__`, inlineCode[i]);
    }
    for (let i = 0; i < codeBlocks.length; i++) {
      html = html.replace(`__CODEBLOCK_${i}__`, codeBlocks[i]);
    }

    return html;
  }

  function formatSourceType(type) {
    const map = {
      official_docs: "Official Docs",
      blog: "Blog",
      github_issue: "GitHub Issue",
      github_discussion: "GitHub Discussion",
      github_release: "GitHub Release",
      community_reddit: "Reddit",
      community_hn: "Hacker News",
      benchmark: "Benchmark",
      product_page: "Product Page",
      case_study: "Case Study",
      whitepaper: "Whitepaper",
      comparison_page: "Comparison",
    };
    return map[type] || type;
  }

  function truncateUrl(url) {
    try {
      const u = new URL(url);
      let path = u.pathname;
      if (path.length > 40) path = path.slice(0, 37) + "...";
      return u.hostname + path;
    } catch {
      return url.length > 60 ? url.slice(0, 57) + "..." : url;
    }
  }

  function formatDuration(ms) {
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  }

  function autoResizeTextarea() {
    const el = $("#query-input");
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  }

  function checkSvg() {
    return '<svg class="progress-step__icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 8 7 12 13 4"></polyline></svg>';
  }

  function showToast(message, type = "success") {
    const toast = $("#toast");
    toast.textContent = message;
    toast.className = `toast toast--${type} toast--visible`;
    setTimeout(() => {
      toast.classList.remove("toast--visible");
    }, 3500);
  }

  // ═════════════════════════════════════════════════════════════
  // TAB NAVIGATION
  // ═════════════════════════════════════════════════════════════

  document.addEventListener("DOMContentLoaded", () => {
    const tabBtns = document.querySelectorAll(".tab-nav__btn");
    tabBtns.forEach(btn => {
      btn.addEventListener("click", () => {
        const tabId = btn.dataset.tab;

        // Update buttons
        tabBtns.forEach(b => b.classList.remove("tab-nav__btn--active"));
        btn.classList.add("tab-nav__btn--active");

        // Update tab content
        document.querySelectorAll(".tab-content").forEach(tc => {
          tc.classList.remove("tab-content--active");
        });
        const target = document.getElementById("tab-" + tabId);
        if (target) target.classList.add("tab-content--active");

        // Toggle chat-specific nav buttons visibility
        const chatButtons = [
          document.getElementById("btn-open-ingest"),
          document.getElementById("btn-open-explorer"),
          document.getElementById("btn-new-session"),
          document.getElementById("btn-open-history"),
          document.getElementById("btn-open-export"),
        ];
        const isChatTab = tabId === "chat";
        chatButtons.forEach(b => {
          if (b) b.style.display = isChatTab ? "" : "none";
        });
        // Show/hide export dropdown parent
        const exportParent = document.getElementById("btn-open-export")?.parentElement;
        if (exportParent) exportParent.style.display = isChatTab ? "" : "none";
      });
    });
  });

  // Expose showToast and state for battlecard.js
  window.__appShowToast = showToast;
  window.__appState = state;
  window.__appEscapeHtml = escapeHtml;
})();
