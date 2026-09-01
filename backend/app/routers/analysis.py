"""阶段性复盘分析接口（多用户：按当前用户隔离）"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..routers.auth import get_current_user
from ..services import period_stats
from ..services.analysis import AnalysisError, phase_summary

router = APIRouter(prefix="/api/analysis", tags=["阶段复盘"])


class PeriodAiIn(BaseModel):
    start: datetime
    end: datetime
    instrument_type: str | None = None


@router.get("/period")
def get_period_stats(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    start: datetime | None = Query(None, description="开始时间"),
    end: datetime | None = Query(None, description="结束时间"),
    instrument_type: str | None = Query(None, description="品种类型筛选"),
):
    """时间段统计：汇总 + 按日 + 按品种"""
    if not start:
        start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if not end:
        end = datetime.now()
    return period_stats.period_stats(db, start, end, instrument_type, user.id)


@router.get("/scores")
def get_score_trend(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    start: datetime | None = Query(None, description="开始时间"),
    end: datetime | None = Query(None, description="结束时间"),
    instrument_type: str | None = Query(None, description="品种类型筛选"),
):
    """评分趋势 + 维度平均分 + 常见问题"""
    if not start:
        start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if not end:
        end = datetime.now()
    return period_stats.score_trend(db, start, end, instrument_type, user.id)


@router.post("/period-ai")
def period_ai_analysis(
    data: PeriodAiIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """对所选时间段内的所有交易做 AI 阶段性复盘分析"""
    try:
        return phase_summary(db, user.id, data.start, data.end, data.instrument_type)
    except AnalysisError as e:
        raise HTTPException(status_code=502, detail=str(e))
