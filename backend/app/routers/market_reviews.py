"""阶段盘面综述接口（V1.008.2 功能1）"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import MarketReview, User
from ..routers.auth import get_current_user
from ..services import market_review

router = APIRouter(prefix="/api/market-reviews", tags=["盘面综述"])

VALID_INSTRUMENTS = ("", "A股", "商品期货", "数字货币")


class MarketReviewIn(BaseModel):
    instrument_type: str = "A股"
    start: date
    end: date


def _serialize(r: MarketReview) -> dict:
    return market_review._serialize(r)


@router.post("/generate")
def generate(
    data: MarketReviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """生成（或覆盖）指定 品种 × 时间段的盘面综述。

    该操作会联网抓取行情 + 调用 LLM，耗时约 30~120 秒，请前端保持等待。
    失败时返回 HTTP 502（detail 带原因）。
    """
    if data.instrument_type not in VALID_INSTRUMENTS:
        raise HTTPException(status_code=400, detail="品种类型仅支持 全部/通用、A股、商品期货、数字货币")
    if data.start > data.end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    try:
        return market_review.generate_market_review(
            db, user.id, data.instrument_type, data.start, data.end
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"盘面综述生成失败: {e}") from e


@router.get("")
def list_market_reviews(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    instrument_type: str | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: int = 100,
):
    """列出盘面综述（可按品种/时间段过滤；时间倒序）"""
    q = db.query(MarketReview).filter(MarketReview.user_id == user.id)
    if instrument_type:
        q = q.filter(MarketReview.instrument_type == instrument_type)
    if start and end:
        q = q.filter(MarketReview.start_date == start, MarketReview.end_date == end)
    rows = q.order_by(MarketReview.start_date.desc(), MarketReview.id.desc()).limit(
        min(max(limit, 1), 500)
    ).all()
    return [_serialize(r) for r in rows]


@router.get("/{review_id}")
def get_market_review(
    review_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    r = db.query(MarketReview).filter(MarketReview.id == review_id).first()
    if not r or r.user_id != user.id:
        raise HTTPException(status_code=404, detail="盘面综述不存在")
    return _serialize(r)


@router.delete("/{review_id}")
def delete_market_review(
    review_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    r = db.query(MarketReview).filter(MarketReview.id == review_id).first()
    if not r or r.user_id != user.id:
        raise HTTPException(status_code=404, detail="盘面综述不存在")
    db.delete(r)
    db.commit()
    return {"ok": True}
