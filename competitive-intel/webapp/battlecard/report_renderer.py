"""McKinsey-style HTML report renderer for battle cards.

Generates a premium, print-ready HTML document with:
- Page 1: Executive Overview & Client Alignment
- Page 2: Head-to-Head Technical & Visual Evidence
- Page 3: Tactical Execution (Sales Rep Only)

The HTML is self-contained with embedded CSS for both screen and print/PDF.
"""

import html
import logging
from datetime import datetime

from webapp.battlecard.models import BattleCardReport

logger = logging.getLogger(__name__)


def render_html(report: BattleCardReport) -> str:
    """Render a BattleCardReport as premium HTML."""
    h = html.escape

    # Build feature matrix rows
    feature_rows = ""
    for f in report.feature_matrix:
        kx_class = f"rating-{f.kx_rating}" if f.kx_rating in ("green", "yellow", "red") else ""
        comp_class = f"rating-{f.competitor_rating}" if f.competitor_rating in ("green", "yellow", "red") else ""
        feature_rows += f"""
        <tr>
          <td class="feature-name">{h(f.feature)}</td>
          <td class="rating-cell {kx_class}">{h(f.kx_detail or f.kx_rating.upper())}</td>
          <td class="rating-cell {comp_class}">{h(f.competitor_detail or f.competitor_rating.upper())}</td>
        </tr>"""

    # Build benchmark rows + chart data
    benchmark_rows = ""
    chart_labels = []
    chart_kx = []
    chart_comp = []
    for b in report.benchmarks:
        benchmark_rows += f"""
        <tr>
          <td>{h(b.metric)}</td>
          <td class="kx-value">{h(b.kx_value)}</td>
          <td class="comp-value">{h(b.competitor_value)}</td>
          <td class="source-cell">{h(b.source)}</td>
        </tr>"""
        chart_labels.append(b.metric[:30])
        # Extract numeric values for chart (best effort)
        chart_kx.append(_extract_number(b.kx_value))
        chart_comp.append(_extract_number(b.competitor_value))

    # Build pain points
    pain_rows = ""
    for p in report.pain_points:
        pain_rows += f"""
        <tr>
          <td class="pain-cell">{h(p.client_pain)}</td>
          <td class="solution-cell">{h(p.kx_solution)}</td>
        </tr>"""

    # Build trap questions
    trap_items = ""
    for i, t in enumerate(report.trap_questions, 1):
        trap_items += f"""
        <div class="trap-card">
          <div class="trap-number">Q{i}</div>
          <div class="trap-content">
            <div class="trap-question">"{h(t.question)}"</div>
            <div class="trap-why">{h(t.why_it_works)}</div>
            {f'<div class="trap-source">Source: {h(t.source)}</div>' if t.source else ''}
          </div>
        </div>"""

    # Build objection handlers
    objection_items = ""
    for o in report.objection_handlers:
        objection_items += f"""
        <div class="objection-card">
          <div class="objection-label">IF THEY SAY</div>
          <div class="objection-text">"{h(o.objection)}"</div>
          <div class="response-label">YOU SAY</div>
          <div class="response-text">{h(o.response)}</div>
        </div>"""

    # Build competitor news
    news_items = ""
    for n in report.competitor_news:
        news_items += f"""
        <div class="news-item">
          <div class="news-date">{h(n.date)}</div>
          <div class="news-headline">{h(n.headline)}</div>
          <div class="news-implication">{h(n.implication)}</div>
        </div>"""

    generated_time = report.generated_at.strftime("%B %d, %Y at %H:%M UTC")
    use_case_label = report.use_case or "General Competitive Analysis"
    competitor = report.competitor_name or "Competitor"
    client = report.client_name or "Prospect"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Battle Card: KX vs {h(competitor)} | {h(client)}</title>
  <style>
    /* ── Reset & Typography ── */
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    :root {{
      --navy: #0a1628;
      --navy-light: #14243d;
      --indigo: #5b6ef5;
      --indigo-dark: #4757d6;
      --indigo-glow: rgba(91, 110, 245, 0.15);
      --teal: #2dd4bf;
      --emerald: #34d399;
      --amber: #fbbf24;
      --rose: #f43f5e;
      --slate-50: #f8fafc;
      --slate-100: #f1f5f9;
      --slate-200: #e2e8f0;
      --slate-300: #cbd5e1;
      --slate-400: #94a3b8;
      --slate-500: #64748b;
      --slate-600: #475569;
      --slate-700: #334155;
      --slate-800: #1e293b;
      --white: #ffffff;
      --font-sans: 'DM Sans', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
    }}

    @page {{
      size: A4;
      margin: 0.75in;
    }}

    body {{
      font-family: var(--font-sans);
      color: var(--slate-800);
      background: var(--white);
      font-size: 11pt;
      line-height: 1.6;
      -webkit-print-color-adjust: exact !important;
      print-color-adjust: exact !important;
    }}

    /* ── Page Breaks ── */
    .page {{
      page-break-after: always;
      padding: 0;
      min-height: auto;
    }}
    .page:last-child {{
      page-break-after: avoid;
    }}

    /* ── Header Bar ── */
    .report-header {{
      background: linear-gradient(135deg, var(--navy) 0%, var(--navy-light) 100%);
      color: var(--white);
      padding: 28px 36px;
      border-radius: 10px;
      margin-bottom: 28px;
      position: relative;
      overflow: hidden;
    }}
    .report-header::before {{
      content: '';
      position: absolute;
      top: -50%;
      right: -10%;
      width: 300px;
      height: 300px;
      background: radial-gradient(circle, var(--indigo-glow) 0%, transparent 70%);
      border-radius: 50%;
    }}
    .header-top {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      position: relative;
      z-index: 1;
    }}
    .header-brand {{
      font-size: 10pt;
      text-transform: uppercase;
      letter-spacing: 2.5px;
      color: var(--indigo);
      font-weight: 600;
      margin-bottom: 6px;
    }}
    .header-title {{
      font-size: 22pt;
      font-weight: 700;
      letter-spacing: -0.5px;
      line-height: 1.2;
    }}
    .header-title span {{
      color: var(--indigo);
    }}
    .header-meta {{
      display: flex;
      gap: 24px;
      margin-top: 12px;
      position: relative;
      z-index: 1;
    }}
    .meta-tag {{
      background: rgba(255,255,255,0.08);
      border: 1px solid rgba(255,255,255,0.12);
      border-radius: 6px;
      padding: 4px 14px;
      font-size: 9pt;
      color: var(--slate-300);
    }}
    .meta-tag strong {{
      color: var(--white);
    }}
    .header-badge {{
      background: var(--indigo);
      color: var(--white);
      padding: 8px 20px;
      border-radius: 8px;
      font-size: 9pt;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 1px;
    }}

    /* ── Sections ── */
    .section {{
      margin-bottom: 24px;
    }}
    .section-title {{
      font-size: 13pt;
      font-weight: 700;
      color: var(--navy);
      border-bottom: 3px solid var(--indigo);
      padding-bottom: 6px;
      margin-bottom: 16px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .page-label {{
      font-size: 8pt;
      text-transform: uppercase;
      letter-spacing: 3px;
      color: var(--slate-400);
      margin-bottom: 8px;
    }}

    /* ── Why KX Wins Box ── */
    .kx-wins {{
      background: linear-gradient(135deg, var(--indigo-glow), rgba(45, 212, 191, 0.08));
      border: 1px solid rgba(91, 110, 245, 0.25);
      border-left: 5px solid var(--indigo);
      border-radius: 8px;
      padding: 20px 24px;
      margin-bottom: 24px;
      font-size: 11.5pt;
      line-height: 1.7;
      color: var(--navy);
    }}

    /* ── Tables ── */
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 10pt;
    }}
    th {{
      background: var(--navy);
      color: var(--white);
      padding: 10px 14px;
      text-align: left;
      font-weight: 600;
      font-size: 9pt;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    td {{
      padding: 10px 14px;
      border-bottom: 1px solid var(--slate-200);
      vertical-align: top;
    }}
    tr:nth-child(even) td {{
      background: var(--slate-50);
    }}
    tr:hover td {{
      background: var(--indigo-glow);
    }}

    /* Pain Points Table */
    .pain-cell {{
      color: var(--rose);
      font-weight: 500;
    }}
    .solution-cell {{
      color: var(--navy);
    }}

    /* Feature Matrix Ratings */
    .rating-cell {{
      text-align: center;
      font-weight: 600;
      font-size: 9pt;
    }}
    .rating-green {{
      background: rgba(52, 211, 153, 0.15) !important;
      color: #059669;
    }}
    .rating-yellow {{
      background: rgba(251, 191, 36, 0.15) !important;
      color: #d97706;
    }}
    .rating-red {{
      background: rgba(244, 63, 94, 0.15) !important;
      color: #dc2626;
    }}
    .feature-name {{
      font-weight: 600;
      color: var(--navy);
    }}

    /* Benchmark Values */
    .kx-value {{
      color: #059669;
      font-weight: 700;
      font-family: var(--font-mono);
    }}
    .comp-value {{
      color: var(--slate-600);
      font-family: var(--font-mono);
    }}
    .source-cell {{
      font-size: 8.5pt;
      color: var(--slate-500);
      font-style: italic;
    }}

    /* ── Architecture Comparison ── */
    .arch-box {{
      background: var(--slate-50);
      border: 1px solid var(--slate-200);
      border-radius: 8px;
      padding: 20px 24px;
      font-size: 10pt;
      line-height: 1.7;
      margin-bottom: 20px;
    }}
    .arch-box p {{ margin-bottom: 8px; }}
    .arch-box strong {{ color: var(--indigo); }}

    /* ── Chart Container ── */
    .chart-container {{
      background: var(--white);
      border: 1px solid var(--slate-200);
      border-radius: 8px;
      padding: 20px;
      margin-bottom: 20px;
      height: 300px;
    }}

    /* ── Trap Questions ── */
    .trap-card {{
      display: flex;
      gap: 16px;
      margin-bottom: 16px;
      padding: 16px;
      background: var(--slate-50);
      border-radius: 8px;
      border-left: 4px solid var(--rose);
    }}
    .trap-number {{
      background: var(--rose);
      color: var(--white);
      width: 36px;
      height: 36px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 10pt;
      font-weight: 700;
      flex-shrink: 0;
    }}
    .trap-question {{
      font-weight: 600;
      color: var(--navy);
      font-size: 10.5pt;
      margin-bottom: 4px;
    }}
    .trap-why {{
      font-size: 9.5pt;
      color: var(--slate-600);
    }}
    .trap-source {{
      font-size: 8.5pt;
      color: var(--slate-400);
      margin-top: 4px;
      font-style: italic;
    }}

    /* ── Objection Handlers ── */
    .objection-card {{
      margin-bottom: 16px;
      padding: 16px;
      background: var(--slate-50);
      border-radius: 8px;
      border-left: 4px solid var(--indigo);
    }}
    .objection-label {{
      font-size: 8pt;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1.5px;
      color: var(--rose);
      margin-bottom: 4px;
    }}
    .objection-text {{
      font-size: 10pt;
      color: var(--slate-700);
      font-style: italic;
      margin-bottom: 10px;
    }}
    .response-label {{
      font-size: 8pt;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1.5px;
      color: var(--emerald);
      margin-bottom: 4px;
    }}
    .response-text {{
      font-size: 10pt;
      color: var(--navy);
      font-weight: 500;
    }}

    /* ── Competitor News ── */
    .news-item {{
      display: flex;
      gap: 12px;
      margin-bottom: 12px;
      padding: 10px 14px;
      background: var(--slate-50);
      border-radius: 6px;
    }}
    .news-date {{
      font-size: 8.5pt;
      color: var(--slate-400);
      white-space: nowrap;
      min-width: 80px;
      font-family: var(--font-mono);
    }}
    .news-headline {{
      font-weight: 600;
      color: var(--navy);
      font-size: 10pt;
    }}
    .news-implication {{
      font-size: 9pt;
      color: var(--slate-600);
      margin-top: 2px;
    }}

    /* ── Confidential Footer ── */
    .confidential-footer {{
      text-align: center;
      font-size: 8pt;
      color: var(--slate-400);
      border-top: 1px solid var(--slate-200);
      padding-top: 12px;
      margin-top: 24px;
      text-transform: uppercase;
      letter-spacing: 2px;
    }}

    /* ── Tactical Page Header ── */
    .tactical-warning {{
      background: linear-gradient(135deg, rgba(244, 63, 94, 0.08), rgba(244, 63, 94, 0.03));
      border: 1px solid rgba(244, 63, 94, 0.2);
      border-radius: 8px;
      padding: 12px 20px;
      margin-bottom: 20px;
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .tactical-warning-icon {{
      font-size: 18pt;
    }}
    .tactical-warning-text {{
      font-size: 9pt;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: var(--rose);
    }}

    /* ── Print Styles ── */
    @media print {{
      body {{ font-size: 10pt; }}
      .report-header {{ break-inside: avoid; }}
      .section {{ break-inside: avoid; }}
      .chart-container {{ break-inside: avoid; }}
      .trap-card {{ break-inside: avoid; }}
      .objection-card {{ break-inside: avoid; }}
    }}

    /* ── Screen Hover Effects ── */
    @media screen {{
      .trap-card:hover {{
        box-shadow: 0 4px 12px rgba(0,0,0,0.08);
        transform: translateY(-1px);
        transition: all 0.2s ease;
      }}
      .objection-card:hover {{
        box-shadow: 0 4px 12px rgba(0,0,0,0.08);
        transform: translateY(-1px);
        transition: all 0.2s ease;
      }}
      tr:hover td {{
        transition: background 0.15s ease;
      }}
    }}
  </style>
</head>
<body>

  <!-- ═══════════════ PAGE 1: EXECUTIVE OVERVIEW ═══════════════ -->
  <div class="page">
    <div class="report-header">
      <div class="header-top">
        <div>
          <div class="header-brand">KX Competitive Intelligence</div>
          <div class="header-title">Battle Card: <span>KX/kdb+</span> vs {h(competitor)}</div>
        </div>
        <div class="header-badge">CONFIDENTIAL</div>
      </div>
      <div class="header-meta">
        <div class="meta-tag"><strong>Client:</strong> {h(client)}</div>
        <div class="meta-tag"><strong>Use Case:</strong> {h(use_case_label)}</div>
        <div class="meta-tag"><strong>Generated:</strong> {generated_time}</div>
      </div>
    </div>

    <div class="page-label">Page 1 of 3 — Executive Overview</div>

    <div class="section">
      <div class="section-title">Why KX Wins</div>
      <div class="kx-wins">{h(report.why_kx_wins)}</div>
    </div>

    <div class="section">
      <div class="section-title">Client Context Matrix</div>
      <table>
        <thead>
          <tr>
            <th style="width:45%">Client Pain Point</th>
            <th style="width:55%">kdb+ Solution</th>
          </tr>
        </thead>
        <tbody>
          {pain_rows if pain_rows else '<tr><td colspan="2" style="text-align:center;color:var(--slate-400)">No client context provided — pain points inferred from use case</td></tr>'}
        </tbody>
      </table>
    </div>

    <div class="confidential-footer">
      KX Confidential — For Internal Sales Use Only — {generated_time}
    </div>
  </div>

  <!-- ═══════════════ PAGE 2: TECHNICAL EVIDENCE ═══════════════ -->
  <div class="page">
    <div class="page-label">Page 2 of 3 — Head-to-Head Technical Evidence</div>

    <div class="section">
      <div class="section-title">Architecture Comparison</div>
      <div class="arch-box">{_markdown_to_html(report.architecture_comparison)}</div>
    </div>

    <div class="section">
      <div class="section-title">Performance Benchmarks</div>
      <div class="chart-container" id="benchmarkChart">
        <canvas id="chartCanvas"></canvas>
      </div>
      <table>
        <thead>
          <tr>
            <th>Metric</th>
            <th>KX/kdb+</th>
            <th>{h(competitor)}</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {benchmark_rows if benchmark_rows else '<tr><td colspan="4" style="text-align:center;color:var(--slate-400)">No benchmark data available</td></tr>'}
        </tbody>
      </table>
    </div>

    <div class="section">
      <div class="section-title">Feature Matrix</div>
      <table>
        <thead>
          <tr>
            <th style="width:35%">Capability</th>
            <th style="width:32.5%">KX/kdb+</th>
            <th style="width:32.5%">{h(competitor)}</th>
          </tr>
        </thead>
        <tbody>
          {feature_rows if feature_rows else '<tr><td colspan="3" style="text-align:center;color:var(--slate-400)">Feature matrix data not available</td></tr>'}
        </tbody>
      </table>
    </div>

    <div class="confidential-footer">
      KX Confidential — For Internal Sales Use Only — {generated_time}
    </div>
  </div>

  <!-- ═══════════════ PAGE 3: TACTICAL EXECUTION ═══════════════ -->
  <div class="page">
    <div class="page-label">Page 3 of 3 — Tactical Execution</div>

    <div class="tactical-warning">
      <div class="tactical-warning-icon">&#128274;</div>
      <div class="tactical-warning-text">For Sales Rep's Eyes Only — Do Not Share With Client</div>
    </div>

    <div class="section">
      <div class="section-title">"Trap" Questions to Expose Weaknesses</div>
      {trap_items if trap_items else '<div style="text-align:center;color:var(--slate-400);padding:20px">No trap questions generated</div>'}
    </div>

    <div class="section">
      <div class="section-title">Objection Handling Playbook</div>
      {objection_items if objection_items else '<div style="text-align:center;color:var(--slate-400);padding:20px">No objection handlers generated</div>'}
    </div>

    <div class="section">
      <div class="section-title">Recent Competitor Activity (Last 90 Days)</div>
      {news_items if news_items else '<div style="text-align:center;color:var(--slate-400);padding:20px">No recent news found</div>'}
    </div>

    <div class="confidential-footer">
      KX Confidential — For Internal Sales Use Only — {generated_time}<br>
      Generated by KX Competitive Intelligence Platform — {len(report.agents_used)} agents deployed — {report.sources_count} sources analyzed — {report.generation_time_ms}ms
    </div>
  </div>

  <!-- ── Chart.js for Benchmark Visualization ── -->
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <script>
    document.addEventListener('DOMContentLoaded', function() {{
      const labels = {json_safe(chart_labels)};
      const kxData = {json_safe(chart_kx)};
      const compData = {json_safe(chart_comp)};

      // Only render chart if we have data
      if (labels.length === 0 || kxData.every(v => v === 0)) return;

      const ctx = document.getElementById('chartCanvas');
      if (!ctx) return;

      new Chart(ctx, {{
        type: 'bar',
        data: {{
          labels: labels,
          datasets: [
            {{
              label: 'KX/kdb+',
              data: kxData,
              backgroundColor: 'rgba(91, 110, 245, 0.8)',
              borderColor: '#5b6ef5',
              borderWidth: 1,
              borderRadius: 4,
            }},
            {{
              label: '{h(competitor)}',
              data: compData,
              backgroundColor: 'rgba(148, 163, 184, 0.6)',
              borderColor: '#94a3b8',
              borderWidth: 1,
              borderRadius: 4,
            }}
          ]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{
              position: 'top',
              labels: {{ font: {{ family: "'DM Sans', sans-serif", size: 11 }} }}
            }},
            title: {{
              display: true,
              text: 'Performance Benchmark Comparison',
              font: {{ family: "'DM Sans', sans-serif", size: 14, weight: '700' }},
              color: '#0a1628'
            }}
          }},
          scales: {{
            y: {{
              beginAtZero: true,
              grid: {{ color: 'rgba(0,0,0,0.05)' }}
            }},
            x: {{
              ticks: {{ font: {{ size: 9 }}, maxRotation: 45 }}
            }}
          }}
        }}
      }});
    }});
  </script>

</body>
</html>"""


def _extract_number(value: str) -> float:
    """Try to extract a numeric value from a benchmark string."""
    import re

    match = re.search(r"([\d,]+\.?\d*)", value.replace(",", ""))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return 0


def _markdown_to_html(text: str) -> str:
    """Simple markdown-to-HTML conversion for architecture text."""
    if not text:
        return "<p>Architecture comparison not available.</p>"

    h = html.escape
    lines = text.split("\n")
    result = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                result.append("</ul>")
                in_list = False
            result.append("<br>")
            continue

        if stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                result.append("<ul>")
                in_list = True
            item = stripped[2:]
            item = _inline_md(h(item))
            result.append(f"<li>{item}</li>")
        elif stripped.startswith("### "):
            if in_list:
                result.append("</ul>")
                in_list = False
            result.append(f"<h4>{h(stripped[4:])}</h4>")
        elif stripped.startswith("## "):
            if in_list:
                result.append("</ul>")
                in_list = False
            result.append(f"<h3>{h(stripped[3:])}</h3>")
        else:
            if in_list:
                result.append("</ul>")
                in_list = False
            result.append(f"<p>{_inline_md(h(stripped))}</p>")

    if in_list:
        result.append("</ul>")

    return "\n".join(result)


def _inline_md(text: str) -> str:
    """Convert inline markdown (bold, italic, code) to HTML."""
    import re

    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text


def json_safe(data) -> str:
    """Convert Python data to JSON-safe string for embedding in JS."""
    import json
    return json.dumps(data)
