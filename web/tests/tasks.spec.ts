/**
 * 「写入 NFO 与图片」必须真的是个开关。
 *
 * **起因**：WebUI 起扫描时 `write_metadata` 是**写死的 false** —— 于是记录页一路
 * 显示成功，磁盘上却一个文件都没有，Emby 什么都看不到，而界面上没有任何提示。
 * 这条测试查的是**提交给后端的 payload**，不是界面上有没有那个开关。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { NMessageProvider } from 'naive-ui'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'

import TasksView from '@/views/TasksView.vue'

const calls: { url: string; body: Record<string, unknown> }[] = []

function reply(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as unknown as Response
}

beforeEach(() => {
  calls.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: unknown, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : {} })
      return reply([])
    }),
  )
})

/**
 * naive-ui 不给 test-id，所以按"开关旁边的说明文字"定位。
 * 往上层找到第一个包含该文字的祖先 —— 层数封在 4 层以内，
 * 再往上就是整张卡片，会把别人的文字也算进来。
 */
function switchNear(wrapper: ReturnType<typeof mount>, text: string) {
  for (const sw of wrapper.findAll('.n-switch')) {
    let node = sw.element.parentElement
    for (let depth = 0; depth < 4 && node; depth += 1) {
      if (node.textContent?.includes(text)) return sw
      node = node.parentElement
    }
  }
  throw new Error(`找不到文字为「${text}」的开关`)
}

async function mountTasks() {
  const Host = defineComponent({
    render: () => h(NMessageProvider, null, { default: () => h(TasksView) }),
  })
  const wrapper = mount(Host)
  await flushPromises()
  return wrapper
}

async function submit(wrapper: ReturnType<typeof mount>) {
  // 「我确认」是启动任务的前置条件
  await switchNear(wrapper, '我确认在允许根目录内执行').trigger('click')
  const button = wrapper.findAll('button').find((b) => b.text().includes('开始扫描'))
  if (!button) throw new Error('找不到「开始扫描」按钮')
  await button.trigger('click')
  await flushPromises()

  const scan = calls.find((c) => c.url.includes('/api/tasks/scan'))
  if (!scan) throw new Error('没有提交扫描任务')
  return scan.body
}

describe('扫描任务的提交内容', () => {
  it('默认不写盘', async () => {
    const body = await submit(await mountTasks())
    expect(body.write_metadata).toBe(false)
    expect(body.confirm).toBe(true)
  })

  it('打开「写入 NFO 与图片」后提交的确实是 true', async () => {
    const wrapper = await mountTasks()
    await switchNear(wrapper, '磁盘上不会有任何变化').trigger('click')
    const body = await submit(wrapper)
    expect(body.write_metadata).toBe(true)
  })

  it('「跳过已成功的」也是真的开关', async () => {
    const wrapper = await mountTasks()
    await switchNear(wrapper, '也重新刮一次').trigger('click')
    const body = await submit(wrapper)
    expect(body.skip_known).toBe(false)
  })
})
