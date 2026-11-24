require "test_helper"

class Api::V1::AdminActionResolutionTest < ActionDispatch::IntegrationTest
  setup do
    @origin = principals(:acme_channel)
    @origin.update!(console_user: users(:acme_admin), slack_team_id: "T0123456789",
                    labels: @origin.labels.merge("sandbox_admin" => "true"))
    @worker = Principal.create!(foreign_id: "workflow-access-http-test", kind: "workflow", created_by: users(:acme_admin))
    @proxy = Proxy.create!(name: "access-http-sandbox", principal: @worker, labels: {
      "centaur.admin_origin_principal" => @origin.oid,
      "centaur.workflow_task_id" => "00000000-0000-0000-0000-000000000001",
      "centaur.workflow_run_id" => "00000000-0000-0000-0000-000000000002",
      "centaur.workflow_agent_key" => "build", "centaur.workflow_name" => "access_http_test",
      "centaur.slack_thread_ts" => "1234567890.123456"
    })
  end

  test "a supported denied admin call automatically posts the menu and never mutates" do
    posts = []
    post = ->(action) { posts << action.oid; action.update!(message_ts: "1234567891.123456") }
    with_env("CENTAUR_JWT_SIGNING_SECRET" => "test-secret") do
      headers = { "Authorization" => "Bearer #{SandboxEntitlements::Jwt.encode_for_proxy(@proxy)}" }
      AdminActionBroker.stub(:active_task!, ->(*) { }) do
        AdminActionSlack.stub(:post!, post) do
          assert_difference("AdminActionRequest.count", 1) do
            patch "/api/v1/sandbox/admin/principals/#{@origin.oid}/default_skill",
                  params: { data: { skill: "full-send" } }, headers: headers, as: :json
          end
          assert_response :forbidden
          error = JSON.parse(response.body).fetch("error")
          assert_equal "admin_approval_required", error.fetch("code")
          assert_equal "waiting_for_access", error.dig("resolution", "status")
          assert_equal @proxy.labels["centaur.workflow_run_id"], error.dig("resolution", "workflow_run_id")
          assert_equal [ error.dig("resolution", "id") ], posts
          assert_nil @origin.reload.labels["slack_default_skill"]
          post "/api/v1/admin_actions/#{error.dig('resolution', 'id')}/decide",
               params: { data: { decision: "once", user_id: "U0123456789" } }, headers: headers, as: :json
          assert_response :unauthorized
          assert_nil @origin.reload.labels["slack_default_skill"]
        end
      end
    end
  end

  test "request document cannot choose a trusted origin or a worker identity" do
    with_env("CENTAUR_JWT_SIGNING_SECRET" => "test-secret") do
      headers = { "Authorization" => "Bearer #{SandboxEntitlements::Jwt.encode_for_proxy(@proxy)}" }
      assert_no_difference("AdminActionRequest.count") do
        post "/api/v1/sandbox/admin_actions", headers: headers, as: :json,
             params: { data: { operation: "principal.read", target: @origin.oid, arguments: {}, origin_principal: @origin.oid } }
      end
      assert_response :forbidden
    end
  end
end
