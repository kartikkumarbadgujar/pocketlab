import { BookOpen, ExternalLink, ShieldCheck } from 'lucide-react'
import { api } from '../services/api'
import PageHeader from '../components/PageHeader'
import FaqAccordion from '../components/FaqAccordion'

export default function HelpPage() {
  const docsUrl = `${(api.defaults.baseURL || '').replace(/\/api\/v1\/?$/, '')}/docs`
  return <div className="page-stack"><PageHeader eyebrow="POCKETLAB GUIDE" title="Help & documentation" description="Understand profiles, device measurements, and how to reproduce a failure." />
    <div className="help-hero surface-card"><div className="help-hero-icon"><BookOpen size={20} /></div><div><h2>Build confidence beyond flagship devices</h2><p>PocketLab helps teams make Android applications more reliable under constrained hardware, low storage, power-saving modes, and unreliable mobile networks.</p></div><a href={docsUrl} target="_blank" rel="noreferrer" className="icon-button" aria-label="Open PocketLab API documentation"><ExternalLink size={17} /></a></div>
    <section className="surface-card faq-card"><div className="faq-heading"><div><div className="eyebrow">FREQUENTLY ASKED QUESTIONS</div><h2>How PocketLab works</h2></div><span className="faq-count">10 answers</span></div><FaqAccordion /></section>
    <div className="trust-note"><ShieldCheck size={16} /><span>Only runner-reported measurements are shown. Requested profile settings are labeled separately from observations.</span></div>
  </div>
}
