from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from backend.database import (
    StatementUploadDetail,
    purge_expired_audit_logs,
    purge_expired_statement_uploads,
)


@dataclass(frozen=True)
class RetentionPolicy:
    statement_retention_days: int
    audit_log_retention_days: int


def load_retention_policy() -> RetentionPolicy:
    statement_days = int(os.getenv("STATEMENT_RETENTION_DAYS", "0"))
    audit_days = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "365"))
    return RetentionPolicy(
        statement_retention_days=max(statement_days, 0),
        audit_log_retention_days=max(audit_days, 1),
    )


def purge_expired_data(
    *,
    upload_root: Path,
    database_url: str | None = None,
    policy: RetentionPolicy | None = None,
) -> dict[str, int]:
    resolved_policy = policy or load_retention_policy()
    deleted_audit_logs = purge_expired_audit_logs(
        retention_days=resolved_policy.audit_log_retention_days,
        database_url=database_url,
    )

    deleted_uploads: list[StatementUploadDetail] = []
    if resolved_policy.statement_retention_days > 0:
        deleted_uploads = purge_expired_statement_uploads(
            retention_days=resolved_policy.statement_retention_days,
            database_url=database_url,
        )

    deleted_files = 0
    for upload in deleted_uploads:
        for user_dir in upload_root.glob("user-*"):
            candidate = user_dir / upload["stored_file_name"]
            if candidate.exists():
                candidate.unlink(missing_ok=True)
                deleted_files += 1

    return {
        "deleted_audit_logs": deleted_audit_logs,
        "deleted_statement_uploads": len(deleted_uploads),
        "deleted_statement_files": deleted_files,
    }
