"""Contract primitives.

Contracts accept unknown fields so a newer producer can add optional data without
breaking an older consumer. Required semantics still have explicit validators.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION = "1.0.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ContractModel(BaseModel):
    """Base for JSON-compatible, forward-compatible boundary objects."""

    model_config = ConfigDict(extra="allow", populate_by_name=True, use_enum_values=True)

    schema_version: str = Field(default=SCHEMA_VERSION, min_length=1)

    def to_wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)
