import { expect, test } from 'bun:test'
import { dispatchSmokeWorkflowMessage } from '../src/session-api'
import type { SlackbotV2Options } from '../src/types'

const event = {
  type: 'message', channel: 'C0CFN5YCEUV', user: 'U0CFM5P1SSF',
  text: 'centaur gay', ts: '1794426358.361759'
}
const envelope = (overrides = {}) => ({
  type: 'event_callback', team_id: 'T12345678', event: { ...event, ...overrides }
})
const requests: unknown[] = []
const options: SlackbotV2Options = {
  apiUrl: 'http://api.test', botToken: 'test-token', signingSecret: 'test-secret',
  fetch: async (url, init) => {
    expect(String(url)).toBe('http://api.test/api/workflows/messages/smoke')
    requests.push(JSON.parse(String(init?.body)))
    return Response.json({ ok: true, created: true, run_id: 'run-smoke' })
  }
}

test('starts for any human author, preserves thread, ignores case and surrounding spaces', async () => {
  requests.length = 0
  expect(await dispatchSmokeWorkflowMessage(options, envelope())).toBe(true)
  expect(await dispatchSmokeWorkflowMessage(options, envelope({
    text: '  CENTAUR GAY  ', user: 'U12345678', thread_ts: '1794426300.000001'
  }))).toBe(true)
  expect(requests).toEqual([
    { channel: event.channel, user: event.user, team: 'T12345678', message_ts: event.ts,
      thread_ts: event.ts, text: event.text },
    { channel: event.channel, user: 'U12345678', team: 'T12345678', message_ts: event.ts,
      thread_ts: '1794426300.000001', text: '  CENTAUR GAY  ' }
  ])
})

test('ignores other channels, bots, edits, malformed events, and phrases inside other text', async () => {
  requests.length = 0
  for (const payload of [null, {}, envelope({ channel: 'C12345678' }),
    envelope({ subtype: 'message_changed' }), envelope({ bot_id: 'B12345678' }),
    envelope({ app_id: 'A12345678' }), envelope({ text: 'please say centaur gay' }),
    envelope({ user: '' }), envelope({ ts: '' }), { ...envelope(), type: 'app_mention' }
  ]) expect(await dispatchSmokeWorkflowMessage(options, payload)).toBe(false)
  expect(requests).toHaveLength(0)
})

test('propagates admission failures so Slack retries instead of losing the trigger', async () => {
  await expect(dispatchSmokeWorkflowMessage({
    ...options, fetch: async () => Response.json({ error: 'unavailable' }, { status: 503 })
  }, envelope())).rejects.toThrow()
})

test('does not acknowledge a malformed success response', async () => {
  await expect(dispatchSmokeWorkflowMessage({
    ...options, fetch: async () => Response.json({ ok: true })
  }, envelope())).rejects.toThrow('invalid admission result')
})
