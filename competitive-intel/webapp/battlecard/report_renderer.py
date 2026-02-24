"""McKinsey-style HTML report renderer for battle cards.

Generates a premium, print-ready HTML document with:
- Page 1: Executive Overview & Client Alignment (+ Client Intelligence)
- Page 2: Head-to-Head Technical & Visual Evidence
- Page 3: Tactical Execution (Sales Rep Only — enhanced)

Supports three export modes:
- "combined":           Full battle card (all pages, logos, confidential)
- "client_tearsheet":   Pages 1-2 only, no confidential markings
- "sales_confidential": Page 3 only, sales-eyes-only

The HTML is self-contained with embedded CSS for both screen and print/PDF.
"""

import html
import json
import logging
from datetime import datetime

from webapp.battlecard.models import BattleCardReport

logger = logging.getLogger(__name__)

# ── KX logo as inline SVG ──
KX_LOGO_SVG = """<svg viewBox="0 0 120 40" xmlns="http://www.w3.org/2000/svg" width="90" height="30">
  <rect width="120" height="40" rx="4" fill="#0a1628"/>
  <text x="16" y="29" font-family="'DM Sans','Inter',sans-serif" font-size="24" font-weight="800" fill="#5b6ef5">K</text>
  <text x="40" y="29" font-family="'DM Sans','Inter',sans-serif" font-size="24" font-weight="800" fill="#2dd4bf">X</text>
  <text x="64" y="26" font-family="'JetBrains Mono',monospace" font-size="10" font-weight="600" fill="#94a3b8">kdb+</text>
</svg>"""


