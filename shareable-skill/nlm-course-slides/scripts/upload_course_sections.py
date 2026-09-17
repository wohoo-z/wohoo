#!/usr/bin/env python3
"""
Upload course sections to NotebookLM serially and write the stage-C report.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from common import (
    DEFAULT_NOTEBOOK_SHARE_ROLE,
    add_text_source,
    build_notebook_url,
    configure_nlm_api_delay,
    create_notebook,
    default_resource_title,
    ensure_authenticated,
    find_section,
    load_course_manifest,
    log_message,
    read_json,
    safe_print_json,
    sanitize_filename,
    share_notebook_with_collaborator,
    utc_timestamp,
    write_json,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="Course manifest (.json/.yaml/.md)")
    parser.add_argument("--section-list", help="Stage-B section-list.json")
    parser.add_argument("--retry-failed-from-report", help="Retry only failed uploads from a previous upload report")
    parser.add_argument("--section-id", action="append", dest="section_ids", help="Only upload the specified section ID")
    parser.add_argument("--notebook-id", help="Existing notebook ID or alias")
    parser.add_argument("--notebook-title", help="Notebook title when creating a new notebook")
    parser.add_argument("--output-dir", help="Course output directory")
    parser.add_argument("--report-path", help="Optional upload report output path")
    parser.add_argument("--profile", help="NotebookLM profile")
    parser.add_argument("--source-wait-timeout", type=float, default=600.0)
    parser.add_argument("--api-delay-seconds", type=float, default=5.0)
    parser.add_argument(
        "--share-email",
        action="append",
        dest="share_emails",
        help="Invite this collaborator after the notebook is ready. Repeat to invite multiple collaborators.",
    )
    parser.add_argument("--share-role", default=DEFAULT_NOTEBOOK_SHARE_ROLE, help="Collaborator role: viewer or editor")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _resolve_share_emails(args: argparse.Namespace) -> list[str]:
    raw_items = list(args.share_emails or [])
    seen: set[str] = set()
    emails: list[str] = []
    for item in raw_items:
        clean_email = str(item or "").strip()
        if not clean_email or clean_email in seen:
            continue
        seen.add(clean_email)
        emails.append(clean_email)
    return emails


def _load_section_id_filter(args: argparse.Namespace) -> set[str] | None:
    selected: set[str] = set(args.section_ids or [])
    if args.section_list:
        payload = read_json(Path(args.section_list).resolve())
        for item in payload.get("sections", []):
            if isinstance(item, dict) and isinstance(item.get("section_id"), str):
                selected.add(item["section_id"])
    if args.retry_failed_from_report:
        payload = read_json(Path(args.retry_failed_from_report).resolve())
        for item in payload.get("results", []):
            if isinstance(item, dict) and item.get("status") == "failed_upload" and isinstance(item.get("section_id"), str):
                selected.add(item["section_id"])
    return selected or None


def main() -> int:
    args = build_parser().parse_args()
    if args.api_delay_seconds < 0:
        raise SystemExit("--api-delay-seconds must be at least 0")

    configure_nlm_api_delay(args.api_delay_seconds)
    manifest = load_course_manifest(args.manifest)
    output_dir = Path(args.output_dir or sanitize_filename(manifest.course_title)).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    section_id_filter = _load_section_id_filter(args)
    sections = manifest.sections
    if section_id_filter:
        sections = [section for section in sections if section.id in section_id_filter]
    if not sections:
        raise SystemExit("No sections selected for upload")

    ensure_authenticated(profile=args.profile, dry_run=args.dry_run)
    notebook_id = args.notebook_id or create_notebook(
        args.notebook_title or manifest.course_title,
        profile=args.profile,
        dry_run=args.dry_run,
    )
    notebook_action = "reused" if args.notebook_id else "created"
    notebook_url = build_notebook_url(notebook_id)
    log_message(f"[upload] notebook {notebook_action}: {notebook_id}")
    log_message(f"[upload] notebook url: {notebook_url}")
    share_results = []
    for share_email in _resolve_share_emails(args):
        log_message(f"[upload] share notebook with {share_email} as {args.share_role}")
        share_result = share_notebook_with_collaborator(
            notebook_id,
            email=share_email,
            role=args.share_role,
            profile=args.profile,
            dry_run=args.dry_run,
        )
        if share_result:
            share_results.append(share_result)
            log_message(
                f"[upload] share result for {share_email}: {share_result.get('status') or 'ok'}"
            )

    results = []
    failures = 0
    for index, section in enumerate(sections):
        resource_title = section.resource_title or default_resource_title(section.id, section.title)
        log_message(
            f"[upload] ({index + 1}/{len(sections)}) {section.id} {section.title}"
        )
        try:
            source_id = add_text_source(
                notebook_id,
                resource_title,
                section.content,
                profile=args.profile,
                dry_run=args.dry_run,
                wait_timeout=args.source_wait_timeout,
            )
            results.append(
                {
                    "section_id": section.id,
                    "section_title": section.title,
                    "resource_title": resource_title,
                    "status": "uploaded",
                    "source_id": source_id,
                    "error": None,
                }
            )
            log_message(f"[upload] uploaded {section.id} -> source {source_id}")
        except Exception as exc:
            failures += 1
            results.append(
                {
                    "section_id": section.id,
                    "section_title": section.title,
                    "resource_title": resource_title,
                    "status": "failed_upload",
                    "source_id": None,
                    "error": str(exc),
                }
            )
            log_message(f"[upload] failed {section.id}: {exc}")
        if index < len(sections) - 1:
            time.sleep(args.api_delay_seconds)

    payload = {
        "manifest": manifest.source_path,
        "course_title": manifest.course_title,
        "output_dir": str(output_dir),
        "notebook_id": notebook_id,
        "notebook_title": args.notebook_title or manifest.course_title,
        "notebook_url": notebook_url,
        "notebook_action": notebook_action,
        "share_result": share_results[0] if share_results else None,
        "share_results": share_results,
        "generated_at": utc_timestamp(),
        "api_delay_seconds": args.api_delay_seconds,
        "total_sections": len(sections),
        "failures": failures,
        "results": results,
    }
    report_path = Path(args.report_path).resolve() if args.report_path else output_dir / "upload-report.json"
    write_json(report_path, payload)
    safe_print_json(payload)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
