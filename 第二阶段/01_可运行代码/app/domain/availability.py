"""Shared state/time policy. Expiry is derived, never a scheduled status rewrite."""
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc)


def availability(survey, now):
    if survey["status"] == "draft":
        return "draft"
    if survey["status"] == "closed":
        return "closed"
    if survey["status"] != "published":
        raise ValueError("未知问卷状态")
    deadline = survey.get("deadline")
    return "expired" if deadline is not None and deadline <= now else "open"
