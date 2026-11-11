import { apiHeaders, fetchWithTimeout, sessionApiTimeoutMs } from './session-api'
import type { SlackbotV2Options } from './types'

export interface WorkflowTrigger {
  id: string
  channel: string
  phrase: string
  workflow: string
  input?: Record<string, unknown>
}
const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/** Configuration errors stop startup; never widen a malformed trigger. */
export function parseWorkflowTriggers(raw: string): WorkflowTrigger[] {
  const rows: unknown = JSON.parse(raw)
  if (!Array.isArray(rows) || rows.length > 100) throw new Error('Workflow triggers must be an array of at most 100 rules')
  const ids = new Set<string>()
  const matches = new Set<string>()
  return rows.map(row => {
    if (!object(row) || typeof row.id !== 'string' || !/^[a-zA-Z0-9_-]{1,64}$/.test(row.id)
      || typeof row.channel !== 'string' || !/^[CG][A-Z0-9]{8,}$/.test(row.channel)
      || typeof row.workflow !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(row.workflow)
      || typeof row.phrase !== 'string' || !row.phrase.trim() || row.phrase.length > 200
      || (row.input !== undefined && !object(row.input))) throw new Error('Invalid workflow trigger')
    const match = `${row.channel}:${row.phrase.trim().toLowerCase()}`
    if (ids.has(row.id) || matches.has(match)) throw new Error('Duplicate workflow trigger')
    ids.add(row.id); matches.add(match)
    const input = row.input as Record<string, unknown> | undefined
    if (input && ['channel', 'thread_ts', 'slack_user_id', 'principal'].some(key => key in input)) {
      throw new Error('Workflow trigger input cannot override delivery or principal identity')
    }
    return { id: row.id, channel: row.channel, workflow: row.workflow, phrase: row.phrase.trim(), ...(row.input ? { input: row.input } : {}) }
  })
}

/** Invoked after signature verification and the existing Slack policy gate. */
export async function dispatchWorkflowMessage(
  options: SlackbotV2Options,
  payload: Record<string, unknown> | null,
  triggers: WorkflowTrigger[]
): Promise<boolean> {
  const event = payload?.event
  if (payload?.type !== 'event_callback' || !object(event)
    || !['message', 'app_mention'].includes(String(event.type))
    || event.user === options.botUserId || event.subtype || event.bot_id || event.bot_profile
    || typeof event.user !== 'string' || !/^[UW][A-Z0-9]{8,}$/.test(event.user)
    || typeof event.channel !== 'string' || typeof event.ts !== 'string' || typeof event.text !== 'string') return false
  const text = event.text
  const trigger = triggers.find(rule => rule.channel === event.channel
    && new RegExp(`(?:^|\\W)${rule.phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?:$|\\W)`, 'i').test(text))
  if (!trigger) return false
  const response = await fetchWithTimeout(options.fetch ?? globalThis.fetch,
    new URL('/api/workflows/runs', options.apiUrl), {
      method: 'POST', headers: apiHeaders(options),
      body: JSON.stringify({ workflow_name: trigger.workflow,
        idempotency_key: `slack.workflow:${trigger.id}:${event.channel}:${event.ts}`,
        input: { ...trigger.input, slack_user_id: event.user, channel: event.channel,
          thread_ts: typeof event.thread_ts === 'string' ? event.thread_ts : event.ts } })
    }, sessionApiTimeoutMs(options), 'start configured Slack workflow')
  if (!response.ok) {
    await response.body?.cancel()
    throw new Error(`Configured workflow ${trigger.workflow} was not accepted (HTTP ${response.status}); check workflow enablement and caller permissions`)
  }
  await response.body?.cancel()
  return true
}
