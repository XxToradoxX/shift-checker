<script setup>
import { computed } from "vue";
import { S, check, doc, statsById } from "../store";

const rows = computed(() => check.value.stats);
const days = computed(() => doc.value.days);

function pct(n) {
  return Math.max(0, Math.min(100, (n / days.value) * 100));
}
function toggle(eid) {
  S.highlightEmp = S.highlightEmp === eid ? null : eid;
}
</script>

<template>
  <section class="panel stats-panel">
    <div class="panel-head compact">
      <h2><span class="pdot teal"></span>人员次数统计<span class="en">LOAD</span></h2>
      <span class="head-hint">{{ rows.length }} 人 · 值/上限</span>
    </div>
    <div class="stats-body">
      <div class="stat-head">
        <span>员工</span><span>早</span><span>晚</span><span>值</span><span>休</span><span>负荷</span>
      </div>
      <div
        v-for="s in rows"
        :key="s.id"
        class="stat-row"
        :class="{ active: S.highlightEmp === s.id }"
        @click="toggle(s.id)"
      >
        <span class="stat-name"><i class="sw" :style="{ background: s.color }"></i>{{ s.name }}<em v-if="s.key">◆</em></span>
        <span class="stat-cell e">{{ s.early }}</span>
        <span class="stat-cell l">{{ s.late }}</span>
        <span class="stat-cell d" :class="{ over: s.over || s.short }">{{ s.duty }}<span class="cap">/{{ s.cap }}</span></span>
        <span class="stat-cell r">{{ s.rest }}</span>
        <span class="stat-bar" :title="`早 ${s.early} / 晚 ${s.late} / 值 ${s.duty} / 休 ${s.rest}`">
          <i class="be" :style="{ width: pct(s.early) + '%' }"></i>
          <i class="bl" :style="{ width: pct(s.late) + '%' }"></i>
          <i class="bd" :style="{ width: pct(s.duty) + '%' }"></i>
        </span>
      </div>
    </div>
  </section>
</template>
