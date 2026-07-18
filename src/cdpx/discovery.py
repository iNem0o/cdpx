"""Tab discovery and management via Chrome's HTTP API (/json).

This is the only part of the protocol that goes through HTTP: listing/
creating/activating/closing targets, and fetching a page's
webSocketDebuggerUrl.

Compat note: since Chrome 111, /json/new requires a PUT. We try PUT then
fall back to GET for old Chrome/chromium headless.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from cdpx.cdp_types import BrowserVersion, DiscoveryTarget

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class DiscoveryError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        method: str | None = None,
        url: str | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.method = method
        self.url = url
        self.status = status


def _http(host: str, port: int, path: str, method: str = "GET") -> str:
    url = f"http://{host}:{port}{path}"
    req = urllib.request.Request(url, method=method)
    try:
        if host in LOOPBACK_HOSTS:
            # CDP loopback must never be routed through a runner or workstation
            # HTTP proxy. ProxyHandler({}) is the urllib direct-connection path.
            response = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                req, timeout=10
            )
        else:
            response = urllib.request.urlopen(req, timeout=10)
        with response as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise DiscoveryError(
            f"{method} {url}: HTTP {e.code} {e.reason}",
            method=method,
            url=url,
            status=e.code,
        ) from e
    except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
        raise DiscoveryError(f"{method} {url}: {e}", method=method, url=url) from e


def _response_json(host: str, port: int, path: str, *, method: str = "GET") -> Any:
    url = f"http://{host}:{port}{path}"
    raw = _http(host, port, path, method=method)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise DiscoveryError(
            f"{method} {url}: invalid JSON",
            method=method,
            url=url,
        ) from error


def _json_object(value: Any, *, method: str, url: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DiscoveryError(
            f"{method} {url}: JSON object required",
            method=method,
            url=url,
        )
    return value


def _target(value: Any, *, method: str, url: str, label: str = "target") -> DiscoveryTarget:
    if not isinstance(value, dict):
        raise DiscoveryError(
            f"{method} {url}: {label}: JSON object required",
            method=method,
            url=url,
        )
    target_id = value.get("id")
    if not isinstance(target_id, str):
        raise DiscoveryError(
            f"{method} {url}: {label} without valid id",
            method=method,
            url=url,
        )
    target: DiscoveryTarget = {"id": target_id}
    type_ = _optional_string(value, "type", method=method, url=url, label=label)
    title = _optional_string(value, "title", method=method, url=url, label=label)
    target_url = _optional_string(value, "url", method=method, url=url, label=label)
    websocket_url = _optional_string(
        value,
        "webSocketDebuggerUrl",
        method=method,
        url=url,
        label=label,
    )
    frontend_url = _optional_string(
        value,
        "devtoolsFrontendUrl",
        method=method,
        url=url,
        label=label,
    )
    description = _optional_string(
        value,
        "description",
        method=method,
        url=url,
        label=label,
    )
    favicon_url = _optional_string(
        value,
        "faviconUrl",
        method=method,
        url=url,
        label=label,
    )
    if type_ is not None:
        target["type"] = type_
    if title is not None:
        target["title"] = title
    if target_url is not None:
        target["url"] = target_url
    if websocket_url is not None:
        target["webSocketDebuggerUrl"] = websocket_url
    if frontend_url is not None:
        target["devtoolsFrontendUrl"] = frontend_url
    if description is not None:
        target["description"] = description
    if favicon_url is not None:
        target["faviconUrl"] = favicon_url
    return target


def _optional_string(
    value: dict[str, Any],
    field: str,
    *,
    method: str,
    url: str,
    label: str,
) -> str | None:
    if field not in value:
        return None
    item = value[field]
    if not isinstance(item, str):
        raise DiscoveryError(
            f"{method} {url}: {label}.{field} text required",
            method=method,
            url=url,
        )
    return item


def version(host: str, port: int) -> BrowserVersion:
    path = "/json/version"
    url = f"http://{host}:{port}{path}"
    value = _response_json(host, port, path)
    payload = _json_object(value, method="GET", url=url)
    result: BrowserVersion = {}
    browser = _optional_string(payload, "Browser", method="GET", url=url, label="version")
    protocol = _optional_string(
        payload,
        "Protocol-Version",
        method="GET",
        url=url,
        label="version",
    )
    user_agent = _optional_string(
        payload,
        "User-Agent",
        method="GET",
        url=url,
        label="version",
    )
    v8 = _optional_string(payload, "V8-Version", method="GET", url=url, label="version")
    webkit = _optional_string(
        payload,
        "WebKit-Version",
        method="GET",
        url=url,
        label="version",
    )
    websocket = _optional_string(
        payload,
        "webSocketDebuggerUrl",
        method="GET",
        url=url,
        label="version",
    )
    if browser is not None:
        result["Browser"] = browser
    if protocol is not None:
        result["Protocol-Version"] = protocol
    if user_agent is not None:
        result["User-Agent"] = user_agent
    if v8 is not None:
        result["V8-Version"] = v8
    if webkit is not None:
        result["WebKit-Version"] = webkit
    if websocket is not None:
        result["webSocketDebuggerUrl"] = websocket
    return result


def list_targets(host: str, port: int) -> list[DiscoveryTarget]:
    path = "/json/list"
    url = f"http://{host}:{port}{path}"
    value = _response_json(host, port, path)
    if not isinstance(value, list):
        raise DiscoveryError(
            f"GET {url}: JSON array required",
            method="GET",
            url=url,
        )
    return [
        _target(target, method="GET", url=url, label=f"target[{index}]")
        for index, target in enumerate(value)
    ]


def new_tab(host: str, port: int, url: str | None = None) -> DiscoveryTarget:
    path = "/json/new"
    if url:
        path += "?" + urllib.parse.quote(url, safe="")
    endpoint = f"http://{host}:{port}{path}"
    try:
        return _target(
            _response_json(host, port, path, method="PUT"),
            method="PUT",
            url=endpoint,
        )
    except DiscoveryError as put_error:
        if not _legacy_new_tab_method_rejection(put_error):
            raise
        try:
            return _target(
                _response_json(host, port, path, method="GET"),
                method="GET",
                url=endpoint,
            )
        except DiscoveryError as get_error:
            raise DiscoveryError(
                f"{put_error}; legacy GET fallback failed: {get_error}",
                method="GET",
                url=get_error.url,
                status=get_error.status,
            ) from get_error


def _legacy_new_tab_method_rejection(error: DiscoveryError) -> bool:
    """Recognize explicit legacy servers that cannot dispatch PUT /json/new.

    Factual basis for the predicate: Chrome 111+ requires PUT; earlier
    DevTools servers respond 405 (compliant method rejection) or close the
    connection without a response (RemoteDisconnected). Any other PUT
    failure (5xx, network URLError) is not a legacy-server signal and must
    not trigger a silent GET fallback.
    """
    return error.status == 405 or isinstance(error.__cause__, http.client.RemoteDisconnected)


def activate_tab(host: str, port: int, target_id: str) -> str:
    return _http(host, port, f"/json/activate/{target_id}")


def close_tab(host: str, port: int, target_id: str) -> str:
    return _http(host, port, f"/json/close/{target_id}")


def pick_page(host: str, port: int, target_id: str) -> DiscoveryTarget:
    """Return only the explicitly assigned target."""
    targets = list_targets(host, port)
    for t in targets:
        if t.get("id") == target_id:
            return t
    raise DiscoveryError(f"target {target_id} not found")
