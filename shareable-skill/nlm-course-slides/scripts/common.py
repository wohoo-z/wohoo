#!/usr/bin/env python3
"""
Shared helpers for the nlm-course-slides skill.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from html import unescape
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
SECTION_ID_PREFIX_RE = re.compile(r"^\s*([一二三四五六七八九十百千万零两\d]+(?:[.\-、]\d+)*)\s+")

SECTION_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"第\s*[一二三四五六七八九十百千万零两\d]+\s*[章节讲部分单元课]|"
    r"[一二三四五六七八九十百千万零两\d]+(?:[.\-、]\d+)*[.\-、)]?|"
    r"\(\d+(?:\.\d+)*\)|"
    r"（\d+(?:\.\d+)*）"
    r")\s*"
)

FOCUS_PROMPT_TEMPLATE = """文稿题目：{section_title}
目标受众：
- 流程与智能化中的企业领导者
- 企业各个部门业务负责人
- 企业流程与IT部门员工
- 人工智能变革项目管理者
这是企业流程智能化培训解决方案。
本解决方案旨在企业流程框架（参见企业流程框架基础解决方案）的基础上，讲解人工智能对企业流程的设计、实施、运营所产生的深刻影响。
课程为流程与智能化中的管理者和参与数智变革的人员提供数智化基础理论、实操建议和案例研究。
请根据来源文档生成演示文稿，演示文稿要和来源文档的逻辑与核心内容严格对应，支持学员边看演示文稿边理解讲解主线、关键方法和落地动作。
开篇尽量简洁，直入主题。
必须遵循来源文档中的专业用语和定义。
页数要求：10-16页，用适当的文字讲解主要内容。
不要浪费页面讲口号，要讲具体的要点、方法、边界和案例启发。
演示文稿以中文为主，但允许保留来源文档中已经出现的英文缩写或英文术语，例如 AI、LTC、IPD、CRM、ERP。不要额外引入来源文档里没有出现的英文单词、英文句子或纯英文标题。
取消结尾页。
视觉效果要求: 遵循极简的商务视觉原则，以现代企业扁平线性矢量插图为主，构图上合理留白，营造舒适的视觉呼吸感。全文稿任何地方都严禁放任何徽标（Logo）。
底版强制要求：全部演示文稿的底版必须统一为纯白色（#FFFFFF），底版区域禁止出现任何底纹、网格线、辅助线、水印、杂色及各类装饰性背景元素，保证底版干净无杂质。
配图、图标等须使用品牌色，即绿色（RGB 0-176-80），橙色（RGB 255-153-0），蓝色（RGB 51-153-255），可适当加入浅绿色（RGB 97-209-116）、浅橙色（RGB 255-194-102）、浅蓝色（RGB 153-204-255），严禁使用粉色系颜色。"""

FIRA_SKIP_CHAPTER_NAMES = {"训战启程", "训战总结、训战输出", "满意度调查"}
PROMPT_TITLE_RE = re.compile(r"文稿题目：([^\n\r]+)")
NLM_API_DELAY_SECONDS = 15.0
NLM_STATUS_API_DELAY_SECONDS = 60.0
NLM_RATE_LIMIT_MAX_DELAY_SECONDS = 240.0
NLM_RATE_LIMIT_MAX_RETRIES = 4
NLM_RATE_LIMIT_LOCK_PATH = Path(tempfile.gettempdir()) / "nlm-course-slides.rate-limit.lock"
NLM_STATUS_RATE_LIMIT_LOCK_PATH = Path(tempfile.gettempdir()) / "nlm-course-slides.status-rate-limit.lock"
DEFAULT_NOTEBOOK_SHARE_EMAIL = ""
DEFAULT_NOTEBOOK_SHARE_EMAILS: list[str] = [
    "wlydsydmhmdsyd@gmail.com",
    "whatmatthew697@gmail.com",
    "dababyturnsintoaconvertible@gmail.com",
]
DEFAULT_NOTEBOOK_SHARE_ROLE = "editor"


def _lock_file(handle: Any) -> None:
    if fcntl is None:
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: Any) -> None:
    if fcntl is None:
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass
class Section:
    id: str
    order: int
    title: str
    content: str
    slug: str
    output_name: str
    focus: str | None = None
    resource_title: str | None = None


@dataclass
class CourseManifest:
    course_title: str
    sections: list[Section]
    source_path: str


@dataclass
class RecoverySectionState:
    section_id: str
    section_title: str
    resource_title: str
    output_name: str
    status: str
    source_id: str | None
    artifact_id: str | None
    artifact_status: str | None
    local_output_exists: bool
    local_sidecar_exists: bool
    resume_action: str
    notes: list[str]


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str, *, fallback: str = "section") -> str:
    text = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE).strip().lower()
    text = re.sub(r"[-\s]+", "-", text, flags=re.UNICODE).strip("-")
    return text or fallback


def sanitize_filename(value: str, *, fallback: str = "section") -> str:
    text = re.sub(r'[\\/:*?"<>|]+', "-", value, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text, flags=re.UNICODE).strip(" .")
    return text or fallback


def default_output_name(section_id: str, title: str) -> str:
    clean_title = sanitize_filename(title)
    if title_starts_with_section_id(title, section_id):
        return f"{clean_title}.pptx"
    return f"{section_id} {clean_title}.pptx"


def default_resource_title(section_id: str, title: str) -> str:
    if title_starts_with_section_id(title, section_id):
        return title.strip()
    return f"{section_id} {title.strip()}"


def extract_section_id(title: str) -> str | None:
    match = SECTION_ID_PREFIX_RE.match(title or "")
    if not match:
        return None
    return match.group(1).rstrip(".-、)")


def title_starts_with_section_id(title: str, section_id: str) -> bool:
    extracted = extract_section_id(title)
    return bool(extracted and extracted == section_id)


def strip_section_prefix(title: str) -> str:
    cleaned = title.strip()
    previous = None
    while cleaned and cleaned != previous:
        previous = cleaned
        cleaned = SECTION_PREFIX_RE.sub("", cleaned).strip()
    return cleaned or title.strip()


def build_focus_prompt(title: str) -> str:
    return FOCUS_PROMPT_TEMPLATE.format(section_title=strip_section_prefix(title))


def parse_first_uuid(text: str) -> str:
    match = UUID_RE.search(text)
    if not match:
        raise RuntimeError(f"Could not find UUID in command output:\n{text}")
    return match.group(0)


def log_message(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def safe_print_json(payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    try:
        sys.stdout.write(text)
        sys.stdout.write("\n")
        sys.stdout.flush()
    except UnicodeEncodeError:
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            fallback_encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            sys.stdout.write(text.encode(fallback_encoding, errors="replace").decode(fallback_encoding))
            sys.stdout.write("\n")
            sys.stdout.flush()
            return
        buffer.write(f"{text}\n".encode("utf-8", errors="replace"))
        buffer.flush()


def configure_nlm_api_delay(delay_seconds: float) -> None:
    global NLM_API_DELAY_SECONDS
    NLM_API_DELAY_SECONDS = max(0.0, delay_seconds)


def _load_profile_auth(profile: str | None = None) -> Any:
    from notebooklm_tools.core.auth import AuthManager

    auth = AuthManager(profile) if profile else AuthManager()
    return auth.load_profile()


@contextmanager
def notebooklm_client(profile: str | None = None) -> Iterator[Any]:
    from notebooklm_tools.core.client import NotebookLMClient

    saved_profile = _load_profile_auth(profile)
    with NotebookLMClient(
        cookies=saved_profile.cookies,
        csrf_token=saved_profile.csrf_token or "",
        session_id=saved_profile.session_id or "",
        build_label=saved_profile.build_label or "",
    ) as client:
        yield client


def _slide_deck_format_code(deck_format: str) -> int:
    from notebooklm_tools.core import constants

    normalized = str(deck_format or "").strip().lower()
    return constants.SLIDE_DECK_FORMATS.get_code(normalized or "detailed_deck")


def _slide_deck_length_code(length: str) -> int:
    from notebooklm_tools.core import constants

    normalized = str(length or "").strip().lower()
    return constants.SLIDE_DECK_LENGTHS.get_code(normalized or "default")


def _is_nlm_command(args: list[str]) -> bool:
    return bool(args) and Path(args[0]).name == "nlm"


def _subprocess_args(args: list[str]) -> list[str]:
    if os.name != "nt" or not _is_nlm_command(args):
        return args
    nlm_path = shutil.which("nlm") or shutil.which("nlm.cmd")
    if nlm_path:
        return [nlm_path, *args[1:]]
    return args


def _read_rate_limit_state(handle: Any, *, base_delay: float) -> dict[str, float]:
    handle.seek(0)
    raw_value = handle.read().strip()
    if not raw_value:
        return {"last_started_at": 0.0, "current_delay": base_delay}
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        try:
            return {
                "last_started_at": float(raw_value),
                "current_delay": base_delay,
            }
        except ValueError:
            return {"last_started_at": 0.0, "current_delay": base_delay}
    if isinstance(payload, (int, float)):
        return {
            "last_started_at": float(payload),
            "current_delay": base_delay,
        }
    if not isinstance(payload, dict):
        return {"last_started_at": 0.0, "current_delay": base_delay}
    return {
        "last_started_at": float(payload.get("last_started_at") or 0.0),
        "current_delay": max(
            base_delay,
            float(payload.get("current_delay") or base_delay),
        ),
    }


def _write_rate_limit_state(handle: Any, state: dict[str, float]) -> None:
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps(state, ensure_ascii=False))
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())


def _rate_limit_delay_markers() -> list[str]:
    return [
        "rate limited",
        "rate limit",
        "api error (code 8)",
        "wait a few minutes before retrying",
        "too many requests",
    ]


def _is_rate_limit_error(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _rate_limit_delay_markers())


def _is_transient_network_error(text: str) -> bool:
    lowered = text.lower()
    markers = [
        "unexpected_eof_while_reading",
        "connecterror",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "temporary failure",
        "timed out",
        "timeout",
        "503 service unavailable",
        "remote end closed connection",
    ]
    return any(marker in lowered for marker in markers)


def _is_status_poll_command(args: list[str]) -> bool:
    return args[:4] == ["nlm", "studio", "status", "--json"] or args[:4] == [
        "nlm",
        "status",
        "artifacts",
        "--json",
    ] or args[:4] == ["nlm", "list", "artifacts", "--json"]


def _rate_limit_bucket(args: list[str]) -> tuple[Path, float]:
    if _is_status_poll_command(args):
        return NLM_STATUS_RATE_LIMIT_LOCK_PATH, NLM_STATUS_API_DELAY_SECONDS
    return NLM_RATE_LIMIT_LOCK_PATH, NLM_API_DELAY_SECONDS


def _rate_limit_nlm_command(args: list[str]) -> None:
    if not _is_nlm_command(args) or NLM_API_DELAY_SECONDS <= 0:
        return

    lock_path, base_delay = _rate_limit_bucket(args)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        _lock_file(handle)
        state = _read_rate_limit_state(handle, base_delay=base_delay)
        last_started_at = state["last_started_at"]
        current_delay = max(base_delay, state["current_delay"])
        now = time.time()
        delay = max(0.0, last_started_at + current_delay - now)
        if delay > 0:
            log_message(
                f"[rate-limit] wait {delay:.1f}s before NotebookLM API call: {shlex.join(args)}"
            )
            time.sleep(delay)
        state["last_started_at"] = time.time()
        _write_rate_limit_state(handle, state)
        _unlock_file(handle)


def _update_rate_limit_delay(
    args: list[str],
    *,
    multiplier: float | None = None,
    reset: bool = False,
) -> float:
    lock_path, base_delay = _rate_limit_bucket(args)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        _lock_file(handle)
        state = _read_rate_limit_state(handle, base_delay=base_delay)
        if reset:
            state["current_delay"] = base_delay
        elif multiplier is not None:
            state["current_delay"] = min(
                NLM_RATE_LIMIT_MAX_DELAY_SECONDS,
                max(base_delay, state["current_delay"]) * multiplier,
            )
        _write_rate_limit_state(handle, state)
        _unlock_file(handle)
        return state["current_delay"]


def run_cmd(
    args: list[str],
    *,
    dry_run: bool = False,
    cwd: str | None = None,
    capture_output: bool = True,
    check: bool = True,
) -> str:
    command = shlex.join(args)
    if dry_run:
        log_message(f"[dry-run] {command}")
        return ""

    attempts = 0
    while True:
        _rate_limit_nlm_command(args)
        completed = subprocess.run(
            _subprocess_args(args),
            cwd=cwd,
            text=True,
            capture_output=capture_output,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode == 0:
            if _is_nlm_command(args):
                _update_rate_limit_delay(args, reset=True)
            return stdout if capture_output else ""

        error_text = (
            f"Command failed with exit code {completed.returncode}: {command}\n"
            f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )
        if _is_nlm_command(args) and _is_rate_limit_error(error_text) and attempts < NLM_RATE_LIMIT_MAX_RETRIES:
            attempts += 1
            new_delay = _update_rate_limit_delay(args, multiplier=2.0)
            log_message(
                f"[rate-limit] NotebookLM rate limit on attempt {attempts} for {command}; "
                f"increase shared delay to {new_delay:.1f}s and retry"
            )
            continue

        if check:
            raise RuntimeError(error_text)
        return stdout if capture_output else ""


def ensure_authenticated(*, profile: str | None = None, dry_run: bool = False) -> None:
    if dry_run:
        return

    attempts = 0
    while True:
        try:
            with notebooklm_client(profile) as client:
                client.list_notebooks()
            return
        except Exception as exc:
            error_text = str(exc)
            if _is_transient_network_error(error_text) and attempts < 2:
                attempts += 1
                log_message(
                    f"[auth-check] transient NotebookLM network error on attempt {attempts} "
                    f"for profile {profile or 'default'}; retry in 3s"
                )
                time.sleep(3)
                continue
            raise RuntimeError(
                f"NotebookLM authentication failed for profile {profile or 'default'}: {error_text}"
            ) from exc


def create_notebook(
    title: str,
    *,
    profile: str | None = None,
    dry_run: bool = False,
) -> str:
    if dry_run:
        return "dry-run-notebook-id"
    with notebooklm_client(profile) as client:
        notebook = client.create_notebook(title)
    notebook_id = str(getattr(notebook, "id", "") or "").strip()
    if not notebook_id:
        raise RuntimeError(f"NotebookLM did not return a notebook ID for '{title}'.")
    return notebook_id


def get_notebooklm_base_url() -> str:
    return os.environ.get("NOTEBOOKLM_BASE_URL", "https://notebooklm.google.com").rstrip("/")


def build_notebook_url(notebook_id: str) -> str:
    return f"{get_notebooklm_base_url()}/notebook/{notebook_id}"


def share_notebook_with_collaborator(
    notebook_id: str,
    *,
    email: str | None,
    role: str = DEFAULT_NOTEBOOK_SHARE_ROLE,
    profile: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    clean_email = str(email or "").strip()
    if not clean_email:
        return None

    clean_role = str(role or DEFAULT_NOTEBOOK_SHARE_ROLE).strip().lower() or DEFAULT_NOTEBOOK_SHARE_ROLE
    if clean_role not in {"viewer", "editor"}:
        raise RuntimeError(f"Unsupported collaborator role: {clean_role}")

    if not dry_run:
        with notebooklm_client(profile) as client:
            shared = client.add_collaborator(
                notebook_id,
                clean_email,
                role=clean_role,
            )
        if not shared:
            raise RuntimeError(
                f"NotebookLM did not confirm collaborator sharing for {clean_email}."
            )
    return {
        "email": clean_email,
        "role": clean_role,
        "status": "shared" if not dry_run else "dry-run",
    }


def add_text_source(
    notebook_id: str,
    title: str,
    text: str,
    *,
    profile: str | None = None,
    dry_run: bool = False,
    wait_timeout: float = 600.0,
) -> str:
    if dry_run:
        return "dry-run-source-id"
    with notebooklm_client(profile) as client:
        source_result = client.add_text_source(
            notebook_id,
            text,
            title=title,
            wait=True,
            wait_timeout=wait_timeout,
        )
    source_id = str((source_result or {}).get("id") or "").strip()
    if not source_id:
        raise RuntimeError(f"NotebookLM did not return a source ID for '{title}'.")
    return source_id


def create_slide_deck(
    notebook_id: str,
    *,
    source_id: str,
    focus: str | None,
    language: str,
    deck_format: str,
    length: str,
    profile: str | None = None,
    dry_run: bool = False,
) -> str:
    if dry_run:
        return "dry-run-artifact-id"
    with notebooklm_client(profile) as client:
        artifact = client.create_slide_deck(
            notebook_id,
            source_ids=[source_id],
            format_code=_slide_deck_format_code(deck_format),
            length_code=_slide_deck_length_code(length),
            language=language,
            focus_prompt=focus or "",
        )
    artifact_id = str((artifact or {}).get("artifact_id") or "").strip()
    if not artifact_id:
        raise RuntimeError(
            f"NotebookLM did not return an artifact ID while creating slides for source {source_id}."
        )
    return artifact_id


def wait_for_artifact(
    notebook_id: str,
    artifact_id: str,
    *,
    profile: str | None = None,
    dry_run: bool = False,
    timeout_seconds: float = 1800.0,
    poll_interval: float = 30.0,
) -> dict[str, Any]:
    if dry_run:
        return {"id": artifact_id, "status": "completed", "type": "slide_deck"}

    deadline = time.time() + timeout_seconds
    transient_failures = 0

    while time.time() < deadline:
        try:
            with notebooklm_client(profile) as client:
                payload = client.get_studio_status(notebook_id)
        except Exception as exc:
            error_text = str(exc)
            if _is_transient_network_error(error_text):
                transient_failures += 1
                log_message(
                    f"[artifact {artifact_id}] status query temporarily unavailable "
                    f"(consecutive={transient_failures}), retry in {poll_interval} seconds"
                )
                time.sleep(poll_interval)
                continue
            raise RuntimeError(
                f"Status query returned an error for artifact {artifact_id}: {error_text}"
            ) from exc

        transient_failures = 0
        if not isinstance(payload, list):
            raise RuntimeError(
                f"Unexpected status payload for artifact {artifact_id}: {json.dumps(payload, ensure_ascii=False)}"
            )

        for item in payload:
            # NotebookLM status payloads may expose either `id` or `artifact_id`.
            candidate_id = str(item.get("id") or item.get("artifact_id") or "")
            if candidate_id != artifact_id:
                continue
            status = str(item.get("status") or "").lower()
            if status == "completed":
                return item
            if status in {"failed", "cancelled", "canceled"}:
                raise RuntimeError(
                    f"Artifact {artifact_id} ended with status '{status}': {json.dumps(item, ensure_ascii=False)}"
                )
            break
        time.sleep(poll_interval)

    raise TimeoutError(f"Timed out waiting for artifact {artifact_id} after {timeout_seconds} seconds.")


def rename_artifact(
    artifact_id: str,
    new_title: str,
    *,
    profile: str | None = None,
    dry_run: bool = False,
) -> None:
    if dry_run:
        return
    with notebooklm_client(profile) as client:
        renamed = client.rename_studio_artifact(artifact_id, new_title)
    if not renamed:
        raise RuntimeError(f"NotebookLM did not confirm rename for artifact {artifact_id}.")


def download_slide_deck(
    notebook_id: str,
    artifact_id: str,
    output_path: Path,
    *,
    profile: str | None = None,
    file_format: str = "pptx",
    retry_attempts: int = 3,
    retry_delay_seconds: float = 5.0,
    dry_run: bool = False,
) -> None:
    if dry_run:
        args = [
            "nlm",
            "download",
            "slide-deck",
            "--id",
            artifact_id,
            "--output",
            str(output_path),
            "--format",
            file_format,
            notebook_id,
        ]
        if profile:
            args.extend(["--profile", profile])
        run_cmd(args, dry_run=True)
        return

    from notebooklm_tools.core.auth import AuthManager
    from notebooklm_tools.core.client import NotebookLMClient

    attempts = 0
    while True:
        try:
            auth = AuthManager(profile) if profile else AuthManager()
            saved_profile = auth.load_profile()
            with NotebookLMClient(
                cookies=saved_profile.cookies,
                csrf_token=saved_profile.csrf_token or "",
                session_id=saved_profile.session_id or "",
                build_label=saved_profile.build_label or "",
            ) as client:
                client.download_slide_deck(
                    notebook_id,
                    str(output_path),
                    artifact_id=artifact_id,
                    file_format=file_format,
                )
            return
        except RuntimeError as exc:
            if attempts >= retry_attempts:
                raise
            attempts += 1
            log_message(
                f"[download {artifact_id}] download failed on attempt {attempts}: {exc}. "
                f"Retry in {retry_delay_seconds:.0f}s"
            )
            time.sleep(retry_delay_seconds)
        except Exception as exc:
            if attempts >= retry_attempts:
                raise RuntimeError(str(exc)) from exc
            attempts += 1
            log_message(
                f"[download {artifact_id}] download failed on attempt {attempts}: {exc}. "
                f"Retry in {retry_delay_seconds:.0f}s"
            )
            time.sleep(retry_delay_seconds)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_json_output(output: str, *, command: str) -> Any:
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from {command}:\n{output}") from exc


def _first_non_empty(mapping: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _coerce_title(item: dict[str, Any]) -> str:
    value = _first_non_empty(
        item,
        [
            "title",
            "name",
            "display_name",
            "displayName",
            "artifact_title",
            "artifactTitle",
            "source_title",
            "sourceTitle",
        ],
    )
    if value in (None, "", [], {}):
        custom_instructions = str(item.get("custom_instructions") or "").strip()
        match = PROMPT_TITLE_RE.search(custom_instructions)
        if match:
            value = match.group(1).strip()
    return str(value or "").strip()


def _coerce_id(item: dict[str, Any]) -> str | None:
    value = _first_non_empty(item, ["id", "artifact_id", "artifactId", "source_id", "sourceId", "uuid"])
    if value is None:
        return None
    return str(value).strip() or None


def _coerce_status(item: dict[str, Any]) -> str | None:
    value = _first_non_empty(item, ["status", "state", "artifact_status", "artifactStatus"])
    if value is None:
        return None
    return str(value).strip().lower() or None


def _coerce_source_ids(item: dict[str, Any]) -> list[str]:
    raw = _first_non_empty(item, ["source_ids", "sourceIds", "sources"])
    if raw is None:
        return []
    if isinstance(raw, list):
        source_ids: list[str] = []
        for entry in raw:
            if isinstance(entry, str):
                source_ids.append(entry)
                continue
            if isinstance(entry, dict):
                source_id = _coerce_id(entry)
                if source_id:
                    source_ids.append(source_id)
        return source_ids
    if isinstance(raw, str):
        return [raw]
    return []


def list_notebook_sources(
    notebook_id: str,
    *,
    profile: str | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    if dry_run:
        return []
    with notebooklm_client(profile) as client:
        payload = client.get_notebook_sources_with_types(notebook_id)
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected source list payload: {json.dumps(payload, ensure_ascii=False)}")
    return payload


def list_notebook_artifacts(
    notebook_id: str,
    *,
    profile: str | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    if dry_run:
        return []
    with notebooklm_client(profile) as client:
        payload = client.get_studio_status(notebook_id)
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected artifact list payload: {json.dumps(payload, ensure_ascii=False)}")
    return payload


def match_sections_to_notebook_state(
    manifest: CourseManifest,
    *,
    notebook_id: str,
    output_dir: Path,
    profile: str | None = None,
    dry_run: bool = False,
) -> list[RecoverySectionState]:
    sources = list_notebook_sources(notebook_id, profile=profile, dry_run=dry_run)
    artifacts = list_notebook_artifacts(notebook_id, profile=profile, dry_run=dry_run)

    source_matches: dict[str, list[dict[str, Any]]] = {}
    for item in sources:
        source_title = _coerce_title(item)
        if source_title:
            source_matches.setdefault(source_title, []).append(item)

    artifact_matches: dict[str, list[dict[str, Any]]] = {}
    for item in artifacts:
        artifact_title = _coerce_title(item)
        if artifact_title:
            artifact_matches.setdefault(artifact_title, []).append(item)

    states: list[RecoverySectionState] = []
    for section in manifest.sections:
        resource_title = section.resource_title or default_resource_title(section.id, section.title)
        output_name = section.output_name
        output_stem = Path(output_name).stem
        output_path = output_dir / output_name
        sidecar_path = output_path.with_suffix(".slide.json")
        notes: list[str] = []

        matched_artifacts = list(artifact_matches.get(output_stem, []))
        if not matched_artifacts:
            stripped_title = strip_section_prefix(section.title)
            matched_artifacts = list(artifact_matches.get(stripped_title, []))
        matched_sources = list(source_matches.get(resource_title, []))
        if not matched_sources and output_stem != resource_title:
            matched_sources = list(source_matches.get(output_stem, []))

        local_output_exists = output_path.exists()
        local_sidecar_exists = sidecar_path.exists()

        if len(matched_artifacts) > 1:
            notes.append(f"Multiple artifacts matched title '{output_stem}'")
        if len(matched_sources) > 1:
            notes.append(f"Multiple sources matched title '{resource_title}'")

        artifact = matched_artifacts[0] if len(matched_artifacts) == 1 else None
        source = matched_sources[0] if len(matched_sources) == 1 else None

        artifact_id = _coerce_id(artifact) if artifact else None
        artifact_status = _coerce_status(artifact) if artifact else None
        source_id = _coerce_id(source) if source else None

        if local_output_exists:
            status = "completed_local"
            resume_action = "skip"
        elif notes:
            status = "ambiguous"
            resume_action = "manual_review"
        elif artifact_id and artifact_status == "completed":
            status = "completed_remote_needs_download"
            resume_action = "download_only"
        elif artifact_id and artifact_status in {"running", "processing", "pending", "queued", "in_progress"}:
            status = "running_remote"
            resume_action = "wait_and_finalize"
        elif artifact_id and artifact_status in {"failed", "cancelled", "canceled"}:
            status = "failed_remote"
            resume_action = "create_from_existing_source" if source_id else "upload_and_create"
        elif source_id:
            status = "source_only"
            resume_action = "create_from_existing_source"
        elif artifact_id:
            status = "unknown"
            resume_action = "manual_review"
            notes.append("Artifact exists but status could not be classified")
        else:
            status = "not_started"
            resume_action = "upload_and_create"

        states.append(
            RecoverySectionState(
                section_id=section.id,
                section_title=section.title,
                resource_title=resource_title,
                output_name=output_name,
                status=status,
                source_id=source_id,
                artifact_id=artifact_id,
                artifact_status=artifact_status,
                local_output_exists=local_output_exists,
                local_sidecar_exists=local_sidecar_exists,
                resume_action=resume_action,
                notes=notes,
            )
        )
    return states


def find_section(manifest: CourseManifest, section_id: str) -> Section:
    for section in manifest.sections:
        if section.id == section_id:
            return section
    raise KeyError(f"Section '{section_id}' was not found in {manifest.source_path}")


def manifest_to_dict(manifest: CourseManifest) -> dict[str, Any]:
    return {
        "course_title": manifest.course_title,
        "source_path": manifest.source_path,
        "sections": [asdict(section) for section in manifest.sections],
    }


def load_course_manifest(path: str | Path) -> CourseManifest:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Manifest does not exist: {source}")

    suffix = source.suffix.lower()
    if suffix == ".json":
        data = json.loads(source.read_text(encoding="utf-8"))
        return _normalize_manifest(data, source)
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError(
                "YAML manifest support requires PyYAML. Install it or use JSON/Markdown."
            ) from exc
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
        return _normalize_manifest(data, source)
    if suffix == ".md":
        return _load_markdown_manifest(source)
    raise RuntimeError(
        f"Unsupported manifest extension '{suffix}'. Use .json, .yaml, .yml, or .md."
    )


def _normalize_manifest(data: Any, source: Path) -> CourseManifest:
    if not isinstance(data, dict):
        raise RuntimeError(f"Manifest root must be an object: {source}")

    if isinstance(data.get("course_structure"), list):
        return _load_fira_course_structure_manifest(data, source)

    if isinstance(data.get("chapters"), list):
        return _load_fira_course_manifest(data, source)

    course_title = str(data.get("course_title") or data.get("title") or source.stem).strip()
    raw_sections = data.get("sections") or data.get("chapters")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise RuntimeError(f"Manifest must contain a non-empty sections list: {source}")

    sections: list[Section] = []
    counter = 0

    def visit(items: list[Any], path_parts: list[int]) -> None:
        nonlocal counter
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise RuntimeError(f"Each section must be an object: {source}")

            next_path = [*path_parts, index]
            section_id = str(item.get("id") or ".".join(str(part) for part in next_path))
            title = str(item.get("title") or item.get("name") or "").strip()
            if not title:
                raise RuntimeError(f"Section {section_id} is missing a title in {source}")

            content = str(
                item.get("content")
                or item.get("text")
                or item.get("body")
                or item.get("markdown")
                or ""
            ).strip()
            focus = item.get("focus") or item.get("prompt")
            resource_title = item.get("resource_title") or item.get("source_title")
            output_name = item.get("output_name") or item.get("filename")

            if content:
                counter += 1
                slug = str(item.get("slug") or slugify(title, fallback=f"section-{counter}"))
                sections.append(
                    Section(
                        id=section_id,
                        order=counter,
                        title=title,
                        content=content,
                        slug=slug,
                        output_name=str(output_name or default_output_name(section_id, title)),
                        focus=str(focus).strip() if focus else None,
                        resource_title=str(resource_title).strip() if resource_title else None,
                    )
                )

            children = item.get("sections") or item.get("children") or []
            if children:
                if not isinstance(children, list):
                    raise RuntimeError(
                        f"Section {section_id} has non-list children in {source}"
                    )
                visit(children, next_path)

    visit(raw_sections, [])
    if not sections:
        raise RuntimeError(f"No leaf sections with content were found in {source}")
    return CourseManifest(
        course_title=course_title,
        sections=sections,
        source_path=str(source.resolve()),
    )


def _parse_embedded_json(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return value
    return value


def _html_to_text(raw_html: str) -> str:
    text = raw_html
    text = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|h[1-6])>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_course_structure_block_text(block: dict[str, Any]) -> str:
    definition = _parse_embedded_json(block.get("definition"))
    if isinstance(definition, str):
        return _html_to_text(definition)
    if isinstance(definition, dict):
        for key in ("text", "html", "content", "body"):
            value = definition.get(key)
            if isinstance(value, str) and value.strip():
                return _html_to_text(value)
    return ""


def _load_fira_course_structure_manifest(data: dict[str, Any], source: Path) -> CourseManifest:
    course_info = data.get("course_info_statistics") or {}
    course_title = str(
        course_info.get("display_name") or data.get("name") or data.get("course_title") or source.stem
    ).strip()
    items = data.get("course_structure") or []
    if not isinstance(items, list):
        raise RuntimeError(f"FIRA course_structure must be a list: {source}")

    chapters: list[dict[str, Any]] = []
    sequentials_by_parent: dict[str, list[dict[str, Any]]] = {}
    verticals_by_parent: dict[str, list[dict[str, Any]]] = {}
    html_blocks_by_parent: dict[str, list[dict[str, Any]]] = {}

    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = _parse_embedded_json(item.get("metadata"))
        if not isinstance(metadata, dict):
            metadata = {}
        prepared = dict(item)
        prepared["_parent"] = str(metadata.get("parent") or "").strip()
        category = str(item.get("category") or "").strip()
        parent = prepared["_parent"]
        if category == "chapter":
            chapters.append(prepared)
        elif category == "sequential" and parent:
            sequentials_by_parent.setdefault(parent, []).append(prepared)
        elif category == "vertical" and parent:
            verticals_by_parent.setdefault(parent, []).append(prepared)
        elif category == "html" and parent:
            html_blocks_by_parent.setdefault(parent, []).append(prepared)

    def ordered(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(entries, key=lambda entry: str(entry.get("block_order") or ""))

    sections: list[Section] = []
    counter = 0

    for chapter in ordered(chapters):
        chapter_name = str(chapter.get("name") or "").strip()
        if chapter_name in FIRA_SKIP_CHAPTER_NAMES:
            continue

        chapter_location = str(chapter.get("block_location") or "").strip()
        chapter_sections = ordered(sequentials_by_parent.get(chapter_location, []))
        for index, sequential in enumerate(chapter_sections):
            if index == 0:
                continue

            title = str(sequential.get("name") or "").strip()
            if not title:
                continue

            vertical_entries = []
            sequential_location = str(sequential.get("block_location") or "").strip()
            for vertical in ordered(verticals_by_parent.get(sequential_location, [])):
                blocks = []
                vertical_location = str(vertical.get("block_location") or "").strip()
                for block in ordered(html_blocks_by_parent.get(vertical_location, [])):
                    if block.get("name") != "文字讲解":
                        continue
                    text = _extract_course_structure_block_text(block)
                    if text:
                        blocks.append({"name": block.get("name"), "text": text})
                if blocks:
                    vertical_entries.append({"name": vertical.get("name"), "blocks": blocks})

            content = extract_fira_section_content({"verticals": vertical_entries})
            if not content:
                continue

            counter += 1
            section_id = extract_section_id(title) or str(counter)
            sections.append(
                Section(
                    id=section_id,
                    order=counter,
                    title=title,
                    content=content,
                    slug=slugify(strip_section_prefix(title), fallback=f"section-{counter}"),
                    output_name=default_output_name(section_id, title),
                    resource_title=default_resource_title(section_id, title),
                )
            )

    if not sections:
        raise RuntimeError(f"No slide-generation sections were found in {source}")

    return CourseManifest(
        course_title=course_title,
        sections=sections,
        source_path=str(source.resolve()),
    )


def _load_fira_course_manifest(data: dict[str, Any], source: Path) -> CourseManifest:
    course_title = str(data.get("name") or data.get("course_title") or source.stem).strip()
    chapters = data.get("chapters") or []
    if not isinstance(chapters, list):
        raise RuntimeError(f"FIRA course chapters must be a list: {source}")

    sections: list[Section] = []
    counter = 0

    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        chapter_name = str(chapter.get("name") or "").strip()
        if chapter_name in FIRA_SKIP_CHAPTER_NAMES:
            continue

        chapter_sections = chapter.get("sections") or []
        if not isinstance(chapter_sections, list):
            continue

        for index, section_data in enumerate(chapter_sections):
            if not isinstance(section_data, dict):
                continue
            if index == 0:
                continue

            title = str(section_data.get("name") or "").strip()
            if not title:
                continue

            content = extract_fira_section_content(section_data)
            if not content:
                continue

            counter += 1
            section_id = extract_section_id(title) or str(counter)
            sections.append(
                Section(
                    id=section_id,
                    order=counter,
                    title=title,
                    content=content,
                    slug=slugify(strip_section_prefix(title), fallback=f"section-{counter}"),
                    output_name=default_output_name(section_id, title),
                    resource_title=default_resource_title(section_id, title),
                )
            )

    if not sections:
        raise RuntimeError(f"No slide-generation sections were found in {source}")

    return CourseManifest(
        course_title=course_title,
        sections=sections,
        source_path=str(source.resolve()),
    )


def extract_fira_section_content(section_data: dict[str, Any]) -> str:
    voiced_slide_texts: list[str] = []
    summary_texts: list[str] = []
    teaching_texts: list[str] = []
    fallback_texts: list[str] = []

    for vertical in section_data.get("verticals") or []:
        if not isinstance(vertical, dict):
            continue
        vertical_name = str(vertical.get("name") or "").strip()
        blocks = vertical.get("blocks") or []
        if not isinstance(blocks, list):
            continue

        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_name = str(block.get("name") or "").strip()
            text = str(block.get("text") or "").strip()
            if not text:
                continue

            if vertical_name == "赋能内容" and block_name == "有声幻灯片":
                voiced_slide_texts.append(text)
            elif vertical_name == "赋能内容" and block_name == "本节要点":
                summary_texts.append(text)
            elif "训战" not in vertical_name and block_name in {"文字讲解", "有声幻灯片", "本节要点"}:
                teaching_texts.append(text)
            elif block_name == "文字讲解":
                fallback_texts.append(text)

    texts = voiced_slide_texts or summary_texts or teaching_texts or fallback_texts
    return "\n\n".join(texts).strip()


def _load_markdown_manifest(source: Path) -> CourseManifest:
    lines = source.read_text(encoding="utf-8").splitlines()
    course_title = source.stem
    sections: list[Section] = []
    current_title: str | None = None
    current_lines: list[str] = []
    counter = 0

    def flush() -> None:
        nonlocal counter, current_title, current_lines
        if not current_title:
            return
        content = "\n".join(current_lines).strip()
        if content:
            counter += 1
            section_id = str(counter)
            sections.append(
                Section(
                    id=section_id,
                    order=counter,
                    title=current_title,
                    content=content,
                    slug=slugify(current_title, fallback=f"section-{counter}"),
                    output_name=default_output_name(section_id, current_title),
                )
            )
        current_title = None
        current_lines = []

    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not match:
            if current_title:
                current_lines.append(line)
            continue

        level = len(match.group(1))
        title = match.group(2).strip()
        if level == 1 and title and course_title == source.stem:
            course_title = title
            continue
        if level == 2:
            flush()
            current_title = title
            current_lines = []
            continue
        if current_title:
            current_lines.append(line)

    flush()
    if not sections:
        raise RuntimeError(
            f"Markdown manifest {source} must use '## Section Title' headings with body text."
        )
    return CourseManifest(
        course_title=course_title,
        sections=sections,
        source_path=str(source.resolve()),
    )
