module Api
  module V1
    module Sandbox
      class AdminActionsController < Api::SandboxBaseController
        def create
          action = AdminActionBroker.request!(proxy: current_proxy, operation: params.require(:data).to_unsafe_h)
          AdminActionSlack.post!(action)
          render json: { data: action.public_payload }
        rescue AdminActionBroker::Denied, AdminActionOperations::Unsupported => e
          render_error(status: :forbidden, message: e.message)
        end

        def show
          action = AdminActionRequest.find_by_oid!(params[:id])
          unless action.worker_principal_id == current_proxy.principal_id &&
                 action.workflow_task_id == current_proxy.labels["centaur.workflow_task_id"]
            return render_error(status: :forbidden, message: "Access request belongs to another execution.")
          end
          render json: { data: action.public_payload }
        end
      end
    end
  end
end
