import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

export type HITLDecisionPreset = 'approve' | 'override' | 'abort' | undefined

export function useDecisionPreset(): HITLDecisionPreset {
  const [searchParams] = useSearchParams()

  return useMemo(() => {
    const requestedDecision = searchParams.get('decision')
    if (
      requestedDecision === 'approve' ||
      requestedDecision === 'override' ||
      requestedDecision === 'abort'
    ) {
      return requestedDecision
    }
    return undefined
  }, [searchParams])
}
