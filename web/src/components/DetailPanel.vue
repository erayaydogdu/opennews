<template>
  <aside class="detail-panel" :class="{ open: !!item }" aria-label="新闻详情面板" role="complementary">
    <div class="detail-panel-inner">
      <button class="detail-close" @click="$emit('close')">✕</button>
      <div v-if="item" class="detail-body">
        <div class="d-title">{{ news.title || '' }}</div>
        <div class="d-meta">
          <span class="d-tag score" :class="levelClass(level)">{{ score.toFixed(1) }} · {{ level }}</span>
          <span class="d-tag" :style="{ color: catColor[clf.category!] || '#6b7280' }">{{ catLabel[clf.category!] || clf.category || '—' }}</span>
          <span class="d-tag">{{ sourceName(news.source) }}</span>
          <span class="d-tag">{{ fmtTime(news.published_at) }}</span>
        </div>

        <!-- DK-COT 四维评分 -->
        <div class="d-section">
          <div class="d-section-title">DK-COT 四维评分</div>
          <div class="d-scores">
            <div v-for="d in dims" :key="d.key" class="d-score-row">
              <span class="d-score-label">{{ d.label }} <span style="color:var(--text-dim);font-size:9px">{{ d.weight }}</span></span>
              <div class="d-score-track">
                <div class="d-score-fill" :style="{ width: (dkScores[d.key] ?? 0) + '%', background: scoreColor(dkScores[d.key] ?? 0) }"></div>
              </div>
              <span class="d-score-val" :style="{ color: scoreColor(dkScores[d.key] ?? 0) }">{{ (dkScores[d.key] ?? 0).toFixed(1) }}</span>
            </div>
            <div class="d-score-row" style="margin-top:4px;padding-top:6px;border-top:1px solid var(--border)">
              <span class="d-score-label" style="font-weight:700;color:var(--text-bright)">加权总分</span>
              <div class="d-score-track">
                <div class="d-score-fill" :style="{ width: score + '%', background: scoreColor(score) }"></div>
              </div>
              <span class="d-score-val" :style="{ fontWeight: 700, color: scoreColor(score) }">{{ score.toFixed(1) }}</span>
            </div>
          </div>
        </div>

        <!-- 7 维特征 -->
        <div class="d-section">
          <div class="d-section-title">7 维特征 (1-5)</div>
          <div class="d-features">
            <div v-for="f in featKeys" :key="f.key" class="d-feat">
              <div class="d-feat-val">{{ (feat[f.key] ?? 0).toFixed(2) }}</div>
              <div class="d-feat-name">{{ f.label }}</div>
            </div>
          </div>
        </div>

        <!-- 分类置信度 -->
        <div class="d-section">
          <div class="d-section-title">分类置信度</div>
          <div class="d-clf-scores">
            <div v-for="[cat, pct] in clfScores" :key="cat" class="d-clf-row">
              <span class="d-clf-label" :style="{ color: catColor[cat] || '#6b7280' }">{{ catLabel[cat] || cat }}</span>
              <div class="d-clf-bar">
                <div class="d-clf-fill" :style="{ width: (pct as number) * 100 + '%', background: catColor[cat] || '#6b7280' }"></div>
              </div>
              <span class="d-clf-pct">{{ ((pct as number) * 100).toFixed(1) }}%</span>
            </div>
          </div>
        </div>

        <!-- 识别实体 -->
        <div v-if="entities.length" class="d-section">
          <div class="d-section-title">识别实体</div>
          <div class="d-entities">
            <span v-for="(e, i) in entities" :key="i" class="d-entity">
              {{ e.name }}<span class="d-entity-type">{{ e.type }}</span>
            </span>
          </div>
        </div>

        <!-- DK-COT 推理过程 -->
        <div v-if="report.reasoning" class="d-section">
          <details class="d-reasoning-toggle" @toggle="onToggle">
            <summary class="d-section-title" style="cursor:pointer;user-select:none">
              DK-COT 推理过程 <span ref="toggleHint" style="font-size:10px;color:var(--text-dim);font-weight:400">▶ 展开</span>
            </summary>
            <div class="d-reasoning">{{ report.reasoning }}</div>
          </details>
        </div>

        <!-- 原文摘要 -->
        <div class="d-section">
          <div class="d-section-title">原文摘要</div>
          <p style="font-size:13px;line-height:1.7;color:var(--text)">{{ news.content || '—' }}</p>
          <a
            v-if="isValidUrl(news.url)"
            :href="news.url"
            target="_blank"
            rel="noopener noreferrer"
            style="font-family:var(--font-mono);font-size:11px;color:var(--accent);margin-top:8px;display:inline-block"
          >查看原文 →</a>
        </div>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { BatchItem, NewsData, ClassificationData, FeaturesData, ReportData, EntityData } from '@/types'
import { levelClass, scoreColor, catLabel, catColor, sourceName, fmtTime, isValidUrl } from '@/utils'

const props = defineProps<{ item: BatchItem | null }>()
defineEmits<{ close: [] }>()

const toggleHint = ref<HTMLSpanElement>()

const news = computed<Partial<NewsData>>(() => props.item?.news || {})
const clf = computed<Partial<ClassificationData>>(() => props.item?.classification || {})
const feat = computed<Record<string, number>>(() => (props.item?.features || {}) as Record<string, number>)
const report = computed<Partial<ReportData>>(() => props.item?.report || {})
const entities = computed<EntityData[]>(() => props.item?.entities || [])
const dkScores = computed<Record<string, number>>(() => report.value.dk_cot_scores || {})
const level = computed(() => report.value.impact_level || '低')
const score = computed(() => report.value.final_score ?? 0)

const clfScores = computed(() =>
  Object.entries(clf.value.all_scores || {}).sort((a, b) => b[1] - a[1])
)

const dims = [
  { key: 'stock_relevance', label: '股价相关性', weight: '40%' },
  { key: 'market_sentiment', label: '市场情绪', weight: '20%' },
  { key: 'policy_risk', label: '政策风险', weight: '20%' },
  { key: 'spread_breadth', label: '传播广度', weight: '20%' },
]

const featKeys = [
  { key: 'market_impact', label: 'MKT IMP' },
  { key: 'price_signal', label: 'PRICE SIG' },
  { key: 'regulatory_risk', label: 'REG RISK' },
  { key: 'timeliness', label: 'TIMELY' },
  { key: 'impact', label: 'IMPACT' },
  { key: 'controversy', label: 'CONTROV' },
  { key: 'generalizability', label: 'GENERAL' },
  { key: 'impact_score', label: 'TOTAL' },
]

function onToggle(e: Event) {
  const details = e.target as HTMLDetailsElement
  if (toggleHint.value) {
    toggleHint.value.textContent = details.open ? '▼ 折叠' : '▶ 展开'
  }
}
</script>
