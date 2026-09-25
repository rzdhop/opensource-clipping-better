import { NavLink } from 'react-router-dom'

// The two top-level modes of rzdhop AI (DEC-094). The URL is the source of
// truth for the current mode; localStorage only remembers the last one so
// that `/` reopens where the user left off.
export const MODE_KEY = 'rzc_mode'

export function modeFromPath(pathname) {
  if (pathname === '/story' || pathname.startsWith('/story/')) return 'story'
  if (pathname === '/clips' || pathname.startsWith('/clips/')) return 'clips'
  return null
}

export function readMode() {
  try {
    return localStorage.getItem(MODE_KEY) === 'story' ? 'story' : 'clips'
  } catch {
    return 'clips'
  }
}

export function rememberMode(mode) {
  try {
    localStorage.setItem(MODE_KEY, mode)
  } catch {
    // Storage can be unavailable (private mode). The switch still works; the
    // app just opens on Clips next time.
  }
}

export default function ModeSwitch({ compact = false }) {
  const link = ({ isActive }) => `mode-link${isActive ? ' active' : ''}`
  return (
    <nav className={`mode-switch${compact ? ' compact' : ''}`} aria-label="Mode">
      <NavLink to="/clips" className={link}>🎬 Clips</NavLink>
      <NavLink to="/story" className={link}>✨ AI Story</NavLink>
    </nav>
  )
}
