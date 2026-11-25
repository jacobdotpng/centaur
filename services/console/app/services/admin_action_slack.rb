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
    "*Approval needed*\n#{AdminActionPresentation.summary(request)}\nApprove once to continue."
  end

  def self.details_text(request)
    "#{AdminActionPresentation.summary(request)}\n\n" \
      "Operation: `#{request.operation}` on `#{request.target}`.\n" \
      "Workflow: `#{request.workflow_name}` · task `#{request.workflow_task_id}`\n" \
      "Run: `#{request.workflow_run_id}`\n" \
      "Exact parameters: `#{JSON.generate(request.arguments)}`\n" \
      "Status: #{request.status.tr('_', ' ')}. Decision: #{request.approval_scope || 'not approved'}.\n" \
      "Expires #{request.expires_at.utc.iso8601}. Request `#{request.oid}`. " \
      "\nFuture approval covers only this exact change, agent and workflow in this channel. " \
      "Ask Centaur to remove a saved approval when you no longer want it."
  end

  def self.details_button(request)
    { type: "actions", elements: [ { type: "button", text: { type: "plain_text", text: "Details" },
      action_id: "centaur.admin-action:#{request.oid}:details", value: request.oid } ] }
  end

  def self.post!(request)
    request.with_lock do
      return unless request.status == "waiting_for_access" && request.message_ts.blank?
      actions = [ [ "once", "Approve once", "primary" ], [ "workflow", "Always allow this change", nil ],
                  [ "details", "Details", nil ], [ "deny", "Deny", "danger" ] ]
      minutes = [ ((request.expires_at - Time.current) / 60).ceil, 1 ].max
      blocks = [ { type: "section", text: { type: "mrkdwn", text: text(request) } },
                 { type: "actions", elements: actions.map do |decision, label, style|
                   { type: "button", text: { type: "plain_text", text: label },
                     action_id: "centaur.admin-action:#{request.oid}:#{decision}", value: request.oid,
                     **(style ? { style: style } : {}),
                     **(decision == "workflow" ? { confirm: { title: { type: "plain_text", text: "Allow future matching requests?" },
                       text: { type: "mrkdwn", text: "Allow this exact change in future runs of #{AdminActionPresentation.safe(request.workflow_name.tr('_', ' ').humanize)} from this channel. Other changes still need approval." },
                       confirm: { type: "plain_text", text: "Allow matching requests" }, deny: { type: "plain_text", text: "Cancel" } } } : {}) }
                 end },
                 { type: "context", elements: [ { type: "mrkdwn", text: "Only the change above is requested. Expires in #{minutes} #{'minute'.pluralize(minutes)}." } ] } ]
      key = request.request_key[0, 32]
      client_id = [ key[0, 8], key[8, 4], key[12, 4], key[16, 4], key[20, 12] ].join("-")
      payload = { channel: request.channel_id, text: text(request), blocks: blocks,
                  thread_ts: request.thread_ts.presence, client_msg_id: client_id }.compact
      begin
        posted = api("chat.postMessage", payload)
      rescue SlackApi::Error => e
        raise unless %w[invalid_blocks invalid_blocks_format msg_blocks_too_long].include?(e.code)
        Rails.logger.warn("admin_action_menu_fallback request=#{request.oid} slack_error=#{e.code}")
        fallback = text(request) + "\nReply in this thread mentioning Centaur with one of:\n" \
          "`approve once #{request.oid}` · `details #{request.oid}` · `deny #{request.oid}`\n" \
          "Button delivery was rejected; standing approval is unavailable through text replies."
        posted = api("chat.postMessage", payload.except(:blocks).merge(text: fallback))
      end
      request.update!(message_ts: posted.fetch("ts"))
    end
  end

  def self.feedback!(request, user_id:, details: false)
    if details
      api("chat.postEphemeral", { channel: request.channel_id, user: user_id, text: details_text(request), thread_ts: request.thread_ts.presence }.compact)
    elsif request.status != "waiting_for_access"
      status = case request.status
      when "completed" then "*Done*\n#{AdminActionPresentation.completion(request)}"
      when "denied" then "Declined. No change was made."
      when "expired" then "This approval expired. Ask me to request it again."
      else "I couldn't apply this change. Ask me to check what happened before retrying."
      end
      api("chat.update", { channel: request.channel_id, ts: request.message_ts,
                           text: status, blocks: [
                             { type: "section", text: { type: "mrkdwn", text: status } }, details_button(request)
                           ] })
    end
  end
end
