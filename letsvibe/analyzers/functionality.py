"""
Functionality, infrastructure, and test presence analyzer.
"""

from pathlib import Path
from typing import Dict, List, Tuple


class FunctionalityAnalyzer:
    """Analyzes test coverage density, CI/CD, documentation, and infrastructure presence."""

    @classmethod
    def evaluate(cls, files: Dict[str, str], total_loc: int) -> Tuple[int, float, bool, bool, bool, bool, bool, str]:
        """
        Returns:
            - test_files_count: int
            - test_to_code_ratio: float
            - has_ci_cd: bool
            - has_docker: bool
            - has_desktop_packaging: bool
            - has_license: bool
            - has_readme: bool
            - readme_excerpt: str
        """
        test_files = 0
        test_loc = 0
        has_ci_cd = False
        has_docker = False
        has_desktop_packaging = False
        has_license = False
        has_readme = False
        readme_excerpt = ""

        for rel_path, content in files.items():
            lower = rel_path.lower()

            # Test files

            # Test files
            if (
                "test" in lower
                or "spec" in lower
                or lower.endswith("_test.py")
                or lower.startswith("test_")
                or "/tests/" in lower
                or "/__tests__/" in lower
            ):
                test_files += 1
                test_loc += content.count("\n") + 1

            # CI/CD
            if ".github/workflows" in lower or ".gitlab-ci.yml" in lower or "jenkinsfile" in lower:
                has_ci_cd = True

            # Docker / Containers
            if "dockerfile" in lower or "docker-compose" in lower or "containerfile" in lower:
                has_docker = True

            # Desktop, Mobile & Native Packaging Distribution
            if (
                lower.endswith(".iss")
                or "appimage" in lower
                or lower.endswith(".spec")
                or lower.endswith(".desktop")
                or "pyinstaller" in lower
                or lower in ("setup.py", "start.sh", "start.bat", "run.sh", "install.sh")
                # Mobile / Android / iOS Packaging
                or lower.endswith(".apk")
                or lower.endswith(".aab")
                or lower.endswith("androidmanifest.xml")
                or "assembledebug" in content.lower()
                or "assemblerelease" in content.lower()
                or "com.android.application" in content.lower()
                or "android.application" in content.lower()
                or lower.endswith(".ipa")
                or lower.endswith(".podspec")
                or "xcodeproj" in lower
                # Linux & Windows Distribution
                or lower.endswith(".deb")
                or lower.endswith(".rpm")
                or lower.endswith(".dmg")
                or lower.endswith(".pkg")
                or lower.endswith(".msi")
            ):
                has_desktop_packaging = True

            # License
            if "license" in lower or "copying" in lower:
                has_license = True

            # Readme
            is_root_readme = lower in ("readme.md", "readme.txt", "readme.rst", "readme")
            is_any_readme = lower.endswith(("/readme.md", "/readme.txt", "/readme.rst", "/readme"))
            if (is_root_readme or is_any_readme) and (not readme_excerpt or is_root_readme):
                has_readme = True
                readme_excerpt = content[:3000].strip()

        test_ratio = round((test_loc / total_loc), 3) if total_loc > 0 else 0.0

        return (
            test_files,
            test_ratio,
            has_ci_cd,
            has_docker,
            has_desktop_packaging,
            has_license,
            has_readme,
            readme_excerpt,
        )

    @classmethod
    def evaluate_cli_ux(cls, files: Dict[str, str]) -> Tuple[bool, bool, bool, List[str]]:
        """
        Detects CLI frameworks, interactive TUI menus, and rich terminal styling.
        Returns:
            - has_cli_framework: bool
            - has_tui_interactive: bool
            - has_rich_terminal_styling: bool
            - cli_ux_details: List[str]
        """
        has_cli_framework = False
        has_tui_interactive = False
        has_rich_terminal_styling = False

        cli_frameworks = set()
        tui_frameworks = set()
        styling_libs = set()
        details = []

        for rel_path, content in files.items():
            content_lower = content.lower()

            for fw in ("click", "typer", "argparse", "clap", "cobra", "picocli", "commander"):
                if f"import {fw}" in content_lower or f"from {fw}" in content_lower or f'"{fw}"' in content_lower:
                    has_cli_framework = True
                    cli_frameworks.add(fw)

            for tui in ("textual", "curses", "bubbletea", "prompt_toolkit", "blessed", "inquirer"):
                if f"import {tui}" in content_lower or f"from {tui}" in content_lower or f'"{tui}"' in content_lower:
                    has_tui_interactive = True
                    tui_frameworks.add(tui)

            for style in ("rich", "colorama", "chalk", "tabulate", "tqdm", "alive_progress"):
                if f"import {style}" in content_lower or f"from {style}" in content_lower or f'"{style}"' in content_lower:
                    has_rich_terminal_styling = True
                    styling_libs.add(style)

        if cli_frameworks:
            details.append(f"Structured CLI frameworks: {', '.join(sorted(cli_frameworks))}")
        if tui_frameworks:
            details.append(f"Interactive TUI / DOS-style menus: {', '.join(sorted(tui_frameworks))}")
        if styling_libs:
            details.append(f"Terminal styling & progress: {', '.join(sorted(styling_libs))}")

        return has_cli_framework, has_tui_interactive, has_rich_terminal_styling, details
