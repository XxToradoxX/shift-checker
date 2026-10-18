"""月排班生成器。

思路：
1. 先解「值班」：每日 1 人、每人当月配额、值班次日必须空班、上一休一员工只能在自己的
   出勤日值班。用带回溯的搜索 + 配额前向剪枝求解。
2. 再铺班次：值班人进晚班，保证早晚各 2 人、各含关键岗，满足怡值班补吴、徐早晚轮换、
   琳按周休 4 天。
3. 最后用模拟退火以「与检查器同逻辑的代价函数」做局部搜索，把残余冲突压到最少。
   若硬约束无法全部满足，输出冲突最少的一版并给出说明，供人工确认。
"""
from __future__ import annotations

import datetime as _dt
import math
import random
import sys
import time

from . import rules as R

HARD = 1000
SOFT = 8


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Ctx:
    """生成期使用的扁平化上下文。"""

    def __init__(self, doc, employees):
        self.doc = doc
        self.employees = employees
        self.days = int(doc["days"])
        self.year = int(doc["year"])
        self.month = int(doc["month"])
        self.n = len(employees)
        self.rules = {r["code"]: r for r in R.effective_rules(doc)}
        # 规则在前端停用后，生成器里也必须真的不生效
        self.enabled = {code: bool(rule.get("enabled", True)) for code, rule in self.rules.items()}

        def on(code: str) -> bool:
            return self.enabled.get(code, True)

        self.on = on
        self.ids = [e["id"] for e in employees]
        self.index = {eid: i for i, eid in enumerate(self.ids)}
        self.names = [e["name"] for e in employees]

        self.avail = [[False] * (self.days + 2) for _ in range(self.n)]
        for i, eid in enumerate(self.ids):
            days_list = (doc.get("availability") or {}).get(eid) or list(range(1, self.days + 1))
            for d in days_list:
                if 1 <= _int(d) <= self.days:
                    self.avail[i][_int(d)] = True

        self.target = [_int(e.get("dutyTarget")) for e in employees]
        self.cap = [_int(e.get("dutyMax")) for e in employees]
        self.key = [bool(e.get("keyShift")) and on("R7") for e in employees]
        self.alternate = [bool(e.get("alternate")) and on("R4") for e in employees]
        self.cycle = [bool(e.get("shiftCycle")) and on("R8") for e in employees]
        self.monthly_rest = [e.get("monthlyRest") for e in employees]
        self.prefer_shift = [e.get("preferShift") for e in employees]
        # 目标上班天数：有月度休息目标 → days-rest；值班岗 → 除值班次日休息外都上班
        if not on("R5"):
            self.target = [0] * self.n
            self.cap = [self.days] * self.n

        self.target_work = []
        for e in employees:
            if e.get("alternate"):
                self.target_work.append(None)
            elif e.get("monthlyRest") and on("R10"):
                self.target_work.append(max(0, self.days - int(e.get("monthlyRest") or 0)))
            elif int(e.get("dutyMax") or 0) > 0 and on("R5"):
                self.target_work.append(max(0, self.days - int(e.get("dutyMax") or 0)))
            else:
                self.target_work.append(None)

        p1 = self.rules["R1"]["params"]
        if on("R1"):
            self.lo = max(1, _int(p1.get("min"), 2))
            self.hi = max(self.lo, _int(p1.get("max"), 3))
            self.prefer = max(self.lo, min(self.hi, _int(p1.get("prefer"), 2)))
        else:  # 停用后不再限制每个班次的人数
            self.lo, self.hi, self.prefer = 1, max(1, self.n), 1

        p3 = self.rules["R3"]["params"]
        if on("R3"):
            self.duty_min = _int(p3.get("min"), 1)
            self.duty_max_per_day = _int(p3.get("max"), 1)
        else:
            self.duty_min, self.duty_max_per_day = 0, max(1, self.n)

        p6 = self.rules["R6"]["params"]
        self.need_evening = bool(p6.get("requireEvening", True)) and on("R6")
        self.next_rest = bool(p6.get("nextDayRest", True)) and on("R6")

        p10 = self.rules["R10"]["params"]
        self.rest_tol = _int(p10.get("tolerance"), 1)

        p12 = self.rules["R12"]["params"]
        self.opener_require = bool(p12.get("require", True)) and on("R12")
        self.opener_max_repeat = _int(p12.get("maxRepeat"), 0) if on("R12") else 999
        self.opener_bound = on("R12")

        r14 = self.rules.get("R14") or {}
        p14 = r14.get("params") or {}
        self.early_warn = max(2, _int(p14.get("warnDays"), 2)) if r14.get("enabled") else None
        self.early_hard_at = _int(p14.get("hardDays"), 0) if r14.get("enabled") else 0
        self.prio = {code: float(rule.get("priority") or 1.0) for code, rule in self.rules.items()}
        r17 = self.rules.get("R17") or {}
        self.duty_gap = max(2, _int((r17.get("params") or {}).get("minGap"), 3)) if r17.get("enabled") else None
        r15 = self.rules.get("R15") or {}
        p15 = r15.get("params") or {}
        self.late_warn = max(2, _int(p15.get("maxConsecutive"), 2)) if r15.get("enabled") else None
        self.late_hard_at = 0
        # R16：每人当月早/晚班次数上下限
        self.min_early = [e.get("minEarly") for e in employees]
        self.max_early = [e.get("maxEarly") for e in employees]
        self.min_late = [e.get("minLate") for e in employees]
        self.max_late = [e.get("maxLate") for e in employees]

        if on("R9"):
            self.yi = self._by_name(self.rules["R9"]["params"].get("triggerName", "怡"))
            self.wu = self._by_name(self.rules["R9"]["params"].get("supportName", "吴"))
        else:
            self.yi = self.wu = None

        self.forced_duty = {}
        self.forced_rest = [set() for _ in range(self.days + 2)]
        self.forced_morning = [set() for _ in range(self.days + 2)]
        self.forced_evening = [set() for _ in range(self.days + 2)]
        self.forced_unavail: list[tuple[int, int]] = []
        self._unavail_cache: set[tuple[int, int]] = set()
        self.no_duty = [set() for _ in range(self.n)]
        self.forced_opener = {}
        self.headcount = {}
        self.duty_count_override = {}
        self.pair_separate = []
        self.pair_together = []
        self._parse_extra()

        self.parity: list = [None] * self.n
        self.weeks = self._iso_weeks()
        self.week_of = {}
        for idx, days in enumerate(self.weeks):
            for d in days:
                self.week_of[d] = idx

    # -- 初始化辅助 -------------------------------------------------------
    def _by_name(self, name):
        for i, e in enumerate(self.employees):
            if e["name"] == name:
                return i
        return None

    def _idx(self, eid):
        return self.index.get(eid)

    def _parse_extra(self):
        for rule in R.extra_rules_of(self.doc):
            if not rule["enabled"]:
                continue
            t = rule["type"]
            p = rule.get("params") or {}
            i = self._idx(p.get("person"))
            day = _int(p.get("day"))
            if t == "duty" and i is not None and day:
                self.forced_duty[day] = i
            elif t in ("rest", "unavailable") and i is not None and day:
                self.forced_rest[day].add(i)
                if t == "unavailable":
                    self.forced_unavail.append((i, day))
            elif t == "early" and i is not None and day:
                self.forced_morning[day].add(i)
            elif t == "late" and i is not None and day:
                self.forced_evening[day].add(i)
            elif t == "no_duty" and i is not None and day:
                self.no_duty[i].add(day)
            elif t == "opener" and i is not None and day:
                self.forced_opener[day] = i
            elif t == "headcount" and day:
                shift = "late" if p.get("shift") == "late" else "early"
                self.headcount[(day, shift)] = _int(p.get("count"), 2)
            elif t == "duty_count" and i is not None:
                self.duty_count_override[i] = _int(p.get("count"), 0)
            elif t == "rest_range" and i is not None:
                d1, d2 = _int(p.get("from")), _int(p.get("to"))
                if d1 and d2:
                    for d in range(max(1, min(d1, d2)), min(self.days, max(d1, d2)) + 1):
                        self.forced_rest[d].add(i)
            elif t in ("pair_separate", "pair_together"):
                a, b = self._idx(p.get("a")), self._idx(p.get("b"))
                if a is None or b is None or a == b:
                    continue
                shift = p.get("shift")
                rec = (a, b, shift if shift in ("early", "late") else None)
                (self.pair_separate if t == "pair_separate" else self.pair_together).append(rec)

        self._unavail_cache = set(self.forced_unavail)
        for i, count in self.duty_count_override.items():
            self.target[i] = count
            self.cap[i] = max(self.cap[i], count)

    def _iso_weeks(self):
        groups = {}
        for d in range(1, self.days + 1):
            iso = _dt.date(self.year, self.month, d).isocalendar()
            groups.setdefault(iso[1], []).append(d)
        return [days for _, days in sorted(groups.items())]

    # -- 可用性 -----------------------------------------------------------
    def work_ok(self, i, d):
        if not self.avail[i][d]:
            return False
        if (i, d) in self._unavail_cache:
            return False
        if i in self.forced_rest[d]:
            return False
        return True

    def parity_ok(self, i, d):
        p = self.parity[i]
        if p is None:
            return True
        return (d + p) % 2 == 0

    def work_target(self, i):
        """该员工当月应上班天数；None 表示不限制。"""
        tw = self.target_work[i]
        if tw is not None:
            return tw
        if not self.alternate[i]:
            return None
        return sum(1 for d in range(1, self.days + 1)
                   if self.parity_ok(i, d) and self.work_ok(i, d) and i not in self.forced_rest[d])


