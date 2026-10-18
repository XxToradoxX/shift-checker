<script setup>
import { computed, ref } from "vue";
import { S, load, undo, generate, save, exportCsv, importFile } from "../store";

const fileInput = ref(null);
const baseYear = (window.__APP__ && window.__APP__.defaultYear) || 2026;
const years = computed(() => {
  const list = [];
  for (let y = baseYear - 2; y <= baseYear + 3; y++) list.push(y);
  if (!list.includes(S.year)) list.push(S.year);
  return list.sort((a, b) => a - b);
});
const months = Array.from({ length: 12 }, (_, i) => i + 1);

function onYear(e) {
  load(Number(e.target.value), S.month);
}
function onMonth(e) {
  load(S.year, Number(e.target.value));
}
function pickFile() {
  fileInput.value && fileInput.value.click();
}
function onFile(e) {
  const f = e.target.files && e.target.files[0];
  importFile(f);
  e.target.value = "";
}
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <div class="logo"><span></span></div>
      <div class="titles">
        <h1>员工月排班冲突检查器</h1>
        <p>SHIFT&nbsp;CONFLICT&nbsp;CONSOLE<span class="ver">v1.1</span></p>
      </div>
    </div>

    <div class="month-pick">
      <span class="mp-label">排班周期</span>
      <div class="select-wrap">
        <select :value="S.year" @change="onYear" aria-label="年份">
          <option v-for="y in years" :key="y" :value="y">{{ y }} 年</option>
        </select>
      </div>
      <div class="select-wrap">
        <select :value="S.month" @change="onMonth" aria-label="月份">
          <option v-for="m in months" :key="m" :value="m">{{ m }} 月</option>
        </select>
      </div>
      <span class="month-tag">{{ S.year }}-{{ String(S.month).padStart(2, "0") }}</span>
    </div>

    <nav class="actions">
      <div class="iter-pick" title="退火迭代次数：越大越接近最优，但耗时更久">
        <span>迭代</span>
        <select v-model.number="S.iterations">
          <option :value="60000">6 万</option>
          <option :value="100000">10 万</option>
          <option :value="200000">20 万</option>
          <option :value="500000">50 万</option>
          <option :value="1000000">100 万</option>
        </select>
      </div>
      <button class="btn btn-primary" :disabled="S.busy" @click="generate">
        <i class="ico">◈</i><span>{{ S.busy ? "处理中…" : "生成排班" }}</span>
      </button>
      <button class="btn" @click="save({})"><i class="ico">◎</i><span>检查</span></button>
      <span class="divider"></span>
      <button class="btn" :disabled="!S.undo.length" title="撤销 (Ctrl+Z)" @click="undo">
        <i class="ico">↺</i><span>撤销</span>
      </button>
      <button class="btn" @click="S.modal = 'confirm'"><i class="ico">⨯</i><span>重置</span></button>
      <span class="divider"></span>
      <button class="btn" @click="pickFile"><i class="ico">▲</i><span>导入</span></button>
      <button class="btn" @click="exportCsv"><i class="ico">▼</i><span>导出</span></button>
      <span class="divider"></span>
      <button class="btn" @click="S.modal = 'rules'"><i class="ico">▤</i><span>规则详情</span></button>
      <button class="btn" @click="S.modal = 'people'"><i class="ico">☰</i><span>人员</span></button>
      <button class="btn btn-save" @click="save({})"><i class="ico">⭳</i><span>保存</span></button>
    </nav>
    <input ref="fileInput" type="file" accept=".csv,.xlsx,.xlsm" hidden @change="onFile" />
  </header>
</template>
