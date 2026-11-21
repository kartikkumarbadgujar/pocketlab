import { useEffect, useRef, useState } from 'react'
import { animate, motion, useInView, useMotionValue, useReducedMotion, useTransform } from 'motion/react'

export type StatsCounterProps = { label: string; value: number | null; icon: React.ReactNode; tone?: string; prefix?: string; suffix?: string; decimals?: number; hint?: string; loading?: boolean }

function AnimatedNumber({ value, prefix = '', suffix = '', decimals = 0 }: Pick<StatsCounterProps, 'value' | 'prefix' | 'suffix' | 'decimals'>) {
  const motionValue = useMotionValue(0)
  const rounded = useTransform(motionValue, (latest) => `${prefix}${latest.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}${suffix}`)
  const [display, setDisplay] = useState(`${prefix}0${suffix}`)
  const reduce = useReducedMotion()
  useEffect(() => { const unsubscribe = rounded.on('change', setDisplay); return unsubscribe }, [rounded])
  useEffect(() => {
    const controls = animate(motionValue, value ?? 0, { duration: reduce ? 0 : 1.25, ease: [0.16, 1, 0.3, 1] })
    return () => controls.stop()
  }, [motionValue, value, reduce])
  return <motion.span className="stat-value tabular-nums" aria-label={value === null ? 'Not available' : `${prefix}${value.toLocaleString()}${suffix}`}>{value === null ? '—' : display}</motion.span>
}

export default function StatsCounter({ label, value, icon, tone = 'blue', prefix = '', suffix = '', decimals = 0, hint, loading = false }: StatsCounterProps) {
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true, margin: '0px 0px -30px 0px' })
  const [started, setStarted] = useState(false)
  useEffect(() => { if (inView && !loading && value !== null) setStarted(true) }, [inView, loading, value])
  return <motion.div ref={ref} className="stat-card surface-card" initial={{ opacity: 0, y: 12 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, amount: 0.16 }} transition={{ duration: 0.32 }}>
    <div className={`stat-icon ${tone}`}>{icon}</div>
    <div className="stat-copy">
      <div className="stat-label">{label}</div>
      {loading ? <div className="skeleton stat-skeleton" aria-label={`Loading ${label}`} /> : <AnimatedNumber value={started ? value : 0} prefix={prefix} suffix={suffix} decimals={decimals} />}
      <div className="stat-hint">{hint ?? (value === null ? 'No estimate available yet' : 'From PocketLab API')}</div>
    </div>
  </motion.div>
}