# --------------------------------------------------------------------------
# 值班分配
# --------------------------------------------------------------------------
def assign_duty(ctx: Ctx, rng: random.Random, node_budget: int = 80000):
    days, n = ctx.days, ctx.n
    duty = [-1] * (days + 2)
    rest = [-1] * (days + 2)
    count = [0] * n

    caps = list(ctx.cap)
    duty_emps = [i for i in range(n) if caps[i] > 0]
    if not duty_emps:
        return None
    deficit = days - sum(caps[i] for i in duty_emps)
    if deficit > 0:
        pool = sorted(duty_emps, key=lambda i: (0 if ctx.cycle[i] else 1, -ctx.target[i], rng.random()))
        for k in range(deficit):
            caps[pool[k % len(pool)]] += 1

    nodes = [0]
    deadline = time.perf_counter() + 1.0

    def ok(d, i):
        if not ctx.work_ok(i, d):
            return False
        if i == rest[d]:
            return False
        if count[i] >= caps[i]:
            return False
        if d in ctx.no_duty[i]:
            return False
        if ctx.alternate[i] and not ctx.parity_ok(i, d):
            return False
        if d < days and rest[d + 1] not in (-1, i):
            return False
        if i == ctx.yi and ctx.wu is not None:
            # 怡值班 → 吴必须在早班，因此吴当天必须能上班
            if rest[d] == ctx.wu or not ctx.work_ok(ctx.wu, d):
                return False
        if ctx.on("R7") and not ctx.key[i]:
            # 非关键岗值班时，早晚两个班次各需 1 名关键岗，当天至少要 2 名关键岗可上班
            key_ok = sum(1 for k in range(n)
                         if ctx.key[k] and ctx.work_ok(k, d) and k != rest[d])
            if key_ok < 2:
                return False
        return True

    def cands(d):
        forced = ctx.forced_duty.get(d)
        if forced is not None:
            return [forced] if ok(d, forced) else []
        out = [i for i in duty_emps if ok(d, i)]
        rng.shuffle(out)

        def last_gap(x):
            for dd in range(d - 1, 0, -1):
                if duty[dd] == x:
                    return d - dd
            return 99

        def key(x):
            gap = last_gap(x)
            near = 0.6 if ctx.duty_gap and gap < ctx.duty_gap else 0.0
            return count[x] / max(1, caps[x]) + near + rng.random() * 0.08

        out.sort(key=key)
        return out

    def forward_ok(d):
        remain = days - d + 1
        capacity = sum(caps[i] - count[i] for i in duty_emps)
        return capacity >= remain

    def dfs(d):
        if d > days:
            return True
        nodes[0] += 1
        if nodes[0] > node_budget or time.perf_counter() > deadline:
            return False
        if not forward_ok(d):
            return False
        for i in cands(d):
            duty[d] = i
            count[i] += 1
            prev_rest = rest[d + 1]
            if d < days:
                rest[d + 1] = i
            if dfs(d + 1):
                return True
            rest[d + 1] = prev_rest
            duty[d] = -1
            count[i] -= 1
            if nodes[0] > node_budget:
                break
        return False

    if dfs(1):
        return duty, rest, count

    duty = [-1] * (days + 2)
    rest = [-1] * (days + 2)
    count = [0] * n
    caps2 = [max(ctx.cap[i], ctx.target[i]) + 3 for i in range(n)]
    for d in range(1, days + 1):
        forced = ctx.forced_duty.get(d)
        if forced is not None and ctx.work_ok(forced, d) and forced != rest[d]:
            pool = [forced]
        else:
            pool = [
                i for i in duty_emps
                if ctx.work_ok(i, d) and i != rest[d] and count[i] < caps2[i]
                and not (ctx.alternate[i] and not ctx.parity_ok(i, d))
                and d not in ctx.no_duty[i]
                and not (i == ctx.yi and ctx.wu is not None
                         and (rest[d] == ctx.wu or not ctx.work_ok(ctx.wu, d)))
                and (not ctx.on("R7") or ctx.key[i] or sum(
                    1 for k in range(n)
                    if ctx.key[k] and ctx.work_ok(k, d) and k != rest[d]) >= 2)
            ]
        if not pool:
            pool = [i for i in duty_emps if ctx.work_ok(i, d) and i != rest[d]]
        if not pool:
            continue
        pool.sort(key=lambda i: (count[i], rng.random()))
        i = pool[0]
        duty[d] = i
        count[i] += 1
        if d < days:
            rest[d + 1] = i
    return duty, rest, count


