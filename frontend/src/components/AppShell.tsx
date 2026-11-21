import { useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Activity, Bell, ChevronDown, CircleHelp, FlaskConical, FolderKanban, House, LogOut, Menu, Settings2, Smartphone, X } from 'lucide-react'
import { useAuth } from '../auth/useAuth'
import FloatingDock from './FloatingDock'

const sections = [
  { label: 'Workspace', items: [{ to: '/', title: 'Overview', icon: House }, { to: '/projects', title: 'Projects', icon: FolderKanban }, { to: '/devices', title: 'Device inventory', icon: Smartphone }] },
  { label: 'Test management', items: [{ to: '/profiles', title: 'Condition profiles', icon: Settings2 }, { to: '/scenarios', title: 'Scenario library', icon: Activity }, { to: '/test-lab', title: 'Test lab', icon: FlaskConical }] },
  { label: 'Analysis', items: [{ to: '/findings', title: 'Findings', icon: Bell }, { to: '/reports', title: 'Impact reports', icon: FolderKanban }, { to: '/help', title: 'Help & docs', icon: CircleHelp }] },
]
const titles: Record<string, string> = { '/': 'Overview', '/projects': 'Projects', '/devices': 'Device inventory', '/profiles': 'Condition profile builder', '/scenarios': 'Scenario library', '/test-lab': 'Test lab', '/findings': 'Findings', '/reports': 'Impact reports', '/help': 'Help & documentation' }

export default function AppShell() {
  const { logout } = useAuth(), navigate = useNavigate(), location = useLocation()
  const [menuOpen, setMenuOpen] = useState(false), [userMenu, setUserMenu] = useState(false)
  const title = location.pathname.startsWith('/runs/') ? location.pathname.endsWith('/live') ? 'Live test monitoring' : 'Test run details' : titles[location.pathname] || 'PocketLab'
  const signOut = async () => { try { await logout() } finally { navigate('/login', { replace: true }) } }
  return <div className="app-shell">
    <aside className={`sidebar ${menuOpen ? 'mobile-open' : ''}`} aria-label="Main navigation">
      <div className="brand-lockup"><div className="brand-mark"><span /></div><div><div className="brand-name">POCKETLAB</div><div className="brand-tagline">Test beyond the flagship.</div></div><button className="mobile-close icon-button" aria-label="Close menu" onClick={() => setMenuOpen(false)}><X size={19} /></button></div>
      <div className="workspace-switch"><div className="workspace-avatar">PL</div><div><strong>My workspace</strong><span>Developer plan</span></div><ChevronDown size={15} /></div>
      <nav className="side-nav">{sections.map((section) => <div className="nav-section" key={section.label}><div className="nav-section-title">{section.label}</div>{section.items.map(({ to, title: label, icon: Icon }) => <NavLink to={to} end={to === '/'} key={to} onClick={() => setMenuOpen(false)} className={({ isActive }) => `side-link ${isActive ? 'active' : ''}`}><Icon size={17} strokeWidth={1.8} /><span>{label}</span>{to === '/test-lab' && <span className="nav-new">NEW</span>}</NavLink>)}</div>)}</nav>
      <div className="sidebar-bottom"><div className="sidebar-note"><div className="online-dot" /><div><strong>Runner agents</strong><span>Live status is not exposed by API</span></div></div><div className="side-user"><div className="user-avatar">PL</div><div className="user-copy"><strong>Workspace user</strong><span>Authenticated session</span></div><button type="button" className="icon-button user-menu-toggle" aria-label="Account options" aria-expanded={userMenu} onClick={() => setUserMenu(!userMenu)}><ChevronDown size={15} /></button>{userMenu && <button type="button" className="signout-menu" onClick={signOut}><LogOut size={15} /> Sign out</button>}</div></div>
    </aside>
    {menuOpen && <button className="mobile-scrim" aria-label="Close navigation" onClick={() => setMenuOpen(false)} />}
    <div className="app-main"><header className="topbar"><div className="topbar-left"><button className="icon-button mobile-menu" aria-label="Open menu" onClick={() => setMenuOpen(true)}><Menu size={20} /></button><span className="topbar-context">Workspace</span><span className="breadcrumb-slash">/</span><span className="topbar-current">{title}</span></div><div className="topbar-right"><span className="environment-pill"><span /> API-backed workspace</span><button className="icon-button topbar-help" title="Help" onClick={() => navigate('/help')}><CircleHelp size={18} /></button><div className="topbar-avatar" aria-label="PocketLab account">PL</div></div></header><main className="page-content"><Outlet /></main></div>
    <FloatingDock />
  </div>
}
