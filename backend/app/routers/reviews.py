"""复盘报告接口：AI 分析与报告存储（多用户：按当前用户隔离）"""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import DEEPSEEK_MODEL
from ..database import get_db
from ..models import ReviewReport, Trade, User
from ..routers.auth import get_current_user
from ..schemas import ReviewReportOut
from ..services.analysis import AnalysisError, analyze_trade

router = APIRouter(prefix="/api", tags=["复盘报告"])


def _get_owned_trade_or_404(trade_id: int, db: Session, user_id: int) -> Trade:
    trade = db.get(Trade, trade_id)
    if not trade or trade.user_id != user_id:
        raise HTTPException(status_code=404, detail="交易记录不存在")
    return trade


@router.post("/trades/{trade_id}/analyze", response_model=ReviewReportOut)
def analyze_trade_endpoint(
    trade_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """对一笔交易发起 AI 复盘分析并保存报告"""
    trade = _get_owned_trade_or_404(trade_id, db, user.id)
    system = trade.trading_system

    try:
        result = analyze_trade(trade, system, db)
    except AnalysisError as e:
        raise HTTPException(status_code=502, detail=str(e))

    report = ReviewReport(
        trade_id=trade.id,
        user_id=user.id,
        score=result["score"],
        analysis=json.dumps(result, ensure_ascii=False),
        model_name=DEEPSEEK_MODEL,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


@router.get("/trades/{trade_id}/reports", response_model=list[ReviewReportOut])
def list_reports(
    trade_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """该交易的历史复盘报告（新→旧）"""
    _get_owned_trade_or_404(trade_id, db, user.id)
    return (
        db.query(ReviewReport)
        .filter(ReviewReport.trade_id == trade_id, ReviewReport.user_id == user.id)
        .order_by(ReviewReport.id.desc())
        .all()
    )


@router.get("/reports/{report_id}", response_model=ReviewReportOut)
def get_report(
    report_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = db.get(ReviewReport, report_id)
    if not report or report.user_id != user.id:
        raise HTTPException(status_code=404, detail="复盘报告不存在")
    return report
