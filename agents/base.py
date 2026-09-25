"""Common helpers shared by every agent."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
STANDARDS_FILE = ROOT / "CLAUDE.md"

SKIP_DIRS = {
    "tasks", "logs", "node_modules", "__pycache__", ".git", "venv", ".venv",
    "dist", "build", ".pytest_cache", ".mypy_cache", ".idea", ".vscode",
    "target", "out", ".next", ".turbo", "coverage", "runs", "workspace",
}
SKIP_EXTS = {
    ".pyc", ".log", ".lock", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar", ".dll", ".so", ".dylib",
    ".exe", ".bin", ".class", ".jar", ".wasm", ".mp3", ".mp4", ".mov",
}
SENSITIVE_DIRS = {
    ".aws", ".azure", ".gnupg", ".ssh", "credentials", "private_keys", "secrets",
}
SENSITIVE_FILE_NAMES = {
    ".netrc", ".npmrc", ".pypirc", ".yarnrc", "credentials", "credentials.json",
    "id_dsa", "id_ecdsa", "id_ed25519", "id_rsa", "private_key", "secrets.json",
    "service-account.json",
}
SENSITIVE_EXTS = {
    ".jks", ".key", ".keystore", ".p12", ".p8", ".pem", ".pfx",
}
SENSITIVE_NAME_RE = re.compile(
    r"(^|[._-])("
    r"api[-_]?keys?|credentials?|deploy[-_]?keys?|private[-_]?keys?|"
    r"secret[-_]?keys?|secrets?|service[-_]?accounts?|tokens?"
    r")([._-]|$)",
    re.IGNORECASE,
)


class BaseAgent:
    """Common behavior: prompt loading, logging, file collection."""

    def __init__(self, config: dict):
        self.config = config

    @property
    def name(self) -> str:
        return self.__class__.__name__

    def load_system_prompt(self, prompt_name: str) -> str:
        """Read prompts/<name>_system.md and inject CLAUDE.md and lessons.md."""
        prompt = (PROMPTS_DIR / f"{prompt_name}_system.md").read_text(encoding="utf-8")
        standards = STANDARDS_FILE.read_text(encoding="utf-8")
        lessons_file = ROOT / "tasks" / "lessons.md"
        lessons = lessons_file.read_text(encoding="utf-8") if lessons_file.exists() else ""
        return prompt.replace("{CLAUDE_MD}", standards).replace("{LESSONS_MD}", lessons)

    def log(self, project_dir: Path, message: str) -> None:
        log_dir = project_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().isoformat(timespec="seconds")
        with open(log_dir / f"{self.name}.log", "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")

    def claude_backend(self) -> str:
        """Return the configured Claude backend.

        ``claude_code`` uses the local Claude Code CLI/OAuth subscription. The
        legacy ``anthropic_api`` backend is still available for callers that
        explicitly opt into API-key billing.
        """
        return str(self.config.get("claude_backend", "claude_code")).strip().lower()

    def resolve_command(self, key: str, default: str) -> str:
        """Resolve a configured CLI command to an executable path when possible."""
        command = str(self.config.get(key, default))
        return shutil.which(command) or command

    def run_claude_stage(
        self,
        project_dir: Path,
        *,
        stage: str,
        system_prompt: str,
        user_message: str,
        artifact_hint: str,
        max_tokens_key: str,
        default_max_tokens: int = 16000,
    ) -> str:
        """Run an Architect/Tester style Claude stage through the configured backend."""
        backend = self.claude_backend()
        if backend == "claude_code":
            return self.run_claude_code(
                project_dir,
                stage=stage,
                system_prompt=system_prompt,
                user_message=user_message,
                artifact_hint=artifact_hint,
            )
        if backend == "anthropic_api":
            return self.run_anthropic_api(
                system_prompt=system_prompt,
                user_message=user_message,
                max_tokens_key=max_tokens_key,
                default_max_tokens=default_max_tokens,
            )
        raise RuntimeError(
            f"Unsupported claude_backend={backend!r}. "
            "Expected 'claude_code' or 'anthropic_api'."
        )

    def run_anthropic_api(
        self,
        *,
        system_prompt: str,
        user_message: str,
        max_tokens_key: str,
        default_max_tokens: int,
    ) -> str:
        """Run a Claude stage through the Anthropic API backend."""
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError(
                "Anthropic Python SDK is required when claude_backend='anthropic_api'. "
                "Use claude_backend='claude_code' to run through a Claude Code subscription."
            ) from exc

        client = Anthropic()
        response = client.messages.create(
            model=self.config["claude_model"],
            max_tokens=self.config.get(max_tokens_key, default_max_tokens),
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )

    def run_claude_code(
        self,
        project_dir: Path,
        *,
        stage: str,
        system_prompt: str,
        user_message: str,
        artifact_hint: str,
    ) -> str:
        """Run a Claude-backed stage through Claude Code CLI, not the API.

        The dashboard launches long-running pipelines without API keys. Claude
        Code reads the user's subscription/OAuth auth from the CLI's own config,
        so prompts are sent to ``claude -p`` instead of Anthropic's Python SDK.
        Prompt files are persisted under tasks/.claude_prompts for auditability
        and to avoid leaking huge prompts into process command lines.
        """
        resolved = self.resolve_command("claude_command", "claude")
        prompt_dir = project_dir / "tasks" / ".claude_prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        safe_stage = re.sub(r"[^a-z0-9]+", "-", stage.lower()).strip("-") or "stage"
        system_path = prompt_dir / f"{safe_stage}_system.md"
        user_path = prompt_dir / f"{safe_stage}_user.md"
        system_path.write_text(system_prompt, encoding="utf-8")
        user_path.write_text(user_message, encoding="utf-8")

        instruction = (
            "Use the task content supplied on stdin. Return only the requested "
            f"fenced artifact(s): {artifact_hint}. Do not include commentary outside the fences."
        )
        cmd = [
            resolved,
            "-p",
            instruction,
            "--append-system-prompt-file",
            str(system_path),
            "--no-session-persistence",
            "--max-turns",
            str(self.config.get("claude_code_max_turns", 12)),
        ]
        model = str(self.config.get("claude_code_model", "opus")).strip()
        if model:
            cmd.extend(["--model", model])

        log_cmd = [
            resolved,
            "-p",
            f"<stdin {len(user_message)} chars>",
            "--append-system-prompt-file",
            str(system_path),
            "--no-session-persistence",
            "--max-turns",
            str(self.config.get("claude_code_max_turns", 12)),
        ]
        if model:
            log_cmd.extend(["--model", model])
        self.log(project_dir, "exec: " + " ".join(log_cmd))
        result = subprocess.run(
            cmd,
            cwd=str(project_dir),
            input=user_message,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.log(project_dir, f"returncode={result.returncode}")
        if result.stdout:
            self.log(project_dir, f"stdout[:4000]={result.stdout[:4000]}")
        if result.stderr:
            self.log(project_dir, f"stderr[:4000]={result.stderr[:4000]}")
        if result.returncode != 0:
            raise RuntimeError(
                f"Claude Code stage '{stage}' failed with return code {result.returncode}. "
                f"See logs/{self.name}.log and {user_path}."
            )
        return result.stdout


def collect_code_files(project_dir: Path, max_bytes: int | None = 200_000) -> list[Path]:
    """Return all code-like files under project_dir, skipping junk + oversized files.

    ``max_bytes`` limits prompt/context collection. Pass ``None`` for non-prompt
    callers, such as clean-resume fingerprints, that need to account for every
    reviewable source file regardless of size.
    """
    files: list[Path] = []
    for root, dirs, fnames in os.walk(project_dir):
        dirs[:] = [
            d for d in dirs
            if d.lower() not in SKIP_DIRS
            and d.lower() not in SENSITIVE_DIRS
            and not is_sensitive_name(d)
            and not d.startswith(".")
        ]
        for fname in fnames:
            path = Path(root) / fname
            if is_sensitive_file(path):
                continue
            if path.suffix.lower() in SKIP_EXTS:
                continue
            try:
                if max_bytes is not None and path.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            files.append(path)
    files.sort()
    return files


def is_sensitive_file(path: Path) -> bool:
    """Return True for filenames that commonly contain credentials or key material."""
    name = path.name.lower()
    if name.startswith(".env"):
        return True
    if name in SENSITIVE_FILE_NAMES:
        return True
    if path.suffix.lower() in SENSITIVE_EXTS:
        return True
    return is_sensitive_name(name)


def is_sensitive_name(name: str) -> bool:
    """Return True for file or directory names that commonly contain credentials."""
    return SENSITIVE_NAME_RE.search(name.lower()) is not None


def extract_fenced_section(text: str, name: str) -> str:
    """Pull out content between required '=== NAME ===' and '=== END NAME ===' markers."""
    start_marker = f"=== {name} ==="
    end_marker = f"=== END {name} ==="
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker)) if start != -1 else -1
    if start == -1 or end == -1:
        return ""
    return text[start + len(start_marker):end].strip()


FILE_BLOCK_RE = re.compile(
    r"===\s*FILE:\s*(?P<path>[^\n=]+?)\s*===\n(?P<body>.*?)\n===\s*END FILE\s*===",
    re.DOTALL,
)


def parse_file_blocks(text: str) -> list[tuple[str, str]]:
    """Extract (relative_path, content) pairs from `=== FILE: <path> ===` blocks."""
    blocks: list[tuple[str, str]] = []
    for m in FILE_BLOCK_RE.finditer(text):
        rel_path = m.group("path").strip().strip('"').strip("'")
        body = m.group("body")
        # Strip leading/trailing language fences if the model wrapped the content in ``` blocks.
        body = _strip_optional_code_fence(body)
        blocks.append((rel_path, body))
    return blocks


def _strip_optional_code_fence(body: str) -> str:
    stripped = body.strip("\n")
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            after = stripped[first_newline + 1:]
            if after.rstrip().endswith("```"):
                return after.rstrip()[: -3].rstrip("\n")
    return body


def write_files(project_dir: Path, blocks: Iterable[tuple[str, str]]) -> list[Path]:
    """Write parsed (path, content) blocks to disk, refusing path traversal."""
    written: list[Path] = []
    project_dir = project_dir.resolve()
    for rel, content in blocks:
        rel_norm = rel.replace("\\", "/").lstrip("/")
        target = (project_dir / rel_norm).resolve()
        # Refuse any path that escapes the project directory.
        try:
            target.relative_to(project_dir)
        except ValueError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target)
    return written
