import { reactive, computed, nextTick } from "vue";

/* ------------------------------ 基础工具 ------------------------------ */
async function request(url, opts = {}) {
  const resp = await fetch(url, opts);
  let payload = null;
  try {
    payload = await resp.json();
  } catch (e) {
    throw new Error("服务器返回异常（HTTP " + resp.status + "）");
  }
  if (!resp.ok || payload.ok === false) {
    throw new Error(payload && payload.error ? payload.error : "请求失败");
  }
  return payload;
}

const getJSON = (url) => request(url);

const postJSON = (url, body) =>
  request(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

let toastSeq = 0;

export const S = reactive({
  data: null,
  year: 2026,
  month: 10,
  sel: null,
  highlightEmp: null,
  focusCells: [],   // 冲突涉及的格子 ["eid:day"]
  focusStamp: 0,
  upd: {
    open: false,
    status: "idle",     // idle | checking | latest | available | downloading | ready | applying | error | offline
    info: null,
    progress: { downloaded: 0, total: 0, speed: 0, state: "idle", message: "" },
    error: "",
  },
  focusV: null,
  filter: "all",
  busy: false,
  iterations: 100000,
  workers: 4,
  undo: [],
  toasts: [],
  modal: null,
  peopleSel: null,
  ruleType: "rest",
  editingRule: null,
  savedAt: "",
  savedState: "idle",
  loading: true,
  error: "",
});

/* ------------------------------ computed ------------------------------ */
export const doc = computed(() => (S.data ? S.data.doc : null));
export const employees = computed(() => (S.data ? S.data.employees : []));
export const check = computed(() => (S.data ? S.data.check : null));
export const catalog = computed(() => (S.data ? S.data.catalog : { base: [], extra: [] }));

/** 把当月日期按「周一到周日」切成若干周，不足的位置为 null（不可编辑） */
export const weeks = computed(() => {
  if (!S.data) return [];
  const d = S.data.doc;
  const first = new Date(d.year, d.month - 1, 1);
  const offset = (first.getDay() + 6) % 7; // 周一 = 0
  const cells = [];
  for (let i = 0; i < offset; i++) cells.push(null);
  for (let i = 1; i <= d.days; i++) cells.push(i);
  while (cells.length % 7 !== 0) cells.push(null);
  const out = [];
  for (let i = 0; i < cells.length; i += 7) {
    const week = cells.slice(i, i + 7);
    const real = week.filter((x) => x !== null);
    out.push({
      index: out.length + 1,
      cells: week,
      from: real.length ? real[0] : null,
      to: real.length ? real[real.length - 1] : null,
    });
  }
  return out;
});

export const statsById = computed(() => {
  const map = {};
  (check.value ? check.value.stats : []).forEach((s) => (map[s.id] = s));
  return map;
});

export const violationsById = computed(() => {
  const map = {};
  (check.value ? check.value.violations : []).forEach((v) => (map[v.id] = v));
  return map;
});

/* ------------------------------ 提示条 ------------------------------ */
export function toast(html, kind = "", ms = 3200) {
  const id = ++toastSeq;
  S.toasts.push({ id, html, kind });
  setTimeout(() => {
    const idx = S.toasts.findIndex((t) => t.id === id);
    if (idx >= 0) S.toasts.splice(idx, 1);
  }, ms);
}

/* ------------------------------ 载入 ------------------------------ */
export async function load(year, month) {
  if (year) S.year = Number(year);
  if (month) S.month = Number(month);
  S.loading = true;
  S.error = "";
  try {
    const payload = await request(`/api/state?year=${S.year}&month=${S.month}`);
    S.undo = [];
    S.sel = null;
    S.focusV = null;
    applyState(payload);
    S.savedAt = (payload.now || "").slice(11, 19);
    S.savedState = "ok";
  } catch (err) {
    S.error = err.message;
    toast(escapeHtml(err.message), "err", 6000);
  } finally {
    S.loading = false;
  }
}

export function applyState(payload) {
  S.data = payload;
  S.year = payload.year;
  S.month = payload.month;
  if (S.sel && S.sel.day > payload.doc.days) S.sel = null;
  if (S.sel && S.sel.eid && !payload.employees.some((e) => e.id === S.sel.eid)) S.sel = null;
  if (payload.generate) {
    const g = payload.generate;
    toast(
      `<b>${g.hard ? "已生成（含残余冲突）" : "生成完成"}</b><br>` +
        g.notes.map((n) => "· " + escapeHtml(n)).join("<br>"),
      g.hard ? "warn" : "ok",
      8000
    );
  } else if (payload.message) {
    toast(escapeHtml(payload.message), "ok", 2000);
  }
}

/* ------------------------------ 保存 ------------------------------ */
export async function save(opts = {}) {
  const d = S.data.doc;
  if (!opts.silent) S.busy = true;
  S.savedState = "saving";
  try {
    const payload = await postJSON("/api/state", {
      year: d.year,
      month: d.month,
      schedule: d.schedule,
      availability: d.availability,
      extraRules: d.extraRules,
      ruleOverrides: d.ruleOverrides,
      note: d.note,
    });
    applyState(payload);
    S.savedAt = (payload.now || "").slice(11, 19);
    S.savedState = "ok";
    if (!opts.silent) {
      toast(
        `已保存 · 冲突 ${payload.check.errors} / 提示 ${payload.check.warns}`,
        payload.check.errors ? "warn" : "ok",
        1800
      );
    }
  } catch (err) {
    S.savedState = "err";
    toast("保存失败：" + escapeHtml(err.message), "err", 5000);
  } finally {
    if (!opts.silent) S.busy = false;
  }
}

/* ------------------------------ 撤销 ------------------------------ */
export function snapshot() {
  const d = S.data.doc;
  S.undo.push(
    JSON.stringify({
      schedule: d.schedule,
      availability: d.availability,
      extraRules: d.extraRules,
      ruleOverrides: d.ruleOverrides,
    })
  );
  if (S.undo.length > 60) S.undo.shift();
}

export async function undo() {
  if (!S.undo.length) return;
  const snap = JSON.parse(S.undo.pop());
  const d = S.data.doc;
  d.schedule = snap.schedule;
  d.availability = snap.availability;
  d.extraRules = snap.extraRules;
  d.ruleOverrides = snap.ruleOverrides;
  await save({ silent: true });
  toast("已撤销上一步操作", "", 1600);
}

/* ------------------------------ 单元格编辑 ------------------------------ */
export function select(eid, day) {
  S.focusCells = [];
  S.focusV = null;
  S.sel = { eid, day };
  S.focusV = null;
}

export function setStatus(eid, day, status) {
  const d = S.data.doc;
  const cell = d.schedule[String(day)];
  if (!cell) return;
  snapshot();
  const strip = () => {
    cell.morning = cell.morning.filter((x) => x !== eid);
    cell.evening = cell.evening.filter((x) => x !== eid);
    cell.duty = cell.duty.filter((x) => x !== eid);
    if (cell.opener === eid) cell.opener = "";
  };
  const avail = d.availability[eid] || [];
  if (status === "unavailable") {
    strip();
    d.availability[eid] = avail.filter((x) => x !== day);
  } else if (status === "clear" || status === "off") {
    strip();
  } else {
    if (!avail.includes(day)) d.availability[eid] = [...avail, day].sort((a, b) => a - b);
    strip();
    if (status === "early") cell.morning.push(eid);
    else if (status === "late") cell.evening.push(eid);
    else if (status === "both") {
      if (!cell.morning.includes(eid)) cell.morning.push(eid);
      if (!cell.evening.includes(eid)) cell.evening.push(eid);
    }
    else if (status === "duty") {
      cell.duty = [eid];
      cell.evening.push(eid);
      cell.morning = cell.morning.filter((x) => x !== eid);
    }
    if (status === "early" || status === "late") {
      if (!cell.opener && cell.morning.length) cell.opener = cell.morning[0];
    }
  }
  if (cell.opener && !cell.morning.includes(cell.opener)) cell.opener = cell.morning[0] || "";
  S.sel = { eid, day };
  save({ silent: true });
}

export function toggleShift(eid, day, kind) {
  const d = S.data.doc;
  const cell = d.schedule[String(day)];
  if (!cell) return;
  const avail = d.availability[eid] || [];
  if (!avail.includes(day)) {
    toast("该员工当天为不可用日，请先在「人员」中调整可用日", "warn");
    return;
  }
  snapshot();
  const list = kind === "early" ? cell.morning : kind === "late" ? cell.evening : cell.duty;
  const inList = list.includes(eid);
  if (kind === "duty") {
    cell.duty = inList ? [] : [eid];
    if (!inList) {
      if (!cell.evening.includes(eid)) cell.evening.push(eid);
      cell.morning = cell.morning.filter((x) => x !== eid);
    }
  } else if (inList) {
    if (kind === "early") cell.morning = cell.morning.filter((x) => x !== eid);
    else cell.evening = cell.evening.filter((x) => x !== eid);
  } else {
    const other = kind === "early" ? cell.evening : cell.morning;
    const dedup = other.filter((x) => x !== eid);
    if (kind === "early") {
      cell.morning = [...cell.morning.filter((x) => x !== eid), eid];
      cell.evening = dedup;
    } else {
      cell.evening = [...cell.evening.filter((x) => x !== eid), eid];
      cell.morning = dedup;
    }
  }
  if (cell.duty.includes(eid) && !cell.evening.includes(eid)) cell.evening.push(eid);
  if (cell.opener && !cell.morning.includes(cell.opener)) cell.opener = cell.morning[0] || "";
  S.sel = { eid, day };
  save({ silent: true });
}

/* ------------------------------ 开门人 ------------------------------ */
export function setOpener(day, eid) {
  const d = S.data.doc;
  const cell = d.schedule[String(day)];
  if (!cell) return;
  if (eid && !cell.morning.includes(eid)) {
    toast("开门人必须是当天早班成员", "warn");
    return;
  }
  snapshot();
  cell.opener = eid || "";
  save({ silent: true });
}

/** 按生成器同样的规则给某天自动挑一个：不是昨天的人，且开过门次数最少 */
export function autoOpener(day) {
  const d = S.data.doc;
  const cell = d.schedule[String(day)];
  if (!cell || !cell.morning.length) return;
  const counts = {};
  for (let i = 1; i < day; i++) {
    const prev = d.schedule[String(i)].opener;
    if (prev) counts[prev] = (counts[prev] || 0) + 1;
  }
  const last = day > 1 ? d.schedule[String(day - 1)].opener : "";
  const cands = cell.morning.filter((x) => x !== last);
  const pool = (cands.length ? cands : cell.morning).slice();
  pool.sort((a, b) => (counts[a] || 0) - (counts[b] || 0));
  setOpener(day, pool[0]);
}

/* ------------------------------ 生成 / 重置 ------------------------------ */
export async function generate() {
  if (S.busy) return;
  snapshot();
  S.busy = true;
  try {
    const payload = await postJSON("/api/generate", {
      year: S.data.doc.year,
      month: S.data.doc.month,
      extraRules: S.data.doc.extraRules,
      ruleOverrides: S.data.doc.ruleOverrides,
      availability: S.data.doc.availability,
      iterations: S.iterations,
      workers: S.workers,
      budget: Math.max(8, Math.round(S.iterations / 12000)),
    });
    applyState(payload);
  } catch (err) {
    toast("生成失败：" + escapeHtml(err.message), "err", 5000);
  } finally {
    S.busy = false;
  }
}

export async function resetMonth(mode) {
  snapshot();
  try {
    const payload = await postJSON("/api/reset", {
      year: S.data.doc.year,
      month: S.data.doc.month,
      mode,
    });
    applyState(payload);
    toast("已清空本月排班", "ok", 2000);
  } catch (err) {
    toast(escapeHtml(err.message), "err", 4000);
  }
}

/* ------------------------------ 导入导出 ------------------------------ */
export function exportCsv() {
  const d = S.data.doc;
  window.location.href = `/api/export.csv?year=${d.year}&month=${d.month}`;
  toast("正在导出 CSV…", "", 1600);
}

export async function importFile(file) {
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  fd.append("year", S.data.doc.year);
  fd.append("month", S.data.doc.month);
  S.busy = true;
  try {
    const payload = await request("/api/import", { method: "POST", body: fd });
    applyState(payload);
    toast("导入成功：" + escapeHtml(payload.message || ""), "ok", 3000);
  } catch (err) {
    toast("导入失败：" + escapeHtml(err.message), "err", 5000);
  } finally {
    S.busy = false;
  }
}

/* ------------------------------ 员工 ------------------------------ */
export async function moveEmployee(eid, delta) {
  const list = S.data.employees;
  const idx = list.findIndex((e) => e.id === eid);
  const target = idx + delta;
  if (idx < 0 || target < 0 || target >= list.length) return;
  // 先本地换位，界面立即响应
  const item = list.splice(idx, 1)[0];
  list.splice(target, 0, item);
  try {
    const payload = await postJSON("/api/employees", {
      action: "move",
      id: eid,
      delta,
      year: S.data.doc.year,
      month: S.data.doc.month,
    });
    // 只刷新依赖顺序的统计，避免整页重绘造成闪烁
    S.data.check = payload.check;
    S.data.months = payload.months;
    S.data.employees = payload.employees;
  } catch (err) {
    toast(escapeHtml(err.message), "err", 4000);
    S.data.employees = list;
  }
}

export async function saveEmployee(id, data, isNew) {
  try {
    const payload = await postJSON("/api/employees", {
      action: isNew ? "create" : "update",
      id,
      employee: data,
      year: S.data.doc.year,
      month: S.data.doc.month,
    });
    if (isNew) {
      const created = payload.employees.find((e) => e.name === data.name);
      if (created) S.peopleSel = created.id;
    }
    applyState(payload);
    toast("员工信息已保存", "ok", 1800);
    return true;
  } catch (err) {
    toast(escapeHtml(err.message), "err", 4000);
    return false;
  }
}

export async function deleteEmployee(id) {
  try {
    const payload = await postJSON("/api/employees", {
      action: "delete",
      id,
      year: S.data.doc.year,
      month: S.data.doc.month,
    });
    S.peopleSel = payload.employees[0] ? payload.employees[0].id : null;
    applyState(payload);
    toast("员工已删除", "ok", 1800);
  } catch (err) {
    toast(escapeHtml(err.message), "err", 4000);
  }
}

/* ------------------------------ 规则 ------------------------------ */
export async function setRuleOverride(code, patch) {
  snapshot();
  const d = S.data.doc;
  d.ruleOverrides = d.ruleOverrides || {};
  d.ruleOverrides[code] = { ...(d.ruleOverrides[code] || {}), ...patch };
  await save({ silent: true });
}

export async function saveExtraRules(list) {
  snapshot();
  S.data.doc.extraRules = list;
  await save({ silent: true });
}


/* ------------------------------ 在线更新 ------------------------------ */
export function openUpdate() {
  S.upd.open = true;
  S.upd.error = "";
  if (S.upd.status === "idle" || S.upd.status === "error") checkUpdate();
}

export function closeUpdate() {
  S.upd.open = false;
}

export async function checkUpdate() {
  S.upd.status = "checking";
  S.upd.error = "";
  try {
    const info = await getJSON("/api/update/check");
    S.upd.info = info;
    if (info.error) {
      S.upd.status = "error";
      S.upd.error = info.error;
    } else if (!info.hasUpdate) {
      S.upd.status = "latest";
    } else {
      S.upd.status = "available";
    }
  } catch (err) {
    S.upd.status = "error";
    S.upd.error = escapeHtml(err.message);
  }
}

export async function startUpdate() {
  S.upd.status = "downloading";
  S.upd.progress = { downloaded: 0, total: 0, speed: 0, state: "downloading", message: "" };
  try {
    await postJSON("/api/update/download", {});
  } catch (err) {
    S.upd.status = "error";
    S.upd.error = escapeHtml(err.message);
  }
}

export async function pollUpdate() {
  try {
    const pr = await getJSON("/api/update/progress");
    S.upd.progress = pr;
    if (pr.state === "ready") S.upd.status = "ready";
    else if (pr.state === "error") {
      S.upd.status = "error";
      S.upd.error = pr.message || "下载失败";
    }
  } catch (err) {
    /* 服务重启中，忽略 */
  }
}

export async function applyUpdate() {
  S.upd.status = "applying";
  try {
    await postJSON("/api/update/apply", {});
  } catch (err) {
    S.upd.status = "error";
    S.upd.error = escapeHtml(err.message);
    return;
  }
  // 等服务重启
  const t0 = Date.now();
  const timer = setInterval(async () => {
    if (Date.now() - t0 > 90000) {
      clearInterval(timer);
      S.upd.status = "offline";
      return;
    }
    try {
      await getJSON("/api/health");
      clearInterval(timer);
      location.reload();
    } catch (err) {
      /* 还没起来，继续等 */
    }
  }, 1200);
}

/* ------------------------------ 工具 ------------------------------ */
export function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function empName(eid) {
  const e = (S.data ? S.data.employees : []).find((x) => x.id === eid);
  return e ? e.name : eid;
}

export const WEEK_LABEL = ["一", "二", "三", "四", "五", "六", "日"];

export const STATUS_GLYPH = {
  early: "早班",
  late: "晚班",
  duty: "值班",
  both: "全天",
  off: "休息",
  unavailable: "不可用",
  over: "超次",
  conflict: "冲突",
};

export const STATUS_TEXT = {
  early: "早班",
  late: "晚班",
  duty: "值班",
  both: "早班+晚班（全天）",
  off: "休息（空班）",
  unavailable: "不可用日",
  over: "超过次数上限",
  conflict: "不可用日排班",
};

/* ------------------------------ 冲突定位 ------------------------------ */
export function locate(v) {
  // 同一个冲突再点一次 → 取消高亮，恢复原本的排班地图
  if (S.focusV === v.id && (S.focusCells || []).length) {
    S.focusV = null;
    S.focusCells = [];
    return;
  }
  S.focusV = v.id;
  const cells = (v.cells || []).filter((c) => c && c[0]);
  let eid = null;
  let day = v.day || 1;
  if (cells.length) {
    const pick = cells.find((c) => c[1] === v.day) || cells[0];
    eid = pick[0];
    day = pick[1] || day;
  }
  if (!eid && S.data.employees.length) {
    const emp = S.data.employees.find((e) => e.id === v.persons?.[0]) || S.data.employees[0];
    eid = emp.id;
  }
  S.sel = { eid, day };
  // 该冲突涉及的所有格子：这些高亮，其余变暗
  // R7 → 当天所有关键岗；R5 → 该员工全月值班日；R14 → 连续早班那一段 …
  S.focusCells = cells.map((c) => `${c[0]}:${c[1]}`);
  nextTick(() => {
    const el = document.querySelector(".gcell.cell.is-selected");
    if (el) el.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  });
}
