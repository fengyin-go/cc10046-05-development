<template>
  <section class="quote-workflow">
    <header class="page-head">
      <div>
        <h2>批量报价解析</h2>
        <p class="page-desc">
          勾选多个来源页一次提交，逐页逐条保留报价在原始页面的位置；修复坏片段后重新提交即可补齐，已确认的位置不会重复登记。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" :disabled="loading" @click="reloadAll">刷新</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="quote-error">{{ errorMessage }}</p>
    <p v-if="notice" class="notice-text" data-testid="quote-notice">{{ notice }}</p>

    <div class="workflow-grid">
      <article class="workflow-card">
        <h3>① 选择来源页</h3>
        <p class="hint">可多选。每页标题显示可解析条数，红色为价格栏待补条数。</p>
        <div class="source-page-list">
          <label
            v-for="page in sourcePages"
            :key="page.page"
            class="source-page-item"
            :data-testid="`source-page-${page.page}`"
          >
            <input
              v-model="selectedPages"
              type="checkbox"
              :value="page.page"
              :data-testid="`select-page-${page.page}`"
            />
            <span class="source-page-title">第{{ page.page }}页 · {{ page.title }}</span>
            <span class="badge">{{ page.count }} 条</span>
            <span v-if="page.pending_count" class="badge warn">
              {{ page.pending_count }} 处待补
            </span>
            <span class="source-page-preview">{{ page.preview.join('、') }}</span>
          </label>
        </div>
        <div class="page-actions">
          <button
            class="btn primary"
            type="button"
            data-testid="parse-selected"
            :disabled="parsing || !selectedPages.length"
            @click="submitParse"
          >
            {{ parsing ? '解析中…' : `批量解析已选 ${selectedPages.length} 页` }}
          </button>
        </div>
        <p v-if="parseResult" class="hint" data-testid="parse-status">
          解析批次 {{ parseResult.batch_id }}：{{ parseResult.status }}，
          {{ parseResult.counts.record_count }} 条记录，
          {{ parseResult.counts.available_count }} 条可用，
          {{ parseResult.counts.pending_count }} 条待补。
        </p>
        <ul v-if="parseResult" class="page-status-list">
          <li
            v-for="page in parseResult.pages"
            :key="page.page"
            :class="['page-status', page.pending_count ? 'warn' : 'ok']"
            :data-testid="`parse-page-${page.page}`"
          >
            第{{ page.page }}页：{{ page.status }}
            <template v-if="page.pending_positions.length">
              · 待补位置 {{ page.pending_positions.map((pos) => `#${pos}`).join('、') }}
            </template>
            <template v-else>· {{ page.count }} 条全部可映射</template>
          </li>
        </ul>
      </article>

      <article class="workflow-card">
        <h3>② 修复坏片段</h3>
        <p class="hint">直接修改页面 HTML 里的价格栏，保存后重新提交该页；只有待补位置会新增。</p>
        <label class="field">
          <span>来源页</span>
          <select v-model.number="repairPage" data-testid="repair-page">
            <option v-for="page in sourcePages" :key="page.page" :value="page.page">
              第{{ page.page }}页
            </option>
          </select>
        </label>
        <label class="field">
          <span>页面 HTML</span>
          <textarea
            v-model="repairHtml"
            rows="10"
            data-testid="repair-html"
            spellcheck="false"
          />
        </label>
        <div class="page-actions">
          <button
            class="btn"
            type="button"
            data-testid="resubmit-repair"
            :disabled="parsing"
            @click="resubmitRepair"
          >
            重送第{{ repairPage }}页
          </button>
        </div>
        <p class="hint">价格栏如果是 0 会被当作有效零价；缺失、`--` 或纯文字才算提取失败。</p>
      </article>

      <article class="workflow-card">
        <h3>③ 批量字段映射</h3>
        <p class="hint">把解析记录集一次提交，逐条按原始位置登记；失败位置不映射，已确认位置自动跳过。</p>
        <p class="hint" data-testid="map-preview">
          待映射记录：{{ mappableRecords.length }} 条可用，
          {{ pendingRecords.length }} 条待补（待补不登记）。
          已确认过的位置再提交会自动跳过。
        </p>
        <div class="page-actions">
          <button
            class="btn primary"
            type="button"
            data-testid="submit-map"
            :disabled="mapping || !mappableRecords.length"
            @click="submitMap"
          >
            {{ mapping ? '登记中…' : `提交映射 ${mappableRecords.length} 条` }}
          </button>
        </div>
        <p v-if="mapResult" class="hint" data-testid="map-summary">{{ mapResult.message }}</p>
        <table v-if="mapResult" class="data-table compact">
          <thead>
            <tr><th>位置</th><th>商品</th><th>报价</th><th>结果</th></tr>
          </thead>
          <tbody>
            <tr
              v-for="row in mapOutcomeRows"
              :key="`${row.来源页}-${row.页内位置}`"
              :data-testid="`map-outcome-${row.来源页}-${row.页内位置}`"
            >
              <td>第{{ row.来源页 }}页 #{{ row.页内位置 }}</td>
              <td>{{ row.商品 }}</td>
              <td>{{ row.报价 ?? '—' }}</td>
              <td>{{ row.outcome }}</td>
            </tr>
          </tbody>
        </table>
      </article>
    </div>

    <article class="workflow-card wide">
      <header class="result-head">
        <h3>④ 结果回查</h3>
        <div class="page-actions">
          <input
            v-model="keyword"
            class="inline-input"
            placeholder="按商品或位置检索"
            data-testid="result-keyword"
            @keyup.enter="refreshResults"
          />
          <button class="btn" type="button" data-testid="refresh-results" @click="refreshResults">
            查询
          </button>
        </div>
      </header>

      <p class="hint" data-testid="result-summary">
        最近一次解析可用 {{ results?.counts.parsed_available ?? 0 }} 条，其中已确认
        {{ results?.counts.available ?? 0 }} 条；待补 {{ results?.counts.pending ?? 0 }} 处；
        零价报价 {{ results?.counts.zero_price ?? 0 }} 条。
      </p>

      <div v-if="results?.pending_pages.length" class="pending-banner" data-testid="pending-banner">
        <strong>还有位置待补：</strong>
        <span v-for="item in results.pending_pages" :key="item.page" class="pending-page">
          第{{ item.page }}页
          <template v-if="item.positions.length">
            #{{ item.positions.join('、#') }}
          </template>
          <template v-if="item.reasons.length">
            （{{ item.reasons.map((reason) => reason.reason).join('；') }}）
          </template>
        </span>
        <p class="hint">
          完整页仍可正常映射；待补位置修好后重送该页，再提交映射即可追平，已确认位置不会重复登记。
        </p>
      </div>

      <table class="data-table">
        <thead>
          <tr>
            <th>来源页</th>
            <th>页内位置</th>
            <th>商品</th>
            <th>报价</th>
            <th>零价</th>
            <th>状态</th>
            <th>解析批次</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in resultRows"
            :key="`${row.来源页}-${row.页内位置}`"
            :data-testid="`result-${row.来源页}-${row.页内位置}`"
          >
            <td>第{{ row.来源页 }}页</td>
            <td>#{{ row.页内位置 }}</td>
            <td>{{ row.商品 }}</td>
            <td>{{ row.报价 }}</td>
            <td>{{ row.零价 ? '是' : '否' }}</td>
            <td>{{ row.已确认 ? row.状态 : '已解析未确认' }}</td>
            <td>{{ row.parse_batch || '—' }}</td>
          </tr>
          <tr v-if="!resultRows.length">
            <td colspan="7" class="empty-state">还没有已确认的报价，先批量解析并提交映射</td>
          </tr>
        </tbody>
      </table>

      <table
        v-if="results?.pending.length"
        class="data-table pending-table"
        data-testid="pending-table"
      >
        <thead>
          <tr><th>来源页</th><th>页内位置</th><th>商品</th><th>原始价格</th><th>待补原因</th></tr>
        </thead>
        <tbody>
          <tr
            v-for="row in results?.pending ?? []"
            :key="`pending-${row.来源页}-${row.页内位置}`"
            :data-testid="`pending-${row.来源页}-${row.页内位置}`"
          >
            <td>第{{ row.来源页 }}页</td>
            <td>#{{ row.页内位置 }}</td>
            <td>{{ row.商品 }}</td>
            <td>{{ row.原始价格 ?? '—' }}</td>
            <td>{{ row.失败原因 }}</td>
          </tr>
        </tbody>
      </table>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  listSourcePages,
  loadLatestParse,
  loadQuoteResults,
  mapRecords,
  parseSourcePages,
  type MapResult,
  type ParseResult,
  type QuoteRecord,
  type QuoteResults,
  type SourcePage,
} from '@/api/quote-service'

