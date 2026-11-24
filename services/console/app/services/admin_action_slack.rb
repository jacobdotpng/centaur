require "digest"

class AdminActionSlack
  def self.api(method, payload)
    token = SlackChannelCatalogSync.token
    raise SlackApi::Error, "Slack approval delivery is not configured." if token.blank?
    response = HttpClient.new(open_timeout: 2, read_timeout: 5).request(
      method: :post, url: "#{SlackChannelCatalogSync.api_url}/#{method}", json: payload,
      headers: { "Authorization" => "Bearer #{token}", "Content-Type" => "application/json" }
    )
    SlackApi.parse_response!(response, operation: method)
  end

  def self.text(request)
    "Access needed: `#{request.operation}` on `#{request.target}`.\n" \
      "Workflow: `#{request.workflow_name}` · task `#{request.workflow_task_id}`\n" \
      "Exact parameters: `#{JSON.generate(request.arguments)}`\n" \
      "The worker cannot administer Console directly. Approve this action to continue the same run; no action has been applied.\n" \
      "Expires #{request.expires_at.utc.iso8601}. Request `#{request.oid}`. " \
      "Standing approvals can be inspected/revoked with `centaur-console admin action-policies` and `revoke-action-policy`."
  end

  def self.post!(request)
    request.with_lock do
      return unless request.status == "waiting_for_access" && request.message_ts.blank?
      actions = [ [ "once", "Approve once", "primary" ], [ "workflow", "Allow matching workflow action", nil ],
                  [ "details", "Details", nil ], [ "deny", "Deny", "danger" ] ]
      blocks = [ { type: "section", text: { type: "mrkdwn", text: text(request) } },
                 { type: "actions", elements: actions.map do |decision, label, style|
                   { type: "button", text: { type: "plain_text", text: label },
                     action_id: "centaur.admin-action:#{request.oid}:#{decision}", value: request.oid,
                     **(style ? { style: style } : {}),
                     **(decision == "workflow" ? { confirm: { title: { type: "plain_text", text: "Save an exact standing approval?" },
                       text: { type: "mrkdwn", text: "Allows only this operation, target and parameters for future runs of this workflow from this trusted channel. It does not grant general administration." },
                       confirm: { type: "plain_text", text: "Allow matching action" }, cancel: { type: "plain_text", text: "Cancel" } } } : {}) }
                 end },
                 { type: "context", elements: [ { type: "mrkdwn", text: "Or mention Centaur with `approve once #{request.oid}`, `details #{request.oid}` or `deny #{request.oid}` in this thread. Standing approvals require the explicit menu option." } ] } ]
      key = request.request_key[0, 32]
      client_id = [ key[0, 8], key[8, 4], key[12, 4], key[16, 4], key[20, 12] ].join("-")
      posted = api("chat.postMessage", { channel: request.channel_id, text: text(request), blocks: blocks,
                                        thread_ts: request.thread_ts.presence, client_msg_id: client_id }.compact)
      request.update!(message_ts: posted.fetch("ts"))
    end
  end

  def self.feedback!(request, user_id:, details: false)
    if details
      api("chat.postEphemeral", { channel: request.channel_id, user: user_id, text: text(request), thread_ts: request.thread_ts.presence }.compact)
    elsif request.status != "waiting_for_access"
      status = request.status == "completed" ? "Approved and applied. The same workflow will continue from its saved progress." : "#{request.status.capitalize}: #{request.error_message}"
      api("chat.update", { channel: request.channel_id, ts: request.message_ts,
                           text: "#{request.operation} on #{request.target}: #{status}", blocks: [
                             { type: "section", text: { type: "mrkdwn", text: "#{status}\nTask `#{request.workflow_task_id}` · Request `#{request.oid}`" } }
                           ] })
    end
  end
end
