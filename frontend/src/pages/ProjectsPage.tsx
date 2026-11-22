import { useState } from 'react'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import { Box, FileUp, FolderKanban, PackageCheck, Pencil, Plus, Trash2, Upload, X } from 'lucide-react'
import type { Build, BuildUpload, Project } from '../types'
import { buildsApi, errorMessage, projectsApi } from '../services/api'
import PageHeader from '../components/PageHeader'
import { EmptyPanel, ErrorPanel, LoadingPanel } from '../components/Feedback'
import KineticTextLoader from '../components/KineticTextLoader'

const projectSchema = z.object({ name: z.string().trim().min(1, 'Enter a project name').max(160), application_package: z.string().optional(), description: z.string().optional() })
type ProjectForm = z.infer<typeof projectSchema>
const bytes = (count?: number | null) => count ? `${(count / 1024 / 1024).toFixed(1)} MB` : '—'

function BuildUpload({ project, onClose, onUploaded }: { project: Project; onClose: () => void; onUploaded: (build: BuildUpload) => void }) {
  const [file, setFile] = useState<File | null>(null), [packageName, setPackageName] = useState(project.application_package ?? ''), [version, setVersion] = useState(''), [progress, setProgress] = useState(0), [error, setError] = useState('')
  const upload = useMutation({ mutationFn: () => buildsApi.upload(project.id, file!, packageName, version || undefined, undefined, setProgress) })
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setError('')
    if (!file) return setError('Choose an APK file to upload.')
    if (!file.name.toLowerCase().endsWith('.apk')) return setError('The selected file must have an .apk extension.')
    if (file.size > 100 * 1024 * 1024) return setError('APK uploads are limited to 100 MB.')
    try { onUploaded(await upload.mutateAsync()) }
    catch (e) { setError(errorMessage(e)) }
  }
  const busy = upload.isPending
  return <div className="modal-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
    <section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="upload-title">
      <div className="modal-header"><div><div className="eyebrow">PROJECT BUILD</div><h2 id="upload-title">Upload APK</h2></div><button className="icon-button" onClick={onClose} aria-label="Close"><X size={18} /></button></div>
      <p className="modal-description">Uploads are stored in quarantine. PocketLab reads the package and version from the APK manifest; a build cannot be queued until it is approved.</p>
      <form onSubmit={submit} className="form-stack">
        <label className="upload-drop"><Upload size={20} /><strong>{file?.name ?? 'Choose an APK file'}</strong><span>{file ? bytes(file.size) : 'APK up to 100 MB'}</span><input type="file" accept=".apk,application/vnd.android.package-archive" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
        <label className="field-label" htmlFor="package-name">Expected package name <span className="muted">(optional)</span></label>
        <input id="package-name" value={packageName} onChange={(e) => setPackageName(e.target.value)} placeholder="Read from APK manifest" />
        <p className="field-help">Leave blank to detect it automatically. If entered, it must match the APK and the project package.</p>
        <label className="field-label" htmlFor="version-name">Version name <span className="muted">(optional override)</span></label>
        <input id="version-name" value={version} onChange={(e) => setVersion(e.target.value)} placeholder="Read from APK manifest" />
        {busy && <div className="upload-progress"><KineticTextLoader message="Uploading APK..." compact /><span>{progress}%</span></div>}
        {error && <div className="form-error" role="alert">{error}</div>}
        <div className="modal-actions"><button className="button button-secondary" type="button" onClick={onClose} disabled={busy}>Cancel</button><button className="button button-primary" type="submit" disabled={busy}><FileUp size={16} /> {busy ? 'Uploading...' : 'Upload to quarantine'}</button></div>
      </form>
    </section>
  </div>
}

