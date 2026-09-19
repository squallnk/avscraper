<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { NButton, NCard, NDataTable, NSelect, NSpace, NSwitch, useMessage } from 'naive-ui'
import { api } from '@/api'

const message = useMessage()
const logs = ref<{ level: string; logger: string; message: string; created_at: string }[]>([])
const level = ref<string | null>(null)
const auto = ref(true)
let timer: number | undefined

const levelOptions = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'].map((value) => ({
  label: value,
  value,
}))

async function load() {
  try {
    logs.value = await api.logs({ level: level.value ?? undefined })
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

// 自动刷新：日志是"边跑边看"的东西，手动点刷新总会漏掉刚发生的事。
function restartTimer() {
  if (timer !== undefined) window.clearInterval(timer)
  timer = auto.value ? window.setInterval(load, 3000) : undefined
}

watch(auto, restartTimer)
watch(level, load)

onMounted(async () => {
  await load()
  restartTimer()
})
onUnmounted(() => {
  if (timer !== undefined) window.clearInterval(timer)
})

const columns = [
  { title: '时间', key: 'created_at', width: 200 },
  { title: '级别', key: 'level', width: 90 },
  { title: '模块', key: 'logger', width: 160 },
  { title: '内容', key: 'message', ellipsis: { tooltip: true } },
]
</script>

<template>
  <n-card title="运行日志">
    <n-space style="margin-bottom: 12px" align="center">
      <n-button @click="load">刷新</n-button>
      <n-select
        v-model:value="level"
        :options="levelOptions"
        placeholder="全部级别"
        clearable
        style="width: 150px"
      />
      <n-switch v-model:value="auto" />
      <span style="font-size: 13px; opacity: 0.8">自动刷新（3 秒）</span>
    </n-space>
    <n-data-table :columns="columns" :data="logs" :bordered="false" size="small" />
  </n-card>
</template>
