import { describe, expect, test } from 'bun:test'
import { dispatchConcurrentSmokeMessage } from '../src/session-api'
import type { SlackbotV2Options } from '../src/types'

describe('concurrent smoke message trigger', () => {
  const channel = 'C12345678'
  const message = { type: 'message', channel, user: 'U12345678', ts: '123.456', text: 'centaur gay' }
  test('starts a threaded workflow with stable retry identity', async () => {
    const bodies: unknown[] = []
    const options = {
      apiUrl: 'https://api.example.test',
      fetch: async (_url: unknown, init: RequestInit) => {
        bodies.push(JSON.parse(String(init.body)))
        return new Response('{}')
      }
    } as unknown as SlackbotV2Options
    for (const type of ['message', 'app_mention']) {
      expect(await dispatchConcurrentSmokeMessage(options, {
        type: 'event_callback', event: { ...message, type, text: 'CENTAuR GAY', thread_ts: '100.001' }
      }, channel)).toBe(true)
    }
    expect(bodies[0]).toEqual(bodies[1])
    expect(bodies[0]).toEqual({ workflow_name: 'concurrent_agent_smoke',
      idempotency_key: `slack.concurrent-smoke:${channel}:123.456`,
      input: { slack_user_id: 'U12345678', channel, thread_ts: '100.001' } })
  })
  test('ignores disabled, other-channel, bot, edit and unrelated events', async () => {
    const options = {} as SlackbotV2Options
    for (const event of [
      { ...message, channel: 'C87654321' }, { ...message, bot_id: 'B123' },
      { ...message, subtype: 'message_changed' }, { ...message, text: 'unrelated' },
      { ...message, text: 'centaur gayer' }, { ...message, type: 'reaction_added' }
    ]) {
      expect(await dispatchConcurrentSmokeMessage(options, { type: 'event_callback', event }, channel)).toBe(false)
    }
    expect(await dispatchConcurrentSmokeMessage(options, { type: 'event_callback', event: message }, '')).toBe(false)
  })
  test('propagates API failures for Slack retry', async () => {
    const options = { apiUrl: 'https://api.example.test', fetch: async () => new Response('unavailable', { status: 503 }) } as unknown as SlackbotV2Options
    await expect(dispatchConcurrentSmokeMessage(options, { type: 'event_callback', event: message }, channel)).rejects.toThrow()
  })
})
