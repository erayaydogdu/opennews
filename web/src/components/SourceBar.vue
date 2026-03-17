<template>
  <div class="source-bar">
    <label class="source-label">数据源</label>
    <select id="sourceSelect" :value="modelValue" @change="$emit('update:modelValue', ($event.target as HTMLSelectElement).value)">
      <option value="hours:1">近 1 小时</option>
      <option value="hours:3">近 3 小时</option>
      <option value="hours:12">近 12 小时</option>
      <option value="hours:24">近 1 天</option>
      <option value="hours:72">近 3 天</option>
      <option value="hours:168">近 7 天</option>
    </select>
    <button class="source-btn" @click="$emit('load')">加载</button>
    <input ref="fileInput" type="file" accept=".json" style="display:none" @change="onFileChange">
    <button class="source-btn" @click="($refs.fileInput as HTMLInputElement).click()">导入 JSON</button>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { BatchItem } from '@/types'

defineProps<{ modelValue: string }>()
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
