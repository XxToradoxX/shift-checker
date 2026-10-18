<script setup>
import { S } from "../store";
</script>

<template>
  <div class="toast-wrap">
    <div v-for="t in S.toasts" :key="t.id" class="toast" :class="t.kind" v-html="t.html"></div>
  </div>
</template>
