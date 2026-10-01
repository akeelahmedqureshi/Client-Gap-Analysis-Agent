"""Audit trail of security-relevant actions (logins, user/role changes, uploads, runs,
approvals, connections, access changes). Entries are added to the caller's session and
committed with the action they describe."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from cip.db.models import AuditLog, User


def client_ip(request: Request | None) -> str | None:
    # Behind nginx, uvicorn's proxy-headers handling (trusted 127.0.0.1) sets request.client.
    return request.client.host if request and request.client else None


def record(session: AsyncSession, request: Request | None, user: User | None, action: str,
           target_type: str | None = None, target_id: str | None = None, *,
           org_id: str | None = None, email: str | None = None, **details: Any) -> None:
    session.add(AuditLog(
        org_id=org_id or (user.org_id if user else None),
        user_id=user.id if user else None,
        user_email=email or (user.email if user else None),
        action=action, target_type=target_type, target_id=target_id,
        details={k: v for k, v in details.items() if v is not None}, ip=client_ip(request),
    ))
