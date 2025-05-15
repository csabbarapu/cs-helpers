#!/bin/bash
set -euo pipefail

# === CONFIGURATION ===
SELECTED_ACCOUNTS=("Acc1" "Acc2") # List of AWS account IDs (12-digit numeric strings) for member accounts

# === LOGGING FUNCTION ===
LOG_FILE="guardduty-runtime.log"
exec > >(tee -a "$LOG_FILE") 2>&1
log() {
  echo -e "[$(date +'%Y-%m-%d %H:%M:%S')] $*"
}

# === FUNCTION: Get or create the detector in the admin account ===
get_admin_detector() {
  local detector
  detector=$(aws guardduty list-detectors --query 'DetectorIds[0]' --output text)
  if [[ -z "$detector" || "$detector" == "None" ]]; then
    log "Creating detector in admin account..."
    detector=$(aws guardduty create-detector --enable --query 'DetectorId' --output text)
  fi
  echo "$detector"
}

# === FUNCTION: Enable runtime monitoring in the admin account ===
enable_admin_monitoring() {
  local detector_id="$1"
  log "Enabling runtime monitoring in admin account (detector: $detector_id)..."
  if ! aws guardduty update-detector \
    --detector-id "$detector_id" \
    --features Name=RUNTIME_MONITORING,Status=ENABLED; then
    log "❌ Failed to update detector. Exiting."
    exit 1
  fi

}

# === FUNCTION: Enable monitoring for selected member accounts ===
enable_member_monitoring() {
  local detector_id="$1"
  local member_ids=("$@")
  member_ids=("${member_ids[@]:1}") # Skip first arg (detector_id)

  log "Updating runtime monitoring for selected member accounts..."
  if [[ ${#member_ids[@]} -eq 0 ]]; then
    log "⚠️  No member accounts provided. Skipping update."
    return
  fi
  aws guardduty update-member-detectors \
    --detector-id "$detector_id" \
    --account-ids "${member_ids[@]}" \
    --features '[
      {
        "Name": "RUNTIME_MONITORING",
        "Status": "ENABLED",
        "AdditionalConfiguration": [
          {
            "Name": "EKS_ADDON_MANAGEMENT",
            "Status": "ENABLED"
          },
          {
            "Name": "ECS_FARGATE_AGENT_MANAGEMENT",
            "Status": "ENABLED"
          },
          {
            "Name": "EC2_AGENT_MANAGEMENT",
            "Status": "ENABLED"
          }
        ]
      }
    ]'
}

# === MAIN ===
log "🔍 Getting admin detector ID..."
ADMIN_DETECTOR_ID=$(get_admin_detector)
log "✅ Detector ID: $ADMIN_DETECTOR_ID"

enable_admin_monitoring "$ADMIN_DETECTOR_ID"

log "📋 Retrieving active GuardDuty member accounts..."
ACTIVE_MEMBERS=$(aws guardduty list-members \
  --detector-id "$ADMIN_DETECTOR_ID" \
  --only-associated TRUE \
  --query 'Members[?RelationshipStatus==`Enabled`].AccountId' \
  --output text)

# Filter only selected active accounts
TARGET_MEMBERS=($(grep -Fxf <(printf "%s\n" "${SELECTED_ACCOUNTS[@]}") <(printf "%s\n" $ACTIVE_MEMBERS)))

# Log skipped accounts
for account_id in "${SELECTED_ACCOUNTS[@]}"; do
  if ! grep -q "$account_id" <<<"$ACTIVE_MEMBERS"; then
    log "⚠️  Skipping $account_id (not active or not associated)"
  fi
done

if [[ ${#TARGET_MEMBERS[@]} -eq 0 ]]; then
  log "❌ No valid member accounts to update. None of the selected accounts are active or associated with the GuardDuty detector."
fi
else
log "✅ Runtime Monitoring enabled for ${#TARGET_MEMBERS[@]} selected member accounts."
