<script setup lang="ts">
import { computed, h, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import {
  NConfigProvider,
  NDialogProvider,
  NLayout,
  NLayoutSider,
  NMenu,
  NMessageProvider,
  darkTheme,
} from 'naive-ui'
import type { MenuOption } from 'naive-ui'

const route = useRoute()
const collapsed = ref(false)

const menu: MenuOption[] = [
  { label: () => h(RouterLink, { to: '/dashboard' }, { default: () => '总览' }), key: 'dashboard' },
  { label: () => h(RouterLink, { to: '/tasks' }, { default: () => '任务' }), key: 'tasks' },
  { label: () => h(RouterLink, { to: '/records' }, { default: () => '刮削记录' }), key: 'records' },
  { label: () => h(RouterLink, { to: '/sources' }, { default: () => '数据源' }), key: 'sources' },
  { label: () => h(RouterLink, { to: '/settings' }, { default: () => '设置' }), key: 'settings' },
  { label: () => h(RouterLink, { to: '/logs' }, { default: () => '日志' }), key: 'logs' },
]

const active = computed(() => String(route.path.replace('/', '') || 'dashboard'))
</script>

<template>
  <n-config-provider :theme="darkTheme">
    <!-- useDialog() 拿不到 provider 时**直接抛异常**，而它在组件 setup 里调用 ——
         少了这一层，整个「刮削记录」页会白屏，且 npm run build 不会报错。 -->
    <n-dialog-provider>
      <n-message-provider>
        <n-layout has-sider style="height: 100vh">
        <n-layout-sider
          bordered
          collapse-mode="width"
          :collapsed-width="64"
          :width="200"
          :collapsed="collapsed"
          show-trigger
          @collapse="collapsed = true"
          @expand="collapsed = false"
        >
          <div style="padding: 16px 20px; font-weight: 600; letter-spacing: 1px">avscraper</div>
          <n-menu :value="active" :options="menu" :collapsed="collapsed" />
        </n-layout-sider>
        <n-layout content-style="padding: 20px; overflow: auto">
          <router-view />
        </n-layout>
        </n-layout>
      </n-message-provider>
    </n-dialog-provider>
  </n-config-provider>
</template>

<style>
body {
  margin: 0;
  font-family: system-ui, -apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
</style>
