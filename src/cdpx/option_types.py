"""Shared domain literals used at CLI and primitive boundaries."""

from typing import Literal

NavigationWait = Literal["load", "domcontentloaded", "none"]
StorageKind = Literal["local", "session"]
