require "test_helper"

class AdminActionBrokerTest < ActiveSupport::TestCase
  setup do
    @origin = principals(:acme_channel)
    @origin.update!(console_user: users(:acme_admin), slack_team_id: "T0123456789",
                    labels: @origin.labels.merge("sandbox_admin" => "true"))
    @catalog = slack_bot_channels(:general)
    @catalog.update!(name: "trusted", bot_user_id: "UBOT0000001", member_user_ids: %w[UBOT0000001 UADMIN00001])
    @worker = Principal.create!(foreign_id: "workflow-test-access", kind: "workflow", created_by: users(:acme_admin))
    @proxy = Proxy.create!(name: "access-test-sandbox", principal: @worker, labels: {
      "centaur.admin_origin_principal" => @origin.oid,
      "centaur.workflow_task_id" => "00000000-0000-0000-0000-000000000001",
      "centaur.workflow_run_id" => "00000000-0000-0000-0000-000000000002",
      "centaur.workflow_agent_key" => "build", "centaur.workflow_name" => "test_access",
      "centaur.slack_thread_ts" => "1234567890.123456"
    })
    @operation = { "operation" => "principal.default_skill", "target" => @origin.oid, "arguments" => { "skill" => "full-send" } }
  end

  def create_request(operation = @operation)
    AdminActionBroker.stub(:active_task!, ->(*) { }) { AdminActionBroker.request!(proxy: @proxy, operation: operation) }
  end

  def decide(request, decision = "once", user: "UADMIN00001", channel: @origin.slack_channel_id)
    AdminActionBroker.stub(:active_task!, ->(*) { }) do
      AdminActionBroker.decide!(request: request, decision: decision, user_id: user,
                               team_id: @origin.slack_team_id, channel_id: channel,
                               message_ts: request.message_ts, refresh_membership: false)
    end
  end

  test "a blocker records one exact request without applying it" do
    request = create_request
    assert_equal "waiting_for_access", request.status
    assert_nil @origin.reload.labels["slack_default_skill"]
    assert_equal request.id, create_request.id
    assert_equal @proxy.labels["centaur.workflow_task_id"], request.workflow_task_id
  end

  test "approval menu uses the Slack confirmation schema and persists delivery" do
    request = create_request
    payloads = []
    AdminActionSlack.stub(:api, ->(_method, payload) { payloads << payload; { "ts" => "1234567891.123456" } }) do
      AdminActionSlack.post!(request)
    end
    confirm = payloads.first[:blocks][1][:elements][1][:confirm]
    assert_equal "Cancel", confirm.fetch(:deny).fetch(:text)
    assert_not confirm.key?(:cancel)
    assert_equal "1234567891.123456", request.reload.message_ts
    assert_equal "waiting_for_access", request.status
    assert_nil @origin.reload.labels["slack_default_skill"]
  end

  test "rejected button cards fall back to exact reply choices without applying an action" do
    request = create_request
    payloads = []
    api = lambda do |_method, payload|
      payloads << payload
      raise SlackApi::Error.new("invalid blocks", code: "invalid_blocks") if payload.key?(:blocks)
      { "ts" => "1234567891.123456" }
    end
    AdminActionSlack.stub(:api, api) { AdminActionSlack.post!(request) }
    assert_equal 2, payloads.length
    assert_not payloads.last.key?(:blocks)
    assert_includes payloads.last[:text], "approve once #{request.oid}"
    assert_includes payloads.last[:text], "deny #{request.oid}"
    assert_equal payloads.first[:client_msg_id], payloads.last[:client_msg_id]
    assert_equal "1234567891.123456", request.reload.message_ts
    assert_equal "waiting_for_access", request.status
    assert_nil @origin.reload.labels["slack_default_skill"]
  end

  test "approval executes once and records the original workflow and actor" do
    request = create_request
    request.update!(message_ts: "1234567891.123456")
    decide(request)
    assert_equal "completed", request.reload.status
    assert_equal "full-send", @origin.reload.labels["slack_default_skill"]
    assert_equal "UADMIN00001", request.approved_by_slack_user
    assert_equal "full-send", request.receipt["labels"]["slack_default_skill"]
    assert_no_difference("AdminActionPolicy.count") { decide(request) }
    assert_equal "completed", request.reload.status
  end

  test "wrong channel nonmember and expired approvals cannot mutate" do
    request = create_request
    request.update!(message_ts: "1234567891.123456")
    assert_raises(AdminActionBroker::Denied) { decide(request, channel: "COTHER00001") }
    assert_raises(AdminActionBroker::Denied) { decide(request, user: "UNOBODY0001") }
    request.update!(expires_at: 1.minute.ago)
    assert_raises(AdminActionBroker::Denied) { decide(request) }
    assert_nil @origin.reload.labels["slack_default_skill"]
  end

  test "revoking originating administrator prevents an outstanding approval" do
    request = create_request
    request.update!(message_ts: "1234567891.123456")
    @origin.update!(labels: @origin.labels.except("sandbox_admin"))
    assert_raises(AdminActionBroker::Denied) { decide(request) }
    assert_nil @origin.reload.labels["slack_default_skill"]
  end

  test "deny preserves settings and is terminal" do
    request = create_request
    request.update!(message_ts: "1234567891.123456")
    decide(request, "deny")
    assert_equal "denied", request.reload.status
    decide(request)
    assert_nil @origin.reload.labels["slack_default_skill"]
  end

  test "a standing policy matches origin workflow target and exact arguments" do
    request = create_request
    request.update!(message_ts: "1234567891.123456")
    decide(request, "workflow")
    @proxy.update!(labels: @proxy.labels.merge("centaur.workflow_task_id" => "00000000-0000-0000-0000-000000000002"))
    assert_equal "completed", create_request.status
    changed = @operation.deep_dup
    changed["arguments"]["skill"] = "another-skill"
    assert_equal "waiting_for_access", create_request(changed).status
  end

  test "role membership is a second typed operation with idempotent readback" do
    role = roles(:acme_admin_role)
    request = create_request({ "operation" => "principal.role.assign", "target" => @worker.oid,
                               "arguments" => { "role_id" => role.oid } })
    request.update!(message_ts: "1234567891.123456")
    assert_not_includes @worker.roles, role
    decide(request)
    assert_includes @worker.reload.roles, role
    assert_equal role.oid, request.reload.receipt["role_id"]
    assert_no_difference("PrincipalRole.count") { decide(request) }
  end

  test "unsupported operation extra parameters and arbitrary flag names fail closed" do
    assert_raises(AdminActionOperations::Unsupported) { create_request(@operation.merge("operation" => "http.request")) }
    assert_raises(AdminActionOperations::Unsupported) { create_request(@operation.merge("arguments" => { "skill" => "x", "admin" => true })) }
    assert_raises(AdminActionOperations::Unsupported) do
      create_request({ "operation" => "principal.sandbox_access", "target" => @worker.oid, "arguments" => { "admin" => true } })
    end
  end

  test "workflow proxy without server supplied origin cannot request approval" do
    @proxy.update!(labels: {})
    assert_raises(AdminActionBroker::Denied) { create_request }
  end
  test "a cancelled or missing workflow cannot authorize a stale operation" do
    request = create_request
    api = Minitest::Mock.new
    api.expect(:get_workflow_run, { "task_id" => request.workflow_task_id, "workflow_name" => request.workflow_name, "status" => "cancelled" }, [ request.workflow_run_id ])
    CentaurApiClient.stub(:new, api) do
      assert_raises(AdminActionBroker::Denied) { AdminActionBroker.active_task!(request) }
    end
    api.verify
  end
  test "a linked admin DM validates its actual recipient and rejects a different DM" do
    @origin.update!(kind: "slack_dm", slack_user_id: "UADMIN00001", slack_channel_id: nil)
    @proxy.update!(labels: @proxy.labels.merge("centaur.admin_delivery_channel" => "DDIRECT00001"))
    AdminActionSlack.stub(:api, ->(*) { { "channel" => { "is_im" => true, "user" => "UADMIN00001" } } }) do
      assert_equal "DDIRECT00001", create_request.channel_id
    end
    AdminActionSlack.stub(:api, ->(*) { { "channel" => { "is_im" => true, "user" => "UOTHER00001" } } }) do
      assert_raises(AdminActionBroker::Denied) { create_request }
    end
  end

  test "removing a principal preserves action audit and invalidates future approval" do
    request = create_request
    original_oid = @origin.oid
    @origin.destroy!
    assert_nil request.reload.origin_principal
    assert_equal original_oid, request.origin_principal_oid
    assert_raises(AdminActionBroker::Denied) { AdminActionBroker.origin_admin!(request.origin_principal) }
  end
  test "permission catalogs are explicit metadata-only approved operations" do
    request = create_request({ "operation" => "catalog.credentials", "target" => "credentials", "arguments" => {} })
    assert_equal "completed", request.status
    assert_equal "origin_read", request.approval_scope
    assert request.reload.receipt.is_a?(Array)
    assert request.receipt.any?
    assert request.receipt.all? { |record| (record.keys - %w[id foreign_id name grant_key]).empty? }
  end
end
