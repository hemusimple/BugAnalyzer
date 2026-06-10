"""
jira_client.py — Jira REST API + webhook payload handling.
Downloads log attachments from issues.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from config import settings


class JiraClient:
    def __init__(self):
        self.base_url = settings.JIRA_BASE_URL.rstrip("/")
        self.auth = (settings.JIRA_EMAIL, settings.JIRA_API_TOKEN)

    def _headers(self) -> dict:
        import base64
        creds = base64.b64encode(f"{settings.JIRA_EMAIL}:{settings.JIRA_API_TOKEN}".encode()).decode()
        return {
            "Authorization": f"Basic {creds}",
            "Accept": "application/json",
        }

    async def get_issue(self, issue_key: str) -> dict:
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def get_attachments(self, issue_key: str) -> list[dict]:
        """Return list of attachment metadata for an issue."""
        data = await self.get_issue(issue_key)
        return data.get("fields", {}).get("attachment", [])

    async def download_attachment(self, attachment: dict) -> Optional[Path]:
        """Download a single attachment and save to LOGS_DIR. Returns local path."""
        content_url = attachment.get("content", "")
        filename = attachment.get("filename", "attachment.txt")
        mime = attachment.get("mimeType", "")

        # Only download text/log files
        if not _is_log_file(filename, mime):
            logger.debug(f"Skipping non-log attachment: {filename}")
            return None

        dest = settings.LOGS_DIR / filename
        if dest.exists():
            logger.debug(f"Already have {filename}, skipping re-download")
            return dest

        logger.info(f"Downloading attachment: {filename}")
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(content_url, headers=self._headers(), follow_redirects=True)
            r.raise_for_status()
            dest.write_bytes(r.content)

        logger.info(f"Saved attachment: {dest}")
        return dest

    async def download_all_attachments(self, issue_key: str) -> list[Path]:
        """Download all log attachments for an issue."""
        attachments = await self.get_attachments(issue_key)
        paths = []
        for att in attachments:
            path = await self.download_attachment(att)
            if path:
                paths.append(path)
        return paths

    async def post_comment(self, issue_key: str, body_adf: dict):
        """Post an ADF comment to the Jira issue."""
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/comment"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                url, headers=self._headers(), json={"body": body_adf}
            )
            if r.status_code not in (200, 201):
                logger.warning(f"Comment post failed {r.status_code}: {r.text[:200]}")
            else:
                logger.info(f"Posted comment to {issue_key}")

    async def post_analysis_comment(self, issue_key: str, analysis: str):
        """Wrap plain text analysis in minimal ADF and post."""
        adf = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "🤖 ", "marks": []},
                        {"type": "text", "text": "Log Analyzer Agent Report", "marks": [{"type": "strong"}]},
                    ],
                },
                {
                    "type": "codeBlock",
                    "attrs": {"language": "text"},
                    "content": [{"type": "text", "text": analysis}],
                },
            ],
        }
        await self.post_comment(issue_key, adf)


def _is_log_file(filename: str, mime: str) -> bool:
    filename_lower = filename.lower()
    if any(filename_lower.endswith(ext) for ext in [".log", ".txt", ".logcat"]):
        return True
    if "text" in mime:
        return True
    # Android bugreports
    if "bugreport" in filename_lower or "logcat" in filename_lower:
        return True
    return False


def verify_webhook_signature(body: bytes, signature_header: str) -> bool:
    """Verify Jira webhook HMAC-SHA256 signature."""
    if not settings.JIRA_WEBHOOK_SECRET:
        return True  # skip if not configured
    expected = hmac.new(
        settings.JIRA_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header or "")


def extract_issue_key(payload: dict) -> Optional[str]:
    """Extract issue key from Jira webhook payload."""
    try:
        return payload["issue"]["key"]
    except (KeyError, TypeError):
        return None


def extract_issue_summary(payload: dict) -> str:
    try:
        return payload["issue"]["fields"]["summary"]
    except (KeyError, TypeError):
        return ""


def extract_issue_description(payload: dict) -> str:
    try:
        desc = payload["issue"]["fields"]["description"]
        if isinstance(desc, dict):
            # ADF format — extract plain text
            return _adf_to_text(desc)
        return desc or ""
    except (KeyError, TypeError):
        return ""


def _adf_to_text(adf: dict) -> str:
    """Recursively extract text from ADF."""
    if adf.get("type") == "text":
        return adf.get("text", "")
    parts = []
    for child in adf.get("content", []):
        parts.append(_adf_to_text(child))
    return " ".join(parts)


# Singleton
_client: Optional[JiraClient] = None


def get_jira_client() -> JiraClient:
    global _client
    if _client is None:
        _client = JiraClient()
    return _client
