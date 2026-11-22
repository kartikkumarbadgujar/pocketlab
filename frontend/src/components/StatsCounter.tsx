import { useEffect, useRef, useState } from 'react'
import { motion, useInView, useMotionValue, useReducedMotion, useSpring } from 'motion/react'

export type StatsCounterProps = {
  label: string
  value: number | null
  icon: React.ReactNode
  tone?: string
  prefix?: string
  suffix?: string
  decimals?: number
  duration?: number
  className?: string
  hint?: string
  loading?: boolean
}

function AnimatedNumber({
  value,
  prefix = '',
  suffix = '',
  decimals = 0,
  duration = 1.5,
  className = '',
}: Pick<StatsCounterProps, 'value' | 'prefix' | 'suffix' | 'decimals' | 'duration' | 'className'>) {
  const ref = useRef<HTMLSpanElement>(null)
  const inView = useInView(ref, { once: true, margin: '-100px' })
  const motionValue = useMotionValue(0)
  const springValue = useSpring(motionValue, { duration: duration * 1000, bounce: 0 })
  const [displayValue, setDisplayValue] = useState(0)
  const reduceMotion = useReducedMotion()

  useEffect(() => {
    if (value !== null && inView) {
      if (reduceMotion) setDisplayValue(value)
      else motionValue.set(value)
    }
  }, [inView, motionValue, reduceMotion, value])

  useEffect(() => {
    const unsubscribe = springValue.on('change', setDisplayValue)
    return unsubscribe
  }, [springValue])

  return <motion.span ref={ref} className={`stat-value tabular-nums ${className}`} aria-label={value === null ? 'Not available' : `${prefix}${value.toLocaleString()}${suffix}`}>
    {value === null ? '—' : `${prefix}${displayValue.toFixed(decimals)}${suffix}`}
  </motion.span>
}

export default function StatsCounter({ label, value, icon, tone = 'blue', prefix = '', suffix = '', decimals = 0, duration = 1.5, className = '', hint, loading = false }: StatsCounterProps) {
  return <motion.div className="stat-card surface-card" initial={{ opacity: 0, y: 12 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, amount: 0.16 }} transition={{ duration: 0.32 }}>
    <div className={`stat-icon ${tone}`}>{icon}</div>
    <div className="stat-copy">
      <div className="stat-label">{label}</div>
      {loading ? <div className="skeleton stat-skeleton" aria-label={`Loading ${label}`} /> : <AnimatedNumber value={value} prefix={prefix} suffix={suffix} decimals={decimals} duration={duration} className={className} />}
      <div className="stat-hint">{hint ?? (value === null ? 'No estimate available yet' : 'From PocketLab API')}</div>
    </div>
  </motion.div>
}