# --------------------------------------------------------------------------
# 铺班次
# --------------------------------------------------------------------------
def build_shifts(ctx: Ctx, duty, rest, rng=None):
    rng = rng or random.Random(0)
    days, n = ctx.days, ctx.n
    morning = [set() for _ in range(days + 2)]
    evening = [set() for _ in range(days + 2)]
    work = [0] * n
    last_kind = [""] * n

    def rest_ok(i):
        tw = ctx.work_target(i)
        return True if tw is None else work[i] < tw

    for d in range(1, days + 1):
        wi = ctx.week_of[d]
        pool = []
        for i in range(n):
            if not ctx.work_ok(i, d):
                continue
            if i == rest[d]:
                continue
            if ctx.alternate[i] and not ctx.parity_ok(i, d):
                continue
            pool.append(i)

        # 上一休一员工：轮到上班的那天直接早晚都上
        for i in list(pool):
            if not ctx.alternate[i]:
                continue
            for table in (morning, evening):
                if len(table[d]) >= ctx.hi:
                    for x in sorted(table[d]):
                        if not ctx.key[x] and not ctx.alternate[x]:
                            table[d].discard(x)
                            work[x] = max(0, work[x] - 1)
                            break
            morning[d].add(i)
            evening[d].add(i)
            work[i] += 1
            last_kind[i] = "m"
            pool.remove(i)

        d_i = duty[d]
        if d_i >= 0:
            pool = [i for i in pool if i != d_i]
            if len(evening[d]) >= ctx.hi and d_i not in evening[d]:
                for x in sorted(evening[d]):
                    if not ctx.key[x] and not ctx.alternate[x]:
                        evening[d].discard(x)
                        work[x] = max(0, work[x] - 1)
                        break
            if d_i not in evening[d]:
                work[d_i] += 1
            if ctx.need_evening:
                evening[d].add(d_i)

        for i in ctx.forced_morning[d]:
            if i != d_i and ctx.work_ok(i, d) and i not in evening[d]:
                morning[d].add(i)
                work[i] += 1
                pool = [x for x in pool if x != i]
        for i in ctx.forced_evening[d]:
            if i != d_i and ctx.work_ok(i, d) and i not in morning[d]:
                evening[d].add(i)
                work[i] += 1
                pool = [x for x in pool if x != i]

        def pick(kind, pred=None, allow_over=False):
            best, best_score = None, None
            for i in pool:
                if i in morning[d] or i in evening[d]:
                    continue
                if pred is not None and not pred(i):
                    continue
                score = work[i] * 10.0
                # 避免连续早班：昨天上过早班的人今天优先排晚班
                if kind == "m" and i in morning[d - 1]:
                    score += 60
                if kind == "e" and i in evening[d - 1]:
                    score += 10 if ctx.cycle[i] else 4
                if ctx.cycle[i] and last_kind[i] == kind:
                    score += 12
                if not rest_ok(i):
                    score += 500.0
                # 偏好班次：喜欢的班次适当加权
                if ctx.prefer_shift[i] == "early" and kind == "m":
                    score -= 20
                if ctx.prefer_shift[i] == "late" and kind == "e":
                    score -= 12
                if best_score is None or score < best_score:
                    best, best_score = i, score
            return best

        def add(i, kind, allow_over=True):
            if i is None:
                return
            table = evening if kind == "e" else morning
            if len(table[d]) >= ctx.hi:
                return  # 已经满员，交给后面的修补逻辑
            if kind == "e":
                evening[d].add(i)
            else:
                morning[d].add(i)
            work[i] += 1
            if i in pool:
                pool.remove(i)
            last_kind[i] = kind

        # 1) 怡值班 -> 吴早班（先占位，保证规则 9；早班满了就腾一个非关键位）
        if ctx.yi is not None and d_i == ctx.yi and ctx.wu is not None:
            if (ctx.wu not in morning[d] and ctx.wu not in evening[d]
                    and ctx.work_ok(ctx.wu, d) and ctx.wu != rest[d]):
                if len(morning[d]) >= ctx.hi:
                    for x in sorted(morning[d]):
                        if not ctx.key[x] and not ctx.alternate[x]:
                            morning[d].discard(x)
                            work[x] = max(0, work[x] - 1)
                            break
                morning[d].add(ctx.wu)
                work[ctx.wu] += 1
                last_kind[ctx.wu] = "m"
        # 2) 关键岗覆盖（每日随机决定先排早班还是晚班，避免长期固定）
        first = "e" if rng.random() < 0.5 else "m"
        for kind in (first, "m" if first == "e" else "e"):
            table = evening if kind == "e" else morning
            if not any(ctx.key[i] for i in table[d]):
                add(pick(kind, lambda i: ctx.key[i], allow_over=True), kind)
        # 4) 特殊规则指定的人数
        target_m = max(ctx.prefer, ctx.lo)
        target_e = max(ctx.prefer, ctx.lo)
        if (d, "early") in ctx.headcount:
            target_m = ctx.headcount[(d, "early")]
        if (d, "late") in ctx.headcount:
            target_e = ctx.headcount[(d, "late")]
        guard = 0
        while guard < 10:
            guard += 1
            need_e = len(evening[d]) < min(target_e, ctx.hi)
            need_m = len(morning[d]) < min(target_m, ctx.hi)
            if not need_e and not need_m:
                break
            if need_e:
                cand = pick("e")
                if cand is None:
                    break
                add(cand, "e")
            if need_m:
                cand = pick("m")
                if cand is None:
                    break
                add(cand, "m")
        # 5) 收缩到上限
        for table in (morning, evening):
            while len(table[d]) > ctx.hi:
                removable = [i for i in table[d]
                             if i != d_i and i not in ctx.forced_morning[d] and i not in ctx.forced_evening[d]
                             and not ctx.alternate[i]
                             and not (ctx.yi is not None and d_i == ctx.yi and i == ctx.wu)]
                if not removable:
                    break
                removable.sort(key=lambda i: (0 if ctx.key[i] else 1, work[i]), reverse=True)
                victim = removable[0]
                table[d].discard(victim)
                work[victim] -= 1
                pool.append(victim)
    # 6) 按「目标上班天数」补齐，差额优先塞进人少的班次（必要时 3 人一班）
    for _ in range(3):
        progressed = False
        deficit = [i for i in range(n)
                   if not ctx.alternate[i]
                   and ctx.work_target(i) is not None and work[i] < ctx.work_target(i)]
        for i in sorted(deficit, key=lambda x: -(ctx.work_target(x) - work[x])):
            for d in range(1, days + 1):
                if work[i] >= ctx.work_target(i):
                    break
                if not ctx.work_ok(i, d) or i == rest[d] or duty[d] == i:
                    continue
                if i in morning[d] or i in evening[d]:
                    continue
                if ctx.alternate[i] and not ctx.parity_ok(i, d):
                    continue
                opts = []
                if len(morning[d]) < ctx.hi and (d, "early") not in ctx.headcount:
                    opts.append("m")
                if len(evening[d]) < ctx.hi and (d, "late") not in ctx.headcount:
                    opts.append("e")
                if not opts:
                    continue
                if ctx.prefer_shift[i] == "early" and "m" in opts:
                    kind = "m"
                elif ctx.prefer_shift[i] == "late" and "e" in opts:
                    kind = "e"
                else:
                    kind = min(opts, key=lambda k: len(morning[d]) if k == "m" else len(evening[d]))
                (morning if kind == "m" else evening)[d].add(i)
                work[i] += 1
                last_kind[i] = kind
                progressed = True
        if not progressed:
            break

    return {"morning": morning, "evening": evening,
            "duty": list(duty), "opener": [-1] * (days + 2)}


