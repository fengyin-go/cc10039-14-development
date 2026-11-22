import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---- 分域计量：所有读取都显式带上业务域，跨域读取由后端返回 403 ----

async function requestMetering<T>(
  path: string,
  params: Record<string, string> = {},
): Promise<T> {
  const query = new URLSearchParams(params).toString()
  const response = await request(`/api/metering${path}${query ? `?${query}` : ''}`)
  if (!response.ok) {
    let detail = `接口返回 ${response.status}`
    try {
      const payload = (await response.json()) as { detail?: string }
      if (payload?.detail) detail = payload.detail
    } catch {
      // 保留状态码说明
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export async function loadMeteringOverview() {
  return requestMetering<import('@/domain/metering').MeteringOverview>('/overview')
}

export async function loadOwnership(tableId?: number) {
  return requestMetering<{ total: number; items: import('@/domain/metering').OwnershipRecord[] }>(
    '/ownership',
    tableId ? { table_id: String(tableId) } : {},
  )
}

export async function loadDomainDataset(domain: string, period: string) {
  return requestMetering<import('@/domain/metering').DomainDataset>('/datasets', {
    domain,
    period,
  })
}

export async function loadDomainSettlements(domain: string) {
  return requestMetering<import('@/domain/metering').DomainSettlementStats>(
    '/settlements',
    { domain },
  )
}

export async function loadSettlementDetails(settlementNo: string, domain: string) {
  return requestMetering<import('@/domain/metering').DomainDataset>(
    `/settlements/${encodeURIComponent(settlementNo)}`,
    { domain },
  )
}

export async function refreshPeriod(period: string) {
  const response = await request(`/api/metering/periods/${encodeURIComponent(period)}/refresh`, {
    method: 'POST',
  })
  return (await response.json()) as { ok: boolean; message: string }
}

export async function settlePeriod(period: string, domain: string) {
  const response = await request('/api/metering/settle', {
    method: 'POST',
    body: JSON.stringify({ period, domain }),
  })
  return (await response.json()) as { ok: boolean; message: string }
}

export async function changeOwnership(
  tableId: number,
  domain: string,
  effectiveTime?: string,
) {
  const response = await request('/api/metering/ownership/change', {
    method: 'POST',
    body: JSON.stringify({
      table_id: tableId,
      domain,
      effective_time: effectiveTime || null,
    }),
  })
  return (await response.json()) as { ok: boolean; message: string }
}
