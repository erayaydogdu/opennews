import { ref, watchEffect } from 'vue'

const isLight = ref(true)

export function useTheme() {
  // init: localStorage > system preference > light
  const saved = localStorage.getItem('theme')
  if (saved) {
    isLight.value = saved === 'light'
  } else {
    isLight.value = !window.matchMedia('(prefers-color-scheme: dark)').matches
  }

  watchEffect(() => {
    if (isLight.value) {
      document.documentElement.removeAttribute('data-theme')
    } else {
      document.documentElement.setAttribute('data-theme', 'dark')
    }
    localStorage.setItem('theme', isLight.value ? 'light' : 'dark')
  })

  const toggle = () => { isLight.value = !isLight.value }

  return { isLight, toggle }
}
