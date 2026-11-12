"""
Main CLI entrypoint for LetsVibe.
"""

import sys
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console

from .core.ingest import RepositoryIngestor
from .evaluator.classifier import classify_project
from .evaluator.pipeline import CHUNK_TOKENS, gather_metrics
from .evaluator.triage import select_for_review
from .evaluator.consensus import ConsensusEngine
from .evaluator.ollama import OllamaClient, get_free_vram_mb
from .reporters.console import render_terminal_report
from .reporters.html import generate_html_report
from .reporters.markdown import generate_markdown_report


app = typer.Typer(
    name="letsvibe",
    help="✨ 100% Offline, Dual-Model Repository Vibe & Health Analyzer",
    add_completion=False,
)
console = Console()


@app.command()
def analyze(
    target: str = typer.Argument(
        ...,
        help="Local repository path or remote GitHub URL (e.g. https://github.com/org/repo)",
    ),
    mode: str = typer.Option(
        "blend",
        "--mode",
        "-m",
        help="Evaluation mode: 'blend' (7B Micro + 14B Macro), 'fast' (7B only), or 'deep' (14B only)",
    ),
    micro_model: str = typer.Option(
        "qwen2.5-coder:7b",
        "--micro-model",
        help="Ollama model for Pass 1 (Micro-analyst)",
    ),
    macro_model: Optional[str] = typer.Option(
        None,
        "--macro-model",
        help="Ollama model for Pass 2 (Macro-architect). Auto-detects best GPU fit if omitted.",
    ),
    html: Optional[Path] = typer.Option(
        None,
        "--html",
        help="Path to export standalone HTML report with interactive radar chart",
    ),
    markdown: Optional[Path] = typer.Option(
        None,
        "--markdown",
        "--md",
        help="Path to export Markdown report (e.g. VIBE_REPORT.md)",
    ),
    json_output: Optional[Path] = typer.Option(
        None,
        "--json",
        help="Path to export raw JSON report",
    ),
    context_tokens: int = typer.Option(
        CHUNK_TOKENS,
        "--context-tokens",
        "-c",
        help="Maximum context window size per module batch for whole-repo ingestion",
    ),
    distill: bool = typer.Option(
        True,
        "--distill/--no-distill",
        help="Condense function and data bodies for 60-75% token reduction while retaining full architecture",
    ),
):
    """
    Ingests and evaluates a whole repository across 7 dimensions using 100% local LLMs.
    """
    console.rule("[bold cyan]LetsVibe: 100% Local Repository Evaluator[/bold cyan]")

    client = OllamaClient()

    with console.status("[bold yellow]Verifying local Ollama service...[/bold yellow]"):
        if not client.is_running():
            console.print("[dim]Ollama not responding on port 11434. Attempting to start daemon...[/dim]")
            if not client.ensure_server_running():
                console.print(
                    "[bold red]Error:[/] Could not connect to or start Ollama. "
                    "Please ensure Ollama is installed and running (`ollama serve`)."
                )
                raise typer.Exit(code=1)

    # Resolve macro-model (defaults to 14b for macro-analysis)
    explicit_macro = macro_model is not None
    if macro_model is None:
        macro_model = "qwen2.5-coder:14b"

    # 1. Ingestion
    with console.status(f"[bold green]Ingesting repository: {target}...[/bold green]"):
        try:
            packed_repo = RepositoryIngestor.ingest(target)
        except Exception as e:
            console.print(f"[bold red]Ingestion Error:[/] {e}")
            raise typer.Exit(code=1)

    try:
        console.print(
            f"[dim]Ingested [bold]{len(packed_repo.files)}[/bold] source files "
            f"([bold]{packed_repo.total_loc:,}[/bold] LOC, ~{packed_repo.estimated_tokens:,} tokens)[/dim]"
        )

        import time
        t_static_start = time.perf_counter()
        # 2. Deterministic Static Collectors
        with console.status("[bold blue]Running deterministic static code collectors...[/bold blue]"):
            # Same recipe as the website and the curve calibration (evaluator/pipeline.py)
            metrics, platform_info = gather_metrics(packed_repo)
            readme_excerpt = metrics.readme_excerpt
        static_elapsed = round(time.perf_counter() - t_static_start, 2)

        # 3. Whole-Repo Manifest Batches (100% file coverage without omissions)
        keep, _coverage, _roles = select_for_review(packed_repo.files)  # name-based file filter (no AI)
        manifest_batches = packed_repo.get_coverage_manifests(
            max_batch_tokens=context_tokens,
            distill=distill,
            only=keep,
            local_tree=True,
        )
        distill_tag = " (distilled)" if distill else ""
        console.print(f"[dim]Prepared [bold]{len(manifest_batches)}[/bold] whole-codebase module batches for evaluation{distill_tag}[/dim]")

        # 4. LLM Consensus Engine
        engine = ConsensusEngine(
            client=client,
            micro_model=micro_model,
            macro_model=macro_model,
            explicit_macro=explicit_macro,
        )

        with console.status(
            f"[bold magenta]Executing {mode.upper()} mode local evaluation pipeline...[/bold magenta]"
        ):
            classification = classify_project(
                client, packed_repo.source_path, platform_info,
                readme=readme_excerpt, file_tree=packed_repo.file_tree,
            )
            report = engine.evaluate_pipeline(
                repo_name=packed_repo.repo_name,
                repo_source=target,
                manifest_batches=manifest_batches,
                metrics=metrics,
                mode=mode,
                classification=classification,
                file_tree=packed_repo.file_tree,
            )
        report.timing_breakdown["deterministic_static_scan_sec"] = static_elapsed

        # 5. Output rendering
        console.print()
        render_terminal_report(report)

        # 6. Automatic Structured Storage & Leaderboard Indexing
        from .storage.manager import ReportStorageManager
        identity, json_p, html_p, md_p, report_id = ReportStorageManager.persist_audit(
            report=report,
            target=target,
            custom_html=html,
            custom_markdown=markdown,
            custom_json=json_output,
            source_path=packed_repo.source_path,
            file_list=list(packed_repo.files.keys()),
        )

        console.print(f"\n[bold green]✔ Saved to structured repository vault:[/] [cyan]{identity.get_report_dir()}[/cyan]")
        console.print(f"[bold green]✔ Report indexed in leaderboard database[/] (ID: [bold]#{report_id}[/bold])")
        if html:
            console.print(f"[green]✔ Interactive HTML report saved to:[/] [bold]{html}[/bold]")
        if markdown:
            console.print(f"[green]✔ Markdown report saved to:[/] [bold]{markdown}[/bold]")
        if json_output:
            console.print(f"[green]✔ JSON data saved to:[/] [bold]{json_output}[/bold]")

    finally:
        # Automatic temporary clone cleanup
        if packed_repo and packed_repo.is_temporary:
            packed_repo.cleanup()
        # Automatic GPU memory release
        try:
            client.unload_all_models()
        except Exception:
            pass


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host address to bind to"),
    port: int = typer.Option(8000, "--port", "-p", help="Port number to serve on"),
    browser: bool = typer.Option(True, "--browser/--no-browser", help="Automatically launch browser"),
):
    """Launches the interactive Web UI Leaderboard, Podium, and Admin Console."""
    import uvicorn
    import webbrowser

    client = OllamaClient()
    if not client.is_running():
        console.print("[dim]Checking Ollama daemon...[/dim]")
        client.ensure_server_running()

    console.rule("[bold cyan]LetsVibe Web Hub & Leaderboard[/bold cyan]")
    console.print(f"🏆 User Leaderboard: [bold green]http://{host}:{port}[/bold green]")
    console.print(f"⚙️ Admin Console:  [bold cyan]http://{host}:{port}/admin[/bold cyan]")
    console.print("[dim]Press Ctrl+C to stop the server.[/dim]\n")

    if browser:
        try:
            webbrowser.open(f"http://{host}:{port}")
        except Exception:
            pass

    uvicorn.run("letsvibe.web.server:app", host=host, port=port, log_level="info")


@app.command()
def models():
    """Lists locally available Ollama models."""
    client = OllamaClient()
    if not client.is_running():
        client.ensure_server_running()

    models_list = client.list_models()
    if not models_list:
        console.print("[yellow]No Ollama models found locally or service is offline.[/yellow]")
        return

    console.print("[bold cyan]Locally Available Ollama Models:[/bold cyan]")
    for m in models_list:
        console.print(f" • [green]{m}[/green]")


def main():
    # Smart CLI routing:
    # If the user runs `letsvibe <repo-address>` without 'analyze', route to 'analyze' automatically
    if len(sys.argv) > 1:
        first_arg = sys.argv[1]
        known_cmds = {"analyze", "web", "models", "--help", "-h"}
        if first_arg not in known_cmds and not first_arg.startswith("-"):
            # Insert 'analyze' as default command
            sys.argv.insert(1, "analyze")
    elif len(sys.argv) == 1:
        # If no arguments provided, launch the web server directly!
        sys.argv.append("web")

    app()


if __name__ == "__main__":
    main()
