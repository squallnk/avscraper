<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NForm,
  NFormItem,
  NImage,
  NInput,
  NModal,
  NSelect,
  NSpace,
  NSwitch,
  NTag,
  useMessage,
} from 'naive-ui'
import { api, type MediaMetadataInfo, type RecordInfo, type SourceInfo } from '@/api'

const message = useMessage()
const records = ref<RecordInfo[]>([])
const sources = ref<SourceInfo[]>([])
const status = ref<string | null>(null)
const writesEnabled = ref(false)

/** 状态 -> 中文标签。need_selection 单独列一档，因为它需要人动手。 */
const STATUS_LABELS: Record<string, string> = {
  pending: '排队中',
  running: '进行中',
  success: '成功',
  not_found: '未命中',
  failed: '失败',
  skipped: '跳过',
  need_selection: '待确认',
}

const STATUS_TYPES: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  success: 'success',
  not_found: 'warning',
  failed: 'error',
  skipped: 'default',
  need_selection: 'warning',
}

const statusOptions = [
  { label: '全部', value: null },
  { label: '待确认', value: 'need_selection' },
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

// ------------------------------------------------------------------ 重刮

const showRescan = ref(false)
const target = ref<RecordInfo | null>(null)
const query = ref('')
const source = ref<string | null>(null)
const force = ref(false)
const busy = ref(false)
const preview = ref<RecordInfo | null>(null)

const sourceOptions = computed(() => [
  { label: '按正常路由（全部相关源）', value: null },
  ...sources.value.filter((s) => s.active).map((s) => ({ label: s.name, value: s.id })),
])

function openRescan(row: RecordInfo) {
  target.value = row
  // 默认沿用后端清洗出来的词，注意这里不从标题倒推，避免引入第二套清洗规则
  query.value = row.number ?? ''
  source.value = null
  force.value = false
  preview.value = null
  showRescan.value = true
}

/** 查询词的默认值要跟后端清洗规则一致 —— 由后端给，不由前端猜。 */
async function defaultQuery(row: RecordInfo): Promise<string> {
  try {
    const info = await api.classify(row.path)
    if (info.number) return info.number
  } catch {
    /* 解析失败就留空，让用户自己填 */
  }
  return ''
}

async function runRescan(write: boolean) {
  if (!target.value) return
  busy.value = true
  try {
    const result = await api.rescan(target.value.id, {
      query: query.value.trim() || undefined,
      source: source.value ?? undefined,
      force: force.value,
      write,
      confirm: write,
    })
    preview.value = result.record
    if (write) {
      message.success(
        result.written.length
          ? `已写入 ${result.written.length} 个文件`
          : '已记录，但没有文件被写入（写入未开启或结果不是 success）',
      )
      await load()
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    busy.value = false
  }
}

const columns = [
  { title: '番号', key: 'number', width: 130, render: (row: RecordInfo) => row.number ?? '-' },
  {
    title: '类型',
    key: 'content_type',
    width: 100,
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
  {
    title: '状态',
    key: 'status',
    width: 110,
    render: (row: RecordInfo) =>
      h(
        NTag,
        { type: STATUS_TYPES[row.status] ?? 'default', size: 'small' },
        { default: () => STATUS_LABELS[row.status] ?? row.status },
      ),
  },
  { title: '标题', key: 'title', width: 220, ellipsis: { tooltip: true },
    render: (row: RecordInfo) => row.metadata?.title ?? '-' },
  { title: '路径', key: 'path', ellipsis: { tooltip: true } },
  {
    title: '字段来源',
    key: 'field_sources',
    width: 180,
    render: (row: RecordInfo) =>
      Object.entries(row.field_sources ?? {}).map(([k, v]) => `${k}←${v}`).join(' '),
  },
  { title: '错误', key: 'error', ellipsis: { tooltip: true } },
  {
    title: '操作',
    key: 'actions',
    width: 90,
    fixed: 'right' as const,
    render: (row: RecordInfo) =>
      h(
        NButton,
        {
          size: 'small',
          type: row.status === 'need_selection' ? 'warning' : 'default',
          onClick: async () => {
            openRescan(row)
            query.value = (await defaultQuery(row)) || row.number || ''
          },
        },
        { default: () => '重刮' },
      ),
  },
]

const previewMetadata = computed<MediaMetadataInfo | null>(() => preview.value?.metadata ?? null)

onMounted(async () => {
  await load()
  try {
    sources.value = await api.sources()
    writesEnabled.value = (await api.health()).writes_enabled
  } catch {
    /* 源列表拿不到不影响看记录 */
  }
})
</script>

<template>
  <n-card title="刮削记录">
    <n-space style="margin-bottom: 12px">
      <n-select v-model:value="status" :options="statusOptions" style="width: 160px" @update:value="load" />
      <n-button @click="load">刷新</n-button>
    </n-space>
    <n-data-table :columns="columns" :data="records" :bordered="false" size="small" :scroll-x="1500" />

    <n-modal v-model:show="showRescan" preset="card" style="width: 720px" title="重刮">
      <n-alert v-if="target" type="warning" :bordered="false" style="margin-bottom: 12px">
        {{ target.path }}
        <div v-if="target.error" style="margin-top: 4px">{{ target.error }}</div>
      </n-alert>
      <n-form label-placement="left" label-width="100">
        <n-form-item label="查询词">
          <n-input v-model:value="query" placeholder="留空则用后端清洗出来的名字" />
        </n-form-item>
        <n-form-item label="数据源">
          <n-select v-model:value="source" :options="sourceOptions" />
        </n-form-item>
        <n-form-item label="跳过校验">
          <n-space align="center">
            <n-switch v-model:value="force" />
            <span style="color: #888">抓到的标题对不上查询词时也采用（看着下面的预览结果再决定）</span>
          </n-space>
        </n-form-item>
      </n-form>

      <n-alert v-if="preview" :type="preview.status === 'success' ? 'success' : 'warning'" :bordered="false">
        <div>状态：{{ STATUS_LABELS[preview.status] ?? preview.status }}</div>
        <div v-if="preview.error">说明：{{ preview.error }}</div>
      </n-alert>

      <div v-if="previewMetadata" style="display: flex; gap: 12px; margin-top: 12px">
        <n-image
          v-if="previewMetadata.poster_url"
          :src="previewMetadata.poster_url"
          width="130"
          object-fit="cover"
        />
        <div style="flex: 1; min-width: 0">
          <div><b>{{ previewMetadata.title || '(无标题)' }}</b></div>
          <div v-if="previewMetadata.original_title" style="color: #888">{{ previewMetadata.original_title }}</div>
          <div v-if="previewMetadata.studio">厂牌：{{ previewMetadata.studio }}</div>
          <div v-if="previewMetadata.release_date">发售：{{ previewMetadata.release_date }}</div>
          <div v-if="previewMetadata.actors?.length">演员：{{ previewMetadata.actors.join(' / ') }}</div>
          <div v-if="previewMetadata.tags?.length">标签：{{ previewMetadata.tags.join(' / ') }}</div>
          <div v-if="previewMetadata.plot" style="margin-top: 6px">{{ previewMetadata.plot }}</div>
        </div>
      </div>

      <template #footer>
        <n-space justify="end">
          <n-button :loading="busy" @click="runRescan(false)">预览</n-button>
          <n-button
            type="primary"
            :loading="busy"
            :disabled="!preview || preview.status !== 'success' || !writesEnabled"
            @click="runRescan(true)"
          >
            预览并写入
          </n-button>
        </n-space>
        <div v-if="!writesEnabled" style="color: #888; text-align: right; margin-top: 6px">
          写入未开启（dry_run=true 或 organize_enabled=false），只能预览
        </div>
      </template>
    </n-modal>
  </n-card>
</template>
