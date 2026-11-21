import { lazy, Suspense } from 'react'
import { Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { useAuth } from './auth/useAuth'
import AppShell from './components/AppShell'
import KineticTextLoader from './components/KineticTextLoader'

const LoginPage = lazy(() => import('./pages/LoginPage'))
const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const ProjectsPage = lazy(() => import('./pages/ProjectsPage'))
const DevicesPage = lazy(() => import('./pages/DevicesPage'))
const ProfilesPage = lazy(() => import('./pages/ProfilesPage'))
const ScenariosPage = lazy(() => import('./pages/ScenariosPage'))
const TestLabPage = lazy(() => import('./pages/TestLabPage'))
const LiveRunPage = lazy(() => import('./pages/LiveRunPage'))
const RunDetailsPage = lazy(() => import('./pages/RunDetailsPage'))
const FindingsPage = lazy(() => import('./pages/FindingsPage'))
const ReportsPage = lazy(() => import('./pages/ReportsPage'))
const HelpPage = lazy(() => import('./pages/HelpPage'))

function ProtectedRoute() { const { isAuthenticated } = useAuth(); return isAuthenticated ? <Outlet /> : <Navigate to="/login" replace /> }
export default function App() {
  return <AuthProvider><Suspense fallback={<div className="route-loading"><KineticTextLoader message="Loading PocketLab..." /></div>}><Routes><Route path="/login" element={<LoginPage />} /><Route element={<ProtectedRoute />}><Route element={<AppShell />}>
    <Route index element={<DashboardPage />} /><Route path="projects" element={<ProjectsPage />} /><Route path="devices" element={<DevicesPage />} /><Route path="profiles" element={<ProfilesPage />} /><Route path="scenarios" element={<ScenariosPage />} /><Route path="test-lab" element={<TestLabPage />} /><Route path="runs/:runId/live" element={<LiveRunPage />} /><Route path="runs/:runId" element={<RunDetailsPage />} /><Route path="findings" element={<FindingsPage />} /><Route path="reports" element={<ReportsPage />} /><Route path="help" element={<HelpPage />} />
  </Route></Route><Route path="*" element={<Navigate to="/" replace />} /></Routes></Suspense></AuthProvider>
}
