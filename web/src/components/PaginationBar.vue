<template>
  <nav v-if="totalPages > 1" class="pagination" aria-label="Pagination">
    <button class="page-btn" :disabled="currentPage <= 1" @click="$emit('go', currentPage - 1)">‹</button>

    <template v-if="startPage > 1">
      <button class="page-btn" @click="$emit('go', 1)">1</button>
      <span v-if="startPage > 2" class="page-ellipsis">…</span>
    </template>

    <button
      v-for="i in pageRange"
      :key="i"
      class="page-btn"
      :class="{ active: i === currentPage }"
      @click="$emit('go', i)"
    >{{ i }}</button>

    <template v-if="endPage < totalPages">
      <span v-if="endPage < totalPages - 1" class="page-ellipsis">…</span>
      <button class="page-btn" @click="$emit('go', totalPages)">{{ totalPages }}</button>
    </template>

    <button class="page-btn" :disabled="currentPage >= totalPages" @click="$emit('go', currentPage + 1)">›</button>
  </nav>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  currentPage: number
  totalPages: number
}>()

defineEmits<{ go: [page: number] }>()

const maxVisible = 5

const startPage = computed(() => {
  let s = Math.max(1, props.currentPage - Math.floor(maxVisible / 2))
  const e = s + maxVisible - 1
  if (e > props.totalPages) {
    s = Math.max(1, props.totalPages - maxVisible + 1)
  }
  return s
})

const endPage = computed(() => Math.min(startPage.value + maxVisible - 1, props.totalPages))

const pageRange = computed(() => {
  const arr: number[] = []
  for (let i = startPage.value; i <= endPage.value; i++) arr.push(i)
  return arr
})
</script>
