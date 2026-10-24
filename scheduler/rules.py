"""规则的量化定义、规则目录、以及冲突检查引擎。

每条基础规则都给出：
  desc   —— 业务描述
  quant  —— 量化表达（可直接用于程序判定）
  params —— 可调参数
新增的月度特殊规则同样被量化成「断言」，参与同一套检查。
"""
from __future__ import annotations

import calendar
import datetime as _dt

# 严重级别：error = 硬冲突（橙红），warn = 提示（弱化显示）
SEV_ERROR = "error"
SEV_WARN = "warn"


def _p(key, label, ptype="int", **kw):
    item = {"key": key, "label": label, "type": ptype}
    item.update(kw)
    return item


RULE_CATALOG: list[dict] = [
    {
        "code": "R1",
        "name": "早晚班人数区间",
        "severity": SEV_ERROR,
        "enabled": True,
        "params": {"min": 2, "max": 3, "prefer": 2, "warnThree": False},
        "paramsSpec": [
            _p("min", "每班最少人数", min=1, max=6),
            _p("max", "每班最多人数", min=1, max=8),
            _p("prefer", "优先人数", min=1, max=6),
            _p("warnThree", "三人班次也报警", "bool"),
        ],
        "desc": "早班、晚班各安排 2~3 人，以 2 人一班优先。",
        "action": "调整当天该班次的人数：从空闲多的员工里补人或移出多余的人。",
        "quant": "∀d:min ≤ |早(d)| ≤ max ∧ min ≤ |晚(d)| ≤ max；|早(d)|>prefer 或 |晚(d)|>prefer 记提示。",
    },
    {
        "code": "R2",
        "name": "同日不得双班",
        "severity": SEV_ERROR,
        "enabled": True,
        "params": {},
        "paramsSpec": [],
        "desc": "上早班的人当天不能再安排晚班（上一休一员工除外：他们当天要早晚一起上）。",
        "action": "把其中一个人改到另一个班次或空班，避免同一天双班。",
        "quant": "∀d:早(d) ∩ 晚(d) = ∅，但 alternate(e)=true 的员工不计入。",
    },
    {
        "code": "R3",
        "name": "每日必须值班",
        "severity": SEV_ERROR,
        "enabled": True,
        "params": {"min": 1, "max": 1},
        "paramsSpec": [
            _p("min", "每日最少值班人数", min=0, max=4),
            _p("max", "每日最多值班人数", min=1, max=6),
        ],
        "desc": "每天必须安排员工值班，默认 1 人。",
        "action": "当天安排一名员工值班。",
        "quant": "∀d:min ≤ |值(d)| ≤ max。",
    },
    {
        "code": "R4",
        "name": "上一休一",
        "severity": SEV_ERROR,
        "enabled": True,
        "params": {"strictRest": False},
        "paramsSpec": [_p("strictRest", "连续休息也判为冲突", "bool")],
        "desc": "标记为「上一休一」的员工：上班那天早班和晚班一起上，第二天全天休息。",
        "action": "按上一休一节奏：上班日早班+晚班都排，次日整体空班。",
        "quant": "上班日:早(e)∧晚(e) 必须同时成立；∀d:work(d) ≠ work(d+1)。",
    },
    {
        "code": "R5",
        "name": "值班次数配额",
        "severity": SEV_ERROR,
        "enabled": True,
        "params": {"tolerance": 0},
        "paramsSpec": [_p("tolerance", "允许偏差(天)", min=0, max=4)],
        "desc": "按员工属性里的「值班目标 / 值班上限」校验：实际值班天数应落在目标附近，且不超过上限。",
        "action": "把超出次数的值班换给别人，或调整该员工的值班上限。",
        "quant": "∀e∈值班岗:dutyCount(e) ∈ [target-tol, max]，低于 target-tol 记不足，高于 max 记超限。",
    },
    {
        "code": "R6",
        "name": "值班→晚班+次日休息",
        "severity": SEV_ERROR,
        "enabled": True,
        "params": {"requireEvening": True, "nextDayRest": True},
        "paramsSpec": [
            _p("requireEvening", "值班当日必须排晚班", "bool"),
            _p("nextDayRest", "值班次日必须空班", "bool"),
        ],
        "desc": "值班当天需同时安排晚班；值班日的第二天安排休息（空班）。",
        "action": "值班日务必同时排晚班；值班次日不要安排任何班次。",
        "quant": "∀d,e∈值(d):e∈晚(d)；work(e,d+1)=false。",
    },
    {
        "code": "R7",
        "name": "关键岗覆盖",
        "severity": SEV_ERROR,
        "enabled": True,
        "params": {"shifts": "both"},
        "paramsSpec": [],
        "desc": "早班与晚班都必须至少包含一名标记为「关键岗」的员工。",
        "action": "在该班次补一名关键岗员工，或把关键岗从另一个班次调过来。",
        "quant": "∀d:早(d) ∩ 关键 = ∅ 或 晚(d) ∩ 关键 = ∅ → 冲突（关键 = 标记为「关键岗」的员工集合）。",
    },
    {
        "code": "R8",
        "name": "早晚轮换",
        "severity": SEV_WARN,
        "enabled": True,
        "params": {"ignoreDuty": True},
        "paramsSpec": [_p("ignoreDuty", "值班日不参与判定", "bool")],
        "desc": "标记为「早晚轮换」的员工：上班时早班、晚班交替，值班日不参与判定。",
        "action": "让该员工早班、晚班交替出现，避免连着上同一个班。",
        "quant": "∀d:若 e(d)、e(d+1) 均出勤且均非值班，则 shift(d) ≠ shift(d+1)。",
    },
    {
        "code": "R9",
        "name": "值班补位",
        "severity": SEV_ERROR,
        "enabled": True,
        "params": {"triggerName": "怡", "supportName": "吴"},
        "paramsSpec": [
            _p("triggerName", "触发人姓名", "text"),
            _p("supportName", "补位人姓名", "text"),
        ],
        "desc": "当「触发员工」值班时，早班必须安排「支援员工」。（两名员工在规则参数里指定）",
        "action": "触发员工值班时，把支援员工排进当天早班。",
        "quant": "∀d:触发员工∈晚(d) ∧ ∈值(d) → 支援员工∈早(d)。",
    },
    {
        "code": "R10",
        "name": "休息天数配额",
        "severity": SEV_WARN,
        "enabled": True,
        "params": {"tolerance": 1},
        "paramsSpec": [_p("tolerance", "允许偏差(天)", min=0, max=5)],
        "desc": "按员工属性里的「全月休息天数」校验：实际休息天数与该值相差不超过容差。",
        "action": "增减该员工的上班天数，让休息天数回到目标附近。",
        "quant": "∀e:|rest(e) − monthlyRest(e)| ≤ tolerance，超出记提示。",
    },
    {
        "code": "R11",
        "name": "不可用日约束",
        "severity": SEV_ERROR,
        "enabled": True,
        "params": {},
        "paramsSpec": [],
        "desc": "员工标记为不可用日的日期不得安排任何班次。",
        "action": "把该员工当天改到不可用日之外，或调整可用日设置。",
        "quant": "∀e,d:d∉avail(e) → work(e,d)=false。",
    },
    {
        "code": "R12",
        "name": "开门人轮换",
        "severity": SEV_ERROR,
        "enabled": True,
        "params": {"maxRepeat": 0, "require": True},
        "paramsSpec": [
            _p("maxRepeat", "允许连开天数(0=不连开)", min=0, max=3),
            _p("require", "必须指定开门人", "bool"),
        ],
        "desc": "每日 1 名开门人，必须来自当天早班，且不重复同一人连续开门。",
        "action": "从当天早班里重新指定开门人，避免连续同一个人开门。",
        "quant": "∀d:opener(d)∈早(d) ∧ |{opener(d)}|=1；opener(d) ≠ opener(d-1)（maxRepeat=0 时）。",
    },
    {
        "code": "R13",
        "name": "连续上班上限",
        "severity": SEV_WARN,
        "enabled": False,
        "params": {"maxConsecutive": 6},
        "paramsSpec": [_p("maxConsecutive", "最多连续上班天数", min=2, max=31)],
        "desc": "连续上班天数不得超过上限（默认关闭，可开启）。",
        "action": "连续上班超限时，中间插入一天空班。",
        "quant": "∀e:maxRun(e) ≤ maxConsecutive。",
    },
    {
        "code": "R14",
        "name": "连续早班限制",
        "severity": SEV_WARN,
        "enabled": True,
        "params": {"warnDays": 2, "hardDays": 3},
        "paramsSpec": [
            _p("warnDays", "连续天数≥此值 → 提示", min=2, max=7),
            _p("hardDays", "连续天数≥此值 → 冲突", min=2, max=10),
        ],
        "desc": "不建议连续两天上早班；连续三天及以上早班直接判为冲突。",
        "action": "把连续早班中的一天改成晚班或空班。",
        "quant": "run(早,e) ≥ hardDays → 冲突；≥ warnDays → 提示。",
    },
    {
        "code": "R15",
        "name": "避免连续晚班",
        "severity": SEV_WARN,
        "enabled": False,
        "params": {"maxConsecutive": 2},
        "paramsSpec": [_p("maxConsecutive", "最多连续晚班天数", min=1, max=7)],
        "desc": "尽量不要连续上晚班（默认关闭，可按需开启）。",
        "action": "把连续晚班中的一天改成早班或空班。",
        "quant": "∀e:run(晚,e) ≤ maxConsecutive。",
    },
    {
        "code": "R18",
        "name": "开门人分配均衡",
        "severity": SEV_WARN,
        "enabled": True,
        "params": {"tolerance": 1},
        "paramsSpec": [_p("tolerance", "允许偏差(次)", min=0, max=5)],
        "desc": "开门人尽量平均分给早班的人，不要总是同几个人开门。",
        "action": "把开门次数多的人的开门日，换给开门次数少的人。",
        "quant": "∀e:|open(e) − 平均开门次数| ≤ tolerance。",
    },
    {
        "code": "R17",
        "name": "值班间隔均衡",
        "severity": SEV_WARN,
        "enabled": True,
        "params": {"minGap": 3},
        "paramsSpec": [_p("minGap", "两次值班最少间隔(天)", min=2, max=15)],
        "desc": "值班尽量平均分布，避免出现「值班一天、休息一天、又值班」的密集节奏。",
        "action": "把其中一次值班换到间隔更远的日子。",
        "quant": "∀相邻值班日 d1<d2：d2 − d1 ≥ minGap。",
    },
    {
        "code": "R16",
        "name": "班次数上下限",
        "severity": SEV_ERROR,
        "enabled": True,
        "params": {},
        "paramsSpec": [],
        "desc": "按员工属性里的「早班/晚班 最少~最多 天数」校验（晚班含值班日）。",
        "action": "在格子上调整该员工的早/晚班次数，或修改上下限。",
        "quant": "∀e:minEarly(e) ≤ |早(e)| ≤ maxEarly(e)；minLate(e) ≤ |晚(e)| ≤ maxLate(e)。",
    },
    {
        "code": "R19",
        "name": "仅在单号工作",
        "severity": SEV_ERROR,
        "enabled": True,
        "params": {},
        "paramsSpec": [],
        "desc": "标记为「单号工作」的员工，只允许在 1、3、5… 等单数日期上班；双数日期必须休息。",
        "action": "把这些天改到单数日期，或取消该员工的「单号工作」标记。",
        "quant": "∀e:oddOnly(e) ⇒ 双数日 e 不得出现于早/晚/值。",
    },
    {
        "code": "R20",
        "name": "仅在双号工作",
        "severity": SEV_ERROR,
        "enabled": True,
        "params": {},
        "paramsSpec": [],
        "desc": "标记为「双号工作」的员工，只允许在 2、4、6… 等双数日期上班；单数日期必须休息。",
        "action": "把这些天改到双数日期，或取消该员工的「双号工作」标记。",
        "quant": "∀e:evenOnly(e) ⇒ 单数日 e 不得出现于早/晚/值。",
    },
]

