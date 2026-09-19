from __future__ import annotations

from maagent.i18n import t

# key -> (display name, i18n description key)
SOFTWARE_META: dict[str, tuple[str, str]] = {
    "maa": ("MAA", "card.maa.desc"),
    "maaend": ("MaaEnd", "card.maaend.desc"),
}

WORKFLOW_KEY = "workflow"
WORKFLOW_NAME = "card.workflow.name"
WORKFLOW_DESC = "card.workflow.desc"
DEFAULT_HOTKEYS = {"start": "F8", "stop": "F9"}


def software_meta() -> dict[str, tuple[str, str]]:
    """SOFTWARE_META with descriptions translated for the current language."""
    return {key: (name, t(desc)) for key, (name, desc) in SOFTWARE_META.items()}
