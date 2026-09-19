<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NAlert, NButton, NCard, NFormItem, NInput, NInputNumber, NSpace, NSwitch, NTag, useMessage } from 'naive-ui'
import { api, type RuntimeConfig, type SourceInfo } from '@/api'

const message = useMessage()
const config = ref<RuntimeConfig | null>(null)
const sources = ref<SourceInfo[]>([])
const cookieDraft = ref<Record<string, string>>({})
const verifying = ref('')
const verifyResult = ref<Record<string, { ok: boolean; reason: string; detail?: string }>>({})

async function verifyCookie(id: string) {
  verifying.value = id
  try {
    const r = await api.verifyCookie(id)
    verifyResult.value[id] = { ok: Boolean(r.ok), reason: r.reason || '', detail: r.detail }
    if (r.ok) message.success(`${id} 的 Cookie 有效`)
    else message.warning(`${id}: ${r.detail || r.reason}`)
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    verifying.value = ''
  }
}

async function load() {
  try {
    config.value = await api.config()
    sources.value = (await api.sources()).filter((s) => s.needs_cookie)
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

async function saveCookie(id: string) {
  const value = cookieDraft.value[id] ?? ''
  if (!value) {
    message.warning('请先粘贴 Cookie，或用「清除」按钮移除')
    return
  }
  try {
    await api.setCookie(id, value)
    cookieDraft.value[id] = ''
    await load()
    message.success(`${id} 的 Cookie 已保存`)
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

async function clearCookie(id: string) {
  try {
    await api.setCookie(id, '')
    await load()
    message.success(`已清除 ${id} 的 Cookie`)
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

async function save() {
  if (!config.value) return
  try {
    config.value = await api.patchConfig(config.value)
    message.success('已保存')
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

onMounted(load)
</script>

<template>
  <n-card v-if="config" title="运行期设置">
    <n-alert type="warning" style="margin-bottom: 16px">
      写入开关控制本进程能否移动/改名/删除文件。默认全关；打开前请确认允许根目录只包含测试目录。
    </n-alert>
    <n-space vertical>
      <n-form-item label="dry_run（只记录不执行）">
        <n-switch v-model:value="config.dry_run" />
      </n-form-item>
      <n-form-item label="organize_enabled（允许移动/改名）">
        <n-switch v-model:value="config.organize_enabled" />
      </n-form-item>
      <n-form-item label="HTTP 速率（次/秒）">
        <n-input-number v-model:value="config.http_rate" :min="0.1" :max="60" :step="0.5" />
      </n-form-item>
      <n-form-item label="文件操作速率（次/秒）">
        <n-input-number v-model:value="config.file_op_rate" :min="0.1" :max="60" :step="0.1" />
      </n-form-item>
      <n-form-item label="连续失败熔断阈值">
        <n-input-number v-model:value="config.failure_threshold" :min="1" :max="20" />
      </n-form-item>
      <n-form-item label="冷却时长（秒）">
        <n-input-number v-model:value="config.cooldown_seconds" :min="1" :max="3600" />
      </n-form-item>
      <n-form-item label="请求超时（秒）">
        <n-input-number v-model:value="config.request_timeout" :min="1" :max="180" />
      </n-form-item>
      <n-form-item label="代理">
        <n-input v-model:value="config.proxy" placeholder="http://127.0.0.1:7890 或 socks5://..." />
      </n-form-item>
      <n-form-item label="User-Agent（留空用内置默认值）">
        <n-input
          v-model:value="config.user_agent"
          placeholder="从浏览器 F12 → Network → Request Headers 里复制整串 User-Agent"
        />
      </n-form-item>
      <n-alert type="warning" style="margin-bottom: 12px">
        有些站点的 Cloudflare 会把 cf_clearance Cookie 与 User-Agent 绑定校验。
        如果从浏览器复制的 Cookie 配上默认 UA 不生效，就把浏览器的 UA 也一起填进来。
      </n-alert>
      <n-form-item label="目录模板">
        <n-input v-model:value="config.directory_template" />
      </n-form-item>
      <n-form-item label="文件名模板">
        <n-input v-model:value="config.filename_template" />
      </n-form-item>
      <n-button type="primary" @click="save">保存</n-button>
    </n-space>
  </n-card>

  <n-card v-if="config" title="图片下载" style="margin-top: 16px">
    <n-alert type="info" style="margin-bottom: 12px">
      海报决定 Emby 列表封面，建议保持开启。缩略图不再下载 —— 站点的缩略图只有
      120x90，Emby 自己从封面生成的效果更好。<b>剧照体积大、张数多</b>，
      只影响详情页观感，默认关闭；需要时再打开并限制张数。
    </n-alert>
    <n-space vertical>
      <n-form-item label="海报 poster（Emby 列表封面）">
        <n-switch v-model:value="config.images.poster" />
      </n-form-item>
      <n-form-item label="背景图 fanart（详情页大图）">
        <n-switch v-model:value="config.images.fanart" />
      </n-form-item>
      <n-form-item label="背景图最小宽度（像素，0 = 不把关）">
        <n-input-number v-model:value="config.images.fanart_min_width" :min="0" :max="4000" />
      </n-form-item>
      <n-alert type="info" style="margin-bottom: 12px">
        站点的「剧照」候选常常只是 120x90 的缩略图，当背景图用会糊。低于这个宽度的候选会被跳过，
        自动改用封面大图。设成 0 表示不把关。
      </n-alert>
      <n-form-item label="剧照 extrafanart（详情页画廊，体积大）">
        <n-switch v-model:value="config.images.extrafanart" />
      </n-form-item>
      <n-form-item label="剧照最多下载几张">
        <n-input-number
          v-model:value="config.images.extrafanart_limit"
          :min="1"
          :max="50"
          :disabled="!config.images.extrafanart"
        />
      </n-form-item>
      <n-form-item label="覆盖已存在的图片">
        <n-switch v-model:value="config.images.overwrite" />
      </n-form-item>
      <n-form-item label="并发下载数">
        <n-input-number v-model:value="config.images.concurrency" :min="1" :max="8" />
      </n-form-item>
      <n-button type="primary" @click="save">保存</n-button>
    </n-space>
  </n-card>

  <n-card v-if="config" title="CD2 / Webhook" style="margin-top: 16px">
    <n-space vertical>
      <n-form-item label="启用 webhook">
        <n-switch v-model:value="config.webhook_enabled" />
      </n-form-item>
      <n-form-item label="webhook 令牌（CD2 侧 Authorization: Bearer <token>）">
        <n-input v-model:value="config.webhook_token" placeholder="留空表示不校验（仅建议本机调试）" />
      </n-form-item>
      <n-form-item label="目录事件防抖（秒）">
        <n-input-number v-model:value="config.webhook_debounce_seconds" :min="0" :max="600" />
      </n-form-item>
      <n-form-item label="收到新文件后自动刮削">
        <n-switch v-model:value="config.webhook_auto_scrape" />
      </n-form-item>
      <n-form-item label="CD2 虚拟路径映射（每行：虚拟前缀=本地挂载点，最长前缀优先）">
        <n-input
          type="textarea"
          :rows="3"
          :value="config.cd2_mappings.map((m) => m.join('=')).join('\n')"
          @update:value="(v: string) => config && (config.cd2_mappings = v.split('\n').filter(Boolean).map((line) => line.split('=')))"
          placeholder="/115/Test=/media/115test"
        />
      </n-form-item>
      <n-button type="primary" @click="save">保存</n-button>
    </n-space>
  </n-card>

  <n-card v-if="sources.length" title="站点 Cookie" style="margin-top: 16px">
    <n-alert type="info" style="margin-bottom: 12px">
      这些站点有年龄确认墙或登录墙，需要从浏览器复制 Cookie。Cookie 存在本地 SQLite 里，
      接口回显一律是掩码，不会把明文传给前端。
    </n-alert>
    <n-space vertical>
      <div v-for="source in sources" :key="source.id">
        <n-space align="center">
          <n-tag :type="source.id in (config?.source_cookies ?? {}) ? 'success' : 'default'" size="small">
            {{ source.id }}
          </n-tag>
          <span style="opacity: 0.7">{{ source.note }}</span>
        </n-space>
        <n-space align="center" style="margin-top: 8px">
          <n-input
            v-model:value="cookieDraft[source.id]"
            type="password"
            show-password-on="click"
            placeholder="粘贴 Cookie 串"
            style="width: 420px"
          />
          <n-button @click="saveCookie(source.id)">保存</n-button>
          <n-button
            :disabled="!source.cookie_probe_query"
            :loading="verifying === source.id"
            @click="verifyCookie(source.id)"
          >
            验证
          </n-button>
          <n-button quaternary @click="clearCookie(source.id)">清除</n-button>
          <n-tag
            v-if="verifyResult[source.id]"
            :type="verifyResult[source.id].ok ? 'success' : 'error'"
            size="small"
          >
            {{ verifyResult[source.id].ok ? 'Cookie 有效' : verifyResult[source.id].reason }}
          </n-tag>
        </n-space>
      </div>
    </n-space>
  </n-card>
</template>
