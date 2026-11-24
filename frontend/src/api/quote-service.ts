import { request } from './client'

export type QuoteRecord = {
  record_id?: string
  来源页: number
  页内位置: number
  来源位置?: string
  商品: string
  报价: number | null
  原始价格: string | number | null
  提取状态: '成功' | '失败'
  零价: boolean
  失败原因: string
  来源页标题?: string
}

export type SourcePage = {
  page: number
  title: string
  html: string
  count: number
  pending_count: number
  preview: string[]
}

export type ParsedPage = {
  page: number
  title: string
  status: string
  reason: string
  count: number
  available_count: number
  pending_count: number
  pending_positions: number[]
  zero_price_count: number
  records: QuoteRecord[]
}

export type ParseResult = {
  batch_id: string
  status: string
  partial_failure: boolean
  pages: ParsedPage[]
  records: QuoteRecord[]
  available: QuoteRecord[]
  pending: QuoteRecord[]
  counts: {
    page_count: number
    record_count: number
    available_count: number
    pending_count: number
    zero_price_count: number
    failed_page_count: number
  }
  pending_positions: {
    page: number
    positions: number[]
    reasons: { position: number; reason: string }[]
  }[]
}

export type ConfirmedQuote = {
  记录编号: string
  来源页: number
  页内位置: number
  来源位置: string
  商品: string
  报价: number
  原始价格: string | number | null
  零价: boolean
  状态: string
  parse_batch: string
  映射字段: Record<string, unknown>
}

export type PendingQuote = QuoteRecord

export type QuoteResults = {
  available: ConfirmedQuote[]
  parsed_available: (QuoteRecord & { 已确认: boolean; 记录编号: string })[]
  pending: PendingQuote[]
  pending_pages: {
    page: number
    positions: number[]
    reasons: { position: number; 商品: string; reason: string; 原始价格: unknown }[]
  }[]
  pages: {
    page: number
    title: string
    available_count: number
    parsed_count: number
    pending_count: number
    pending_positions: number[]
    parse_status: string
    complete: boolean
  }[]
  counts: {
    available: number
    parsed_available: number
    pending: number
    zero_price: number
    page_count: number
  }
  latest_parse: { batch_id: string; status: string }
}

export type MapResult = {
  accepted: ConfirmedQuote[]
  skipped: QuoteRecord[]
  rejected: QuoteRecord[]
  pending: QuoteRecord[]
  counts: { accepted: number; skipped: number; rejected: number; pending: number }
  message: string
  batch_id: string
  results?: QuoteResults
}

export async function listSourcePages(): Promise<SourcePage[]> {
  const response = await request('/api/quote-workflow/source-pages')
  if (!response.ok) throw new Error('来源页清单读取失败')
  const payload = await response.json()
  return payload.items as SourcePage[]
}

export async function parseSourcePages(
  sourcePages: { page: number; html: string }[],
): Promise<ParseResult> {
  const response = await request('/api/quote-workflow/parse', {
    method: 'POST',
    body: JSON.stringify({ source_pages: sourcePages }),
  })
  if (!response.ok) throw new Error('批量解析提交失败')
  return (await response.json()) as ParseResult
}

export async function loadLatestParse(): Promise<ParseResult | null> {
  const response = await request('/api/quote-workflow/parse/latest')
  if (!response.ok) throw new Error('解析批次读取失败')
  const payload = await response.json()
  return payload.exists ? (payload as ParseResult) : null
}

export async function mapRecords(
  records: QuoteRecord[],
  mappingRules?: Record<string, string>[],
): Promise<MapResult> {
  const response = await request('/api/quote-workflow/map', {
    method: 'POST',
    body: JSON.stringify({ records, mapping_rules: mappingRules ?? undefined }),
  })
  if (!response.ok) throw new Error('字段映射提交失败')
  return (await response.json()) as MapResult
}

export async function loadQuoteResults(
  filters: { page?: number; keyword?: string } = {},
): Promise<QuoteResults> {
  const query = new URLSearchParams()
  if (filters.page) query.set('page', String(filters.page))
  if (filters.keyword) query.set('keyword', filters.keyword)
  const response = await request(`/api/quote-workflow/results?${query.toString()}`)
  if (!response.ok) throw new Error('映射结果读取失败')
  return (await response.json()) as QuoteResults
}
