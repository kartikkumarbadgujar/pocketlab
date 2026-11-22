import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { gsap } from 'gsap'
import { Layers, X } from 'lucide-react'
import { useReducedMotion } from 'motion/react'
import type { ReactNode } from 'react'
import type { Profile } from '../types'
import KineticTextLoader from './KineticTextLoader'
import { errorMessage, profilesApi } from '../services/api'

export type VerseNavItem = { label: string; icon: ReactNode; badge?: boolean; isTrigger?: boolean; onClick?: () => void }
export type VerseCard = { label: string; description?: string; className?: string }
type Props = {
  profiles: Profile[]
  loading: boolean
  onConfigure: (versionId: number) => void
  navItems?: VerseNavItem[]
  cards?: (VerseCard | string)[]
  footerText?: string
  onOpenChange?: (open: boolean) => void
  onDeal?: (index: number) => void
  className?: string
}
const CLOSED = { x: 0, y: 640, rotation: 0, opacity: 0 }
const stackTransform = (depth: number) => ({ x: depth * 9, y: depth * 7, rotation: depth * 2, opacity: 1 })
const displayName = (name: string) => ({ NORMAL_BASELINE: 'Normal Baseline', ENTRY_LEVEL_CONSTRAINED: 'Entry-Level Android', INTERMITTENT_NETWORK: 'Intermittent Network', THERMAL_STRESS_OBSERVATION: 'Thermal Stress Observation' }[name.toUpperCase()] || name)
const awayTransform = (index: number) => {
  const side = index % 2 === 0 ? -1 : 1
  return { x: side * 420, y: -360, rotation: side * 30, opacity: 0 }
}

