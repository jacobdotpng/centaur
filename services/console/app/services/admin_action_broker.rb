require "digest"

# A worker may propose a typed operation. Only an authenticated Slack decision or
# an exact standing policy can authorize execution. Origin comes from proxy labels
# written by the trusted session runtime, never the worker's request document.
class AdminActionBroker
  class Denied < StandardError; end
  TTL = 30.minutes

  def self.origin_admin!(origin)
    user = origin.console_user
    raise Denied, "The originating Console administrator is inactive or unlinked; reconnect its admin identity." unless user&.active? && user.admin?
    if origin.kind == "slack_channel" && origin.labels["sandbox_admin"] == "true"
      channel = SlackBotChannel.active.find_by(team_id: origin.slack_team_id, channel_id: origin.slack_channel_id)
      return user if channel && channel.member_user_ids.include?(channel.bot_user_id)
    end
    raise Denied, "Administration requires an explicitly trusted home-workspace channel with Centaur present. Use that channel or ask its administrator to restore the grant."
  end

  def self.request!(proxy:, operation:)
    document = AdminActionOperations.validate!(operation)
    labels = proxy.labels
    origin_id = labels["centaur.admin_origin_principal"]
    raise Denied, "No verified Slack origin is attached to this execution. Start from a trusted channel; scheduled jobs need an explicit standing grant." if origin_id.blank?
    origin = Principal.find_by_oid!(origin_id)
    user = origin_admin!(origin)
    target = document.fetch("target")
    target_principal = target.start_with?("prn_") ? Principal.find_by_oid!(target) : Principal.find_by!(foreign_id: target)
    document["target"] = target_principal.oid
    task = labels["centaur.workflow_task_id"].to_s
    agent = labels["centaur.workflow_agent_key"].to_s
    workflow = labels["centaur.workflow_name"].to_s
    raise Denied, "No server-issued task identity is attached to this worker." if task.blank? || agent.blank? || workflow.blank?
    context = { "workflow_task_id" => task, "workflow_name" => workflow, "workflow_run_id" => labels["centaur.workflow_run_id"] }
    active_task!(context)
    policy_key = Digest::SHA256.hexdigest(JSON.generate([ origin.oid, workflow, document ]))
    key = Digest::SHA256.hexdigest(JSON.generate([ task, agent, document ]))
    request = AdminActionRequest.create_with(
      origin_principal: origin, worker_principal: proxy.principal,
      workflow_task_id: task, workflow_run_id: context.fetch("workflow_run_id"), workflow_agent_key: agent, workflow_name: workflow,
      channel_id: origin.slack_channel_id, team_id: origin.slack_team_id,
      thread_ts: labels["centaur.slack_thread_ts"].to_s,
      operation: document.fetch("operation"), target: document.fetch("target"), arguments: document.fetch("arguments"),
      status: "waiting_for_access", expires_at: TTL.from_now, policy_key: policy_key
    ).create_or_find_by!(request_key: key)
    request.with_lock do
      if request.status == "waiting_for_access" && AdminActionPolicy.exists?(policy_key: policy_key, origin_principal: origin)
        execute!(request, user: user, actor: "standing-policy", scope: "workflow")
      end
    end
    request
  end

  def self.decide!(request:, decision:, user_id:, team_id:, channel_id:, message_ts:, refresh_membership: true)
    raise Denied, "Unknown approval option." unless %w[once workflow deny details].include?(decision)
    origin = request.origin_principal
    user = origin_admin!(origin)
    raise Denied, "This approval belongs to a different Slack message or channel." unless
      request.team_id == team_id && request.channel_id == channel_id && request.message_ts.present? && request.message_ts == message_ts
    members = if refresh_membership
      SlackChannelCatalogSync.new.channel_members(channel_id)
    else
      SlackBotChannel.active.find_by!(team_id: team_id, channel_id: channel_id).member_user_ids
    end
    raise Denied, "Only current members of the originating trusted channel may decide this request." unless members.include?(user_id)
    expired = false
    active_task!(request) if request.status == "waiting_for_access"
    request.with_lock do
      return request unless request.status == "waiting_for_access"
      if request.expires_at <= Time.current
        request.update!(status: "expired", error_message: "Approval expired; no action was applied. Request a new scoped approval.")
        expired = true
        next
      end
      return request if decision == "details"
      origin_admin!(origin.reload)
      if decision == "deny"
        request.update!(status: "denied", approved_by_slack_user: user_id, error_message: "Access request denied; no action was applied.")
      else
        if decision == "workflow"
          AdminActionPolicy.create_or_find_by!(policy_key: request.policy_key) do |policy|
            policy.origin_principal = origin
            policy.workflow_name = request.workflow_name
            policy.document = request.document
            policy.approved_by_slack_user = user_id
          end
        end
        execute!(request, user: user, actor: user_id, scope: decision)
      end
    end
    raise Denied, "Approval expired; no action was applied." if expired
    request
  rescue AdminActionOperations::Unsupported, ActiveRecord::RecordNotFound, ActiveRecord::RecordInvalid => e
    request.update!(status: "failed", error_message: "The approved action could not be applied: #{e.class.name}. Inspect the target and parameters before requesting a new approval.")
    request
  end

  def self.active_task!(context)
    run_id = context.is_a?(Hash) ? context["workflow_run_id"] : context.workflow_run_id
    task_id = context.is_a?(Hash) ? context["workflow_task_id"] : context.workflow_task_id
    workflow = context.is_a?(Hash) ? context["workflow_name"] : context.workflow_name
    raise Denied, "No server-issued workflow run is attached to this request." if run_id.blank?
    run = CentaurApiClient.new.get_workflow_run(run_id)
    run = run["run"] || run
    unless run["task_id"] == task_id && run["workflow_name"] == workflow &&
           %w[queued pending running sleeping].include?(run["status"])
      raise Denied, "The originating workflow is no longer active. Inspect its preserved work; do not approve a stale request."
    end
  rescue CentaurApiClient::Error
    raise Denied, "Cannot verify that the originating workflow is active. Retry when its status service is available; no action was applied."
  end

  def self.execute!(request, user:, actor:, scope:)
    receipt = AdminActionOperations.execute!(request.document, user: user)
    request.update!(status: "completed", receipt: receipt, approved_by_slack_user: actor, approval_scope: scope)
    Rails.logger.info("admin_action_completed request=#{request.oid} origin=#{request.origin_principal.oid} task=#{request.workflow_task_id} operation=#{request.operation} target=#{request.target} actor=#{actor} scope=#{scope}")
  end
end
