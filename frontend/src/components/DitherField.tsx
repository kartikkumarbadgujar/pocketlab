import { useEffect, useRef } from 'react'
import { useReducedMotion } from 'motion/react'

export default function DitherField() {
  const ref = useRef<HTMLCanvasElement>(null)
  const reduce = useReducedMotion()
  useEffect(() => {
    const canvas = ref.current
    const ctx = canvas?.getContext('2d', { alpha: false })
    if (!canvas || !ctx) return
    let frame = 0, visible = true, last = 0, width = 0, height = 0
    const resize = () => { const box = canvas.getBoundingClientRect(); width = Math.max(1, Math.round(box.width / 7)); height = Math.max(1, Math.round(box.height / 7)); canvas.width = Math.min(320, width); canvas.height = Math.min(240, height); draw(performance.now()) }
    const draw = (time: number) => {
      const image = ctx.createImageData(canvas.width, canvas.height)
      const phase = reduce ? 0 : time * 0.00018
      for (let y = 0; y < canvas.height; y++) for (let x = 0; x < canvas.width; x++) {
        const wave = Math.sin(x * .07 + phase) * Math.cos(y * .08 - phase * .7)
        const noise = (Math.sin(x * 12.9898 + y * 78.233) * 43758.5453) % 1
        const alpha = Math.max(0, Math.min(1, (wave + .15) * .31 + (noise + 1) * .09))
        const i = (y * canvas.width + x) * 4
        image.data[i] = 7 + alpha * 18; image.data[i + 1] = 13 + alpha * 26; image.data[i + 2] = 29 + alpha * 66; image.data[i + 3] = 255
      }
      ctx.putImageData(image, 0, 0)
    }
    const tick = (time: number) => { frame = requestAnimationFrame(tick); if (!visible || document.hidden || time - last < 70) return; last = time; draw(time) }
    const observer = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; if (visible && !reduce && !frame) frame = requestAnimationFrame(tick) })
    observer.observe(canvas)
    const onVisibility = () => { if (document.hidden) { cancelAnimationFrame(frame); frame = 0 } else if (visible && !reduce) frame = requestAnimationFrame(tick) }
    const resizeObserver = new ResizeObserver(resize); resizeObserver.observe(canvas); resize()
    if (!reduce) frame = requestAnimationFrame(tick)
    document.addEventListener('visibilitychange', onVisibility)
    return () => { cancelAnimationFrame(frame); observer.disconnect(); resizeObserver.disconnect(); document.removeEventListener('visibilitychange', onVisibility) }
  }, [reduce])
  return <canvas ref={ref} className="dither-field" aria-hidden="true" />
}
