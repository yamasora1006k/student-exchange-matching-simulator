#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_FILE="$PROJECT_DIR/.deployment-state"
PROJECT_TAG="GraphMatchingSimulator"
MANAGED_BY_TAG="deploy.sh"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
AUTO_APPROVE="${AUTO_APPROVE:-false}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region) REGION="${2:?--regionには値が必要です}"; shift 2 ;;
    --yes) AUTO_APPROVE=true; shift ;;
    -h|--help)
      echo "Usage: ./deploy/destroy.sh [--region REGION] [--yes]"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

command -v aws >/dev/null 2>&1 || { echo "AWS CLIが必要です。" >&2; exit 1; }

STATE_INSTANCE_ID=""
STATE_SECURITY_GROUP_ID=""
if [[ -f "$STATE_FILE" ]]; then
  # このファイルはdeploy.sh自身がumask 077で作成した固定形式の状態ファイル。
  # shellcheck disable=SC1090
  source "$STATE_FILE"
  REGION="${REGION:-${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-northeast-1}}}"
  STATE_INSTANCE_ID="${INSTANCE_ID:-}"
  STATE_SECURITY_GROUP_ID="${SECURITY_GROUP_ID:-}"
fi

if [[ -z "$REGION" ]]; then
  REGION="$(aws configure get region 2>/dev/null || true)"
fi
REGION="${REGION:-ap-northeast-1}"
AWS_ARGS=(--region "$REGION")
if [[ -n "${AWS_PROFILE:-}" ]]; then
  AWS_ARGS+=(--profile "$AWS_PROFILE")
fi
aws_cli() { aws "${AWS_ARGS[@]}" "$@"; }

aws_cli sts get-caller-identity --query Arn --output text >/dev/null

if [[ "$AUTO_APPROVE" != true ]]; then
  read -r -p "Project=$PROJECT_TAG のEC2とSecurity Groupを削除します。Continue? [y/N] " response
  [[ "$response" =~ ^[Yy]$ ]] || { echo "Canceled."; exit 0; }
fi

INSTANCE_IDS="$(aws_cli ec2 describe-instances \
  --filters \
    "Name=tag:Project,Values=$PROJECT_TAG" \
    "Name=tag:ManagedBy,Values=$MANAGED_BY_TAG" \
    'Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down' \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
if [[ -n "$STATE_INSTANCE_ID" && "$INSTANCE_IDS" != *"$STATE_INSTANCE_ID"* ]]; then
  INSTANCE_IDS="$INSTANCE_IDS $STATE_INSTANCE_ID"
fi

if [[ -n "${INSTANCE_IDS// }" && "$INSTANCE_IDS" != "None" ]]; then
  echo "EC2を終了します: $INSTANCE_IDS"
  # shellcheck disable=SC2086
  aws_cli ec2 terminate-instances --instance-ids $INSTANCE_IDS >/dev/null
  # shellcheck disable=SC2086
  aws_cli ec2 wait instance-terminated --instance-ids $INSTANCE_IDS
else
  echo "削除対象のEC2はありません。"
fi

SECURITY_GROUP_IDS="$(aws_cli ec2 describe-security-groups \
  --filters "Name=tag:Project,Values=$PROJECT_TAG" "Name=tag:ManagedBy,Values=$MANAGED_BY_TAG" \
  --query 'SecurityGroups[].GroupId' --output text)"
if [[ -n "$STATE_SECURITY_GROUP_ID" && "$SECURITY_GROUP_IDS" != *"$STATE_SECURITY_GROUP_ID"* ]]; then
  SECURITY_GROUP_IDS="$SECURITY_GROUP_IDS $STATE_SECURITY_GROUP_ID"
fi

for group_id in $SECURITY_GROUP_IDS; do
  [[ "$group_id" == "None" ]] && continue
  echo "Security Groupを削除します: $group_id"
  deleted=false
  for _ in $(seq 1 15); do
    if aws_cli ec2 delete-security-group --group-id "$group_id" >/dev/null 2>&1; then
      deleted=true
      break
    fi
    sleep 4
  done
  [[ "$deleted" == true ]] || { echo "Security Group $group_id を削除できませんでした。" >&2; exit 1; }
done

rm -f "$STATE_FILE"
echo "Destroy complete. Projectタグ付きリソースを削除しました。"
