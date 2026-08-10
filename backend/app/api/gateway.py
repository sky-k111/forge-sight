"""data-gateway 调试接口。正常流程下 SQL 由 Agent 生成，此接口用于开发验证。"""

from dataclasses import asdict

from fastapi import APIRouter
from pydantic import BaseModel

from app.gateway.executor import execute_readonly

router = APIRouter(prefix="/api/gateway", tags=["gateway"])


class QueryRequest(BaseModel):
    sql: str


@router.post("/query")
def run_query(request: QueryRequest):
    return asdict(execute_readonly(request.sql, action="手动执行 SQL"))
