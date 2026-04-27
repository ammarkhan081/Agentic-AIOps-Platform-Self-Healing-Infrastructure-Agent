import { Suspense, lazy, type ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import {
  Activity,
  BarChart2,
  Database,
  FlaskConical,
  HeartPulse,
  LogOut,
  Workflow,
} from 'lucide-react'

import { getHealth, getMe, logout } from './api/client'
import { Badge, EmptyState, ShellNavItem, Surface } from './components/ui'

const Dashboard = lazy(() => import('./pages/Dashboard'))
const FaultLab = lazy(() => import('./pages/FaultLab'))
const History = lazy(() => import('./pages/History'))
const IncidentDetail = lazy(() => import('./pages/IncidentDetail'))
const Login = lazy(() => import('./pages/Login'))
const Memory = lazy(() => import('./pages/Memory'))
const Reports = lazy(() => import('./pages/Reports'))

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      refetchInterval: 15000,
      staleTime: 5000,
      retry: 1,
    },
  },
})

function isAuthenticated() {
  return !!localStorage.getItem('access_token')
}

function Layout({ children }: { children: ReactNode }) {
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: getMe, retry: false })
  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
    refetchInterval: 15000,
  })
  const checks = health?.checks || {}
  const overallHealthy = health?.status === 'ok'

  return (
    <div className="shell">
      <aside className="shell__sidebar">
        <div className="shell__brand">
          <div className="shell__eyebrow">ASHIA Control Plane</div>
          <div className="shell__title">Incident Operations</div>
          <p className="shell__copy">
            Autonomous detection, diagnosis, remediation, validation, and learning for a live
            microservice estate.
          </p>
        </div>

        <div className="shell__status-card">
          <div className="shell__status-head">
            <HeartPulse size={16} />
            <span>Platform health</span>
          </div>
          <div className="shell__status-row">
            <span>{overallHealthy ? 'Operational' : 'Degraded'}</span>
            <Badge
              label={overallHealthy ? 'Healthy' : 'Review'}
              tone={overallHealthy ? 'success' : 'danger'}
            />
          </div>
          <div className="shell__status-grid">
            {[
              { label: 'Prometheus', ok: !!checks.prometheus },
              { label: 'Loki', ok: !!checks.loki },
              { label: 'Jaeger', ok: !!checks.jaeger },
              { label: 'Postgres', ok: !!checks.postgres },
              { label: 'Incident memory', ok: !!checks.incident_memory },
              { label: 'LangSmith', ok: !!checks.langsmith },
            ].map((item) => (
              <div key={item.label} className="shell__health-item">
                <span>{item.label}</span>
                <Badge
                  label={item.ok ? 'Online' : 'Offline'}
                  tone={item.ok ? 'success' : 'danger'}
                />
              </div>
            ))}
          </div>
        </div>

        <nav className="shell-nav">
          <ShellNavItem to="/" label="Overview" icon={<Activity size={16} />} />
          <ShellNavItem to="/history" label="Incidents" icon={<Workflow size={16} />} />
          <ShellNavItem to="/reports" label="Reports" icon={<BarChart2 size={16} />} />
          <ShellNavItem to="/memory" label="Memory" icon={<Database size={16} />} />
          <ShellNavItem to="/fault-lab" label="Fault Lab" icon={<FlaskConical size={16} />} />
        </nav>

        <div className="shell__footer">
          <div className="shell__user">
            <div>
              <div className="shell__user-name">{me?.name || 'Operator session'}</div>
              <div className="shell__user-meta">{me?.username || 'user'}</div>
            </div>
            <Badge label={me?.role || 'viewer'} tone="brand" />
          </div>
          <button
            className="button button--ghost button--block"
            onClick={async () => {
              await logout()
              window.location.href = '/login'
            }}
          >
            <LogOut size={15} />
            Sign out
          </button>
        </div>
      </aside>

      <main className="shell__main">
        <div className="shell__topbar">
          <div>
            <div className="shell__topbar-label">Production Incident Operations</div>
            <div className="shell__topbar-title">Autonomous AIOps command surface</div>
          </div>
          <Badge
            label={overallHealthy ? 'Ready for operations' : 'Investigate control plane'}
            tone={overallHealthy ? 'success' : 'warning'}
          />
        </div>
        <div className="shell__content">{children}</div>
      </main>
    </div>
  )
}

function ProtectedRoute({ children }: { children: ReactNode }) {
  if (!isAuthenticated()) return <Navigate to="/login" replace />
  return <Layout>{children}</Layout>
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <Suspense
          fallback={
            <div className="page-grid">
              <Surface>
                <EmptyState
                  title="Loading workspace"
                  detail="Preparing the ASHIA operator console."
                />
              </Surface>
            </div>
          }
        >
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <Dashboard />
                </ProtectedRoute>
              }
            />
            <Route
              path="/fault-lab"
              element={
                <ProtectedRoute>
                  <FaultLab />
                </ProtectedRoute>
              }
            />
            <Route
              path="/incidents/:id"
              element={
                <ProtectedRoute>
                  <IncidentDetail />
                </ProtectedRoute>
              }
            />
            <Route
              path="/history"
              element={
                <ProtectedRoute>
                  <History />
                </ProtectedRoute>
              }
            />
            <Route
              path="/reports"
              element={
                <ProtectedRoute>
                  <Reports />
                </ProtectedRoute>
              }
            />
            <Route
              path="/memory"
              element={
                <ProtectedRoute>
                  <Memory />
                </ProtectedRoute>
              }
            />
            <Route
              path="*"
              element={<Navigate to={isAuthenticated() ? '/' : '/login'} replace />}
            />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
