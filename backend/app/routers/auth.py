"""认证与用户管理接口：注册 / 登录 / 当前用户 / 修改密码 / 管理员用户管理"""
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ReviewReport, Screenshot, Trade, TradingSystem, User
from ..schemas import UserOut
from ..services.security import create_token, hash_password, verify_password, verify_token

router = APIRouter(prefix="/api", tags=["认证"])


class AuthIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=100)


class UserStatusIn(BaseModel):
    is_active: bool


# ---------- 鉴权依赖 ----------
def get_current_user(
    authorization: str = Header(default=""), db: Session = Depends(get_db)
) -> User:
    """解析 Bearer Token，返回当前登录用户"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    payload = verify_token(authorization[7:].strip())
    if not payload:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="账号不可用或不存在")
    return user


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    """要求当前用户是管理员"""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


# ---------- 认证接口 ----------
@router.post("/auth/register", response_model=dict)
def register(data: AuthIn, db: Session = Depends(get_db)):
    """注册。第一个注册用户自动成为管理员，并继承历史数据"""
    if db.query(User).filter_by(username=data.username).first():
        raise HTTPException(status_code=409, detail="用户名已存在")

    is_first = db.query(User).count() == 0
    user = User(
        username=data.username,
        password_hash=hash_password(data.password),
        is_admin=1 if is_first else 0,
    )
    db.add(user)
    db.flush()

    # 第一个用户继承历史数据（升级前已有的交易/截图/系统/报告）
    if is_first:
        for model in (Trade, Screenshot, TradingSystem, ReviewReport):
            db.query(model).filter(model.user_id.is_(None)).update(
                {model.user_id: user.id}
            )

    db.commit()
    return {
        "token": create_token(user.id),
        "user": UserOut.model_validate(user).model_dump(),
    }


@router.post("/auth/login", response_model=dict)
def login(data: AuthIn, db: Session = Depends(get_db)):
    """登录"""
    user = db.query(User).filter_by(username=data.username).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")
    return {
        "token": create_token(user.id),
        "user": UserOut.model_validate(user).model_dump(),
    }


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    """当前登录用户信息"""
    return user


@router.post("/auth/change-password")
def change_password(
    data: ChangePasswordIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """修改自己的密码"""
    if not verify_password(data.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")
    user.password_hash = hash_password(data.new_password)
    db.commit()
    return {"message": "密码已修改"}


# ---------- 管理员：用户管理 ----------
@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(get_current_admin)):
    """用户列表（管理员）"""
    return db.query(User).order_by(User.id.asc()).all()


@router.patch("/users/{user_id}", response_model=UserOut)
def set_user_status(
    user_id: int,
    data: UserStatusIn,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """启用/禁用用户（管理员），不能禁用自己"""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="不能禁用当前管理员账号")
    user.is_active = int(data.is_active)
    db.commit()
    return user
