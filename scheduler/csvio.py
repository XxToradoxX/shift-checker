"""CSV / Excel 导入导出。

导出两种布局：
  1) 长表（默认，与用户 Excel 表一致）：日期,星期,早班,晚班,值班,开门人,休息
  2) 矩阵：员工,1,2,3,...  单元格为 早/晚/值/空/不
导入时自动识别两种布局，也支持 .xlsx。
"""
from __future__ import annotations

import csv
import io
from . import model

ALIASES = {
    "day": ("日期", "日", "号", "day", "date"),
    "weekday": ("星期", "周几", "weekday"),
    "morning": ("早班", "早", "morning", "am"),
    "evening": ("晚班", "晚", "evening", "pm"),
    "duty": ("值班", "值", "duty", "oncall"),
    "opener": ("开门人", "开门", "开", "opener"),
    "rest": ("休息", "休", "rest", "off"),
}

STATUS_CODES = {
    "早": "early", "早班": "early", "early": "early", "A": "early",
    "晚": "late", "晚班": "late", "late": "late", "B": "late",
    "值": "duty", "值班": "duty", "duty": "duty", "D": "duty",
    "空": "off", "空班": "off", "休": "off", "off": "off", "-": "off", "": "off",
    "不": "off", "不可用": "off",
}


def export_csv(doc: dict, employees: list[dict]) -> str:
    by_id = {e["id"]: e["name"] for e in employees}
    days = int(doc["days"])
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["月份", f'{doc["year"]}-{int(doc["month"]):02d}'])
    writer.writerow(["日期", "星期", "早班", "晚班", "值班", "开门人", "休息"])
    for d in range(1, days + 1):
        cell = doc["schedule"].get(str(d)) or {}
        morning = [by_id.get(x, "") for x in cell.get("morning") or []]
        evening = [by_id.get(x, "") for x in cell.get("evening") or []]
        duty = [by_id.get(x, "") for x in cell.get("duty") or []]
        opener = by_id.get(cell.get("opener") or "", "")
        worked = set(morning) | set(evening) | set(duty)
        rest = [e["name"] for e in employees if e["name"] not in worked]
        writer.writerow([
            d,
            "一二三四五六日"[_weekday(doc["year"], doc["month"], d)],
            "、".join(morning),
            "、".join(evening),
            "、".join(duty),
            opener,
            "、".join(rest),
        ])
    writer.writerow([])
    writer.writerow(["# 员工统计"])
    writer.writerow(["员工", "早班", "晚班", "值班", "休息", "值班目标", "值班上限", "可用天数"])
    for e in employees:
        eid = e["id"]
        morning = evening = duty = work = 0
        for d in range(1, days + 1):
            cell = doc["schedule"][str(d)]
            in_m = eid in (cell.get("morning") or [])
            in_e = eid in (cell.get("evening") or [])
            in_d = eid in (cell.get("duty") or [])
            morning += 1 if in_m else 0
            evening += 1 if in_e else 0
            duty += 1 if in_d else 0
            # 值班当天同时算晚班，休息天数按「是否出勤」去重统计
            work += 1 if (in_m or in_e or in_d) else 0
        avail = len((doc.get("availability") or {}).get(eid) or [])
        writer.writerow([e["name"], morning, evening, duty, days - work,
                         e.get("dutyTarget") or 0, e.get("dutyMax") or 0, avail])
    return buf.getvalue()


def export_matrix_csv(doc: dict, employees: list[dict]) -> str:
    by_id = {e["id"]: e["name"] for e in employees}
    days = int(doc["days"])
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["员工"] + [str(d) for d in range(1, days + 1)])
    for e in employees:
        eid = e["id"]
        row = [e["name"]]
        for d in range(1, days + 1):
            cell = doc["schedule"][str(d)]
            if eid in (cell.get("duty") or []):
                row.append("值")
            elif eid in (cell.get("morning") or []):
                row.append("早")
            elif eid in (cell.get("evening") or []):
                row.append("晚")
            else:
                row.append("空")
        writer.writerow(row)
    return buf.getvalue()


def _weekday(year, month, day):
    import datetime
    return datetime.date(int(year), int(month), int(day)).weekday()


