<script setup>
import { computed, reactive, watch } from "vue";
import { S, doc, employees, saveEmployee, deleteEmployee, moveEmployee, save, toast } from "../store";

const sel = computed(() => {
  if (!S.peopleSel && employees.value.length) S.peopleSel = employees.value[0].id;
  return employees.value.find((e) => e.id === S.peopleSel) || null;
});

const form = reactive({
  name: "", color: "#a875ff", role: "", keyShift: false, dutyTarget: 0, dutyMax: 0,
  monthlyRest: "", preferShift: null, minEarly: "", maxEarly: "", minLate: "", maxLate: "",
  alternate: false, shiftCycle: false, oddOnly: false, evenOnly: false, active: true, note: "",
});

const isNew = computed(() => form._new === true);

function fill(e) {
  if (!e) return;
  form._new = false;
  form.name = e.name;
  form.color = e.color || "#a875ff";
  form.role = e.role || "";
  form.keyShift = !!e.keyShift;
  form.dutyTarget = e.dutyTarget || 0;
  form.dutyMax = e.dutyMax || 0;
  form.monthlyRest = e.monthlyRest == null ? "" : e.monthlyRest;
  form.preferShift = e.preferShift || null;
  form.minEarly = e.minEarly == null ? "" : e.minEarly;
  form.maxEarly = e.maxEarly == null ? "" : e.maxEarly;
  form.minLate = e.minLate == null ? "" : e.minLate;
  form.maxLate = e.maxLate == null ? "" : e.maxLate;
  form.alternate = !!e.alternate;
  form.shiftCycle = !!e.shiftCycle;
  form.oddOnly = !!e.oddOnly;
  form.evenOnly = !!e.evenOnly;
  form.active = e.active !== false;
  form.note = e.note || "";
}
watch(sel, (e) => fill(e), { immediate: true });

const days = computed(() => doc.value.days);
const avail = computed(() => new Set((doc.value.availability || {})[sel.value ? sel.value.id : ""] || []));
function isWeekend(d) {
  const wd = new Date(doc.value.year, doc.value.month - 1, d).getDay();
  return wd === 0 || wd === 6;
}

function pick(id) {
  S.peopleSel = id;
}
function move(eid, delta) {
  moveEmployee(eid, delta);
}
function newPerson() {
  const name = (prompt("新员工姓名（1-6 个字）") || "").trim();
  if (!name) return;
  saveEmployee(null, { name, color: "#a875ff", dutyTarget: 0, dutyMax: 0, keyShift: false }, true).then((ok) => {
    if (ok) S.peopleSel = (employees.value.find((e) => e.name === name) || {}).id || S.peopleSel;
  });
}
async function submit() {
  const data = {
    name: form.name.trim(),
    color: form.color,
    role: form.role.trim(),
    keyShift: form.keyShift,
    dutyTarget: Number(form.dutyTarget) || 0,
    dutyMax: Number(form.dutyMax) || 0,
    monthlyRest: form.monthlyRest === "" ? null : Number(form.monthlyRest),
    minEarly: form.minEarly === "" ? null : Number(form.minEarly),
    maxEarly: form.maxEarly === "" ? null : Number(form.maxEarly),
    minLate: form.minLate === "" ? null : Number(form.minLate),
    maxLate: form.maxLate === "" ? null : Number(form.maxLate),
    preferShift: form.preferShift || null,
    alternate: form.alternate,
    oddOnly: form.oddOnly,
    evenOnly: form.evenOnly,
    shiftCycle: form.shiftCycle ? "早晚" : null,
    active: form.active,
    note: form.note.trim(),
  };
  if (!data.name) return toast("请填写姓名", "warn");
  if (data.dutyMax < data.dutyTarget) data.dutyMax = data.dutyTarget;
  await saveEmployee(sel.value.id, data, false);
}
function setOdd(v) {
  form.oddOnly = v;
  if (v) form.evenOnly = false;
}
function setEven(v) {
  form.evenOnly = v;
  if (v) form.oddOnly = false;
}
function remove() {
  if (!sel.value) return;
  if (!confirm(`确认删除员工「${sel.value.name}」？该员工在所有月份的排班记录都会被移除。`)) return;
  deleteEmployee(sel.value.id);
}
function toggleAvail(day) {
  if (!sel.value) return;
  const set = new Set(avail.value);
  if (set.has(day)) set.delete(day);
  else set.add(day);
  S.data.doc.availability[sel.value.id] = [...set].sort((a, b) => a - b);
  save({ silent: true });
}
function allAvail(on) {
  if (!sel.value) return;
  S.data.doc.availability[sel.value.id] = on ? Array.from({ length: days.value }, (_, i) => i + 1) : [];
  save({ silent: true });
}
</script>

