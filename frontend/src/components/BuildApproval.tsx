import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ShieldCheck } from 'lucide-react'
import { buildsApi, errorMessage } from '../services/api'
import type { Build } from '../types'
import KineticTextLoader from './KineticTextLoader'

// Lets the project owner approve a quarantined build after confirming they are authorized to test it.
export default function BuildApproval({ projectId, builds, onApproved }: { projectId: number; builds: Build[]; onApproved?: (buildId: number) => void }) {
  const queryClient = useQueryClient()
  const pending = builds.filter((b) => b.scan_status === 'quarantined')
  const [buildId, setBuildId] = useState<number | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const selected = buildId ?? pending[0]?.id ?? null
  const approve = useMutation({
    mutationFn: (id: number) => buildsApi.approve(projectId, id, 'Approved by the project owner in the dashboard'),
    onSuccess: async (result) => {
      setConfirmed(false)
      await queryClient.invalidateQueries({ queryKey: ['builds', projectId] })
      onApproved?.(result.id)
    },
  })
  if (!pending.length) return null
  return <div className="build-approval" role="group" aria-label="Approve a quarantined build">
    <div className="build-approval-head"><ShieldCheck size={17} /><strong>Approve a quarantined APK</strong></div>
    <p>Uploads are quarantined until approved. Approving lets a test run install and run this APK on an Android device.</p>
    {pending.length > 1 && <select aria-label="Build to approve" value={selected ?? ''} onChange={(e) => setBuildId(Number(e.target.value))}>
      {pending.map((b) => <option key={b.id} value={b.id}>Build #{b.id} · {b.version_name || b.package_name}</option>)}
    </select>}
    {pending.length === 1 && <div className="build-approval-target">Build #{pending[0].id} · {pending[0].package_name} · SHA-256 {pending[0].apk_sha256.slice(0, 12)}…</div>}
    <label className="checkbox-row"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /><span>I own this app or am authorized to test it</span></label>
    {approve.error && <div className="form-error" role="alert">{errorMessage(approve.error)}</div>}
    <button type="button" className="button button-primary" disabled={!confirmed || selected === null || approve.isPending} onClick={() => selected !== null && approve.mutate(selected)}>
      {approve.isPending ? <KineticTextLoader message="Verifying and approving..." compact /> : <><ShieldCheck size={15} /> Approve for testing</>}
    </button>
  </div>
}
