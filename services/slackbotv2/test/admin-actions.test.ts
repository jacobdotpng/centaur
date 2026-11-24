import { describe, expect, test } from 'bun:test'
import { dispatchAdminActionReply, dispatchSlackBlockAction } from '../src/session-api'
import type { SlackbotV2Options } from '../src/types'

function options(calls: Array<{url: string, body: any}>, status = 200): SlackbotV2Options {
  return { apiUrl: 'http://api.example', botUserId: 'UBOT000001', botToken: 'test', signingSecret: 'test',
    fetch: async (url: any, init: any) => {
      calls.push({ url: String(url), body: JSON.parse(init.body) })
      return new Response(JSON.stringify({ data: { status: 'completed' } }), { status })
    }
  } as SlackbotV2Options
}

const click = { type: 'block_actions' as const, message_id: '1234567890.123456', thread_id: 'slack:CTRUSTED001:1234567890.123456', user_name: 'member', action_id: 'centaur.admin-action:aar_Example:once', value: 'aar_Example',
  action_ts: '1234567891.123456', channel_id: 'CTRUSTED001', message_ts: '1234567890.123456',
  team_id: 'TTEAM000001', user_id: 'UMEMBER001' }

describe('administrative approval ingress', () => {
  test('button forwards the actual verified click identity without starting a workflow', async () => {
    const calls: Array<{url: string, body: any}> = []
    await dispatchSlackBlockAction(options(calls), click)
    expect(calls).toHaveLength(1)
    expect(calls[0]!.url).toBe('http://api.example/api/admin-actions/aar_Example/decide')
    expect(calls[0]!.body).toEqual({ decision: 'once', user_id: click.user_id, team_id: click.team_id,
      channel_id: click.channel_id, message_ts: click.message_ts })
  })

  test('tampered value is rejected before any request', async () => {
    const calls: Array<{url: string, body: any}> = []
    await expect(dispatchSlackBlockAction(options(calls), { ...click, value: 'aar_Other' })).rejects.toThrow()
    expect(calls).toHaveLength(0)
  })

  test('mention reply is a deterministic decision with its real originating thread', async () => {
    const calls: Array<{url: string, body: any}> = []
    const envelope = { type: 'event_callback', team_id: click.team_id, event: {
      type: 'app_mention', user: click.user_id, channel: click.channel_id,
      thread_ts: '1111111111.111111', text: '<@UBOT000001> approve once aar_Example' } }
    expect(await dispatchAdminActionReply(options(calls), envelope)).toBe(true)
    expect(calls[0]!.body.thread_ts).toBe('1111111111.111111')
    expect(calls[0]!.body.user_id).toBe(click.user_id)
    for (const event of [ { ...envelope.event, bot_id: 'BBOT' }, { ...envelope.event, text: 'approve once aar_Example' },
      { ...envelope.event, text: '<@UBOT000001> I might approve once aar_Example later' } ]) {
      expect(await dispatchAdminActionReply(options(calls), { ...envelope, event })).toBe(false)
    }
    expect(calls).toHaveLength(1)
  })

  test('accepts the actual Slack MCP mention and attribution format without borrowing its actor', async () => {
    const calls: Array<{url: string, body: any}> = []
    expect(await dispatchAdminActionReply(options(calls), { type: 'event_callback', team_id: click.team_id, event: {
      type: 'app_mention', user: click.user_id, channel: click.channel_id, thread_ts: '1111111111.111111',
      text: '<@UBOT000001|Centaur> approve once aar_Example\n\n*Sent using* <@UATTRIBUTION|ChatGPT>'
    } })).toBe(true)
    expect(calls).toHaveLength(1)
    expect(calls[0]!.body.user_id).toBe(click.user_id)
    expect(calls[0]!.body.decision).toBe('once')
  })

  test('a refused decision produces actionable feedback instead of claiming success', async () => {
    const result = await dispatchSlackBlockAction(options([], 403), click)
    expect(result?.outcome).toBe('denied')
    expect(String(result?.message)).toContain('originating trusted channel')
  })
})
