#!/usr/bin/env bash
# deploy-scenarios.sh — Deploy all 4 Azure AI Foundry comparison scenarios
#
# Usage:
#   ./deploy-scenarios.sh [SUBSCRIPTION_ID] [LOCATION]
#   ARM_SUBSCRIPTION_ID=<id> ./deploy-scenarios.sh
#
# To deploy a single scenario:
#   ./deploy-scenarios.sh <sub-id> swedencentral public-no-cap-host
#
# To destroy all scenarios:
#   DESTROY=true ./deploy-scenarios.sh <sub-id>

set -euo pipefail

SUBSCRIPTION_ID="${1:-${ARM_SUBSCRIPTION_ID:-}}"
LOCATION="${2:-swedencentral}"
TARGET_SCENARIO="${3:-all}"
DESTROY="${DESTROY:-false}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ordered from simplest to most complex for easier debugging
SCENARIOS=(
  "public-no-cap-host"
  "public-cap-host"
  "private-no-cap-host"
  "private-cap-host"
)

declare -A SCENARIO_VARFILES=(
  ["public-no-cap-host"]="scenarios/public-no-cap-host.tfvars"
  ["public-cap-host"]="scenarios/public-cap-host.tfvars"
  ["private-no-cap-host"]="scenarios/private-no-cap-host.tfvars"
  ["private-cap-host"]="scenarios/private-cap-host.tfvars"
)

# ── Validation ────────────────────────────────────────────────────────────────
if [[ -z "$SUBSCRIPTION_ID" ]]; then
  echo "ERROR: Subscription ID is required."
  echo ""
  echo "  Pass as the first argument:  $0 <subscription-id>"
  echo "  Or set environment variable: export ARM_SUBSCRIPTION_ID=<subscription-id>"
  exit 1
fi

if ! command -v terraform &>/dev/null; then
  echo "ERROR: terraform not found in PATH"
  exit 1
fi

cd "$SCRIPT_DIR"

# ── Init ──────────────────────────────────────────────────────────────────────
echo "======================================================"
echo "  Initializing Terraform"
echo "======================================================"
terraform init -upgrade

# ── Deploy / Destroy loop ─────────────────────────────────────────────────────
declare -A RESULTS

for SCENARIO in "${SCENARIOS[@]}"; do
  # Skip if a specific scenario was requested and this isn't it
  if [[ "$TARGET_SCENARIO" != "all" && "$TARGET_SCENARIO" != "$SCENARIO" ]]; then
    continue
  fi

  VARFILE="${SCENARIO_VARFILES[$SCENARIO]}"

  echo ""
  echo "======================================================"
  if [[ "$DESTROY" == "true" ]]; then
    echo "  Destroying scenario: $SCENARIO"
  else
    echo "  Deploying scenario: $SCENARIO"
  fi
  echo "======================================================"

  # Select or create the workspace for this scenario
  terraform workspace select "$SCENARIO" 2>/dev/null \
    || terraform workspace new "$SCENARIO"

  if [[ "$DESTROY" == "true" ]]; then
    CMD="destroy"
    EXTRA_FLAGS="-auto-approve"
  else
    CMD="apply"
    EXTRA_FLAGS="-auto-approve"
  fi

  if terraform "$CMD" \
    -var-file="$VARFILE" \
    -var="subscription_id=$SUBSCRIPTION_ID" \
    -var="location=$LOCATION" \
    $EXTRA_FLAGS; then
    RESULTS[$SCENARIO]="SUCCESS"
  else
    RESULTS[$SCENARIO]="FAILED"
    echo "WARNING: Scenario '$SCENARIO' failed. Continuing with remaining scenarios..."
  fi
done

# Return to default workspace
terraform workspace select default

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "======================================================"
echo "  DEPLOYMENT SUMMARY"
echo "======================================================"
printf "  %-30s %s\n" "SCENARIO" "RESULT"
printf "  %-30s %s\n" "--------" "------"

ALL_OK=true
for SCENARIO in "${SCENARIOS[@]}"; do
  if [[ "$TARGET_SCENARIO" != "all" && "$TARGET_SCENARIO" != "$SCENARIO" ]]; then
    continue
  fi
  RESULT="${RESULTS[$SCENARIO]:-SKIPPED}"
  printf "  %-30s %s\n" "$SCENARIO" "$RESULT"
  if [[ "$RESULT" == "FAILED" ]]; then
    ALL_OK=false
  fi
done

echo ""
if [[ "$ALL_OK" == "true" ]]; then
  echo "  All scenarios completed successfully."
  echo ""
  echo "  To view outputs for a scenario, run:"
  echo "    terraform workspace select <scenario-name>"
  echo "    terraform output"
  echo ""
  echo "  To destroy a scenario:"
  echo "    DESTROY=true ./deploy-scenarios.sh $SUBSCRIPTION_ID $LOCATION <scenario-name>"
else
  echo "  One or more scenarios failed. Check the output above for details."
  exit 1
fi
