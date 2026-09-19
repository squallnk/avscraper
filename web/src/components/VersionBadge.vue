<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NButton, NTag, useMessage } from 'naive-ui'
import { api, type HealthInfo } from '@/api'

const props = defineProps<{ collapsed: boolean }>()
const message = useMessage()
const health = ref<HealthInfo | null>(null)
const checking = ref(false)

// 镜像里的 build_sha 是 12 位（Dockerfile 截过），认版本够用了。
const sha = computed(() => health.value?.build_sha ?? '…')
const built = computed(() => (health.value?.build_time ?? '').replace('T', ' ').slice(0, 16))

/** 问后端要"GHCR 上 latest 镜像的构建号"，和本地这个比。 */
async function check() {
  checking.value = true
  try {
    const info = await api.versionCheck()
    if (info.error) message.error(info.error)
    else if (info.up_to_date) message.success('已是最新版本：' + info.build_sha)
    else message.warning('有新版本：' + (info.latest_sha ?? '').slice(0, 12) + '，在 Unraid 里 force update 拉取')
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    checking.value = false
  }
}

onMounted(async () => {
  try {
    health.value = await api.health()
  } catch {
    // 拉不到就不显示版本 —— 总览页会把连接错误说清楚，这里不重复报
  }
})
</script>

<template>
  <div class="version">
    <n-tag v-if="!props.collapsed" size="small" :bordered="false">
      v{{ health?.version ?? '?' }} · {{ sha }}
    </n-tag>
    <div v-if="!props.collapsed && built" class="built">构建 {{ built }}</div>
    <n-button size="tiny" quaternary :loading="checking" @click="check">
      {{ props.collapsed ? '?' : '检查更新' }}
    </n-button>
  </div>
</template>

<style scoped>
.version {
  padding: 12px 20px 0;
  font-size: 12px;
  line-height: 1.6;
}
.built {
  opacity: 0.7;
}
</style>
