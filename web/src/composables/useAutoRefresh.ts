import { onUnmounted } from 'vue'

const AUTO_REFRESH_INTERVAL = 30_000

export function useAutoRefresh(callback: () => Promise<void>) {
  let timer: ReturnType<typeof setInterval> | null = null

  const start = () => {
    stop()
    timer = setInterval(callback, AUTO_REFRESH_INTERVAL)
  }

  const stop = () => {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  onUnmounted(stop)

  return { start, stop }
}
