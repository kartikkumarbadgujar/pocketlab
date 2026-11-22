import { useReducedMotion } from 'motion/react'
import type { HTMLAttributes } from 'react'

type Props = HTMLAttributes<HTMLDivElement> & { message?: string; text?: string; compact?: boolean }

export default function KineticTextLoader({ message, text = 'Loading', compact = false, className, ...props }: Props) {
  const reduceMotion = useReducedMotion()
  const content = message ?? text
  const letters = content.split('')

  return <div className={`kinetic-loader ${compact ? 'compact' : ''} ${className ?? ''}`} role="status" aria-live="polite" aria-label={content} {...props}>
    <style>{`
      @keyframes ktl-dotMove {
        0%, 100% { transform: rotate(180deg) translate(-80px, -10px) rotate(-180deg); }
        50% { transform: rotate(0deg) translate(-81px, 10px) rotate(0deg); }
      }
      @keyframes ktl-letterStretch {
        0%, 100% { transform: scale(1, 0.35); transform-origin: 100% 75%; }
        8%, 28% { transform: scale(1, 1.4); transform-origin: 100% 67%; }
        37% { transform: scale(1, 0.875); transform-origin: 100% 75%; }
        46% { transform: scale(1, 1.03); transform-origin: 100% 75%; }
        50%, 97% { transform: scale(1); transform-origin: 100% 75%; }
      }
      @keyframes ktl-l-bounce {
        0%, 45%, 70%, 100% { transform: scaleY(1.11); }
        49% { transform: scaleY(0.31); }
        50% { transform: scaleY(0.16); }
        53% { transform: scaleY(0.63); }
        60% { transform: scaleY(1.275); }
        68% { transform: scaleY(1.04); }
      }
      .kinetic-loader-visual { position: relative; transform: scale(.76); transform-origin: center; }
      .kinetic-loader.compact .kinetic-loader-visual { transform: scale(.43); margin: -22px -38px; }
      .kinetic-loader-dot { position: absolute; z-index: 1; top: 40px; left: 85px; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
      .kinetic-loader-text { position: relative; margin: 0; color: currentColor; font: 300 3.75rem/1.1 Roboto, Arial, sans-serif; white-space: nowrap; }
      .kinetic-loader-letter { display: inline-block; position: relative; letter-spacing: 8px; }
      .kinetic-loader-letter.is-leading { transform-origin: 100% 70%; }
      .kinetic-loader-letter.is-stretch { transform-origin: 100% 70%; }
      @media (prefers-reduced-motion: reduce) {
        .kinetic-loader-dot, .kinetic-loader-letter { animation: none !important; }
      }
    `}</style>
    <div className="kinetic-loader-visual" aria-hidden="true">
      <div className="kinetic-loader-dot" style={{ animation: reduceMotion ? 'none' : 'ktl-dotMove 1800ms cubic-bezier(0.25,0.25,0.75,0.75) infinite' }} />
      <p className="kinetic-loader-text">
        {letters.map((char, index) => {
          const isLeading = index === 0 && char.toUpperCase() === 'L'
          const isStretch = index === 4 && char.toLowerCase() === 'i'
          const animation = isLeading
            ? 'ktl-l-bounce 1800ms cubic-bezier(0.25,0.25,0.75,0.75) infinite'
            : 'ktl-letterStretch 1800ms cubic-bezier(0.25,0.23,0.73,0.75) infinite'
          return <span key={`${index}-${char}`} className={`kinetic-loader-letter ${isLeading ? 'is-leading' : ''} ${isStretch ? 'is-stretch' : ''}`} style={isLeading || isStretch ? { animation: reduceMotion || (!isLeading && !isStretch) ? 'none' : animation } : undefined}>{char}</span>
        })}
      </p>
    </div>
  </div>
}

