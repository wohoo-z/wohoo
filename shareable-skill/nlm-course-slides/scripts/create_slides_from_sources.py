#!/usr/bin/env python3
"""
Create NotebookLM slide artifacts serially from uploaded section sources.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import sys
import time
from pathlib import Path

from common import (
    build_focus_prompt,
    build_notebook_url,
    configure_nlm_api_delay,
    create_slide_deck,
    ensure_authenticated,
    find_section,
    load_course_manifest,
    log_message,
    read_json,
    safe_print_json,
    sanitize_filename,
    utc_timestamp,
    write_json,
)

DEFAULT_CREATE_PROFILE_POOL = [
    {"profile": "default", "email": "wuzhijian1999@gmail.com"},
    {"profile": "worker_wly", "email": "wlydsydmhmdsyd@gmail.com"},
    {"profile": "worker_daba", "email": "dababyturnsintoaconvertible@gmail.com"},
]
DEFAULT_SINGLE_PROFILE = {"profile": "default", "email": ""}
DEFAULT_CREATE_DAILY_LIMIT = 15
CREATE_USAGE_TRACKER_PATH = Path.home() / ".notebooklm-mcp-cli" / "slide-create-usage.json"
CREATE_QUOTA_ERROR_MARKERS = [
    "quota",
    "daily limit",
    "usage limit",
    "limit reached",
    "exceeded your",
    "come back tomorrow",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="Course manifest (.json/.yaml/.md)")
    parser.add_argument("--upload-report", required=True, help="Stage-C upload-report.json")
    parser.add_argument("--retry-failed-from-report", help="Retry only failed creates from a previous create report")
    parser.add_argument("--section-id", action="append", dest="section_ids", help="Only create slides for the specified section ID")
    parser.add_argument("--notebook-id", help="Existing notebook ID or alias")
    parser.add_argument("--output-dir", help="Course output directory")
    parser.add_argument("--report-path", help="Optional create report output path")
    parser.add_argument("--profile", help="NotebookLM profile")
    parser.add_argument(
        "--daily-create-limit",
        type=int,
        default=DEFAULT_CREATE_DAILY_LIMIT,
        help="Estimated per-profile daily slide creation capacity used by the local profile-pool tracker.",
    )
    parser.add_argument(
        "--disable-profile-pool",
        action="store_true",
        help="Do not use the local multi-profile fallback pool when --profile is omitted.",
    )
    parser.add_argument(
        "--profile-pool-item",
        action="append",
        dest="profile_pool_items",
        help="Custom profile-pool entry for shared setups. Format: <profile> or <profile>:<email>. Repeat to add multiple entries.",
    )
    parser.add_argument("--language", default="zh_Hans")
    parser.add_argument("--deck-format", default="detailed_deck")
    parser.add_argument("--length", default="default")
    parser.add_argument("--api-delay-seconds", type=float, default=10.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _load_retry_filter(path: str | None) -> set[str]:
    if not path:
        return set()
    payload = read_json(Path(path).resolve())
    selected = set()
    for item in payload.get("results", []):
        if isinstance(item, dict) and item.get("status") == "failed_create" and isinstance(item.get("section_id"), str):
            selected.add(item["section_id"])
    return selected


def _today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _load_usage_tracker() -> dict[str, object]:
    if not CREATE_USAGE_TRACKER_PATH.exists():
        return {"updated_at": None, "days": {}}
    payload = read_json(CREATE_USAGE_TRACKER_PATH)
    if not isinstance(payload, dict):
        return {"updated_at": None, "days": {}}
    payload.setdefault("days", {})
    return payload


def _save_usage_tracker(payload: dict[str, object]) -> None:
    CREATE_USAGE_TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_json(CREATE_USAGE_TRACKER_PATH, payload)


def _ensure_day_bucket(payload: dict[str, object], day_key: str) -> dict[str, object]:
    days = payload.setdefault("days", {})
    if not isinstance(days, dict):
        payload["days"] = {}
        days = payload["days"]
    day_bucket = days.setdefault(day_key, {"profiles": {}})
    if not isinstance(day_bucket, dict):
        day_bucket = {"profiles": {}}
        days[day_key] = day_bucket
    profiles = day_bucket.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        day_bucket["profiles"] = {}
    return day_bucket


def _profile_state(payload: dict[str, object], day_key: str, profile_name: str) -> dict[str, object]:
    day_bucket = _ensure_day_bucket(payload, day_key)
    profiles = day_bucket["profiles"]
    profile_state = profiles.setdefault(profile_name, {"used": 0, "blocked": False, "last_error": None})
    if not isinstance(profile_state, dict):
        profile_state = {"used": 0, "blocked": False, "last_error": None}
        profiles[profile_name] = profile_state
    profile_state.setdefault("used", 0)
    profile_state.setdefault("blocked", False)
    profile_state.setdefault("last_error", None)
    return profile_state


def _estimate_remaining(used: int, limit: int) -> int:
    return max(0, limit - max(0, int(used)))


def _is_create_quota_error(text: str) -> bool:
    lowered = text.lower()
    if "rate limit" in lowered or "rate limited" in lowered or "resource_exhausted" in lowered:
        return False
    return any(marker in lowered for marker in CREATE_QUOTA_ERROR_MARKERS)


def _is_temporary_create_rate_limit(text: str) -> bool:
    lowered = text.lower()
    return (
        "rate limit" in lowered
        or "rate limited" in lowered
        or "resource_exhausted" in lowered
        or "api error (code 8)" in lowered
        or "userdisplayableerror" in lowered
    )


def _build_profile_pool(args: argparse.Namespace) -> list[dict[str, str]]:
    if args.profile:
        email = next(
            (item["email"] for item in DEFAULT_CREATE_PROFILE_POOL if item["profile"] == args.profile),
            "",
        )
        return [{"profile": args.profile, "email": email}]
    if args.profile_pool_items:
        custom_pool: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw_item in args.profile_pool_items:
            clean_item = str(raw_item or "").strip()
            if not clean_item:
                continue
            if ":" in clean_item:
                profile_name, email = clean_item.split(":", 1)
            else:
                profile_name, email = clean_item, ""
            profile_name = profile_name.strip()
            email = email.strip()
            if not profile_name or profile_name in seen:
                continue
            seen.add(profile_name)
            custom_pool.append({"profile": profile_name, "email": email})
        if custom_pool:
            return custom_pool
    if args.disable_profile_pool:
        return [dict(DEFAULT_SINGLE_PROFILE)]
    return list(DEFAULT_CREATE_PROFILE_POOL)


def _record_profile_success(
    usage_tracker: dict[str, object],
    *,
    day_key: str,
    profile_name: str,
    daily_limit: int,
) -> dict[str, int]:
    state = _profile_state(usage_tracker, day_key, profile_name)
    state["used"] = int(state.get("used") or 0) + 1
    state["blocked"] = False
    state["last_error"] = None
    usage_tracker["updated_at"] = utc_timestamp()
    _save_usage_tracker(usage_tracker)
    used = int(state["used"])
    return {
        "used_estimate": used,
        "remaining_estimate": _estimate_remaining(used, daily_limit),
    }


def _record_profile_exhausted(
    usage_tracker: dict[str, object],
    *,
    day_key: str,
    profile_name: str,
    daily_limit: int,
    error: str,
) -> None:
    state = _profile_state(usage_tracker, day_key, profile_name)
    state["used"] = max(int(state.get("used") or 0), daily_limit)
    state["blocked"] = True
    state["last_error"] = error
    usage_tracker["updated_at"] = utc_timestamp()
    _save_usage_tracker(usage_tracker)


def _select_profile_candidates(
    usage_tracker: dict[str, object],
    *,
    day_key: str,
    profile_pool: list[dict[str, str]],
    daily_limit: int,
) -> list[dict[str, object]]:
    ready: list[dict[str, object]] = []
    exhausted: list[dict[str, object]] = []
    for item in profile_pool:
        state = _profile_state(usage_tracker, day_key, item["profile"])
        used = int(state.get("used") or 0)
        blocked = bool(state.get("blocked"))
        candidate = {
            "profile": item["profile"],
            "email": item.get("email") or "",
            "used_estimate": used,
            "remaining_estimate": _estimate_remaining(used, daily_limit),
            "blocked": blocked,
        }
        if not blocked and candidate["remaining_estimate"] > 0:
            ready.append(candidate)
        elif not blocked:
            exhausted.append(candidate)
    return ready or exhausted


def _profile_usage_summary(
    usage_tracker: dict[str, object],
    *,
    day_key: str,
    profile_pool: list[dict[str, str]],
    daily_limit: int,
) -> list[dict[str, object]]:
    rows = []
    for item in profile_pool:
        state = _profile_state(usage_tracker, day_key, item["profile"])
        used = int(state.get("used") or 0)
        rows.append(
            {
                "profile": item["profile"],
                "email": item.get("email") or "",
                "used_estimate": used,
                "remaining_estimate": _estimate_remaining(used, daily_limit),
                "blocked": bool(state.get("blocked")),
                "last_error": state.get("last_error"),
            }
        )
    return rows


def _create_with_profile_pool(
    *,
    args: argparse.Namespace,
    notebook_id: str,
    source_id: str,
    focus: str,
    usage_tracker: dict[str, object],
    authenticated_profiles: set[str],
    day_key: str,
    profile_pool: list[dict[str, str]],
) -> tuple[str, dict[str, object], list[dict[str, object]]]:
    attempted_profiles: list[dict[str, object]] = []
    candidates = _select_profile_candidates(
        usage_tracker,
        day_key=day_key,
        profile_pool=profile_pool,
        daily_limit=args.daily_create_limit,
    )
    if not candidates:
        raise RuntimeError("No available NotebookLM profiles were configured for slide generation.")

    last_error: Exception | None = None
    for candidate in candidates:
        profile_name = str(candidate["profile"])
        profile_email = str(candidate.get("email") or "")
        if profile_name not in authenticated_profiles:
            ensure_authenticated(profile=profile_name, dry_run=args.dry_run)
            authenticated_profiles.add(profile_name)
        try:
            artifact_id = create_slide_deck(
                notebook_id,
                source_id=source_id,
                focus=focus,
                language=args.language,
                deck_format=args.deck_format,
                length=args.length,
                profile=profile_name,
                dry_run=args.dry_run,
            )
            usage = _record_profile_success(
                usage_tracker,
                day_key=day_key,
                profile_name=profile_name,
                daily_limit=args.daily_create_limit,
            )
            attempted_profiles.append(
                {
                    "profile": profile_name,
                    "email": profile_email,
                    "status": "success",
                    "error": None,
                }
            )
            return (
                artifact_id,
                {
                    "profile": profile_name,
                    "email": profile_email,
                    "used_estimate": usage["used_estimate"],
                    "remaining_estimate": usage["remaining_estimate"],
                },
                attempted_profiles,
            )
        except Exception as exc:
            error_text = str(exc)
            is_quota = _is_create_quota_error(error_text)
            is_temporary_rate_limit = _is_temporary_create_rate_limit(error_text)
            attempted_profiles.append(
                {
                    "profile": profile_name,
                    "email": profile_email,
                    "status": "failed_quota" if is_quota else ("failed_rate_limit" if is_temporary_rate_limit else "failed"),
                    "error": error_text,
                }
            )
            last_error = exc
            if len(profile_pool) > 1 and is_quota:
                _record_profile_exhausted(
                    usage_tracker,
                    day_key=day_key,
                    profile_name=profile_name,
                    daily_limit=args.daily_create_limit,
                    error=error_text,
                )
                continue
            if len(profile_pool) > 1 and is_temporary_rate_limit:
                continue
            raise RuntimeError(
                f"{error_text}\nAttempted profiles: {json.dumps(attempted_profiles, ensure_ascii=False)}"
            ) from exc

    assert last_error is not None
    raise RuntimeError(
        f"All configured NotebookLM profiles appear exhausted for today. "
        f"Attempted profiles: {json.dumps(attempted_profiles, ensure_ascii=False)}"
    ) from last_error


def main() -> int:
    args = build_parser().parse_args()
    if args.api_delay_seconds < 0:
        raise SystemExit("--api-delay-seconds must be at least 0")
    if args.daily_create_limit < 1:
        raise SystemExit("--daily-create-limit must be at least 1")

    configure_nlm_api_delay(args.api_delay_seconds)
    manifest = load_course_manifest(args.manifest)
    output_dir = Path(args.output_dir or sanitize_filename(manifest.course_title)).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    upload_report = read_json(Path(args.upload_report).resolve())
    notebook_id = args.notebook_id or str(upload_report.get("notebook_id") or "").strip()
    if not notebook_id:
        raise SystemExit("Notebook ID is required via --notebook-id or upload-report.json")

    requested_ids = set(args.section_ids or [])
    requested_ids.update(_load_retry_filter(args.retry_failed_from_report))
    usage_tracker = _load_usage_tracker()
    day_key = _today_key()
    profile_pool = _build_profile_pool(args)
    authenticated_profiles: set[str] = set()
    if args.profile:
        ensure_authenticated(profile=args.profile, dry_run=args.dry_run)
        authenticated_profiles.add(args.profile)

    queued_items = []
    for item in upload_report.get("results", []):
        if not isinstance(item, dict):
            continue
        section_id = item.get("section_id")
        if not isinstance(section_id, str):
            continue
        if requested_ids and section_id not in requested_ids:
            continue
        queued_items.append(item)

    eligible_create_count = sum(
        1
        for item in queued_items
        if item.get("status") == "uploaded" and isinstance(item.get("source_id"), str) and str(item.get("source_id")).strip()
    )

    results = []
    failures = 0
    created_count = 0
    for item in queued_items:
        if not isinstance(item, dict):
            continue
        section_id = item.get("section_id")
        if not isinstance(section_id, str):
            continue

        section = find_section(manifest, section_id)
        source_id = item.get("source_id")
        if item.get("status") != "uploaded" or not isinstance(source_id, str) or not source_id.strip():
            results.append(
                {
                    "section_id": section.id,
                    "section_title": section.title,
                    "source_id": source_id if isinstance(source_id, str) else None,
                    "artifact_id": None,
                    "status": "skipped",
                    "profile_used": None,
                    "profile_email": None,
                    "profile_daily_used_estimate": None,
                    "profile_daily_remaining_estimate": None,
                    "attempted_profiles": [],
                    "error": item.get("error"),
                }
            )
            continue

        try:
            log_message(
                f"[create] section {section.id} {section.title}: request slide create"
            )
            artifact_id, profile_result, attempted_profiles = _create_with_profile_pool(
                args=args,
                notebook_id=notebook_id,
                source_id=source_id,
                focus=section.focus or build_focus_prompt(section.title, manifest.course_title),
                usage_tracker=usage_tracker,
                authenticated_profiles=authenticated_profiles,
                day_key=day_key,
                profile_pool=profile_pool,
            )
            created_count += 1
            results.append(
                {
                    "section_id": section.id,
                    "section_title": section.title,
                    "source_id": source_id,
                    "artifact_id": artifact_id,
                    "status": "create_requested",
                    "profile_used": profile_result["profile"],
                    "profile_email": profile_result["email"],
                    "profile_daily_used_estimate": profile_result["used_estimate"],
                    "profile_daily_remaining_estimate": profile_result["remaining_estimate"],
                    "attempted_profiles": attempted_profiles,
                    "error": None,
                }
            )
            log_message(
                f"[create] created {section.id} -> artifact {artifact_id} via {profile_result['profile']}"
            )
        except Exception as exc:
            failures += 1
            results.append(
                {
                    "section_id": section.id,
                    "section_title": section.title,
                    "source_id": source_id,
                    "artifact_id": None,
                    "status": "failed_create",
                    "profile_used": None,
                    "profile_email": None,
                    "profile_daily_used_estimate": None,
                    "profile_daily_remaining_estimate": None,
                    "attempted_profiles": [],
                    "error": str(exc),
                }
            )
            log_message(f"[create] failed {section.id}: {exc}")
        if created_count + failures < eligible_create_count:
            time.sleep(args.api_delay_seconds)

    payload = {
        "manifest": manifest.source_path,
        "course_title": manifest.course_title,
        "output_dir": str(output_dir),
        "notebook_id": notebook_id,
        "notebook_url": str(upload_report.get("notebook_url") or build_notebook_url(notebook_id)),
        "share_result": upload_report.get("share_result"),
        "generated_at": utc_timestamp(),
        "api_delay_seconds": args.api_delay_seconds,
        "daily_create_limit": args.daily_create_limit,
        "profile_pool_enabled": not args.disable_profile_pool and not args.profile,
        "profile_usage_day": day_key,
        "profile_usage_summary": _profile_usage_summary(
            usage_tracker,
            day_key=day_key,
            profile_pool=profile_pool,
            daily_limit=args.daily_create_limit,
        ),
        "estimated_total_remaining_today": sum(
            item["remaining_estimate"]
            for item in _profile_usage_summary(
                usage_tracker,
                day_key=day_key,
                profile_pool=profile_pool,
                daily_limit=args.daily_create_limit,
            )
        ),
        "failures": failures,
        "results": results,
    }
    report_path = Path(args.report_path).resolve() if args.report_path else output_dir / "create-report.json"
    write_json(report_path, payload)
    safe_print_json(payload)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
