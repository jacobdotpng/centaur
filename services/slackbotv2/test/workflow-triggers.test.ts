import { describe, expect, test } from 'bun:test'
import { dispatchWorkflowMessage, parseWorkflowTriggers } from '../src/workflow-triggers'
import type { SlackbotV2Options } from '../src/types'

const trigger = { id: 'review', channel: 'C12345678', phrase: 'review please', workflow: 'review_pr', input: { topic: 'code' } }
const botUserId = 'U87654321'
const event = { type: 'app_mention', channel: trigger.channel, user: 'U12345678', ts: '123.456', text: `<@${botUserId}> REVIEW PLEASE` }
describe('configurable workflow triggers', () => {
  test('validates configuration before accepting messages', () => {
    expect(parseWorkflowTriggers(JSON.stringify([trigger]))).toEqual([trigger])
    for (const rows of [[trigger, trigger], [{ ...trigger, channel: '*' }], [{ ...trigger, phrase: '' }], [{ ...trigger, input: { channel: 'C87654321' } }]]) {
      expect(() => parseWorkflowTriggers(JSON.stringify(rows))).toThrow()
    }
    expect(() => parseWorkflowTriggers('bad json')).toThrow()
  })
  test('preserves configured input and binds trusted Slack delivery context', async () => {
    const bodies: unknown[] = []
    const options = { apiUrl: 'https://api.example.test', botUserId, fetch: async (_url: unknown, init: RequestInit) => {
      bodies.push(JSON.parse(String(init.body))); return Response.json({ ok: true, run_id: 'run-1' })
    } } as unknown as SlackbotV2Options
    for (const type of ['app_mention', 'app_mention']) {
      expect(await dispatchWorkflowMessage(options, { type: 'event_callback', event: { ...event, type } }, [trigger])).toBe(true)
    }
    expect(bodies[0]).toEqual(bodies[1])
    expect(bodies[0]).toEqual({ workflow_name: trigger.workflow, idempotency_key: `slack.workflow:review:${event.channel}:${event.ts}`,
      input: { topic: 'code', slack_user_id: event.user, channel: event.channel, thread_ts: event.ts } })
  })
  test('ignores other channels, bots, edits and unrelated messages', async () => {
    for (const changed of [{ channel: 'C87654321' }, { bot_id: 'B123' }, { subtype: 'message_changed' }, { text: 'review pleasex' }]) {
      expect(await dispatchWorkflowMessage({} as SlackbotV2Options, { type: 'event_callback', event: { ...event, ...changed } }, [trigger])).toBe(false)
    }
  })
  test('requires an explicit mention of this bot', async () => {
    const options = { botUserId, fetch: async () => { throw new Error('must not call API') } } as unknown as SlackbotV2Options
    for (const changed of [{ type: 'message' }, { text: 'review please' }, { text: '<@U11111111> review please' }]) {
      expect(await dispatchWorkflowMessage(options, { type: 'event_callback', event: { ...event, ...changed } }, [trigger])).toBe(false)
    }
  })
  test('propagates transient API failures so Slack retries', async () => {
    const options = { apiUrl: 'https://api.example.test', botUserId, fetch: async () => new Response('down', { status: 503 }) } as unknown as SlackbotV2Options
    await expect(dispatchWorkflowMessage(options, { type: 'event_callback', event }, [trigger])).rejects.toThrow()
  })
})
