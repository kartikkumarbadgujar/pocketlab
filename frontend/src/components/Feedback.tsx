import { AlertCircle, RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'
import KineticTextLoader from './KineticTextLoader'

export function LoadingPanel({ message = 'Loading projects...' }: { message?: string }) { return <div className="loading-panel surface-card"><KineticTextLoader message={message} /></div> }
export function ErrorPanel({ message, retry }: { message: string; retry?: () => void }) { return <div className="feedback-panel error-panel" role="alert"><AlertCircle size={19} /><div><strong>Could not load this data</strong><p>{message}</p>{retry && <button className="button button-secondary button-small" onClick={retry}><RefreshCw size={14} /> Try again</button>}</div></div> }
export function EmptyPanel({ title, description, action }: { title: string; description: string; action?: ReactNode }) { return <div className="empty-panel surface-card"><div className="empty-mark"><AlertCircle size={19} /></div><strong>{title}</strong><p>{description}</p>{action}</div> }
