<script setup>
import { computed } from "vue";
import { S, doc, resetMonth } from "../store";

const hint = computed(() => {
  const src = doc.value.source || "";
  return src && src !== "blank" ? "当前数据来源：" + src : "当前月份为空表";
});
</script>

<template>
  <div class="modal open">
    <div class="modal-mask" @click="S.modal = null"></div>
    <div class="modal-card small">
      <header class="modal-head">
        <h3><span class="pdot orange"></span>重置本月排班</h3>
        <button class="modal-x" @click="S.modal = null">✕</button>
      </header>
      <div class="modal-body">
        <p class="confirm-text">清空后本月所有早/晚/值班与开门人都会被移除，操作可通过「撤销」恢复。</p>
        <div class="confirm-actions">
          <button class="btn btn-danger" @click="S.modal = null; resetMonth('clear')">清空本月所有班次</button>
          <button class="btn" @click="S.modal = null">取消</button>
        </div>
      </div>
      <footer class="modal-foot">
        <span class="hint">{{ hint }}</span>
        <button class="btn" @click="S.modal = null">取消</button>
      </footer>
    </div>
  </div>
</template>
