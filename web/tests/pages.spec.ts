/**
 * 每个页面都真挂载一遍。
 *
 * **起因是一次线上白屏**：「刮削记录」用了 `useDialog()`，而 App.vue 里只挂了
 * `n-message-provider`。naive-ui 在注入不到 provider 时直接抛异常，异常发生在
 * 组件 setup 阶段 —— 整页空白。而 `npm run build` 完全抓不到它：
 * provider 是运行时的依赖注入，类型检查与打包都不会碰。
 *
 * 所以这里不测逻辑，只测一件最基本的事：**页面能打开、能渲染出内容。**
 * 这类测试的价值不在覆盖率，在于它是唯一能挡住"白屏"的东西。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from '@/App.vue'
import { router } from '@/router'

function reply(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as unknown as Response
}

const HEALTH = {
  status: 'ok',
  version: '0.1.0',
  build_sha: 'test',
  build_time: '',
  data_dir: '/app/data',
  allowed_roots: ['/media'],
  writes_enabled: false,
  dry_run: true,
  organize_enabled: false,
  queue_depth: 0,
  sources: 5,
}

const VERSION = {
  version: '0.1.0',
  build_sha: 'test',
  build_time: '2026-09-19T18:20:00Z',
  latest_sha: 'test',
  up_to_date: true,
  error: null,
}

const CONFIG = {
  dry_run: true,
  organize_enabled: false,
  file_op_rate: 0.5,
  file_op_burst: 3,
  http_rate: 1,
  http_burst: 3,
  cooldown_seconds: 60,
  failure_threshold: 3,
  max_retries: 1,
  request_timeout: 20,
  proxy: '',
  user_agent: '',
  enabled_sources: [],
  field_priority: {},
  route_override: {},
  directory_template: '',
  filename_template: '',
  image_template: '',
  nfo_template: '',
  metadata_dir: '',
  cd2_mappings: [],
  webhook_enabled: false,
  webhook_token: '',
  webhook_debounce_seconds: 5,
  webhook_max_subtree_files: 500,
  webhook_auto_scrape: false,
  source_cookies: {},
  images: {
    poster: true,
    thumb: true,
    fanart: false,
    extrafanart: false,
    extrafanart_limit: 5,
    fanart_min_width: 400,
    overwrite: false,
    concurrency: 3,
    timeout: 20,
  },
}

const RECORD = {
  id: 'r1',
  path: '/media/media/[251128][nur]ドSなペット.chs.mp4',
  provider: 'local',
  number: null,
  content_type: 'janime',
  season: null,
  episode: null,
  cd: null,
  episode_source: '',
  status: 'success',
  error: null,
  field_sources: { title: 'bangumi' },
  metadata: { title: '抖S的宠物' },
  updated_at: '2026-09-19T00:00:00Z',
}

let records: unknown[] = []

beforeEach(() => {
  records = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: unknown) => {
      const url = String(input)
      if (url.startsWith('/api/records')) return reply(records)
      if (url.startsWith('/api/health')) return reply(HEALTH)
      if (url.startsWith('/api/version/check')) return reply(VERSION)
      if (url.startsWith('/api/config')) return reply(CONFIG)
      return reply([])
    }),
  )
})

async function open(path: string) {
  await router.push(path)
  await router.isReady()
  const wrapper = mount(App, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

const PAGES: [string, string][] = [
  ['/dashboard', '允许根目录'],
  ['/tasks', '新建扫描任务'],
  ['/records', '刮削记录'],
  ['/sources', '数据源'],
  ['/settings', '运行期设置'],
  ['/logs', '运行日志'],
]

describe('每个页面都能挂载并渲染出内容', () => {
  for (const [path, needle] of PAGES) {
    it(`${path} 渲染出「${needle}」`, async () => {
      const wrapper = await open(path)
      expect(wrapper.text()).toContain(needle)
      wrapper.unmount()
    })
  }
})

describe('刮削记录页', () => {
  it('空列表时工具栏仍然可用', async () => {
    const wrapper = await open('/records')
    expect(wrapper.text()).toContain('清理记录')
    wrapper.unmount()
  })

  it('有记录时行内操作按钮渲染出来', async () => {
    // 这条直接锁住那次白屏：表格是为了渲染行内按钮才用 NSpace 的，
    // 而整个页面在 setup 阶段就因为缺 provider 挂了。
    records = [RECORD]
    const wrapper = await open('/records')
    const text = wrapper.text()
    expect(text).toContain('重刮')
    expect(text).toContain('删除记录')
    expect(text).toContain('抖S的宠物')
    wrapper.unmount()
  })
})

describe('版本号', () => {
  // 用户反复踩的坑：分不清容器里跑的是不是最新版，只能靠行为反推。
  // 所以侧边栏直接显示版本 + 构建号，并且能一键问后端。
  it('侧边栏显示版本号和构建号', async () => {
    const wrapper = await open('/dashboard')
    const text = wrapper.text()
    expect(text).toContain('v0.1.0')
    expect(text).toContain('test')
    wrapper.unmount()
  })

  it('点「检查更新」会去问后端', async () => {
    const wrapper = await open('/dashboard')
    const button = wrapper.findAll('button').find((item) => item.text().includes('检查更新'))
    expect(button).toBeTruthy()

    await button!.trigger('click')
    await flushPromises()

    const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
    const urls = calls.map((call) => String(call[0]))
    expect(urls).toContain('/api/version/check')
    wrapper.unmount()
  })
})
