"""Structural types for the small CDP surface used by cdpx.

CDP payload contents remain intentionally open-ended: Chrome domains evolve
independently, while the command/response/event envelopes are stable enough to
model and validate at the transport boundary.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

type CDPParams = dict[str, Any]
type CDPResult = dict[str, Any]


class CDPErrorPayload(TypedDict):
    code: int
    message: str
    data: NotRequired[Any]


class CDPCommand(TypedDict):
    id: int
    method: str
    params: CDPParams


class CDPResponse(TypedDict):
    id: int
    result: NotRequired[CDPResult]
    error: NotRequired[CDPErrorPayload]


class CDPEvent(TypedDict):
    method: str
    params: NotRequired[CDPParams]


class DiscoveryTarget(TypedDict):
    id: str
    type: NotRequired[str]
    title: NotRequired[str]
    url: NotRequired[str]
    webSocketDebuggerUrl: NotRequired[str]
    devtoolsFrontendUrl: NotRequired[str]
    description: NotRequired[str]
    faviconUrl: NotRequired[str]


BrowserVersion = TypedDict(
    "BrowserVersion",
    {
        "Browser": str,
        "Protocol-Version": str,
        "User-Agent": str,
        "V8-Version": str,
        "WebKit-Version": str,
        "webSocketDebuggerUrl": str,
    },
    total=False,
)