def evaluate(ctx: Ctx, st):
    days, n = ctx.days, ctx.n
    m, e, duty = st["morning"], st["evening"], st["duty"]
    hard = 0
    soft = 0
    dcount = [0] * n
    wcount = [0] * n
    ecount = [0] * n
    lcount = [0] * n
    for d in range(1, days + 1):
        nm, ne = len(m[d]), len(e[d])
        if nm < ctx.lo or nm > ctx.hi:
            hard += 1
        elif nm > ctx.prefer:
            soft += 1
        if ne < ctx.lo or ne > ctx.hi:
            hard += 1
        elif ne > ctx.prefer:
            soft += 1
        if nm and ne and ctx.on("R2"):
            for x in (m[d] & e[d]):
                if not ctx.alternate[x]:
                    hard += 1
        di = duty[d]
        if di < 0:
            if ctx.on("R3"):
                hard += 1
        else:
            dcount[di] += 1
            if ctx.need_evening and di not in e[d]:
                hard += 1
        if ctx.key and any(ctx.key):
            if not any(ctx.key[i] for i in m[d]):
                hard += 1
            if not any(ctx.key[i] for i in e[d]):
                hard += 1
        if ctx.yi is not None and di == ctx.yi and ctx.wu is not None and ctx.wu not in m[d]:
            hard += 1
        target = ctx.headcount.get((d, "early"))
        if target is not None and nm != target:
            hard += 1
        target = ctx.headcount.get((d, "late"))
        if target is not None and ne != target:
            hard += 1

    for i in range(n):
        run = 0
        erun = 0
        lrun = 0
        for d in range(1, days + 1):
            worked = (i in m[d]) or (i in e[d]) or duty[d] == i
            if worked:
                wcount[i] += 1
            if i in m[d]:
                ecount[i] += 1
            if i in e[d]:
                lcount[i] += 1
            if worked and not ctx.work_ok(i, d):
                hard += 1
            erun = erun + 1 if i in m[d] else 0
            lrun = lrun + 1 if i in e[d] else 0
            if ctx.early_warn and erun >= ctx.early_warn:
                # 连续 3 天及以上用高权重软约束，避免为了它牺牲结构性规则（人数/关键岗）
                w = ctx.prio.get("R14", 1.0)
                if ctx.early_hard_at and erun >= ctx.early_hard_at:
                    soft += 200 * w
                else:
                    soft += 8 * w
            if ctx.late_warn and lrun >= ctx.late_warn:
                soft += 4 * ctx.prio.get("R15", 1.0)
            if ctx.alternate[i] and worked and not (i in m[d] and i in e[d]):
                hard += 1  # 上一休一：上班当天必须早班+晚班都在
            if ctx.alternate[i] and worked and d < days:
                if (i in m[d + 1]) or (i in e[d + 1]) or duty[d + 1] == i:
                    hard += 1
            if ctx.alternate[i] and not worked and ctx.work_ok(i, d) and ctx.parity_ok(i, d):
                soft += 2
            if ctx.cycle[i] and worked and d < days:
                nxt = (i in m[d + 1]) or (i in e[d + 1]) or duty[d + 1] == i
                if nxt and duty[d] != i and duty[d + 1] != i:
                    cur_kind = "m" if i in m[d] else ("e" if i in e[d] else "")
                    nxt_kind = "m" if i in m[d + 1] else ("e" if i in e[d + 1] else "")
                    if cur_kind and nxt_kind and cur_kind == nxt_kind:
                        soft += 1
            run = run + 1 if worked else 0
            if run > 6 and ctx.on("R13"):
                soft += 1
        target = ctx.target[i]
        cap = max(target, ctx.cap[i])
        if ctx.on("R5"):
            if dcount[i] > cap:
                hard += dcount[i] - cap
            elif target and dcount[i] < target:
                hard += target - dcount[i]
        # R17：值班间隔过密
        if ctx.duty_gap:
            prev = -99
            for d in range(1, days + 1):
                if duty[d] < 0 or duty[d] != i:
                    continue
                if d - prev < ctx.duty_gap:
                    soft += 14 * ctx.prio.get("R17", 1.0)
                prev = d

        # 上班天数目标（= 当月天数 − 休息目标；上一休一按可出勤的日数）
        tw = ctx.work_target(i)
        if tw is not None:
            soft += abs(wcount[i] - int(tw)) * 6 * ctx.prio.get("R10", 1.0)
        # R16：早/晚班次数上下限
        w16 = ctx.prio.get("R16", 1.0) if ctx.on("R16") else 0.0
        lo, hi = ctx.min_early[i], ctx.max_early[i]
        if lo and ecount[i] < lo:
            soft += (lo - ecount[i]) * 40 * w16
        if hi and ecount[i] > hi:
            soft += (ecount[i] - hi) * 40 * w16
        lo, hi = ctx.min_late[i], ctx.max_late[i]
        if lo and lcount[i] < lo:
            soft += (lo - lcount[i]) * 40 * w16
        if hi and lcount[i] > hi:
            soft += (lcount[i] - hi) * 40 * w16

        # 早晚比例目标：默认各半；偏好早班的人目标 60% 早班
        # 值班本身算晚班，统计时剔除，避免值班多的人被误判为「太偏晚班」
        late_plain = max(0, lcount[i] - dcount[i])
        shifts_i = ecount[i] + late_plain
        if shifts_i:
            share = 0.6 if ctx.prefer_shift[i] == "early" else (0.4 if ctx.prefer_shift[i] == "late" else 0.5)
            soft += abs(ecount[i] - shifts_i * share) * 8

    for d in range(1, days):
        di = duty[d]
        if ctx.next_rest and di >= 0 and ((di in m[d + 1]) or (di in e[d + 1]) or duty[d + 1] == di):
            hard += 1

    op = st["opener"]
    for d in range(1, days + 1):
        if ctx.opener_bound and op[d] >= 0 and op[d] not in m[d]:
            soft += 1
        if ctx.opener_require and m[d] and op[d] < 0:
            soft += 1
        if ctx.forced_opener.get(d) is not None and op[d] != ctx.forced_opener[d]:
            soft += 1
        if (ctx.opener_bound and ctx.opener_max_repeat == 0
                and d > 1 and op[d] >= 0 and op[d] == op[d - 1]):
            soft += 1

    for d in range(1, days + 1):
        for i in ctx.forced_rest[d]:
            if (i in m[d]) or (i in e[d]) or duty[d] == i:
                hard += 1
        for i in ctx.forced_morning[d]:
            if i not in m[d]:
                hard += 1
        for i in ctx.forced_evening[d]:
            if i not in e[d]:
                hard += 1
    for a, b, shift in ctx.pair_separate:
        for d in range(1, days + 1):
            for kind, table in (("early", m), ("late", e)):
                if shift and shift != kind:
                    continue
                if a in table[d] and b in table[d]:
                    hard += 1
    for a, b, shift in ctx.pair_together:
        for d in range(1, days + 1):
            a_work = a in m[d] or a in e[d] or duty[d] == a
            b_work = b in m[d] or b in e[d] or duty[d] == b
            if a_work and b_work:
                if not any((a in table[d]) and (b in table[d]) for table in (m, e)):
                    hard += 1
    return hard * HARD + soft * SOFT, hard, soft


