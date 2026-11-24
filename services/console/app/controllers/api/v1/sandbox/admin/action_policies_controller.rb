module Api
  module V1
    module Sandbox
      module Admin
        class ActionPoliciesController < Api::BaseController
          include SandboxAdminAuthentication

          def index
            render json: { data: policies.order(:id).map { |policy| {
              id: policy.oid, origin_principal: policy.origin_principal.oid,
              workflow_name: policy.workflow_name, operation: policy.document,
              approved_by: policy.approved_by_slack_user
            } } }
          end

          def destroy
            policies.find_by_oid!(params[:id]).destroy!
            head :no_content
          end

          private

          def policies
            AdminActionPolicy.joins(:origin_principal).where(principals: { console_user_id: current_user.id })
          end
        end
      end
    end
  end
end
