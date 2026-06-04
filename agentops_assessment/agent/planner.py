from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agentops_assessment.agent.fake_llm import FakeLLM


@dataclass(frozen=True)
class PlanStep:
    id: str
    tool_name: str
    description: str
    input_template: dict[str, Any] = field(default_factory=dict)


class Planner:
    def __init__(self, llm: FakeLLM | None = None) -> None:
        self.llm = llm or FakeLLM()

    def _extract_sku(self, prompt: str) -> str | None:
        match = re.search(r"\bSKU[-_]?\d{3,}\b", prompt, re.IGNORECASE)
        if match:
            return match.group(0).upper()
        match = re.search(r"\bSKU[-_]?[A-Za-z0-9]+\b", prompt, re.IGNORECASE)
        if match:
            return match.group(0).upper()
        return None

    def _intends_to_create_approval(self, prompt: str) -> bool:
        create_keywords = {"创建", "生成", "发起", "提交", "申请", "审批草稿"}
        analysis_only_keywords = {"只分析", "仅分析", "不创建", "不生成", "不要", "无需"}
        
        has_create_intent = any(keyword in prompt for keyword in create_keywords)
        has_analysis_only = any(keyword in prompt for keyword in analysis_only_keywords)
        
        if has_analysis_only:
            return False
        return has_create_intent

    def create_plan(self, prompt: str, context: dict[str, Any] | None = None) -> list[PlanStep]:
        self.llm.complete(prompt)
        
        sku = self._extract_sku(prompt)
        needs_approval = self._intends_to_create_approval(prompt)
        
        plan: list[PlanStep] = []
        step_id = 1
        
        if sku:
            plan.append(PlanStep(
                id=f"step_{step_id}",
                tool_name="erp.get_inventory",
                description=f"查询 ERP 库存数据: {sku}",
                input_template={"sku": sku},
            ))
            step_id += 1
            
            plan.append(PlanStep(
                id=f"step_{step_id}",
                tool_name="bi.get_sales",
                description=f"查询 BI 销售预测: {sku}",
                input_template={"sku": sku},
            ))
            step_id += 1
            
            plan.append(PlanStep(
                id=f"step_{step_id}",
                tool_name="knowledge.search",
                description="查询库存处理规则知识库",
                input_template={"query": f"库存异常审批规则 {sku}", "top_k": 5},
            ))
            step_id += 1
            
            plan.append(PlanStep(
                id=f"step_{step_id}",
                tool_name="supplier.get_risk",
                description="查询供应商风险",
                input_template={"supplier_id": ""},
            ))
            step_id += 1
            
            if needs_approval:
                plan.append(PlanStep(
                    id=f"step_{step_id}",
                    tool_name="oa.create_approval_draft",
                    description="创建补货审批草稿",
                    input_template={"sku": sku, "approval_type": "inventory_replenishment"},
                ))
        else:
            plan.append(PlanStep(
                id=f"step_{step_id}",
                tool_name="llm.summarize",
                description="无法识别 SKU，需要用户提供更多信息",
                input_template={"prompt": prompt},
            ))
        
        return plan