<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { NCard, NDataTable, NSpace, NTag, useMessage } from 'naive-ui'
import { api, type SourceInfo } from '@/api'

const message = useMessage()
const sources = ref<SourceInfo[]>([])
const cooldowns = ref<Record<string, unknown>>({})

async function load() {
  try {
    sources.value = await api.sources()
    cooldowns.value = (await api.sourceHealth()).cooldowns ?? {}
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

const columns = [
  { title: 'ID', key: 'id', width: 120 },
  { title: '名称', key: 'name', width: 140 },
  { title: '支持类型', key: 'supports',
    render: (row: SourceInfo) => row.supports.join(' / ') || '-' },
  { title: '需要代理', key: 'needs_proxy', width: 100,
    render: (row: SourceInfo) => (row.needs_proxy ? '是' : '否') },
  { title: '启用', key: 'active', width: 90,
    render: (row: SourceInfo) => h(NTag, { type: row.active ? 'success' : 'default', size: 'small' },
      { default: () => (row.active ? '启用' : '停用') }) },
  { title: '说明', key: 'note', ellipsis: { tooltip: true } },
]

onMounted(load)
</script>

<template>
  <n-card title="数据源">
    <n-space vertical>
      <n-data-table :columns="columns" :data="sources" :bordered="false" size="small" :scroll-x="1000" />
      <n-card title="熔断状态" size="small">
        <pre style="margin: 0">{{ JSON.stringify(cooldowns, null, 2) }}</pre>
      </n-card>
    </n-space>
  </n-card>
</template>
