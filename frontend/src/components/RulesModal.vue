<script setup>
import { computed, reactive, watch } from "vue";
import { S, check, catalog, doc, employees, setRuleOverride, saveExtraRules, empName, toast } from "../store";

const baseRules = computed(() => check.value.rules || []);
const extraTypes = computed(() => catalog.value.extra || []);
const extraRules = computed(() => doc.value.extraRules || []);
const meta = computed(() => extraTypes.value.find((t) => t.type === draft.type));

const draft = reactive({ type: "rest", params: {}, note: "" });
const editing = computed(() => S.editingRule);

function defaultParams(type) {
  const t = extraTypes.value.find((x) => x.type === type);
  const p = {};
  if (t) {
    t.fields.forEach((f) => {
      if (f.type === "day") p[f.key] = f.key === "day" ? 1 : 1;
      else if (f.type === "int") p[f.key] = 2;
      else if (f.type === "person") p[f.key] = employees.value[0] ? employees.value[0].id : "";
      else if (f.type === "shift") p[f.key] = "early";
    });
  }
  return p;
}
watch(
  () => draft.type,
  (t) => {
    if (S.editingRule == null) draft.params = defaultParams(t);
  }
);

function startEdit(idx) {
  const r = extraRules.value[idx];
  S.editingRule = idx;
  draft.type = r.type;
  draft.params = { ...(r.params || {}) };
  draft.note = r.note || "";
}
function cancelEdit() {
  S.editingRule = null;
  draft.params = defaultParams(draft.type);
  draft.note = "";
}
function submit() {
  const params = {};
  meta.value.fields.forEach((f) => {
    const v = draft.params[f.key];
    if (v === undefined || v === "" || v === null) return;
    params[f.key] = f.type === "int" || f.type === "day" ? Number(v) : v;
  });
  const list = JSON.parse(JSON.stringify(extraRules.value));
  if (editing.value != null) {
    list[editing.value] = { ...list[editing.value], type: draft.type, params, note: draft.note };
  } else {
    list.push({
      id: "x_" + Math.random().toString(36).slice(2, 8),
      type: draft.type,
      params,
      note: draft.note,
      enabled: true,
      severity: "error",
    });
  }
  saveExtraRules(list).then(() => {
    toast(editing.value != null ? "规则已更新" : "已添加本月特殊规则", "ok", 1600);
    S.editingRule = null;
    draft.params = defaultParams(draft.type);
    draft.note = "";
  });
}
function removeRule(idx) {
  const list = JSON.parse(JSON.stringify(extraRules.value));
  list.splice(idx, 1);
  saveExtraRules(list).then(() => toast("规则已删除（可撤销）", "", 1600));
}
function toggleRule(idx) {
  const list = JSON.parse(JSON.stringify(extraRules.value));
  list[idx].enabled = list[idx].enabled === false;
  saveExtraRules(list);
}
function onParam(key, val, type) {
  draft.params = { ...draft.params, [key]: type === "int" ? Number(val) : val };
}
function onPriority(r, ev) {
  let v = Number(ev.target.value);
  if (!Number.isFinite(v)) v = 1;
  v = Math.min(2, Math.max(0.01, Math.round(v * 100) / 100));
  ev.target.value = v.toFixed(2);
  changeBase(r, { priority: v });
}

function changeBase(rule, patch) {
  setRuleOverride(rule.code, patch);
}
function onBaseParam(rule, spec, ev) {
  const el = ev.target;
  const val = spec.type === "bool" ? el.checked : spec.type === "text" ? el.value.trim() : Number(el.value);
  changeBase(rule, { params: { ...rule.params, [spec.key]: val } });
}
function ruleParamsText(r) {
  const p = r.params || {};
  const bits = [];
  const nm = (id) => empName(id);
  if (p.person) bits.push(nm(p.person));
  if (p.a) bits.push(nm(p.a));
  if (p.b) bits.push(nm(p.b));
  if (p.day) bits.push(`${doc.value.month}月${p.day}日`);
  if (p.from) bits.push(`${doc.value.month}月${p.from}日起`);
  if (p.to) bits.push(`至${p.to}日`);
  if (p.shift) bits.push(p.shift === "late" ? "晚班" : "早班");
  if (p.count != null) bits.push(`${p.count}`);
  return bits.filter(Boolean).join(" · ");
}
const typeMeta = (type) => extraTypes.value.find((t) => t.type === type) || { name: type, desc: "" };
</script>