# --------------------------------------------------------------------------
# 局部搜索
# --------------------------------------------------------------------------
def anneal(ctx: Ctx, st, budget: float, rng: random.Random, max_iters: int | None = None):
    days, n = ctx.days, ctx.n
    m, e, duty = st["morning"], st["evening"], st["duty"]
    best = _clone(st)
    cur_cost = evaluate(ctx, st)[0]
    best_cost = cur_cost
    t_end = time.perf_counter() + budget
    t0 = time.perf_counter()
    temp0, temp1 = 400.0, 1.5
    it = 0
    while True:
        it += 1
        if max_iters and it >= max_iters:
            break
        if it % 32 == 0 and time.perf_counter() > t_end:
            break
        frac = min(1.0, (time.perf_counter() - t0) / max(0.01, budget))
        temp = temp0 * (temp1 / temp0) ** frac
        roll = rng.random()
        d = rng.randint(1, days)
        i = rng.randrange(n)
        if ctx.alternate[i]:
            others = [k for k in range(n) if not ctx.alternate[k]]
            if not others:
                break
            i = others[rng.randrange(len(others))]
        undo = None

        if roll < 0.34:
            if i == duty[d]:
                continue
            if i in m[d]:
                m[d].discard(i)
                undo = ("m+", d, i)
            elif i in e[d]:
                e[d].discard(i)
                undo = ("e+", d, i)
            elif rng.random() < 0.5:
                m[d].add(i)
                undo = ("m-", d, i)
            else:
                e[d].add(i)
                undo = ("e-", d, i)
        elif roll < 0.50:
            if i in m[d] and i not in e[d]:
                m[d].discard(i)
                e[d].add(i)
                undo = ("swap", d, i)
            elif i in e[d] and i != duty[d] and i not in m[d]:
                e[d].discard(i)
                m[d].add(i)
                undo = ("swap", d, i)
            else:
                continue
        elif roll < 0.66:
            j = rng.randrange(n)
            if i == j:
                continue
            if i in m[d] and j in e[d] and j != duty[d]:
                m[d].discard(i); m[d].add(j)
                e[d].discard(j); e[d].add(i)
                undo = ("xchg", d, i, j)
            elif i in e[d] and j in m[d] and i != duty[d]:
                m[d].discard(j); m[d].add(i)
                e[d].discard(i); e[d].add(j)
                undo = ("xchg", d, i, j)
            else:
                continue
        elif roll < 0.78:
            d2 = rng.randint(1, days)
            if d2 == d:
                continue
            if i in m[d] and i not in m[d2] and i not in e[d2] and duty[d2] != i:
                m[d].discard(i); m[d2].add(i)
                undo = ("mv", d, d2, i, "m")
            elif i in e[d] and i != duty[d] and i not in m[d2] and i not in e[d2] and duty[d2] != i:
                e[d].discard(i); e[d2].add(i)
                undo = ("mv", d, d2, i, "e")
            else:
                continue
        elif roll < 0.90:
            j = rng.randrange(n)
            if j == i or duty[d] != i or ctx.cap[j] <= 0:
                continue
            if ctx.alternate[i] or ctx.alternate[j]:
                continue
            d2 = rng.randint(1, days)
            if d2 == d or duty[d2] != j:
                continue
            if not (ctx.work_ok(i, d2) and ctx.work_ok(j, d)):
                continue
            if ctx.alternate[i] and not ctx.parity_ok(i, d2):
                continue
            if ctx.alternate[j] and not ctx.parity_ok(j, d):
                continue
            if d in ctx.no_duty[j] or d2 in ctx.no_duty[i]:
                continue
            duty[d], duty[d2] = j, i
            e[d].discard(i); e[d].add(j)
            e[d2].discard(j); e[d2].add(i)
            m[d].discard(i)
            m[d2].discard(j)
            undo = ("duty", d, d2, i, j)
        else:
            if not m[d]:
                continue
            cand = rng.choice(sorted(m[d]))
            old = st["opener"][d]
            if cand == old:
                continue
            st["opener"][d] = cand
            undo = ("op", d, old)

        cost = evaluate(ctx, st)[0]
        delta = cost - cur_cost
        if delta <= 0 or rng.random() < math.exp(-delta / max(1e-6, temp)):
            cur_cost = cost
            if cost < best_cost:
                best_cost = cost
                best = _clone(st)
        else:
            _undo(st, undo)
    _restore(st, best)
    return best_cost, it


