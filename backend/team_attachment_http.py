from __future__ import annotations

"""HTTP controls scoped only to the team-conversation attachment endpoint."""

import json
import re
from typing import Any, Awaitable, Callable

from backend.team_attachment_security import get_team_secure_attachment_max_bytes


TEAM_ATTACHMENT_UPLOAD_PATH_RE = re.compile(
    r"^/api/v1/conversations/[1-9][0-9]*/attachments/?$"
)
MULTIPART_OVERHEAD_ALLOWANCE_BYTES = 1024 * 1024


class _TeamAttachmentBodyTooLarge(Exception):
    pass


class TeamAttachmentRequestSizeLimitMiddleware:
    """Cap multipart request bytes without changing any other upload route.

    FastAPI resolves ``UploadFile`` before entering the route function.  This
    pure ASGI middleware therefore enforces the limit while the multipart body
    is still streaming, including when a client omits Content-Length.
    """

    def __init__(self, app: Callable[..., Awaitable[None]]):
        self.app = app

    @staticmethod
    async def _send_too_large(send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        maximum_mb = get_team_secure_attachment_max_bytes() // (1024 * 1024)
        body = json.dumps(
            {
                "error": "attachment_request_too_large",
                "message": (
                    "Attachment request is too large. The maximum file size is "
                    f"{maximum_mb} MB."
                ),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope.get("type") != "http"
            or str(scope.get("method") or "").upper() != "POST"
            or not TEAM_ATTACHMENT_UPLOAD_PATH_RE.fullmatch(str(scope.get("path") or ""))
        ):
            await self.app(scope, receive, send)
            return

        maximum_request_bytes = (
            get_team_secure_attachment_max_bytes()
            + MULTIPART_OVERHEAD_ALLOWANCE_BYTES
        )
        headers = {
            key.lower(): value
            for key, value in scope.get("headers") or []
        }
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > maximum_request_bytes:
                    await self._send_too_large(send)
                    return
            except ValueError:
                # Invalid Content-Length is left to the HTTP server/parser.
                pass

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > maximum_request_bytes:
                    raise _TeamAttachmentBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _TeamAttachmentBodyTooLarge:
            await self._send_too_large(send)


__all__ = ["TeamAttachmentRequestSizeLimitMiddleware"]
