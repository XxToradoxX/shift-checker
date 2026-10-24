<script setup>
import { computed } from "vue";
import { S, check, doc, employees, statsById, setStatus, toggleShift, setOpener, autoOpener, empName, STATUS_TEXT, STATUS_GLYPH } from "../store";

const d = computed(() => doc.value);
const day = computed(() => (S.sel ? S.sel.day : null));
const dayRow = computed(() => (day.value ? check.value.days[day.value - 1] : null));
const cell = computed(() => (day.value ? d.value.schedule[String(day.value)] : null));
const eid = computed(() => (S.sel ? S.sel.eid : null));
const grid = computed(() => {
  if (!eid.value || !day.value) return null;
  return (check.value.grid[eid.value] || {})[String(day.value)] || { s: "off", v: [] };
});
const emp = computed(() => (eid.value ? employees.value.find((e) => e.id === eid.value) : null));
const st = computed(() => (eid.value ? statsById.value[eid.value] || {} : {}));
const statusText = computed(() => {
  const g = grid.value;
  if (!g) return "";
  if (g.over) return `${STATUS_GLYPH[g.shift] || ""}-超次（超出次数上限）`;
  return STATUS_TEXT[g.s] || g.s;
});
// 超次时按钮要识别「真实班次」，否则早/晚班按钮不会高亮
function isCur(kind) {
  const g = grid.value;
  if (!g) return false;
  return g.s === kind || (g.over && g.shift === kind);
}
const vios = computed(() => {
  if (!day.value) return [];
  return check.value.violations.filter(
    (v) => v.day === day.value && (!eid.value || !v.cells.length || v.cells.some((c) => c[0] === eid.value))
  );
});

function memberIn(id, kind) {
  const c = cell.value;
  if (!c) return false;
  return (kind === "early" ? c.morning : kind === "late" ? c.evening : c.duty).includes(id);
}
function tagOf(empId) {
  const g = (check.value.grid[empId] || {})[String(day.value)] || { s: "off" };
  if (g.s === "unavailable") return { text: "不可用日", cls: "no" };
  if (g.s === "conflict") return { text: "冲突", cls: "no" };
  if (memberIn(empId, "duty")) return { text: "值班", cls: "ok" };
  if (memberIn(empId, "early")) return { text: "早班", cls: "ok" };
  if (memberIn(empId, "late")) return { text: "晚班", cls: "ok" };
  return { text: "休息", cls: "off" };
}
function disabled(empId) {
  const g = (check.value.grid[empId] || {})[String(day.value)] || { s: "off" };
  return g.s === "unavailable" || g.s === "conflict";
}
</script>