# --------------------------------------------------------------------------
# 导入
# --------------------------------------------------------------------------
def import_table(text: str, doc: dict, employees: list[dict]) -> dict:
    """解析 CSV 文本；返回 {'schedule': ..., 'availability': ..., 'layout': ...}"""
    rows = list(csv.reader(io.StringIO(text)))
    rows = [[(c or "").strip() for c in row] for row in rows]
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        raise ValueError("CSV 内容为空")

    header_idx, mapping = _detect_long(rows)
    if header_idx is not None:
        return _parse_long(rows, header_idx, mapping, doc, employees)
    header_idx, day_cols = _detect_matrix(rows, doc)
    if header_idx is not None:
        return _parse_matrix(rows, header_idx, day_cols, doc, employees)
    raise ValueError("无法识别表格格式：需要包含 日期/早班/晚班/值班 或 员工+日期矩阵 的表头")


def _match(token: str, keys) -> bool:
    token = token.replace(" ", "")
    return any(k and k in token for k in keys)


def _detect_long(rows):
    for idx, row in enumerate(rows[:8]):
        mapping = {}
        for col, title in enumerate(row):
            for key, aliases in ALIASES.items():
                if key in mapping:
                    continue
                if _match(title, aliases):
                    mapping[key] = col
        if "day" in mapping and ("morning" in mapping or "evening" in mapping or "duty" in mapping):
            return idx, mapping
    return None, {}


def _parse_long(rows, header_idx, mapping, doc, employees):
    days = int(doc["days"])
    schedule = {str(d): {"morning": [], "evening": [], "duty": [], "opener": ""} for d in range(1, days + 1)}
    used = 0
    for row in rows[header_idx + 1:]:
        raw_day = row[mapping["day"]] if mapping.get("day") is not None and mapping["day"] < len(row) else ""
        day = _to_day(raw_day)
        if not day or day < 1 or day > days:
            continue

        def names(key):
            col = mapping.get(key)
            if col is None or col >= len(row):
                return []
            return model.parse_names_to_ids(row[col], employees)

        schedule[str(day)]["morning"] = names("morning")
        schedule[str(day)]["evening"] = names("evening")
        schedule[str(day)]["duty"] = names("duty")
        op = names("opener")
        schedule[str(day)]["opener"] = op[0] if op else ""
        used += 1
    if not used:
        raise ValueError("没有解析到有效日期行")
    return {"schedule": schedule, "layout": "long", "rows": used}


def _detect_matrix(rows, doc):
    days = int(doc["days"])
    for idx, row in enumerate(rows[:6]):
        cols = {}
        for col, title in enumerate(row[1:], start=1):
            d = _to_day(title)
            if d and 1 <= d <= days:
                cols[col] = d
        if len(cols) >= min(10, days // 2) and row and _match(row[0], ALIASES["day"] + ("员工", "姓名", "人员")):
            return idx, cols
    return None, {}


def _parse_matrix(rows, header_idx, day_cols, doc, employees):
    days = int(doc["days"])
    by_name = {e["name"]: e["id"] for e in employees}
    schedule = {str(d): {"morning": [], "evening": [], "duty": [], "opener": ""} for d in range(1, days + 1)}
    count = 0
    for row in rows[header_idx + 1:]:
        if not row:
            continue
        name = (row[0] or "").strip()
        eid = by_name.get(name)
        if not eid:
            candidates = model.parse_names_to_ids(name, employees)
            eid = candidates[0] if candidates else None
        if not eid:
            continue
        count += 1
        for col, day in day_cols.items():
            if col >= len(row):
                continue
            status = STATUS_CODES.get((row[col] or "").strip(), None)
            if status is None:
                continue
            cell = schedule[str(day)]
            if status == "duty":
                cell["duty"].append(eid)
                cell["evening"].append(eid)
            elif status == "early":
                cell["morning"].append(eid)
            elif status == "late":
                cell["evening"].append(eid)
    if not count:
        raise ValueError("矩阵中没有匹配到员工姓名")
    return {"schedule": schedule, "layout": "matrix", "rows": count}


def _to_day(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def import_xlsx(path, doc: dict, employees: list[dict]) -> dict:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover
        raise ValueError("缺少 openpyxl，无法导入 Excel") from exc
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    rows = []
    for row in ws.iter_rows(values_only=True):
        rows.append(["" if c is None else str(c).strip() for c in row])
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for row in rows:
        writer.writerow(row)
    return import_table(buf.getvalue(), doc, employees)