EXTRA_RULE_TYPES: list[dict] = [
    {
        "type": "rest", "name": "指定某人某天休息", "severity": SEV_ERROR,
        "desc": "该员工当天不得安排早班/晚班/值班。",
        "fields": [{"key": "person", "label": "员工", "type": "person"},
                   {"key": "day", "label": "日期", "type": "day"}],
    },
    {
        "type": "early", "name": "指定某人某天早班", "severity": SEV_ERROR,
        "desc": "该员工当天必须出现在早班。",
        "fields": [{"key": "person", "label": "员工", "type": "person"},
                   {"key": "day", "label": "日期", "type": "day"}],
    },
    {
        "type": "late", "name": "指定某人某天晚班", "severity": SEV_ERROR,
        "desc": "该员工当天必须出现在晚班。",
        "fields": [{"key": "person", "label": "员工", "type": "person"},
                   {"key": "day", "label": "日期", "type": "day"}],
    },
    {
        "type": "duty", "name": "指定某人某天值班", "severity": SEV_ERROR,
        "desc": "该员工当天必须值班。",
        "fields": [{"key": "person", "label": "员工", "type": "person"},
                   {"key": "day", "label": "日期", "type": "day"}],
    },
    {
        "type": "unavailable", "name": "指定某人某天不可用", "severity": SEV_ERROR,
        "desc": "该员工当天视为不可用日（请假/外派）。",
        "fields": [{"key": "person", "label": "员工", "type": "person"},
                   {"key": "day", "label": "日期", "type": "day"}],
    },
    {
        "type": "no_duty", "name": "指定某人某天不值班", "severity": SEV_ERROR,
        "desc": "该员工当天不得安排值班。",
        "fields": [{"key": "person", "label": "员工", "type": "person"},
                   {"key": "day", "label": "日期", "type": "day"}],
    },
    {
        "type": "opener", "name": "指定某天开门人", "severity": SEV_ERROR,
        "desc": "该日期开门人固定为指定员工（须在早班）。",
        "fields": [{"key": "person", "label": "员工", "type": "person"},
                   {"key": "day", "label": "日期", "type": "day"}],
    },
    {
        "type": "headcount", "name": "指定某天某班人数", "severity": SEV_ERROR,
        "desc": "指定日期早班或晚班的固定人数。",
        "fields": [{"key": "day", "label": "日期", "type": "day"},
                   {"key": "shift", "label": "班次", "type": "shift"},
                   {"key": "count", "label": "人数", "type": "int", "min": 0, "max": 8}],
    },
    {
        "type": "duty_count", "name": "指定某人本月值班次数", "severity": SEV_ERROR,
        "desc": "覆盖该员工当月的值班次数目标。",
        "fields": [{"key": "person", "label": "员工", "type": "person"},
                   {"key": "count", "label": "次数", "type": "int", "min": 0, "max": 31}],
    },
    {
        "type": "rest_range", "name": "指定某人日期区间休息", "severity": SEV_ERROR,
        "desc": "该员工在起止日期之间（含）全部休息。",
        "fields": [{"key": "person", "label": "员工", "type": "person"},
                   {"key": "from", "label": "起始日", "type": "day"},
                   {"key": "to", "label": "结束日", "type": "day"}],
    },
    {
        "type": "pair_separate", "name": "两人不得同班", "severity": SEV_ERROR,
        "desc": "两名员工不得被安排在同一个班次。",
        "fields": [{"key": "a", "label": "员工A", "type": "person"},
                   {"key": "b", "label": "员工B", "type": "person"},
                   {"key": "shift", "label": "班次", "type": "shift", "optional": True}],
    },
    {
        "type": "pair_together", "name": "两人必须同班", "severity": SEV_ERROR,
        "desc": "两名员工必须被安排在同一个班次。",
        "fields": [{"key": "a", "label": "员工A", "type": "person"},
                   {"key": "b", "label": "员工B", "type": "person"},
                   {"key": "shift", "label": "班次", "type": "shift", "optional": True}],
    },
]

