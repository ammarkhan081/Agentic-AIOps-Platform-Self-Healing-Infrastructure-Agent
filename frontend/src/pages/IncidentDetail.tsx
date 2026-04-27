import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Bot, CheckCircle2, Clock3, FileDown, PlayCircle, Sparkles } from 'lucide-react'

import {
  createIncidentStream,
  exportIncidentPostmortem,
  getIncident,
  submitHITL,
} from '../api/client'
import { useDecisionPreset } from '../hooks/useDecisionPreset'
import { Badge, EmptyState, KeyValueList, MetricCard, Surface } from '../components/ui'

function eventTone(type: string) {
  if (type === 'pipeline_complete') return 'success'
  if (type === 'pipeline_error' || type === 'hitl_timeout') return 'danger'
  if (type === 'hitl_required' || type === 'hitl_decision') return 'warning'
  return 'info'
}

export default function IncidentDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const preset = useDecisionPreset()
  const [streamEvents, setStreamEvents] = useState<any[]>([])
  const [decision, setDecision] = useState<'approve' | 'override' | 'abort'>(preset || 'approve')
  const [instruction, setInstruction] = useState('')
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const { data: incident, refetch } = useQuery({
    queryKey: ['incident', id],
    queryFn: () => getIncident(id!),
    enabled: !!id,
    refetchInterval: 5000,
  })

  useEffect(() => {
    if (!id) return
    setStreamEvents([])
    const socket = createIncidentStream(id, (event) => {
      if (event.type !== 'ping') {
        setStreamEvents((previous) => [...previous, event])
      }
    })
    return () => socket.close()
  }, [id])

  const events = useMemo(() => {
    const seen = new Set<string>()
    return [...(incident?.events || []), ...streamEvents].filter((event) => {
      const key = `${event.type}-${event.timestamp}-${event.message || ''}-${event.status || ''}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  }, [incident?.events, streamEvents])

  if (!incident) {
    return (
      <div className="page-grid">
        <Surface>
          <EmptyState
            title="Loading incident"
            detail="Fetching the latest incident state and execution timeline."
          />
        </Surface>
      </div>
    )
  }

  const hypothesis =
    incident.hypotheses?.[incident.current_hypothesis_idx || 0] || incident.hypotheses?.[0]

  const handleSubmitDecision = async () => {
    if (!id) return
    setSubmitting(true)
    setError('')
    try {
      await submitHITL(id, decision, instruction || undefined, reason || undefined)
      await refetch()
      setInstruction('')
      setReason('')
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleExport = async (format: 'markdown' | 'json' | 'pdf') => {
    try {
      const result = await exportIncidentPostmortem(incident.incident_id, format)
      const blob =
        format === 'pdf'
          ? (result as Blob)
          : new Blob([format === 'json' ? JSON.stringify(result, null, 2) : String(result)], {
              type: format === 'json' ? 'application/json' : 'text/markdown',
            })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `incident-${incident.incident_id.slice(0, 8)}-postmortem.${format === 'markdown' ? 'md' : format}`
      link.click()
      URL.revokeObjectURL(url)
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message)
    }
  }

  return (
    <div className="page-grid">
      <div className="page-actions">
        <button className="button button--ghost" onClick={() => navigate('/history')}>
          <ArrowLeft size={15} />
          Back to incidents
        </button>
        {incident.postmortem ? (
          <div className="button-row">
            <button className="button button--ghost" onClick={() => handleExport('markdown')}>
              <FileDown size={15} />
              Markdown
            </button>
            <button className="button button--ghost" onClick={() => handleExport('json')}>
              <FileDown size={15} />
              JSON
            </button>
            <button className="button button--ghost" onClick={() => handleExport('pdf')}>
              <FileDown size={15} />
              PDF
            </button>
          </div>
        ) : null}
      </div>

      <section className="hero hero--compact">
        <div className="hero__copy">
          <div className="hero__eyebrow">Incident Session</div>
          <h1 className="hero__title">Incident {incident.incident_id.slice(0, 8)}</h1>
          <p className="hero__text">
            {incident.alert
              ? `${incident.alert.service} · ${incident.alert.metric_name} · ${incident.alert.severity}. ${incident.alert.description}`
              : 'Awaiting alert context from the control plane.'}
          </p>
        </div>
        <div className="button-row">
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
          {incident.alert?.severity ? (
            <Badge
              label={incident.alert.severity}
              tone={incident.alert.severity === 'CRITICAL' ? 'danger' : 'warning'}
            />
          ) : null}
        </div>
      </section>

      <section className="metrics-grid">
        <MetricCard
          label="Retry count"
          value={incident.retry_count || 0}
          icon={<PlayCircle size={16} />}
        />
        <MetricCard
          label="Hypotheses"
          value={incident.hypotheses?.length || 0}
          icon={<Sparkles size={16} />}
        />
        <MetricCard
          label="Past incidents"
          value={incident.past_incidents?.length || 0}
          icon={<Bot size={16} />}
        />
        <MetricCard
          label="Recovery"
          value={incident.time_to_recovery ? `${incident.time_to_recovery.toFixed(1)}s` : '-'}
          icon={<Clock3 size={16} />}
        />
        <MetricCard
          label="Automation cost"
          value={`$${(incident.total_cost_usd || 0).toFixed(4)}`}
          icon={<CheckCircle2 size={16} />}
        />
      </section>

      {incident.status === 'paused' ? (
        <Surface
          title="Human approval checkpoint"
          subtitle="This incident is paused behind a medium/high-risk remediation decision."
          actions={<Badge label="HITL required" tone="warning" />}
        >
          <div className="approval-grid">
            <div className="approval-card">
              <div className="approval-card__title">Lead hypothesis</div>
              <div className="approval-card__body">
                {hypothesis?.description || 'No hypothesis recorded'}
              </div>
            </div>
            <div className="approval-card">
              <div className="approval-card__title">Selected fix</div>
              <div className="approval-card__body">
                {incident.selected_fix?.action_type || 'No selected fix'}
                {incident.selected_fix?.risk_score ? ` · ${incident.selected_fix.risk_score}` : ''}
              </div>
            </div>
          </div>
          <div className="segmented">
            {(['approve', 'override', 'abort'] as const).map((item) => (
              <button
                key={item}
                className={
                  decision === item ? 'segmented__item segmented__item--active' : 'segmented__item'
                }
                onClick={() => setDecision(item)}
              >
                {item}
              </button>
            ))}
          </div>
          {decision === 'override' ? (
            <label className="field">
              <span className="field__label">Override instruction</span>
              <textarea
                value={instruction}
                onChange={(event) => setInstruction(event.target.value)}
                className="field__input field__input--textarea"
              />
            </label>
          ) : null}
          <label className="field">
            <span className="field__label">Audit note</span>
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              className="field__input field__input--textarea"
            />
          </label>
          {error ? <div className="inline-message inline-message--danger">{error}</div> : null}
          <div className="button-row">
            <button
              className="button button--primary"
              onClick={handleSubmitDecision}
              disabled={submitting}
            >
              {submitting ? 'Submitting...' : `Submit ${decision}`}
            </button>
          </div>
        </Surface>
      ) : null}

      <div className="two-column">
        <Surface title="Incident state" subtitle="Current RCA, remediation, and resolution summary">
          <KeyValueList
            items={[
              { label: 'Service', value: incident.alert?.service || '-' },
              { label: 'Metric', value: incident.alert?.metric_name || '-' },
              { label: 'Severity', value: incident.alert?.severity || '-' },
              { label: 'Recovery confirmed', value: String(incident.recovery_confirmed ?? '-') },
              { label: 'Error state', value: incident.error_message || 'None' },
            ]}
          />
          {incident.postmortem ? (
            <div className="summary-block">
              <div className="summary-block__title">Postmortem</div>
              <div className="summary-block__body">
                <p>
                  <strong>Root cause:</strong> {incident.postmortem.root_cause_confirmed}
                </p>
                <p>
                  <strong>Fix applied:</strong> {incident.postmortem.fix_applied}
                </p>
                <p>
                  <strong>Outcome:</strong> {incident.postmortem.outcome}
                </p>
              </div>
            </div>
          ) : null}
        </Surface>

        <Surface
          title="Execution timeline"
          subtitle="State transitions, pauses, completions, and agent events"
        >
          {events.length ? (
            <div className="timeline">
              {events.map((event, index) => (
                <div key={`${event.type}-${event.timestamp}-${index}`} className="timeline__item">
                  <div className="timeline__meta">
                    <Badge
                      label={event.type.replace(/_/g, ' ')}
                      tone={eventTone(event.type) as any}
                    />
                    <span>{new Date(event.timestamp).toLocaleString()}</span>
                  </div>
                  <div className="timeline__body">
                    {event.message || event.status || event.error || 'State update recorded'}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              title="No execution events yet"
              detail="As the graph progresses, live state changes will stream here."
            />
          )}
        </Surface>
      </div>

      <div className="three-column">
        <Surface
          title="Hypothesis board"
          subtitle="Ranked root-cause candidates generated during RCA"
        >
          {incident.hypotheses?.length ? (
            <div className="stack-list">
              {incident.hypotheses.map((item: any, index: number) => (
                <div key={item.hypothesis_id || index} className="list-card">
                  <div className="list-card__title">{item.description}</div>
                  <div className="list-card__meta">
                    <Badge
                      label={`${Math.round((item.confidence || 0) * 100)}% confidence`}
                      tone="brand"
                    />
                    {item.attempted ? <Badge label="Attempted" tone="success" /> : null}
                  </div>
                  {item.evidence?.length ? (
                    <div className="list-card__detail">{item.evidence.join(' | ')}</div>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              title="No hypotheses captured"
              detail="The RCA layer has not produced a ranked hypothesis set for this incident yet."
            />
          )}
        </Surface>

        <Surface
          title="Fix options"
          subtitle="Candidate remediation paths for the active hypothesis"
        >
          {incident.fix_options?.length ? (
            <div className="stack-list">
              {incident.fix_options.map((item: any) => (
                <div key={item.fix_id} className="list-card">
                  <div className="list-card__title">{item.action_type}</div>
                  <div className="list-card__meta">
                    <Badge
                      label={item.risk_score || 'Unknown risk'}
                      tone={
                        item.risk_score === 'LOW'
                          ? 'success'
                          : item.risk_score === 'MEDIUM'
                            ? 'warning'
                            : 'danger'
                      }
                    />
                    <span>{item.estimated_recovery_seconds || 0}s estimated recovery</span>
                  </div>
                  <div className="list-card__detail">
                    {item.reasoning || 'No reasoning recorded'}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              title="No remediation options"
              detail="Fix candidates will appear here once remediation planning completes."
            />
          )}
        </Surface>

        <Surface
          title="Incident memory"
          subtitle="Similar historical incidents retrieved from semantic search"
        >
          {incident.past_incidents?.length ? (
            <div className="stack-list">
              {incident.past_incidents.map((item: any) => (
                <div key={item.incident_id} className="list-card">
                  <div className="list-card__title">{item.service}</div>
                  <div className="list-card__meta">
                    <Badge
                      label={`${Math.round((item.similarity_score || 0) * 100)}% similarity`}
                      tone="info"
                    />
                  </div>
                  <div className="list-card__detail">{item.root_cause}</div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              title="No similar incidents returned"
              detail="The memory layer did not find matching historical incidents for this case."
            />
          )}
        </Surface>
      </div>
    </div>
  )
}
