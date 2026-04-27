import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { RefreshCcw, Zap } from 'lucide-react'

import { getDemoFaultStatus, getMe, injectDemoFault, resetDemoFaults } from '../api/client'
import { Badge, EmptyState, Surface } from '../components/ui'

const FAULT_HELP: Record<string, string> = {
  memory_leak:
    'Repeatedly allocates memory in order-service until memory pressure becomes visible to the monitor.',
  cpu_spike:
    'Starts a request flood against order-service for the configured duration to create throughput and compute pressure.',
  db_exhaustion:
    'Pushes user-service close to pool exhaustion to exercise high-risk approval flow.',
  slow_query: 'Activates sustained latency on order-service to mimic degraded query performance.',
  error_rate: 'Injects HTTP 500 responses into order-service at the configured ratio.',
  redis_overflow:
    'Raises simulated cache pressure on order-service to stress cache remediation logic.',
  cascade_failure:
    'Combines memory, latency, Redis pressure, and DB exhaustion into a multi-signal event.',
  rollback:
    'Rolls order-service back to a previous version to exercise high-risk operational controls.',
}

function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
}: {
  label: string
  value: number
  onChange: (value: number) => void
  min: number
  max: number
  step?: number
}) {
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
        className="field__input"
      />
    </label>
  )
}

export default function FaultLab() {
  const [faultType, setFaultType] = useState('memory_leak')
  const [service, setService] = useState('order-service')
  const [cycles, setCycles] = useState(10)
  const [duration, setDuration] = useState(30)
  const [rate, setRate] = useState(0.7)
  const [ratio, setRatio] = useState(0.95)
  const [connections, setConnections] = useState(95)
  const [delaySeconds, setDelaySeconds] = useState(2.5)
  const [targetVersion, setTargetVersion] = useState('v0.9.0')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: getMe, retry: false })
  const {
    data: faultStatus,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ['fault-status-page'],
    queryFn: getDemoFaultStatus,
    refetchInterval: 8000,
  })

  const canOperate = me?.role === 'admin' || me?.role === 'sre'
  const services = faultStatus?.services || {}
  const effectiveService =
    faultType === 'db_exhaustion'
      ? 'user-service'
      : faultType === 'cpu_spike'
        ? 'order-service'
        : faultType === 'cascade_failure'
          ? 'order-service'
          : faultType === 'rollback'
            ? 'order-service'
            : service

  const handleInject = async () => {
    setBusy(true)
    setMessage('')
    setError('')
    try {
      const result = await injectDemoFault({
        fault_type: faultType,
        service: effectiveService,
        cycles,
        duration,
        rate,
        ratio,
        connections,
        delay_seconds: delaySeconds,
        target_version: targetVersion,
      })
      setMessage(
        result.message ||
          `${result.fault_type} ${result.queued ? 'started' : 'applied'} successfully.`,
      )
      await refetch()
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setBusy(false)
    }
  }

  const handleReset = async () => {
    setBusy(true)
    setMessage('')
    setError('')
    try {
      await resetDemoFaults()
      setMessage('All target-system demo faults were reset.')
      await refetch()
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page-grid">
      <section className="hero hero--compact">
        <div className="hero__copy">
          <div className="hero__eyebrow">Fault Laboratory</div>
          <h1 className="hero__title">Inject controlled failures into the demo estate.</h1>
          <p className="hero__text">
            Configure and execute repeatable fault scenarios to validate detection, remediation,
            HITL routing, and memory-backed recovery behavior.
          </p>
        </div>
      </section>

      <div className="two-column">
        <Surface
          title="Scenario controls"
          subtitle="Configure fault behavior and dispatch a new scenario"
        >
          <div className="form-grid">
            <label className="field">
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
            <label className="field">
              <span className="field__label">Target service</span>
              <select
                value={effectiveService}
                onChange={(event) => setService(event.target.value)}
                disabled={
                  faultType === 'db_exhaustion' ||
                  faultType === 'cpu_spike' ||
                  faultType === 'cascade_failure' ||
                  faultType === 'rollback'
                }
                className="field__input"
              >
                <option value="order-service">order-service</option>
                <option value="user-service">user-service</option>
                <option value="api-gateway">api-gateway</option>
              </select>
            </label>
            <div className="hint-card hint-card--full">
              <div className="hint-card__title">Scenario description</div>
              <div className="hint-card__body">{FAULT_HELP[faultType]}</div>
            </div>

            {(faultType === 'memory_leak' || faultType === 'cascade_failure') && (
              <NumberField
                label="Leak cycles"
                value={cycles}
                onChange={setCycles}
                min={1}
                max={50}
              />
            )}
            {faultType === 'cpu_spike' && (
              <NumberField
                label="Duration (s)"
                value={duration}
                onChange={setDuration}
                min={1}
                max={180}
              />
            )}
            {(faultType === 'db_exhaustion' || faultType === 'cascade_failure') && (
              <NumberField
                label="DB connections"
                value={connections}
                onChange={setConnections}
                min={1}
                max={100}
              />
            )}
            {(faultType === 'slow_query' || faultType === 'cascade_failure') && (
              <NumberField
                label="Delay seconds"
                value={delaySeconds}
                onChange={setDelaySeconds}
                min={0}
                max={10}
                step={0.5}
              />
            )}
            {faultType === 'error_rate' && (
              <NumberField
                label="Error ratio"
                value={rate}
                onChange={setRate}
                min={0}
                max={1}
                step={0.05}
              />
            )}
            {(faultType === 'redis_overflow' || faultType === 'cascade_failure') && (
              <NumberField
                label="Redis pressure"
                value={ratio}
                onChange={setRatio}
                min={0}
                max={1}
                step={0.05}
              />
            )}
            {faultType === 'rollback' && (
              <label className="field field--full">
                <span className="field__label">Target version</span>
                <input
                  type="text"
                  value={targetVersion}
                  onChange={(event) => setTargetVersion(event.target.value)}
                  className="field__input"
                />
              </label>
            )}
          </div>

          <div className="button-row">
            <button
              className="button button--secondary"
              onClick={handleInject}
              disabled={busy || !canOperate}
            >
              <Zap size={15} />
              {busy ? 'Working...' : 'Inject scenario'}
            </button>
            <button
              className="button button--ghost"
              onClick={handleReset}
              disabled={busy || !canOperate}
            >
              <RefreshCcw size={15} />
              Reset all faults
            </button>
          </div>

          {message ? <div className="inline-message inline-message--success">{message}</div> : null}
          {error ? <div className="inline-message inline-message--danger">{error}</div> : null}
        </Surface>

        <Surface
          title="Live target-system state"
          subtitle="Current health and fault posture reported by each service"
          actions={
            <button className="button button--ghost" onClick={() => refetch()}>
              <RefreshCcw size={15} />
              {isFetching ? 'Refreshing...' : 'Refresh'}
            </button>
          }
        >
          {Object.entries(services).length ? (
            <div className="stack-list">
              {Object.entries(services).map(([name, payload]: any) => (
                <div key={name} className="list-card">
                  <div className="list-card__title">{name}</div>
                  <div className="list-card__meta">
                    <Badge
                      label={payload.ok ? 'Reachable' : 'Unavailable'}
                      tone={payload.ok ? 'success' : 'danger'}
                    />
                  </div>
                  <div className="token-row">
                    {Object.entries(payload.data || {})
                      .slice(0, 8)
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
              title="No fault status yet"
              detail="The live fault-state view will populate once the target services are reachable."
            />
          )}
        </Surface>
      </div>
    </div>
  )
}
