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
      return render_error(
        status: :forbidden,
        message: "admin changes require an active Console admin linked to a home-workspace Slack DM or an explicitly trusted channel"
      )
    end

    @sandbox_admin = user
    Rails.logger.info(
      "sandbox_admin_request admin=#{user.oid} principal=#{principal.oid} " \
      "proxy=#{current_proxy.oid} method=#{request.method} path=#{request.path}"
    )
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
