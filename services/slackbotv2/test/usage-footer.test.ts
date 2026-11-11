import { expect, test } from 'bun:test'
import { codexUsageRemaining, withCodexUsageFooter, type SlackContextBlock } from '../src/response-context'

test('uses actual durations and validates missing or malformed quota data', () => {
  expect(codexUsageRemaining({ primary: { usedPercent: 25, windowDurationMins: 300 }, secondary: { usedPercent: 80, windowDurationMins: 10080 } })).toEqual(['5h 75% remaining', 'Weekly 20% remaining'])
  expect(codexUsageRemaining({ primary: { usedPercent: 10, windowDurationMins: 43200 } })).toEqual(['Monthly 90% remaining'])
  for (const value of [null, {}, { primary: { usedPercent: '10', windowDurationMins: 300 } }, { limitId: 'other', primary: { usedPercent: 0, windowDurationMins: 300 } }]) expect(codexUsageRemaining(value)).toEqual([])
})

test('footer takes latest durable event without rendering account events', async () => {
  const block: SlackContextBlock = { type: 'context', elements: [{ type: 'mrkdwn', text: 'Sol 6.1 · Codex · Low' }] }
  const event = (usedPercent: number) => ({ eventKind: 'session.output.line', data: { raw: JSON.stringify({ method: 'account/rateLimits/updated', params: { rateLimits: { secondary: { usedPercent, windowDurationMins: 10080 } } } }) } })
  async function* source() { yield event(20); yield { data: 'answer' }; yield event(30) }
  const delivered = []
  for await (const item of withCodexUsageFooter(source(), block)) delivered.push(item)
  expect(delivered).toEqual([{ data: 'answer' }])
  expect(block.elements[0]?.text).toBe('Sol 6.1 · Codex · Low · Weekly 70% remaining')
  for await (const _ of withCodexUsageFooter(source())) { /* disabled metadata */ }
})

test('passes direct notifications and clears unavailable limits without stale quota', async () => {
  const block: SlackContextBlock = { type: 'context', elements: [{ type: 'mrkdwn', text: 'Codex' }] }
  async function* source() {
    yield { method: 'account/rateLimits/updated', params: { rateLimits: { primary: { usedPercent: 125, windowDurationMins: 300 } } } }
    expect(block.elements[0]?.text).toBe('Codex · 5h 0% remaining')
    yield { method: 'account/rateLimits/updated', params: { rateLimits: null } }
    yield { method: 'turn/completed' }
  }
  const delivered = []
  for await (const event of withCodexUsageFooter(source(), block)) delivered.push(event)
  expect(block.elements[0]?.text).toBe('Codex')
  expect(delivered).toEqual([{ method: 'turn/completed' }])
})