def render_html(report: BattleCardReport, export_mode: str = "combined") -> str:
    """Render a BattleCardReport as premium HTML.

    Args:
        report: The battle card report data.
        export_mode: One of "combined", "client_tearsheet", "sales_confidential".
    """
    h = html.escape

    # Determine which pages to include
    show_page1 = export_mode in ("combined", "client_tearsheet")
    show_page2 = export_mode in ("combined", "client_tearsheet")
    show_page3 = export_mode in ("combined", "sales_confidential")
    show_confidential = export_mode in ("combined", "sales_confidential")

    generated_time = report.generated_at.strftime("%B %d, %Y at %H:%M UTC")
    use_case_label = report.use_case or "General Competitive Analysis"
    competitor = report.competitor_name or "Competitor"
    client = report.client_name or "Prospect"

    # ── Build data fragments ──

    # Feature matrix rows
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

    # Benchmark rows + chart data
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
        chart_kx.append(_extract_number(b.kx_value))
        chart_comp.append(_extract_number(b.competitor_value))

    # Pain points
    pain_rows = ""
    for p in report.pain_points:
        pain_rows += f"""
        <tr>
          <td class="pain-cell">{h(p.client_pain)}</td>
          <td class="solution-cell">{h(p.kx_solution)}</td>
        </tr>"""

    # Trap questions
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

    # Objection handlers
    objection_items = ""
    for o in report.objection_handlers:
        objection_items += f"""
        <div class="objection-card">
          <div class="objection-label">IF THEY SAY</div>
          <div class="objection-text">"{h(o.objection)}"</div>
          <div class="response-label">YOU SAY</div>
          <div class="response-text">{h(o.response)}</div>
        </div>"""

    # Competitor news — using a TABLE to fix the date/headline overlap
    news_table_rows = ""
    for n in report.competitor_news:
        news_table_rows += f"""
        <tr>
          <td class="news-date-cell">{h(n.date)}</td>
          <td>
            <div class="news-headline">{h(n.headline)}</div>
            <div class="news-implication">{h(n.implication)}</div>
          </td>
        </tr>"""

    # Client intelligence section
    client_intel_html = ""
    if report.client_intelligence and show_page1:
        ci = report.client_intelligence
        intel_parts = []
        if ci.company_overview:
            intel_parts.append(f'<div class="intel-overview">{h(ci.company_overview)}</div>')
        if ci.ai_db_initiatives:
            intel_parts.append(f"""
            <div class="intel-subsection">
              <div class="intel-subsection-title">AI & Database Initiatives</div>
              <div class="intel-subsection-text">{h(ci.ai_db_initiatives)}</div>
            </div>""")
        if ci.technology_stack:
            intel_parts.append(f"""
            <div class="intel-subsection">
              <div class="intel-subsection-title">Known Technology Stack</div>
              <div class="intel-subsection-text">{h(ci.technology_stack)}</div>
            </div>""")
        if ci.key_priorities:
            priority_list = "".join(f"<li>{h(p)}</li>" for p in ci.key_priorities)
            intel_parts.append(f"""
            <div class="intel-subsection">
              <div class="intel-subsection-title">Key Priorities</div>
              <ul class="intel-list">{priority_list}</ul>
            </div>""")
        if ci.recent_news:
            news_rows = ""
            for item in ci.recent_news[:6]:
                cat_badge = f'<span class="intel-category-badge">{h(item.category)}</span>' if item.category else ""
                news_rows += f"""
                <tr>
                  <td class="news-date-cell">{h(item.date)}</td>
                  <td>
                    <div class="news-headline">{h(item.headline)} {cat_badge}</div>
                    {f'<div class="news-implication">{h(item.summary)}</div>' if item.summary else ''}
                  </td>
                </tr>"""
            intel_parts.append(f"""
            <div class="intel-subsection">
              <div class="intel-subsection-title">Recent Activity</div>
              <table class="news-table">
                <thead><tr><th style="width:100px">Date</th><th>Activity</th></tr></thead>
                <tbody>{news_rows}</tbody>
              </table>
            </div>""")

        if intel_parts:
            client_intel_html = f"""
    <div class="section">
      <div class="section-title">Client Intelligence: {h(client)}</div>
      {"".join(intel_parts)}
    </div>"""

    # Competitive positioning section
    positioning_html = ""
    if report.competitive_positioning and show_page3:
        cp = report.competitive_positioning
        diff_list = "".join(f"<li>{h(d)}</li>" for d in cp.key_differentiators)
        landmine_list = "".join(f"<li>{h(l)}</li>" for l in cp.landmines_to_set)
        proof_list = "".join(f"<li>{h(p)}</li>" for p in cp.proof_points)
        positioning_html = f"""
    <div class="section">
      <div class="section-title">Competitive Positioning</div>
      <div class="positioning-statement">{h(cp.positioning_statement)}</div>
      <div class="positioning-grid">
        <div class="positioning-col">
          <div class="positioning-col-title">Key Differentiators</div>
          <ul class="positioning-list positioning-list--green">{diff_list}</ul>
        </div>
        <div class="positioning-col">
          <div class="positioning-col-title">Landmines to Set</div>
          <ul class="positioning-list positioning-list--amber">{landmine_list}</ul>
        </div>
        <div class="positioning-col">
          <div class="positioning-col-title">Proof Points</div>
          <ul class="positioning-list positioning-list--blue">{proof_list}</ul>
        </div>
      </div>
    </div>"""

    # Deal strategy section
    deal_strategy_html = ""
    if report.deal_strategy and show_page3:
        strategy_rows = ""
        for ds in report.deal_strategy:
            strategy_rows += f"""
            <tr>
              <td class="strategy-stage">{h(ds.stage)}</td>
              <td>{h(ds.action)}</td>
              <td class="strategy-talking-point">{h(ds.talking_point)}</td>
            </tr>"""
        deal_strategy_html = f"""
    <div class="section">
      <div class="section-title">Deal Strategy Playbook</div>
      <table>
        <thead>
          <tr>
            <th style="width:20%">Stage</th>
            <th style="width:40%">Action</th>
            <th style="width:40%">Talking Point</th>
          </tr>
        </thead>
        <tbody>{strategy_rows}</tbody>
      </table>
    </div>"""

    # Pricing guidance section
    pricing_html = ""
    if report.pricing_guidance and show_page3:
        pricing_html = f"""
    <div class="section">
      <div class="section-title">Pricing & TCO Guidance</div>
      <div class="pricing-box">{h(report.pricing_guidance)}</div>
    </div>"""

    # Client logo
    client_logo_html = ""
    if report.client_logo_url:
        client_logo_html = f'<img src="{h(report.client_logo_url)}" alt="{h(client)} logo" class="client-logo" crossorigin="anonymous">'

    # Header badge text
    badge_text = "CONFIDENTIAL" if show_confidential else "CLIENT DOCUMENT"

    # Determine total page count and label per page
    pages = []
    if show_page1:
        pages.append("executive")
    if show_page2:
        pages.append("technical")
    if show_page3:
        pages.append("tactical")
    total_pages = len(pages)

    def page_label(page_type):
        idx = pages.index(page_type) + 1
        labels = {"executive": "Executive Overview", "technical": "Head-to-Head Technical Evidence", "tactical": "Tactical Execution"}
        return f"Page {idx} of {total_pages} — {labels[page_type]}"

    # ── Assemble HTML ──
    html_parts = []

    html_parts.append(f"""<!DOCTYPE html>
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
      padding: 24px 32px;
      border-radius: 10px;
      margin-bottom: 24px;
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
    .header-logos {{
      display: flex;
      align-items: center;
      gap: 16px;
      margin-bottom: 10px;
    }}
    .client-logo {{
      height: 28px;
      max-width: 100px;
      object-fit: contain;
      border-radius: 4px;
      background: rgba(255,255,255,0.9);
      padding: 3px 6px;
    }}
    .logo-divider {{
      color: var(--slate-400);
      font-size: 16pt;
      font-weight: 300;
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
      font-size: 20pt;
      font-weight: 700;
      letter-spacing: -0.5px;
      line-height: 1.2;
    }}
    .header-title span {{
      color: var(--indigo);
    }}
    .header-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
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
      white-space: nowrap;
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
      table-layout: fixed;
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
      word-wrap: break-word;
      overflow-wrap: break-word;
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

    /* ── Client Intelligence ── */
    .intel-overview {{
      font-size: 10.5pt;
      color: var(--slate-700);
      line-height: 1.7;
      margin-bottom: 16px;
      padding: 14px 18px;
      background: var(--slate-50);
      border-radius: 8px;
      border-left: 4px solid var(--teal);
    }}
    .intel-subsection {{
      margin-bottom: 14px;
    }}
    .intel-subsection-title {{
      font-size: 10pt;
      font-weight: 700;
      color: var(--navy);
      margin-bottom: 6px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .intel-subsection-text {{
      font-size: 10pt;
      color: var(--slate-600);
      line-height: 1.6;
    }}
    .intel-list {{
      list-style: none;
      padding: 0;
    }}
    .intel-list li {{
      padding: 4px 0 4px 16px;
      position: relative;
      font-size: 10pt;
      color: var(--slate-600);
    }}
    .intel-list li::before {{
      content: '';
      position: absolute;
      left: 0;
      top: 12px;
      width: 6px;
      height: 6px;
      background: var(--teal);
      border-radius: 50%;
    }}
    .intel-category-badge {{
      display: inline-block;
      font-size: 7.5pt;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      background: var(--indigo-glow);
      color: var(--indigo);
      padding: 1px 8px;
      border-radius: 10px;
      margin-left: 6px;
      vertical-align: middle;
    }}

    /* ── News Table (fixes overlap) ── */
    .news-table {{
      table-layout: fixed;
    }}
    .news-date-cell {{
      width: 100px;
      font-size: 8.5pt;
      color: var(--slate-400);
      font-family: var(--font-mono);
      white-space: nowrap;
      vertical-align: top;
      padding-right: 8px;
    }}
    .news-headline {{
      font-weight: 600;
      color: var(--navy);
      font-size: 10pt;
      word-wrap: break-word;
    }}
    .news-implication {{
      font-size: 9pt;
      color: var(--slate-600);
      margin-top: 2px;
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

    /* ── Competitive Positioning ── */
    .positioning-statement {{
      font-size: 10.5pt;
      color: var(--navy);
      line-height: 1.7;
      padding: 14px 18px;
      background: linear-gradient(135deg, var(--indigo-glow), rgba(45, 212, 191, 0.06));
      border: 1px solid rgba(91, 110, 245, 0.2);
      border-radius: 8px;
      margin-bottom: 16px;
    }}
    .positioning-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 16px;
    }}
    .positioning-col {{
      background: var(--slate-50);
      border-radius: 8px;
      padding: 14px;
    }}
    .positioning-col-title {{
      font-size: 9pt;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--navy);
      margin-bottom: 8px;
    }}
    .positioning-list {{
      list-style: none;
      padding: 0;
      font-size: 9.5pt;
    }}
    .positioning-list li {{
      padding: 4px 0 4px 14px;
      position: relative;
      color: var(--slate-700);
    }}
    .positioning-list li::before {{
      content: '';
      position: absolute;
      left: 0;
      top: 10px;
      width: 6px;
      height: 6px;
      border-radius: 50%;
    }}
    .positioning-list--green li::before {{ background: var(--emerald); }}
    .positioning-list--amber li::before {{ background: var(--amber); }}
    .positioning-list--blue li::before {{ background: var(--indigo); }}

    /* ── Deal Strategy ── */
    .strategy-stage {{
      font-weight: 700;
      color: var(--indigo);
      white-space: nowrap;
    }}
    .strategy-talking-point {{
      font-style: italic;
      color: var(--slate-600);
    }}

    /* ── Pricing Box ── */
    .pricing-box {{
      background: var(--slate-50);
      border: 1px solid var(--slate-200);
      border-left: 4px solid var(--emerald);
      border-radius: 8px;
      padding: 16px 20px;
      font-size: 10pt;
      line-height: 1.7;
      color: var(--slate-700);
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
      .positioning-grid {{ break-inside: avoid; }}
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
<body>""")

    # ── PAGE 1: Executive Overview ──
    if show_page1:
        html_parts.append(f"""
  <div class="page">
    <div class="report-header">
      <div class="header-top">
        <div>
          <div class="header-logos">
            {KX_LOGO_SVG}
            {f'<span class="logo-divider">|</span>{client_logo_html}' if client_logo_html else ''}
          </div>
          <div class="header-brand">KX Competitive Intelligence</div>
          <div class="header-title">Battle Card: <span>KX/kdb+</span> vs {h(competitor)}</div>
        </div>
        <div class="header-badge">{badge_text}</div>
      </div>
      <div class="header-meta">
        <div class="meta-tag"><strong>Client:</strong> {h(client)}</div>
        <div class="meta-tag"><strong>Use Case:</strong> {h(use_case_label)}</div>
        <div class="meta-tag"><strong>Generated:</strong> {generated_time}</div>
      </div>
    </div>

    <div class="page-label">{page_label("executive")}</div>

    <div class="section">
      <div class="section-title">Why KX Wins</div>
      <div class="kx-wins">{h(report.why_kx_wins)}</div>
    </div>

    {client_intel_html}

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
      {"KX Confidential — For Internal Sales Use Only" if show_confidential else "KX — Client Document"} — {generated_time}
    </div>
  </div>""")

    # ── PAGE 2: Technical Evidence ──
    if show_page2:
        html_parts.append(f"""
  <div class="page">
    <div class="page-label">{page_label("technical")}</div>

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
            <th style="width:25%">Metric</th>
            <th style="width:25%">KX/kdb+</th>
            <th style="width:25%">{h(competitor)}</th>
            <th style="width:25%">Source</th>
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
      {"KX Confidential — For Internal Sales Use Only" if show_confidential else "KX — Client Document"} — {generated_time}
    </div>
  </div>""")

    # ── PAGE 3: Tactical Execution (Enhanced) ──
    if show_page3:
        html_parts.append(f"""
  <div class="page">
    <div class="page-label">{page_label("tactical")}</div>

    <div class="tactical-warning">
      <div class="tactical-warning-icon">&#128274;</div>
      <div class="tactical-warning-text">For Sales Rep's Eyes Only — Do Not Share With Client</div>
    </div>

    {positioning_html}

    <div class="section">
      <div class="section-title">"Trap" Questions to Expose Weaknesses</div>
      {trap_items if trap_items else '<div style="text-align:center;color:var(--slate-400);padding:20px">No trap questions generated</div>'}
    </div>

    <div class="section">
      <div class="section-title">Objection Handling Playbook</div>
      {objection_items if objection_items else '<div style="text-align:center;color:var(--slate-400);padding:20px">No objection handlers generated</div>'}
    </div>

    {deal_strategy_html}

    {pricing_html}

    <div class="section">
      <div class="section-title">Recent Competitor Activity (Last 90 Days)</div>
      {_build_news_section(news_table_rows)}
    </div>

    <div class="confidential-footer">
      KX Confidential — For Internal Sales Use Only — {generated_time}<br>
      Generated by KX Competitive Intelligence Platform — {len(report.agents_used)} agents deployed — {report.sources_count} sources analyzed — {report.generation_time_ms}ms
    </div>
  </div>""")

    # ── Chart.js ──
    if show_page2:
        html_parts.append(f"""
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <script>
    document.addEventListener('DOMContentLoaded', function() {{
      const labels = {json_safe(chart_labels)};
      const kxData = {json_safe(chart_kx)};
      const compData = {json_safe(chart_comp)};

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
  </script>""")

    html_parts.append("""
</body>
</html>""")

    return "\n".join(html_parts)


def _build_news_section(news_table_rows: str) -> str:
    """Build the competitor news section HTML."""
    if news_table_rows:
        return (
            '<table class="news-table">'
            '<thead><tr><th style="width:100px">Date</th><th>Activity &amp; Implication</th></tr></thead>'
            f'<tbody>{news_table_rows}</tbody>'
            '</table>'
        )
    return '<div style="text-align:center;color:var(--slate-400);padding:20px">No recent news found</div>'


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
    return json.dumps(data)
