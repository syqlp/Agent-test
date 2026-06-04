from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentops_assessment.integrations.bi import BIClient
from agentops_assessment.integrations.erp import ERPClient
from agentops_assessment.integrations.exceptions import TransientIntegrationError
from agentops_assessment.integrations.oa import OAClient
from agentops_assessment.integrations.third_party import SupplierRiskClient
from agentops_assessment.rag.search import KnowledgeIndex

ToolCallable = Callable[[dict[str, Any]], dict[str, Any]]


class ToolRegistry:
    def __init__(self, retry_attempts: int = 1) -> None:
        self.retry_attempts = retry_attempts
        self._tools: dict[str, ToolCallable] = {}
        self.last_call_attempts: dict[str, int] = {}
        self._tool_permissions: dict[str, str] = {
            "erp.get_inventory": "erp:read",
            "bi.get_sales": "bi:read",
            "oa.create_approval_draft": "oa:approval:write",
            "supplier.get_risk": "supplier:read",
            "knowledge.search": "knowledge:read",
        }

    def register(self, name: str, func: ToolCallable) -> None:
        self._tools[name] = func

    def get_required_permission(self, tool_name: str) -> str | None:
        return self._tool_permissions.get(tool_name)

    @classmethod
    def with_default_clients(
        cls,
        fixtures_dir: str | Path = "fixtures",
        retry_attempts: int = 1,
        supplier_fail_first: bool = False,
    ) -> "ToolRegistry":
        registry = cls(retry_attempts=retry_attempts)
        fixtures = Path(fixtures_dir)
        erp = ERPClient(fixtures / "business" / "erp_inventory.json")
        bi = BIClient(fixtures / "business" / "bi_sales.json")
        oa = OAClient(fixtures / "business" / "oa_rules.json")
        supplier = SupplierRiskClient(
            fixtures / "business" / "supplier_risk.json",
            fail_first=supplier_fail_first,
        )
        knowledge = KnowledgeIndex()

        registry.register("erp.get_inventory", lambda args: erp.get_inventory(args["sku"]))
        registry.register("bi.get_sales", lambda args: bi.get_sales(args["sku"]))
        registry.register("oa.create_approval_draft", oa.create_approval_draft)
        registry.register("supplier.get_risk", lambda args: supplier.get_supplier_risk(args["supplier_id"]))
        registry.register(
            "knowledge.search",
            lambda args: knowledge.search(
                args["query"],
                user_permissions=args.get("user_permissions", []),
                top_k=args.get("top_k", 3),
            ),
        )
        return registry

    def _redact_sensitive_fields(self, data: dict[str, Any]) -> dict[str, Any]:
        sensitive_fields = {"vendor_secret", "unit_cost_usd", "ACME-TIER-2-REBATE", "BETA-PRICE-FLOOR"}
        result = {}
        for key, value in data.items():
            if key in sensitive_fields:
                continue
            if isinstance(value, dict):
                result[key] = self._redact_sensitive_fields(value)
            elif isinstance(value, str):
                for sensitive in sensitive_fields:
                    value = re.sub(re.escape(sensitive), "[REDACTED]", value)
                result[key] = value
            else:
                result[key] = value
        return result

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in self._tools:
            raise KeyError(f"未知工具: {name}")

        attempts = 0
        last_error: Exception | None = None
        max_attempts = max(1, self.retry_attempts)
        for _ in range(max_attempts):
            attempts += 1
            self.last_call_attempts[name] = attempts
            try:
                result = self._tools[name](args)
                result = self._redact_sensitive_fields(result)
                return result
            except TransientIntegrationError as exc:
                last_error = exc
                continue
        if last_error:
            raise last_error
        raise RuntimeError(f"工具失败但没有抛出明确异常: {name}")