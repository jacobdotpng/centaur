# Selected admin operations require a linked active Console admin in the bot's
# home workspace. Slack channels additionally need explicit operator opt-in and
# active catalog membership for the bot. Console/MCP/workflow sessions stay denied.
module SandboxAdminAuthentication
  extend ActiveSupport::Concern
  include SandboxTokenAuthentication

  included do
    skip_before_action :authenticate_api_key!
    before_action :authenticate_sandbox_admin!
  end

  def current_user
    @sandbox_admin
  end

  private

  def authenticate_sandbox_admin!
    authenticate_sandbox_token!
    return if performed?

    principal = current_proxy.principal
    user = principal.console_user
    unless admin_slack_scope?(principal) && user&.active? && user.admin?
      return resolve_admin_access_blocker!
    end

    @sandbox_admin = user
    Rails.logger.info(
      "sandbox_admin_request admin=#{user.oid} principal=#{principal.oid} " \
      "proxy=#{current_proxy.oid} method=#{request.method} path=#{request.path}"
    )
  end


  def resolve_admin_access_blocker!
    if current_proxy.labels["centaur.admin_origin_principal"].present?
      AdminActionBroker.origin_admin!(Principal.find_by_oid!(current_proxy.labels["centaur.admin_origin_principal"]))
    end
    operation = if current_proxy.labels["centaur.admin_origin_principal"].present?
      AdminActionOperations.from_http(request.method, request.path, params.to_unsafe_h)
    end
    if operation && current_proxy.labels["centaur.admin_origin_principal"].present?
      action = AdminActionBroker.request!(proxy: current_proxy, operation: operation)
      AdminActionSlack.post!(action)
      return render status: :forbidden, json: { error: {
        code: "admin_approval_required", message: "A scoped access request is available in the originating Slack thread. Stop this agent turn; the workflow will wait and continue after the decision.",
        resolution: action.public_payload
      } }
    end
    render status: :forbidden, json: { error: {
      code: "admin_scope_required",
      message: "This session cannot administer Console directly. Use centaur-console request-action with a registered typed operation from a workflow with a verified trusted Slack origin. For an unregistered operation, propose a reviewed broker handler; do not repeat the same HTTP request or assume broader worker authority.",
      supported_operations: AdminActionOperations::KEYS.keys
    } }
  rescue AdminActionBroker::Denied, AdminActionOperations::Unsupported => e
    render status: :forbidden, json: { error: { code: "admin_resolution_unavailable", message: e.message } }
  end

  def admin_slack_scope?(principal)
    return false unless home_slack_team?(principal.slack_team_id)
    return true if principal.kind == "slack_dm"
    return false unless principal.kind == "slack_channel" && principal.labels.to_h["sandbox_admin"] == "true"

    channel = SlackBotChannel.active.find_by(team_id: principal.slack_team_id, channel_id: principal.slack_channel_id)
    channel.present? && channel.member_user_ids.include?(channel.bot_user_id)
  end

  def home_slack_team?(team_id)
    team_id.present? && SlackBotChannel.exists?(team_id: team_id)
  end
end
