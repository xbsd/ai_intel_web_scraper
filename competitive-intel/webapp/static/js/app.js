/**
 * Competitive Intelligence Q&A — Frontend Application
 *
 * Handles: query submission, streaming-style display, citation rendering,
 * filter management, settings modal, and conversation history.
 */

(function () {
  "use strict";

  // ── State ──
  const state = {
    loading: false,
    conversations: [],    // [{role, content, citations, followUps, metadata}]
    settings: null,
    statusData: null,
  };

  // ── DOM refs ──
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  // ── Initialization ──
  document.addEventListener("DOMContentLoaded", () => {
    bindEvents();
    loadStatus();
    loadSettings();
  });

  function bindEvents() {
    // Query submission
    $("#query-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submitQuery();
      }
    });
    $("#btn-submit").addEventListener("click", submitQuery);

    // Settings modal
    $("#btn-open-settings").addEventListener("click", openSettings);
    $("#btn-close-settings").addEventListener("click", closeSettings);
    $("#btn-cancel-settings").addEventListener("click", closeSettings);
    $("#btn-save-settings").addEventListener("click", saveSettings);
    $("#modal-overlay").addEventListener("click", (e) => {
      if (e.target === e.currentTarget) closeSettings();
    });

    // Provider change updates model dropdown
    $("#settings-provider").addEventListener("change", updateModelDropdown);

    // Auto-resize textarea
    $("#query-input").addEventListener("input", autoResizeTextarea);
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

  // ── Load metadata (competitors, topics, etc.) ──
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
    // Competitor filter
    const compSelect = $("#filter-competitor");
    compSelect.innerHTML = '<option value="">All Competitors</option>';
    (data.competitors || []).forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = c.name;
      compSelect.appendChild(opt);
    });

    // Topic filter
    const topicSelect = $("#filter-topic");
    topicSelect.innerHTML = '<option value="">All Topics</option>';
    (data.topics || []).forEach((t) => {
      const opt = document.createElement("option");
      opt.value = t.id;
      opt.textContent = t.name;
      topicSelect.appendChild(opt);
    });

    // Source type filter
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

  // ── Submit Query ──
  async function submitQuery() {
    const input = $("#query-input");
    const query = input.value.trim();
    if (!query || state.loading) return;

    state.loading = true;
    input.value = "";
    autoResizeTextarea();
    $("#btn-submit").disabled = true;

    // Add user message
    addUserMessage(query);

    // Show loading
    showLoading();

    try {
      // Build filters
      const competitorFilter = $("#filter-competitor").value || null;
      const topicFilter = $("#filter-topic").value || null;
      const sourceFilter = $("#filter-source").value || null;

      const result = await api("POST", "/api/query", {
        query,
        competitor_filter: competitorFilter ? [competitorFilter] : null,
        topic_filter: topicFilter ? [topicFilter] : null,
        source_type_filter: sourceFilter ? [sourceFilter] : null,
        n_results: 12,
      });

      hideLoading();
      addAssistantMessage(result);
    } catch (e) {
      hideLoading();
      addErrorMessage(e.message);
      showToast(e.message, "error");
    } finally {
      state.loading = false;
      $("#btn-submit").disabled = false;
      input.focus();
    }
  }

  // ── Render Messages ──
  function addUserMessage(query) {
    hideEmptyState();
    const conv = $("#conversation");
    const div = document.createElement("div");
    div.className = "message message--user";
    div.innerHTML = `
      <div class="message__header">
        <span class="message__role">Query</span>
      </div>
      <div class="message__body">${escapeHtml(query)}</div>
    `;
    conv.appendChild(div);
    scrollToBottom();
  }

  function addAssistantMessage(result) {
    const conv = $("#conversation");
    const div = document.createElement("div");
    div.className = "message message--assistant";

    const timing = result.metadata?.timings?.total_ms;
    const model = result.metadata?.llm_model || "";
    const chunksRetrieved = result.metadata?.chunks_retrieved || 0;

    let answerHtml = renderMarkdown(result.answer);
    // Convert [N] references to clickable citation badges
    answerHtml = answerHtml.replace(
      /\[(\d+)\]/g,
      '<span class="citation-ref" data-index="$1" title="View source [$1]">$1</span>'
    );

    let followUpsHtml = "";
    if (result.follow_up_questions && result.follow_up_questions.length > 0) {
      followUpsHtml = `
        <div class="follow-ups">
          ${result.follow_up_questions
            .map(
              (q) =>
                `<button class="follow-up-btn" onclick="window.__askFollowUp(this)">${escapeHtml(q)}</button>`
            )
            .join("")}
        </div>
      `;
    }

    div.innerHTML = `
      <div class="message__header">
        <span class="message__role">Analysis</span>
        <span class="message__model">${escapeHtml(model)}</span>
        <span class="message__timing">${
          timing ? formatDuration(timing) : ""
        } · ${chunksRetrieved} sources</span>
      </div>
      <div class="message__body">${answerHtml}</div>
      ${followUpsHtml}
    `;
    conv.appendChild(div);

    // Bind citation click handlers
    div.querySelectorAll(".citation-ref").forEach((el) => {
      el.addEventListener("click", () => {
        const idx = parseInt(el.dataset.index, 10);
        highlightCitation(idx);
      });
    });

    // Render citations in right rail
    renderCitations(result.citations || []);
    renderMetadata(result.metadata || {});

    scrollToBottom();
  }

  function addErrorMessage(message) {
    const conv = $("#conversation");
    const div = document.createElement("div");
    div.className = "message message--assistant";
    div.innerHTML = `
      <div class="message__header">
        <span class="message__role" style="color:var(--negative)">Error</span>
      </div>
      <div class="message__body" style="color:var(--negative)">${escapeHtml(message)}</div>
    `;
    conv.appendChild(div);
    scrollToBottom();
  }

  // ── Loading UI ──
  function showLoading() {
    hideEmptyState();
    const conv = $("#conversation");
    const div = document.createElement("div");
    div.id = "loading-indicator";
    div.innerHTML = `
      <div class="loading-bar"><div class="loading-bar__fill"></div></div>
      <div class="loading-message">
        <div class="message__header">
          <span class="message__role" style="color:var(--accent-primary)">Processing</span>
        </div>
        <div class="loading-message__steps">
          <div class="loading-step loading-step--active" id="step-analyze">
            <div class="loading-spinner"></div>
            <span>Analyzing query and generating sub-queries</span>
          </div>
          <div class="loading-step" id="step-retrieve">
            <div class="loading-step__icon"></div>
            <span>Searching vector database (HyDE + multi-query)</span>
          </div>
          <div class="loading-step" id="step-synthesize">
            <div class="loading-step__icon"></div>
            <span>Synthesizing grounded answer with citations</span>
          </div>
        </div>
      </div>
    `;
    conv.appendChild(div);
    scrollToBottom();

    // Simulate step progression
    setTimeout(() => {
      const el = document.getElementById("step-analyze");
      if (el) {
        el.classList.remove("loading-step--active");
        el.classList.add("loading-step--done");
        el.querySelector(".loading-spinner")?.remove();
        el.insertAdjacentHTML("afterbegin", checkSvg());
      }
      const el2 = document.getElementById("step-retrieve");
      if (el2) {
        el2.classList.add("loading-step--active");
        el2.insertAdjacentHTML("afterbegin", '<div class="loading-spinner"></div>');
      }
    }, 2000);

    setTimeout(() => {
      const el = document.getElementById("step-retrieve");
      if (el) {
        el.classList.remove("loading-step--active");
        el.classList.add("loading-step--done");
        el.querySelector(".loading-spinner")?.remove();
        el.insertAdjacentHTML("afterbegin", checkSvg());
      }
      const el2 = document.getElementById("step-synthesize");
      if (el2) {
        el2.classList.add("loading-step--active");
        el2.insertAdjacentHTML("afterbegin", '<div class="loading-spinner"></div>');
      }
    }, 4000);
  }

  function hideLoading() {
    const el = document.getElementById("loading-indicator");
    if (el) el.remove();
  }

  function hideEmptyState() {
    const el = $("#conversation-empty");
    if (el) el.style.display = "none";
  }

  // ── Right Rail: Citations ──
  function renderCitations(citations) {
    const container = $("#citations-list");
    const countEl = $("#citations-count");

    if (!citations.length) {
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

  function highlightCitation(index) {
    // Scroll to and highlight the citation card in the right rail
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
      ["Intent", analysis.intent || "-"],
    ];

    container.innerHTML = `
      <table class="metadata-table">
        ${rows.map(([k, v]) => `<tr><td>${k}</td><td>${escapeHtml(String(v))}</td></tr>`).join("")}
      </table>
    `;
  }

  // ── Settings Modal ──
  function openSettings() {
    const overlay = $("#modal-overlay");
    overlay.classList.add("modal-overlay--visible");

    // Populate current values
    if (state.settings) {
      $("#settings-provider").value = state.settings.llm_provider || "anthropic";
      updateModelDropdown();
      if (state.settings.llm_model) {
        $("#settings-model").value = state.settings.llm_model;
      }
      // Show key status
      updateKeyStatus("openai", state.settings.openai_api_key_set);
      updateKeyStatus("anthropic", state.settings.anthropic_api_key_set);
    }
    // Clear key inputs (never show keys)
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

  // ── Follow-up Questions ──
  window.__askFollowUp = function (btn) {
    const query = btn.textContent;
    $("#query-input").value = query;
    submitQuery();
  };

  // ── Example Queries ──
  window.__runExample = function (btn) {
    const query = btn.textContent;
    $("#query-input").value = query;
    submitQuery();
  };

  // ── Utilities ──
  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function renderMarkdown(text) {
    // Lightweight markdown rendering (no external deps)
    let html = escapeHtml(text);

    // Horizontal rules (---) — must be processed before bold/headers
    html = html.replace(/^---$/gm, '<hr>');

    // Headers
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    // Italic
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Unordered lists
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');

    // Numbered lists
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');

    // Links
    html = html.replace(
      /\[([^\]]+)\]\(([^)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener" style="color:var(--text-link)">$1</a>'
    );

    // Paragraphs (double newline)
    html = html.replace(/\n\n/g, '</p><p>');
    html = '<p>' + html + '</p>';

    // Clean up empty paragraphs
    html = html.replace(/<p>\s*<\/p>/g, '');
    // Fix paragraphs wrapping block elements
    html = html.replace(/<p>(<h[123]>)/g, '$1');
    html = html.replace(/(<\/h[123]>)<\/p>/g, '$1');
    html = html.replace(/<p>(<ul>)/g, '$1');
    html = html.replace(/(<\/ul>)<\/p>/g, '$1');
    html = html.replace(/<p>(<hr>)<\/p>/g, '$1');
    html = html.replace(/<p>(<hr>)/g, '$1');

    // Single newlines to <br> inside paragraphs
    html = html.replace(/([^>])\n([^<])/g, '$1<br>$2');

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

  function scrollToBottom() {
    const conv = $("#conversation");
    requestAnimationFrame(() => {
      conv.scrollTop = conv.scrollHeight;
    });
  }

  function autoResizeTextarea() {
    const el = $("#query-input");
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  }

  function checkSvg() {
    return '<svg class="loading-step__icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 8 7 12 13 4"></polyline></svg>';
  }

  // ── Toast Notifications ──
  function showToast(message, type = "success") {
    const toast = $("#toast");
    toast.textContent = message;
    toast.className = `toast toast--${type} toast--visible`;
    setTimeout(() => {
      toast.classList.remove("toast--visible");
    }, 3500);
  }
})();
