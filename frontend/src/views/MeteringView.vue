<template>
  <section class="page metering-page" data-testid="metering-page">
    <header class="page-head">
      <div>
        <h2>分域访问计量</h2>
        <p class="page-desc">
          按湖表归属把访问审计聚合成业务域用量。历史周期已出账并被旧域冻结，
          当期周期由新域按尚未结算的实际返回记录数复核。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="refresh-period" type="button" :disabled="busy" @click="refresh">
          刷新计量
        </button>
        <button class="btn" type="button" :disabled="busy" @click="loadOverview">
          重载口径
        </button>
      </div>
    </header>

    <form class="analysis-form" data-testid="metering-filters" @submit.prevent="loadDataset">
      <label>
        <span>业务域</span>
        <select v-model="activeDomain" data-testid="select-domain">
          <option v-for="domain in domains" :key="domain" :value="domain">{{ domain }}</option>
        </select>
      </label>
      <label>
        <span>湖表</span>
        <select v-model.number="activeTableId" data-testid="select-table" @change="loadOwnershipPayload">
          <option v-for="table in tables" :key="table.湖表ID" :value="table.湖表ID">
            {{ table.表名 }}（{{ table.表标识 }}）
          </option>
        </select>
      </label>
      <label>
        <span>结算周期</span>
        <select v-model="activePeriod" data-testid="select-period">
          <option v-for="period in periods" :key="period.周期编号" :value="period.周期编号">
            {{ period.周期编号 }} {{ period.数据集类型 }}
          </option>
        </select>
      </label>
      <button class="btn primary" data-testid="load-dataset" type="submit" :disabled="busy">
        查看本域数据集
      </button>
      <button
        class="btn"
        data-testid="settle"
        type="button"
        :disabled="busy || !canSettle"
        :title="canSettle ? '关闭本域在该周期的开放结算' : '本域在该周期没有未结算的账'"
        @click="settleCurrent"
      >
        结算本域当期
      </button>
    </form>

    <p v-if="message" class="metering-message" data-testid="metering-message">{{ message }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="metering-error">{{ errorMessage }}</p>

    <div class="stat-row" data-testid="dataset-summary">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>

    <p class="status-legend" data-testid="dataset-scope">
      {{ activeDomain }} · {{ activePeriod }} · {{ scopeLabel }} ·
      {{ dataset?.结算?.结算编号 ?? '无结算单' }}
    </p>

    <h3 class="section-title">按表聚合（本域可读总量）</h3>
    <table class="data-table" data-testid="table-aggregate">
      <thead>
        <tr>
          <th>目标湖表</th>
          <th>表标识</th>
          <th>业务域</th>
          <th>访问条数</th>
          <th>记录数合计</th>
          <th>归属记录</th>
          <th>归属冲突</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in dataset?.按表聚合 ?? []" :key="row.目标湖表" :data-testid="`aggregate-${row.目标湖表}`">
          <td>{{ row.目标湖表 }}</td>
          <td>{{ row.表标识 }}</td>
          <td>{{ row.业务域 }}</td>
          <td>{{ row.访问条数 }}</td>
          <td>{{ row.记录数合计 }}</td>
          <td class="ownership-cell">{{ row.归属记录编号.join('、') }}</td>
          <td>{{ row.归属冲突 ? '有' : '无' }}</td>
        </tr>
        <tr v-if="!(dataset?.按表聚合 ?? []).length">
          <td colspan="7" class="empty-state">本域在该周期没有可计量访问</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">访问明细（仅本域）</h3>
    <table class="data-table" data-testid="ledger-detail">
      <thead>
        <tr>
          <th>访问编号</th>
          <th>访问时间</th>
          <th>访问者</th>
          <th>目标湖表</th>
          <th>返回记录数</th>
          <th>归属记录</th>
          <th>归属生效时间</th>
          <th>归属依据</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in dataset?.明细 ?? []" :key="row.访问编号" :data-testid="`detail-${row.访问编号}`">
          <td>{{ row.访问编号 }}</td>
          <td>{{ row.访问时间 }}</td>
          <td>{{ row.访问者 }}</td>
          <td>{{ row.目标湖表 }}</td>
          <td>{{ row.返回记录数 }}</td>
          <td>{{ row.归属记录编号 }}</td>
          <td>{{ row.归属生效时间 }}</td>
          <td>{{ row.归属依据 }}</td>
        </tr>
        <tr v-if="!(dataset?.明细 ?? []).length">
          <td colspan="8" class="empty-state">没有本域可读的访问明细</td>
        </tr>
      </tbody>
    </table>

    <template v-if="(dataset?.冲突 ?? []).length">
      <h3 class="section-title">归属冲突（旧域复查用）</h3>
      <table class="data-table" data-testid="conflict-table">
        <thead>
          <tr>
            <th>访问编号</th>
            <th>归属业务域</th>
            <th>当前归属业务域</th>
            <th>归属记录编号</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in dataset?.冲突 ?? []" :key="row.访问编号">
            <td>{{ row.访问编号 }}</td>
            <td>{{ row.归属业务域 }}</td>
            <td>{{ row.当前归属业务域 }}</td>
            <td>{{ row.归属记录编号 }}</td>
            <td>{{ row.冲突说明 }}</td>
          </tr>
        </tbody>
      </table>
    </template>

    <h3 class="section-title">本域结算统计（不含访问明细）</h3>
    <table class="data-table" data-testid="settlement-stats">
      <thead>
        <tr>
          <th>结算编号</th>
          <th>周期</th>
          <th>类型</th>
          <th>状态</th>
          <th>访问条数</th>
          <th>记录数合计</th>
          <th>冲突条数</th>
          <th>复核</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in settlements?.items ?? []" :key="item.结算编号">
          <td>{{ item.结算编号 }}</td>
          <td>{{ item.周期编号 }}</td>
          <td>{{ item.数据集类型 }}</td>
          <td>{{ item.状态 }}</td>
          <td>{{ item.访问条数 }}</td>
          <td>{{ item.记录数合计 }}</td>
          <td>{{ item.冲突条数 }}</td>
          <td>
            <button
              class="link"
              type="button"
              :data-testid="`trace-${item.结算编号}`"
              @click="traceSettlement(item.结算编号, item.周期编号)"
            >
              追到相关访问
            </button>
          </td>
        </tr>
        <tr v-if="!(settlements?.items ?? []).length">
          <td colspan="8" class="empty-state">本域暂无结算单</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">湖表归属登记（带表标识与生效区间）</h3>
    <table class="data-table" data-testid="ownership-table">
      <thead>
        <tr>
          <th>归属记录编号</th>
          <th>表标识</th>
          <th>表名</th>
          <th>业务域</th>
          <th>生效时间</th>
          <th>失效时间</th>
          <th>变更原因</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in ownership" :key="row.归属记录编号" :data-testid="`ownership-${row.归属记录编号}`">
          <td>{{ row.归属记录编号 }}</td>
          <td>{{ row.表标识 }}</td>
          <td>{{ row.表名 }}</td>
          <td>{{ row.业务域 }}</td>
          <td>{{ row.生效时间 }}</td>
          <td>{{ row.失效时间 ?? '至今' }}</td>
          <td>{{ row.变更原因 }}</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>归属冲突 {{ dataset?.总量?.归属冲突条数 ?? 0 }} 条，每条访问只计入一个业务域</span>
      <span>{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  loadDomainDataset,
  loadDomainSettlements,
  loadMeteringOverview,
  loadOwnership,
  loadSettlementDetails,
  refreshPeriod,
  settlePeriod,
} from '@/api/backend-service'
import type {
  DomainDataset,
  DomainSettlementStats,
  MeteringOverview,
  OwnershipRecord,
  TableOwnershipSummary,
} from '@/domain/metering'

const overview = ref<MeteringOverview | null>(null)
const dataset = ref<DomainDataset | null>(null)
const settlements = ref<DomainSettlementStats | null>(null)
const ownership = ref<OwnershipRecord[]>([])
const tables = ref<TableOwnershipSummary[]>([])
const activeTableId = ref(4)
const activeDomain = ref('')
const activePeriod = ref('')
const busy = ref(false)
const message = ref('')
const errorMessage = ref('')

const domains = computed(() =>
  (overview.value?.业务域汇总.items ?? []).map((item) => item.业务域),
)
const periods = computed(() => overview.value?.结算周期 ?? [])
const scopeLabel = computed(() => {
  if (!dataset.value) return '尚未生成'
  return dataset.value.数据集类型 === '历史'
    ? '历史（已出账，冻结）'
    : '当期（未结算，可继续补计）'
})
const canSettle = computed(() =>
  (settlements.value?.items ?? []).some(
    (item) => !item.冻结 && item.周期编号 === activePeriod.value,
  ),
)
const cards = computed(() => {
  const total = dataset.value?.总量
  return [
    { label: '本域访问条数', value: total?.访问条数 ?? 0 },
    { label: '本域记录数合计', value: total?.记录数合计 ?? 0 },
    { label: '覆盖湖表数', value: total?.湖表数 ?? 0 },
    { label: '归属冲突', value: total?.归属冲突条数 ?? 0 },
  ]
})

function resetMessages() {
  message.value = ''
  errorMessage.value = ''
}

async function loadOverview() {
  resetMessages()
  try {
    const payload = await loadMeteringOverview()
    overview.value = payload
    if (!activeDomain.value && payload.业务域汇总.items.length) {
      activeDomain.value = payload.业务域汇总.items[0].业务域
    }
    if (!activePeriod.value && payload.结算周期.length) {
      activePeriod.value = payload.结算周期[0].周期编号
    }
    tables.value = payload.湖表归属
    if (!tables.value.some((table) => table.湖表ID === activeTableId.value)) {
      activeTableId.value = tables.value[0]?.湖表ID ?? 4
    }
    await loadOwnershipPayload()
    await loadDataset()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '计量口径读取失败'
  }
}

async function loadOwnershipPayload() {
  const payload = await loadOwnership(activeTableId.value)
  ownership.value = payload.items
}

async function loadDataset() {
  if (!activeDomain.value || !activePeriod.value) return
  busy.value = true
  resetMessages()
  try {
    dataset.value = await loadDomainDataset(activeDomain.value, activePeriod.value)
    settlements.value = await loadDomainSettlements(activeDomain.value)
  } catch (error) {
    dataset.value = null
    settlements.value = null
    errorMessage.value = error instanceof Error ? error.message : '分域数据集读取失败'
  } finally {
    busy.value = false
  }
}

async function refresh() {
  if (!activePeriod.value) return
  busy.value = true
  resetMessages()
  try {
    const result = await refreshPeriod(activePeriod.value)
    message.value = result.message
    await loadOverview()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '刷新计量失败'
  } finally {
    busy.value = false
  }
}

async function settleCurrent() {
  if (!activeDomain.value || !activePeriod.value) return
  busy.value = true
  resetMessages()
  try {
    const result = await settlePeriod(activePeriod.value, activeDomain.value)
    message.value = result.message
    await loadOverview()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '结算失败'
  } finally {
    busy.value = false
  }
}

async function traceSettlement(settlementNo: string, period: string) {
  // 从本域结算统计追到该结算单的访问明细；后端会再次校验业务域。
  busy.value = true
  resetMessages()
  try {
    dataset.value = await loadSettlementDetails(settlementNo, activeDomain.value)
    activePeriod.value = period
    message.value = `已从 ${settlementNo} 追溯到本域可读访问明细`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '结算明细读取失败'
  } finally {
    busy.value = false
  }
}

onMounted(loadOverview)
</script>

<style scoped>
.section-title {
  font-size: 14px;
  margin: 18px 0 8px;
}
.metering-message {
  color: #166534;
  font-size: 13px;
}
.ownership-cell {
  font-size: 12px;
  color: var(--muted);
}
</style>
