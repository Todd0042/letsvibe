"""
Repository ingestion and whole-codebase packing for LetsVibe.
Handles local paths and remote GitHub URLs via shallow cloning and cleanup.
"""

import os
import re
import shutil
import subprocess  # nosec B404 - git/nvidia-smi run with fixed list arguments, never a shell
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


IGNORE_DIRS: Set[str] = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
    ".idea",
    ".vscode",
    ".cache",
    ".next",
    ".nuxt",
    ".cargo",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".pio",
    "managed_components",
}

ALLOWED_DOT_DIRS: Set[str] = {
    ".github",
    ".agy",
}

BINARY_EXTENSIONS: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".mp3", ".wav", ".ogg", ".mp4", ".mov", ".avi", ".mkv",
    ".cdg", ".db", ".sqlite", ".sqlite3", ".AppImage",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar", ".iso", ".apk", ".aar", ".jar",
    ".exe", ".bin", ".dll", ".so", ".dylib", ".o", ".a",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pyc", ".pyo", ".pyd", ".class",
    ".pt", ".pth", ".onnx", ".parquet", ".arrow", ".h5", ".hdf5",
    ".safetensors", ".pkl", ".pickle", ".dmg", ".pkg", ".deb", ".rpm", ".msi",
    ".bz2", ".xz", ".tgz",
}

