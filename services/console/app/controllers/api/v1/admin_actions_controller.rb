module Api
  module V1
    # Only the service transport accepts a verified Slack decision. A sandbox
    # cannot submit an actor identity through this endpoint.
    class AdminActionsController < Api::BaseController
      def index
        actions = AdminActionRequest.where(workflow_task_id: params.require(:task_id),
                                           workflow_agent_key: params.require(:agent_key)).order(:id)
        actions.each do |action|
          action.with_lock do
            if action.status == "waiting_for_access" && action.expires_at <= Time.current
              action.update!(status: "expired", error_message: "Approval expired; no action was applied.")
            end
          end
          AdminActionSlack.post!(action) if action.status == "waiting_for_access" && action.message_ts.blank?
        end
        render json: { data: actions.map(&:public_payload) }
      end

      def decide
        action = AdminActionRequest.find_by_oid!(params[:id])
        data = data_params.permit(:decision, :user_id, :team_id, :channel_id, :message_ts, :thread_ts).to_h.symbolize_keys
        if data[:message_ts].blank?
          raise AdminActionBroker::Denied, "Reply in the original approval thread." unless
            action.thread_ts.present? && action.thread_ts == data.delete(:thread_ts)
          data[:message_ts] = action.message_ts
        else
          data.delete(:thread_ts)
        end
        AdminActionBroker.decide!(request: action, **data)
        AdminActionSlack.feedback!(action, user_id: data.fetch(:user_id), details: data[:decision] == "details")
        render json: { data: action.public_payload }
      rescue AdminActionBroker::Denied, AdminActionOperations::Unsupported => e
        render_error(status: :forbidden, message: e.message)
      end
    end
  end
end
