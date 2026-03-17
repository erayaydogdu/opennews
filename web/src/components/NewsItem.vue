<template>
  <div
    class="news-item"
    :class="{ active: isActive }"
    @click="$emit('select', item.news?.news_id ?? '')"
  >
    <span class="news-score" :class="levelClass(level)">{{ score.toFixed(1) }}</span>
    <span class="news-level" :class="levelClass(level)">{{ level }}</span>
    <span class="news-cat" :data-cat="cat">{{ catLabel[cat] || cat.toUpperCase() }}</span>
    <span class="news-title">{{ item.news?.title ?? '' }}</span>
    <span class="news-source">{{ sourceName(item.news?.source) }}</span>
    <span class="news-time">{{ fmtTime(item.news?.published_at) }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { BatchItem } from '@/types'
import { levelClass, catLabel, sourceName, fmtTime } from '@/utils'

const props = defineProps<{
  item: BatchItem
  activeNewsId: string | null
}>()

defineEmits<{ select: [nid: string] }>()

const score = computed(() => props.item.report?.final_score ?? 0)
const level = computed(() => props.item.report?.impact_level ?? '低')
const cat = computed(() => props.item.classification?.category ?? 'unknown')
const isActive = computed(() => (props.item.news?.news_id ?? '') === props.activeNewsId)
</script>
