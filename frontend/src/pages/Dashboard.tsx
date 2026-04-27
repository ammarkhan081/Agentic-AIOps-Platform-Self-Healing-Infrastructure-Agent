import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Gauge,
  Play,
  Sparkles,
  Wrench,
} from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import {
  getControlPlaneMetrics,
  getDemoFaultStatus,
  getMe,
  getMemoryIncidents,
  getMetricsSummary,
  injectDemoFault,
  listIncidents,
  resetDemoFaults,
  triggerIncident,
} from '../api/client'
import { Badge, EmptyState, MetricCard, Surface } from '../components/ui'

type FaultPayload = {
  fault_type: string
  service?: string
  cycles?: number
  duration?: number
  rate?: number
  ratio?: number
  connections?: number
  delay_seconds?: number
  replicas?: number
  target_version?: string
}

function faultDefaultPayload(faultType: string, service: string): FaultPayload {
  const base: FaultPayload = {
    fault_type: faultType,
    service,
    cycles: 10,
    duration: 30,
    rate: 0.7,
    ratio: 0.95,
    connections: 95,
    delay_seconds: 2.5,
    target_version: 'v0.9.0',
  }
  if (faultType === 'db_exhaustion') base.service = 'user-service'
  if (faultType === 'cpu_spike') base.service = 'order-service'
  if (faultType === 'cascade_failure') {
    base.service = 'order-service'
    base.cycles = 8
    base.connections = 92
    base.ratio = 0.96
    base.delay_seconds = 3
  }
  if (faultType === 'rollback') base.service = 'order-service'
  return base
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [service, setService] = useState('order-service')
  const [notes, setNotes] = useState('Manual operator trigger from dashboard')
  const [faultType, setFaultType] = useState('memory_leak')
  const [busy, setBusy] = useState(false)
  const [faultBusy, setFaultBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: getMe, retry: false })
  const { data: incidentData, refetch: refetchIncidents } = useQuery({
    queryKey: ['incidents'],
    queryFn: listIncidents,
    refetchInterval: 15000,
  })
  const { data: metricsSummary } = useQuery({
    queryKey: ['metrics-summary'],
    queryFn: getMetricsSummary,
    refetchInterval: 15000,
  })
  const { data: controlMetrics } = useQuery({
    queryKey: ['control-metrics'],
    queryFn: getControlPlaneMetrics,
    refetchInterval: 15000,
  })
  const { data: memoryData } = useQuery({
    queryKey: ['memory-overview'],
    queryFn: () => getMemoryIncidents(6),
    refetchInterval: 20000,
  })
  const { data: faultStatus } = useQuery({
    queryKey: ['fault-status'],
    queryFn: getDemoFaultStatus,
    refetchInterval: 10000,
  })

  const canOperate = me?.role === 'admin' || me?.role === 'sre'
  const incidents = useMemo(() => incidentData?.incidents || [], [incidentData])
  const memoryIncidents = useMemo(() => memoryData?.incidents || [], [memoryData])
  const cp = controlMetrics?.metrics || {}
  const metricSummary = metricsSummary?.metrics || {}
  const serviceFaults = faultStatus?.services || {}

  const summary = useMemo(() => {
    const active = incidents.filter((item) => item.status === 'active').length
    const paused = incidents.filter((item) => item.status === 'paused').length
    const resolved = incidents.filter((item) => item.status === 'resolved').length
    const recoveries = incidents
      .map((item) => item.time_to_recovery)
      .filter((item): item is number => typeof item === 'number')
    const avgRecovery = recoveries.length
      ? `${Math.round(recoveries.reduce((sum, item) => sum + item, 0) / recoveries.length)}s`
      : '-'
    return { active, paused, resolved, avgRecovery }
  }, [incidents])

  const incidentChartData = [
    { name: 'Active', value: summary.active, color: '#2563eb' },
    { name: 'Paused', value: summary.paused, color: '#d97706' },
    { name: 'Resolved', value: summary.resolved, color: '#15803d' },
    {
      name: 'Other',
      value: Math.max(incidents.length - summary.active - summary.paused - summary.resolved, 0),
      color: '#64748b',
    },
  ]

  const metricChartData = Object.entries(metricSummary)
    .slice(0, 6)
    .map(([name, payload]: any) => ({
      name,
      value: typeof payload?.value === 'number' ? Number(payload.value.toFixed(2)) : 0,
    }))

  const pendingIncident =
    incidents.find((item) => item.status === 'paused') ||
    incidents.find((item) => item.severity === 'CRITICAL')

  const handleTrigger = async () => {
    setBusy(true)
    setError('')
    try {
      const result = await triggerIncident({ service, notes })
      navigate(`/incidents/${result.incident_id}`)
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setBusy(false)
    }
  }

  const handleFault = async () => {
    setFaultBusy(true)
    setError('')
    setMessage('')
    try {
      const payload = faultDefaultPayload(faultType, service)
      const result = await injectDemoFault(payload)
      setMessage(
        result.message ||
          `${result.fault_type || faultType} queued for ${result.service || payload.service}`,
      )
      refetchIncidents()
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setFaultBusy(false)
    }
  }

  const handleFaultReset = async () => {
    setFaultBusy(true)
    setError('')
    setMessage('')
    try {
      await resetDemoFaults()
      setMessage('All target-system faults were reset successfully.')
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setFaultBusy(false)
    }
  }

  return (
    <div className="page-grid">
      <section className="hero">
        <div className="hero__copy">
          <div className="hero__eyebrow">Autonomous Operations</div>
          <h1 className="hero__title">
            Drive, observe, and validate the full incident response loop.
          </h1>
          <p className="hero__text">
            ASHIA coordinates anomaly detection, RCA, remediation, verification, and memory-backed
            learning across a live demo estate. Use this surface to trigger incidents, inject
            faults, and monitor operator checkpoints.
          </p>
        </div>
        <div className="hero__actions">
          <div className="form-grid">
            <label className="field">
              <span className="field__label">Target service</span>
              <select
                value={service}
                onChange={(event) => setService(event.target.value)}
                className="field__input"
              >
                <option value="order-service">order-service</option>
                <option value="user-service">user-service</option>
                <option value="api-gateway">api-gateway</option>
              </select>
            </label>
            <label className="field field--full">
              <span className="field__label">Operator notes</span>
              <textarea
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                className="field__input field__input--textarea"
              />
            </label>
          </div>
          <div className="button-row">
            <button
              className="button button--primary"
              onClick={handleTrigger}
              disabled={busy || !canOperate}
            >
              <Play size={15} />
              {busy ? 'Triggering...' : 'Trigger pipeline'}
            </button>
            <button className="button button--ghost" onClick={() => navigate('/history')}>
              View incidents
              <ArrowRight size={15} />
            </button>
          </div>
        </div>
      </section>

      <section className="metrics-grid">
        <MetricCard
          label="Incident records"
          value={incidents.length}
          note="Persisted sessions across the control plane"
          tone="brand"
          icon={<Activity size={16} />}
        />
        <MetricCard
          label="Active runs"
          value={summary.active}
          note="Pipelines currently executing"
          tone="info"
          icon={<Gauge size={16} />}
        />
        <MetricCard
          label="Awaiting HITL"
          value={summary.paused}
          note="Approval checkpoints still open"
          tone="warning"
          icon={<AlertTriangle size={16} />}
        />
        <MetricCard
          label="Resolved"
          value={summary.resolved}
          note="Closed with postmortems"
          tone="success"
          icon={<CheckCircle2 size={16} />}
        />
        <MetricCard
          label="Average recovery"
          value={summary.avgRecovery}
          note="Observed across completed sessions"
          icon={<Sparkles size={16} />}
        />
      </section>

      <div className="two-column">
        <Surface
          title="Control-plane summary"
          subtitle="Self-observability and live incident posture"
        >
          <div className="metrics-grid metrics-grid--compact">
            <MetricCard
              label="Detected"
              value={
                typeof cp.incidents_detected_total === 'number'
                  ? cp.incidents_detected_total.toFixed(0)
                  : '0'
              }
              note="Incident detections by ASHIA"
              tone="brand"
            />
            <MetricCard
              label="Resolved"
              value={
                typeof cp.incidents_resolved_total === 'number'
                  ? cp.incidents_resolved_total.toFixed(0)
                  : '0'
              }
              note="Resolved by automation loop"
              tone="success"
            />
            <MetricCard
              label="HITL interventions"
              value={
                typeof cp.hitl_interventions_total === 'number'
                  ? cp.hitl_interventions_total.toFixed(0)
                  : '0'
              }
              note="Human checkpoints opened"
              tone="warning"
            />
            <MetricCard
              label="Rolling TTR"
              value={
                typeof cp.avg_time_to_recovery_seconds === 'number'
                  ? `${cp.avg_time_to_recovery_seconds.toFixed(0)}s`
                  : '-'
              }
              note="Average control-plane recovery time"
            />
          </div>
          <div className="chart-card">
            <div className="chart-card__title">Incident status distribution</div>
            <div className="chart-card__body">
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={incidentChartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(148, 163, 184, 0.2)" />
                  <XAxis dataKey="name" tick={{ fill: '#94a3b8', fontSize: 12 }} />
                  <YAxis tick={{ fill: '#94a3b8', fontSize: 12 }} allowDecimals={false} />
                  <Tooltip cursor={{ fill: 'rgba(15, 23, 42, 0.04)' }} />
                  <Bar dataKey="value" radius={[8, 8, 0, 0]}>
                    {incidentChartData.map((entry) => (
                      <Cell key={entry.name} fill={entry.color} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </Surface>

        <Surface
          title="Priority queue"
          subtitle="What most likely needs operator attention right now"
          actions={
            pendingIncident ? (
              <Badge
                label={pendingIncident.status}
                tone={pendingIncident.status === 'paused' ? 'warning' : 'danger'}
              />
            ) : undefined
          }
        >
          {pendingIncident ? (
            <button
              className="list-card list-card--interactive"
              onClick={() => navigate(`/incidents/${pendingIncident.incident_id}`)}
            >
              <div className="list-card__title">{pendingIncident.service || 'Unknown service'}</div>
              <div className="list-card__detail">
                Incident {pendingIncident.incident_id.slice(0, 8)}
              </div>
              <div className="list-card__meta">
                <Badge
                  label={pendingIncident.severity || 'active'}
                  tone={pendingIncident.severity === 'CRITICAL' ? 'danger' : 'warning'}
                />
                <span>{pendingIncident.retry_count || 0} retries</span>
                <span>
                  {pendingIncident.time_to_recovery
                    ? `${pendingIncident.time_to_recovery.toFixed(1)}s recovery`
                    : 'Recovery pending'}
                </span>
              </div>
            </button>
          ) : (
            <EmptyState
              title="No critical or paused incidents"
              detail="The queue is clear. Inject a fault or trigger a manual run to exercise the platform."
            />
          )}
        </Surface>
      </div>

      <div className="three-column">
        <Surface title="Fault console" subtitle="Fast path for common demo scenarios">
          <div className="form-grid">
            <label className="field field--full">
              <span className="field__label">Fault type</span>
              <select
                value={faultType}
                onChange={(event) => setFaultType(event.target.value)}
                className="field__input"
              >
                <option value="memory_leak">memory_leak</option>
                <option value="cpu_spike">cpu_spike</option>
                <option value="db_exhaustion">db_exhaustion</option>
                <option value="slow_query">slow_query</option>
                <option value="error_rate">error_rate</option>
                <option value="redis_overflow">redis_overflow</option>
                <option value="cascade_failure">cascade_failure</option>
                <option value="rollback">rollback</option>
              </select>
            </label>
          </div>
          <div className="button-row">
            <button
              className="button button--secondary"
              onClick={handleFault}
              disabled={faultBusy || !canOperate}
            >
              <Wrench size={15} />
              {faultBusy ? 'Working...' : 'Inject fault'}
            </button>
            <button
              className="button button--ghost"
              onClick={handleFaultReset}
              disabled={faultBusy || !canOperate}
            >
              Reset faults
            </button>
          </div>
          {message ? <div className="inline-message inline-message--success">{message}</div> : null}
          {error ? <div className="inline-message inline-message--danger">{error}</div> : null}
        </Surface>

        <Surface
          title="Monitored metrics"
          subtitle="Current values from the Prometheus summary endpoint"
        >
          {metricChartData.length ? (
            <div className="chart-card chart-card--flush">
              <div className="chart-card__body">
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={metricChartData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(148, 163, 184, 0.2)" />
                    <XAxis
                      dataKey="name"
                      tick={{ fill: '#94a3b8', fontSize: 11 }}
                      interval={0}
                      angle={-12}
                      textAnchor="end"
                      height={68}
                    />
                    <YAxis tick={{ fill: '#94a3b8', fontSize: 12 }} />
                    <Tooltip cursor={{ fill: 'rgba(15, 23, 42, 0.04)' }} />
                    <Bar dataKey="value" fill="#2dd4bf" radius={[8, 8, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          ) : (
            <EmptyState
              title="Metric summary unavailable"
              detail="The dashboard will render values here once the metrics endpoint responds."
            />
          )}
        </Surface>

        <Surface
          title="Memory fabric"
          subtitle="Recently persisted postmortems and semantic memory status"
        >
          {memoryIncidents.length ? (
            <div className="stack-list">
              {memoryIncidents.slice(0, 4).map((item) => (
                <button
                  key={item.incident_id}
                  className="list-card list-card--interactive"
                  onClick={() => navigate(`/incidents/${item.incident_id}`)}
                >
                  <div className="list-card__title">{item.service}</div>
                  <div className="list-card__detail">
                    {(item.postmortem?.root_cause_confirmed || 'Postmortem available').slice(0, 88)}
                  </div>
                  <div className="list-card__meta">
                    <Badge
                      label={item.outcome || item.status || 'stored'}
                      tone={item.outcome === 'resolved' ? 'success' : 'warning'}
                    />
                    <span>
                      {item.postmortem?.time_to_recovery_seconds
                        ? `${item.postmortem.time_to_recovery_seconds.toFixed(1)}s`
                        : 'Recovery n/a'}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <EmptyState
              title="No stored postmortems yet"
              detail="Resolved incidents will start appearing here once the learning agent closes a session."
            />
          )}
        </Surface>
      </div>

      <div className="two-column">
        <Surface title="Recent incidents" subtitle="Live operational history">
          {incidents.length ? (
            <div className="stack-list">
              {incidents.slice(0, 6).map((incident) => (
                <button
                  key={incident.incident_id}
                  className="list-card list-card--interactive"
                  onClick={() => navigate(`/incidents/${incident.incident_id}`)}
                >
                  <div className="list-card__title">{incident.service || 'Unknown service'}</div>
                  <div className="list-card__detail">
                    Incident {incident.incident_id.slice(0, 8)} ·{' '}
                    {new Date(incident.created_at).toLocaleString()}
                  </div>
                  <div className="list-card__meta">
                    <Badge
                      label={incident.status}
                      tone={
                        incident.status === 'resolved'
                          ? 'success'
                          : incident.status === 'paused'
                            ? 'warning'
                            : incident.status === 'failed' || incident.status === 'escalated'
                              ? 'danger'
                              : 'info'
                      }
                    />
                    {incident.severity ? (
                      <Badge
                        label={incident.severity}
                        tone={incident.severity === 'CRITICAL' ? 'danger' : 'warning'}
                      />
                    ) : null}
                    <span>{incident.retry_count || 0} retries</span>
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <EmptyState
              title="No incidents recorded"
              detail="Trigger an incident pipeline or inject a fault to create the first execution trace."
            />
          )}
        </Surface>

        <Surface
          title="Target system status"
          subtitle="Current fault state reported by each service"
        >
          {Object.entries(serviceFaults).length ? (
            <div className="stack-list">
              {Object.entries(serviceFaults).map(([name, payload]: any) => (
                <div key={name} className="list-card">
                  <div className="list-card__title">{name}</div>
                  <div className="list-card__meta">
                    <Badge
                      label={payload.ok ? 'Reachable' : 'Unreachable'}
                      tone={payload.ok ? 'success' : 'danger'}
                    />
                  </div>
                  <div className="token-row">
                    {Object.entries(payload.data || {})
                      .slice(0, 6)
                      .map(([key, value]: any) => (
                        <span key={key} className="token">
                          {key}: {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                        </span>
                      ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              title="Fault state unavailable"
              detail="The target services will publish fault status here once they are reachable."
            />
          )}
        </Surface>
      </div>
    </div>
  )
}
