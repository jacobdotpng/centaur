class CreateAdminActionRequests < ActiveRecord::Migration[8.1]
  def change
    create_table :admin_action_requests do |t|
      t.references :origin_principal, null: false, foreign_key: { to_table: :principals }
      t.references :worker_principal, null: false, foreign_key: { to_table: :principals }
      t.string :request_key, null: false, index: { unique: true }
      t.string :policy_key, null: false
      t.string :workflow_task_id, null: false
      t.string :workflow_run_id, null: false
      t.string :workflow_agent_key, null: false
      t.string :workflow_name, null: false
      t.string :channel_id, null: false
      t.string :team_id, null: false
      t.string :thread_ts
      t.string :message_ts
      t.string :operation, null: false
      t.string :target, null: false
      t.jsonb :arguments, null: false, default: {}
      t.string :status, null: false, default: "waiting_for_access"
      t.jsonb :receipt
      t.text :error_message
      t.string :approved_by_slack_user
      t.string :approval_scope
      t.datetime :expires_at, null: false
      t.timestamps
    end
    add_index :admin_action_requests, [ :workflow_task_id, :workflow_agent_key ], name: :admin_actions_agent_index
    create_table :admin_action_policies do |t|
      t.references :origin_principal, null: false, foreign_key: { to_table: :principals }
      t.string :policy_key, null: false, index: { unique: true }
      t.string :workflow_name, null: false
      t.jsonb :document, null: false
      t.string :approved_by_slack_user, null: false
      t.timestamps
    end
  end
end
