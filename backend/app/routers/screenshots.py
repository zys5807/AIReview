"""截图上传与查询接口（多用户：按当前用户隔离）"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import BASE_DIR
from ..database import get_db
from ..models import Screenshot, Trade, TradeScreenshot, User
from ..routers.auth import get_current_user
from ..schemas import ScreenshotOut
from ..services import kline_recognize
from ..services import storage

router = APIRouter(prefix="/api/screenshots", tags=["截图"])


def delete_shot_file(shot: Screenshot):
    """删除截图对应的磁盘文件（文件不存在则忽略）"""
    path = BASE_DIR / shot.stored_path
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _get_owned_shot(db: Session, shot_id: int, user_id: int) -> Screenshot:
    shot = db.get(Screenshot, shot_id)
    if not shot or shot.user_id != user_id:
        raise HTTPException(status_code=404, detail="截图不存在")
    return shot


@router.post("", response_model=ScreenshotOut, status_code=201)
async def upload_screenshot(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """上传K线截图"""
    try:
        info = await storage.save_screenshot(file)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    shot = Screenshot(**info, user_id=user.id)
    db.add(shot)
    db.commit()
    db.refresh(shot)
    return shot


@router.get("", response_model=list[ScreenshotOut])
def list_screenshots(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """截图列表（当前用户，新→旧）"""
    return (
        db.query(Screenshot)
        .filter(Screenshot.user_id == user.id)
        .order_by(Screenshot.id.desc())
        .all()
    )


@router.get("/orphans", response_model=list[ScreenshotOut])
def list_orphans(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    days: int = Query(7, ge=0, description="仅列出超过N天前上传且未被任何交易引用的截图"),
):
    """列出"孤儿截图"：未被任何交易关联、且上传超过指定天数的截图（可安全清理）"""
    linked_ids = select(TradeScreenshot.screenshot_id).where(
        TradeScreenshot.trade_id.in_(select(Trade.id).where(Trade.user_id == user.id))
    )
    cutoff = datetime.now() - timedelta(days=days)
    return (
        db.query(Screenshot)
        .filter(
            Screenshot.user_id == user.id,
            ~Screenshot.id.in_(linked_ids),
            Screenshot.created_at < cutoff,
        )
        .order_by(Screenshot.id.desc())
        .all()
    )


@router.post("/cleanup-orphans")
def cleanup_orphans(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    days: int = Query(7, ge=0, description="仅清理超过N天前上传且未被任何交易引用的截图"),
):
    """清理"孤儿截图"（记录 + 磁盘文件），返回清理数量"""
    linked_ids = select(TradeScreenshot.screenshot_id).where(
        TradeScreenshot.trade_id.in_(select(Trade.id).where(Trade.user_id == user.id))
    )
    cutoff = datetime.now() - timedelta(days=days)
    orphans = (
        db.query(Screenshot)
        .filter(
            Screenshot.user_id == user.id,
            ~Screenshot.id.in_(linked_ids),
            Screenshot.created_at < cutoff,
        )
        .all()
    )
    count = 0
    for shot in orphans:
        delete_shot_file(shot)
        db.delete(shot)
        count += 1
    db.commit()
    return {"message": f"已清理 {count} 张无用截图", "cleaned": count}


@router.get("/{shot_id}", response_model=ScreenshotOut)
def get_screenshot(
    shot_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _get_owned_shot(db, shot_id, user.id)


@router.delete("/{shot_id}")
def delete_screenshot(
    shot_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删除截图（记录 + 磁盘文件）。若被交易引用则拒绝，需先解除关联"""
    shot = _get_owned_shot(db, shot_id, user.id)
    ref = db.query(TradeScreenshot).filter_by(screenshot_id=shot_id).first()
    if ref:
        raise HTTPException(
            status_code=409,
            detail="该截图正被交易引用，无法删除；请先编辑交易解除关联，或使用'清理无用截图'",
        )
    delete_shot_file(shot)
    db.delete(shot)
    db.commit()
    return {"message": "截图已删除"}


@router.post("/{shot_id}/recognize")
def recognize_screenshot(
    shot_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """对已上传的截图执行识别：品种/周期/箭头/备注等"""
    shot = _get_owned_shot(db, shot_id, user.id)

    # 还原文件路径
    file_path = BASE_DIR / shot.stored_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"截图文件已丢失: {shot.stored_path}")

    try:
        result = kline_recognize.recognize_screenshot(file_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"识别失败: {e}")

    return result.to_dict()