const sourcePages = ref<SourcePage[]>([])
const selectedPages = ref<number[]>([])
const parseResult = ref<ParseResult | null>(null)
const mapResult = ref<MapResult | null>(null)
const results = ref<QuoteResults | null>(null)
const repairPage = ref(7)
const repairHtml = ref('')
const keyword = ref('')
const loading = ref(false)
const parsing = ref(false)
const mapping = ref(false)
const errorMessage = ref('')
const notice = ref('')

const mappableRecords = computed<QuoteRecord[]>(
  () => (parseResult.value?.records ?? []).filter((row) => row.提取状态 !== '失败'),
)
const pendingRecords = computed<QuoteRecord[]>(
  () => (parseResult.value?.records ?? []).filter((row) => row.提取状态 === '失败'),
)

const mapOutcomeRows = computed(() => {
  if (!mapResult.value) return []
  const rows: {
    来源页: number
    页内位置: number
    商品: string
    报价: number | null
    outcome: string
  }[] = []
  for (const row of mapResult.value.accepted) {
    rows.push({ ...pick(row), outcome: '已确认' })
  }
  for (const row of mapResult.value.skipped) {
    rows.push({ ...pick(row), outcome: '重复跳过' })
  }
  for (const row of mapResult.value.rejected) {
    rows.push({ ...pick(row), outcome: '位置冲突拒绝' })
  }
  for (const row of mapResult.value.pending) {
    rows.push({ ...pick(row), outcome: '待补，未登记' })
  }
  return rows
})

