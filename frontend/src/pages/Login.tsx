import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, KeyRound, ShieldCheck, Workflow } from 'lucide-react'

import { login } from '../api/client'

const SEEDED_USERS = [
  { username: 'ammar', password: 'ammar123', role: 'sre' },
  { username: 'admin', password: 'admin123', role: 'admin' },
  { username: 'viewer', password: 'viewer123', role: 'viewer' },
]

export default function Login() {
  const [username, setUsername] = useState('ammar')
  const [password, setPassword] = useState('ammar123')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  const handleLogin = async (event: FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError('')
    try {
      await login(username, password)
      navigate('/')
    } catch {
      setError('Invalid username or password.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-shell">
      <div className="login-shell__panel">
        <section className="login-shell__brand">
          <div className="hero__eyebrow">ASHIA Control Plane</div>
          <h1 className="login-shell__title">
            Production-grade incident automation for modern infrastructure.
          </h1>
          <p className="login-shell__text">
            Monitor anomalies, inspect root-cause reasoning, approve higher-risk remediations, and
            review learned postmortems from a single operator-grade command surface.
          </p>
          <div className="login-shell__features">
            <div className="hint-card">
              <div className="hint-card__title">
                <ShieldCheck size={15} /> Observe
              </div>
              <div className="hint-card__body">
                Track live signals from Prometheus, Loki, and Jaeger in one place.
              </div>
            </div>
            <div className="hint-card">
              <div className="hint-card__title">
                <Workflow size={15} /> Approve
              </div>
              <div className="hint-card__body">
                Gate medium and high-risk actions through a complete audit trail.
              </div>
            </div>
          </div>
        </section>

        <section className="login-shell__form">
          <div className="login-shell__form-head">
            <KeyRound size={18} />
            <div>
              <div className="login-shell__form-title">Sign in</div>
              <div className="login-shell__form-subtitle">
                Use a seeded operator account or your stored credentials.
              </div>
            </div>
          </div>

          <form onSubmit={handleLogin} className="form-grid">
            <label className="field field--full">
              <span className="field__label">Username</span>
              <input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                className="field__input"
              />
            </label>
            <label className="field field--full">
              <span className="field__label">Password</span>
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="field__input"
              />
            </label>
            {error ? <div className="inline-message inline-message--danger">{error}</div> : null}
            <button
              className="button button--primary button--block"
              type="submit"
              disabled={loading}
            >
              {loading ? 'Signing in...' : 'Enter control plane'}
              {!loading ? <ArrowRight size={15} /> : null}
            </button>
          </form>

          <div className="stack-list">
            {SEEDED_USERS.map((user) => (
              <button
                key={user.username}
                className="list-card list-card--interactive"
                onClick={() => {
                  setUsername(user.username)
                  setPassword(user.password)
                }}
              >
                <div className="list-card__title">{user.username}</div>
                <div className="list-card__meta">
                  <span>{user.password}</span>
                  <span className="token">{user.role}</span>
                </div>
              </button>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
