<script setup>
import { onMounted, onUnmounted } from "vue";
import { S, load, undo, select, locate, employees } from "./store";
import TopBar from "./components/TopBar.vue";
import StatusStrip from "./components/StatusStrip.vue";
import MapPanel from "./components/MapPanel.vue";
import StatsPanel from "./components/StatsPanel.vue";
import ConflictList from "./components/ConflictList.vue";
import Inspector from "./components/Inspector.vue";
import RulesModal from "./components/RulesModal.vue";
import PeopleModal from "./components/PeopleModal.vue";
import ConfirmModal from "./components/ConfirmModal.vue";
import Toasts from "./components/Toasts.vue";

function onKey(e) {
  if (e.key === "Escape") {
    if (S.modal) S.modal = null;
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
    if (S.modal) return;
    e.preventDefault();
    undo();
    return;
  }
  const arrows = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"];
  if (arrows.includes(e.key) && S.sel && !S.modal && S.data) {
    e.preventDefault();
    const list = employees.value;
    const days = S.data.doc.days;
    let idx = list.findIndex((x) => x.id === S.sel.eid);
    if (idx < 0) idx = 0;
    let day = S.sel.day;
    if (e.key === "ArrowLeft") day = Math.max(1, day - 1);
    if (e.key === "ArrowRight") day = Math.min(days, day + 1);
    if (e.key === "ArrowUp") idx = Math.max(0, idx - 1);
    if (e.key === "ArrowDown") idx = Math.min(list.length - 1, idx + 1);
    select(list[idx] ? list[idx].id : null, day);
  }
}

function onResize() {
  window.dispatchEvent(new CustomEvent("app:resize"));
}

onMounted(() => {
  document.addEventListener("keydown", onKey);
  window.addEventListener("resize", onResize);
  const app = window.__APP__ || {};
  const q = new URLSearchParams(location.search);
  load(Number(q.get("year")) || app.defaultYear || 2026, Number(q.get("month")) || app.defaultMonth || 10);
  const openWhat = q.get("open");
  const locateWhat = q.get("locate");
  setTimeout(() => {
    if (openWhat === "rules") S.modal = "rules";
    if (openWhat === "people") {
      if (!S.peopleSel && S.data) S.peopleSel = S.data.employees[0].id;
      S.modal = "people";
    }
    if (locateWhat && S.data && S.data.check.violations.length) {
      const v = S.data.check.violations.find((x) => x.id === locateWhat) || S.data.check.violations[0];
      locate(v);
    }
  }, 350);
});
onUnmounted(() => {
  document.removeEventListener("keydown", onKey);
  window.removeEventListener("resize", onResize);
});
</script>

<template>
  <div v-if="S.data" class="app">
    <TopBar />
    <StatusStrip />
    <main class="layout">
      <MapPanel />
      <aside class="side">
        <StatsPanel />
        <ConflictList />
        <Inspector />
      </aside>
    </main>
    <RulesModal v-if="S.modal === 'rules'" />
    <PeopleModal v-if="S.modal === 'people'" />
    <ConfirmModal v-if="S.modal === 'confirm'" />
  </div>
  <div v-else class="boot-screen">
    <div class="boot-inner">
      <span class="boot-ring"></span>
      <p v-if="S.error">加载失败：{{ S.error }}</p>
      <p v-else>正在载入排班数据…</p>
    </div>
  </div>
  <Toasts />
  <div class="busy-bar" :class="{ on: S.busy }"></div>
</template>

<style>
.boot-screen {
  position: fixed; inset: 0; display: grid; place-items: center;
  color: var(--muted); font-family: var(--mono); letter-spacing: 2px;
}
.boot-inner { text-align: center; }
.boot-ring {
  display: inline-block; width: 26px; height: 26px; margin-bottom: 14px;
  border: 1px solid rgba(55, 220, 255, 0.25); border-top-color: var(--ice);
  border-radius: 50%; animation: spin 1s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
