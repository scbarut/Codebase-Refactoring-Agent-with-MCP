"""Rule loading utilities for YAML-based migration rule sets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_rules(rule_file: str | Path) -> list[dict[str, Any]]:
    """Load migration rules from a YAML rule set file.

    Args:
        rule_file: Path to the YAML rule set file.

    Returns:
        List of rule dictionaries, each containing id, old_qualified_name,
        new_qualified_name, risk, doc_ref, and optionally transformer_class.

    Raises:
        FileNotFoundError: If the rule file does not exist.
        ValueError: If the YAML is missing a ``rules`` key.
    """
    path = Path(rule_file)
    if not path.exists():
        raise FileNotFoundError(f"Rule file not found: {rule_file}")

    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if not isinstance(data, dict) or "rules" not in data:
        raise ValueError(f"Rule file must contain a 'rules' key: {rule_file}")

    return data["rules"]