export default function VerseCards({ profiles, loading, onConfigure, navItems, cards: cardOverrides, footerText = 'Select the front card to inspect its settings.', onOpenChange, onDeal, className = '' }: Props) {
  const root = useRef<HTMLDivElement>(null)
  const initialized = useRef(false)
  const previousOpen = useRef(false)
  const [open, setOpen] = useState(false)
  const [dealt, setDealt] = useState(0)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const selectedQuery = useQuery({ queryKey: ['profiles', selectedId], queryFn: ({ signal }) => profilesApi.get(selectedId!, { signal }), enabled: selectedId !== null })
  const reduceMotion = useReducedMotion()
  const selected = selectedQuery.data
  const cards = cardOverrides
    ? cardOverrides.map((card, index) => {
      const profile = profiles[index]
      return { id: profile?.id, label: typeof card === 'string' ? card : card.label, description: typeof card === 'string' ? profile?.description : card.description ?? profile?.description, className: typeof card === 'string' ? '' : card.className ?? '' }
    })
    : profiles.map((profile) => ({ id: profile.id, label: displayName(profile.name), description: profile.description, className: '' }))
  const navigation = navItems ?? [{ label: 'Test profiles', icon: <Layers size={20} />, badge: profiles.length > 0, isTrigger: true }]

  useEffect(() => {
    const element = root.current
    if (!element) return
    const cardElements = gsap.utils.toArray<HTMLElement>(element.querySelectorAll('[data-verse-card]'))
    const closeButton = element.querySelector<HTMLElement>('[data-verse-close]')
    const footer = element.querySelector<HTMLElement>('[data-verse-footer]')
    const target = (index: number) => {
      if (!open) return CLOSED
      if (index < dealt) return awayTransform(index)
      return stackTransform(index - dealt)
    }

    if (!initialized.current || (!open && !previousOpen.current)) {
      cardElements.forEach((card, index) => gsap.set(card, target(index)))
      gsap.set(closeButton, { scale: 0 })
      gsap.set(footer, { opacity: 0 })
      initialized.current = true
      previousOpen.current = open
      return
    }

    const isToggle = previousOpen.current !== open
    previousOpen.current = open
    if (open) {
      gsap.to(cardElements, {
        x: (index) => target(index).x,
        y: (index) => target(index).y,
        rotation: (index) => target(index).rotation,
        opacity: (index) => target(index).opacity,
        duration: reduceMotion ? 0 : isToggle ? 0.9 : 0.6,
        ease: isToggle ? 'power4.inOut' : 'power3.out',
        stagger: isToggle ? { amount: 0.3, from: 'start' } : 0,
        overwrite: 'auto',
      })
      gsap.to(closeButton, { scale: 1, duration: reduceMotion ? 0 : 0.4, delay: !reduceMotion && isToggle ? 0.5 : 0, ease: 'back.out(1.7)', overwrite: 'auto' })
      gsap.to(footer, { opacity: 1, duration: reduceMotion ? 0 : 0.4, overwrite: 'auto' })
    } else {
      gsap.to(cardElements, {
        x: (index) => target(index).x,
        y: (index) => target(index).y,
        rotation: (index) => target(index).rotation,
        opacity: (index) => target(index).opacity,
        duration: reduceMotion ? 0 : isToggle ? 0.9 : 0.6,
        ease: 'power4.inOut',
        stagger: reduceMotion ? 0 : { amount: 0.3, from: 'end' },
        overwrite: 'auto',
      })
      gsap.to(closeButton, { scale: 0, duration: reduceMotion ? 0 : 0.3, overwrite: 'auto' })
      gsap.to(footer, { opacity: 0, duration: reduceMotion ? 0 : 0.3, overwrite: 'auto' })
    }
    return () => { gsap.killTweensOf([cardElements, closeButton, footer]) }
  }, [open, dealt, cards.length, reduceMotion])

  const closeDeck = () => { setOpen(false); setDealt(0); onOpenChange?.(false) }
  const dealFront = (index: number) => {
    if (!open || index !== dealt || index >= cards.length) return
    onDeal?.(index)
    if (cards[index].id !== undefined) setSelectedId(cards[index].id)
    setDealt((count) => count + 1)
  }

  return <section className={`profile-section ${className}`} aria-labelledby="quick-profile-title">
    <div className="section-heading"><div><div className="eyebrow">QUICK TEST PROFILES</div><h2 id="quick-profile-title">Start with a real-world condition</h2><p>Browse a profile, inspect its saved settings, then configure a test run.</p></div><span className="section-link">{loading ? 'Loading…' : `${profiles.length} available`}</span></div>
    <div className="verse-deck" ref={root}>
      <div className="verse-dock">
        {navigation.map((item, index) => <button type="button" key={`${item.label}-${index}`} className="verse-trigger" aria-label={item.label} aria-expanded={item.isTrigger ? open : undefined} disabled={loading && item.isTrigger} onClick={item.isTrigger ? () => { const nextOpen = !open; setDealt(0); setOpen(nextOpen); onOpenChange?.(nextOpen) } : item.onClick}>
          <span className="verse-trigger-icon">{item.icon}{item.badge && <i className="verse-trigger-badge" />}</span><span>{item.isTrigger && loading ? 'Loading profiles…' : item.label}</span>{item.isTrigger && <span className="verse-trigger-count">{profiles.length}</span>}
        </button>)}
      </div>
      <div className={`verse-stage ${open ? 'is-open' : ''}`} aria-label="Test profile cards" aria-hidden={!open}>
        {open && <button type="button" data-verse-close onClick={closeDeck} aria-label="Close profile cards" className="verse-close"><X size={19} /></button>}
        {!loading && cards.map((card, index) => <button
          type="button"
          key={card.id}
          data-verse-card
          className={`verse-card ${index % 4 === 0 ? 'blue' : index % 4 === 1 ? 'violet' : index % 4 === 2 ? 'cyan' : 'amber'} ${card.className}`}
          style={{ zIndex: index < dealt ? 0 : 100 - (index - dealt), pointerEvents: open && index === dealt ? 'auto' : 'none' }}
          onClick={() => dealFront(index)}
          aria-label={`Inspect ${card.label}`}
          tabIndex={open && index === dealt ? 0 : -1}
        ><span className="verse-card-kicker">POCKETLAB PROFILE</span><strong>{card.label}</strong>{card.description && <span className="verse-card-description">{card.description}</span>}<span className="verse-card-action">Select to inspect</span></button>)}
        {open && !loading && cards.length === 0 && <p className="verse-empty">No profiles are available in the backend catalog.</p>}
        <div data-verse-footer className="verse-footer">{dealt >= cards.length && cards.length ? 'That’s the whole deck — close to reset.' : footerText}</div>
      </div>
    </div>
    {loading && <div className="verse-loading surface-card"><KineticTextLoader message="Loading profiles..." compact /></div>}
    {selectedQuery.error && <div className="form-error" role="alert">{errorMessage(selectedQuery.error)}</div>}
    {selectedQuery.isLoading && selectedId !== null && <div className="selected-profile surface-card"><KineticTextLoader message="Loading profile version..." compact /></div>}
    {selected && (() => { const latest = [...selected.versions].sort((a, b) => b.version - a.version)[0]; return latest ? <div className="selected-profile surface-card" aria-live="polite"><div><div className="eyebrow">SELECTED · VERSION {latest.version} · PROFILE #{selected.profile.id}</div><strong>{selected.profile.name}</strong><p>{selected.profile.description}</p><div className="condition-chips">{Object.entries(latest.configuration_json).map(([key, value]) => <span key={key}>{key.split('.').at(-1)?.replaceAll('_', ' ')} <b>{String(value)}</b></span>)}</div></div><button className="button button-primary" onClick={() => onConfigure(latest.id)}>Configure test <span aria-hidden="true">↗</span></button></div> : <div className="form-error" role="status">The selected profile has no saved version.</div> })()}
  </section>
}

