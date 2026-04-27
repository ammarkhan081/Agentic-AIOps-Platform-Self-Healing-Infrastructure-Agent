import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Activity, CheckCircle2, Clock3, Filter } from 'lucide-react'

import { listIncidentsFiltered } from '../api/client'
import { Badge, EmptyState, MetricCard, Surface } from '../components/ui'

export default function History() {
  const navigate = useNavigate()
  const [statusFilter, setStatusFilter] = useState('')
  const [severityFilter, setSeverityFilter] = useState('')
  const [serviceFilter, setServiceFilter] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')

  const { data } = useQuery({
    queryKey: ['incident-history', statusFilter, severityFilter, serviceFilter, dateFrom, dateTo],
    queryFn: () =>
      listIncidentsFiltered({
        status: statusFilter || undefined,
        severity: severityFilter || undefined,
        service: serviceFilter || undefined,
        date_from: dateFrom ? `${dateFrom}T00:00:00` : undefined,
        date_to: dateTo ? `${dateTo}T23:59:59` : undefined,
      }),
  })

  const incidents = useMemo(() => data?.incidents || [], [data])
  const summary = useMemo(() => {
    const resolved = incidents.filter((item) => item.status === 'resolved').length
    const active = incidents.filter((item) => item.status === 'active').length
    const recoveryValues = incidents
      .map((item) => item.time_to_recovery)
      .filter((item): item is number => typeof item === 'number')
    return {
      resolved,
      active,
      avg: recoveryValues.length
        ? `${Math.round(recoveryValues.reduce((sum, item) => sum + item, 0) / recoveryValues.length)}s`
        : '-',
    }
  }, [incidents])

  return (
    <div className="page-grid">
      <section className="hero hero--compact">
        <div className="hero__copy">
          <div className="hero__eyebrow">Incident Archive</div>
          <h1 className="hero__title">Review the operational history of the control plane.</h1>
          <p className="hero__text">
            Filter by status, severity, service, and date range to inspect historical sessions and
            jump straight into the full execution detail for any incident.
          </p>
        </div>
      </section>

      <section className="metrics-grid">
        <MetricCard
          label="Visible incidents"
          value={incidents.length}
          icon={<Activity size={16} />}
          tone="brand"
        />
        <MetricCard label="Active" value={summary.active} icon={<Filter size={16} />} tone="info" />
        <MetricCard
          label="Resolved"
          value={summary.resolved}
          icon={<CheckCircle2 size={16} />}
          tone="success"
        />
        <MetricCard label="Average recovery" value={summary.avg} icon={<Clock3 size={16} />} />
      </section>

      <Surface title="Filters" subtitle="Refine the incident set used by the archive table">
        <div className="form-grid form-grid--five">
          <label className="field">
            <span className="field__label">Status</span>
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              className="field__input"
            >
              <option value="">All</option>
              <option value="active">active</option>
              <option value="paused">paused</option>
              <option value="resolved">resolved</option>
              <option value="escalated">escalated</option>
              <option value="failed">failed</option>
            </select>
          </label>
          <label className="field">
            <span className="field__label">Severity</span>
            <select
              value={severityFilter}
              onChange={(event) => setSeverityFilter(event.target.value)}
              className="field__input"
            >
              <option value="">All</option>
              <option value="CRITICAL">CRITICAL</option>
              <option value="HIGH">HIGH</option>
              <option value="MEDIUM">MEDIUM</option>
              <option value="LOW">LOW</option>
            </select>
          </label>
          <label className="field">
            <span className="field__label">Service</span>
            <select
              value={serviceFilter}
              onChange={(event) => setServiceFilter(event.target.value)}
              className="field__input"
            >
              <option value="">All</option>
              <option value="api-gateway">api-gateway</option>
              <option value="user-service">user-service</option>
              <option value="order-service">order-service</option>
            </select>
          </label>
          <label className="field">
            <span className="field__label">Date from</span>
            <input
              type="date"
              value={dateFrom}
              onChange={(event) => setDateFrom(event.target.value)}
              className="field__input"
            />
          </label>
          <label className="field">
            <span className="field__label">Date to</span>
            <input
              type="date"
              value={dateTo}
              onChange={(event) => setDateTo(event.target.value)}
              className="field__input"
            />
          </label>
        </div>
      </Surface>

      <Surface
        title="Incident archive"
        subtitle="Persisted incident records from the control plane"
      >
        {incidents.length ? (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Incident</th>
                  <th>Service</th>
                  <th>Severity</th>
                  <th>Status</th>
                  <th>Retries</th>
                  <th>Recovery</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {incidents.map((incident) => (
                  <tr
                    key={incident.incident_id}
                    onClick={() => navigate(`/incidents/${incident.incident_id}`)}
                  >
                    <td className="mono">{incident.incident_id.slice(0, 8)}</td>
                    <td>{incident.service || '-'}</td>
                    <td>{incident.severity || '-'}</td>
                    <td>
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
                    </td>
                    <td>{incident.retry_count || 0}</td>
                    <td>
                      {incident.time_to_recovery ? `${incident.time_to_recovery.toFixed(1)}s` : '-'}
                    </td>
                    <td>{new Date(incident.created_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title="No incidents match the current filter set"
            detail="Widen the filters or trigger a new run to populate the incident archive."
          />
        )}
      </Surface>
    </div>
  )
}
