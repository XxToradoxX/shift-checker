<script setup>
import { computed } from "vue";
import { S, check, locate } from "../store";

const list = computed(() => {
  const all = check.value.violations;
  if (S.filter === "error") return all.filter((v) => v.severity === "error");
  if (S.filter === "warn") return all.filter((v) => v.severity !== "error");
  return all;
});
const counts = computed(() => ({ all: check.value.violations.length, error: check.value.errors, warn: check.value.warns }));
</script>

<template>
  <section class="panel conflict-panel">
    <div class="panel-head compact">
      <h2><span class="pdot orange"></span>冲突清单<span class="en">ISSUES</span></h2>
      <div class="filter-tabs">
        <button v-for="f in ['all', 'error', 'warn']" :key="f" :class="{ on: S.filter === f }" @click="S.filter = f">
          {{ { all: "全部", error: "冲突", warn: "提示" }[f] }} {{ counts[f] }}
        </button>
      </div>
    </div>
    <div class="conflict-body">
      <div v-if="check.empty" class="no-issue" style="color: var(--muted)">
        <span class="big" style="color: var(--ice)">◌</span>本月还没有排班<br />
        <span style="font-size: 10.5px; color: var(--dim)">点击「生成排班」，或直接在格子上手动排班</span>
      </div>
      <div v-else-if="!list.length" class="no-issue">
        <span class="big">✓</span>{{ counts.all ? "当前筛选下没有条目" : "未检测到冲突，排班符合全部量化规则" }}
      </div>
      <button
        v-for="v in list"
        :key="v.id"
        class="v-item"
        :class="{ warn: v.severity !== 'error', sel: S.focusV === v.id }"
        @click="locate(v)"
      >
        <span class="v-code">{{ v.code }}</span>
        <span class="v-main">
          <b>{{ v.day ? `${S.data.doc.month}月${v.day}日` : "全月" }}</b> {{ v.title }}
          <span class="v-msg">{{ v.message }}</span>
        </span>
      </button>
    </div>
  </section>
</template>
