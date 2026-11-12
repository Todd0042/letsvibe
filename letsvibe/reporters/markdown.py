"""
Markdown report generator for exportable repository audits.
"""

from pathlib import Path
from ..core.models import VibeReport


def generate_markdown_report(report: VibeReport, output_path: Path) -> Path:
    """Generates a comprehensive Markdown report file."""

    badge_color = "brightgreen" if "A" in report.letter_grade else ("yellow" if "B" in report.letter_grade else "red")
    badge_url = f"https://img.shields.io/badge/LetsVibe_Score-{report.final_vibe_score}%20({report.letter_grade})-{badge_color}?style=for-the-badge"

    md = [
        f"# LetsVibe Audit Report: {report.repo_name}",
        f"\n![LetsVibe Score]({badge_url})\n",
        f"- **Audited On:** {report.timestamp}",
        f"- **Repository:** `{report.repo_source}`",
        f"- **Archetype:** **{report.archetype.category}** ({report.archetype.intended_scale})",
        f"- **Primary Language:** {report.archetype.primary_language}",
        f"- **Core Purpose:** {report.archetype.core_purpose}",
        f"- **Target Audience:** {report.archetype.target_audience}",
        "\n---",
        "\n## 🎯 Dynamic Weight Allocation Rationale",
        f"> {report.weights.justification}",
        "\n---",
        "\n## 📊 Evaluation Scorecard",
        "| Factor / Qualifier | Dynamic Weight | Score | Contribution | Key Observations |",
        "| :--- | :---: | :---: | :---: | :--- |",
    ]

    factor_names = {
        "functionality": "⚙️ Functionality",
        "user_journey": "🧭 User Journey",
        "ui_ux": "✨ UI & UX",
        "resonance": "🚀 Real-World Value",
        "creativity": "🎨 Creativity",
        "code_health": "🧠 Code Health",
        "operational_safety": "🔒 Operational Safety",
        "maintainability": "🧠 Maintainability (v5)",
        "architecture": "🏛️ Architecture (v5)",
    }

    for fname, fe in report.factor_evaluations.items():
        label = factor_names.get(fname, fname.replace('_', ' ').title())
        md.append(f"| {label} | `{fe.weight}%` | **{fe.score}/100** | +{fe.weighted_score} | {fe.reasoning} |")

    md.extend([
        "\n---",
        "\n## 💎 Top 5 Most Solid Features (Least Work Needed)",
    ])
    solid_items = report.top_solid_features if report.top_solid_features else report.top_strengths
    for s in solid_items[:5]:
        md.append(f"* ✅ **{s}**")

    md.extend([
        "\n## 🛠️ Top 5 Features Needing Attention (Most Work Needed)",
    ])
    needs_work_items = report.top_features_needing_work if report.top_features_needing_work else report.priority_improvements
    for imp in needs_work_items[:5]:
        md.append(f"* ⚠️ **{imp}**")

    md.extend([
        "\n---",
        "\n## ⚙️ Static Telemetry Data",
        f"- **Total Lines of Code:** {report.deterministic_metrics.total_loc:,}",
        f"- **Total Files Analyzed:** {report.deterministic_metrics.total_files}",
        f"- **Average Cyclomatic Complexity:** {report.deterministic_metrics.avg_cyclomatic_complexity}",
        f"- **Peak Cyclomatic Complexity:** {report.deterministic_metrics.max_cyclomatic_complexity}",
        f"- **Hardcoded Secrets Flagged:** {report.deterministic_metrics.detected_secrets_count}",
        f"- **Risky Code Patterns Flagged:** {report.deterministic_metrics.risky_patterns_count}",
        f"- **Test Files:** {report.deterministic_metrics.test_files_count} (Test-to-code ratio: {round(report.deterministic_metrics.test_to_code_ratio*100, 1)}%)",
        f"- **CI/CD Infrastructure:** {'Configured' if report.deterministic_metrics.has_ci_cd else 'None detected'}",
        f"- **Containerization (Docker):** {'Present' if report.deterministic_metrics.has_docker else 'None detected'}",
        f"- **Native Standalone Packaging:** {'Present (APK/AppImage/Installer/Scripts)' if report.deterministic_metrics.has_desktop_packaging else 'None detected'}",
        f"- **Open Source License:** {'Present' if report.deterministic_metrics.has_license else 'Missing'}",
        "\n---",
        "*Report generated 100% locally and offline by [LetsVibe](https://github.com/todd/letsvibe).*",
    ])

    content = "\n".join(md)
    output_path.write_text(content, encoding="utf-8")
    return output_path

