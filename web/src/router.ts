import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/dashboard' },
    { path: '/dashboard', component: () => import('@/views/DashboardView.vue'), meta: { title: '总览' } },
    { path: '/tasks', component: () => import('@/views/TasksView.vue'), meta: { title: '任务' } },
    { path: '/records', component: () => import('@/views/RecordsView.vue'), meta: { title: '刮削记录' } },
    { path: '/sources', component: () => import('@/views/SourcesView.vue'), meta: { title: '数据源' } },
    { path: '/settings', component: () => import('@/views/SettingsView.vue'), meta: { title: '设置' } },
    { path: '/logs', component: () => import('@/views/LogsView.vue'), meta: { title: '日志' } },
  ],
})
