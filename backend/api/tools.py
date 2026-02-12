"""
API routes for SQL and Python tool execution.
Thin route layer - business logic lives in services/tool_executor.py
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services.tool_executor import (
    execute_python_tool,
    execute_sql_tool,
    get_sandbox_manager,
)

router = APIRouter(prefix="/api/tools", tags=["tools"])


class SqlToolRequest(BaseModel):
    worldline_id: str
    sql: str
    limit: int = Field(default=1000, ge=1, le=100_000)
    allowed_external_aliases: list[str] | None = None
    call_id: str | None = None


class PythonToolRequest(BaseModel):
    worldline_id: str
    code: str
    timeout: int = Field(default=60, ge=1, le=600)
    call_id: str | None = None


@router.post("/sql")
async def run_sql(body: SqlToolRequest):
    return await execute_sql_tool(
        worldline_id=body.worldline_id,
        sql=body.sql,
        limit=body.limit,
        allowed_external_aliases=body.allowed_external_aliases,
        call_id=body.call_id,
    )


@router.post("/python")
async def run_python(body: PythonToolRequest):
    return await execute_python_tool(
        worldline_id=body.worldline_id,
        code=body.code,
        timeout=body.timeout,
        call_id=body.call_id,
    )


# Re-export for main.py usage
__all__ = ["router", "get_sandbox_manager"]
