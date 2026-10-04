"""Qoder quota normalization shared by the Hub management adapter."""
import math
import time
from datetime import datetime


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def expiry(value):
    numeric = number(value)
    if numeric is not None:
        return int(numeric * 1000 if 0 < numeric < 10**12 else numeric)
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except (AttributeError, ValueError):
        return 0


def quota_balance(data):
    buckets = [data.get("userQuota"), data.get("addOnQuota")]
    for package in data.get("dedicatedResourcePackages") or []:
        if not isinstance(package, dict) or package.get("available") is False:
            continue
        if any(hint in str(package.get("status", "")).upper() for hint in
               ("EXPIRED", "INVALID", "INACTIVE", "DISABLED", "USED_UP", "DEPLETED")):
            continue
        end = expiry(package.get("expiresAt"))
        if end and end <= time.time() * 1000:
            continue
        buckets.append(package)
    values = [number(bucket.get("remaining")) for bucket in buckets if isinstance(bucket, dict)]
    values = [value for value in values if value is not None]
    return sum(values) if values else None
