# Human-readable scope for the closed administrative-action registry. Credential
# display names are metadata; never read or render the credential's value.
class AdminActionPresentation
  CAPABILITIES = {
    "sandbox_observability_enabled" => "diagnostics",
    "sandbox_sessions_read_enabled" => "read session history",
    "sandbox_workflows_read_enabled" => "inspect workflows",
    "sandbox_workflows_write_enabled" => "manage workflows"
  }.freeze

  def self.safe(value)
    value.to_s.first(160).gsub("&", "&amp;").gsub("<", "&lt;").gsub(">", "&gt;")
  end

  def self.display_name(record, fallback)
    return fallback unless record
    value = record.try(:name).presence || record.try(:foreign_id).presence
    if record.is_a?(Principal) && record.kind == "slack_channel" && record.name.blank?
      channel = SlackBotChannel.find_by(team_id: record.slack_team_id, channel_id: record.slack_channel_id)
      value = channel&.name.presence || "the selected channel"
    end
    return fallback unless value
    if record.is_a?(Principal) && record.kind == "workflow"
      value = value.delete_prefix("Workflow ").delete_prefix("workflow-").tr("_-", " ").humanize
    end
    safe(value)
  end

  def self.target(request)
    principal = if request.target.start_with?("prn_")
      Principal.find_by_oid(request.target)
    else
      Principal.find_by(foreign_id: request.target)
    end
    display_name(principal, "the selected agent")
  end

  def self.summary(request)
    target = target(request)
    args = request.arguments
    case request.operation
    when "principal.default_skill"
      skill = args["skill"]
      skill ? "Set #{target}'s default workflow to #{safe(skill.tr("-", " ").humanize)}." : "Clear #{target}'s default workflow."
    when "principal.sandbox_access"
      settings = args.map do |key, value|
        if key == "sandbox_repo_cache"
          { "all" => "public and private cached repositories", "public" => "public cached repositories only", "none" => "no cached repositories" }.fetch(value)
        else
          "#{CAPABILITIES.fetch(key)} #{value ? 'on' : 'off'}"
        end
      end
      "Update #{target}: #{settings.to_sentence}."
    when "principal.role.assign", "principal.role.remove"
      role = display_name(Role.find_by_oid(args["role_id"]), "the selected role")
      request.operation.end_with?("assign") ? "Give #{target} the #{role} role." : "Remove the #{role} role from #{target}."
    when "principal.credential.grant"
      key, id = args.first
      credential = display_name(AdminActionOperations::GRANT_TYPES.fetch(key).find_by_oid(id), "the selected credential")
      "Give #{target} access to #{credential}."
    when "principal.credential.revoke"
      credential = display_name(Grant.find_by_oid(args["grant_id"])&.grantable, "the selected credential")
      "Remove #{target}'s access to #{credential}."
    when "principal.slack_permission"
      channel = SlackBotChannel.find_by(team_id: request.team_id, channel_id: args["channel_id"])
      labels = { "upload_enabled" => "upload files", "download_enabled" => "download files", "history_enabled" => "read channel history" }
      settings = args.except("channel_id").map { |key, value| "#{labels.fetch(key)} #{value ? 'on' : 'off'}" }.to_sentence
      "Update #{target}'s access in #{display_name(channel, 'the selected channel')}: #{settings}."
    when "principal.slack_permission.remove"
      channel = SlackBotChannel.find_by(team_id: request.team_id, channel_id: args["channel_id"])
      "Remove #{target}'s access to #{display_name(channel, 'the selected channel')}."
    when "principal.read", "principal.roles.read", "principal.grants.read"
      "Check access for #{target}."
    else
      "Check available access settings."
    end
  end

  def self.completion(request)
    if request.operation.start_with?("catalog.")
      "Access check complete."
    elsif AdminActionOperations.read?(request.operation)
      "Checked access for #{target(request)}."
    elsif request.operation == "principal.default_skill"
      "Updated #{target(request)}'s default workflow."
    else
      "Updated access for #{target(request)}."
    end
  end
end
