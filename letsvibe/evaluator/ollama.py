"""
Ollama client wrapper supporting 8GB VRAM guardrails, CPU-spillover circuit breakers,
auto-server initialization, and JSON output.
"""

import json
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx


DEFAULT_OLLAMA_HOST = "http://localhost:11434"

# Conservative chars-per-token estimate. Source code tokenizes at ~3.0-3.4 chars/token with
# Qwen2.5; under-estimating tokens makes Ollama silently drop the FRONT of the prompt (i.e. the
# system prompt with the instructions and schema), which is how blank/garbage reports were made.
CHARS_PER_TOKEN = 3.0
TEMPLATE_OVERHEAD_TOKENS = 64
TRIM_MARKER = "\n\n[... input trimmed to fit the model context window ...]\n"


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def is_moe(model: str) -> bool:
    """Mixture-of-experts models activate only a few billion parameters per token, so they stay usable
    with part of the model in system RAM."""
    m = model.lower()
    return "gpt-oss" in m or "-a3b" in m or "a3b" in m.split(":")[-1] or "moe" in m


def is_large(model: str) -> bool:
    """Models too big to sit entirely on an 8GB GPU (need longer timeouts and to have VRAM to themselves)."""
    m = model.lower()
    return is_moe(m) or any(tag in m for tag in ("14b", "20b", "24b", "27b", "30b", "32b"))


def supports_format(model: str) -> bool:
    """
    gpt-oss on this Ollama build returns empty or garbled output whenever the output format is
    constrained (schema or "json" mode), but answers correctly in plain mode. For it we ask for JSON in
    the prompt and extract it from the reply; every caller validates the result and retries or fails
    closed, so a malformed answer can never slip through.
    """
    return "gpt-oss" not in model.lower()


def extract_json(text: str) -> Any:
    """Parses JSON from a model reply: raw JSON, a fenced block, or the outermost {...} in prose."""
    clean = (text or "").strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
    try:
        return json.loads(clean.strip())
    except json.JSONDecodeError:
        start, end = clean.find("{"), clean.rfind("}")
        if start != -1 and end > start:
            return json.loads(clean[start:end + 1])
        raise


def think_option(model: str):
    """Thinking/reasoning modes write a long hidden monologue before the JSON answer; keep them minimal so
    every model is compared on the answer itself and within the same output budget."""
    m = model.lower()
    if "gpt-oss" in m:
        return "low"  # gpt-oss cannot disable reasoning entirely
    if m.startswith(("qwen3", "deepseek-r1", "magistral")):
        return False
    return None


def max_ctx_for(model: str) -> int:
    """
    Largest context window that stays (mostly) on an 8GB GPU. The 7B KV cache is ~57KB/token so
    12K is cheap; the 14B is already ~50% CPU-offloaded at 4K and trips the spillover guard above
    ~6K. Override with LETSVIBE_MAX_CTX_14B / LETSVIBE_MAX_CTX.
    """
    if "14b" in model.lower():
        return int(os.environ.get("LETSVIBE_MAX_CTX_14B", "6144"))
    return int(os.environ.get("LETSVIBE_MAX_CTX", "12288"))


def fit_prompt(system_prompt: str, user_prompt: str, max_ctx: int, max_output_tokens: int) -> Tuple[str, int, bool]:
    """
    Returns (user_prompt, num_ctx, trimmed). num_ctx is always the model's fixed window (a changing
    window forces a model reload); if the prompt cannot fit, the END of the user prompt is trimmed
    ourselves so the system prompt is never lost.
    """
    fixed = estimate_tokens(system_prompt) + max_output_tokens + TEMPLATE_OVERHEAD_TOKENS
    user_budget = max_ctx - fixed
    if user_budget < 256:
        raise ValueError(
            f"System prompt + output reserve ({fixed} tokens) leave no room for input in a {max_ctx}-token context."
        )
    trimmed = False
    if estimate_tokens(user_prompt) > user_budget:
        keep_chars = int(user_budget * CHARS_PER_TOKEN) - len(TRIM_MARKER)
        user_prompt = user_prompt[:keep_chars] + TRIM_MARKER
        trimmed = True
    # Always the same window per model: Ollama reloads the whole model whenever num_ctx changes
    # (3-4 s per call on this GPU, far more for CPU-offloaded models), which cost more than a larger KV cache.
    return user_prompt, max_ctx, trimmed


