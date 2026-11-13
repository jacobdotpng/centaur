import { rustSessionEventToServerNotification } from '@centaur/rendering'
import { codexUsageContextBlocks, type SlackContextBlock } from './response-context'

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' ? value as Record<string, unknown> : undefined
}

/** Labels always describe the reported window, never the primary/secondary slot. */
export function formatCodexUsage(value: unknown): string[] {
  const limits = record(value)
  return ['primary', 'secondary'].flatMap(key => {
    const window = record(limits?.[key])
    const used = window?.usedPercent
    const minutes = window?.windowDurationMins
    if (typeof used !== 'number' || !Number.isFinite(used) || used < 0 || used > 100 ||
        typeof minutes !== 'number' || !Number.isSafeInteger(minutes) || minutes <= 0) return []
    const label = minutes === 10080 ? 'Weekly'
      : minutes % 1440 === 0 ? `${minutes / 1440}d`
      : minutes % 60 === 0 ? `${minutes / 60}h` : `${minutes}m`
    return [`${Math.round((100 - used) * 10) / 10}% remaining (${label})`]
  })
}

/** Each stream owns its quota state. No account-wide or thread-wide cache. */
export async function* withCodexUsageFooter<T>(
  stream: AsyncIterable<T>, block?: SlackContextBlock
): AsyncIterable<T> {
  const base = block && codexUsageContextBlocks.get(block)
  let accountChanged = false
  if (block && base) block.elements[0]!.text = base
  for await (const source of stream) {
    const mapped = rustSessionEventToServerNotification(source)
    if (block && base && mapped?.kind === 'notification') {
      const event = mapped.notification as Record<string, unknown>
      if (event.type === 'account.updated') {
        accountChanged = true
        block.elements[0]!.text = base
      } else if (!accountChanged && event.type === 'account.rateLimits.updated') {
        block.elements[0]!.text = [base, ...formatCodexUsage(event.rateLimits)].join(' · ')
      }
    }
    yield source
  }
}
