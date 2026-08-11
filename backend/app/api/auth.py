"""平台用户注册/登录/当前用户 HTTP API。"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth.deps import require_user
from app.auth.service import login, logout, register_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=6, max_length=64)
    displayName: str = Field(default="", max_length=50)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=6, max_length=64)


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest) -> dict:
    try:
        return register_user(payload.username, payload.password, payload.displayName)
    except ValueError as exc:
        message = str(exc)
        if message == "用户名已存在":
            raise HTTPException(status_code=409, detail=message) from exc
        raise HTTPException(status_code=400, detail=message) from exc


@router.post("/login")
def login_api(payload: LoginRequest) -> dict:
    try:
        return login(payload.username, payload.password)
    except ValueError as exc:
        # 失败统一 401，文案不泄露用户是否存在
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/me")
def me(user: dict = Depends(require_user)) -> dict:
    return {"user": user}


@router.post("/logout")
def logout_api(user: dict = Depends(require_user)) -> dict:
    logout(user["username"])
    return {"ok": True}
