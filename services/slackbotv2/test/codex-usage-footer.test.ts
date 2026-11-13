import { expect, test } from 'bun:test'
import { formatCodexUsage, withCodexUsageFooter } from '../src/codex-usage-footer'
import { buildSlackResponseContextBlock } from '../src/response-context'

const rate = (usedPercent: unknown, windowDurationMins: unknown) => ({ primary: { usedPercent, windowDurationMins } })
const output = (method: string, params = {}) => ({ event: 'session.output.line', data: { raw: JSON.stringify({ method, params }) } })
const usage = output('account/rateLimits/updated', { rateLimits: rate(18, 300) })
async function* events(...values: unknown[]) { yield* values }
const block = (metadataEnabled = true, provider?: string) => buildSlackResponseContextBlock({
  harnessType: 'codex', model: 'gpt-6.1-sol', metadataEnabled, provider, notice: 'Notice'
})!

test('formats only valid reported windows, including nonstandard durations and boundaries', () => {
  expect(formatCodexUsage({ ...rate(18, 300), secondary: { usedPercent: 36, windowDurationMins: 10080 } })).toEqual(['82% remaining (5h)', '64% remaining (Weekly)'])
  expect(formatCodexUsage(rate(100, 90))).toEqual(['0% remaining (90m)'])
  expect(formatCodexUsage(rate(0, 1440))).toEqual(['100% remaining (1d)'])
  for (const used of [-1, 101, NaN, Infinity, null, '18']) expect(formatCodexUsage(rate(used, 300))).toEqual([])
  for (const duration of [0, -1, 1.5, NaN, null, '300']) expect(formatCodexUsage(rate(18, duration))).toEqual([])
  expect(formatCodexUsage(null)).toEqual([])
})

test('keeps quota execution-local and clears it on account changes and unavailable updates', async () => {
  const footer = block()
  const base = footer.elements[0]!.text
  for await (const _ of withCodexUsageFooter(events(usage), footer)) {}
  expect(footer.elements[0]!.text).toContain('82% remaining (5h)')
  for await (const _ of withCodexUsageFooter(events(), footer)) {}
  expect(footer.elements[0]!.text).toBe(base)
  for await (const _ of withCodexUsageFooter(events(usage, output('account/updated'), usage), footer)) {}
  expect(footer.elements[0]!.text).toBe(base)
  for await (const _ of withCodexUsageFooter(events(usage, output('account/rateLimits/updated', { rateLimits: null })), footer)) {}
  expect(footer.elements[0]!.text).toBe(base)
})

test('preserves metadata visibility and excludes other providers', async () => {
  for (const footer of [block(false), block(true, 'amazon-bedrock')]) {
    const base = footer.elements[0]!.text
    for await (const _ of withCodexUsageFooter(events(usage), footer)) {}
    expect(footer.elements[0]!.text).toBe(base)
  }
})
