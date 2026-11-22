/** 分域计量的前端数据模型与展示辅助，保持与后端字段一一对应。 */

export type OwnershipRecord = {
  id: number
  归属记录编号: string
  表标识: string
  湖表ID: number
  表名: string
  业务域: string
  生效时间: string
  失效时间: string | null
  状态: string
  变更原因: string
  操作人?: string
}

export type SettlementPeriod = {
  id: number
  周期编号: string
  周期名称: string
  开始时间: string
  结束时间: string
  状态: string
  数据集类型: string
  结算时间: string | null
  业务域: string[]
}

export type TableOwnershipSummary = {
  湖表ID: number
  表标识: string
  表名: string
  当前业务域: string
  当前归属记录编号: string | null
  归属记录数: number
  已计量访问条数: number
  已计量记录数: number
}

export type MeteringOverview = {
  业务域汇总: {
    业务域数: number
    items: {
      业务域: string
      结算单数: number
      访问条数: number
      记录数合计: number
      湖表数: number
      冲突条数: number
      结算单: {
        结算编号: string
        周期编号: string
        数据集类型: string
        状态: string
        访问条数: number
        记录数合计: number
        冲突条数: number
      }[]
    }[]
  }
  结算周期: SettlementPeriod[]
  湖表归属: TableOwnershipSummary[]
}

export type LedgerDetail = {
  id: number
  结算编号: string
  周期编号: string
  访问编号: string
  访问ID: number
  业务域: string
  目标湖表: string
  表标识: string
  访问时间: string
  访问者: string
  操作类型: string
  返回记录数: number
  计量口径: string
  计量异常: boolean
  归属记录编号: string
  归属生效时间: string
  归属失效时间: string | null
  归属依据: string
  当前归属业务域: string | null
  当前归属记录编号: string | null
  归属冲突: boolean
  冲突说明: string
  结算状态: string
  冻结: boolean
}

export type TableAggregate = {
  目标湖表: string
  表标识: string
  业务域: string
  访问条数: number
  记录数合计: number
  归属记录编号: string[]
  归属依据: string[]
  归属冲突: boolean
}

export type ConflictRow = {
  访问编号: string
  目标湖表: string
  归属业务域: string
  当前归属业务域: string
  归属记录编号: string
  归属生效时间: string
  冲突说明: string
}

export type DomainDataset = {
  业务域: string
  周期编号: string
  周期名称: string
  数据集类型: string
  周期状态: string
  结算: {
    结算编号: string
    状态: string
    结算状态: string
    生成时间: string
    结算时间: string | null
    冻结: boolean
  } | null
  结算单: {
    结算编号: string
    状态: string
    结算状态: string
    冻结: boolean
    访问条数: number
    记录数合计: number
  }[]
  总量: {
    访问条数: number
    记录数合计: number
    湖表数: number
    归属冲突条数: number
  }
  按表聚合: TableAggregate[]
  明细: LedgerDetail[]
  冲突: ConflictRow[]
}

export type DomainSettlementStats = {
  业务域: string
  结算单数: number
  items: {
    结算编号: string
    周期编号: string
    业务域: string
    数据集类型: string
    状态: string
    结算状态: string
    结算时间: string | null
    冻结: boolean
    访问条数: number
    记录数合计: number
    湖表数: number
    冲突条数: number
    按表聚合: {
      目标湖表: string
      表标识: string
      访问条数: number
      记录数合计: number
      归属冲突: boolean
    }[]
  }[]
}

/** 数据集口径说明：历史账冻结、当期账可继续补齐。 */
export function datasetScopeLabel(dataset: DomainDataset | null): string {
  if (!dataset) return '尚未生成'
  if (dataset.数据集类型 === '历史') return '历史（已出账，冻结）'
  return '当期（未结算，可继续补计）'
}

/** 把归属链压缩成一行，便于在明细里复核。 */
export function ownershipLabel(row: LedgerDetail): string {
  const end = row.归属失效时间 ? row.归属失效时间 : '至今'
  return `${row.归属记录编号}（${row.归属生效时间} 起，${end}）`
}