EXTRA_TYPE_MAP = {item["type"]: item for item in EXTRA_RULE_TYPES}


# --------------------------------------------------------------------------
# 规则合并
# --------------------------------------------------------------------------
# 每条规则都有「约束优先级」：1.00 = 标准权重，0.01 几乎忽略，2.00 最高
for _r in RULE_CATALOG:
    _r.setdefault("priority", 1.0)


def _clamp_priority(value) -> float:
    try:
        p = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(0.01, min(2.0, round(p, 2)))



def effective_rules(doc: dict) -> list[dict]:
    overrides = doc.get("ruleOverrides") or {}
    out = []
    for rule in RULE_CATALOG:
        eff = {
            "code": rule["code"], "name": rule["name"], "desc": rule["desc"],
            "quant": rule["quant"], "enabled": rule["enabled"],
            "severity": rule["severity"], "params": dict(rule["params"]),
            "paramsSpec": rule["paramsSpec"],
            "priority": _clamp_priority(rule.get("priority", 1.0)),
            "action": str(rule.get("action") or ""),
        }
        ov = overrides.get(rule["code"])
        if isinstance(ov, dict):
            if "enabled" in ov:
                eff["enabled"] = bool(ov["enabled"])
            if ov.get("severity") in (SEV_ERROR, SEV_WARN):
                eff["severity"] = ov["severity"]
            if isinstance(ov.get("params"), dict):
                eff["params"].update(ov["params"])
            if "priority" in ov:
                eff["priority"] = _clamp_priority(ov["priority"])
            if isinstance(ov.get("action"), str):
                eff["action"] = ov["action"]
        out.append(eff)
    return out


def rule_map(doc: dict) -> dict:
    return {r["code"]: r for r in effective_rules(doc)}


def extra_rules_of(doc: dict) -> list[dict]:
    rules = []
    for idx, raw in enumerate(doc.get("extraRules") or []):
        tmeta = EXTRA_TYPE_MAP.get(raw.get("type"))
        if not tmeta:
            continue
        item = dict(raw)
        item["_idx"] = idx
        item["_meta"] = tmeta
        item["enabled"] = bool(raw.get("enabled", True))
        item["severity"] = raw.get("severity") if raw.get("severity") in (SEV_ERROR, SEV_WARN) else tmeta["severity"]
        rules.append(item)
    return rules


