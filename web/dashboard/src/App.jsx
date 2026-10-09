import { useEffect, useRef, useState } from 'react'
import { Routes, Route, NavLink, Navigate, useLocation, useParams } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import NewJob from './pages/NewJob'
import JobDetail from './pages/JobDetail'
import Login from './pages/Login'
import Settings from './pages/Settings'
import { checkToken, clearToken, getToken } from './api'
import { Clapperboard, LayoutDashboard, Lock, MenuIcon, Plus, Settings as SettingsIcon, X } from './ui/icons'

// Move focus into the page itself (the skip link's target): a plain #hash
// link would also change the router's location.
function skipToContent(event) {
  event.preventDefault()
  const main = document.getElementById('main-content')
  if (main) main.focus()
}

/**
 * The phone's navigation drawer: under 768 px the sidebar is hidden (CSS)
 * until the top bar's menu button opens it over the page. Escape, a click on
 * the backdrop or a navigation closes it; focus goes into it on open and back
 * to the menu button on close.
 */
function useNavDrawer(pathname) {
  const [open, setOpen] = useState(false)
  const buttonRef = useRef(null)
  const drawerRef = useRef(null)
  const wasOpen = useRef(false)

  useEffect(() => { setOpen(false) }, [pathname])

  useEffect(() => {
    if (open) {
      wasOpen.current = true
      const first = drawerRef.current && drawerRef.current.querySelector('a[href], button')
      if (first) first.focus()
      const onKeyDown = (event) => { if (event.key === 'Escape') setOpen(false) }
      document.addEventListener('keydown', onKeyDown)
      document.body.classList.add('nav-drawer-open')
      return () => {
        document.removeEventListener('keydown', onKeyDown)
        document.body.classList.remove('nav-drawer-open')
      }
    }
    if (wasOpen.current && buttonRef.current) buttonRef.current.focus()
    wasOpen.current = false
    return undefined
  }, [open])

  return { open, setOpen, buttonRef, drawerRef }
}

// The path the product shipped with: bookmarks and the PC helper's printed
// link still say /job/<id>.
function LegacyJobRedirect() {
  const { jobId } = useParams()
  return <Navigate to={`/clips/job/${jobId}`} replace />
}

function App() {
  const location = useLocation()

  // 'checking' until the server has answered. A stored token can be stale --
  // the server's API_TOKEN may have changed since it was pasted here -- so its
  // presence is not proof of anything; and a server with no API_TOKEN set
  // needs no token at all, so its absence is not proof either (DEC-092,
  // DEC-173). Ask first.
  const [auth, setAuth] = useState('checking')

  useEffect(() => {
    if (auth !== 'checking') return
    let cancelled = false
    checkToken(getToken())
      .then((ok) => {
        if (cancelled) return
        if (!ok && getToken()) clearToken()
        setAuth(ok ? 'in' : 'out')
      })
      .catch(() => { if (!cancelled) setAuth('out') })
    return () => { cancelled = true }
  }, [auth])

  const drawer = useNavDrawer(location.pathname)

  // Ask the server again rather than assume: one with no API_TOKEN answers
  // "in", so signing out there never reaches the Login screen (DEC-173).
  const signOut = () => {
    clearToken()
    setAuth('checking')
  }

  if (auth === 'checking') {
    return <div className="login-screen"><p>Checking access…</p></div>
  }
  if (auth === 'out') {
    return <Login onAuthenticated={() => setAuth('in')} />
  }

  return (
    <div className="app-layout">
      <a href="#main-content" className="skip-link" onClick={skipToContent}>Skip to content</a>
      {/* Sidebar; on a phone, the drawer the top bar's menu button opens. */}
      <aside
        id="app-sidebar"
        className={`sidebar${drawer.open ? ' sidebar-open' : ''}`}
        ref={drawer.drawerRef}
        aria-label="Navigation"
      >
        <div className="sidebar-brand">
          <h1><Clapperboard className="sidebar-brand-icon" size={18} aria-hidden="true" />rzdhop AI</h1>
          <p>AI clips, on free APIs</p>
          <button
            type="button"
            className="sidebar-close ui-icon-btn ui-icon-btn-ghost ui-icon-btn-md"
            aria-label="Close navigation"
            onClick={() => drawer.setOpen(false)}
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <nav className="sidebar-nav" aria-label="Main">
          <NavLink to="/clips" end className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
            <span className="icon"><LayoutDashboard size={18} aria-hidden="true" /></span>
            Dashboard
          </NavLink>
          <NavLink to="/clips/new" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
            <span className="icon"><Plus size={18} aria-hidden="true" /></span>
            New Job
          </NavLink>
          <NavLink to="/settings" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
            <span className="icon"><SettingsIcon size={18} aria-hidden="true" /></span>
            Settings
          </NavLink>
        </nav>
        <div style={{ padding: '12px 14px', borderTop: '1px solid var(--border-color)' }}>
          {/* Only a deployment with a token has anything to sign out of. */}
          {getToken() && (
            <button type="button" className="nav-link sign-out" onClick={signOut}>
              <span className="icon"><Lock size={18} aria-hidden="true" /></span>
              Sign out
            </button>
          )}
          <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
            rzdhop AI
          </div>
        </div>
      </aside>
      {drawer.open && (
        <div className="sidebar-backdrop" aria-hidden="true" onClick={() => drawer.setOpen(false)} />
      )}

      {/* Main Content */}
      <main className="main-content" id="main-content" tabIndex={-1}>
        {/* The sidebar is hidden under 768 px; the menu opens it as a drawer. */}
        <header className="mobile-topbar">
          <button
            type="button"
            ref={drawer.buttonRef}
            className="topbar-menu ui-icon-btn ui-icon-btn-ghost ui-icon-btn-md"
            aria-label="Open navigation"
            aria-expanded={drawer.open}
            aria-controls="app-sidebar"
            onClick={() => drawer.setOpen(true)}
          >
            <MenuIcon size={20} aria-hidden="true" />
          </button>
          <span className="topbar-brand"><Clapperboard className="sidebar-brand-icon" size={16} aria-hidden="true" />rzdhop AI</span>
        </header>
        <Routes>
          <Route path="/" element={<Navigate to="/clips" replace />} />
          <Route path="/clips" element={<Dashboard />} />
          <Route path="/clips/new" element={<NewJob />} />
          <Route path="/clips/job/:jobId" element={<JobDetail />} />
          <Route path="/settings" element={<Settings />} />
          {/* The paths the product shipped with keep working through a redirect. */}
          <Route path="/new" element={<Navigate to="/clips/new" replace />} />
          <Route path="/job/:jobId" element={<LegacyJobRedirect />} />
          <Route path="*" element={<Navigate to="/clips" replace />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
