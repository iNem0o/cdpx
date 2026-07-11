"""Découverte et gestion des onglets via l'API HTTP de Chrome (/json).

C'est la seule partie du protocole qui passe par HTTP: lister/créer/activer/
fermer des targets, et récupérer le webSocketDebuggerUrl d'une page.

Note compat: depuis Chrome 111, /json/new exige un PUT. On tente PUT puis on
retombe sur GET pour les vieux Chrome/chromium headless.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class DiscoveryError(RuntimeError):
    pass


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
    except urllib.error.URLError as e:
        raise DiscoveryError(f"{method} {url}: {e}") from e


def version(host: str, port: int) -> dict:
    return json.loads(_http(host, port, "/json/version"))


def list_targets(host: str, port: int) -> list[dict]:
    return json.loads(_http(host, port, "/json/list"))


def new_tab(host: str, port: int, url: str | None = None) -> dict:
    path = "/json/new"
    if url:
        path += "?" + urllib.parse.quote(url, safe="")
    try:
        return json.loads(_http(host, port, path, method="PUT"))
    except DiscoveryError:
        return json.loads(_http(host, port, path, method="GET"))


def activate_tab(host: str, port: int, target_id: str) -> str:
    return _http(host, port, f"/json/activate/{target_id}")


def close_tab(host: str, port: int, target_id: str) -> str:
    return _http(host, port, f"/json/close/{target_id}")


def pick_page(host: str, port: int, target_id: str | None = None) -> dict:
    """Retourne le target à piloter: celui demandé, sinon la première page."""
    targets = list_targets(host, port)
    if target_id:
        for t in targets:
            if t.get("id") == target_id:
                return t
        raise DiscoveryError(f"target {target_id} introuvable")
    for t in targets:
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return t
    raise DiscoveryError("aucun target de type 'page' avec webSocketDebuggerUrl")
