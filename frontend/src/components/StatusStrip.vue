<script setup>
import { computed } from "vue";
import { S } from "../store";

const c = computed(() => S.data.check);
const d = computed(() => S.data.doc);
const scheduled = computed(
  () =>
    Object.values(d.value.schedule).filter(
      (x) => (x.morning || []).length || (x.evening || []).length || (x.duty || []).length
    ).length
);
const savedText = computed(() => {
  if (S.savedState === "saving") return "保存中…";
  if (S.savedState === "err") return "保存失败";
  return S.savedAt ? "已保存 " + S.savedAt : "未保存";
});
</script>

<template>
  <div class="statusstrip">
    <span class="chip">周期 <b>{{ d.year }}年{{ d.month }}月</b> · {{ d.days }}天</span>
    <span class="sep"></span>
    <span class="chip">员工 <b>{{ S.data.employees.length }}</b> 人</span>
    <span class="chip">已排 <b>{{ scheduled }}</b>/{{ d.days }} 天</span>
    <span class="sep"></span>
    <span class="chip" :class="{ err: c.errors > 0 }">冲突 <b>{{ c.errors }}</b></span>
    <span class="chip" :class="{ warn: c.warns > 0 }">提示 <b>{{ c.warns }}</b></span>
    <span class="sep"></span>
    <span class="chip">值班覆盖 <b>{{ c.coverage.duty }}</b>/{{ c.coverage.days }}</span>
    <span class="chip">开门覆盖 <b>{{ c.coverage.opener }}</b>/{{ c.coverage.days }}</span>
    <span class="flex"></span>
    <span class="live"><i></i>实时检测中</span>
    <span class="chip muted">{{ savedText }}</span>
  </div>
</template>
