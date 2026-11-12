"""
Rich terminal UI renderer for LetsVibe evaluation cards and tables.
"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from ..core.models import VibeReport


console = Console()


def render_terminal_report(report: VibeReport) -> None:
    """Renders the comprehensive Vibe Report to the terminal."""

    # 1. Header Banner
    grade_color = "green" if "A" in report.letter_grade else ("yellow" if "B" in report.letter_grade else "red")
    
    header_text = Text()
    header_text.append("✨ LetsVibe Quality & Health Audit ✨\n", style="bold cyan")
    header_text.append(f"Target: {report.repo_name}  |  Archetype: {report.archetype.category}  |  Source: {report.repo_source}\n", style="dim")
    header_text.append(f"\nFinal Vibe Score: {report.final_vibe_score} / 100   ", style="bold white")
    header_text.append(f"Grade: [{report.letter_grade}]", style=f"bold {grade_color} reverse")

    console.print(Panel(header_text, border_style="cyan", expand=False))

    # 2. Project Purpose & Dynamic Weights Justification
    arch_table = Table(show_header=False, box=None, padding=(0, 2))
    arch_table.add_column("Key", style="bold magenta")
    arch_table.add_column("Value", style="white")

    arch_table.add_row("Core Purpose:", report.archetype.core_purpose)
    arch_table.add_row("Target Audience:", report.archetype.target_audience)
    arch_table.add_row("Intended Scale:", report.archetype.intended_scale)
    arch_table.add_row("Weight Rationale:", f"[italic yellow]{report.weights.justification}[/]")
    _mal = report.factor_evaluations.get("malware_risk")
    _mal_score = _mal.score if _mal else report.malware_risk_score
    if _mal_score >= 85.0:
        arch_table.add_row("Malware Check:", "[green]No malicious behavior was found in the reviewed code.[/]")
    else:
        arch_table.add_row("Malware Check:", f"[bold red]⚠️ Possible malicious behavior flagged ({_mal_score}/100); heavily reduces the final score.[/]")

    console.print(Panel(arch_table, title="🎯 Project Archetype & Unbiased Dynamic Weights", border_style="magenta"))

    # 3. Evaluation Scorecard Table
    score_table = Table(title="📊 Evaluation Scorecard", expand=True, border_style="blue")
    score_table.add_column("Factor / Qualifier", style="bold cyan", width=22)
    score_table.add_column("Dynamic Weight", justify="right", style="yellow", width=14)
    score_table.add_column("Score", justify="right", style="bold", width=10)
    score_table.add_column("Contribution", justify="right", style="green", width=14)
    score_table.add_column("Summary Insights", style="white")

    factor_labels = {
        "malware_risk": "🛡️ Malware Check",
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
        label = factor_labels.get(fname, fname.replace('_', ' ').title())
        score_style = "green" if fe.score >= 80 else ("yellow" if fe.score >= 65 else "red")
        
        insight = fe.reasoning[:110] + "..." if len(fe.reasoning) > 110 else fe.reasoning
        score_table.add_row(
            label,
            f"{fe.weight}%",
            f"[{score_style}]{fe.score}[/]",
            f"+{fe.weighted_score}",
            insight,
        )

    console.print(score_table)

    # 4. Top 5 Most Solid Features vs Top 5 Needing Attention
    solid_items = report.top_solid_features if report.top_solid_features else report.top_strengths
    solid_text = "\n".join(f" [green]✔[/] {s}" for s in solid_items[:5]) if solid_items else "No standout features logged."

    needs_work_items = report.top_features_needing_work if report.top_features_needing_work else report.priority_improvements
    needs_work_text = "\n".join(f" [yellow]▲[/] {i}" for i in needs_work_items[:5]) if needs_work_items else "Codebase is in top shape."

    p_solid = Panel(solid_text, title="💎 Top 5 Most Solid Features (Least Work Needed)", border_style="green")
    p_needs_work = Panel(needs_work_text, title="🛠️ Top 5 Features Needing Attention (Most Work Needed)", border_style="yellow")

    console.print(Columns([p_solid, p_needs_work]))

    # 5. Deterministic Quick Stats Bar
    m = report.deterministic_metrics
    stats_text = Text()
    stats_text.append(f"Lines of Code: {m.total_loc:,}  |  ", style="dim")
    stats_text.append(f"Files: {m.total_files}  |  ", style="dim")
    stats_text.append(f"Avg Complexity: {m.avg_cyclomatic_complexity}  |  ", style="dim")
    stats_text.append(f"Secrets Found: {m.detected_secrets_count}  |  ", style="dim")
    stats_text.append(f"Risky Patterns: {m.risky_patterns_count}  |  ", style="dim")
    stats_text.append(f"Tests: {m.test_files_count} ({round(m.test_to_code_ratio*100, 1)}% ratio)  |  ", style="dim")
    stats_text.append(f"CI/CD: {'Yes' if m.has_ci_cd else 'No'}  |  Docker: {'Yes' if m.has_docker else 'No'}  |  ", style="dim")
    stats_text.append(f"Native Packaging (APK/Desktop): {'Yes' if m.has_desktop_packaging else 'No'}", style="dim")
    
    console.print(Panel(stats_text, title="⚙️ Static Telemetry", border_style="dim"))

    # 6. Timing Breakdown & Execution Health
    if report.timing_breakdown:
        timing_parts = [f"[bold]{k.replace('_', ' ').title()}:[/] {v}s" for k, v in report.timing_breakdown.items()]
        total_time = round(sum(report.timing_breakdown.values()), 2)
        timing_parts.append(f"[bold cyan]Total LLM Inference:[/] {total_time}s")
        console.print(Panel("  |  ".join(timing_parts), title="⏱️ Analysis Execution Timings", border_style="blue"))

    if report.issues_encountered:
        issues_text = "\n".join(f" [yellow]⚠[/] {iss}" for iss in report.issues_encountered)
        console.print(Panel(issues_text, title="⚠️ Teleynamic Warnings & Issues", border_style="yellow"))