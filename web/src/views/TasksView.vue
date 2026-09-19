<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { NButton, NCard, NDataTable, NFormItem, NInput, NInputNumber, NSpace, NSwitch, useMessage } from 'naive-ui'
import { api, type TaskInfo } from '@/api'

const message = useMessage()
const tasks = ref<TaskInfo[]>([])
const roots = ref('')
const limit = ref(0)
const skipKnown = ref(true)
const writeMetadata = ref(false)
const confirm = ref(false)
let timer: number | undefined

async function refresh() {
  try {
    tasks.value = await api.tasks()
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

async function start() {
  if (!confirm.value) {
    message.warning('请先打开「我确认」开关，避免误触')
    return
  }
  try {
    const payload: Record<string, unknown> = {
      recursive: true,
      limit: limit.value,
      skip_known: skipKnown.value,
      write_metadata: writeMetadata.value,
      confirm: true,
    }
    if (roots.value.trim()) {
      payload.roots = roots.value.split(',').map((s) => s.trim()).filter(Boolean)
    }
    const result = await api.startScan(payload)
    message.success(`已提交任务 ${result.task_id}`)
    await refresh()
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

const columns = [
  { title: 'ID', key: 'id', width: 100 },
  { title: '类型', key: 'kind', width: 140 },
  { title: '状态', key: 'state', width: 100 },
  { title: '进度', key: 'progress', width: 160, render: (row: TaskInfo) =>
      row.total ? `${row.current}/${row.total}` : '-' },
  { title: '当前', key: 'message', ellipsis: { tooltip: true } },
  { title: '错误', key: 'error', ellipsis: { tooltip: true } },
]

onMounted(() => {
  refresh()
  timer = window.setInterval(refresh, 3000)
})
onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <n-card title="新建扫描任务" style="margin-bottom: 16px">
    <n-space vertical>
      <n-form-item label="扫描根目录（留空 = 使用允许根目录，多个用逗号分隔）">
        <n-input v-model:value="roots" placeholder="D:/media/test" />
      </n-form-item>
      <n-form-item label="最多处理文件数（0 = 不限）">
        <n-input-number v-model:value="limit" :min="0" />
      </n-form-item>
      <n-form-item label="跳过已成功的">
        <n-space align="center">
          <n-switch v-model:value="skipKnown" />
          <span style="color: #888">
            关掉则连「未命中 / 待确认 / 失败」的记录也重新刮一次（成功过的不受影响）
          </span>
        </n-space>
      </n-form-item>
      <n-form-item label="写入 NFO 与图片">
        <n-space align="center">
          <n-switch v-model:value="writeMetadata" />
          <span style="color: #888">
            关掉时<strong>只写数据库</strong>，磁盘上不会有任何变化 —— 记录页会显示成功，
            但 Emby 什么都看不到。还需要设置里 dry_run 关闭且 organize_enabled 打开。
          </span>
        </n-space>
      </n-form-item>
      <n-space align="center">
        <n-switch v-model:value="confirm" />
        <span>我确认在允许根目录内执行（不会移动文件；是否写 NFO/图片见上面两个开关）</span>
        <n-button type="primary" @click="start">开始扫描</n-button>
        <n-button @click="refresh">刷新</n-button>
      </n-space>
    </n-space>
  </n-card>

  <n-card title="任务列表（每 3 秒自动刷新）">
    <n-data-table :columns="columns" :data="tasks" :bordered="false" size="small" />
  </n-card>
</template>
