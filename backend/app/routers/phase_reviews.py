"""阶段复盘手写总结接口（V1.008：周/月粒度 × 品种维度，支持 txt/md/docx 导入）"""
import io
import json
import zipfile
import xml.etree.ElementTree as ET
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import PhaseReview, User
from ..routers.auth import get_current_user

router = APIRouter(prefix="/api/phase-reviews", tags=["阶段总结"])

VALID_PERIOD_TYPES = {"week", "month", "custom"}
MAX_CONTENT_LEN = 50000


class PhaseReviewIn(BaseModel):
    period_type: str = "week"  # week / month / custom
    start: date
    end: date
    instrument_type: str = ""  # ''=全部/通用、A股/商品期货/数字货币（决策2：只选品种，不考虑币种）
    title: str = ""
    content: str = ""


def _serialize(r: PhaseReview) -> dict:
    ai_summary = ""
    if r.ai_result:
        try:
            ai_summary = json.loads(r.ai_result).get("summary", "") or ""
        except Exception:
            pass
    return {
        "id": r.id,
        "period_type": r.period_type,
        "start": r.start_date.isoformat(),
        "end": r.end_date.isoformat(),
        "instrument_type": r.instrument_type,
        "title": r.title,
        "content": r.content,
        "has_ai_result": bool(r.ai_result),
        "ai_summary": ai_summary,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
        "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M:%S") if r.updated_at else "",
    }


def _validate(data: PhaseReviewIn) -> None:
    if data.period_type not in VALID_PERIOD_TYPES:
        raise HTTPException(status_code=400, detail="period_type 仅支持 week/month/custom")
    if data.start > data.end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    if not data.content.strip():
        raise HTTPException(status_code=400, detail="总结内容不能为空")
    if len(data.content) > MAX_CONTENT_LEN:
        raise HTTPException(status_code=400, detail=f"总结内容超过 {MAX_CONTENT_LEN} 字上限")
    if data.instrument_type not in ("", "A股", "商品期货", "数字货币"):
        raise HTTPException(status_code=400, detail="品种类型仅支持 全部/通用、A股、商品期货、数字货币")


def _parse_docx_text(raw: bytes) -> str:
    """解析 .docx 正文纯文本（零依赖：docx 本质为 zip，word/document.xml 中 <w:p> 段落、<w:t> 文本）

    仅提取正文段落文字，忽略表格/图片/样式；段落间以换行拼接。
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        xml_data = zf.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"无效的 Word 文档: {e}") from e

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        raise HTTPException(status_code=400, detail=f"Word 文档解析失败: {e}") from e

    lines = []
    for p in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
        texts = [t.text or "" for t in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")]
        lines.append("".join(texts))
    text = "\n".join(lines).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Word 文档中没有提取到正文文字")
    return text


@router.get("")
def list_phase_reviews(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    period_type: str | None = None,
    start: date | None = None,
    end: date | None = None,
    instrument_type: str | None = None,
):
    """查总结：给出 (period_type, start, end, instrument_type) 精确匹配单条；缺省返回该用户全部（时间倒序）"""
    q = db.query(PhaseReview).filter(PhaseReview.user_id == user.id)
    if period_type:
        q = q.filter(PhaseReview.period_type == period_type)
    if start and end:
        q = q.filter(PhaseReview.start_date == start, PhaseReview.end_date == end)
    if instrument_type:
        q = q.filter(PhaseReview.instrument_type == instrument_type)
    rows = q.order_by(PhaseReview.start_date.desc(), PhaseReview.id.desc()).all()
    return [_serialize(r) for r in rows]


@router.get("/history")
def list_phase_review_history(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    end: date | None = None,
    instrument_type: str | None = None,
    limit: int = 3,
):
    """AI 连续性用：end 之前的历史总结（时间倒序，默认 3 条）"""
    q = db.query(PhaseReview).filter(PhaseReview.user_id == user.id)
    if end:
        q = q.filter(PhaseReview.end_date < end)
    if instrument_type:
        q = q.filter(PhaseReview.instrument_type == instrument_type)
    rows = q.order_by(PhaseReview.start_date.desc()).limit(min(max(limit, 1), 20)).all()
    return [_serialize(r) for r in rows]


@router.post("")
def upsert_phase_review(
    data: PhaseReviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """新建或覆盖（同 user+period_type+start+end+instrument_type 键则更新内容，幂等）"""
    _validate(data)
    existing = (
        db.query(PhaseReview)
        .filter(
            PhaseReview.user_id == user.id,
            PhaseReview.period_type == data.period_type,
            PhaseReview.start_date == data.start,
            PhaseReview.end_date == data.end,
            PhaseReview.instrument_type == data.instrument_type,
        )
        .first()
    )
    if existing:
        existing.title = data.title
        existing.content = data.content
        db.commit()
        db.refresh(existing)
        return {"created": False, "review": _serialize(existing)}
    review = PhaseReview(
        user_id=user.id,
        period_type=data.period_type,
        start_date=data.start,
        end_date=data.end,
        instrument_type=data.instrument_type,
        title=data.title,
        content=data.content,
    )
    db.add(review)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # 并发兜底：键已存在则改为更新
        existing = (
            db.query(PhaseReview)
            .filter(
                PhaseReview.user_id == user.id,
                PhaseReview.period_type == data.period_type,
                PhaseReview.start_date == data.start,
                PhaseReview.end_date == data.end,
                PhaseReview.instrument_type == data.instrument_type,
            )
            .first()
        )
        if existing:
            existing.title = data.title
            existing.content = data.content
            db.commit()
            db.refresh(existing)
            return {"created": False, "review": _serialize(existing)}
        raise HTTPException(status_code=500, detail="保存失败，请重试")
    db.refresh(review)
    return {"created": True, "review": _serialize(review)}


@router.put("/{review_id}")
def update_phase_review(
    review_id: int,
    data: PhaseReviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """编辑总结（仅 title/content，归属校验）"""
    _validate(data)
    review = db.query(PhaseReview).filter(PhaseReview.id == review_id).first()
    if not review or review.user_id != user.id:
        raise HTTPException(status_code=404, detail="总结不存在")
    review.title = data.title
    review.content = data.content
    db.commit()
    db.refresh(review)
    return _serialize(review)


@router.delete("/{review_id}")
def delete_phase_review(
    review_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    review = db.query(PhaseReview).filter(PhaseReview.id == review_id).first()
    if not review or review.user_id != user.id:
        raise HTTPException(status_code=404, detail="总结不存在")
    db.delete(review)
    db.commit()
    return {"ok": True}


@router.post("/parse-file")
async def parse_phase_review_file(
    file: UploadFile,
    user: User = Depends(get_current_user),
):
    """解析导入文件为纯文本：.txt/.md 直读；.docx 用零依赖方式提取段落文字"""
    filename = (file.filename or "").lower()
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大（上限 5MB）")

    if filename.endswith(".docx"):
        return {"text": _parse_docx_text(raw)}
    if filename.endswith((".txt", ".md", ".markdown")):
        for enc in ("utf-8", "gbk", "gb18030"):
            try:
                return {"text": raw.decode(enc)}
            except UnicodeDecodeError:
                continue
        raise HTTPException(status_code=400, detail="文本文件编码无法识别（支持 UTF-8/GBK）")
    raise HTTPException(status_code=400, detail="仅支持 .txt / .md / .docx 格式")
