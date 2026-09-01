"""交割单导入接口"""
import os
import tempfile
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..routers.auth import get_current_user
from ..schemas import MessageOut
from ..services import importer as import_svc

router = APIRouter(prefix="/api/import", tags=["交割单导入"])

_TMP_DIR = os.path.join(tempfile.gettempdir(), "aireview_import")
os.makedirs(_TMP_DIR, exist_ok=True)


@router.post("/parse")
async def parse_import_file(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """上传交割单文件并解析，返回表头/预览/列映射"""
    _cleanup_old_tmp()
    if not file.filename or "." not in file.filename:
        raise HTTPException(status_code=400, detail="文件名无效")
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ("xlsx", "xlsm", "csv", "txt", "xls"):
        raise HTTPException(status_code=400, detail="仅支持 xlsx / csv / txt 文件")

    fid = uuid.uuid4().hex
    tmp_path = os.path.join(_TMP_DIR, f"{fid}.{ext}")
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大（上限 20MB）")
    with open(tmp_path, "wb") as f:
        f.write(content)

    try:
        result = import_svc.parse_file(tmp_path)
    except import_svc.ImporterError as e:
        _cleanup(tmp_path)
        raise HTTPException(status_code=422, detail=str(e))
    result["file_id"] = fid
    return result


@router.post("/execute", response_model=MessageOut)
def execute_import(
    data: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """按列映射执行导入：增量合并成交明细 → 更新/新建交易"""
    file_id = data.get("file_id", "")
    mapping = data.get("mapping") or {}
    tmp_path = None
    for f in os.listdir(_TMP_DIR):
        if f.startswith(file_id):
            tmp_path = os.path.join(_TMP_DIR, f)
            break
    if not tmp_path or not os.path.exists(tmp_path):
        raise HTTPException(status_code=400, detail="解析结果已过期，请重新上传文件")

    success = False
    try:
        rows = import_svc._read_cells(tmp_path)
        header_idx = import_svc.locate_header(rows)
        header = rows[header_idx]
        data_rows = import_svc.extract_rows(rows, header_idx)

        # 合并用户修正后的映射
        merged = dict(import_svc.build_mapping(header))
        for k, v in (mapping or {}).items():
            if v is not None:
                merged[k] = v

        required = {"datetime", "code", "direction", "price", "volume"}
        missing = [f for f in required if f not in merged]
        if missing:
            names = {"datetime": "成交日期", "code": "证券代码", "direction": "买卖方向",
                     "price": "成交价格", "volume": "成交数量"}
            raise HTTPException(status_code=422,
                                detail=f"缺少必要列：{'、'.join(names[m] for m in missing)}。请回到上一步指定列对应关系")

        records, skip = import_svc.normalize_records(data_rows, merged)
        if not records:
            reason = _skip_message(skip)
            raise HTTPException(status_code=422, detail=f"未解析到有效成交记录：{reason}")

        # 增量合并：与数据库中未平仓交易逐条合并
        result = import_svc.merge_incremental(db, records, user.id)
        db.commit()
        success = True
    finally:
        # 只有成功才清理临时文件；失败保留，方便修正列映射后重试
        if success:
            _cleanup(tmp_path)

    msg = f"新增 {result['imported_new']} 笔交易，合并 {result['merged_actions']} 次加仓/减仓"
    if result["skipped"]:
        msg += f"，跳过 {result['skipped']} 条重复成交"
    return {"message": msg}


def _cleanup(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _skip_message(skip: dict) -> str:
    """把跳过原因转成可读提示"""
    labels = {
        "no_direction": "买卖方向未识别",
        "no_price": "成交价格无法解析",
        "no_volume": "成交数量无法解析",
        "no_datetime": "成交日期无法解析",
        "no_code": "证券代码为空",
    }
    parts = []
    total = skip.get("rows", 0)
    for key, label in labels.items():
        n = skip.get(key, 0)
        if n:
            parts.append(f"{label} {n} 行")
    if parts:
        return f"共 {total} 行，{'，'.join(parts)}。请检查列对应关系是否正确"
    return "共 {total} 行，请检查文件内容".format(total=total)


def _cleanup_old_tmp(max_hours=1):
    """清理过期的临时解析文件，避免堆积"""
    import time
    now = time.time()
    for f in os.listdir(_TMP_DIR):
        p = os.path.join(_TMP_DIR, f)
        try:
            if now - os.path.getmtime(p) > max_hours * 3600:
                os.remove(p)
        except Exception:
            pass
