"""Mandate: learned least-privilege for unattended Hermes jobs."""

from .mandate_core.plugin import register

__all__ = ["register"]
