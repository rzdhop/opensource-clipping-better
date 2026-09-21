import { useState } from 'react'
import { checkToken, setToken } from '../api'

/**
 * Token entry.
 *
 * The token is printed by the backend on first start and stored server-side in
 * data/api_token. There is no account system and no password: one token, one
 * install, pasted once per device and kept in localStorage.
 */
export default function Login({ onAuthenticated }) {
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  const [checking, setChecking] = useState(false)

  const submit = async (event) => {
    event.preventDefault()
    const token = value.trim()
    if (!token) {
      setError('Paste the token the server printed at startup.')
      return
    }

    setChecking(true)
    setError('')
    try {
      // Verified before it is stored, so a typo says so here rather than
      // turning every later screen into an unexplained failure.
      const ok = await checkToken(token)
      if (!ok) {
        setError('That token was not accepted.')
        return
      }
      setToken(token)
      onAuthenticated()
    } catch {
      setError('Could not reach the server.')
    } finally {
      setChecking(false)
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={submit}>
        <h1>🎬 Sign in</h1>
        <p className="login-help">
          Paste the API token the server printed when it started. It is also in{' '}
          <code>data/api_token</code> on the machine running the backend.
        </p>

        <input
          type="password"
          className="login-input"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="API token"
          autoFocus
          autoComplete="current-password"
          spellCheck="false"
        />

        {error && <p className="login-error">{error}</p>}

        <button type="submit" className="btn btn-primary" disabled={checking}>
          {checking ? 'Checking…' : 'Continue'}
        </button>
      </form>
    </div>
  )
}
