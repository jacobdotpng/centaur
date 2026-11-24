class AdminActionRequest < ApplicationRecord
  oid_prefix "aar"
  belongs_to :origin_principal, class_name: "Principal", optional: true
  belongs_to :worker_principal, class_name: "Principal", optional: true
  validates :status, inclusion: { in: %w[waiting_for_access completed denied expired failed] }

  def document
    { "operation" => operation, "target" => target, "arguments" => arguments }
  end

  def public_payload
    { id: oid, status: status, operation: operation, target: target, arguments: arguments,
      workflow_task_id: workflow_task_id, workflow_run_id: workflow_run_id, workflow_name: workflow_name,
      receipt: receipt, error: error_message, expires_at: expires_at,
      approved_by: approved_by_slack_user, approval_scope: approval_scope, recorded_at: updated_at,
      origin_principal: origin_principal_oid, worker_principal: worker_principal_oid }
  end
end
