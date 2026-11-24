# A closed registry, not an HTTP proxy. Arguments never contain credential values.
class AdminActionOperations
  class Unsupported < StandardError; end
  FLAGS = %w[sandbox_observability_enabled sandbox_sessions_read_enabled sandbox_workflows_read_enabled sandbox_workflows_write_enabled].freeze
  GRANT_TYPES = { "static_secret_id" => StaticSecret, "gcp_auth_secret_id" => GcpAuthSecret,
                  "gcp_id_token_secret_id" => GcpIdTokenSecret, "aws_auth_secret_id" => AwsAuthSecret,
                  "oauth_token_secret_id" => OauthTokenSecret, "pg_dsn_secret_id" => PgDsnSecret,
                  "hmac_secret_id" => HmacSecret }.freeze
  KEYS = {
    "catalog.principals" => %w[slack_channel_id], "catalog.roles" => [], "catalog.role" => %w[reference], "catalog.credentials" => %w[q],
    "principal.read" => [], "principal.roles.read" => [], "principal.grants.read" => [], "principal.default_skill" => %w[skill],
    "principal.sandbox_access" => FLAGS + [ "sandbox_repo_cache" ],
    "principal.role.assign" => %w[role_id], "principal.role.remove" => %w[role_id],
    "principal.credential.grant" => GRANT_TYPES.keys, "principal.credential.revoke" => %w[grant_id],
    "principal.slack_permission" => %w[channel_id upload_enabled download_enabled history_enabled],
    "principal.slack_permission.remove" => %w[channel_id]
  }.freeze

  def self.validate!(document)
    raise Unsupported, "provide a typed operation, target and arguments" unless document.is_a?(Hash) && (document.keys - %w[operation target arguments]).empty?
    operation, target, arguments = document.values_at("operation", "target", "arguments")
    keys = KEYS[operation]
    raise Unsupported, "operation is not registered; propose a reviewed broker operation instead of retrying admin HTTP" unless keys
    raise Unsupported, "target must be a principal OID or exact foreign ID" unless target.is_a?(String) && target.match?(/\A[A-Za-z0-9._~-]{1,200}\z/)
    raise Unsupported, "unexpected operation arguments" unless arguments.is_a?(Hash) && (arguments.keys - keys).empty?
    valid = case operation
    when "catalog.principals" then target == "principals" && (arguments.empty? || channel?(arguments["slack_channel_id"]))
    when "catalog.roles" then target == "roles" && arguments.empty?
    when "catalog.role" then target == "roles" && arguments.length == 1 && arguments["reference"].is_a?(String) && arguments["reference"].match?(/\A[A-Za-z0-9._~-]{1,200}\z/)
    when "catalog.credentials" then target == "credentials" && (arguments.empty? || arguments["q"].is_a?(String) && arguments["q"].match?(/\A[A-Za-z0-9_.: -]{1,160}\z/))
    when "principal.read", "principal.roles.read", "principal.grants.read" then arguments.empty?
    when "principal.default_skill"
      arguments.key?("skill") && (arguments["skill"].nil? || arguments["skill"].is_a?(String) && arguments["skill"].match?(/\A[a-z0-9][a-z0-9-]{0,127}\z/))
    when "principal.sandbox_access"
      arguments.any? && arguments.all? { |key, value| key == "sandbox_repo_cache" ? %w[none public all].include?(value) : value == true || value == false }
    when "principal.slack_permission"
      channel?(arguments["channel_id"]) && (arguments.keys - [ "channel_id" ]).any? &&
        arguments.except("channel_id").values.all? { |value| value == true || value == false }
    when "principal.slack_permission.remove" then channel?(arguments["channel_id"])
    when "principal.credential.grant" then arguments.length == 1 && arguments.values.first.is_a?(String) && arguments.values.first.match?(/\A(?:ssr|gas|gid|aas|ots|pgs|hms)_[a-zA-Z0-9]{1,100}\z/)
    else arguments.length == 1 && arguments.values.first.is_a?(String) && arguments.values.first.match?(/\A[a-z]+_[a-zA-Z0-9]+\z/)
    end
    raise Unsupported, "invalid arguments for #{operation}" unless valid
    { "operation" => operation, "target" => target, "arguments" => arguments.sort.to_h }
  end

  def self.channel?(value)
    value.is_a?(String) && value.match?(/\A[CG][A-Z0-9]{8,}\z/)
  end

  # Recognize supported existing CLI routes before issuing an approval request.
  def self.from_http(method, path, parameters)
    if method == "GET"
      catalog = { "/api/v1/sandbox/admin/principals" => "principals", "/api/v1/sandbox/admin/roles" => "roles", "/api/v1/sandbox/admin/secrets" => "credentials" }[path]
      if catalog
        arguments = catalog == "principals" && parameters["slack_channel_id"].present? ? { "slack_channel_id" => parameters["slack_channel_id"].to_s.upcase } : {}
        arguments = { "q" => parameters["q"].to_s } if catalog == "credentials" && parameters["q"].present?
        return validate!({ "operation" => "catalog.#{catalog}", "target" => catalog, "arguments" => arguments })
      end
    end
    if method == "GET" && (role = path.match(%r{\A/api/v1/sandbox/admin/roles/(?:lookup/)?([A-Za-z0-9._~-]+)\z}))
      return validate!({ "operation" => "catalog.role", "target" => "roles", "arguments" => { "reference" => role[1] } })
    end
    if method == "DELETE" && path.match?(%r{\A/api/v1/sandbox/admin/grants/grant_[a-zA-Z0-9]+\z})
      grant = Grant.find_by_oid!(path.split("/").last)
      raise Unsupported, "role credential revocation requires its own typed handler" unless grant.principal
      return validate!({ "operation" => "principal.credential.revoke", "target" => grant.principal.oid, "arguments" => { "grant_id" => grant.oid } })
    end
    if method == "POST" && path == "/api/v1/sandbox/admin/grants"
      data = parameters.fetch("data", {}).to_h.stringify_keys
      target = data.delete("principal_id")
      return validate!({ "operation" => "principal.credential.grant", "target" => target, "arguments" => data })
    end
    match = path.match(%r{\A/api/v1/sandbox/admin/principals/(?:lookup/)?([A-Za-z0-9._~-]+)(.*)\z})
    return unless match
    target, suffix = match.captures
    args = parameters.fetch("data", {}).to_h.stringify_keys
    operation = case [ method, suffix ]
    when [ "GET", "" ] then "principal.read"
    when [ "GET", "/roles" ] then "principal.roles.read"
    when [ "GET", "/grants" ] then "principal.grants.read"
    when [ "PATCH", "/default_skill" ] then "principal.default_skill"
    when [ "PATCH", "/sandbox_access" ] then "principal.sandbox_access"
    when [ "POST", "/roles" ] then "principal.role.assign"
    when [ "POST", "/slack_channel_permissions" ] then "principal.slack_permission"
    else
      if method == "DELETE" && suffix.match?(%r{\A/roles/role_[a-zA-Z0-9]+\z})
        args = { "role_id" => suffix.split("/").last }; "principal.role.remove"
      elsif method == "DELETE" && suffix.match?(%r{\A/slack_channel_permissions/[CG][A-Z0-9]+\z})
        args = { "channel_id" => suffix.split("/").last }; "principal.slack_permission.remove"
      end
    end
    validate!({ "operation" => operation, "target" => target, "arguments" => args }) if operation
  end

  def self.execute!(document, user:)
    operation, target, arguments = validate!(document).values_at("operation", "target", "arguments")
    if operation == "catalog.principals"
      records = Principal.order(:id)
      records = records.where(slack_channel_id: arguments["slack_channel_id"]) if arguments["slack_channel_id"]
      raise Unsupported, "Filter this principal catalog to a channel before requesting more than 1000 records" if records.count > 1000
      return records.map { |record| record.attributes.slice("foreign_id", "name", "kind", "slack_channel_id", "labels", *FLAGS).merge("id" => record.oid) }
    elsif operation == "catalog.roles"
      raise Unsupported, "Role catalog exceeds the supported 1000-record limit" if Role.count > 1000
      return Role.order(:id).map { |role| { "id" => role.oid, "foreign_id" => role.foreign_id, "name" => role.name } }
    elsif operation == "catalog.role"
      reference = arguments.fetch("reference")
      role = reference.start_with?("role_") ? Role.find_by_oid!(reference) : Role.find_by!(foreign_id: reference)
      return { "id" => role.oid, "foreign_id" => role.foreign_id, "name" => role.name }
    elsif operation == "catalog.credentials"
      raise Unsupported, "Credential catalog exceeds the supported 1000-record limit" if GRANT_TYPES.values.sum(&:count) > 1000
      records = GRANT_TYPES.flat_map { |key, model| model.order(:id).map { |secret| { "id" => secret.oid, "foreign_id" => secret.foreign_id, "name" => secret.try(:name), "grant_key" => key } } }
      return records unless arguments["q"]
      return records.select { |record| [ record["id"], record["foreign_id"], record["name"] ].compact.any? { |value| value.downcase.include?(arguments["q"].downcase) } }
    end
    principal = target.start_with?("prn_") ? Principal.find_by_oid!(target) : Principal.find_by!(foreign_id: target)
    case operation
    when "principal.read" # metadata only
    when "principal.roles.read"
      return principal.roles.order(:id).map { |role| { "id" => role.oid, "foreign_id" => role.foreign_id, "name" => role.name } }
    when "principal.grants.read"
      return principal.grants.order(:id).map { |grant| grant_metadata(grant) }
    when "principal.default_skill"
      raise Unsupported, "default skills require a Slack channel" unless principal.kind == "slack_channel"
      principal.with_lock do
        labels = principal.labels.dup
        arguments["skill"].nil? ? labels.delete("slack_default_skill") : labels["slack_default_skill"] = arguments["skill"]
        principal.update!(labels: labels)
      end
    when "principal.sandbox_access" then principal.update!(arguments)
    when "principal.role.assign"
      role = Role.find_by_oid!(arguments.fetch("role_id"))
      PrincipalRole.find_or_create_by!(principal: principal, role: role)
      return { "principal_id" => principal.oid, "role_id" => role.oid }
    when "principal.role.remove"
      role = Role.find_by_oid!(arguments.fetch("role_id"))
      principal.principal_roles.where(role: role).destroy_all
      return { "principal_id" => principal.oid, "role_id" => role.oid, "assigned" => false }
    when "principal.credential.grant"
      key, oid = arguments.first
      secret = GRANT_TYPES.fetch(key).find_by_oid!(oid)
      grant = Grant.create_with(created_by: user).find_or_create_by!(principal: principal, key.delete_suffix("_id") => secret)
      return { "id" => grant.oid, "principal_id" => principal.oid, key => secret.oid }
    when "principal.credential.revoke"
      grant = principal.grants.find_by_oid!(arguments.fetch("grant_id"))
      grant.destroy!
      return { "id" => grant.oid, "revoked" => true }
    when "principal.slack_permission"
      permission = principal.slack_channel_permissions.find_or_initialize_by(channel_id: arguments.fetch("channel_id"))
      permission.update!(arguments.except("channel_id"))
      return permission.attributes.slice("channel_id", "upload_enabled", "download_enabled", "history_enabled")
    when "principal.slack_permission.remove"
      principal.slack_channel_permissions.where(channel_id: arguments.fetch("channel_id")).destroy_all
      return { "channel_id" => arguments.fetch("channel_id"), "removed" => true }
    end
    receipt = principal.reload.attributes.slice("foreign_id", "name", "kind", "labels", "sandbox_repo_cache", *FLAGS).merge("id" => principal.oid)
    if operation == "principal.read"
      receipt["roles"] = principal.roles.order(:id).map { |role| { "id" => role.oid, "name" => role.name, "foreign_id" => role.foreign_id } }
      receipt["grants"] = principal.grants.order(:id).map { |grant| grant_metadata(grant) }
    end
    receipt
  end
  def self.read?(operation)
    operation.start_with?("catalog.") || %w[principal.read principal.roles.read principal.grants.read].include?(operation)
  end

  def self.grant_metadata(grant)
    { "id" => grant.oid, "principal_id" => grant.principal.oid, GRANT_TYPES.key(grant.grantable.class) => grant.grantable.oid }
  end
end