<template>
  <section class="panel inspect-panel">
    <div class="panel-head compact">
      <h2><span class="pdot ice"></span>格子详情 / 候选人<span class="en">INSPECT</span></h2>
      <span class="head-hint">{{ S.sel ? `${d.month}月${S.sel.day}日 · 周${dayRow.weekday}` : "未选中" }}</span>
    </div>
    <div class="inspect-body">
      <div v-if="!S.sel" class="ins-empty">点击网格中的任意格子<br />查看当日排班、冲突与候选人可用情况</div>
      <template v-else>
        <div class="ins-head">
          <span class="ins-date">{{ d.month }}月{{ day }}日<em>周{{ dayRow.weekday }}</em></span>
          <span class="ins-tag">{{ dayRow.conflicts ? dayRow.conflicts + " 项冲突" : "当日无冲突" }}</span>
        </div>

        <template v-if="emp">
          <div class="ins-sub">
            {{ emp.name }} · 当前状态：{{ statusText }}<span v-if="emp.keyShift"> · 关键岗</span><span v-if="emp.alternate"> · 上一休一（上班日早晚都上）</span>
          </div>
          <div class="ins-actions">
            <button :class="{ cur: isCur('early') }" @click="setStatus(eid, day, 'early')">早班</button>
            <button :class="{ cur: isCur('late') }" @click="setStatus(eid, day, 'late')">晚班</button>
            <button v-if="emp.alternate" :class="{ cur: isCur('both') }" @click="setStatus(eid, day, 'both')">全天</button>
            <button :class="{ cur: isCur('duty') }" @click="setStatus(eid, day, 'duty')">值班</button>
            <button :class="{ cur: isCur('off') }" @click="setStatus(eid, day, 'off')">空班</button>
            <button :class="{ cur: grid.s === 'unavailable' }" @click="setStatus(eid, day, 'unavailable')">不可用日</button>
            <button @click="setStatus(eid, day, 'clear')">移除</button>
          </div>
          <div class="ins-sub" style="color: var(--dim); font-size: 10.5px">
            本月值班 {{ st.duty || 0 }}/{{ st.cap || 0 }} · 早 {{ st.early || 0 }} · 晚 {{ st.late || 0 }} · 休 {{ st.rest || 0 }}
          </div>
        </template>

        <div v-for="v in vios.slice(0, 3)" :key="v.id" class="ins-vio" :class="{ warn: v.severity !== 'error' }">
          <b>{{ v.code }} {{ v.title }}</b>{{ v.message }}
          <span v-if="v.action || v.hint" class="hint">建议：{{ v.action || v.hint }}</span>
        </div>

        <div class="ins-shift-row">
          <span class="lbl">早班</span>
          <div class="chips">
            <span v-for="x in cell.morning" :key="x" class="pchip">{{ empName(x) }}</span>
            <span v-if="!cell.morning.length" class="pchip none">未排</span>
          </div>
        </div>
        <div class="ins-shift-row">
          <span class="lbl">晚班</span>
          <div class="chips">
            <span v-for="x in cell.evening" :key="x" class="pchip">{{ empName(x) }}</span>
            <span v-if="!cell.evening.length" class="pchip none">未排</span>
          </div>
        </div>
        <div class="ins-shift-row">
          <span class="lbl">值班</span>
          <div class="chips">
            <span v-for="x in cell.duty" :key="x" class="pchip teal">{{ empName(x) }}</span>
            <span v-if="!cell.duty.length" class="pchip none">未排</span>
          </div>
        </div>
        <div class="ins-shift-row">
          <span class="lbl">开门</span>
          <div class="chips">
            <button
              v-for="id in cell.morning"
              :key="'op' + id"
              class="op-chip"
              :class="{ active: cell.opener === id }"
              :title="cell.opener === id ? '当前开门人（再点一次取消）' : '设为开门人'"
              @click="setOpener(day, cell.opener === id ? '' : id)"
            >
              {{ empName(id) }}
            </button>
            <span v-if="!cell.morning.length" class="pchip none">早班无人</span>
            <button v-else class="op-auto" title="按轮换规则自动挑选" @click="autoOpener(day)">自动</button>
          </div>
        </div>
        <div class="ins-hint">开门人必须是当天早班成员，点姓名即可切换；不连续重复由规则 12 校验</div>

        <table class="cand-table">
          <thead>
            <tr><th>候选</th><th>可用性</th><th>早</th><th>晚</th><th>值</th></tr>
          </thead>
          <tbody>
            <tr v-for="e in employees" :key="e.id">
              <td :title="`值班 ${(statsById[e.id] || {}).duty || 0}/${(statsById[e.id] || {}).cap || 0}`">{{ e.name }}</td>
              <td><span class="tag" :class="tagOf(e.id).cls">{{ tagOf(e.id).text }}</span></td>
              <td><button class="cand-btn" :class="{ on: memberIn(e.id, 'early') }" :disabled="disabled(e.id)" @click="toggleShift(e.id, day, 'early')">早</button></td>
              <td><button class="cand-btn" :class="{ on: memberIn(e.id, 'late') }" :disabled="disabled(e.id)" @click="toggleShift(e.id, day, 'late')">晚</button></td>
              <td><button class="cand-btn duty" :class="{ on: memberIn(e.id, 'duty') }" :disabled="disabled(e.id)" @click="toggleShift(e.id, day, 'duty')">值</button></td>
            </tr>
          </tbody>
        </table>
      </template>
    </div>
  </section>
</template>
