<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NButton, NCard, NDataTable, NSpace, useMessage } from 'naive-ui'
import { api } from '@/api'

const message = useMessage()
const logs = ref<{ level: string; logger: string; message: string; created_at: string }[]>([])

async function load() {
  try {
    logs.value = await api.logs()
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

const columns = [
  { title: '时间', key: 'created_at', width: 200 },
  { title: '级别', key: 'level', width: 90 },
  { title: '模块', key: 'logger', width: 160 },
  { title: '内容', key: 'message', ellipsis: { tooltip: true } },
]

onMounted(load)
</script>

<template>
  <n-card title="运行日志">
    <n-space style="margin-bottom: 12px">
      <n-button @click="load">刷新</n-button>
    </n-space>
    <n-data-table :columns="columns" :data="logs" :bordered="false" size="small" />
  </n-card>
</template>
