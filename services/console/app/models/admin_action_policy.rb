class AdminActionPolicy < ApplicationRecord
  oid_prefix "aap"
  belongs_to :origin_principal, class_name: "Principal"
end
