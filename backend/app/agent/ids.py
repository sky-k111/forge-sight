"""不依赖数据库序列的稳定领域 ID。"""

from uuid import uuid4


def new_id(prefix: str) -> str:
    """生成适合日志和 API 使用的紧凑 ID。"""
    return f"{prefix}_{uuid4().hex[:24]}"