# Media assets: their CONTENTS are never read or sent to a model, but their NAMES are kept so reviewers
# can see that a project ships sprites, sound effects, screenshots, fonts or 3D models.
MEDIA_KINDS: Dict[str, str] = {
    **{e: "image" for e in (".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".bmp", ".tga", ".psd", ".aseprite", ".xcf")},
    **{e: "audio" for e in (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".mid", ".midi")},
    **{e: "video" for e in (".mp4", ".mov", ".avi", ".mkv", ".webm", ".cdg")},
    **{e: "font" for e in (".woff", ".woff2", ".ttf", ".eot", ".otf")},
    **{e: "3d model" for e in (".glb", ".gltf", ".fbx", ".obj", ".blend", ".dae")},
}
MEDIA_ICONS = {"image": "🖼", "audio": "🔊", "video": "🎬", "font": "🔤", "3d model": "🧊"}
MAX_MEDIA_ENTRIES = 5000
MEDIA_NAMES_PER_FOLDER = 4  # larger asset folders show a few names plus a count, so they don't crowd out code
BINARY_EXTENSIONS.update(MEDIA_KINDS.keys())

# Community Audit Hard Limits (Guards against storage & RAM exhaustion)
MAX_REPO_BYTES: int = 50 * 1024 * 1024  # 50 MB
MAX_REPO_FILES: int = 2000

# Bloat files that inflate token counts without providing architectural meaning
BLOAT_FILENAMES: Set[str] = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "cargo.lock",
    "poetry.lock",
    "composer.lock",
    "gemfile.lock",
    "pipfile.lock",
    "gradle.lockfile",
    "bun.lock",
    "bun.lockb",
}

# Data, config, and documentation extensions tracked separately from source code LOC
DATA_AND_DOC_EXTENSIONS: Set[str] = {
    ".json", ".jsonl", ".ndjson", ".csv", ".tsv",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml",
    ".md", ".markdown", ".txt", ".rst", ".lock",
}

VENDOR_PATH_PATTERNS: Tuple[str, ...] = (
    "vendor/",
    "third_party/",
    "third-party/",
    "extern/",
    "external/",
    "sdk/",
    "libs/",
    "lib/",
    "components/",
    "managed_components/",
    "submodules/",
    "deps/",
)

# Package manifests that mark a subdirectory as a self-contained third-party library
# (Arduino / PlatformIO / ESP-IDF). Vendored copies of these were being audited as the
# developer's own code (e.g. GFX_Library_for_Arduino in lib/).
LIBRARY_MANIFEST_NAMES = {"library.properties", "library.json", "idf_component.yml"}
# A manifest-bearing library dir touched in more commits than this is treated as the developer's own.
VENDORED_LIBRARY_MAX_COMMITS = 3

# Max chars of the directory tree repeated in every batch header (it is not counted in the batch budget)
BATCH_TREE_MAX_CHARS = 3000

# Regex to detect heavy hex/numeric array rows (e.g., C/C++ PROGMEM bitmaps, LVGL fonts, lookup tables)
HEX_OR_NUM_LINE_RE = re.compile(r"^\s*(?:0x[0-9a-fA-F]+|\d+)(?:\s*,\s*(?:0x[0-9a-fA-F]+|\d+)){3,}\s*,?\s*(?://.*|/\*.*\*/)?$")


def strip_heavy_arrays(content: str, max_array_lines: int = 12) -> str:
    """
    Pre-scan helper that immediately collapses massive C/C++/Python/JS byte arrays,
    PROGMEM bitmaps, font glyph tables, and numeric data dumps before AST/LLM processing.
    """
    lines = content.splitlines(keepends=True)
    if len(lines) <= max_array_lines:
        return content

    out: List[str] = []
    data_run: List[str] = []

    def flush_run() -> None:
        nonlocal data_run
        if len(data_run) > max_array_lines:
            out.extend(data_run[:3])
            out.append(
                f"    /* ... [{len(data_run) - 5} lines of heavy raw array/bitmap data skipped by pre-scan] ... */\n"
            )
            out.extend(data_run[-2:])
        else:
            out.extend(data_run)
        data_run = []

    for line in lines:
        stripped = line.strip()
        if HEX_OR_NUM_LINE_RE.match(stripped):
            data_run.append(line)
        else:
            if data_run:
                flush_run()
            out.append(line)

    if data_run:
        flush_run()

    return "".join(out)


def get_untouched_vendor_files(repo_path: Path, tracked_files: List[str]) -> Set[str]:
    """
    Uses git log in a single fast pass to identify files inside vendor/SDK/lib folders
    that were dumped in a bulk upstream import and never subsequently modified by the developer.
    Any library file that was patched/edited in a focused commit (<= 35 files) is KEPT.
    """
    candidate_files = [
        f for f in tracked_files
        if any(f.lower().startswith(p) or f"/{p}" in f.lower() for p in VENDOR_PATH_PATTERNS)
    ]
    if not candidate_files or not (repo_path / ".git").exists():
        return set()

    candidate_set = set(candidate_files)

    try:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        res = subprocess.run(  # nosec B603 B607 - fixed command, list arguments, no shell
            ["git", "log", "--name-only", "--pretty=format:COMMIT:%H"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
            env=env,
        )

        commits: List[List[str]] = []
        current_commit_files: List[str] = []
        for raw_line in res.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("COMMIT:"):
                if current_commit_files:
                    commits.append(current_commit_files)
                current_commit_files = []
            else:
                current_commit_files.append(line)
        if current_commit_files:
            commits.append(current_commit_files)

        if not commits:
            return set()

        # Track how many times each candidate file was touched, and whether it was ever
        # modified in a focused developer commit (as opposed to a massive bulk SDK drop).
        touch_counts: Dict[str, int] = {f: 0 for f in candidate_set}
        developer_patched: Set[str] = set()

        # commits are ordered newest -> oldest; reverse to walk oldest (initial import) -> newest
        for commit_files in reversed(commits):
            is_bulk_import = len(commit_files) > 35
            for f in commit_files:
                if f in candidate_set:
                    touch_counts[f] += 1
                    # If modified after its first appearance, OR touched in a small focused commit
                    # after the repo already existed, mark as developer-modified!
                    if touch_counts[f] > 1 or not is_bulk_import:
                        if touch_counts[f] > 1:
                            developer_patched.add(f)

        # Also: if a vendor directory has 50+ files in it, and a file inside it was only ever
        # touched once (in the commit that added the folder), it is untouched upstream SDK code.
        untouched: Set[str] = set()
        for f in candidate_set:
            if f not in developer_patched and touch_counts.get(f, 0) <= 1:
                untouched.add(f)

        # Whole third-party libraries identified by their package manifest (not at repo root,
        # which would mean the repo itself is the library), unless the developer kept working on them.
        library_dirs = {
            os.path.dirname(f) for f in candidate_set
            if os.path.basename(f) in LIBRARY_MANIFEST_NAMES and "/" in f
        }
        for lib_dir in library_dirs:
            prefix = lib_dir + "/"
            lib_commits = sum(1 for files in commits if any(x.startswith(prefix) for x in files))
            if lib_commits <= VENDORED_LIBRARY_MAX_COMMITS:
                untouched.update(f for f in candidate_set if f.startswith(prefix))

        return untouched
    except Exception:
        return set()


def distill_python(content: str, max_body_lines: int = 16) -> str:
    """Condenses large Python function and data bodies while preserving definitions and docstrings."""
    content = strip_heavy_arrays(content)
    lines = content.splitlines(keepends=True)
    if len(lines) <= 25:
        return content

    out: List[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        is_fn_or_cls = (
            stripped.startswith("def ")
            or stripped.startswith("async def ")
            or stripped.startswith("class ")
        )
        is_data_head = (
            ("=" in stripped or ":" in stripped)
            and (stripped.endswith("{") or stripped.endswith("[") or stripped.endswith("("))
        )

        if is_fn_or_cls or is_data_head:
            out.append(line)
            indent_len = len(line) - len(line.lstrip())
            i += 1

            body: List[str] = []
            while i < n:
                cur_line = lines[i]
                cur_stripped = cur_line.strip()
                if not cur_stripped:
                    body.append(cur_line)
                    i += 1
                    continue
                cur_indent = len(cur_line) - len(cur_line.lstrip())
                if cur_indent <= indent_len:
                    break
                body.append(cur_line)
                i += 1

            if len(body) > max_body_lines:
                lead_space = " " * (indent_len + 4)
                condensed = (
                    body[:5]
                    + [f"{lead_space}# ... [{len(body) - 7} lines condensed for structural analysis] ...\n"]
                    + body[-2:]
                )
                out.extend(condensed)
            else:
                out.extend(body)
        else:
            out.append(line)
            i += 1

    return "".join(out)


def distill_brace_lang(content: str, max_body_lines: int = 16, comment_pfx: str = "//") -> str:
    """Condenses large function/method and data collection bodies in brace-based languages."""
    content = strip_heavy_arrays(content)
    lines = content.splitlines(keepends=True)
    if len(lines) <= 25:
        return content

    out: List[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        is_fn_head = any(
            stripped.startswith(kw) for kw in (
                "fun ", "override fun ", "suspend fun ", "private fun ", "public fun ",
                "function ", "public ", "private ", "protected ", "fn ", "func ",
                "async ", "void ", "int ", "bool ", "string ", "const ", "let ", "var ",
                "static ", "uint8_t ", "uint16_t ", "uint32_t ", "char "
            )
        ) and ("(" in stripped or "=>" in stripped or "PROGMEM" in stripped or "[" in stripped)

        is_data_literal = any(
            kw in stripped for kw in (
                "mapOf(", "listOf(", "setOf(", "arrayOf(", "hashMapOf(",
                "Map.of(", "List.of(", "= [", "= {", "={", "PROGMEM", "[] ="
            )
        )

        if is_fn_head or is_data_literal:
            out.append(line)
            indent_len = len(line) - len(line.lstrip())
            lead_space = " " * (indent_len + 4)

            delims = [("(", ")"), ("{", "}"), ("[", "]")]
            target_delim = None
            for op, cl in delims:
                if stripped.count(op) > stripped.count(cl):
                    target_delim = (op, cl)
                    break

            if not target_delim and ("{" in stripped or "(" in stripped or "[" in stripped):
                target_delim = ("{", "}")

            if target_delim:
                op, cl = target_delim
                delim_depth = line.count(op) - line.count(cl)
                i += 1
                while i < n and delim_depth <= 0 and op not in lines[i]:
                    out.append(lines[i])
                    i += 1
                if i < n and delim_depth <= 0 and op in lines[i]:
                    out.append(lines[i])
                    delim_depth += lines[i].count(op) - lines[i].count(cl)
                    i += 1

                if delim_depth > 0:
                    body: List[str] = []
                    while i < n and delim_depth > 0:
                        cur = lines[i]
                        delim_depth += cur.count(op) - cur.count(cl)
                        if delim_depth <= 0:
                            break
                        body.append(cur)
                        i += 1

                    if len(body) > max_body_lines:
                        condensed = (
                            body[:5]
                            + [f"{lead_space}{comment_pfx} ... [{len(body) - 7} lines condensed for structural analysis] ...\n"]
                            + body[-2:]
                        )
                        out.extend(condensed)
                    else:
                        out.extend(body)

                    if i < n:
                        out.append(lines[i])
                        i += 1
                    continue
        out.append(line)
        i += 1

    return "".join(out)


def distill_code(rel_path: str, content: str, max_body_lines: int = 16) -> str:
    """
    Distills source code down to its architectural essence.
    """
    ext = Path(rel_path).suffix.lower()

    if ext in (".md", ".txt", ".rst") or len(content.splitlines()) <= 25:
        return content

    if ext in (".py", ".pyw"):
        return distill_python(content, max_body_lines)

    if ext in (".kt", ".kts", ".java", ".ts", ".js", ".tsx", ".jsx", ".rs", ".go", ".c", ".cpp", ".ino", ".h", ".hpp", ".cs", ".swift", ".scala"):
        return distill_brace_lang(content, max_body_lines, comment_pfx="//")

    if ext in (".sql", ".lua"):
        return distill_brace_lang(content, max_body_lines, comment_pfx="--")

    content = strip_heavy_arrays(content)
    lines = content.splitlines(keepends=True)
    if len(lines) > 80:
        return "".join(lines[:20] + [f"\n# ... [{len(lines) - 30} lines condensed for context efficiency] ...\n\n"] + lines[-10:])

    return content


def build_file_tree(repo_name: str, code_files: List[str], assets: List[Tuple[str, str, int]]) -> List[str]:
    """
    Directory tree for the reviewers. Code files are listed by name. Media files are listed by name too
    (never their contents); a folder with many assets shows the first few names plus a count, e.g.
    "🖼 hero.png, enemy.png, coin.png, sky.png (+120 more images)".
    """
    entries: Dict[str, List[str]] = {}
    media: Dict[str, List[Tuple[str, str]]] = {}
    for rel in code_files:
        entries.setdefault(os.path.dirname(rel), []).append(os.path.basename(rel))
    for rel, kind, _ in assets:
        media.setdefault(os.path.dirname(rel), []).append((os.path.basename(rel), kind))
    all_dirs: Set[str] = set()
    for d in list(entries) + list(media):
        parts = d.split("/") if d else []
        for i in range(1, len(parts) + 1):
            all_dirs.add("/".join(parts[:i]))
    lines = [f"📁 {repo_name}/"]

    def emit(d: str, depth: int) -> None:
        indent = "  " * (depth + 1)
        for name in sorted(entries.get(d, [])):
            lines.append(f"{indent}📄 {name}")
        items = sorted(media.get(d, []))
        by_kind: Dict[str, List[str]] = {}
        for name, kind in items:
            by_kind.setdefault(kind, []).append(name)
        for kind, names in sorted(by_kind.items()):
            icon = MEDIA_ICONS.get(kind, "📎")
            if len(names) <= MEDIA_NAMES_PER_FOLDER:
                lines.extend(f"{indent}{icon} {n}" for n in names)
            else:
                plural = {"image": "images", "audio": "audio clips", "video": "videos", "font": "fonts", "3d model": "3D models"}
                lines.append(f"{indent}{icon} {', '.join(names[:MEDIA_NAMES_PER_FOLDER])} "
                             f"(+{len(names) - MEDIA_NAMES_PER_FOLDER} more {plural.get(kind, kind)})")
        children = sorted(c for c in all_dirs if os.path.dirname(c) == d and c != d)
        for child in children:
            lines.append(f"{indent}📁 {os.path.basename(child)}/")
            emit(child, depth + 1)

    emit("", 0)
    return lines


@dataclass
class PackedRepository:
    repo_name: str
    source_path: Path
    is_temporary: bool
    temp_dir: Optional[str] = None
    files: Dict[str, str] = field(default_factory=dict)
    file_tree: str = ""
    total_loc: int = 0
    data_loc: int = 0
    language_stats: Dict[str, int] = field(default_factory=dict)
    skipped_vendor_files: int = 0
    # Media files recorded by name only: (relative path, kind, size in bytes). Contents are never read.
    assets: List[Tuple[str, str, int]] = field(default_factory=list)

    def media_summary(self) -> str:
        """One line for the reviewers, e.g. '312 images, 45 audio clips; main folders: assets/sprites (124 images)'."""
        if not self.assets:
            return "none"
        counts: Dict[str, int] = {}
        folders: Dict[str, int] = {}
        for path, kind, _ in self.assets:
            counts[kind] = counts.get(kind, 0) + 1
            folder = os.path.dirname(path) or "."
            folders[folder] = folders.get(folder, 0) + 1
        plural = {"image": "images", "audio": "audio clips", "video": "videos", "font": "fonts", "3d model": "3D models"}
        kinds = ", ".join(f"{n} {plural.get(k, k)}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))
        top = ", ".join(f"{f} ({n})" for f, n in sorted(folders.items(), key=lambda kv: -kv[1])[:4])
        return f"{kinds}; main folders: {top}"

    def cleanup(self) -> None:
        """Removes temporary clone directory if created."""
        if self.is_temporary and self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    @property
    def estimated_tokens(self) -> int:
        """Rough token estimate based on average character-to-token ratio in code."""
        total_chars = sum(len(content) for content in self.files.values())
        return int(total_chars / 3.6)

    def get_distilled_files(self, max_body_lines: int = 16) -> Dict[str, str]:
        """Returns files with large implementation bodies condensed for context efficiency."""
        return {
            path: distill_code(path, content, max_body_lines=max_body_lines)
            for path, content in self.files.items()
        }

    def get_full_manifest(
        self,
        max_tokens: int = 120000,
        files_map: Optional[Dict[str, str]] = None,
    ) -> str:
        target_files = files_map if files_map is not None else self.files
        lines = [
            f"# REPOSITORY: {self.repo_name}",
            f"# TOTAL FILES: {len(target_files)}",
            f"# TOTAL LINES: {self.total_loc}",
            "\n# DIRECTORY STRUCTURE:",
            self.file_tree,
            "\n# ============================================================",
            "# SOURCE CODE COMMENCES BELOW (COMPLETE CODEBASE)",
            "# ============================================================",
        ]

        running_chars = sum(len(l) for l in lines)
        max_chars = max_tokens * 3.6

        for rel_path, content in sorted(target_files.items()):
            file_header = f"\n--- START FILE: {rel_path} ---\n"
            file_footer = f"\n--- END FILE: {rel_path} ---\n"
            file_block = f"{file_header}{content}{file_footer}"

            if running_chars + len(file_block) > max_chars:
                lines.append(
                    f"\n[NOTE: File {rel_path} omitted due to exceeding maximum context window ceiling ({max_tokens} tokens)]"
                )
                continue

            lines.append(file_block)
            running_chars += len(file_block)

        return "".join(lines)

    def get_coverage_manifests(
        self,
        max_batch_tokens: int = 4500,
        distill: bool = True,
        only: Optional[Dict[str, str]] = None,
        local_tree: bool = False,
    ) -> List[Tuple[str, str]]:
        """
        Partitions the codebase into strictly bounded batches (default 4,500 tokens)
        so every batch fits comfortably inside 8GB GPU VRAM with 0% CPU spillover.
        `only`: a pre-selected {path: content} set (see evaluator.triage.select_for_review); it is
        packed as given, so sampled story text in .txt files is kept.
        `local_tree`: instead of repeating the whole directory layout (up to 3,000 chars) in every
        batch, show the top-level folders plus only this batch's own files. The whole-project review
        must then be given the full tree separately (evaluate_pipeline(file_tree=...)).
        """
        source = only if only is not None else self.files
        files_to_pack = ({p: distill_code(p, c) for p, c in source.items()} if distill else dict(source))

        code_files = files_to_pack if only is not None else {
            p: c for p, c in files_to_pack.items()
            if not p.lower().endswith((".md", ".txt", ".rst", ".markdown", ".csv", ".tsv", ".svg"))
        }
        if not code_files:
            code_files = files_to_pack

        total_tokens = sum(int(len(c) / 3.6) for c in code_files.values())

        if total_tokens <= max_batch_tokens:
            return [("Full Codebase (Single Pass)", self.get_full_manifest(max_tokens=max_batch_tokens * 2, files_map=code_files))]

        batches: List[Tuple[str, str]] = []
        max_batch_chars = int(max_batch_tokens * 3.6)
        batch_tree = self.file_tree
        if len(batch_tree) > BATCH_TREE_MAX_CHARS:
            batch_tree = batch_tree[:BATCH_TREE_MAX_CHARS] + "\n... (tree truncated)"

        current_dirs: List[str] = []
        current_lines: List[str] = []
        current_chars = 0

        for rel_path, content in sorted(code_files.items()):
            top_dir = rel_path.split("/")[0] if "/" in rel_path else "root"

            if len(content) > max_batch_chars:
                if current_lines:
                    dir_summary = ", ".join(list(dict.fromkeys(current_dirs))[:4])
                    batch_title = f"Module Batch {len(batches) + 1} ({dir_summary})"
                    header = f"# BATCH: {batch_title}\n# REPOSITORY DIRECTORY LAYOUT:\n{batch_tree}\n\n"
                    batches.append((batch_title, header + "".join(current_lines)))
                    current_dirs = []
                    current_lines = []
                    current_chars = 0

                lines = content.splitlines(keepends=True)
                chunk: List[str] = []
                chunk_len = 0
                part = 1
                for line in lines:
                    if chunk and (chunk_len + len(line) > max_batch_chars - 2000):
                        part_content = "".join(chunk)
                        part_block = f"\n--- START FILE: {rel_path} [Part {part}] ---\n{part_content}\n--- END FILE: {rel_path} [Part {part}] ---\n"
                        batch_title = f"Module Batch {len(batches) + 1} ({top_dir} - {os.path.basename(rel_path)} Part {part})"
                        header = f"# BATCH: {batch_title}\n# REPOSITORY DIRECTORY LAYOUT:\n{batch_tree}\n\n"
                        batches.append((batch_title, header + part_block))
                        chunk = []
                        chunk_len = 0
                        part += 1
                    chunk.append(line)
                    chunk_len += len(line)
                if chunk:
                    part_content = "".join(chunk)
                    part_block = f"\n--- START FILE: {rel_path} [Part {part}] ---\n{part_content}\n--- END FILE: {rel_path} [Part {part}] ---\n"
                    current_dirs.append(top_dir)
                    current_lines.append(part_block)
                    current_chars += len(part_block)
                continue

            block = f"\n--- START FILE: {rel_path} ---\n{content}\n--- END FILE: {rel_path} ---\n"

            if current_lines and (current_chars + len(block) > max_batch_chars):
                dir_summary = ", ".join(list(dict.fromkeys(current_dirs))[:4])
                batch_title = f"Module Batch {len(batches) + 1} ({dir_summary})"
                header = f"# BATCH: {batch_title}\n# REPOSITORY DIRECTORY LAYOUT:\n{batch_tree}\n\n"
                batches.append((batch_title, header + "".join(current_lines)))
                current_dirs = []
                current_lines = []
                current_chars = 0

            current_dirs.append(top_dir)
            current_lines.append(block)
            current_chars += len(block)

        if current_lines:
            dir_summary = ", ".join(list(dict.fromkeys(current_dirs))[:4])
            batch_title = f"Module Batch {len(batches) + 1} ({dir_summary})"
            header = f"# BATCH: {batch_title}\n# REPOSITORY DIRECTORY LAYOUT:\n{batch_tree}\n\n"
            batches.append((batch_title, header + "".join(current_lines)))

        if local_tree:
            full_header_tree = f"# REPOSITORY DIRECTORY LAYOUT:\n{batch_tree}\n\n"
            top = sorted({p.split("/")[0] + "/" for p in self.files if "/" in p})
            overview = "# PROJECT TOP-LEVEL FOLDERS: " + (", ".join(top) if top else "(all files at root)") + "\n"
            localized = []
            for title, text in batches:
                groups: Dict[str, List[str]] = {}
                for path in dict.fromkeys(re.findall(r"--- START FILE: (.+?)(?: \[Part \d+\])? ---", text)):
                    d, _, name = path.rpartition("/")
                    groups.setdefault(d or ".", []).append(name)
                listing = "\n".join(f"#   {d}/: {', '.join(names)}" for d, names in groups.items())
                localized.append((title, text.replace(full_header_tree, overview + "# FILES IN THIS BATCH:\n" + listing + "\n\n", 1)))
            batches = localized
        return batches


def is_safe_repo_file(repo_root: Path, candidate: Path) -> bool:
    """True only when `candidate` is a regular file (never a symlink) resolved strictly inside `repo_root`."""
    try:
        if candidate.is_symlink():
            return False
        root_resolved = repo_root.resolve()
        cand_resolved = candidate.resolve()
        if not cand_resolved.is_relative_to(root_resolved):
            return False
        return cand_resolved.is_file()
    except OSError:
        return False


_KNOWN_VENDOR_BASENAMES = {
    "imgui.cpp", "imgui.h", "imgui_draw.cpp", "imgui_tables.cpp", "imgui_widgets.cpp",
    "imgui_demo.cpp", "imgui_internal.h", "imconfig.h", "imstb_rectpack.h",
    "imstb_textedit.h", "imstb_truetype.h", "imgui_impl_dx9.cpp", "imgui_impl_dx9.h",
    "imgui_impl_dx11.cpp", "imgui_impl_dx11.h", "imgui_impl_dx12.cpp", "imgui_impl_dx12.h",
    "imgui_impl_opengl3.cpp", "imgui_impl_opengl3.h", "imgui_impl_win32.cpp", "imgui_impl_win32.h",
    "imgui_impl_glfw.cpp", "imgui_impl_glfw.h", "imgui_impl_sdl2.cpp", "imgui_impl_sdl2.h",
    "sqlite3.c", "sqlite3.h", "cjson.c", "cjson.h", "miniaudio.h", "miniz.c", "miniz.h",
    "glad.c", "glew.c",
}


def _is_known_vendored_lib_file(rel_path: str, content: str) -> bool:
    """Identifies bundled third-party single-header/amalgamated libraries even in single-commit repos."""
    low = rel_path.replace("\\", "/").lower()
    base = os.path.basename(low)
    if base in _KNOWN_VENDOR_BASENAMES or base.startswith("stb_"):
        return True
    if "nlohmann/json.hpp" in low:
        return True
    head = content[:600].lower()
    if "// dear imgui, v" in head or "/* dear imgui" in head:
        return True
    if "json for modern c++" in head and "nlohmann" in head:
        return True
    return False


class RepositoryIngestor:
    """Manages cloning, traversal, and whole-codebase packing."""

    @staticmethod
    def is_git_url(target: str) -> bool:
        return (
            target.startswith("http://")
            or target.startswith("https://")
            or target.startswith("git@")
            or target.endswith(".git")
        )

    @classmethod
    def ingest(
        cls,
        target: str,
        ignore_limits: bool = False,
        branch: Optional[str] = None,
        subpath: Optional[str] = None,
    ) -> PackedRepository:
        """Ingests a local directory or clones a remote repository (optionally scoped to branch/subpath)."""
        is_temp = False
        temp_dir = None

        if cls.is_git_url(target):
            is_temp = True
            temp_dir = tempfile.mkdtemp(prefix="letsvibe_clone_")
            repo_name = target.rstrip("/").split("/")[-1].replace(".git", "")
            clone_path = Path(temp_dir)

            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            # Every commit, but no file contents from old commits (--filter=blob:none): the vibe-coded check needs the
            # first commit's date (November 2022 rule) and who wrote the commits (forks), and the git log pre-scan below
            # distinguishes initial SDK drops from later developer edits. Only the current files are downloaded in full.
            cmd = [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-recurse-submodules",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.symlinks=false",
            ]
            if branch:
                b_clean = branch.strip()
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,119}", b_clean) or ".." in b_clean or b_clean.startswith("-"):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    raise ValueError(f"Invalid branch name: {branch!r}")
                cmd.extend(["--branch", b_clean])
            cmd.extend([target, str(clone_path)])
            try:
                result = subprocess.run(  # nosec B603 - fixed command, list arguments, no shell
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    env=env,
                )
            except subprocess.TimeoutExpired:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise RuntimeError(f"Git clone timed out after 180 seconds: {target}")

            if result.returncode != 0:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise RuntimeError(f"Failed to clone git repository {target}: {result.stderr.strip()}")
            source_path = clone_path
        else:
            source_path = Path(target).resolve()
            if not source_path.exists() or not source_path.is_dir():
                raise FileNotFoundError(f"Target directory does not exist: {target}")
            repo_name = source_path.name

        clean_subpath = (subpath or "").strip().strip("/")
        if clean_subpath:
            if ".." in Path(clean_subpath).parts:
                if is_temp and temp_dir:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                raise ValueError(f"Invalid subpath: {subpath!r}")
            scoped = (source_path / clean_subpath).resolve()
            if not str(scoped).startswith(str(source_path.resolve())) or not scoped.is_dir():
                if is_temp and temp_dir:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                raise RuntimeError(f"Subpath '{clean_subpath}' not found in repository.")
            source_path = scoped
            repo_name = f"{repo_name}/{clean_subpath}"
        else:
            version_dirs = sorted(
                d.name for d in source_path.iterdir()
                if d.is_dir() and not d.name.startswith(".") and re.fullmatch(r"v?\d+(?:\.\d+)+|v\d+", d.name, re.I)
            )
            root_code_files = [
                f for f in source_path.iterdir()
                if f.is_file()
                and f.suffix
                and f.suffix.lower() not in BINARY_EXTENSIONS
                and f.suffix.lower() not in DATA_AND_DOC_EXTENSIONS
            ]
            if len(version_dirs) >= 2 and len(root_code_files) <= 2:
                base_url = target.rstrip("/").removesuffix(".git")
                br_hint = branch or "main"
                if is_temp and temp_dir:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                raise RuntimeError(
                    f"Multi-version repository detected ({', '.join(version_dirs[:8])}). "
                    f"Please submit the specific version folder URL, e.g. {base_url}/tree/{br_hint}/{version_dirs[-1]}"
                )

        files: Dict[str, str] = {}
        tree_lines: List[str] = []
        lang_stats: Dict[str, int] = {}
        total_loc = 0
        data_loc = 0
        total_bytes = 0
        skipped_vendor_count = 0
        assets: List[Tuple[str, str, int]] = []

        is_git_repo = (source_path / ".git").exists() and not clean_subpath
        tracked_rel_files: Optional[List[str]] = None

        if is_git_repo:
            try:
                env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
                res = subprocess.run(  # nosec B603 B607 - fixed command, list arguments, no shell
                    ["git", "ls-files", "-c", "-o", "--exclude-standard"],
                    cwd=source_path,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=15,
                    env=env,
                )
                tracked_rel_files = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            except Exception:
                tracked_rel_files = None

        if tracked_rel_files is not None:
            untouched_vendor_set = get_untouched_vendor_files(source_path, tracked_rel_files)

            for rel_file in sorted(tracked_rel_files):
                if rel_file in untouched_vendor_set:
                    skipped_vendor_count += 1
                    continue

                parts_set = set(rel_file.split("/"))
                if parts_set.intersection(IGNORE_DIRS):
                    continue

                fn = os.path.basename(rel_file)
                fn_low = fn.lower()
                if fn_low in BLOAT_FILENAMES or fn_low.endswith(".min.js") or fn_low.endswith(".min.css"):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                abs_file = source_path / rel_file
                if not is_safe_repo_file(source_path, abs_file):
                    continue
                if ext in MEDIA_KINDS:
                    if len(assets) < MAX_MEDIA_ENTRIES:
                        assets.append((rel_file, MEDIA_KINDS[ext], abs_file.stat().st_size))
                    continue
                if ext in BINARY_EXTENSIONS:
                    continue

                try:
                    with open(abs_file, "rb") as bf:
                        sample = bf.read(1024)
                    if b"\x00" in sample or sample.startswith(b"\x7fELF") or sample.startswith(b"MZ"):
                        continue
                    with open(abs_file, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()

                    if _is_known_vendored_lib_file(rel_file, content):
                        skipped_vendor_count += 1
                        continue

                    # Strip heavy hex/bitmap arrays immediately on ingest
                    content = strip_heavy_arrays(content)

                    is_secondary_locale = (
                        ("/localization/strings_" in rel_file or "/locales/" in rel_file)
                        and not rel_file.endswith(("_en.json", "/en.json", "strings_en.json"))
                    )
                    if is_secondary_locale and len(content) > 3000:
                        orig_loc = content.count("\n") + 1
                        content = content[:2000] + f"\n\n[... Localized translation dictionary ({orig_loc} entries) truncated for context efficiency ...]\n"

                    files[rel_file] = content
                    loc = content.count("\n") + (1 if content else 0)
                    total_bytes += len(content)
                    if ext in DATA_AND_DOC_EXTENSIONS:
                        data_loc += loc
                    else:
                        total_loc += loc
                        lang = ext.lstrip(".") or "other"
                        lang_stats[lang] = lang_stats.get(lang, 0) + loc
                except Exception:  # nosec B112 - deliberate best-effort fallback
                    continue

            tree_lines.extend(build_file_tree(repo_name, list(files.keys()), assets))

        else:
            for root, dirs, filenames in os.walk(source_path, followlinks=False):
                dirs[:] = [
                    d for d in dirs
                    if d not in IGNORE_DIRS
                    and (not d.startswith(".") or d in ALLOWED_DOT_DIRS)
                    and not (Path(root) / d).is_symlink()
                ]

                rel_root = os.path.relpath(root, source_path)
                indent = "  " * (0 if rel_root == "." else rel_root.count(os.sep) + 1)
                folder_name = os.path.basename(root) if rel_root != "." else repo_name
                tree_lines.append(f"{indent}📁 {folder_name}/")

                for fn in sorted(filenames):
                    fn_low = fn.lower()
                    if fn_low in BLOAT_FILENAMES or fn_low.endswith(".min.js") or fn_low.endswith(".min.css"):
                        continue
                    abs_file = Path(root) / fn
                    if not is_safe_repo_file(source_path, abs_file):
                        continue
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in MEDIA_KINDS:
                        if len(assets) < MAX_MEDIA_ENTRIES:
                            assets.append((str(abs_file.relative_to(source_path)), MEDIA_KINDS[ext], abs_file.stat().st_size))
                            tree_lines.append(f"{indent}  {MEDIA_ICONS[MEDIA_KINDS[ext]]} {fn}")
                        continue
                    if ext in BINARY_EXTENSIONS:
                        continue

                    rel_file = str(abs_file.relative_to(source_path))

                    tree_lines.append(f"{indent}  📄 {fn}")

                    try:
                        with open(abs_file, "rb") as bf:
                            sample = bf.read(1024)
                        if b"\x00" in sample or sample.startswith(b"\x7fELF") or sample.startswith(b"MZ"):
                            continue
                        with open(abs_file, "r", encoding="utf-8", errors="replace") as f:
                            raw_content = f.read()
                        if _is_known_vendored_lib_file(rel_file, raw_content):
                            skipped_vendor_count += 1
                            continue
                        content = strip_heavy_arrays(raw_content)
                        files[rel_file] = content
                        loc = content.count("\n") + (1 if content else 0)
                        total_bytes += len(content)
                        if ext in DATA_AND_DOC_EXTENSIONS:
                            data_loc += loc
                        else:
                            total_loc += loc
                            lang = ext.lstrip(".") or "other"
                            lang_stats[lang] = lang_stats.get(lang, 0) + loc
                    except Exception:  # nosec B112 - deliberate best-effort fallback
                        continue

        return PackedRepository(
            repo_name=repo_name,
            source_path=source_path,
            is_temporary=is_temp,
            temp_dir=temp_dir,
            files=files,
            file_tree="\n".join(tree_lines),
            total_loc=total_loc,
            data_loc=data_loc,
            language_stats=lang_stats,
            skipped_vendor_files=skipped_vendor_count,
            assets=assets,
        )