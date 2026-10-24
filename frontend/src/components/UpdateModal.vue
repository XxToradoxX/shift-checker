<script setup>
import { computed, onUnmounted, watch } from "vue";
import { S, closeUpdate, checkUpdate, startUpdate, applyUpdate, pollUpdate } from "../store";

let timer = null;

function fmtSize(n) {
  if (!n) return "—";
  const mb = n / 1024 / 1024;
  return mb >= 1024 ? (mb / 1024).toFixed(2) + " GB" : mb.toFixed(1) + " MB";
}
function fmtSpeed(n) {
  if (!n) return "—";
  const mb = n / 1024 / 1024;
  return mb >= 1 ? mb.toFixed(2) + " MB/s" : (n / 1024).toFixed(0) + " KB/s";
}
const percent = computed(() => {
  const p = S.upd.progress || {};
  if (!p.total) return 0;
  return Math.min(100, Math.round((p.downloaded / p.total) * 100));
});
const version = computed(() => (S.upd.info && S.upd.info.latest) || "");

watch(
  () => S.upd.status,
  (st) => {
    if (st === "downloading" && !timer) {
      timer = setInterval(pollUpdate, 600);
    } else if (st !== "downloading" && timer) {
      clearInterval(timer);
      timer = null;
    }
  },
);
onUnmounted(() => {
  if (timer) clearInterval(timer);
  timer = null;
});
</script>

<template>
  <div class="modal open">
    <div class="modal-mask" @click="closeUpdate"></div>
    <div class="modal-card small upd-card">
      <header class="modal-head">
        <h3><span class="pdot teal"></span>检查更新</h3>
        <button class="modal-x" @click="closeUpdate">✕</button>
      </header>

      <div class="modal-body">
        <!-- 检查中 -->
        <div v-if="S.upd.status === 'checking'" class="upd-center">
          <span class="spinner"></span> 正在检查 GitHub 上的最新版本…
        </div>

        <!-- 已是最新 -->
        <div v-else-if="S.upd.status === 'latest'" class="upd-center upd-ok">
          <b>已是最新版本</b>
          <span>当前版本 v{{ S.upd.info.current }}</span>
        </div>

        <!-- 有新版本 -->
        <div v-else-if="S.upd.status === 'available'" class="upd-info">
          <div class="upd-row">
            <span>当前版本</span><b>v{{ S.upd.info.current }}</b>
          </div>
          <div class="upd-row">
            <span>最新版本</span><b class="hl">v{{ version }}</b>
          </div>
          <div class="upd-row">
            <span>安装包</span><b>{{ fmtSize(S.upd.info.size) }}</b>
          </div>
          <div v-if="S.upd.info.notes" class="upd-notes">{{ S.upd.info.notes }}</div>
          <p v-if="!S.upd.info.frozen" class="upd-warn">
            当前是源码运行模式，下载后请手动用 git 更新；exe 版本可一键覆盖。
          </p>
        </div>

        <!-- 下载中 -->
        <div v-else-if="S.upd.status === 'downloading'" class="upd-progress">
          <div class="upd-row">
            <span>正在下载 v{{ version }}</span>
            <b>{{ percent }}%</b>
          </div>
          <div class="pbar"><i :style="{ width: percent + '%' }"></i></div>
          <div class="upd-row sub">
            <span>{{ fmtSize(S.upd.progress.downloaded) }} / {{ fmtSize(S.upd.progress.total) }}</span>
            <span>{{ fmtSpeed(S.upd.progress.speed) }}</span>
          </div>
        </div>

        <!-- 下载完成 -->
        <div v-else-if="S.upd.status === 'ready'" class="upd-center upd-ok">
          <b>下载完成</b>
          <span>点击「立即安装」，程序会自动替换文件并重启</span>
        </div>

        <!-- 安装中 -->
        <div v-else-if="S.upd.status === 'applying'" class="upd-center">
          <span class="spinner"></span> 正在替换文件并重启，完成后页面会自动刷新…
        </div>

        <div v-else-if="S.upd.status === 'offline'" class="upd-center upd-err">
          <b>服务没有在预期时间内恢复</b>
          <span>请手动双击 ShiftChecker.exe 重新启动</span>
        </div>

        <!-- 出错 -->
        <div v-else-if="S.upd.status === 'error'" class="upd-center upd-err">
          <b>更新失败</b>
          <span class="upd-errmsg">{{ S.upd.error }}</span>
        </div>
      </div>

      <footer class="modal-foot">
        <span class="hint">
          <template v-if="S.upd.info && S.upd.info.repo">来源：{{ S.upd.info.repo }}</template>
        </span>
        <button v-if="S.upd.status === 'latest' || S.upd.status === 'error'" class="btn" @click="checkUpdate">
          重新检查
        </button>
        <button v-if="S.upd.status === 'available'" class="btn btn-primary" @click="startUpdate">
          立即更新
        </button>
        <button v-if="S.upd.status === 'ready'" class="btn btn-primary" @click="applyUpdate">
          立即安装并重启
        </button>
        <button class="btn" @click="closeUpdate">
          {{ S.upd.status === "downloading" ? "后台下载" : "关闭" }}
        </button>
      </footer>
    </div>
  </div>
</template>
