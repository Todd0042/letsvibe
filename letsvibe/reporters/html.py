"""
HTML dashboard generator for LetsVibe featuring interactive Chart.js radar charts.
"""

import json
from pathlib import Path
from ..core.models import VibeReport


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LetsVibe Audit: {{ report.repo_name }}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg: #0d1117;
            --surface: #161b22;
            --border: #30363d;
            --text: #c9d1d9;
            --text-heading: #f0f6fc;
            --accent: #58a6ff;
            --green: #3fb950;
            --yellow: #d29922;
            --red: #f85149;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text);
            margin: 0;
            padding: 30px;
        }
        .container {
            max-width: 1100px;
            margin: 0 auto;
        }
        .card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .grade-badge {
            font-size: 2.5rem;
            font-weight: 800;
            padding: 10px 24px;
            border-radius: 12px;
            background: rgba(88, 166, 255, 0.15);
            border: 2px solid var(--accent);
            color: var(--text-heading);
            text-align: center;
        }
        .score-val {
            font-size: 1.1rem;
            color: var(--accent);
            display: block;
        }
        .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
        }
        @media (max-width: 800px) {
            .grid { grid-template-columns: 1fr; }
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
        }
        th, td {
            text-align: left;
            padding: 12px;
            border-bottom: 1px solid var(--border);
        }
        th {
            color: var(--text-heading);
            background: rgba(255,255,255,0.03);
        }
        .stat-pill {
            display: inline-block;
            background: rgba(255,255,255,0.05);
            padding: 6px 12px;
            border-radius: 20px;
            margin: 4px;
            font-size: 0.85rem;
        }
        ul { padding-left: 20px; }
        li { margin-bottom: 8px; }
    </style>
</head>
<body>
    <div class="container">
        <!-- Header Card -->
        <div class="card header">
            <div>
                <h1 style="margin: 0; color: var(--text-heading);">✨ LetsVibe: {{ report.repo_name }}</h1>
                <p style="margin: 6px 0 0; color: #8b949e;">
                    Archetype: <strong>{{ report.archetype.category }}</strong> | Target: <code>{{ report.repo_source }}</code>
                </p>
                <p style="margin: 6px 0 0; font-size: 0.9rem;">
                    <em>"{{ report.archetype.core_purpose }}"</em>
                </p>
            </div>
            <div class="grade-badge">
                {{ report.letter_grade }}
                <span class="score-val">{{ report.final_vibe_score }} / 100</span>
            </div>
        </div>

        <!-- Radar & Overview Grid -->
        <div class="grid">
            <div class="card">
                <h2 style="color: var(--text-heading); margin-top: 0;">🎯 Multi-Factor Radar</h2>
                <div style="height: 320px; display: flex; justify-content: center;">
                    <canvas id="radarChart"></canvas>
                </div>
            </div>
            <div class="card">
                <h2 style="color: var(--text-heading); margin-top: 0;">⚖️ Dynamic Weight Rationale</h2>
                <p style="line-height: 1.6; color: #e6edf3;">
                    {{ report.weights.justification }}
                </p>
                <h3 style="color: var(--text-heading); margin-top: 20px;">⚙️ Static Telemetry</h3>
                <div>
                    <span class="stat-pill">LOC: {{ report.deterministic_metrics.total_loc }}</span>
                    <span class="stat-pill">Files: {{ report.deterministic_metrics.total_files }}</span>
                    <span class="stat-pill">Avg Complexity: {{ report.deterministic_metrics.avg_cyclomatic_complexity }}</span>
                    <span class="stat-pill">Secrets: {{ report.deterministic_metrics.detected_secrets_count }}</span>
                    <span class="stat-pill">Risky patterns: {{ report.deterministic_metrics.risky_patterns_count }}</span>
                    <span class="stat-pill">CI/CD: {{ 'Yes' if report.deterministic_metrics.has_ci_cd else 'No' }}</span>
                    <span class="stat-pill">Docker: {{ 'Yes' if report.deterministic_metrics.has_docker else 'No' }}</span>
                    <span class="stat-pill">Native Packaging: {{ 'Yes' if report.deterministic_metrics.has_desktop_packaging else 'No' }}</span>
                </div>
            </div>
        </div>

        <!-- Detailed Scorecard -->
        <div class="card">
            <h2 style="color: var(--text-heading); margin-top: 0;">📊 Evaluation Scorecard</h2>
            <table>
                <thead>
                    <tr>
                        <th>Factor / Qualifier</th>
                        <th>Dynamic Weight</th>
                        <th>Score</th>
                        <th>Contribution</th>
                        <th>Observations</th>
                    </tr>
                </thead>
                <tbody>
                    {% for fname, fe in report.factor_evaluations.items() %}
                    <tr>
                        <td><strong>{{ fname.replace('_', ' ').title() }}</strong></td>
                        <td>{{ fe.weight }}%</td>
                        <td><strong>{{ fe.score }}/100</strong></td>
                        <td style="color: var(--green);">+{{ fe.weighted_score }}</td>
                        <td>{{ fe.reasoning }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>

        <!-- Standout Features vs Needs Work -->
        <div class="grid">
            <div class="card">
                <h3 style="color: var(--green); margin-top: 0;">💎 Top 5 Most Solid Features (Least Work Needed)</h3>
                <ul>
                    {% for s in (report.top_solid_features if report.top_solid_features else report.top_strengths)[:5] %}
                    <li><strong>{{ s }}</strong></li>
                    {% endfor %}
                </ul>
            </div>
            <div class="card">
                <h3 style="color: var(--yellow); margin-top: 0;">🛠️ Top 5 Features Needing Attention (Most Work Needed)</h3>
                <ul>
                    {% for imp in (report.top_features_needing_work if report.top_features_needing_work else report.priority_improvements)[:5] %}
                    <li><strong>{{ imp }}</strong></li>
                    {% endfor %}
                </ul>
            </div>
        </div>
    </div>

    <script>
        const ctx = document.getElementById('radarChart').getContext('2d');
        new Chart(ctx, {
            type: 'radar',
            data: {
                labels: {{ chart_labels | safe }},
                datasets: [{
                    label: 'Score (0-100)',
                    data: {{ chart_scores | safe }},
                    backgroundColor: 'rgba(88, 166, 255, 0.25)',
                    borderColor: '#58a6ff',
                    pointBackgroundColor: '#58a6ff',
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                scales: {
                    r: {
                        angleLines: { color: '#30363d' },
                        grid: { color: '#30363d' },
                        pointLabels: { color: '#c9d1d9', font: { size: 11 } },
                        suggestedMin: 0,
                        suggestedMax: 100,
                        ticks: { display: false }
                    }
                },
                plugins: {
                    legend: { display: false }
                }
            }
        });
    </script>
</body>
</html>
"""


def generate_html_report(report: VibeReport, output_path: Path) -> Path:
    """Renders interactive HTML report with Chart.js."""
    from jinja2 import Template

    radar_items = [(n, fe) for n, fe in report.factor_evaluations.items() if n != "malware_risk"]
    labels = [n.replace("_", " ").title() for n, _ in radar_items]
    scores = [fe.score for _, fe in radar_items]

    template = Template(HTML_TEMPLATE)
    html_content = template.render(
        report=report,
        chart_labels=json.dumps(labels),
        chart_scores=json.dumps(scores),
    )

    output_path.write_text(html_content, encoding="utf-8")
    return output_path