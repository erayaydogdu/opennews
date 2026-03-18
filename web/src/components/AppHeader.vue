<template>
  <header class="hdr" role="banner">
    <div class="hdr-left">
      <span class="hdr-logo" aria-hidden="true">◆</span>
      <h1 class="hdr-title">OPENNEWS</h1>
      <span class="hdr-sub">IMPACT TERMINAL</span>
    </div>
    <div class="hdr-right">
      <button class="theme-toggle" :title="topicLang === 'zh' ? '切换日间/夜间模式' : 'Toggle light/dark mode'" @click="onToggleTheme">
        <svg class="theme-icon theme-icon--sun" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
        <svg class="theme-icon theme-icon--moon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
      </button>
      <div class="lang-switch">
        <button
          v-for="lang in (['zh', 'en'] as const)"
          :key="lang"
          class="lang-btn"
          :class="{ active: topicLang === lang }"
          :data-lang="lang"
          :title="lang === 'zh' ? '中文主题' : 'English topics'"
          @click="$emit('update:topicLang', lang)"
        >{{ lang.toUpperCase() }}</button>
      </div>
      <span class="hdr-divider">│</span>
      <span class="hdr-stat">{{ stats.total }}</span>
      <span class="hdr-stat-label">{{ topicLang === 'zh' ? '条新闻' : 'news' }}</span>
      <span class="hdr-divider">│</span>
      <span class="hdr-stat">{{ stats.topics }}</span>
      <span class="hdr-stat-label">{{ topicLang === 'zh' ? '个主题' : 'topics' }}</span>
      <span class="hdr-divider">│</span>
      <span class="hdr-badge hdr-badge--high">{{ topicLang === 'zh' ? '高' : 'H' }} {{ stats.high }}</span>
      <span class="hdr-badge hdr-badge--mid">{{ topicLang === 'zh' ? '中' : 'M' }} {{ stats.mid }}</span>
      <span class="hdr-badge hdr-badge--low">{{ topicLang === 'zh' ? '低' : 'L' }} {{ stats.low }}</span>
    </div>
  </header>
</template>

<script setup lang="ts">
import type { TopicLang } from '@/types'

defineProps<{
  topicLang: TopicLang
  stats: { total: number; topics: number; high: number; mid: number; low: number }
}>()

defineEmits<{
  'update:topicLang': [lang: TopicLang]
}>()

const onToggleTheme = inject<() => void>('toggleTheme')!

import { inject } from 'vue'
</script>
