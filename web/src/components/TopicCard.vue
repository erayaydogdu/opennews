<template>
  <div class="topic-card" :class="{ open: isOpen }" :data-tid="group.topic_id">
    <div class="topic-head" @click="isOpen = !isOpen">
      <span class="topic-arrow">▶</span>
      <span class="topic-avg-score" :style="{ color: scoreColor(avgScore) }">{{ avgScore.toFixed(1) }}</span>
      <span class="topic-label">
        {{ group.label }}
        <span class="topic-src-info">（{{ srcText }}）</span>
      </span>
      <span v-if="timeAgo" class="topic-time-ago">{{ timeAgo }}</span>
    </div>
    <div class="topic-body">
      <div class="news-list">
        <NewsItem
          v-for="item in sortedItems"
          :key="item.news?.news_id"
          :item="item"
          :topic-lang="topicLang"
          :active-news-id="activeNewsId"
          @select="$emit('selectNews', $event)"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { TopicGroup, TopicLang } from '@/types'
import { scoreColor, sourceName, fmtTimeAgo } from '@/utils'
import NewsItem from './NewsItem.vue'

const props = defineProps<{
  group: TopicGroup
  topicLang: TopicLang
  activeNewsId: string | null
  wasOpen?: boolean
}>()

defineEmits<{ selectNews: [nid: string] }>()

const isOpen = ref(props.wasOpen ?? false)

// 当 wasOpen prop 变化时同步（用于恢复展开状态）
watch(() => props.wasOpen, (v) => {
  if (v !== undefined) isOpen.value = v
})

const sortedItems = computed(() =>
  [...props.group.items].sort((a, b) => (b.report?.final_score ?? 0) - (a.report?.final_score ?? 0))
)

const avgScore = computed(() => {
  const scores = props.group.items.map(i => i.report?.final_score ?? 0)
  return scores.reduce((a, b) => a + b, 0) / (scores.length || 1)
})

const srcText = computed(() => {
  const counts: Record<string, number> = {}
  props.group.items.forEach(i => {
    const s = sourceName(i.news?.source)
    counts[s] = (counts[s] || 0) + 1
  })
  return Object.entries(counts).map(([s, c]) => `${s} +${c}`).join('，')
})

const timeAgo = computed(() => {
  let latestMs = 0
  props.group.items.forEach(i => {
    const t = i.news?.published_at
    if (!t) return
    const ms = new Date(t).getTime()
    if (ms > latestMs) latestMs = ms
  })
  return latestMs > 0 ? fmtTimeAgo(new Date(latestMs).toISOString()) : ''
})
</script>