def _undo(st, undo):
    kind = undo[0]
    m, e, op = st["morning"], st["evening"], st["opener"]
    if kind == "m+":
        m[undo[1]].discard(undo[2])
    elif kind == "m-":
        m[undo[1]].add(undo[2])
    elif kind == "e+":
        e[undo[1]].discard(undo[2])
    elif kind == "e-":
        e[undo[1]].add(undo[2])
    elif kind == "swap":
        d, i = undo[1], undo[2]
        if i in m[d]:
            m[d].discard(i); e[d].add(i)
        else:
            e[d].discard(i); m[d].add(i)
    elif kind == "xchg":
        d, i, j = undo[1], undo[2], undo[3]
        mi, mj = i in m[d], j in m[d]
        ei = i in e[d]
        m[d].discard(i); m[d].discard(j)
        e[d].discard(i); e[d].discard(j)
        if mi:
            m[d].add(j)
        else:
            m[d].add(i)
        if ei:
            e[d].add(j)
        else:
            e[d].add(i)
    elif kind == "mv":
        d, d2, i, sh = undo[1], undo[2], undo[3], undo[4]
        table = m if sh == "m" else e
        table[d2].discard(i)
        table[d].add(i)
    elif kind == "duty":
        d, d2, i, j = undo[1], undo[2], undo[3], undo[4]
        di = st["duty"]
        di[d], di[d2] = i, j
        e[d].discard(j); e[d].add(i)
        e[d2].discard(i); e[d2].add(j)
    elif kind == "op":
        op[undo[1]] = undo[2]


def _clone(st):
    return {
        "morning": [set(x) for x in st["morning"]],
        "evening": [set(x) for x in st["evening"]],
        "duty": list(st["duty"]),
        "opener": list(st["opener"]),
    }


def _restore(st, best):
    st["morning"][:] = [set(x) for x in best["morning"]]
    st["evening"][:] = [set(x) for x in best["evening"]]
    st["duty"][:] = list(best["duty"])
    st["opener"][:] = list(best["opener"])


def _repair_support(ctx: Ctx, st):
    """修补：怡晚班+值班时，早班必须有吴（规则 9）。"""
    days, n = ctx.days, ctx.n
    m, e, duty = st["morning"], st["evening"], st["duty"]
    if ctx.yi is None or ctx.wu is None:
        return
    best_cost = evaluate(ctx, st)[0]
    wu = ctx.wu
    for d in range(1, days + 1):
        if duty[d] != ctx.yi or wu in m[d]:
            continue
        if not ctx.work_ok(wu, d):
            continue
        # a) 早班没满就直接加
        if len(m[d]) < ctx.hi:
            m[d].add(wu)
            cost = evaluate(ctx, st)[0]
            if cost < best_cost:
                best_cost = cost
                continue
            m[d].discard(wu)
        # b) 早班满了：优先换掉非关键岗；若早班有两名以上关键岗，也可以换掉其中一名
        key_count = sum(1 for x in m[d] if ctx.key[x])
        for x in sorted(m[d]):
            if x == duty[d] or ctx.alternate[x]:
                continue
            if ctx.key[x] and key_count < 2:
                continue
            m[d].discard(x)
            m[d].add(wu)
            cost = evaluate(ctx, st)[0]
            if cost < best_cost:
                best_cost = cost
                break
            m[d].discard(wu)
            m[d].add(x)


def _rest_of(ctx: Ctx, st, day: int):
    """值班次日必须休息的人"""
    d = st["duty"]
    return d[day - 1] if day > 1 else -1