export default function ProjectsPage() {
  const queryClient = useQueryClient()
  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: ({ signal }) => projectsApi.list({ signal }) })
  const projects = projectsQuery.data ?? []
  const projectBuildQueries = useQueries({ queries: projects.map((project) => ({ queryKey: ['builds', project.id], queryFn: ({ signal }) => buildsApi.list(project.id, { signal }) })) })
  const builds: Record<number, Build[]> = Object.fromEntries(projects.map((project, index) => [project.id, projectBuildQueries[index]?.data ?? []]))
  const [formOpen, setFormOpen] = useState(false), [editingProject, setEditingProject] = useState<Project | null>(null), [deleteProjectTarget, setDeleteProjectTarget] = useState<Project | null>(null), [uploadProject, setUploadProject] = useState<Project | null>(null), [uploadedBuild, setUploadedBuild] = useState<BuildUpload | null>(null), [formError, setFormError] = useState(''), [saving, setSaving] = useState(false)
  const { register, handleSubmit, reset, formState: { errors } } = useForm<ProjectForm>({ resolver: zodResolver(projectSchema), defaultValues: { name: '', application_package: '', description: '' } })
  const createProject = useMutation({ mutationFn: (body: Parameters<typeof projectsApi.create>[0]) => projectsApi.create(body), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['projects'] }) } })
  const updateProject = useMutation({ mutationFn: ({ id, body }: { id: number; body: Parameters<typeof projectsApi.update>[1] }) => projectsApi.update(id, body), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['projects'] }) } })
  const deleteProject = useMutation({ mutationFn: projectsApi.delete, onSuccess: async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ['projects'] }), queryClient.invalidateQueries({ queryKey: ['runs'] }), queryClient.invalidateQueries({ queryKey: ['dashboard'] })]); setDeleteProjectTarget(null) } })
  const openNewProject = () => { setEditingProject(null); setFormError(''); reset({ name: '', application_package: '', description: '' }); setFormOpen(true) }
  const openEditProject = (project: Project) => { setEditingProject(project); setFormError(''); reset({ name: project.name, application_package: project.application_package ?? '', description: project.description ?? '' }); setFormOpen(true) }
  const save = handleSubmit(async (values) => { setSaving(true); setFormError(''); const body = { name: values.name, description: values.description || undefined, application_package: values.application_package || undefined }; try { if (editingProject) await updateProject.mutateAsync({ id: editingProject.id, body }); else await createProject.mutateAsync(body); setFormOpen(false); setEditingProject(null); reset() } catch (e) { setFormError(errorMessage(e)) } finally { setSaving(false) } })
  return <div className="page-stack"><PageHeader eyebrow="WORKSPACE" title="Projects" description="Keep Android apps, APK builds, and test runs organized." action={<button className="button button-primary" onClick={openNewProject}><Plus size={17} /> New project</button>} />
    {projectsQuery.error ? <ErrorPanel message={errorMessage(projectsQuery.error)} retry={() => void projectsQuery.refetch()} /> : projectsQuery.isLoading ? <LoadingPanel message="Loading projects..." /> : projects.length === 0 ? <EmptyPanel title="No projects yet" description="Create a project to group your APK builds, test profiles, and run history." action={<button className="button button-primary" onClick={openNewProject}><Plus size={16} /> Create your first project</button>} /> : <div className="project-grid">{projects.map((project) => <article className="surface-card project-card" key={project.id}><div className="project-card-top"><div className="project-mark"><FolderKanban size={18} /></div><span className="project-id">PROJECT #{project.id}</span></div><h2>{project.name}</h2><p>{project.description || 'No project description added.'}</p><div className="project-package"><Box size={14} /><span>{project.application_package || 'Application package not set'}</span></div><div className="project-stats"><div><strong>{builds[project.id]?.length ?? '—'}</strong><span>Builds</span></div><div><strong>{builds[project.id]?.filter((build) => build.scan_status === 'approved').length ?? '—'}</strong><span>Approved</span></div><div><strong>{new Date(project.created_at).toLocaleDateString()}</strong><span>Created</span></div></div><div className="project-card-actions"><button className="button button-secondary button-small" onClick={() => setUploadProject(project)}><Upload size={14} /> Upload APK</button><button className="button button-secondary button-small" onClick={() => openEditProject(project)}><Pencil size={14} /> Edit</button><button className="icon-button" aria-label={`Delete ${project.name}`} onClick={() => setDeleteProjectTarget(project)}><Trash2 size={15} /></button></div>{Boolean(builds[project.id]?.length) && <div className="build-list">{builds[project.id].slice(0, 2).map((build) => <div className="build-row" key={build.id}><PackageCheck size={14} /><span>Build #{build.id} · {build.version_name || build.package_name} · SHA-256 {build.apk_sha256.slice(0, 10)}…</span><span className={`status-pill ${build.scan_status}`}>{build.scan_status}</span></div>)}</div>}</article>)}</div>}
    {formOpen && <div className="modal-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) { setFormOpen(false); setEditingProject(null) } }}><section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="project-modal-title"><div className="modal-header"><div><div className="eyebrow">{editingProject ? 'EDIT WORKSPACE ITEM' : 'NEW WORKSPACE ITEM'}</div><h2 id="project-modal-title">{editingProject ? 'Edit project' : 'Create a project'}</h2></div><button className="icon-button" onClick={() => { setFormOpen(false); setEditingProject(null) }} aria-label="Close"><X size={18} /></button></div><form className="form-stack" onSubmit={save}><label className="field-label" htmlFor="project-name">Project name</label><input id="project-name" placeholder="My Android app" {...register('name')} aria-invalid={Boolean(errors.name)} />{errors.name && <span className="field-error">{errors.name.message}</span>}<label className="field-label" htmlFor="project-package">Application package <span className="muted">(optional)</span></label><input id="project-package" placeholder="com.company.app" {...register('application_package')} /><label className="field-label" htmlFor="project-description">Description <span className="muted">(optional)</span></label><textarea id="project-description" rows={3} placeholder="What does this application do?" {...register('description')} />{formError && <div className="form-error" role="alert">{formError}</div>}<div className="modal-actions"><button type="button" className="button button-secondary" onClick={() => { setFormOpen(false); setEditingProject(null) }}>Cancel</button><button type="submit" className="button button-primary" disabled={saving}>{saving ? <KineticTextLoader message="Saving project..." compact /> : <>{editingProject ? 'Save changes' : <><Plus size={16} /> Create project</>}</>}</button></div></form></section></div>}
    {deleteProjectTarget && <div className="modal-backdrop" role="presentation"><section className="modal-card" role="alertdialog" aria-modal="true" aria-labelledby="delete-project-title"><div className="modal-header"><div><div className="eyebrow">REMOVE PROJECT</div><h2 id="delete-project-title">Delete {deleteProjectTarget.name}?</h2></div><button className="icon-button" aria-label="Close" onClick={() => { setDeleteProjectTarget(null); deleteProject.reset() }}><X size={18} /></button></div><p className="modal-description">This removes the project and its related run/build records from the database. Private APK objects may remain until the storage lifecycle removes them.</p>{deleteProject.error && <div className="form-error" role="alert">{errorMessage(deleteProject.error)}</div>}<div className="modal-actions"><button type="button" className="button button-secondary" onClick={() => { setDeleteProjectTarget(null); deleteProject.reset() }}>Keep project</button><button type="button" className="button button-danger" disabled={deleteProject.isPending} onClick={() => deleteProject.mutate(deleteProjectTarget.id)}>{deleteProject.isPending ? 'Deleting...' : 'Delete project'}</button></div></section></div>}
    {uploadedBuild && <div className="info-banner" role="status"><PackageCheck size={16} /><span>Build #{uploadedBuild.id} stored · SHA-256 {uploadedBuild.apk_sha256} · {uploadedBuild.size_bytes.toLocaleString()} bytes · {uploadedBuild.scan_status}. Uploading does not mean the APK was executed.</span><button className="icon-button" aria-label="Dismiss upload result" onClick={() => setUploadedBuild(null)}><X size={16} /></button></div>}
    {uploadProject && <BuildUpload project={uploadProject} onClose={() => setUploadProject(null)} onUploaded={(build) => { setUploadProject(null); setUploadedBuild(build); void queryClient.invalidateQueries({ queryKey: ['builds', uploadProject.id] }) }} />}
  </div>
}
