<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import {
  S,
  weeks,
  employees,
  check,
  statsById,
  select,
  WEEK_LABEL,
  STATUS_GLYPH,
  STATUS_TEXT,
} from "../store";

const scrollRef = ref(null);
const cellH = ref(24);
const HEAD_H = 22;
const showMeta = computed(() => cellH.value >= 40);

const today = new Date();
const isThisMonth = computed(() => {
  const d = S.data.doc;
  return today.getFullYear() === d.year && today.getMonth() + 1 === d.month;
});

const sevMap = computed(() => {
  const m = {};
  check.value.violations.forEach((v) => (m[v.id] = v.severity));
  return m;
});

/* ------------------------------ 尺寸自适应 ------------------------------ */
function fit() {
  const el = scrollRef.value;
  if (!el) return;
  const bands = weeks.value.length || 1;
  const rows = employees.value.length || 1;
  const gap = parseFloat(getComputedStyle(el).rowGap) || 12;
  const avail = el.clientHeight - gap * (bands - 1) - 10;
  let h = Math.floor((avail / bands - HEAD_H) / rows);
  cellH.value = Math.max(15, Math.min(52, h));
  el.style.setProperty("--cell-h", cellH.value + "px");
}
onMounted(() => {
  nextTick(fit);
  window.addEventListener("app:resize", fit);
});
onUnmounted(() => window.removeEventListener("app:resize", fit));
watch(
  () => [S.data && S.data.doc.year, S.data && S.data.doc.month, employees.value.length],
  () => nextTick(fit)
);

/* ------------------------------ 数据取值 ------------------------------ */
function cellOf(eid, day) {
  const row = check.value.grid[eid] || {};
  return row[String(day)] || { s: "off", v: [], duty: false, opener: false };
}
function statOf(eid) {
  return statsById.value[eid] || { duty: 0, target: 0, early: 0, late: 0 };
}
function dayRow(day) {
  return check.value.days[day - 1] || { duty: [], opener: "", weekday: "" };
}
function dutyText(day) {
  const names = dayRow(day).duty;
  return names && names.length ? "值 " + names.join("") : "值 —";
}
function openText(day) {
  const op = dayRow(day).opener;
  return op ? "开 " + op : "";
}
function countBad(day) {
  const r = dayRow(day);
  return r.mCount < 2 || r.mCount > 3 || r.eCount < 2 || r.eCount > 3;
}
function countTitle(day) {
  const r = dayRow(day);
  return `早班 ${r.mCount} 人 · 晚班 ${r.eCount} 人`;
}
function rangeText(w) {
  if (w.from == null) return "—";
  const m = S.data.doc.month;
  const pad = (x) => String(x).padStart(2, "0");
  return `${m}.${pad(w.from)}–${pad(w.to)}`;
}

/* ------------------------------ 样式 ------------------------------ */
function gridStyle() {
  return {
    gridTemplateColumns: "96px repeat(7, minmax(0, 1fr))",
    gridTemplateRows: `${HEAD_H}px repeat(${employees.value.length}, ${cellH.value}px)`,
  };
}
function headClass(day, idx) {
  return {
    weekend: idx >= 5,
    today: day && isThisMonth.value && today.getDate() === day,
    "is-blank": day === null,
  };
}
function nameClass(emp) {
  const st = statOf(emp.id);
  return {
    over: st.over,
    dim: S.highlightEmp && S.highlightEmp !== emp.id,
    active: S.highlightEmp === emp.id,
    "is-blank": false,
  };
}
function cellClass(emp, day) {
  if (day === null) return { "is-blank": true };
  const g = cellOf(emp.id, day);
  const hasErr = g.v.some((id) => sevMap.value[id] === "error");
  const hasWarn = !hasErr && g.v.some((id) => sevMap.value[id] === "warn");
  const focus = S.focusCells || [];
  const inFocus = focus.includes(`${emp.id}:${day}`);
  return [
    "s-" + g.s,
    {
      "has-issue": hasErr,
      "has-warn": hasWarn,
      "is-selected": S.sel && S.sel.day === day && S.sel.eid === emp.id,
      // 选中员工：其他行整体变暗（不只是名字）
      "row-dim": !!S.highlightEmp && S.highlightEmp !== emp.id && !inFocus,
      // 冲突聚焦：涉及的格子闪烁，其余变暗
      "is-focus": inFocus,
      "is-out": focus.length > 0 && !inFocus,
    },
  ];
}
function cellTitle(emp, day) {
  const g = cellOf(emp.id, day);
  const w = dayRow(day).weekday;
  return `${S.data.doc.month}月${day}日 周${w} · ${emp.name} · ${STATUS_TEXT[g.s] || g.s}` +
    (g.opener ? " · 开门人" : "") + (g.duty ? " · 当班值班" : "");
}

