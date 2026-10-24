"""数据模型与持久化：config/ 存配置，data/ 存每月排班数据。"""
from __future__ import annotations

import calendar
import copy
import datetime as _dt
import json
import os
from pathlib import Path

def _base_dir() -> Path:
    """打包成 exe 后，config/ 与 data/ 放在 exe 同级目录。"""
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
SCHEDULE_DIR = DATA_DIR / "schedules"
EXPORT_DIR = DATA_DIR / "exports"
BACKUP_DIR = DATA_DIR / "backups"

EMPLOYEES_FILE = CONFIG_DIR / "employees.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
META_FILE = DATA_DIR / "meta.json"


# --------------------------------------------------------------------------
# 目录与基础读写
# --------------------------------------------------------------------------
def ensure_dirs() -> None:
    """程序启动时若缺少 config / data 目录则创建。"""
    for folder in (CONFIG_DIR, DATA_DIR, SCHEDULE_DIR, EXPORT_DIR, BACKUP_DIR):
        folder.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return copy.deepcopy(default)


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def now_iso() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")



DEFAULT_SETTINGS = {
    "version": 1,
    "appTitle": "员工月排班冲突检查器",
    "weekStart": 1,
    "openers": {"enabled": True, "maxRepeat": 0},
    "generator": {"budgetSeconds": 6.0, "preferTwoPerShift": True},
    "lastMonth": None,
    "createdAt": None,
}


def load_settings() -> dict:
    ensure_dirs()
    data = _read_json(SETTINGS_FILE, DEFAULT_SETTINGS)
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    if isinstance(data, dict):
        merged.update(data)
    if not merged.get("createdAt"):
        merged["createdAt"] = now_iso()
        _write_json(SETTINGS_FILE, merged)
    return merged


def save_settings(settings: dict) -> None:
    ensure_dirs()
    _write_json(SETTINGS_FILE, settings)


# --------------------------------------------------------------------------
# 员工
# --------------------------------------------------------------------------
def load_employees() -> list[dict]:
    """员工只来自 config/employees.json；没有配置时返回空列表（由前端新增）。"""
    ensure_dirs()
    payload = _read_json(EMPLOYEES_FILE, {"version": 1, "employees": []})
    employees = payload.get("employees") if isinstance(payload, dict) else None
    if not isinstance(employees, list):
        employees = []
    return [_normalize_employee(emp, idx) for idx, emp in enumerate(employees)]


def _normalize_employee(emp: dict, idx: int) -> dict:
    # 休息天数：新字段 monthlyRest（全月），兼容旧的 weeklyRest（每周）
    monthly = emp.get("monthlyRest")
    if monthly in (None, "", 0):
        weekly = emp.get("weeklyRest")
        monthly = None if weekly in (None, "", 0) else round(_as_int(weekly, 0) * 30 / 7)
    else:
        monthly = _as_int(monthly, 0)
    prefer = emp.get("preferShift")
    # 单号/双号工作互斥：同时勾选时以「单号」为准
    _odd = bool(emp.get("oddOnly"))
    _even = bool(emp.get("evenOnly")) and not _odd
    out = {
        "id": str(emp.get("id") or f"e_{idx + 1}"),
        "name": str(emp.get("name") or f"员工{idx + 1}").strip()[:6],
        "color": str(emp.get("color") or "#a875ff"),
        "role": str(emp.get("role") or ""),
        "dutyTarget": _as_int(emp.get("dutyTarget"), 0),
        "dutyMax": _as_int(emp.get("dutyMax"), 0),
        "keyShift": bool(emp.get("keyShift")),
        "monthlyRest": monthly,
        "preferShift": prefer if prefer in ("early", "late") else None,
        "minEarly": _opt_int(emp.get("minEarly")),
        "maxEarly": _opt_int(emp.get("maxEarly")),
        "minLate": _opt_int(emp.get("minLate")),
        "maxLate": _opt_int(emp.get("maxLate")),
        "alternate": bool(emp.get("alternate")),
        "oddOnly": _odd,
        "evenOnly": _even,
        "shiftCycle": emp.get("shiftCycle") or None,
        "active": bool(emp.get("active", True)),
        "note": str(emp.get("note") or ""),
    }
    if out["dutyMax"] < out["dutyTarget"]:
        out["dutyMax"] = out["dutyTarget"]
    return out


def _opt_int(value):
    """空 / 0 → None（表示不限制）"""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def save_employees(employees: list[dict]) -> None:
    ensure_dirs()
    _write_json(EMPLOYEES_FILE, {"version": 1, "employees": employees})


# --------------------------------------------------------------------------
# 月度排班文档
# --------------------------------------------------------------------------
def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def month_path(year: int, month: int) -> Path:
    return SCHEDULE_DIR / f"{month_key(year, month)}.json"


def blank_schedule(year: int, month: int, employees: list[dict]) -> dict:
    days = days_in_month(year, month)
    return {
        str(d): {"morning": [], "evening": [], "duty": [], "opener": ""}
        for d in range(1, days + 1)
    }


