import { useEffect, useState } from 'react'
import { Routes, Route, NavLink, Navigate, useLocation, useParams } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import NewJob from './pages/NewJob'
import JobDetail from './pages/JobDetail'
import Login from './pages/Login'
import Settings from './pages/Settings'
import StoryHome from './pages/StoryHome'
import ModeSwitch, { modeFromPath, readMode, rememberMode } from './components/ModeSwitch'
import { checkToken, clearToken, getToken } from './api'

// `/` and any unknown path open the last mode used (DEC-094).
function ModeRedirect() {
  return <Navigate to={readMode() === 'story' ? '/story' : '/clips'} replace />
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
  // the server regenerates it if data/api_token is lost -- so its presence is
  // not proof of anything; and a server started with DISABLE_AUTH needs no
  // token at all, so its absence is not proof either (DEC-092). Ask first.
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

  // The URL decides the mode; storage only remembers it for next time.
  const mode = modeFromPath(location.pathname) || readMode()
  useEffect(() => {
    const current = modeFromPath(location.pathname)
    if (current) rememberMode(current)
  }, [location.pathname])

  const signOut = () => {
    clearToken()
    setAuth('out')
  }

  if (auth === 'checking') {
    return <div className="login-screen"><p>Checking access…</p></div>
  }
  if (auth === 'out') {
    return <Login onAuthenticated={() => setAuth('in')} />
  }

  return (
    <div className="app-layout">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-brand">
          <h1>🎬 rzdhop AI</h1>
          <p>clips &amp; AI stories, on free APIs</p>
          <ModeSwitch />
        </div>
        <nav className="sidebar-nav">
          {mode === 'story' ? (
            <NavLink to="/story" end className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
              <span className="icon">📖</span>
              Stories
            </NavLink>
          ) : (
            <>
              <NavLink to="/clips" end className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
                <span className="icon">📊</span>
                Dashboard
              </NavLink>
              <NavLink to="/clips/new" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
                <span className="icon">➕</span>
                New Job
              </NavLink>
            </>
          )}
          <NavLink to="/settings" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
            <span className="icon">⚙️</span>
            Settings
          </NavLink>
        </nav>
        <div style={{ padding: '12px 14px', borderTop: '1px solid var(--border-color)' }}>
          <button type="button" className="nav-link sign-out" onClick={signOut}>
            <span className="icon">🔒</span>
            Sign out
          </button>
          <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
            rzdhop AI
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="main-content">
        {/* The sidebar is hidden under 768 px; the mode switch and Settings stay reachable here. */}
        <div className="mobile-topbar">
          <span className="topbar-brand">🎬 rzdhop AI</span>
          <ModeSwitch compact />
          <NavLink to="/settings" className="topbar-link" aria-label="Settings">⚙️</NavLink>
        </div>
        <Routes>
          <Route path="/" element={<ModeRedirect />} />
          <Route path="/clips" element={<Dashboard />} />
          <Route path="/clips/new" element={<NewJob />} />
          <Route path="/clips/job/:jobId" element={<JobDetail />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/story" element={<StoryHome />} />
          <Route path="/story/*" element={<StoryHome />} />
          {/* The paths the product shipped with keep working through a redirect. */}
          <Route path="/new" element={<Navigate to="/clips/new" replace />} />
          <Route path="/job/:jobId" element={<LegacyJobRedirect />} />
          <Route path="*" element={<ModeRedirect />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