def _repair_key_coverage(ctx: Ctx, st):
    """修补：某个班次没有关键岗时，尽量把关键岗换进来。"""
    days, n = ctx.days, ctx.n
    m, e, duty = st["morning"], st["evening"], st["duty"]
    best_cost = evaluate(ctx, st)[0]
    key_ids = [i for i in range(n) if ctx.key[i]]
    if not key_ids:
        return best_cost
    for d in range(1, days + 1):
        for table, other in ((m, e), (e, m)):
            if any(k in table[d] for k in key_ids):
                continue
            done = False
            # 1) 与另一个班次里的关键岗互换
            for j in sorted(other[d]):
                if not ctx.key[j] or j == duty[d] or ctx.alternate[j]:
                    continue
                for i in sorted(table[d]):
                    if ctx.key[i] or i == duty[d] or ctx.alternate[i]:
                        continue
                    table[d].discard(i); other[d].add(i)
                    other[d].discard(j); table[d].add(j)
                    cost = evaluate(ctx, st)[0]
                    if cost < best_cost:
                        best_cost = cost
                        done = True
                        break
                    other[d].discard(i); table[d].add(i)
                    other[d].add(j); table[d].discard(j)
                if done:
                    break
            if done:
                continue
            # 2) 从空班/其他班次的人里补一个关键岗
            if len(table[d]) >= ctx.hi:
                continue
            for k in key_ids:
                if k in table[d] or k in other[d] or duty[d] == k:
                    continue
                if not ctx.work_ok(k, d):
                    continue
                if k in ctx.forced_rest[d] or _rest_of(ctx, st, d) == k:
                    continue
                if ctx.alternate[k] and not ctx.parity_ok(k, d):
                    continue
                if ctx.target_work[k] is not None:
                    worked_now = sum(1 for x in range(1, days + 1)
                                     if (k in m[x]) or (k in e[x]) or duty[x] == k)
                    if worked_now > ctx.target_work[k]:
                        continue
                table[d].add(k)
                cost = evaluate(ctx, st)[0]
                if cost < best_cost:
                    best_cost = cost
                    break
                table[d].discard(k)
    return best_cost


def _repair_shift_runs(ctx: Ctx, st, rounds: int = 3):
    """确定性修补：把超出上限的连续早班/晚班尽量换到另一个班次。"""
    days, n = ctx.days, ctx.n
    m, e, duty = st["morning"], st["evening"], st["duty"]
    best_cost = evaluate(ctx, st)[0]
    for _ in range(rounds):
        improved = False
        for i in range(n):
            if ctx.alternate[i]:
                continue
            for table, other, limit in ((m, e, ctx.early_warn and ctx.early_warn - 1), (e, m, ctx.late_warn and ctx.late_warn - 1)):
                if limit is None:
                    continue
                d = 1
                while d <= days:
                    if i not in table[d]:
                        d += 1
                        continue
                    start = d
                    while d <= days and i in table[d]:
                        d += 1
                    run = list(range(start, d))
                    if len(run) <= limit:
                        continue
                    targets = sorted({run[0]} | set(run[limit:]))
                    for day in targets:
                        if duty[day] == i or i in other[day]:
                            continue
                        # 方案 A：与另一班次的某人互换（保持两边人数与关键岗覆盖）
                        swapped = False
                        for j in sorted(other[day]):
                            if j in table[day] or j == duty[day]:
                                continue
                            table[day].discard(i)
                            other[day].add(i)
                            other[day].discard(j)
                            table[day].add(j)
                            cost = evaluate(ctx, st)[0]
                            if cost < best_cost:
                                best_cost = cost
                                improved = True
                                swapped = True
                                break
                            other[day].discard(i)
                            table[day].add(i)
                            other[day].add(j)
                            table[day].discard(j)
                        if swapped:
                            continue
                        # 方案 B：直接把人挪到另一班次
                        if len(other[day]) >= ctx.hi:
                            continue
                        table[day].discard(i)
                        other[day].add(i)
                        cost = evaluate(ctx, st)[0]
                        if cost < best_cost:
                            best_cost = cost
                            improved = True
                        else:
                            other[day].discard(i)
                            table[day].add(i)
        if not improved:
            break
    return best_cost


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------
def generate(doc: dict, employees: list[dict], budget: float | None = None,
             seed: int | None = None, iterations: int | None = None) -> dict:
    rng = random.Random(seed)
    ctx = Ctx(doc, employees)
    budget = float(budget or 4.0)
    started = time.perf_counter()
    notes: list[str] = []

    alts = [i for i in range(ctx.n) if ctx.alternate[i]]
    parity_sets = [{}]
    for mask in range(1, 1 << len(alts)):
        parity_sets.append({alts[k]: (mask >> k) & 1 for k in range(len(alts))})
    parity_sets = parity_sets[:8]

    def _distinct(p):
        vals = [p.get(i) for i in alts]
        if any(v is None for v in vals):
            return False  # 没排相位的上一休一员工会天天上班，把班次挤爆
        return len(set(vals)) == len(vals)

    # 互补相位的组合优先（避免周/怡同一天都上全天班，把早晚班挤爆）
    parity_sets.sort(key=lambda p: 0 if _distinct(p) else 1)

    best = None
    for parity in parity_sets:
        ctx.parity = [parity.get(i) for i in range(ctx.n)]
        got = assign_duty(ctx, rng)
        if got is None:
            continue
        duty, rest, _ = got
        st = build_shifts(ctx, duty, rest, rng)
        _fill_openers(ctx, st)
        cost, hard, soft = evaluate(ctx, st)
        if best is None or cost < best[0]:
            best = (cost, hard, soft, st, parity)
    if best is None:
        return {"ok": False, "error": "无法完成值班分配，请检查可用日与值班岗设置", "schedule": None}

    cost, hard, soft, st, parity = best
    ctx.parity = [parity.get(i) for i in range(ctx.n)]

    remain = max(0.5, budget - (time.perf_counter() - started))
    _, iters = anneal(ctx, st, remain, rng, max_iters=iterations)
    _repair_shift_runs(ctx, st)
    _repair_key_coverage(ctx, st)
    _repair_support(ctx, st)
    _fill_openers(ctx, st)
    hard, soft = evaluate(ctx, st)[1:]

    out = {str(d): {"morning": [], "evening": [], "duty": [], "opener": ""} for d in range(1, ctx.days + 1)}
    for d in range(1, ctx.days + 1):
        out[str(d)]["morning"] = [ctx.ids[i] for i in sorted(st["morning"][d])]
        out[str(d)]["evening"] = [ctx.ids[i] for i in sorted(st["evening"][d])]
        di = st["duty"][d]
        out[str(d)]["duty"] = [ctx.ids[di]] if di >= 0 else []
        op = st["opener"][d]
        out[str(d)]["opener"] = ctx.ids[op] if op >= 0 else ""

    elapsed = time.perf_counter() - started
    if hard:
        notes.append(f"仍有 {hard} 项硬冲突无法自动消解，已输出冲突最少的方案，请人工确认。")
    else:
        notes.append("全部硬性规则已满足。")
    notes.append(f"生成耗时 {elapsed:.1f}s，退火迭代 {iters:,} 次。")
    return {
        "ok": True,
        "schedule": out,
        "hard": hard,
        "soft": soft,
        "iterations": iters,
        "seconds": round(elapsed, 2),
        "notes": notes,
    }


