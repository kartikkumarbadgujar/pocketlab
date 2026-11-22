import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { HugeiconsIcon } from '@hugeicons/react'
import { AiChat02Icon, Cancel01Icon, SentIcon } from '@hugeicons/core-free-icons'
import ThinkingLine from './ThinkingLine'
import { assistantApi, errorMessage, type ChatTurn } from '../services/api'
import './AssistantWidget.css'

type Entry = ChatTurn & { seconds?: number; error?: boolean }

const SUGGESTIONS = ['How do I test my app on a weak 3G network?', 'Explain the findings of this run', 'What should I fix first for low-RAM phones?']

// Minimal, safe Markdown: paragraphs, "-"/"*"/"1." lists, **bold** and `code`. Text is never injected as HTML.
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean).map((part, i) =>
    part.startsWith('**') ? <strong key={i}>{part.slice(2, -2)}</strong> : part.startsWith('`') ? <code key={i}>{part.slice(1, -1)}</code> : part)
}
function Rich({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  let list: string[] = []
  const flush = () => { if (list.length) { blocks.push(<ul key={blocks.length}>{list.map((item, i) => <li key={i}>{inline(item)}</li>)}</ul>); list = [] } }
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    const item = line.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/)
    if (item) { list.push(item[1]); continue }
    flush()
    if (!line) continue
    const heading = line.match(/^#{1,4}\s+(.*)$/)
    blocks.push(heading ? <p key={blocks.length}><strong>{inline(heading[1])}</strong></p> : <p key={blocks.length}>{inline(line)}</p>)
  }
  flush()
  return <>{blocks}</>
}

export default function AssistantWidget() {
  const location = useLocation()
  const runId = Number(location.pathname.match(/^\/runs\/(\d+)/)?.[1]) || undefined
  const [open, setOpen] = useState(false)
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [entries, setEntries] = useState<Entry[]>([])
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (!open || enabled !== null) return
    assistantApi.status().then((s) => setEnabled(s.enabled)).catch(() => setEnabled(false))
  }, [open, enabled])
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' }) }, [entries, pending])
  useEffect(() => { if (open) inputRef.current?.focus() }, [open])

  const send = async (text: string) => {
    const content = text.trim()
    if (!content || pending) return
    const history: Entry[] = [...entries, { role: 'user', content }]
    setEntries(history); setDraft(''); setPending(true)
    const started = performance.now()
    try {
      const turns = history.filter((e) => !e.error).slice(-12).map(({ role, content: c }) => ({ role, content: c }))
      const { reply } = await assistantApi.chat(turns, runId)
      setEntries((prev) => [...prev, { role: 'assistant', content: reply, seconds: (performance.now() - started) / 1000 }])
    } catch (error) {
      setEntries((prev) => [...prev, { role: 'assistant', content: errorMessage(error, 'The assistant could not answer.'), error: true }])
    } finally { setPending(false) }
  }
  const submit = (event: FormEvent) => { event.preventDefault(); void send(draft) }

  return <>
    {!open && <button type="button" className="assistant-fab" onClick={() => setOpen(true)} aria-label="Open PocketLab Assistant">
      <HugeiconsIcon icon={AiChat02Icon} size={22} strokeWidth={1.8} /><span>Ask AI</span>
    </button>}
    {open && <section className="assistant-panel" role="dialog" aria-label="PocketLab Assistant">
      <header className="assistant-head">
        <div className="assistant-avatar"><HugeiconsIcon icon={AiChat02Icon} size={18} strokeWidth={1.8} /></div>
        <div><strong>PocketLab Assistant</strong><span>{runId ? `Looking at run #${runId}` : 'Knows your projects and runs'}</span></div>
        <button type="button" className="icon-button" onClick={() => setOpen(false)} aria-label="Close assistant"><HugeiconsIcon icon={Cancel01Icon} size={18} /></button>
      </header>
      <div className="assistant-body" ref={scrollRef}>
        {enabled === false && <div className="assistant-note">The AI assistant is not configured on this server.</div>}
        {!entries.length && enabled !== false && <div className="assistant-empty">
          <p>Ask about testing Android apps on entry-level phones, your runs, or how to fix a finding.</p>
          <div className="assistant-suggestions">{SUGGESTIONS.filter((s) => runId || !s.includes('this run')).map((s) => <button type="button" key={s} onClick={() => void send(s)}>{s}</button>)}</div>
        </div>}
        {entries.map((entry, i) => entry.role === 'user'
          ? <div key={i} className="assistant-msg user">{entry.content}</div>
          : <div key={i} className={`assistant-msg bot${entry.error ? ' error' : ''}`}>
            {entry.seconds !== undefined && <ThinkingLine working={false} elapsed={entry.seconds} fontSize={12} color="var(--muted)" glyphColor="var(--violet)" collapsible={false} />}
            {entry.error ? <p role="alert">{entry.content}</p> : <Rich text={entry.content} />}
          </div>)}
        {pending && <div className="assistant-msg bot thinking">
          <ThinkingLine working label="Thinking…" fontSize={13} color="var(--ink)" glyphColor="var(--violet)" collapseOnSettle={false}
            steps={[runId ? `Reading run #${runId} findings and metrics` : 'Reading your projects and recent runs']} />
        </div>}
      </div>
      <form className="assistant-input" onSubmit={submit}>
        <textarea ref={inputRef} rows={1} value={draft} maxLength={4000} placeholder={enabled === false ? 'Assistant unavailable' : 'Ask PocketLab Assistant…'} disabled={enabled === false}
          onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(draft) } }} />
        <button type="submit" className="button button-primary" disabled={!draft.trim() || pending || enabled === false} aria-label="Send"><HugeiconsIcon icon={SentIcon} size={17} /></button>
      </form>
      <div className="assistant-foot">AI answers can be wrong. Measurements come only from your runs.</div>
    </section>}
  </>
}