const resultRows = computed(() => {
  const result = results.value
  if (!result) return []
  const rows: {
    来源页: number
    页内位置: number
    商品: string
    报价: number | null
    零价: boolean
    已确认: boolean
    状态: string
    parse_batch: string
  }[] = []
  const seen = new Set<string>()
  const batchId = result.latest_parse?.batch_id ?? ''
  for (const row of result.parsed_available ?? []) {
    seen.add(`${row.来源页}-${row.页内位置}`)
    rows.push({
      来源页: Number(row.来源页),
      页内位置: Number(row.页内位置),
      商品: row.商品,
      报价: row.报价,
      零价: Boolean(row.零价),
      已确认: Boolean(row.已确认),
      状态: row.已确认 ? '已确认' : '已解析未确认',
      parse_batch: batchId,
    })
  }
  for (const row of result.available) {
    const key = `${row.来源页}-${row.页内位置}`
    if (seen.has(key)) continue
    rows.push({
      来源页: Number(row.来源页),
      页内位置: Number(row.页内位置),
      商品: row.商品,
      报价: row.报价,
      零价: Boolean(row.零价),
      已确认: true,
      状态: row.状态,
      parse_batch: row.parse_batch || '',
    })
  }
  return rows
})

function pick(row: {
  来源页: number
  页内位置: number
  商品: string
  报价: number | null
}): {
  来源页: number
  页内位置: number
  商品: string
  报价: number | null
} {
  return {
    来源页: Number(row.来源页),
    页内位置: Number(row.页内位置),
    商品: row.商品,
    报价: row.报价,
  }
}

function syncRepairHtml() {
  const page = sourcePages.value.find((item) => item.page === repairPage.value)
  if (page) repairHtml.value = page.html
}

// 解析后把每页的条数和待补数写回勾选列表，避免徽标停留在上一次的状态。
function syncPageBadges() {
  const result = parseResult.value
  if (!result) return
  for (const page of result.pages) {
    const source = sourcePages.value.find((item) => item.page === page.page)
    if (!source) continue
    source.count = page.count
    source.pending_count = page.pending_count
    source.preview = page.records.map((row) => row.商品).slice(0, 5)
  }
}

watch(repairPage, syncRepairHtml)