def blank_month(year: int, month: int, employees: list[dict]) -> dict:
    days = days_in_month(year, month)
    return {
        "year": int(year),
        "month": int(month),
        "days": days,
        "schedule": blank_schedule(year, month, employees),
        "availability": {e["id"]: list(range(1, days + 1)) for e in employees},
        "extraRules": [],
        "ruleOverrides": {},
        "note": "",
        "source": "blank",
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
    }


def load_month(year: int, month: int, employees: list[dict]) -> dict:
    ensure_dirs()
    path = month_path(year, month)
    if path.exists():
        doc = _read_json(path, None)
        if isinstance(doc, dict):
            return normalize_month(doc, employees)
    # 首次进入该月份：直接空白，数据一律来自 data/schedules/
    return normalize_month(blank_month(year, month, employees), employees)


def normalize_month(doc: dict, employees: list[dict]) -> dict:
    year = _as_int(doc.get("year"), _dt.date.today().year)
    month = _as_int(doc.get("month"), _dt.date.today().month)
    days = days_in_month(year, month)
    base = blank_month(year, month, employees)
    out = copy.deepcopy(base)
    out["year"], out["month"], out["days"] = year, month, days
    out["note"] = str(doc.get("note") or "")
    out["source"] = str(doc.get("source") or "blank")
    out["createdAt"] = doc.get("createdAt") or now_iso()
    out["updatedAt"] = doc.get("updatedAt") or now_iso()

    raw_schedule = doc.get("schedule") or {}
    for d in range(1, days + 1):
        cell = raw_schedule.get(str(d)) or {}
        out["schedule"][str(d)] = {
            "morning": [str(x) for x in (cell.get("morning") or [])][:12],
            "evening": [str(x) for x in (cell.get("evening") or [])][:12],
            "duty": [str(x) for x in (cell.get("duty") or [])][:6],
            "opener": str(cell.get("opener") or ""),
        }

    valid_ids = {e["id"] for e in employees}
    raw_avail = doc.get("availability") or {}
    out["availability"] = {}
    for emp in employees:
        days_list = raw_avail.get(emp["id"])
        if not isinstance(days_list, list):
            days_list = list(range(1, days + 1))
        clean = sorted({_as_int(d, 0) for d in days_list if 1 <= _as_int(d, 0) <= days})
        out["availability"][emp["id"]] = clean

    extra = doc.get("extraRules") or []
    out["extraRules"] = [r for r in extra if isinstance(r, dict) and r.get("type")]

    overrides = doc.get("ruleOverrides") or {}
    out["ruleOverrides"] = {k: v for k, v in overrides.items() if isinstance(v, dict)}

    # 清理已被删除员工的引用
    for d in range(1, days + 1):
        cell = out["schedule"][str(d)]
        for key in ("morning", "evening", "duty"):
            cell[key] = [x for x in cell[key] if x in valid_ids]
        if cell["opener"] and cell["opener"] not in valid_ids:
            cell["opener"] = ""
    return out


def save_month(doc: dict) -> dict:
    ensure_dirs()
    doc = copy.deepcopy(doc)
    doc["updatedAt"] = now_iso()
    _write_json(month_path(doc["year"], doc["month"]), doc)
    return doc


def list_months() -> list[dict]:
    ensure_dirs()
    items = []
    for path in sorted(SCHEDULE_DIR.glob("*.json")):
        stem = path.stem
        try:
            year, month = stem.split("-")
            year, month = int(year), int(month)
        except ValueError:
            continue
        size = path.stat().st_size
        items.append({"key": stem, "year": year, "month": month, "bytes": size})
    return items




def _name_of(emp_id: str, by_name: dict) -> str:
    for name, eid in by_name.items():
        if eid == emp_id:
            return name
    return ""


def _split_names(raw: str, by_name: dict) -> list[str]:
    """'琳吴' -> [e_lin, e_wu]；兼容 '怡-琳'、'陈、范' 等写法。"""
    text = str(raw or "")
    out = []
    for ch in text:
        if ch in by_name:
            eid = by_name[ch]
            if eid not in out:
                out.append(eid)
    return out


def parse_names_to_ids(raw: str, employees: list[dict]) -> list[str]:
    """把任意分隔写法（含全名）解析为员工 id 列表。"""
    by_name = {e["name"]: e["id"] for e in employees}
    text = str(raw or "")
    out = []
    # 先按分隔符切分，再逐字符兜底
    for token in _tokenize(text):
        if token in by_name:
            eid = by_name[token]
        else:
            eid = None
            for ch in token:
                if ch in by_name:
                    eid = by_name[ch]
                    break
        if eid and eid not in out:
            out.append(eid)
    return out


def _tokenize(text: str) -> list[str]:
    seps = "|,，、/;； \t　-—–+"
    buf, tokens = "", []
    for ch in text:
        if ch in seps:
            if buf:
                tokens.append(buf)
                buf = ""
        else:
            buf += ch
    if buf:
        tokens.append(buf)
    return tokens


def meta() -> dict:
    ensure_dirs()
    return _read_json(META_FILE, {"version": 1})


def save_meta(payload: dict) -> None:
    ensure_dirs()
    _write_json(META_FILE, payload)
