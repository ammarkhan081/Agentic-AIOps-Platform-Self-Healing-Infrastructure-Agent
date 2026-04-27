import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Archive, FileDown, FileText, Search, TimerReset } from 'lucide-react'

import { exportReport, listReports } from '../api/client'
import { Badge, EmptyState, MetricCard, Surface } from '../components/ui'

function downloadBlob(data: Blob, filename: string) {
  const url = URL.createObjectURL(data)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export default function Reports() {
  const [query, setQuery] = useState('')
  const [serviceFilter, setServiceFilter] = useState('')
  const [outcomeFilter, setOutcomeFilter] = useState('')
  const [busyId, setBusyId] = useState('')

  const { data } = useQuery({ queryKey: ['reports'], queryFn: listReports })
  const reports = useMemo(() => data?.reports || [], [data])

  const filteredReports = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return reports.filter((item) => {
      const serviceMatch = !serviceFilter || item.service === serviceFilter
      const outcomeMatch = !outcomeFilter || item.outcome === outcomeFilter
      const haystack = [
        item.incident_id,
        item.service,
        item.root_cause_confirmed,
        item.fix_applied,
        item.alert_signature,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return serviceMatch && outcomeMatch && (!needle || haystack.includes(needle))
    })
  }, [outcomeFilter, query, reports, serviceFilter])

  const averageRecovery = useMemo(() => {
    const values = filteredReports
      .map((item) => item.time_to_recovery_seconds)
      .filter((item): item is number => typeof item === 'number')
    return values.length
      ? `${Math.round(values.reduce((sum, item) => sum + item, 0) / values.length)}s`
      : '-'
  }, [filteredReports])

  const handleExport = async (incidentId: string, format: 'markdown' | 'json' | 'pdf') => {
    setBusyId(`${incidentId}:${format}`)
    try {
      const result = await exportReport(incidentId, format)
      if (format === 'pdf') {
        downloadBlob(result as Blob, `incident-${incidentId.slice(0, 8)}.pdf`)
      } else if (format === 'json') {
        downloadBlob(
          new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' }),
          `incident-${incidentId.slice(0, 8)}.json`,
        )
      } else {
        downloadBlob(
          new Blob([String(result)], { type: 'text/markdown' }),
          `incident-${incidentId.slice(0, 8)}.md`,
        )
      }
    } finally {
      setBusyId('')
    }
  }

  return (
    <div className="page-grid">
      <section className="hero hero--compact">
        <div className="hero__copy">
          <div className="hero__eyebrow">Reports Archive</div>
          <h1 className="hero__title">
            Search and export postmortems with operator-grade fidelity.
          </h1>
          <p className="hero__text">
            Review resolved incidents, search root causes and fixes, and export postmortems in
            markdown, JSON, or PDF for handoff, compliance, and engineering review.
          </p>
        </div>
      </section>

      <section className="metrics-grid">
        <MetricCard
          label="Reports"
          value={filteredReports.length}
          icon={<FileText size={16} />}
          tone="brand"
        />
        <MetricCard
          label="Average recovery"
          value={averageRecovery}
          icon={<TimerReset size={16} />}
        />
        <MetricCard
          label="Resolved"
          value={filteredReports.filter((item) => item.outcome === 'resolved').length}
          icon={<Archive size={16} />}
          tone="success"
        />
      </section>

      <Surface title="Filters" subtitle="Search and narrow the postmortem archive">
        <div className="form-grid form-grid--three">
          <label className="field field--wide">
            <span className="field__label">Search</span>
            <div className="field__icon-wrap">
              <Search size={15} />
              <input
                type="text"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="field__input field__input--with-icon"
                placeholder="incident id, service, fix, root cause"
              />
            </div>
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
            <span className="field__label">Outcome</span>
            <select
              value={outcomeFilter}
              onChange={(event) => setOutcomeFilter(event.target.value)}
              className="field__input"
            >
              <option value="">All</option>
              <option value="resolved">resolved</option>
              <option value="failed">failed</option>
              <option value="escalated">escalated</option>
            </select>
          </label>
        </div>
      </Surface>

      <Surface title="Postmortem archive" subtitle="Final incident outcomes ready for export">
        {filteredReports.length ? (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Incident</th>
                  <th>Service</th>
                  <th>Root cause</th>
                  <th>Fix</th>
                  <th>Outcome</th>
                  <th>Recovery</th>
                  <th>Exports</th>
                </tr>
              </thead>
              <tbody>
                {filteredReports.map((report) => (
                  <tr key={report.incident_id}>
                    <td className="mono">{report.incident_id.slice(0, 8)}</td>
                    <td>{report.service}</td>
                    <td>{report.root_cause_confirmed.slice(0, 96)}</td>
                    <td>{report.fix_applied.slice(0, 56)}</td>
                    <td>
                      <Badge
                        label={report.outcome}
                        tone={
                          report.outcome === 'resolved'
                            ? 'success'
                            : report.outcome === 'escalated'
                              ? 'warning'
                              : 'danger'
                        }
                      />
                    </td>
                    <td>
                      {report.time_to_recovery_seconds
                        ? `${report.time_to_recovery_seconds.toFixed(1)}s`
                        : '-'}
                    </td>
                    <td>
                      <div className="button-row">
                        {(['markdown', 'json', 'pdf'] as const).map((format) => (
                          <button
                            key={format}
                            className="button button--ghost button--small"
                            disabled={busyId === `${report.incident_id}:${format}`}
                            onClick={() => handleExport(report.incident_id, format)}
                          >
                            <FileDown size={14} />
                            {busyId === `${report.incident_id}:${format}`
                              ? '...'
                              : format.toUpperCase()}
                          </button>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title="No reports match the current search"
            detail="Adjust the filters or resolve additional incidents to grow the archive."
          />
        )}
      </Surface>
    </div>
  )
}
