import { motion, useReducedMotion } from 'motion/react'
import { LoaderCircle } from 'lucide-react'

export default function KineticTextLoader({ message, compact = false }: { message: string; compact?: boolean }) {
  const reduce = useReducedMotion()
  return <div className={`kinetic-loader ${compact ? 'compact' : ''}`} role="status" aria-live="polite">
    <LoaderCircle size={compact ? 16 : 20} className={reduce ? '' : 'spin-slow'} aria-hidden="true" />
    <motion.span key={message} initial={reduce ? false : { opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.18 }}>{message}</motion.span>
    <span className="sr-only">Please wait.</span>
  </div>
}
