import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Database, HardDrive, RefreshCcw, Trash2 } from 'lucide-react'

import { deleteMemoryIncident, getMe, getMemoryIncidents } from '../api/client'
import { Badge, EmptyState, MetricCard, Surface } from '../components/ui'

export default function Memory() {
  const navigate = useNavigate()
  const [busyId, setBusyId] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: getMe, retry: false })
  const { data, refetch, isFetching } = useQuery({
    queryKey: ['memory-archive'],
    queryFn: () => getMemoryIncidents(100),
  })

  const memory = data?.memory || {}
  const incidents = data?.incidents || []
  const isAdmin = me?.role === 'admin'

  const handleDelete = async (incidentId: string) => {
    if (!isAdmin) return
    setBusyId(incidentId)
    setMessage('')
    setError('')
    try {
      await deleteMemoryIncident(incidentId)
      setMessage(`Removed ${incidentId.slice(0, 8)} from semantic memory.`)
      await refetch()
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setBusyId('')
    }
  }

  return (
    <div className="page-grid">
      <section className="hero hero--compact">
        <div className="hero__copy">
          <div className="hero__eyebrow">Semantic Memory</div>
          <h1 className="hero__title">Inspect and curate the incident learning archive.</h1>
          <p className="hero__text">
            Browse memory-backed incident records, verify provider readiness, and clean up stored
            knowledge when you need a controlled reset of the learning corpus.
          </p>
        </div>
      </section>

      <section className="metrics-grid">
        <MetricCard
          label="Stored incidents"
          value={incidents.length}
          icon={<Database size={16} />}
          tone="brand"
        />
        <MetricCard
          label="Provider"
          value={memory.provider || '-'}
          icon={<HardDrive size={16} />}
        />
        <MetricCard label="Index" value={memory.index || '-'} />
        <MetricCard label="Namespace" value={memory.namespace || '-'} />
      </section>

      <Surface
        title="Memory fabric status"
        subtitle="Provider readiness and current operator permissions"
        actions={
          <button className="button button--ghost" onClick={() => refetch()}>
            <RefreshCcw size={15} />
            {isFetching ? 'Refreshing...' : 'Refresh'}
          </button>
        }
      >
        <div className="two-column two-column--tight">
          <div className="hint-card">
            <div className="hint-card__title">Provider</div>
            <div className="hint-card__body">
              {memory.provider || 'unknown'} · {memory.index || '-'}
            </div>
          </div>
          <div className="hint-card">
            <div className="hint-card__title">Operator role</div>
            <div className="hint-card__body">{me?.role || 'viewer'}</div>
          </div>
        </div>
        {message ? <div className="inline-message inline-message--success">{message}</div> : null}
        {error ? <div className="inline-message inline-message--danger">{error}</div> : null}
      </Surface>

      <Surface title="Stored incidents" subtitle="Postmortems available to the retrieval layer">
        {incidents.length ? (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Incident</th>
                  <th>Service</th>
                  <th>Outcome</th>
                  <th>Root cause</th>
                  <th>Recovery</th>
                  <th>Created</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {incidents.map((item) => (
                  <tr
                    key={item.incident_id}
                    onClick={() => navigate(`/incidents/${item.incident_id}`)}
                  >
                    <td className="mono">{item.incident_id.slice(0, 8)}</td>
                    <td>{item.service}</td>
                    <td>
                      <Badge
                        label={item.outcome || item.status || 'stored'}
                        tone={item.outcome === 'resolved' ? 'success' : 'warning'}
                      />
                    </td>
                    <td>{(item.postmortem?.root_cause_confirmed || '-').slice(0, 88)}</td>
                    <td>
                      {item.postmortem?.time_to_recovery_seconds
                        ? `${item.postmortem.time_to_recovery_seconds.toFixed(1)}s`
                        : '-'}
                    </td>
                    <td>{item.created_at ? new Date(item.created_at).toLocaleString() : '-'}</td>
                    <td>
                      <button
                        className="button button--ghost button--small"
                        disabled={!isAdmin || busyId === item.incident_id}
                        onClick={(event) => {
                          event.stopPropagation()
                          handleDelete(item.incident_id)
                        }}
                      >
                        <Trash2 size={14} />
                        {!isAdmin
                          ? 'Admin only'
                          : busyId === item.incident_id
                            ? 'Deleting...'
                            : 'Delete'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title="No memory-backed incidents yet"
            detail="Resolved incidents will appear here once the learning agent stores them."
          />
        )}
      </Surface>
    </div>
  )
}
