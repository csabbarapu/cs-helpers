#!/bin/bash
# This script updates the desired count of ECS Fargate services in a specified cluster.
#
# ====== USAGE ======
# ./script.sh <cluster-name> <desired-count>
# - <cluster-name>: The name of the ECS cluster.
# - <desired-count>: The desired number of tasks for each Fargate service (must be a positive integer).
#
# ====== REQUIREMENTS ======
# - AWS CLI must be installed and configured with appropriate permissions.
# - jq must be installed for JSON parsing.
#
# ====== FUNCTIONALITY ======
# 1. Fetches all ECS services in the specified cluster using `aws ecs list-services`.
# 2. Filters services with the "FARGATE" launch type.
# 3. Updates the desired count of each Fargate service using `aws ecs update-service`.
# 4. Logs all operations to a file named `update-fargate-services.log`.
#
# ====== NOTES ======
# - Services with a launch type other than "FARGATE" are skipped.
# - The script runs updates in parallel for efficiency.
# - If no services are found in the cluster, the script exits gracefully.
#
# ====== EXIT CODES ======
# - 0: Success or no services found.
# - 1: Invalid usage or missing dependencies.
# - 2: AWS CLI or jq is not installed.

set -euo pipefail

# ====== CONFIGURATION ======
LOG_FILE="update-fargate-services.log"
exec > >(tee -a "$LOG_FILE") 2>&1

# ====== FUNCTIONS ======
fetch_services() {
  local cluster_name=$1
  local next_token=""
  declare -a services=()

  while :; do
    RESPONSE=$(aws ecs list-services --no-cli-pager --cluster "$cluster_name" --output json --max-items 100 ${next_token:+--starting-token "$next_token"})
    services+=($(jq -r '.serviceArns[]' <<<"$RESPONSE"))
    next_token=$(jq -r '.NextToken // empty' <<<"$RESPONSE")
    [[ -z "$next_token" ]] && break
  done

  echo "${services[@]}"
}

update_service() {
  local cluster_name=$1
  local service_name=$2
  local desired_count=$3
  local max_retries=5
  local retry_delay=1

  for ((attempt=1; attempt<=max_retries; attempt++)); do
    LAUNCH_TYPE=$(aws ecs describe-services \
      --no-cli-pager \
      --cluster "$cluster_name" \
      --services "$service_name" \
      --query 'services[0].launchType' \
      --output text 2>/dev/null)

    if [[ "$LAUNCH_TYPE" == "FARGATE" ]]; then
      echo "🔧 Updating '$service_name' (Fargate) → Desired count: $desired_count"
      if aws ecs update-service \
        --no-cli-pager \
        --cluster "$cluster_name" \
        --service "$service_name" \
        --desired-count "$desired_count" \
        --force-new-deployment \
        >/dev/null; then
        echo "✅ Updated: $service_name"
        return 0
      else
        echo "⚠️ Attempt $attempt failed for service '$service_name'. Retrying in $retry_delay seconds..."
        sleep $retry_delay
        retry_delay=$((retry_delay * 2)) # Exponential backoff
      fi
    else
      echo "⏭️ Skipping '$service_name' (Launch type: $LAUNCH_TYPE)"
      return 0
    fi
  done

  echo "❌ Failed to update service '$service_name' after $max_retries attempts."
  return 1
}

# ====== MAIN ======
CLUSTER_NAME="${1:-}"
DESIRED_COUNT="${2:-}"

if [[ -z "$CLUSTER_NAME" || -z "$DESIRED_COUNT" ]]; then
  echo "Usage: $0 <cluster-name> <desired-count>"
  exit 1
fi

if ! [[ "$DESIRED_COUNT" =~ ^[0-9]+$ ]]; then
  echo "❌ Error: Desired count must be a positive integer."
  exit 1
fi

if ! command -v aws &>/dev/null || ! command -v jq &>/dev/null; then
  echo "❌ Error: AWS CLI and jq must be installed."
  exit 1
fi

echo "🔍 Fetching ECS services for cluster: $CLUSTER_NAME..."
SERVICES=($(fetch_services "$CLUSTER_NAME"))

if [[ ${#SERVICES[@]} -eq 0 ]]; then
  echo "⚠️ No services found in cluster '$CLUSTER_NAME'"
  exit 0
fi

echo "📦 Total services found: ${#SERVICES[@]}"

for SERVICE_ARN in "${SERVICES[@]}"; do
  SERVICE_NAME=$(basename "$SERVICE_ARN")
  update_service "$CLUSTER_NAME" "$SERVICE_NAME" "$DESIRED_COUNT" &
done
wait

echo "🎉 All eligible services updated."