<template>
  <div class="modal open">
    <div class="modal-mask" @click="S.modal = null"></div>
    <div class="modal-card wide">
      <header class="modal-head">
        <h3><span class="pdot purple"></span>人员与可用日<span class="en">CREW</span></h3>
        <button class="modal-x" @click="S.modal = null">✕</button>
      </header>

      <div class="modal-body people-layout">
        <div class="people-col">
          <div class="col-title">
            员工列表
            <button class="mini" @click="newPerson">+ 新增</button>
          </div>
          <div class="people-list">
            <div
              v-for="(e, i) in employees"
              :key="e.id"
              class="people-item"
              :class="{ sel: sel && sel.id === e.id }"
              @click="pick(e.id)"
            >
              <i :style="{ width: '4px', height: '16px', background: e.color, display: 'inline-block' }"></i>
              <span class="pname">{{ e.name }}</span>
              <span class="prole">{{ e.keyShift ? "关键岗 " : "" }}值{{ e.dutyTarget }}/{{ e.dutyMax }}</span>
              <span class="pmove" @click.stop>
                <button :disabled="i === 0" title="上移" @click.stop="move(e.id, -1)">▲</button>
                <button :disabled="i === employees.length - 1" title="下移" @click.stop="move(e.id, 1)">▼</button>
              </span>
            </div>
          </div>
          <div class="people-tip">▲▼ 调整顺序，会同时改变表格里的行顺序</div>
        </div>

        <div class="people-col form-col">
          <div class="col-title">员工属性</div>
          <template v-if="sel">
            <div class="people-form">
              <div class="f-row f-grid">
                <label class="field"><span>姓名</span><input v-model="form.name" type="text" maxlength="6" /></label>
                <label class="field"><span>颜色</span><input v-model="form.color" type="color" /></label>
                <label class="field"><span>岗位</span><input v-model="form.role" type="text" placeholder="如 值班岗" /></label>
                <label class="switch"><input v-model="form.keyShift" type="checkbox" /><i></i>关键岗(规则7)</label>
              </div>
              <div class="f-row f-grid">
                <label class="field"><span>值班目标</span><input v-model.number="form.dutyTarget" type="number" min="0" max="31" /><em>天</em></label>
                <label class="field"><span>值班上限</span><input v-model.number="form.dutyMax" type="number" min="0" max="31" /><em>天</em></label>
                <label class="field"><span>全月休息</span><input v-model="form.monthlyRest" type="number" min="0" max="31" placeholder="—" /><em>天</em></label>
              </div>
              <div class="f-row f-grid">
                <label class="field"><span>早班最少</span><input v-model="form.minEarly" type="number" min="0" max="31" placeholder="—" /><em>天</em></label>
                <label class="field"><span>早班最多</span><input v-model="form.maxEarly" type="number" min="0" max="31" placeholder="—" /><em>天</em></label>
                <label class="field"><span>晚班最少</span><input v-model="form.minLate" type="number" min="0" max="31" placeholder="—" /><em>天</em></label>
                <label class="field"><span>晚班最多</span><input v-model="form.maxLate" type="number" min="0" max="31" placeholder="—" /><em>天</em></label>
              </div>
              <div class="f-row" style="margin-top: -4px">
                <span style="font-size: 10px; color: var(--dim)">「最少/最多」留空表示不限制；晚班次数含值班日（规则 16）</span>
              </div>
              <div class="f-row">
                <label class="field"><span>偏好班次</span>
                  <select v-model="form.preferShift">
                    <option :value="null">无偏好</option>
                    <option value="early">多排早班</option>
                    <option value="late">多排晚班</option>
                  </select>
                </label>
                <span style="font-size: 10px; color: var(--dim)">影响生成时的早晚比例</span>
              </div>
              <div class="f-row">
                <label class="switch"><input v-model="form.alternate" type="checkbox" /><i></i>上一休一(规则4)</label>
                <label class="switch"><input v-model="form.shiftCycle" type="checkbox" /><i></i>早晚轮换(规则8)</label>
                <label class="switch"><input v-model="form.active" type="checkbox" /><i></i>参与排班</label>
                <label class="switch"><input type="checkbox" :checked="form.oddOnly" @change="setOdd($event.target.checked)" /><i></i>单号工作(规则19)</label>
                <label class="switch"><input type="checkbox" :checked="form.evenOnly" @change="setEven($event.target.checked)" /><i></i>双号工作(规则20)</label>
              </div>
              <div class="form-sep"></div>
              <div class="f-row" style="margin-bottom: 2px">
                <span class="f-label">可用日</span>
                <span style="font-size: 10.5px; color: var(--dim)">点击切换 · 深色为不可用日</span>
                <button class="mini" @click="allAvail(true)">全可用</button>
                <button class="mini" @click="allAvail(false)">全不可用</button>
              </div>
              <div class="avail-grid">
                <div
                  v-for="d in days"
                  :key="d"
                  class="avail-day"
                  :class="{ off: !avail.has(d), we: isWeekend(d) }"
                  :title="`${doc.month}月${d}日`"
                  @click="toggleAvail(d)"
                >
                  {{ d }}
                </div>
              </div>
              <div class="form-sep"></div>
              <div class="f-row">
                <span class="f-label">备注</span>
                <input v-model="form.note" type="text" style="flex: 1 1 160px; width: auto" />
              </div>
              <div class="f-row" style="margin-top: 10px">
                <button class="btn btn-primary" @click="submit">保存员工</button>
                <button class="btn btn-danger" @click="remove">删除员工</button>
              </div>
            </div>
          </template>
          <div v-else class="empty-state">选择左侧员工进行编辑<br />或点击「+ 新增」添加员工</div>
        </div>
      </div>

      <footer class="modal-foot">
        <span class="hint">可用日：绿色=可排班，暗色=不可用日（规则11）。</span>
        <button class="btn" @click="S.modal = null">关闭</button>
      </footer>
    </div>
  </div>
</template>
