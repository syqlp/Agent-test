from __future__ import annotations

import os
from pathlib import Path

from agentops_assessment.agent.executor import Executor
from agentops_assessment.agent.planner import Planner
from agentops_assessment.agent.tools import ToolRegistry
from agentops_assessment.backend import database


def execute_run(run_id: str) -> None:
    with database.connect() as conn:
        database.init_db(conn)
        run_row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not run_row:
            return
        
        task_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (run_row["task_id"],)).fetchone()
        if not task_row:
            return
        
        prompt = task_row["prompt"]
        requested_by = run_row["requested_by"]
        
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (requested_by,)).fetchone()
        user_permissions = database.decode_json(user_row["permissions_json"], []) if user_row else []
    
    fixtures_dir = os.getenv("ASSESSMENT_FIXTURES_DIR", "fixtures")
    
    planner = Planner()
    plan = planner.create_plan(prompt)
    
    sku = None
    for step in plan:
        if "sku" in step.input_template:
            sku = step.input_template["sku"]
            break
    
    context = {
        "sku": sku,
        "user_permissions": user_permissions,
        "prompt": prompt,
    }
    
    registry = ToolRegistry.with_default_clients(
        fixtures_dir=fixtures_dir,
        retry_attempts=2,
    )
    
    executor = Executor(registry)
    
    with database.connect() as conn:
        database.init_db(conn)
        now = database.now_iso()
        conn.execute(
            "UPDATE runs SET status = ?, started_at = ? WHERE id = ?",
            ("running", now, run_id),
        )
        conn.commit()
    
    try:
        executor.execute(run_id, plan, context)
    except Exception as exc:
        with database.connect() as conn:
            database.init_db(conn)
            conn.execute(
                """
                UPDATE runs
                SET status = ?, error = ?, finished_at = ?
                WHERE id = ?
                """,
                ("failed", str(exc), database.now_iso(), run_id),
            )
            conn.commit()