# --------------------------------------------------------------------------
# 检查引擎
# --------------------------------------------------------------------------
class Checker:
    def __init__(self, doc: dict, employees: list[dict]):
        self.doc = doc
        self.employees = employees
        self.days = int(doc.get("days") or 28)
        self.year = int(doc.get("year"))
        self.month = int(doc.get("month"))
        self.rules = rule_map(doc)
        self.extra = extra_rules_of(doc)
        self.by_id = {e["id"]: e for e in employees}
        self.name = {e["id"]: e["name"] for e in employees}
        self.order = {e["id"]: i for i, e in enumerate(employees)}

        self.morning = [set() for _ in range(self.days + 1)]
        self.evening = [set() for _ in range(self.days + 1)]
        self.duty = [set() for _ in range(self.days + 1)]
        self.opener = ["" for _ in range(self.days + 1)]
        self.avail = {}
        self.unavail_extra = set()  # (eid, day) 由特殊规则指定不可用

        for d in range(1, self.days + 1):
            cell = (doc.get("schedule") or {}).get(str(d)) or {}
            self.morning[d] = {x for x in (cell.get("morning") or []) if x in self.by_id}
            self.evening[d] = {x for x in (cell.get("evening") or []) if x in self.by_id}
            self.duty[d] = {x for x in (cell.get("duty") or []) if x in self.by_id}
            op = cell.get("opener") or ""
            self.opener[d] = op if op in self.by_id else ""
        for emp in employees:
            days_list = (doc.get("availability") or {}).get(emp["id"])
            self.avail[emp["id"]] = set(days_list if isinstance(days_list, list) else range(1, self.days + 1))

        self.violations: list[dict] = []
        self._vseq = 0
        self.worked_days = {e["id"]: [] for e in employees}
        self.duty_count = {e["id"]: 0 for e in employees}
        self.rest_count = {e["id"]: 0 for e in employees}

    # -- 工具 -------------------------------------------------------------
    def works(self, eid: str, day: int) -> bool:
        return (eid in self.morning[day]) or (eid in self.evening[day]) or (eid in self.duty[day])

    def shift_kind(self, eid: str, day: int):
        if eid in self.duty[day]:
            return "值班"
        if eid in self.morning[day]:
            return "早"
        if eid in self.evening[day]:
            return "晚"
        return ""

    def available(self, eid: str, day: int) -> bool:
        return day in self.avail.get(eid, set()) and (eid, day) not in self.unavail_extra

    def is_empty(self) -> bool:
        for d in range(1, self.days + 1):
            if self.morning[d] or self.evening[d] or self.duty[d] or self.opener[d]:
                return False
        return True

    def notify(self, code, severity, day, persons, title, message, hint="", cells=None):
        self._vseq += 1
        vid = f"v{self._vseq}"
        rule = self.rules.get(code) or {}
        cells = cells or []
        if not cells:
            cells = [[p, day] for p in persons if p] if day else []
        self.violations.append({
            "id": vid, "code": code, "rule": rule.get("name", code),
            "severity": severity, "day": day, "persons": list(persons),
            "title": title, "message": message, "hint": hint, "cells": cells,
            # 规则级「处理建议」：在规则详情里可编辑，冲突清单优先展示它
            "action": (rule.get("action") or "").strip() or hint,
        })
        return vid

    def prepare_extra_prepass(self):
        """特殊规则中声明的不可用日先并入可用性判断。"""
        for rule in self.extra:
            if not rule["enabled"]:
                continue
            t = rule["type"]
            params = rule.get("params") or {}
            if t == "unavailable":
                eid = params.get("person")
                day = _to_int(params.get("day"))
                if eid in self.by_id and day:
                    self.unavail_extra.add((eid, day))
            elif t == "rest_range":
                eid = params.get("person")
                d1, d2 = _to_int(params.get("from")), _to_int(params.get("to"))
                if eid in self.by_id and d1 and d2:
                    for d in range(max(1, min(d1, d2)), min(self.days, max(d1, d2)) + 1):
                        self.unavail_extra.add((eid, d))

    # -- 主流程 -----------------------------------------------------------
    def run(self) -> dict:
        self.prepare_extra_prepass()
        empty = self.is_empty()
        for d in range(1, self.days + 1):
            for emp in self.employees:
                eid = emp["id"]
                if self.works(eid, d):
                    self.worked_days[eid].append(d)
                else:
                    self.rest_count[eid] += 1
        for d in range(1, self.days + 1):
            for eid in self.duty[d]:
                if eid in self.duty_count:
                    self.duty_count[eid] += 1

        if not empty:
            self.check_shift_size()
            self.check_dual_shift()
            self.check_daily_duty()
            self.check_alternate()
            self.check_duty_quota()
            self.check_duty_link()
            self.check_key_staff()
            self.check_shift_cycle()
            self.check_shift_runs()
            self.check_shift_quota()
            self.check_duty_gap()
            self.check_opener_balance()
            self.check_yi_support()
            self.check_rest_quota()
            self.check_availability()
            self.check_odd_even()
            self.check_opener()
            self.check_consecutive()
            self.check_extra_rules()

        self.violations.sort(key=lambda v: (
            0 if v["severity"] == SEV_ERROR else 1,
            v["day"] or 0,
            v["code"],
        ))
        return self.result(empty)

    # -- 各条规则 ---------------------------------------------------------
    def check_shift_size(self):
        rule = self.rules["R1"]
        if not rule["enabled"]:
            return
        lo = _to_int(rule["params"].get("min"), 2)
        hi = _to_int(rule["params"].get("max"), 3)
        prefer = _to_int(rule["params"].get("prefer"), 2)
        for d in range(1, self.days + 1):
            for label, members in (("早班", self.morning[d]), ("晚班", self.evening[d])):
                n = len(members)
                persons = sorted(members, key=lambda x: self.order.get(x, 99))
                sev = rule["severity"]
                if n < lo:
                    self.notify("R1", sev, d, [], f"{label}人手不足",
                                f"{self.day_label(d)} {label}仅 {n} 人，低于下限 {lo} 人",
                                "从候选池补人，优先选可用且未排班员工。",
                                cells=[[p, d] for p in persons] or [])
                elif n > hi:
                    self.notify("R1", sev, d, [], f"{label}超过人数上限",
                                f"{self.day_label(d)} {label} {n} 人，超过上限 {hi} 人",
                                f"移出 {n - hi} 人（优先移出当日可用余量大的员工）。",
                                cells=[[p, d] for p in persons])
                elif n > prefer and rule["params"].get("warnThree"):
                    self.notify("R1", SEV_WARN, d, [],
                                f"{label}非最优人数",
                                f"{self.day_label(d)} {label} {n} 人，优先 {prefer} 人一班",
                                "人数合法但非最优，可移出 1 人。",
                                cells=[[p, d] for p in persons])

    def check_dual_shift(self):
        rule = self.rules["R2"]
        if not rule["enabled"]:
            return
        for d in range(1, self.days + 1):
            for eid in sorted(self.morning[d] & self.evening[d], key=lambda x: self.order.get(x, 99)):
                if (self.by_id.get(eid) or {}).get("alternate"):
                    continue  # 上一休一员工当天本来就该早晚都上
                self.notify("R2", rule["severity"], d, [eid], "同日双班冲突",
                            f"{self.day_label(d)} {self.name[eid]} 同时被排早班与晚班",
                            "一人一天只能上一个班次，请移出其中一班。",
                            cells=[[eid, d]])

    def check_daily_duty(self):
        rule = self.rules["R3"]
        if not rule["enabled"]:
            return
        lo = _to_int(rule["params"].get("min"), 1)
        hi = _to_int(rule["params"].get("max"), 1)
        for d in range(1, self.days + 1):
            n = len(self.duty[d])
            if n < lo:
                self.notify("R3", rule["severity"], d, [], "当日无人值班",
                            f"{self.day_label(d)} 未安排值班，规则要求每天至少 {lo} 人值班",
                            "指定一名可用的值班岗员工。")
            elif n > hi:
                self.notify("R3", rule["severity"], d, [], "值班人数超限",
                            f"{self.day_label(d)} 值班 {n} 人，上限 {hi} 人",
                            "只保留 1 名值班人。",
                            cells=[[e, d] for e in self.duty[d]])

    def is_full_day(self, eid: str, day: int) -> bool:
        """上一休一员工「上」的那一天：早班和晚班同时都在。"""
        return (eid in self.morning[day]) and (eid in self.evening[day])

    def check_alternate(self):
        rule = self.rules["R4"]
        if not rule["enabled"]:
            return
        strict_rest = bool(rule["params"].get("strictRest"))
        for emp in self.employees:
            if not emp.get("alternate") or not emp.get("active", True):
                continue
            eid = emp["id"]
            if not self.worked_days[eid]:
                continue

            # (1) 上班日必须「早班 + 晚班」都排
            partial: list[int] = []
            for d in range(1, self.days + 1):
                in_m = eid in self.morning[d]
                in_e = eid in self.evening[d]
                if in_m and not in_e:
                    partial.append(d)
                elif in_e and not in_m:
                    partial.append(d)
            for d in partial:
                have = "早班" if eid in self.morning[d] else "晚班"
                self.notify("R4", SEV_ERROR, d, [eid], "上一休一未排满",
                            f"{self.name[eid]} {self.day_label(d)} 只排了{have}，"
                            f"上一休一员工上班当天需早班、晚班一起上",
                            f"把 {self.name[eid]} 同时加进早班和晚班，或当天整体休息。",
                            cells=[[eid, d]])

            # (2) 「上」的日次必须隔天：连续两天都是全天班 → 冲突
            run: list[int] = []
            for d in range(1, self.days + 2):
                if d <= self.days and self.is_full_day(eid, d):
                    run.append(d)
                    continue
                if len(run) >= 2:
                    self.notify("R4", SEV_ERROR, run[0], [eid], "违反上一休一",
                                f"{self.name[eid]} {self.day_label(run[0])}~{self.day_label(run[-1])} "
                                f"连续上全天班 {len(run)} 天，应上一休一",
                                f"在 {len(run)} 天里隔天安排全天休息（建议保留值班日）。",
                                cells=[[eid, x] for x in run])
                run = []

            # (3) 连续休息（可选）
            if strict_rest:
                rest_run: list[int] = []
                for d in range(1, self.days + 2):
                    if d <= self.days and not self.works(eid, d):
                        rest_run.append(d)
                        continue
                    if len(rest_run) >= 2:
                        self.notify("R4", SEV_WARN, rest_run[0], [eid], "连续休息",
                                    f"{self.name[eid]} {self.day_label(rest_run[0])}~{self.day_label(rest_run[-1])} "
                                    f"连续休息 {len(rest_run)} 天",
                                    "如非请假，建议恢复上一休一节奏。",
                                    cells=[[eid, x] for x in rest_run])
                    rest_run = []

    def check_duty_quota(self):
        rule = self.rules["R5"]
        if not rule["enabled"]:
            return
        tol = _to_int(rule["params"].get("tolerance"), 0)
        override = {}
        for extra in self.extra:
            if extra["enabled"] and extra["type"] == "duty_count":
                p = extra.get("params") or {}
                if p.get("person") in self.by_id:
                    override[p["person"]] = _to_int(p.get("count"), 0)
        for emp in self.employees:
            eid = emp["id"]
            if not emp.get("active", True):
                continue
            target = override.get(eid, emp.get("dutyTarget") or 0)
            cap = max(target, emp.get("dutyMax") or 0)
            count = self.duty_count.get(eid, 0)
            if count > cap:
                self.notify("R5", rule["severity"], None, [eid], "超过值班次数上限",
                            f"{self.name[eid]} 本月值班 {count} 天，上限 {cap} 天（超过 {count - cap} 天）",
                            "把超出部分的值班日转给其他人，或调高该员工上限。",
                            cells=[[eid, d] for d in self.duty_days(eid)])
            elif target and count < target - tol:
                self.notify("R5", rule["severity"], None, [eid], "值班次数不足",
                            f"{self.name[eid]} 本月值班 {count} 天，目标 {target} 天（差 {target - count} 天）",
                            "补排值班日，注意值班次日需空班。",
                            cells=[[eid, d] for d in self.duty_days(eid)])

    def check_duty_link(self):
        rule = self.rules["R6"]
        if not rule["enabled"]:
            return
        need_evening = rule["params"].get("requireEvening", True)
        next_rest = rule["params"].get("nextDayRest", True)
        for d in range(1, self.days + 1):
            for eid in self.duty[d]:
                if need_evening and eid not in self.evening[d]:
                    self.notify("R6", rule["severity"], d, [eid], "值班未排晚班",
                                f"{self.name[eid]} {self.day_label(d)} 值班，但未同时排晚班",
                                "值班日必须同时上晚班。",
                                cells=[[eid, d]])
                if next_rest and d < self.days and self.works(eid, d + 1):
                    self.notify("R6", rule["severity"], d, [eid], "值班次日未休息",
                                f"{self.name[eid]} {self.day_label(d)} 值班，{self.day_label(d + 1)} 应空班",
                                "把值班次日改为休息（空班）。",
                                cells=[[eid, d + 1]])

    def check_key_staff(self):
        rule = self.rules["R7"]
        if not rule["enabled"]:
            return
        key_ids = [e["id"] for e in self.employees if e.get("keyShift") and e.get("active", True)]
        if not key_ids:
            return
        names = "/".join(self.name[i] for i in key_ids)
        for d in range(1, self.days + 1):
            for label, members in (("早班", self.morning[d]), ("晚班", self.evening[d])):
                if not (members & set(key_ids)):
                    present = [k for k in key_ids
                               if (k in self.morning[d]) or (k in self.evening[d]) or (k in self.duty[d])]
                    cells = [[k, d] for k in (present or key_ids)]
                    self.notify("R7", rule["severity"], d, [], f"{label}缺关键岗",
                                f"{self.day_label(d)} {label}没有 {names} 中的任何一位",
                                f"在{label}加入关键岗人员。", cells=cells)

    def check_shift_runs(self):
        """R14/R15：连续早班/晚班。R14 支持「提示阈值」和「冲突阈值」两级。"""
        for code, table, label in (("R14", self.morning, "早班"), ("R15", self.evening, "晚班")):
            rule = self.rules[code]
            if not rule["enabled"]:
                continue
            params = rule["params"]
            warn_days = max(2, _to_int(params.get("warnDays") or params.get("maxConsecutive"), 2))
            hard_days = _to_int(params.get("hardDays"), 0)
            for emp in self.employees:
                if not emp.get("active", True):
                    continue
                eid = emp["id"]
                run: list[int] = []
                for d in range(1, self.days + 2):
                    if d <= self.days and eid in table[d]:
                        run.append(d)
                        continue
                    if run:
                        length = len(run)
                        cells = [[eid, x] for x in run]
                        span = f"{self.day_label(run[0])}~{self.day_label(run[-1])}"
                        if hard_days and length >= hard_days:
                            self.notify(code, SEV_ERROR, run[0], [eid], f"连续{label}超限",
                                        f"{self.name[eid]} {span} 连续 {length} 天{label}，"
                                        f"规则不允许连续 {hard_days} 天及以上",
                                        "把其中一天换到另一个班次。", cells=cells)
                        elif length >= warn_days:
                            self.notify(code, rule["severity"], run[0], [eid], f"连续{label}",
                                        f"{self.name[eid]} {span} 连续 {length} 天{label}，建议不超过 {warn_days - 1} 天",
                                        f"把其中一天换到另一个班次，让{label}与其他班次交替。", cells=cells)
                    run = []

    def check_opener_balance(self):
        """R18：开门人次平均分配。"""
        rule = self.rules["R18"]
        if not rule["enabled"]:
            return
        tol = _to_int(rule["params"].get("tolerance"), 1)
        morning_days: dict[str, int] = {}
        opened: dict[str, int] = {}
        for d in range(1, self.days + 1):
            for eid in self.morning[d]:
                morning_days[eid] = morning_days.get(eid, 0) + 1
            op = self.opener[d]
            if op:
                opened[op] = opened.get(op, 0) + 1
        pool = [eid for eid, n in morning_days.items() if n >= 4]
        if len(pool) < 2:
            return
        total = sum(opened.get(eid, 0) for eid in pool)
        avg = total / len(pool)
        for eid in sorted(pool, key=lambda x: self.order.get(x, 99)):
            got = opened.get(eid, 0)
            if got < avg - tol:
                self.notify("R18", rule["severity"], None, [eid], "开门次数偏少",
                            f"{self.name[eid]} 本月只开 {got} 次门，平均应约 {avg:.1f} 次",
                            "把其他日子的开门人换成该员工。",
                            cells=[[eid, d] for d in range(1, self.days + 1) if self.opener[d] == eid])
            elif got > avg + tol:
                self.notify("R18", rule["severity"], None, [eid], "开门次数偏多",
                            f"{self.name[eid]} 本月开 {got} 次门，平均约 {avg:.1f} 次",
                            "把部分日子换给开门次数少的人。",
                            cells=[[eid, d] for d in range(1, self.days + 1) if self.opener[d] == eid])

    def check_duty_gap(self):
        """R17：两次值班之间的间隔不要过密。"""
        rule = self.rules["R17"]
        if not rule["enabled"]:
            return
        gap = max(2, _to_int(rule["params"].get("minGap"), 3))
        for emp in self.employees:
            if not emp.get("active", True) or not (emp.get("dutyMax") or emp.get("dutyTarget")):
                continue
            eid = emp["id"]
            days = [d for d in range(1, self.days + 1) if eid in self.duty[d]]
            for a, b in zip(days, days[1:]):
                if b - a < gap:
                    self.notify("R17", rule["severity"], b, [eid], "值班间隔过密",
                                f"{self.name[eid]} {self.day_label(a)}、{self.day_label(b)} 两次值班"
                                f"只隔 {b - a} 天，建议至少隔 {gap} 天",
                                "把其中一天的值班换给别人，让值班日更均匀。",
                                cells=[[eid, a], [eid, b]])

    def check_shift_quota(self):
        """R16：每人当月早班/晚班次数上下限。"""
        rule = self.rules["R16"]
        if not rule["enabled"]:
            return
        for emp in self.employees:
            if not emp.get("active", True):
                continue
            eid = emp["id"]
            early = sum(1 for d in range(1, self.days + 1) if eid in self.morning[d])
            late = sum(1 for d in range(1, self.days + 1) if eid in self.evening[d])
            for label, value, lo, hi in (
                ("早班", early, emp.get("minEarly"), emp.get("maxEarly")),
                ("晚班", late, emp.get("minLate"), emp.get("maxLate")),
            ):
                if lo and value < lo:
                    self.notify("R16", rule["severity"], None, [eid], f"{label}次数不足",
                                f"{self.name[eid]} 本月{label} {value} 天，低于下限 {lo} 天",
                                "在格子上补排该班次，或调整下限。",
                                cells=[[eid, d] for d in range(1, self.days + 1)
                                       if eid in (self.morning[d] if label == "早班" else self.evening[d])])
                if hi and value > hi:
                    self.notify("R16", rule["severity"], None, [eid], f"{label}次数超限",
                                f"{self.name[eid]} 本月{label} {value} 天，超过上限 {hi} 天",
                                "减少该班次，或调整上限。",
                                cells=[[eid, d] for d in range(1, self.days + 1)
                                       if eid in (self.morning[d] if label == "早班" else self.evening[d])])

    def check_shift_cycle(self):
        rule = self.rules["R8"]
        if not rule["enabled"]:
            return
        ignore_duty = bool(rule["params"].get("ignoreDuty", True))
        for emp in self.employees:
            if not emp.get("shiftCycle") or not emp.get("active", True):
                continue
            eid = emp["id"]
            for d in range(1, self.days):
                if not (self.works(eid, d) and self.works(eid, d + 1)):
                    continue
                if ignore_duty and (eid in self.duty[d] or eid in self.duty[d + 1]):
                    continue
                k1, k2 = self.shift_kind(eid, d), self.shift_kind(eid, d + 1)
                if k1 and k2 and k1 == k2 and k1 != "值班":
                    self.notify("R8", SEV_WARN, d, [eid], "早晚未轮换",
                                f"{self.name[eid]} {self.day_label(d)}~{self.day_label(d + 1)} 连续上{k1}班，"
                                f"应早/晚交替",
                                "把其中一天换成另一个班次。",
                                cells=[[eid, d], [eid, d + 1]])

    def check_yi_support(self):
        rule = self.rules["R9"]
        if not rule["enabled"]:
            return
        trigger_name = str(rule["params"].get("triggerName") or "怡").strip()
        support_name = str(rule["params"].get("supportName") or "吴").strip()
        trigger = next((e["id"] for e in self.employees if e["name"] == trigger_name), None)
        support = next((e["id"] for e in self.employees if e["name"] == support_name), None)
        if not trigger or not support:
            return
        for d in range(1, self.days + 1):
            if trigger in self.evening[d] and trigger in self.duty[d]:
                if support not in self.morning[d]:
                    self.notify("R9", rule["severity"], d, [trigger], "值班补位缺失",
                                f"{self.name[trigger]} {self.day_label(d)} 晚班+值班，早班未安排{self.name[support]}",
                                f"在早班加入{self.name[support]}。",
                                cells=[[trigger, d], [support, d]])

    def check_rest_quota(self):
        """R10：全月休息天数配额（例如琳全月休 6 天左右）。"""
        rule = self.rules["R10"]
        if not rule["enabled"]:
            return
        tol = _to_int(rule["params"].get("tolerance"), 1)
        for emp in self.employees:
            if not emp.get("active", True):
                continue
            target = emp.get("monthlyRest")
            if not target:
                continue
            eid = emp["id"]
            if not self.worked_days[eid]:
                continue
            rest = self.days - len(self.worked_days[eid])
            diff = rest - target
            if abs(diff) <= tol:
                continue
            self.notify("R10", rule["severity"], None, [eid], "休息天数偏差",
                        f"{self.name[eid]} 本月休息 {rest} 天，目标 {target} 天"
                        f"（{'多休' if diff > 0 else '少休'} {abs(diff)} 天）",
                        "在格子上增减休息日，或调整该员工的休息天数目标。",
                        cells=[[eid, d] for d in sorted(self.worked_days[eid])])

    def check_availability(self):
        rule = self.rules["R11"]
        if not rule["enabled"]:
            return
        for emp in self.employees:
            eid = emp["id"]
            for d in self.worked_days[eid]:
                if not self.available(eid, d):
                    kind = "特殊规则不可用日" if (eid, d) in self.unavail_extra else "不可用日"
                    self.notify("R11", rule["severity"], d, [eid], "不可用日被排班",
                                f"{self.name[eid]} {self.day_label(d)} 为{kind}，仍被安排"
                                f"{self.shift_kind(eid, d)}",
                                "移出该班次或调整可用日。",
                                cells=[[eid, d]])

    def check_odd_even(self):
        """R19/R20：单号/双号工作。"""
        for emp in self.employees:
            if not emp.get("active", True):
                continue
            eid = emp["id"]
            odd = bool(emp.get("oddOnly"))
            even = bool(emp.get("evenOnly"))
            if not (odd or even):
                continue
            code = "R19" if odd else "R20"
            rule = self.rules[code]
            if not rule["enabled"]:
                continue
            label = "单号工作" if odd else "双号工作"
            for d in range(1, self.days + 1):
                disallowed = (odd and d % 2 == 0) or (even and d % 2 == 1)
                if not disallowed:
                    continue
                if eid in self.morning[d] or eid in self.evening[d] or eid in self.duty[d]:
                    self.notify(code, rule["severity"], d, [eid], f"违反{label}",
                                f"{self.name[eid]} 标记为「{label}」，但 {self.day_label(d)} "
                                f"安排了{self.shift_kind(eid, d) or '班次'}",
                                f"把这一天改到{'单' if odd else '双'}数日期，或取消「{label}」标记。",
                                cells=[[eid, d]])

    def check_opener(self):
        rule = self.rules["R12"]
        if not rule["enabled"]:
            return
        max_repeat = _to_int(rule["params"].get("maxRepeat"), 0)
        require = rule["params"].get("require", True)
        for d in range(1, self.days + 1):
            op = self.opener[d]
            if not op:
                if require and self.morning[d]:
                    self.notify("R12", SEV_WARN, d, [], "未指定开门人",
                                f"{self.day_label(d)} 早班 {len(self.morning[d])} 人，但未指定开门人",
                                "从早班成员中指定 1 人开门。")
                continue
            if op not in self.morning[d]:
                self.notify("R12", rule["severity"], d, [op], "开门人不在早班",
                            f"{self.day_label(d)} 开门人 {self.name[op]} 不在早班名单内",
                            "开门人必须是当天早班成员。",
                            cells=[[op, d]])
            if d > 1 and max_repeat == 0 and self.opener[d - 1] == op:
                self.notify("R12", SEV_WARN, d, [op], "开门人连续重复",
                            f"{self.name[op]} {self.day_label(d - 1)}、{self.day_label(d)} 连续两天开门",
                            "换一位早班成员开门。",
                            cells=[[op, d], [op, d - 1]])

    def check_consecutive(self):
        rule = self.rules["R13"]
        if not rule["enabled"]:
            return
        limit = _to_int(rule["params"].get("maxConsecutive"), 6)
        for emp in self.employees:
            eid = emp["id"]
            run = 0
            best = []
            for d in range(1, self.days + 2):
                if d <= self.days and self.works(eid, d):
                    run += 1
                    best.append(d)
                else:
                    if run > limit:
                        self.notify("R13", SEV_WARN, best[0], [eid], "连续上班超限",
                                    f"{self.name[eid]} 连续上班 {run} 天（{self.day_label(best[0])}"
                                    f"~{self.day_label(best[-1])}），上限 {limit} 天",
                                    "插入一天空班。",
                                    cells=[[eid, x] for x in best])
                    run = 0
                    best = []

    def check_extra_rules(self):
        for rule in self.extra:
            if not rule["enabled"]:
                continue
            meta = rule["_meta"]
            params = rule.get("params") or {}
            sev = rule["severity"]
            t = rule["type"]
            note = str(rule.get("note") or "").strip()
            suffix = f"（{note}）" if note else ""
            title = meta["name"]

            def emit(day, persons, msg, hint, cells=None):
                self.notify("X" + t.upper(), sev, day, persons, title, msg + suffix, hint, cells)

            if t in ("rest", "unavailable"):
                eid = params.get("person")
                day = _to_int(params.get("day"))
                if eid not in self.by_id or not day:
                    continue
                if self.works(eid, day):
                    emit(day, [eid], f"特殊规则：{self.name[eid]} {self.day_label(day)} 应休息，"
                                     f"实际排了{self.shift_kind(eid, day)}",
                         "移出该日所有班次。", [[eid, day]])
            elif t in ("early", "late", "duty"):
                eid = params.get("person")
                day = _to_int(params.get("day"))
                if eid not in self.by_id or not day:
                    continue
                target = {"early": self.morning, "late": self.evening, "duty": self.duty}[t]
                label = {"early": "早班", "late": "晚班", "duty": "值班"}[t]
                if eid not in target[day]:
                    emit(day, [eid], f"特殊规则：{self.name[eid]} {self.day_label(day)} 必须安排{label}，实际未安排",
                         f"把{self.name[eid]}加入{label}。", [[eid, day]])
            elif t == "no_duty":
                eid = params.get("person")
                day = _to_int(params.get("day"))
                if eid in self.by_id and day and eid in self.duty[day]:
                    emit(day, [eid], f"特殊规则：{self.name[eid]} {self.day_label(day)} 不得值班",
                         "改用其他值班岗员工。", [[eid, day]])
            elif t == "opener":
                eid = params.get("person")
                day = _to_int(params.get("day"))
                if eid in self.by_id and day and self.opener[day] != eid:
                    want = self.name.get(self.opener[day], "空")
                    emit(day, [eid], f"特殊规则：{self.day_label(day)} 开门人应为{self.name[eid]}，当前为{want}",
                         "修改当天开门人。", [[eid, day]])
            elif t == "headcount":
                day = _to_int(params.get("day"))
                shift = params.get("shift") or "early"
                want = _to_int(params.get("count"), 2)
                if not day:
                    continue
                members = self.morning[day] if shift == "early" else self.evening[day]
                label = "早班" if shift == "early" else "晚班"
                if len(members) != want:
                    emit(day, [], f"特殊规则：{self.day_label(day)} {label}人数应为 {want} 人，实际 {len(members)} 人",
                         "按特殊规则调整人数。", [[e, day] for e in members])
            elif t == "duty_count":
                pass  # 已在 R5 中作为覆盖值参与判定
            elif t == "rest_range":
                eid = params.get("person")
                d1, d2 = _to_int(params.get("from")), _to_int(params.get("to"))
                if eid not in self.by_id or not d1 or not d2:
                    continue
                for d in range(max(1, min(d1, d2)), min(self.days, max(d1, d2)) + 1):
                    if self.works(eid, d):
                        emit(d, [eid], f"特殊规则：{self.name[eid]} {self.day_label(d)} 处于指定休息区间，"
                                       f"实际排了{self.shift_kind(eid, d)}",
                             "移出该日班次。", [[eid, d]])
            elif t in ("pair_separate", "pair_together"):
                a, b = params.get("a"), params.get("b")
                if a not in self.by_id or b not in self.by_id:
                    continue
                shifts = [("早班", self.morning), ("晚班", self.evening)]
                if params.get("shift") in ("early", "late"):
                    shifts = [("早班", self.morning)] if params["shift"] == "early" else [("晚班", self.evening)]
                for d in range(1, self.days + 1):
                    for label, table in shifts:
                        both = (a in table[d]) and (b in table[d])
                        if t == "pair_separate" and both:
                            emit(d, [a, b], f"特殊规则：{self.name[a]} 与 {self.name[b]} 不得同班，"
                                            f"但 {self.day_label(d)} 同在{label}",
                                 "拆开两人。", [[a, d], [b, d]])
                    if t == "pair_together":
                        together = any((a in table[d]) and (b in table[d]) for _, table in shifts)
                        if self.works(a, d) and self.works(b, d) and not together:
                            emit(d, [a, b], f"特殊规则：{self.name[a]} 与 {self.name[b]} 必须同班，"
                                            f"但 {self.day_label(d)} 不在同一班次",
                                 "把两人排到同一班次。", [[a, d], [b, d]])

    # -- 辅助 -------------------------------------------------------------
    def duty_days(self, eid: str) -> list[int]:
        return [d for d in range(1, self.days + 1) if eid in self.duty[d]]

    def _duty_of(self, day: int):
        return next(iter(self.duty[day]), None)

    def iso_week_groups(self):
        groups = {}
        for d in range(1, self.days + 1):
            date = _dt.date(self.year, self.month, d)
            iso = date.isocalendar()
            key = (iso[0], iso[1])
            groups.setdefault(key, []).append(d)
        out = []
        for (yy, ww), days in sorted(groups.items()):
            out.append((f"{yy}年第{ww}周", days))
        return out

    def day_label(self, day: int) -> str:
        return f"{self.month}月{day}日"

    def weekday_name(self, day: int) -> str:
        return "一二三四五六日"[_dt.date(self.year, self.month, day).weekday()]

    def shift_days(self, eid: str, kind: str) -> list[int]:
        """某员工上某种班（early / late）的所有日期，按日期升序"""
        bag = self.morning if kind == "early" else self.evening
        return [d for d in range(1, self.days + 1) if eid in bag[d]]

    def _over_quota(self, eid: str, day: int, kind: str, cap: int) -> bool:
        """按日期顺序排，第 cap+1 次及以后算「超次」（只标超出的部分）"""
        if not cap:
            return False
        stamped = self.duty_days(eid) if kind == "duty" else self.shift_days(eid, kind)
        if day not in stamped:
            return False
        return (stamped.index(day) + 1) > cap

    def cell_state(self, eid: str, day: int) -> tuple:
        """返回 (显示状态, 真实班次)；显示状态为 over 表示该项次数超出上限"""
        emp = self.by_id[eid]
        avail = self.available(eid, day)
        worked = self.works(eid, day)
        if not avail and worked:
            return ("conflict", "conflict")
        if not avail:
            return ("unavailable", "unavailable")
        both = (eid in self.morning[day]) and (eid in self.evening[day])
        if eid in self.duty[day]:
            if self._over_quota(eid, day, "duty", _to_int(emp.get("dutyMax"))):
                return ("over", "duty")
            return ("duty", "duty")
        if both:
            return ("both", "both")
        if eid in self.morning[day]:
            if self._over_quota(eid, day, "early", _to_int(emp.get("maxEarly"))):
                return ("over", "early")
            return ("early", "early")
        if eid in self.evening[day]:
            if self._over_quota(eid, day, "late", _to_int(emp.get("maxLate"))):
                return ("over", "late")
            return ("late", "late")
        return ("off", "off")

    def cell_shift(self, eid: str, day: int) -> str:
        """格子的真实班次（忽略「超次」标记），供前端同时显示班次与超次提示"""
        return self.cell_state(eid, day)[1]

    def cell_status(self, eid: str, day: int) -> str:
        return self.cell_state(eid, day)[0]

    # -- 结果 -------------------------------------------------------------
    def result(self, empty: bool) -> dict:
        grid = {}
        cells_viol = {}
        for v in self.violations:
            for eid, day in v["cells"]:
                cells_viol.setdefault(eid, {}).setdefault(str(day), []).append(v["id"])
        for emp in self.employees:
            eid = emp["id"]
            row = {}
            for d in range(1, self.days + 1):
                st, base = self.cell_state(eid, d)
                row[str(d)] = {
                    "s": st,
                    "shift": base,
                    "over": st == "over",
                    "v": cells_viol.get(eid, {}).get(str(d), []),
                    "duty": eid in self.duty[d],
                    "opener": self.opener[d] == eid,
                }
            grid[eid] = row

        stats = []
        for emp in self.employees:
            eid = emp["id"]
            early = sum(1 for d in range(1, self.days + 1) if eid in self.morning[d])
            late = sum(1 for d in range(1, self.days + 1) if eid in self.evening[d])
            duty = self.duty_count.get(eid, 0)
            work = len(self.worked_days[eid])
            target = emp.get("dutyTarget") or 0
            cap = emp.get("dutyMax") or 0
            stats.append({
                "id": eid, "name": emp["name"], "color": emp["color"],
                "role": emp.get("role") or "",
                "early": early, "late": late, "duty": duty,
                "rest": self.days - work, "work": work,
                "avail": len(self.avail.get(eid, set())),
                "target": target, "cap": cap,
                "over": bool(cap and duty > cap),
                "short": bool(target and duty < target),
                "key": bool(emp.get("keyShift")),
            })

        day_rows = []
        for d in range(1, self.days + 1):
            day_rows.append({
                "day": d,
                "weekday": self.weekday_name(d),
                "weekend": _dt.date(self.year, self.month, d).weekday() >= 5,
                "morning": [self.name[x] for x in sorted(self.morning[d], key=lambda x: self.order.get(x, 99))],
                "evening": [self.name[x] for x in sorted(self.evening[d], key=lambda x: self.order.get(x, 99))],
                "duty": [self.name[x] for x in sorted(self.duty[d], key=lambda x: self.order.get(x, 99))],
                "opener": self.name.get(self.opener[d], ""),
                "mCount": len(self.morning[d]),
                "eCount": len(self.evening[d]),
                "conflicts": sum(1 for v in self.violations if v["day"] == d),
            })

        errors = [v for v in self.violations if v["severity"] == SEV_ERROR]
        warns = [v for v in self.violations if v["severity"] != SEV_ERROR]
        days_with_duty = sum(1 for d in range(1, self.days + 1) if self.duty[d])
        days_with_opener = sum(1 for d in range(1, self.days + 1) if self.opener[d])
        return {
            "empty": empty,
            "ok": not errors,
            "violations": self.violations,
            "errors": len(errors),
            "warns": len(warns),
            "total": len(self.violations),
            "grid": grid,
            "stats": stats,
            "days": day_rows,
            "rules": effective_rules(self.doc),
            "extraRules": [
                {k: v for k, v in r.items() if not k.startswith("_")} for r in self.extra
            ],
            "coverage": {
                "days": self.days,
                "duty": days_with_duty,
                "opener": days_with_opener,
                "morning": sum(1 for d in range(1, self.days + 1) if self.morning[d]),
                "evening": sum(1 for d in range(1, self.days + 1) if self.evening[d]),
            },
            "month": {
                "year": self.year, "month": self.month, "days": self.days,
                "label": f"{self.year}年{self.month}月",
            },
        }


def check(doc: dict, employees: list[dict]) -> dict:
    return Checker(doc, employees).run()


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
