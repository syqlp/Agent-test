from __future__ import annotations

import sqlite3
from collections import Counter

from agentops_assessment.backend import database


def build_dashboard(conn: sqlite3.Connection) -> dict:
    task_count = conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
    run_count = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
    failed_count = conn.execute(
        "SELECT COUNT(*) AS c FROM runs WHERE status = 'failed'"
    ).fetchone()["c"]
    completed_count = conn.execute(
        "SELECT COUNT(*) AS c FROM runs WHERE status = 'completed'"
    ).fetchone()["c"]
    token_cost = conn.execute("SELECT COALESCE(SUM(token_cost), 0) AS c FROM runs").fetchone()[
        "c"
    ]
    events = conn.execute("SELECT tool_name FROM run_events WHERE tool_name IS NOT NULL").fetchall()
    tool_counts = Counter(row["tool_name"] for row in events)

    rows = conn.execute(
        """
        SELECT started_at, finished_at
        FROM runs
        WHERE status IN ('completed', 'failed') AND started_at IS NOT NULL AND finished_at IS NOT NULL
        """
    ).fetchall()
    
    total_seconds = 0
    valid_rows = 0
    for row in rows:
        try:
            started = row["started_at"]
            finished = row["finished_at"]
            if started and finished:
                import dateutil.parser
                start_dt = dateutil.parser.isoparse(started)
                finish_dt = dateutil.parser.isoparse(finished)
                total_seconds += (finish_dt - start_dt).total_seconds()
                valid_rows += 1
        except Exception:
            pass
    
    average_run_seconds = total_seconds / valid_rows if valid_rows else 0

    recent_failures = conn.execute(
        """
        SELECT id, task_id, error, finished_at
        FROM runs
        WHERE status = 'failed'
        ORDER BY finished_at DESC
        LIMIT 10
        """
    ).fetchall()
    
    recent_failures_list = []
    for row in recent_failures:
        recent_failures_list.append({
            "run_id": row["id"],
            "task_id": row["task_id"],
            "error": row["error"],
            "failed_at": row["finished_at"],
        })

    queued_count = conn.execute(
        "SELECT COUNT(*) AS c FROM runs WHERE status = 'queued'"
    ).fetchone()["c"]
    running_count = conn.execute(
        "SELECT COUNT(*) AS c FROM runs WHERE status = 'running'"
    ).fetchone()["c"]

    return {
        "task_count": task_count,
        "run_count": run_count,
        "completed_count": completed_count,
        "failed_count": failed_count,
        "failure_rate": failed_count / run_count if run_count else 0,
        "token_cost": token_cost,
        "average_run_seconds": average_run_seconds,
        "tool_call_counts": dict(tool_counts),
        "recent_failures": recent_failures_list,
        "queue_health": {
            "queued": queued_count,
            "running": running_count,
        },
        "generated_at": database.now_iso(),
    }