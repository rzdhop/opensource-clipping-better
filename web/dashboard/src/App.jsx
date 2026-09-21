import { useEffect, useState } from 'react'
import { Routes, Route, NavLink, useLocation } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import NewJob from './pages/NewJob'
import JobDetail from './pages/JobDetail'
import Login from './pages/Login'
import Settings from './pages/Settings'
import { checkToken, clearToken, getToken } from './api'

function App() {
  const location = useLocation()

  // 'checking' until the stored token has been verified against the API. A
  // stored token can be stale -- the server regenerates it if data/api_token
  // is lost -- so its mere presence is not proof of anything.
  const [auth, setAuth] = useState(() => (getToken() ? 'checking' : 'out'))

  useEffect(() => {
    if (auth !== 'checking') return
    let cancelled = false
    checkToken(getToken())
      .then((ok) => {
        if (cancelled) return
        if (!ok) clearToken()
        setAuth(ok ? 'in' : 'out')
      })
      .catch(() => { if (!cancelled) setAuth('out') })
    return () => { cancelled = true }
  }, [auth])

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
          <h1>🎬 OSC Studio</h1>
          <p>AI Auto-Clipper v1.0.7</p>
        </div>
        <nav className="sidebar-nav">
          <NavLink to="/" end className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
            <span className="icon">📊</span>
            Dashboard
          </NavLink>
          <NavLink to="/new" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
            <span className="icon">➕</span>
            New Job
          </NavLink>
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
            OpenSource Clipping
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="main-content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/new" element={<NewJob />} />
          <Route path="/job/:jobId" element={<JobDetail />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