def _worker_task(payload):
    """多进程里跑一次完整生成（Windows 用 spawn，参数必须可 pickle）。"""
    doc, employees, budget, iterations, seed = payload
    try:
        return generate(doc, employees, budget=budget, seed=seed, iterations=iterations)
    except Exception as exc:  # 单个进程失败不影响整体
        return {"ok": False, "error": str(exc)}


def generate_best(doc: dict, employees: list[dict], budget: float | None = None,
                  iterations: int | None = None, seed: int | None = None,
                  workers: int = 1) -> dict:
    """多核并行：把迭代数分给多个进程，各自退火，取硬冲突最少、软代价最低的一版。"""
    import os

    workers = max(1, min(int(workers or 1), os.cpu_count() or 1, 8))
    if workers <= 1 or not iterations or iterations < 40000:
        return generate(doc, employees, budget=budget, seed=seed, iterations=iterations)

    per = max(20000, int(iterations) // workers)
    jobs = [(doc, employees, budget, per, (seed or 0) + k * 7919) for k in range(workers)]
    t0 = time.perf_counter()
    try:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_worker_task, jobs))
    except Exception as exc:
        import traceback

        print("[generator] 进程池创建失败，改为串行多轮：", file=sys.stderr)
        traceback.print_exc()
        # 串行多轮：不创建子进程，但同样跑多个独立种子，取最优，质量与并行版一致
        results = []
        for job in jobs:
            results.append(_worker_task(job))
        ok = [r for r in results if r and r.get("ok")]
        if ok:
            best = min(ok, key=lambda r: (r.get("hard", 999), r.get("soft", 1e9)))
            best["workers"] = 1
            best["iterations"] = sum(r.get("iterations", 0) for r in ok)
            kept = [n for n in (best.get("notes") or []) if not n.startswith("生成耗时")]
            kept.append(
                f"多核不可用（{type(exc).__name__}），已改为串行跑 {len(ok)} 轮"
                f"（合计 {best['iterations']:,} 次）并取最优一版。"
            )
            best["notes"] = kept
            return best
        res = generate(doc, employees, budget=budget, seed=seed, iterations=iterations)
        res.setdefault('notes', []).append(
            f'多核并行不可用（{type(exc).__name__}: {exc}），已改用单进程。')
        res['workers'] = 1
        return res

    ok = [r for r in results if r and r.get("ok")]
    if not ok:
        return generate(doc, employees, budget=budget, seed=seed, iterations=iterations)
    best = min(ok, key=lambda r: (r.get("hard", 999), r.get("soft", 1e9)))
    wall = time.perf_counter() - t0
    best["workers"] = workers
    best["wall"] = round(wall, 2)
    best["iterations"] = sum(r.get("iterations", 0) for r in ok)
    kept = [n for n in (best.get("notes") or []) if not n.startswith("生成耗时")]
    kept.append(
        f"多核并行：{workers} 个进程各退火约 {per:,} 次，合计 {best['iterations']:,} 次，"
        f"用时 {wall:.1f}s，取冲突最少的一版。"
    )
    best["notes"] = kept
    return best


def _fill_openers(ctx: Ctx, st):
    days = ctx.days
    op = st["opener"]
    counts = {}
    last = -1
    for d in range(1, days + 1):
        m = st["morning"][d]
        if not m:
            op[d] = -1
            continue
        forced = ctx.forced_opener.get(d)
        if forced is not None and forced in m:
            op[d] = forced
            last = forced
            counts[forced] = counts.get(forced, 0) + 1
            continue
        if op[d] in m and op[d] != last:
            last = op[d]
            counts[op[d]] = counts.get(op[d], 0) + 1
            continue
        cands = [i for i in sorted(m) if i != last] or sorted(m)
        cands.sort(key=lambda i: (counts.get(i, 0), i))
        op[d] = cands[0]
        last = op[d]
        counts[op[d]] = counts.get(op[d], 0) + 1

    # 二次平衡：把开门次数多的人，换成同一天早班里次数更少的人（不破坏「不连续」）
    for _ in range(4 if ctx.on("R18") else 0):
        cnt = {}
        for d in range(1, days + 1):
            if op[d] >= 0:
                cnt[op[d]] = cnt.get(op[d], 0) + 1
        changed = False
        for d in range(1, days + 1):
            cur = op[d]
            if cur < 0:
                continue
            m = st["morning"][d]
            prev = op[d - 1] if d > 1 else -1
            nxt = op[d + 1] if d < days else -1
            best = None
            for x in sorted(m):
                if x == cur or x == prev or x == nxt:
                    continue
                if best is None or cnt.get(x, 0) < cnt.get(best, 0):
                    best = x
            if best is None:
                continue
            if cnt.get(cur, 0) - cnt.get(best, 0) >= 2:
                op[d] = best
                cnt[cur] = cnt.get(cur, 0) - 1
                cnt[best] = cnt.get(best, 0) + 1
                changed = True
        if not changed:
            break
