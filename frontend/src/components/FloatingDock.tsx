import { NavLink } from 'react-router-dom'
import { Activity, CircleHelp, FileBarChart2, FlaskConical, FolderKanban, House, Smartphone } from 'lucide-react'

const links = [
  { to: '/', title: 'Home', icon: House }, { to: '/projects', title: 'Projects', icon: FolderKanban }, { to: '/devices', title: 'Devices', icon: Smartphone },
  { to: '/test-lab', title: 'Test Lab', icon: FlaskConical }, { to: '/findings', title: 'Findings', icon: Activity }, { to: '/reports', title: 'Reports', icon: FileBarChart2 },
]
export default function FloatingDock() {
  return <nav className="floating-dock" aria-label="Mobile navigation">{links.map(({ to, title, icon: Icon }) => <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => `dock-link ${isActive ? 'active' : ''}`} aria-label={title} title={title}><Icon size={19} aria-hidden="true" /><span>{title}</span></NavLink>)}<NavLink to="/help" className={({ isActive }) => `dock-link ${isActive ? 'active' : ''}`} aria-label="Help" title="Help"><CircleHelp size={19} /><span>Help</span></NavLink></nav>
}
