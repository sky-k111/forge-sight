"""平台用户注册、登录和邮箱验证 HTTP API。"""

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from app.auth.deps import require_user
from app.auth.email_verification import (
    EmailVerificationError,
    issue_email_challenge,
    verify_email_challenge,
)
from app.auth.mail_provider import get_verification_mail_provider
from app.auth.service import (
    auth_payload,
    get_user_by_username,
    login,
    logout,
    register_user,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=6, max_length=64)
    displayName: str = Field(default="", max_length=50)
    email: str = Field(min_length=3, max_length=254)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=6, max_length=64)


class EmailChallengeRequest(BaseModel):
    email: str | None = Field(default=None, min_length=3, max_length=254)


class EmailChangeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class EmailVerifyRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


EMAIL_ERROR_STATUS = {
    "AUTH_USERNAME_TAKEN": 409,
    "EMAIL_TAKEN": 409,
    "EMAIL_ALREADY_VERIFIED": 409,
    "CHALLENGE_RESEND_COOLDOWN": 429,
    "CHALLENGE_RATE_LIMITED": 429,
    "CHALLENGE_NOT_FOUND": 404,
    "EMAIL_SEND_FAILED": 502,
    "USER_NOT_FOUND": 404,
}


def _error_response(code: str, detail: str, *, retry_after: int | None = None) -> JSONResponse:
    body: dict[str, str | int] = {"detail": detail, "errorCode": code}
    if retry_after is not None:
        body["retryAfterSeconds"] = retry_after
    return JSONResponse(status_code=EMAIL_ERROR_STATUS.get(code, 400), content=body)


def _email_error_response(exc: EmailVerificationError) -> JSONResponse:
    return _error_response(exc.code, str(exc), retry_after=exc.retry_after_seconds)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest) -> dict:
    try:
        return register_user(payload.username, payload.password, payload.displayName, payload.email)
    except EmailVerificationError as exc:
        return _email_error_response(exc)
    except ValueError as exc:
        message = str(exc)
        if message == "用户名已存在":
            return _error_response("AUTH_USERNAME_TAKEN", message)
        return _error_response("AUTH_INVALID_CREDENTIALS", message)


@router.post("/login")
def login_api(payload: LoginRequest) -> dict:
    try:
        return login(payload.username, payload.password)
    except ValueError as exc:
        code = "AUTH_ACCOUNT_DISABLED" if str(exc) == "账号已停用" else "AUTH_INVALID_CREDENTIALS"
        return JSONResponse(status_code=401, content={"detail": str(exc), "errorCode": code})


@router.get("/me")
def me(user: dict = Depends(require_user)) -> dict:
    return {"user": user}


@router.post("/logout")
def logout_api(user: dict = Depends(require_user)) -> dict:
    logout(user["username"])
    return {"ok": True}


@router.post("/email/challenges")
def create_email_challenge(
    payload: EmailChallengeRequest,
    request: Request,
    user: dict = Depends(require_user),
):
    stored_user = get_user_by_username(user["username"])
    if stored_user is None:
        return _error_response("USER_NOT_FOUND", "用户不存在")
    if stored_user.email_verified_at is not None:
        return _error_response("EMAIL_ALREADY_VERIFIED", "该邮箱已完成验证")
    target_email = payload.email or stored_user.email or ""
    purpose = "verify" if stored_user.email else "bind"
    try:
        issued = issue_email_challenge(
            stored_user.id,
            target_email,
            purpose,
            _client_ip(request),
            provider=get_verification_mail_provider(),
        )
    except EmailVerificationError as exc:
        return _email_error_response(exc)
    return {
        "ok": True,
        "emailMasked": issued.email_masked,
        "retryAfterSeconds": issued.retry_after_seconds,
        "purpose": issued.purpose,
    }


@router.post("/email/change")
def change_email(
    payload: EmailChangeRequest,
    request: Request,
    user: dict = Depends(require_user),
):
    try:
        issued = issue_email_challenge(
            user["id"],
            payload.email,
            "change",
            _client_ip(request),
            provider=get_verification_mail_provider(),
        )
    except EmailVerificationError as exc:
        return _email_error_response(exc)
    return {
        "ok": True,
        "emailMasked": issued.email_masked,
        "retryAfterSeconds": issued.retry_after_seconds,
        "purpose": issued.purpose,
    }


@router.post("/email/verify")
def verify_email(
    payload: EmailVerifyRequest,
    request: Request,
    user: dict = Depends(require_user),
):
    try:
        verified_user = verify_email_challenge(
            user["id"], payload.email, payload.code, _client_ip(request)
        )
    except EmailVerificationError as exc:
        return _email_error_response(exc)
    return auth_payload(verified_user)