<template>
  <div class="modal open">
    <div class="modal-mask" @click="S.modal = null"></div>
    <div class="modal-card wide">
      <header class="modal-head">
        <h3><span class="pdot ice"></span>规则详情 · 量化约束<span class="en">RULE&nbsp;SET</span></h3>
        <button class="modal-x" @click="S.modal = null">✕</button>
      </header>

      <div class="modal-body rules-layout">
        <div class="rules-col">
          <div class="col-title">基础规则（每月通用）<span>（{{ baseRules.length }} 条）</span></div>
          <div class="rule-list">
            <div
              v-for="r in baseRules"
              :key="r.code"
              class="rule-item"
              :class="{ disabled: !r.enabled, warnsev: r.severity !== 'error' }"
            >
              <div class="rule-top">
                <span class="rule-code">{{ r.code }}</span>
                <span class="rule-name">{{ r.name }}</span>
                <label class="switch">
                  <input type="checkbox" :checked="r.enabled" @change="changeBase(r, { enabled: $event.target.checked })" /><i></i>启用
                </label>
                <select class="sev-select" :value="r.severity" @change="changeBase(r, { severity: $event.target.value })">
                  <option value="error">冲突</option>
                  <option value="warn">提示</option>
                </select>
                <label class="prio-box" title="约束优先级 0.01~2.00：越大越优先满足">
                  <span>优先级</span>
                  <input
                    type="number"
                    step="0.01"
                    min="0.01"
                    max="2"
                    :value="Number(r.priority ?? 1).toFixed(2)"
                    @change="onPriority(r, $event)"
                  />
                </label>
              </div>
              <div class="rule-desc">{{ r.desc }}<span v-if="Number(r.priority ?? 1) !== 1" class="prio-tag">权重 ×{{ Number(r.priority).toFixed(2) }}</span></div>
              <div class="rule-quant">{{ r.quant }}</div>
              <label class="rule-action" title="冲突清单里显示的处理建议，可自行修改">
                <span>处理建议</span>
                <input
                  type="text"
                  :value="r.action || ''"
                  placeholder="留空则用规则内置建议"
                  @change="changeBase(r, { action: $event.target.value })"
                />
              </label>
              <div v-if="r.paramsSpec.length" class="rule-params">
                <label v-for="p in r.paramsSpec" :key="p.key">
                  <template v-if="p.type === 'bool'">
                    <input type="checkbox" :checked="!!r.params[p.key]" @change="onBaseParam(r, p, $event)" />{{ p.label }}
                  </template>
                  <template v-else>
                    {{ p.label }}
                    <input
                      :type="p.type === 'text' ? 'text' : 'number'"
                      :value="r.params[p.key]"
                      :min="p.min"
                      :max="p.max"
                      @change="onBaseParam(r, p, $event)"
                    />
                  </template>
                </label>
              </div>
            </div>
          </div>
        </div>

        <div class="rules-col">
          <div class="col-title">本月特殊规则<span>（{{ extraRules.length }} 条）</span></div>

          <div class="extra-form">
            <div class="form-row">
              <label>类型</label>
              <select v-model="draft.type">
                <option v-for="t in extraTypes" :key="t.type" :value="t.type">{{ t.name }}</option>
              </select>
              <span style="font-size: 10px; color: var(--dim); flex: 1 1 100%">{{ meta ? meta.desc : "" }}</span>
            </div>
            <div v-if="meta" class="form-row">
              <label v-for="f in meta.fields" :key="f.key">
                {{ f.label }}
                <select v-if="f.type === 'person'" :value="draft.params[f.key]" @change="onParam(f.key, $event.target.value)">
                  <option v-for="e in employees" :key="e.id" :value="e.id">{{ e.name }}</option>
                </select>
                <select v-else-if="f.type === 'shift'" :value="draft.params[f.key]" @change="onParam(f.key, $event.target.value)">
                  <option v-if="f.optional" value="">任意</option>
                  <option value="early">早班</option>
                  <option value="late">晚班</option>
                </select>
                <select v-else-if="f.type === 'day'" :value="draft.params[f.key]" @change="onParam(f.key, $event.target.value)">
                  <option v-for="d in doc.days" :key="d" :value="d">{{ d }}</option>
                </select>
                <input
                  v-else
                  type="number"
                  :value="draft.params[f.key]"
                  :min="f.min"
                  :max="f.max"
                  @input="onParam(f.key, $event.target.value, 'int')"
                />
              </label>
            </div>
            <div v-if="meta" class="form-row">
              <label style="flex: 1 1 100%">
                备注
                <input v-model="draft.note" type="text" placeholder="例如：10月8日 陈 家中有事" style="width: 100%" />
              </label>
            </div>
            <div class="form-row" style="margin-bottom: 0">
              <button class="btn btn-primary" @click="submit">{{ editing != null ? "保存修改" : "添加规则" }}</button>
              <button v-if="editing != null" class="btn" @click="cancelEdit">取消编辑</button>
            </div>
          </div>

          <div class="rule-list extra-list">
            <div v-if="!extraRules.length" class="empty-state">本月还没有特殊规则<br /><b>在上方选择类型后填写参数即可添加</b></div>
            <div v-for="(r, idx) in extraRules" :key="r.id || idx" class="rule-item extra-item" :class="{ disabled: r.enabled === false }">
              <div class="rule-top">
                <span class="rule-code" style="color: var(--orange-2); border-color: rgba(255, 90, 51, 0.35); background: rgba(255, 90, 51, 0.08)">X{{ idx + 1 }}</span>
                <span class="rule-name">{{ typeMeta(r.type).name }}</span>
                <span class="rule-acts">
                  <button @click="toggleRule(idx)">{{ r.enabled === false ? "启用" : "停用" }}</button>
                  <button @click="startEdit(idx)">编辑</button>
                  <button class="del" @click="removeRule(idx)">删除</button>
                </span>
              </div>
              <div class="rule-desc">{{ ruleParamsText(r) }}{{ r.note ? " — " + r.note : "" }}</div>
            </div>
          </div>
        </div>
      </div>

      <footer class="modal-foot">
        <span class="hint">修改即时生效并重新检查；橙色=硬冲突，琥珀=提示。</span>
        <button class="btn" @click="S.modal = null">关闭</button>
      </footer>
    </div>
  </div>
</template>