function onCellClick(emp, day) {
  if (day === null) return;
  select(emp.id, day);
}
function onNameClick(emp) {
  S.peopleSel = emp.id;
  S.modal = "people";
}
function onHeadClick(day) {
  if (day === null) return;
  select(null, day);
}
</script>

<template>
  <section class="panel map-panel">
    <div class="panel-head">
      <h2><span class="pdot"></span>调度地图<span class="en">DISPATCH&nbsp;MAP</span></h2>
      <div class="legend">
        <span class="lg"><i class="sw early"></i>早班</span>
        <span class="lg"><i class="sw late"></i>晚班</span>
        <span class="lg"><i class="sw duty"></i>值班</span>
        <span class="lg"><i class="sw both"></i>全天</span>
        <span class="lg"><i class="sw off"></i>休息</span>
        <span class="lg"><i class="sw unavail"></i>不可用日</span>
        <span class="lg"><i class="sw over"></i>超次</span>
        <span class="lg"><i class="sw violation"></i>冲突</span>
        <span class="lg"><i class="sw purple"></i>人员</span>
      </div>
    </div>

    <div class="map-body">
      <div v-if="!employees.length" class="map-empty">
        <b>还没有员工</b>
        <span>点右上角「人员」→「新增」，先建好员工和可用日，再排班或生成</span>
      </div>
      <div ref="scrollRef" class="map-scroll">
        <section v-for="w in weeks" :key="w.index" class="map-block">
          <div class="grid" :style="gridStyle()">
            <div class="gcell corner">
              <b>第{{ w.index }}周</b>
              <em>{{ rangeText(w) }}</em>
            </div>
            <div
              v-for="(d, i) in w.cells"
              :key="'h' + i"
              class="gcell gh"
              :class="headClass(d, i)"
              :title="d ? `${S.data.doc.month}月${d}日 周${WEEK_LABEL[i]} · ${dutyText(d)}` : ''"
              @click="onHeadClick(d)"
            >
              <template v-if="d">
                <span class="d">{{ d }}<i v-if="countBad(d)" class="badge">!</i></span>
                <span class="w">{{ WEEK_LABEL[i] }}</span>
                <span class="duty">{{ dutyText(d) }}<template v-if="openText(d)"> · {{ openText(d) }}</template></span>
              </template>
            </div>

            <template v-for="emp in employees" :key="emp.id">
              <div class="gcell gname" :class="nameClass(emp)" :title="`${emp.name} · 点击编辑人员与可用日`" @click="onNameClick(emp)">
                <span class="ename">
                  <i class="sw" :style="{ background: emp.color, boxShadow: '0 0 8px ' + emp.color }"></i>{{ emp.name
                  }}<span v-if="emp.keyShift" class="keymark">◆</span><span v-if="emp.oddOnly" class="parmark">单</span><span v-if="emp.evenOnly" class="parmark">双</span>
                </span>
                <span v-if="showMeta" class="emeta">
                  值 {{ statOf(emp.id).duty }}/{{ statOf(emp.id).target }} · 早{{ statOf(emp.id).early }} 晚{{ statOf(emp.id).late }}
                </span>
              </div>
              <div
                v-for="(d, i) in w.cells"
                :key="'c' + i"
                class="gcell cell"
                :class="cellClass(emp, d)"
                :title="d ? cellTitle(emp, d) : ''"
                @click="onCellClick(emp, d)"
              >
                <template v-if="d">
                  <span class="glyph">{{ STATUS_GLYPH[cellOf(emp.id, d).s] || "" }}</span>
                  <span v-if="cellOf(emp.id, d).opener" class="mark op">门</span>
                </template>
              </div>
            </template>
          </div>
        </section>
      </div>
    </div>

    <div class="map-foot">
      <span>
        {{ S.sel ? `已选中：${S.data.doc.month}月${S.sel.day}日${S.sel.eid ? " · " + (employees.find(e => e.id === S.sel.eid) || {}).name : ""}` : "点击格子查看当日详情与候选人 · 点击左侧姓名编辑人员" }}
      </span>
      <span class="map-foot-right">
        早 {{ check.coverage.morning }} 天 · 晚 {{ check.coverage.evening }} 天 · 值班 {{ check.coverage.duty }}/{{ check.coverage.days }}
      </span>
    </div>
  </section>
</template>
