<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NAlert, NCard, NGrid, NGridItem, NStatistic, NTag } from 'naive-ui'
import { api, type HealthInfo } from '@/api'

const health = ref<HealthInfo | null>(null)
const error = ref('')

onMounted(async () => {
  try {
    health.value = await api.health()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
})
</script>

<template>
  <n-alert v-if="error" type="error" title="无法连接后端">{{ error }}</n-alert>
  <template v-else-if="health">
    <n-alert
      v-if="!health.writes_enabled"
      type="success"
      title="当前为只读模式"
      style="margin-bottom: 16px"
    >
      dry_run 与 organize_enabled 决定了本进程不会移动、改名或删除任何文件。这是默认的安全状态。
    </n-alert>
    <n-grid :cols="4" :x-gap="16" :y-gap="16">
      <n-grid-item>
        <n-card><n-statistic label="注册数据源" :value="health.sources" /></n-card>
      </n-grid-item>
      <n-grid-item>
        <n-card><n-statistic label="任务队列" :value="health.queue_depth" /></n-card>
      </n-grid-item>
      <n-grid-item>
        <n-card>
          <n-statistic label="写入">
            <n-tag :type="health.writes_enabled ? 'warning' : 'success'" size="large">
              {{ health.writes_enabled ? '已开启' : '已关闭' }}
            </n-tag>
          </n-statistic>
        </n-card>
      </n-grid-item>
      <n-grid-item>
        <n-card>
          <n-statistic label="dry_run">
            <n-tag :type="health.dry_run ? 'success' : 'error'" size="large">
              {{ health.dry_run ? '是' : '否' }}
            </n-tag>
          </n-statistic>
        </n-card>
      </n-grid-item>
    </n-grid>
    <n-card title="允许根目录" style="margin-top: 16px">
      <n-alert v-if="!health.allowed_roots.length" type="warning">
        未配置 AVS_ALLOWED_ROOTS。扫描与写入都会被拒绝。
      </n-alert>
      <ul v-else>
        <li v-for="root in health.allowed_roots" :key="root">{{ root }}</li>
      </ul>
    </n-card>
  </template>
</template>
