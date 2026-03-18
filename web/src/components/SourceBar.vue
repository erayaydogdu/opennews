<template>
  <div class="source-bar">
    <label class="source-label">{{ topicLang === 'zh' ? '数据源' : 'Source' }}</label>
    <select id="sourceSelect" :value="modelValue" @change="$emit('update:modelValue', ($event.target as HTMLSelectElement).value)">
      <option value="hours:1">{{ topicLang === 'zh' ? '近 1 小时' : 'Last 1 hour' }}</option>
      <option value="hours:3">{{ topicLang === 'zh' ? '近 3 小时' : 'Last 3 hours' }}</option>
      <option value="hours:12">{{ topicLang === 'zh' ? '近 12 小时' : 'Last 12 hours' }}</option>
      <option value="hours:24">{{ topicLang === 'zh' ? '近 1 天' : 'Last 1 day' }}</option>
      <option value="hours:72">{{ topicLang === 'zh' ? '近 3 天' : 'Last 3 days' }}</option>
      <option value="hours:168">{{ topicLang === 'zh' ? '近 7 天' : 'Last 7 days' }}</option>
    </select>
    <button class="source-btn" @click="$emit('load')">{{ topicLang === 'zh' ? '加载' : 'Load' }}</button>
    <input ref="fileInput" type="file" accept=".json" style="display:none" @change="onFileChange">
    <button class="source-btn" @click="($refs.fileInput as HTMLInputElement).click()">{{ topicLang === 'zh' ? '导入 JSON' : 'Import JSON' }}</button>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { BatchItem, TopicLang } from '@/types'

defineProps<{ modelValue: string; topicLang: TopicLang }>()
const emit = defineEmits<{
  'update:modelValue': [val: string]
  load: []
  importJson: [items: BatchItem[]]
}>()

const fileInput = ref<HTMLInputElement>()

function onFileChange(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result as string)
      const items = (Array.isArray(data) ? data : data.items || []).filter(
        (d: any) => d.news && d.report
      )
      emit('importJson', items)
    } catch (err: any) {
      alert('JSON 解析失败: ' + err.message)
    }
  }
  reader.readAsText(file)
}
</script>
