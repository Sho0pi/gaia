"""Minimal GitHub issue client for the monitor — file/dedup issues for findings.

httpx (already a dep), no PyGithub. Dedup is GitHub-as-source-of-truth: each issue carries a hidden
``<!-- gaia:sig:HASH -->`` marker + the configured label; before filing we list the open labelled
issues and match the marker, commenting "seen again" instead of opening a duplicate.
"""

from __future__ import annotations

import hashlib

_API = "https://api.github.com"


def _marker(signature: str) -> str:
    """A stable hidden HTML marker for a signature (lets us find our own issue again)."""
    digest = hashlib.sha1(signature.encode()).hexdigest()[:12]
    return f"<!-- gaia:sig:{digest} -->"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


async def file_issue(
    repo: str, token: str, *, signature: str, title: str, body: str, label: str
) -> str:
    """File the issue, or comment on the existing one with the same signature. Returns its URL."""
    import httpx

    marker = _marker(signature)
    async with httpx.AsyncClient(timeout=20) as client:
        # Dedup: find an open labelled issue carrying this marker.
        resp = await client.get(
            f"{_API}/repos/{repo}/issues",
            headers=_headers(token),
            params={"state": "open", "labels": label, "per_page": 100},
        )
        resp.raise_for_status()
        for issue in resp.json():
            if "pull_request" in issue:  # the issues endpoint also returns PRs
                continue
            if marker in (issue.get("body") or ""):
                num = issue["number"]
                comment = await client.post(
                    f"{_API}/repos/{repo}/issues/{num}/comments",
                    headers=_headers(token),
                    json={"body": f"Seen again by the monitor.\n\n{marker}"},
                )
                comment.raise_for_status()
                return str(issue["html_url"])
        # None found -> open a new one (marker embedded for future dedup).
        created = await client.post(
            f"{_API}/repos/{repo}/issues",
            headers=_headers(token),
            json={"title": title or signature, "body": f"{body}\n\n{marker}", "labels": [label]},
        )
        created.raise_for_status()
        return str(created.json()["html_url"])
