from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

import httpx

from .chargebacks import (
    ChargebackError,
    EmailAttachment,
    EmailMessage,
    decode_gmail_message,
)


_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")
_SAFE_SENDER = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+$"
)
_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


def build_dispute_search_query(
    *,
    lookback_days: int = 60,
    senders: Sequence[str] = (),
) -> str:
    """Build a Gmail query with OR semantics for multiple processor senders."""
    if lookback_days < 1 or lookback_days > 3650:
        raise ChargebackError("lookback_days must be between 1 and 3650")

    normalized_senders: list[str] = []
    for sender in senders:
        value = sender.strip().lower()
        if not value:
            continue
        if not _SAFE_SENDER.fullmatch(value):
            raise ChargebackError(f"Invalid Gmail sender filter: {sender!r}")
        if value not in normalized_senders:
            normalized_senders.append(value)

    sender_clause = ""
    if len(normalized_senders) == 1:
        sender_clause = f"from:{normalized_senders[0]}"
    elif normalized_senders:
        sender_clause = "{" + " ".join(
            f"from:{sender}" for sender in normalized_senders
        ) + "}"

    signals = (
        '{subject:chargeback subject:dispute "reason for challenge" '
        '"customer\'s bank" "respond by"}'
    )
    return " ".join(
        part
        for part in (
            f"newer_than:{lookback_days}d",
            sender_clause,
            signals,
            "-in:trash",
        )
        if part
    )


class GmailDisputeClient:
    """Minimal Gmail REST client for dispute intake.

    The OAuth access token is injected at runtime and is never written to disk,
    logs, approval payloads, or the operational database.
    """

    def __init__(
        self,
        *,
        access_token: str,
        user_id: str = "me",
        timeout_seconds: float = 30.0,
        base_url: str = "https://gmail.googleapis.com/gmail/v1",
    ):
        if not access_token.strip():
            raise ChargebackError("Gmail access token is required")
        self._access_token = access_token
        self.user_id = user_id
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }

    async def _get_json(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{self.base_url}{path}",
                params=params,
                headers=self._headers,
            )
        if response.status_code >= 400:
            request_id = response.headers.get("x-request-id", "unknown")
            raise ChargebackError(
                f"Gmail API request failed with status {response.status_code}; "
                f"request_id={request_id}"
            )
        value = response.json()
        if not isinstance(value, dict):
            raise ChargebackError("Gmail API returned an unexpected response")
        return value

    async def search_message_ids(
        self,
        *,
        query: str,
        max_results: int = 100,
    ) -> list[str]:
        if not query.strip():
            raise ChargebackError("Gmail search query is required")
        if max_results < 1 or max_results > 1000:
            raise ChargebackError("max_results must be between 1 and 1000")

        output: list[str] = []
        page_token: str | None = None
        while len(output) < max_results:
            page_size = min(100, max_results - len(output))
            params: dict[str, Any] = {"q": query, "maxResults": page_size}
            if page_token:
                params["pageToken"] = page_token
            payload = await self._get_json(
                f"/users/{quote(self.user_id, safe='')}/messages",
                params=params,
            )
            for item in payload.get("messages") or []:
                message_id = str(item.get("id") or "").strip()
                if message_id:
                    output.append(message_id)
                    if len(output) >= max_results:
                        break
            page_token = str(payload.get("nextPageToken") or "") or None
            if not page_token:
                break
        return output

    async def get_message(self, message_id: str) -> EmailMessage:
        if not message_id.strip():
            raise ChargebackError("Gmail message ID is required")
        payload = await self._get_json(
            f"/users/{quote(self.user_id, safe='')}/messages/"
            f"{quote(message_id, safe='')}",
            params={"format": "full"},
        )
        return decode_gmail_message(payload)

    async def search_disputes(
        self,
        *,
        lookback_days: int = 60,
        senders: Sequence[str] = (),
        max_results: int = 100,
    ) -> list[EmailMessage]:
        query = build_dispute_search_query(
            lookback_days=lookback_days,
            senders=senders,
        )
        message_ids = await self.search_message_ids(
            query=query,
            max_results=max_results,
        )
        output: list[EmailMessage] = []
        for message_id in message_ids:
            output.append(await self.get_message(message_id))
        return output

    async def download_attachment(
        self,
        *,
        message_id: str,
        attachment: EmailAttachment,
        destination_directory: str | Path,
    ) -> Path:
        if not attachment.attachment_id:
            raise ChargebackError("Gmail attachment ID is missing")
        directory = Path(destination_directory)
        directory.mkdir(parents=True, exist_ok=True)
        filename = _SAFE_FILENAME.sub("_", Path(attachment.filename).name).strip()
        safe_message_id = _SAFE_FILENAME.sub("_", message_id).strip(" .")
        if not filename or not safe_message_id:
            raise ChargebackError("Attachment filename or message ID is invalid")
        payload = await self._get_json(
            f"/users/{quote(self.user_id, safe='')}/messages/"
            f"{quote(message_id, safe='')}/attachments/"
            f"{quote(attachment.attachment_id, safe='')}"
        )
        encoded = str(payload.get("data") or "")
        if not encoded:
            raise ChargebackError("Gmail attachment response did not contain data")
        padding = "=" * (-len(encoded) % 4)
        raw = base64.urlsafe_b64decode(f"{encoded}{padding}")
        if len(raw) > _MAX_ATTACHMENT_BYTES:
            raise ChargebackError("Gmail attachment exceeds the 25 MB safety limit")
        destination = directory / f"{safe_message_id}__{filename}"
        destination.write_bytes(raw)
        return destination
