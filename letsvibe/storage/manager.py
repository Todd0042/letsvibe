"""
Unified report persistence manager.
Ensures every evaluation is systematically saved to structured disk storage
(data/reports/{owner}/{repo_name}/) and registered in the database.
"""

from pathlib import Path
from typing import Optional, Tuple
from ..core.models import VibeReport
from ..reporters.html import generate_html_report
from ..reporters.markdown import generate_markdown_report
from .identity import RepoIdentity, parse_repo_identity
from .db import save_report_to_db, DEFAULT_DB_PATH


class ReportStorageManager:
    """Handles structured saving of reports into database and filesystem."""

    @staticmethod
    def persist_audit(
        report: VibeReport,
        target: str,
        custom_html: Optional[Path] = None,
        custom_markdown: Optional[Path] = None,
        custom_json: Optional[Path] = None,
        source_path: Optional[Path] = None,
        file_list: Optional[list] = None,
        db_path: Path = DEFAULT_DB_PATH,
        commit_sha: Optional[str] = None,
        repo_icon_url: Optional[str] = None,
        is_test: bool = False,
    ) -> Tuple[RepoIdentity, Path, Path, Path, int]:
        """
        Persists report to structured storage:
        - data/reports/{owner}/{repo_name}/vibe_report.json
        - data/reports/{owner}/{repo_name}/vibe_report.html
        - data/reports/{owner}/{repo_name}/VIBE_REPORT.md
        Also writes to custom paths if supplied.
        Registers report in SQLite DB and recalculates rankings.

        Returns (identity, json_path, html_path, md_path, report_id).
        """
        identity = parse_repo_identity(target)
        # Fallback owner from report if target was generic local
        if identity.owner == "local" and report.repo_name:
            identity.repo_name = report.repo_name
            identity.full_slug = f"local/{report.repo_name}"

        # Project icon: GitHub's public link for public repos; a saved copy (in the report folder) for private ones
        from ..core.icons import resolve_icon_url
        if repo_icon_url:
            identity.repo_icon_url = repo_icon_url
        if not identity.repo_icon_url and identity.provider == "github":
            check_dir = source_path or (Path(target) if Path(target).is_dir() else None)
            if check_dir and Path(check_dir).exists():
                try:
                    identity.repo_icon_url = resolve_icon_url(identity.owner, identity.repo_name, check_dir, identity.get_report_dir())
                except Exception:
                    identity.repo_icon_url = None

        report_dir = identity.get_report_dir()

        # Canonical paths
        json_path = identity.get_json_path()
        html_path = identity.get_html_path()
        md_path = identity.get_markdown_path()

        # Serialize JSON (never with internal safety-check data)
        from .db import strip_safety_details
        import json as _json
        clean_dict = strip_safety_details(report.model_dump(mode="json"))
        report = VibeReport.model_validate(clean_dict)  # every file below is generated from the clean copy
        json_content = _json.dumps(clean_dict, indent=2)
        json_path.write_text(json_content, encoding="utf-8")
        if custom_json and custom_json != json_path:
            custom_json.parent.mkdir(parents=True, exist_ok=True)
            custom_json.write_text(json_content, encoding="utf-8")

        # Generate HTML report
        generate_html_report(report, html_path)
        if custom_html and custom_html != html_path:
            custom_html.parent.mkdir(parents=True, exist_ok=True)
            generate_html_report(report, custom_html)

        # Generate Markdown report
        generate_markdown_report(report, md_path)
        if custom_markdown and custom_markdown != md_path:
            custom_markdown.parent.mkdir(parents=True, exist_ok=True)
            generate_markdown_report(report, custom_markdown)

        # Save to database
        check_git_dir = source_path or (Path(target) if Path(target).is_dir() else None)
        if not commit_sha and check_git_dir and (check_git_dir / ".git").exists():
            import subprocess
            try:
                res = subprocess.run(
                    ["git", "-C", str(check_git_dir), "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                if res.returncode == 0 and res.stdout.strip():
                    commit_sha = res.stdout.strip()
            except Exception:
                pass

        report_dict = report.model_dump()
        report_id = save_report_to_db(
            report_dict=report_dict,
            identity=identity,
            raw_json_path=str(json_path),
            html_report_path=str(html_path),
            markdown_report_path=str(md_path),
            commit_sha=commit_sha,
            db_path=db_path,
            is_test=is_test,
        )

        return identity, json_path, html_path, md_path, report_id
