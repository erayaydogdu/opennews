<template>
  <section class="topics" aria-label="News topics">
    <div v-if="groups.length === 0" class="topics-loading">
      {{ emptyText }}
    </div>
    <TopicCard
      v-for="g in groups"
      :key="g.topic_id"
      :group="g"
      :topic-lang="topicLang"
      :active-news-id="activeNewsId"
      :was-open="openTopics.has(g.topic_id)"
      @select-news="$emit('selectNews', $event)"
    />
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { BatchItem, TopicGroup, SortMode, TopicLang } from '@/types'
import { getTopicLabel } from '@/utils'
import TopicCard from './TopicCard.vue'

const props = defineProps<{
  items: BatchItem[]
  sortMode: SortMode
  topicLang: TopicLang
  activeNewsId: string | null
  openTopics: Set<string>
  emptyText: string
}>()

defineEmits<{ selectNews: [nid: string] }>()

const groups = computed<TopicGroup[]>(() => {
  const map = new Map<string, TopicGroup>()
  props.items.forEach(item => {
    const tid = item.topic?.topic_id ?? -1
    const bid = item.topic?.batch_id ?? 0
    const key = `${bid}:${tid}`
    if (!map.has(key)) {
      map.set(key, {
        topic_id: key,
        label: getTopicLabel(item.topic, props.topicLang),
        items: [],
      })
    }
    map.get(key)!.items.push(item)
  })

  const arr = [...map.values()]

  if (props.sortMode === 'time') {
    arr.sort((a, b) => {
      const la = Math.max(...a.items.map(i => new Date(i.news?.published_at || 0).getTime()))
      const lb = Math.max(...b.items.map(i => new Date(i.news?.published_at || 0).getTime()))
      return lb - la
    })
  } else if (props.sortMode === 'avg') {
    arr.sort((a, b) => {
      const aa = a.items.reduce((s, i) => s + (i.report?.final_score ?? 0), 0) / a.items.length
      const ab = b.items.reduce((s, i) => s + (i.report?.final_score ?? 0), 0) / b.items.length
      return ab - aa
    })
  } else {
    arr.sort((a, b) => {
      const ma = Math.max(...a.items.map(i => i.report?.final_score ?? 0))
      const mb = Math.max(...b.items.map(i => i.report?.final_score ?? 0))
      return mb - ma
    })
  }

  return arr
})
</script>