class CpuSpilloverError(RuntimeError):
    """Raised when an Ollama model spills into system RAM/CPU or hangs beyond safe GPU limits."""
    pass


def get_free_vram_mb() -> Optional[int]:
    """Queries nvidia-smi for free GPU VRAM in MiB, returning None if unavailable or non-NVIDIA."""
    try:
        cmd = ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=2.0)
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        if lines:
            return int(lines[0])
    except Exception:
        pass
    return None


def find_ollama_binary() -> Optional[str]:
    """Finds the Ollama executable across system PATH and standard user installation directories."""
    candidates = [
        shutil.which("ollama"),
        os.path.expanduser("~/ollama/bin/ollama"),
        os.path.expanduser("~/.local/bin/ollama"),
        "/usr/local/bin/ollama",
        "/usr/bin/ollama",
        "/opt/ollama/bin/ollama",
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


class OllamaClient:
    """Manages communication with local Ollama daemon with strict GPU/timeout guardrails."""

    def __init__(self, host: str = DEFAULT_OLLAMA_HOST, timeout_seconds: float = 120.0):
        self.host = host.rstrip("/")
        self.timeout = timeout_seconds
        self.last_call: Dict[str, Any] = {}

    def is_running(self) -> bool:
        """Checks if Ollama HTTP server is responsive."""
        try:
            r = httpx.get(f"{self.host}/api/tags", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    def ensure_server_running(self, timeout_sec: float = 15.0) -> bool:
        """Attempts to start the Ollama server in background if not already active."""
        if self.is_running():
            return True

        ollama_bin = find_ollama_binary()
        if not ollama_bin:
            return False

        try:
            subprocess.Popen(
                [ollama_bin, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            return False

        poll_interval = 0.5
        max_attempts = int(timeout_sec / poll_interval)
        for _ in range(max_attempts):
            time.sleep(poll_interval)
            if self.is_running():
                return True

        return False

    def list_models(self) -> List[str]:
        """Returns names of all locally available Ollama models."""
        try:
            r = httpx.get(f"{self.host}/api/tags", timeout=5.0)
            if r.status_code == 200:
                data = r.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            pass
        return []

    def get_loaded_models(self) -> List[Dict[str, Any]]:
        """Returns details of models currently loaded in GPU VRAM via /api/ps."""
        try:
            r = httpx.get(f"{self.host}/api/ps", timeout=5.0)
            if r.status_code == 200:
                return r.json().get("models", [])
        except Exception:
            pass
        return []

    def check_cpu_spillover(self, max_cpu_ratio: float = 0.45) -> Optional[str]:
        """
        Inspects /api/ps to check if a loaded model has spilled onto system RAM (CPU).
        Returns a detailed diagnostic string if CPU spillover exceeds max_cpu_ratio, else None.
        """
        loaded = self.get_loaded_models()
        for m in loaded:
            name = m.get("name") or m.get("model") or "unknown"
            total_size = int(m.get("size") or 0)
            vram_size = int(m.get("size_vram") or 0)
            if total_size > 0:
                cpu_bytes = max(0, total_size - vram_size)
                cpu_ratio = cpu_bytes / float(total_size)
                if cpu_ratio > max_cpu_ratio:
                    total_mb = total_size // (1024 * 1024)
                    vram_mb = vram_size // (1024 * 1024)
                    cpu_mb = cpu_bytes // (1024 * 1024)
                    return (
                        f"Model '{name}' spilled onto CPU RAM ({cpu_mb} MB on CPU / {vram_mb} MB on GPU "
                        f"out of {total_mb} MB total; {int(cpu_ratio * 100)}% CPU offload)."
                    )
        return None

    def unload_model(self, model: str) -> bool:
        """
        Explicitly unloads a model from GPU memory by setting keep_alive to 0.
        """
        success = False
        payload = {"model": model, "prompt": "", "keep_alive": 0}
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.post(f"{self.host}/api/generate", json=payload)
                if r.status_code == 200:
                    success = True
                r_chat = client.post(f"{self.host}/api/chat", json={"model": model, "messages": [], "keep_alive": 0})
                if r_chat.status_code == 200:
                    success = True
            return success
        except Exception:
            return False

    def unload_all_models(self) -> int:
        """Unloads all models currently occupying GPU VRAM."""
        unloaded_count = 0
        loaded = self.get_loaded_models()
        model_names = set()
        for m in loaded:
            name = m.get("name") or m.get("model")
            if name:
                model_names.add(name)

        for name in model_names:
            if self.unload_model(name):
                unloaded_count += 1

        return unloaded_count

    def generate_json(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        num_ctx: Optional[int] = None,
        temperature: float = 0.2,
        keep_alive: Optional[Any] = None,
        format_schema: Optional[Dict[str, Any]] = None,
        max_output_tokens: int = 1024,
    ) -> Dict[str, Any]:
        """
        Sends generation request with forced JSON format (or a JSON schema when
        `format_schema` is given so required keys cannot be omitted), a context window sized to
        the actual prompt (never letting Ollama silently truncate the system prompt), and
        CPU-spillover / timeout protection. `num_ctx`, if given, further caps the window.

        Details of the call (num_ctx, whether input was trimmed, token counts) are left in
        `self.last_call` so callers can surface them.
        """
        is_14b = "14b" in model.lower()
        large = is_large(model)
        cap = max_ctx_for(model) if num_ctx is None else min(num_ctx, max_ctx_for(model))
        user_prompt, safe_ctx, trimmed = fit_prompt(system_prompt, user_prompt, cap, max_output_tokens)
        self.last_call = {"model": model, "num_ctx": safe_ctx, "trimmed": trimmed}
        # The 14B runs ~50% on CPU on an 8GB card (~8 tok/s), so it needs a longer leash.
        call_timeout = max(self.timeout, 600.0) if large else self.timeout

        # Before loading a 14B model, ensure no other model is still occupying VRAM
        if large:
            for loaded_m in self.get_loaded_models():
                lname = loaded_m.get("name") or loaded_m.get("model") or ""
                if lname and lname != model:
                    self.unload_model(lname)
                    time.sleep(0.5)

        url = f"{self.host}/api/generate"
        payload = {
            "model": model,
            "system": system_prompt,
            "prompt": user_prompt,
            "format": format_schema if format_schema else "json",
            "stream": False,
            "options": {
                "num_ctx": safe_ctx,
                "num_predict": max_output_tokens,
                "temperature": temperature,
            },
        }
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        think = think_option(model)
        if think is not None:
            payload["think"] = think
        if not supports_format(model):
            payload.pop("format", None)
            payload["system"] = (system_prompt + "\n\nRespond with ONE valid JSON object only: no prose, no code fences."
                                 + (f"\nRequired top-level keys: {', '.join(format_schema.get('required', []))}." if format_schema else ""))

        try:
            with httpx.Client(timeout=call_timeout) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 400 and format_schema:
                    # Older Ollama builds reject schema formats; degrade to plain JSON mode
                    payload["format"] = "json"
                    resp = client.post(url, json=payload)
                if resp.status_code != 200:
                    raise RuntimeError(f"Ollama error ({resp.status_code}): {resp.text}")
        except httpx.TimeoutException as te:
            # Check if timeout was caused by CPU spillover
            spill_diag = self.check_cpu_spillover(max_cpu_ratio=0.35)
            self.unload_all_models()
            if spill_diag:
                raise CpuSpilloverError(
                    f"Hard Stop Triggered: LLM call timed out after {int(call_timeout)}s due to CPU spillover. {spill_diag}"
                ) from te
            raise CpuSpilloverError(
                f"Hard Stop Triggered: Model '{model}' hung for over {int(call_timeout)}s (safe_ctx={safe_ctx}, prompt_chars={len(user_prompt)})."
            ) from te

        # Verify model didn't severely spill onto CPU during execution
        # MoE models are designed to run partly from system RAM; dense models must stay mostly on the GPU
        spill_diag = self.check_cpu_spillover(max_cpu_ratio=0.80 if is_moe(model) else 0.55)
        if spill_diag:
            self.unload_all_models()
            raise CpuSpilloverError(f"Hard Stop Triggered: {spill_diag}")

        result = resp.json()
        raw_text = result.get("response", "{}")
        self.last_call.update(
            prompt_eval_count=result.get("prompt_eval_count"),
            eval_count=result.get("eval_count"),
            done_reason=result.get("done_reason"),
        )
        if result.get("done_reason") == "length":
            raise RuntimeError(
                f"{model} hit the {max_output_tokens}-token output limit; JSON was cut off."
            )

        return extract_json(raw_text)