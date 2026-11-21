import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { gsap } from 'gsap'
import { ArrowUpRight, Check, Radio, Smartphone, SunMedium, Wifi } from 'lucide-react'
import type { Profile } from '../types'
import KineticTextLoader from './KineticTextLoader'
import { errorMessage, profilesApi } from '../services/api'

const cards = [
  { key: 'NORMAL_BASELINE', title: 'Normal Baseline', description: 'A steady reference for comparing startup, performance, and network behavior.', icon: Radio, accent: 'blue' },
  { key: 'ENTRY_LEVEL_CONSTRAINED', title: 'Entry-Level Android', description: 'Constrained memory, storage, battery, and mobile network conditions.', icon: Smartphone, accent: 'violet' },
  { key: 'INTERMITTENT_NETWORK', title: 'Intermittent Network', description: 'Unstable connectivity with latency, jitter, and online/offline windows.', icon: Wifi, accent: 'cyan' },
  { key: 'THERMAL_STRESS_OBSERVATION', title: 'Thermal Stress Observation', description: 'Observe thermal APIs during a sustained workload on a compatible device.', icon: SunMedium, accent: 'amber' },
]

type Props = { profiles: Profile[]; loading: boolean; onConfigure: (versionId: number) => void }
export default function VerseCards({ profiles, loading, onConfigure }: Props) {
  const deck = useRef<HTMLDivElement>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const selectedQuery = useQuery({ queryKey: ['profiles', selectedId], queryFn: ({ signal }) => profilesApi.get(selectedId!, { signal }), enabled: selectedId !== null })
  const selected = selectedQuery.data
  useEffect(() => {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const ctx = gsap.context(() => { gsap.fromTo('.profile-card', { opacity: 0, y: 18, rotate: (i) => (i % 2 ? 1 : -1) }, { opacity: 1, y: 0, rotate: 0, duration: 0.45, stagger: 0.075, ease: 'power2.out', clearProps: 'transform' }) }, deck)
    return () => ctx.revert()
  }, [loading, profiles.length])
  const choose = (profile: Profile) => setSelectedId(profile.id)
  return <section className="profile-section" aria-labelledby="quick-profile-title">
    <div className="section-heading"><div><div className="eyebrow">QUICK TEST PROFILES</div><h2 id="quick-profile-title">Start with a real-world condition</h2><p>Choose a profile to inspect its settings. Launching a run is a separate step.</p></div><span className="section-link">{profiles.length} available</span></div>
    {loading ? <div className="profile-grid">{cards.map((item) => <div key={item.key} className="surface-card profile-card"><KineticTextLoader message="Loading profiles..." compact /></div>)}</div> : <div className="profile-grid" ref={deck}>
      {cards.map((item) => {
        const profile = profiles.find((entry) => entry.name.toUpperCase() === item.key)
        const Icon = item.icon
        return <button type="button" key={item.key} className={`surface-card profile-card ${selected?.profile.id === profile?.id ? 'selected' : ''}`} disabled={!profile} onClick={() => profile && choose(profile)} aria-pressed={Boolean(profile && selected?.profile.id === profile.id)} title={profile ? `Inspect ${item.title} conditions` : 'This profile is not available in the backend catalog'}>
          <span className={`profile-icon ${item.accent}`}><Icon size={19} /></span><span className="profile-title">{item.title}</span><span className="profile-desc">{profile?.description || item.description}</span>
          <span className="profile-foot">{profile ? <><span>Available profile</span>{selected?.profile.id === profile.id ? <Check size={16} /> : <ArrowUpRight size={16} />}</> : <span className="muted">Not in backend catalog</span>}</span>
        </button>
      })}
    </div>}
    {selectedQuery.error && <div className="form-error" role="alert">{errorMessage(selectedQuery.error)}</div>}
    {selectedQuery.isLoading && selectedId !== null && <div className="selected-profile surface-card"><KineticTextLoader message="Loading profile version..." compact /></div>}
    {selected && (() => { const latest = [...selected.versions].sort((a, b) => b.version - a.version)[0]; return latest ? <div className="selected-profile surface-card" aria-live="polite"><div><div className="eyebrow">SELECTED · VERSION {latest.version} · PROFILE #{selected.profile.id}</div><strong>{cards.find((item) => item.key === selected.profile.name.toUpperCase())?.title || selected.profile.name}</strong><p>{selected.profile.description}</p><div className="condition-chips">{Object.entries(latest.configuration_json).map(([key, value]) => <span key={key}>{key.split('.').at(-1)?.replaceAll('_', ' ')} <b>{String(value)}</b></span>)}</div></div><button className="button button-primary" onClick={() => onConfigure(latest.id)}>Configure test <ArrowUpRight size={16} /></button></div> : <div className="form-error" role="status">The selected profile has no saved version.</div> })()}
  </section>
}
