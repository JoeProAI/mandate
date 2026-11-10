"""Mandate: learned least-privilege for unattended Hermes jobs."""

if __package__:
    from .mandate_core.plugin import register
else:  # pytest imports the checkout root standalone when its directory name is not an identifier
    from mandate_core.plugin import register

__all__ = ["register"]
