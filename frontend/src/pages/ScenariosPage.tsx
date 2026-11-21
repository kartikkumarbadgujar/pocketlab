import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowUpRight, ChevronRight, Clock3, GitBranch, Play, ScrollText } from 'lucide-react'
import { errorMessage, scenariosApi } from '../services/api'
import PageHeader from '../components/PageHeader'
import { EmptyPanel, ErrorPanel, LoadingPanel } from '../components/Feedback'

const group = (category: string) => category.charAt(0).toUpperCase() + category.slice(1)

export default function ScenariosPage() {
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const scenariosQuery = useQuery({ queryKey: ['scenarios'], queryFn: ({ signal }) => scenariosApi.list({ signal }) })
  const scenarios = scenariosQuery.data ?? []
  const selectedQuery = useQuery({ queryKey: ['scenarios', selectedId], queryFn: ({ signal }) => scenariosApi.get(selectedId!, { signal }), enabled: selectedId !== null })
  const selected = selectedQuery.data

  return <div className="page-stack">
    <PageHeader eyebrow="REPRODUCIBLE TESTS" title="Scenario library" description="Versioned actions that help teams reproduce reliability issues." action={<Link className="button button-primary" to="/test-lab"><Play size={15} /> Configure a run</Link>} />
    {scenariosQuery.error ? <ErrorPanel message={errorMessage(scenariosQuery.error)} retry={() => void scenariosQuery.refetch()} /> : scenariosQuery.isLoading ? <LoadingPanel message="Loading test scenarios..." /> : !scenarios.length ? <EmptyPanel title="No scenarios available" description="Seed the PocketLab scenario catalog to make reproducible test cases available." /> : <div className="scenario-layout">
      <div className="scenario-list">{scenarios.map((scenario, index) => <button type="button" className={`surface-card scenario-row ${selected?.scenario.id === scenario.id ? 'active' : ''}`} key={scenario.id} onClick={() => setSelectedId(scenario.id)} aria-pressed={selected?.scenario.id === scenario.id}>
        <span className="scenario-index">{String(index + 1).padStart(2, '0')}</span><span className="scenario-summary"><strong>{scenario.name}</strong><small>{scenario.description || 'No scenario description available.'}</small><span className="scenario-meta"><span>{group(scenario.scenario_category)}</span><span>Scenario #{scenario.id}</span></span></span><ChevronRight size={17} />
      </button>)}</div>
      <aside className="surface-card scenario-detail"><div className="eyebrow">SCENARIO DETAILS</div>
        {selectedQuery.isLoading ? <div className="detail-placeholder">Loading scenario versions...</div> : selectedQuery.error ? <ErrorPanel message={errorMessage(selectedQuery.error)} retry={() => void selectedQuery.refetch()} /> : !selected ? <div className="detail-placeholder"><ScrollText size={22} /><p>Choose a scenario to review its current versions.</p></div> : <>
          <h2>{selected.scenario.name}</h2><p>{selected.scenario.description}</p>
          <div className="scenario-detail-meta"><span><GitBranch size={15} /> {selected.versions.length} version{selected.versions.length !== 1 ? 's' : ''}</span><span><span className="category-dot" /> {group(selected.scenario.scenario_category)}</span></div>
          <div className="scenario-versions">{selected.versions.length ? selected.versions.slice().reverse().map((version) => <div className="scenario-version" key={version.id}><div><strong>Version {version.version}</strong><small>Created {new Date(version.created_at).toLocaleString()}</small></div><Clock3 size={15} /></div>) : <div className="detail-placeholder">No scenario versions have been published.</div>}</div>
          <div className="measurement-separation"><span>Scenario steps are versioned by the backend and delivered to the runner when a job is claimed.</span></div>
          <Link className="button button-secondary full-width" to={`/test-lab?scenarioVersionId=${selected.versions.at(-1)?.id ?? ''}`}>Use scenario in Test Lab <ArrowUpRight size={15} /></Link>
        </>}
      </aside>
    </div>}
  </div>
}
