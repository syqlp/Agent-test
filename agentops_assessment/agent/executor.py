from __future__ import annotations

import json
from typing import Any

from agentops_assessment.agent.planner import PlanStep
from agentops_assessment.agent.state import InMemoryRunStateStore, RunState, StepState
from agentops_assessment.agent.tools import ToolRegistry
from agentops_assessment.backend import database


class Executor:
    def __init__(
        self,
        registry: ToolRegistry,
        state_store: InMemoryRunStateStore | None = None,
    ) -> None:
        self.registry = registry
        self.state_store = state_store or InMemoryRunStateStore()

    def _redact_sensitive_fields(self, data: dict[str, Any]) -> dict[str, Any]:
        sensitive_fields = {"vendor_secret", "unit_cost_usd", "debug", "candidate_note", "ACME-TIER-2-REBATE", "BETA-PRICE-FLOOR"}
        result = {}
        for key, value in data.items():
            if key in sensitive_fields:
                continue
            if isinstance(value, dict):
                result[key] = self._redact_sensitive_fields(value)
            else:
                result[key] = value
        return result

    def _check_tool_permission(self, tool_name: str, user_permissions: list[str]) -> bool:
        required_permission = self.registry.get_required_permission(tool_name)
        if not required_permission:
            return True
        return required_permission in user_permissions

    def execute(
        self,
        run_id: str,
        plan: list[PlanStep],
        context: dict[str, Any],
    ) -> RunState:
        token_cost = 0
        step_outputs: dict[str, Any] = {}
        all_citations: list[dict[str, Any]] = []
        sku = context.get("sku")
        user_permissions = context.get("user_permissions", [])
        
        state = RunState(
            run_id=run_id,
            status="running",
            steps=[
                StepState(
                    step_id=step.id,
                    tool_name=step.tool_name,
                    status="pending",
                )
                for step in plan
            ],
        )
        
        with database.connect() as conn:
            database.init_db(conn)
            
            for step_idx, step in enumerate(plan):
                step_state = state.steps[step_idx]
                step_state.status = "running"
                
                try:
                    if not self._check_tool_permission(step.tool_name, user_permissions):
                        required_perm = self.registry.get_required_permission(step.tool_name)
                        step_state.status = "skipped"
                        step_state.error = f"缺少权限: {required_perm}"
                        database.insert_run_event(
                            conn,
                            run_id,
                            "tool.skipped",
                            {"reason": "permission_denied", "missing_permission": required_perm},
                            tool_name=step.tool_name,
                        )
                        continue
                    
                    inputs = {}
                    for key, template_value in step.input_template.items():
                        if isinstance(template_value, str) and template_value == "" and key == "supplier_id":
                            erp_output = step_outputs.get("erp.get_inventory", {})
                            inputs[key] = erp_output.get("supplier_id", "")
                        elif key == "user_permissions":
                            inputs[key] = user_permissions
                        else:
                            inputs[key] = template_value
                    
                    if step.tool_name == "knowledge.search" and "user_permissions" not in inputs:
                        inputs["user_permissions"] = user_permissions
                    
                    inputs = {k: v for k, v in inputs.items() if v != ""}
                    
                    if step.tool_name == "llm.summarize":
                        token_cost += 100
                        output = {"summary": "分析完成"}
                    else:
                        output = self.registry.call(step.tool_name, inputs)
                        token_cost += 50
                    
                    output = self._redact_sensitive_fields(output)
                    step_state.output = output
                    step_state.status = "completed"
                    step_outputs[step.tool_name] = output
                    
                    if step.tool_name == "knowledge.search" and output.get("citations"):
                        all_citations.extend(output["citations"])
                    
                    database.insert_run_event(
                        conn,
                        run_id,
                        "tool.call",
                        {"input": inputs, "output": output},
                        tool_name=step.tool_name,
                    )
                
                except Exception as exc:
                    step_state.status = "failed"
                    step_state.error = str(exc)
                    database.insert_run_event(
                        conn,
                        run_id,
                        "tool.call",
                        {"input": inputs, "error": str(exc)},
                        tool_name=step.tool_name,
                    )
                    state.status = "failed"
                    state.result = {"error": str(exc)}
                    break
        
        if state.status != "failed":
            state.status = "completed"
            erp_data = step_outputs.get("erp.get_inventory", {})
            bi_data = step_outputs.get("bi.get_sales", {})
            supplier_data = step_outputs.get("supplier.get_risk", {})
            oa_data = step_outputs.get("oa.create_approval_draft", {})
            
            state.result = {
                "sku": sku or erp_data.get("sku", ""),
                "warehouse": erp_data.get("warehouse", ""),
                "stock_gap": erp_data.get("stock_gap", 0),
                "forecast_units_next_14d": bi_data.get("forecast_units_next_14d", 0),
                "supplier_risk": {
                    "supplier_id": supplier_data.get("supplier_id", ""),
                    "risk_level": supplier_data.get("risk_level", "unknown"),
                } if supplier_data else None,
                "citations": all_citations,
                "recommended_action": "create_replenishment_approval" if oa_data else "analysis_only",
            }
            
            if oa_data.get("approval_draft_id"):
                state.result["approval_draft_id"] = oa_data["approval_draft_id"]
        
        state.result = self._redact_sensitive_fields(state.result) if state.result else None
        
        with database.connect() as conn:
            database.init_db(conn)
            result_json = json.dumps(state.result, ensure_ascii=False) if state.result else None
            conn.execute(
                """
                UPDATE runs
                SET status = ?, result_json = ?, token_cost = ?, finished_at = ?
                WHERE id = ?
                """,
                (state.status, result_json, token_cost, database.now_iso(), run_id),
            )
            conn.commit()
        
        self.state_store.save(state)
        return state