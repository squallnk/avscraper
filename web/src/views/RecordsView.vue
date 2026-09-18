<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { NButton, NCard, NDataTable, NSelect, NSpace, NTag, useMessage } from 'naive-ui'
import { api, type RecordInfo } from '@/api'

const message = useMessage()
const records = ref<RecordInfo[]>([])
const status = ref<string | null>(null)

const statusOptions = [
  { label: '全部', value: null },
  { label: '成功', value: 'success' },
  { label: '未命中', value: 'not_found' },
  { label: '失败', value: 'failed' },
  { label: '跳过', value: 'skipped' },
]

const typeColors: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  censored: 'info',
  uncensored: 'warning',
  amateur: 'default',
  fc2: 'default',
  chinese: 'error',
  western: 'error',
  janime: 'success',
  unknown: 'default',
}

async function load() {
  try {
    records.value = await api.records(status.value ?? undefined)
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

const columns = [
  { title: '番号', key: 'number', width: 140, render: (row: RecordInfo) => row.number ?? '-' },
  {
    title: '类型',
    key: 'content_type',
    width: 110,
    render: (row: RecordInfo) =>
      h(NTag, { type: typeColors[row.content_type] ?? 'default', size: 'small' }, { default: () => row.content_type }),
  },
  {
    title: '季集',
    key: 'episode',
    width: 150,
    render: (row: RecordInfo) => {
      if (row.episode === null && row.season === null) return '-'
      const text = `S${(row.season ?? 1).toString().padStart(2, '0')}E${(row.episode ?? 0).toString().padStart(2, '0')}`
      const tag = row.episode_source === '分集兜底' ? 'warning' : 'success'
      return h(
        NTag,
        { type: tag, size: 'small' },
        { default: () => (row.episode_source ? `${text} · ${row.episode_source}` : text) },
      )
    },
  },
  { title: '状态', key: 'status', width: 110 },
  { title: '路径', key: 'path', ellipsis: { tooltip: true } },
  { title: '字段来源', key: 'field_sources', width: 200,
    render: (row: RecordInfo) => Object.entries(row.field_sources ?? {}).map(([k, v]) => `${k}←${v}`).join(' ') },
  { title: '错误', key: 'error', ellipsis: { tooltip: true } },
]

onMounted(load)
</script>

<template>
  <n-card title="刮削记录">
    <n-space style="margin-bottom: 12px">
      <n-select v-model:value="status" :options="statusOptions" style="width: 160px" @update:value="load" />
      <n-button @click="load">刷新</n-button>
    </n-space>
    <n-data-table :columns="columns" :data="records" :bordered="false" size="small" :scroll-x="1100" />
  </n-card>
</template>
