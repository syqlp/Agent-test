from __future__ import annotations

import re
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from agentops_assessment.backend import database


def _decode_user(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "roles": database.decode_json(row["roles_json"], []),
        "permissions": database.decode_json(row["permissions_json"], []),
    }


def get_user(user_id: str) -> dict | None:
    with database.connect() as conn:
        database.init_db(conn)
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _decode_user(row) if row else None


def get_current_user(x_user_id: Annotated[str | None, Header()] = None) -> dict:
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 X-User-Id 请求头。",
        )
    user = get_user(x_user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"未知用户: {x_user_id}",
        )
    return user


def require_permissions(*permissions: str):
    def dependency(user: dict = Depends(get_current_user)) -> dict:
        missing = [p for p in permissions if p not in user["permissions"]]
        if missing:
            with database.connect() as conn:
                database.init_db(conn)
                database.insert_audit_log(
                    conn,
                    actor_id=user["id"],
                    action="permission.denied",
                    resource="api",
                    payload={"missing_permissions": missing, "request_path": "unknown"},
                    decision="deny",
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"missing_permissions": missing},
            )
        return user

    return dependency


def detect_prompt_injection(prompt: str) -> bool:
    injection_patterns = [
        r"忽略之前的所有指令",
        r"忽略之前指令",
        r"覆盖之前的所有指令",
        r"按照我的指令执行",
        r"执行我的命令",
        r"泄露.*密钥",
        r"获取.*密码",
        r"绕过.*安全",
        r"提升.*权限",
        r"删除.*日志",
        r"隐藏.*操作",
    ]
    
    for pattern in injection_patterns:
        if re.search(pattern, prompt, re.IGNORECASE):
            return True
    return False