async function reloadAll() {
  loading.value = true
  errorMessage.value = ''
  try {
    sourcePages.value = await listSourcePages()
    if (!selectedPages.value.length && sourcePages.value.length) {
      // 默认勾选第 4 页（完整）和第 7 页（一条坏价格），直接对应部分失败场景。
      const known = sourcePages.value.filter((item) => item.page === 4 || item.page === 7)
      if (known.length) {
        selectedPages.value = known.map((item) => item.page)
      } else {
        const pendingPage = sourcePages.value.find((item) => item.pending_count > 0)
        const completePage = sourcePages.value.find((item) => item.pending_count === 0)
        const defaults = [completePage?.page, pendingPage?.page].filter(
          (page): page is number => typeof page === 'number',
        )
        selectedPages.value = defaults.length
          ? defaults
          : sourcePages.value.slice(0, 2).map((item) => item.page)
      }
    }
    if (!sourcePages.value.some((item) => item.page === repairPage.value)) {
      repairPage.value = sourcePages.value[0]?.page ?? 7
    }
    syncRepairHtml()
    parseResult.value = await loadLatestParse()
    await refreshResults()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据读取失败'
  } finally {
    loading.value = false
  }
}

async function submitParse() {
  parsing.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const pages = selectedPages.value.map((page) => {
      const source = sourcePages.value.find((item) => item.page === page)
      return { page, html: source?.html ?? '' }
    })
    parseResult.value = await parseSourcePages(pages)
    notice.value = `已提交 ${pages.length} 页，解析结果 ${parseResult.value.status}`
    syncPageBadges()
    syncRepairHtml()
    await refreshResults()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量解析失败'
  } finally {
    parsing.value = false
  }
}

async function resubmitRepair() {
  parsing.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const source = sourcePages.value.find((item) => item.page === repairPage.value)
    if (source) source.html = repairHtml.value
    const pages = selectedPages.value.includes(repairPage.value)
      ? selectedPages.value
      : [...selectedPages.value, repairPage.value]
    selectedPages.value = [...new Set(pages)].sort((a, b) => a - b)
    parseResult.value = await parseSourcePages(
      selectedPages.value.map((page) => {
        const item = sourcePages.value.find((entry) => entry.page === page)
        return { page, html: page === repairPage.value ? repairHtml.value : item?.html ?? '' }
      }),
    )
    notice.value = `第${repairPage.value}页已重新提交，当前 ${parseResult.value.status}`
    await refreshResults()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重新提交失败'
  } finally {
    parsing.value = false
  }
}

async function submitMap() {
  mapping.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    mapResult.value = await mapRecords(parseResult.value?.records ?? [])
    notice.value = mapResult.value.message
    if (mapResult.value.results) results.value = mapResult.value.results
    else await refreshResults()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '字段映射失败'
  } finally {
    mapping.value = false
  }
}

async function refreshResults() {
  if (loading.value) return
  try {
    results.value = await loadQuoteResults({ keyword: keyword.value || undefined })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '结果读取失败'
  }
}

onMounted(reloadAll)
</script>

<style scoped>
.workflow-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}
.workflow-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
}
.workflow-card.wide { margin-bottom: 12px; }
.workflow-card h3 { margin: 0 0 6px; font-size: 14px; }
.hint { color: var(--muted); font-size: 12px; margin: 4px 0; }
.notice-text { color: #1f6feb; font-size: 13px; }
.source-page-list { display: flex; flex-direction: column; gap: 6px; margin: 8px 0; }
.source-page-item {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
  cursor: pointer;
}
.source-page-title { font-weight: 600; }
.source-page-preview { color: var(--muted); font-size: 12px; width: 100%; }
.badge {
  background: #eef2f7;
  border-radius: 10px;
  padding: 1px 8px;
  font-size: 11px;
  color: #334155;
}
.badge.warn { background: #fef3c7; color: #92400e; }
.page-status-list { list-style: none; padding: 0; margin: 6px 0 0; font-size: 12px; }
.page-status.ok { color: #15803d; }
.page-status.warn { color: #b45309; }
.field { display: block; margin-bottom: 8px; }
.field span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.field select, .field textarea {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.result-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.inline-input { border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 12px; }
.pending-banner {
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
  color: #92400e;
  margin-bottom: 10px;
}
.pending-page { margin-right: 12px; }
.data-table.compact th, .data-table.compact td { padding: 4px 8px; font-size: 12px; }
.pending-table td, .pending-table th { background: #fffdf5; }
</style>
