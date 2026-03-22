import type { TopicData, TopicLang } from './types'

export const getTopicLabel = (topic: TopicData | undefined, lang: TopicLang): string => {
  const label = topic?.label
  if (!label) return 'outlier'
  if (typeof label === 'string') return label
  return label[lang] || label.zh || label.en || 'outlier'
}

export const levelClass = (level: string): string => {
  if (level === 'High') return 'high'
  if (level === 'Medium') return 'mid'
  return 'low'
}

export const scoreColor = (score: number): string => {
  if (score > 75) return '#ef4444'
  if (score > 40) return '#f59e0b'
  return '#22c55e'
}

export const catLabel: Record<string, string> = {
  financial_market: 'FINANCIAL',
  policy_regulation: 'POLICY',
  company_event: 'COMPANY',
  macro_economy: 'MACRO',
  industry_trend: 'INDUSTRY',
}

export const catColor: Record<string, string> = {
  financial_market: '#3b82f6',
  policy_regulation: '#a855f7',
  company_event: '#f59e0b',
  macro_economy: '#06b6d4',
  industry_trend: '#10b981',
}

export const sourceName = (src: string | undefined): string => {
  if (!src) return '—'
  if (src.includes('finnhub')) return 'Finnhub'
  if (src.includes('marketaux')) return 'Marketaux'
  if (src.includes('stockdata')) return 'StockData'
  if (src.includes('alphavantage')) return 'Alpha Vantage'
  if (src.includes('polygon')) return 'Polygon'
  if (src.includes('newsapi')) return 'NewsAPI'
  if (src.includes('gnews')) return 'GNews'
  if (src.includes('mktnews')) return 'MktNews Flash'
  if (src.includes('hackernews')) return 'Hacker News'
  if (src.includes('eodhd')) return 'EODHD'
  if (src.includes('currentsapi')) return 'CurrentsAPI'
  if (src.includes('mediastack')) return 'Mediastack'
  if (src.includes('bbc')) return 'BBC Business'
  if (src.includes('yahoo')) return 'Yahoo Finance'
  if (src.includes('seed')) return 'Seed'
  return src
}

export const fmtTime = (iso: string | undefined): string => {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleString('en-US', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  })
}

export const fmtTimeAgo = (iso: string): string => {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const now = Date.now()
  const diff = now - d.getTime()
  if (diff < 0) return '0m'
  const mins = Math.floor(diff / 60000)
  const hours = Math.floor(mins / 60)
  const days = Math.floor(hours / 24)
  if (days > 0) {
    const remH = hours - days * 24
    return remH > 0 ? `${days}d${remH}h` : `${days}d`
  }
  if (hours > 0) {
    const remM = mins - hours * 60
    return remM > 0 ? `${hours}h${remM}m` : `${hours}h`
  }
  return `${mins}m`
}

export const isValidUrl = (url: string | undefined): boolean =>
  !!url && (url.startsWith('http://') || url.startsWith('https://'))
