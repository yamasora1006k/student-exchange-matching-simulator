#!/usr/bin/env bash
set -Eeuo pipefail
export AWS_PAGER=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_FILE="$PROJECT_DIR/.deployment-state"
PROJECT_TAG="GraphMatchingSimulator"
MANAGED_BY_TAG="deploy.sh"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
INSTANCE_TYPE="${INSTANCE_TYPE:-}"
GITHUB_REPO_URL="${GITHUB_REPO_URL:-}"
GITHUB_REF="${GITHUB_REF:-}"
ALLOWED_CIDR="${ALLOWED_CIDR:-0.0.0.0/0}"
AUTO_APPROVE="${AUTO_APPROVE:-false}"
DEPLOY_SUCCEEDED=false
INSTANCE_ID=""
SECURITY_GROUP_ID=""
TEMP_USER_DATA=""

usage() {
  cat <<'USAGE'
Usage: ./deploy/deploy.sh [options]

Options:
  --region REGION          AWS Region（default: AWS CLI設定またはap-northeast-1）
  --instance-type TYPE     EC2 instance type（環境変数 INSTANCE_TYPE でも指定可）
  --repo-url URL           公開GitHub clone URL（環境変数 GITHUB_REPO_URL でも指定可）
  --ref REF                cloneするbranch/tag（環境変数 GITHUB_REF でも指定可）
  --allowed-cidr CIDR      TCP 8501の許可元（default: 0.0.0.0/0）
  --yes                    料金確認を省略（環境変数 AUTO_APPROVE=true と同じ）
  -h, --help               このヘルプを表示
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region) REGION="${2:?--regionには値が必要です}"; shift 2 ;;
    --instance-type) INSTANCE_TYPE="${2:?--instance-typeには値が必要です}"; shift 2 ;;
    --repo-url) GITHUB_REPO_URL="${2:?--repo-urlには値が必要です}"; shift 2 ;;
    --ref) GITHUB_REF="${2:?--refには値が必要です}"; shift 2 ;;
    --allowed-cidr) ALLOWED_CIDR="${2:?--allowed-cidrには値が必要です}"; shift 2 ;;
    --yes) AUTO_APPROVE=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

command -v aws >/dev/null 2>&1 || { echo "AWS CLIが必要です。" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curlが必要です。" >&2; exit 1; }
command -v git >/dev/null 2>&1 || { echo "gitが必要です。" >&2; exit 1; }

if [[ -z "$REGION" ]]; then
  REGION="$(aws configure get region 2>/dev/null || true)"
fi
REGION="${REGION:-ap-northeast-1}"

AWS_ARGS=(--region "$REGION")
if [[ -n "${AWS_PROFILE:-}" ]]; then
  AWS_ARGS+=(--profile "$AWS_PROFILE")
fi
aws_cli() { aws "${AWS_ARGS[@]}" "$@"; }

cleanup_on_error() {
  local status=$?
  trap - ERR INT TERM
  [[ "$DEPLOY_SUCCEEDED" == true ]] && exit "$status"
  echo "Deployment failed. 今回作成したリソースをロールバックします。" >&2
  if [[ -n "$INSTANCE_ID" ]]; then
    aws_cli ec2 get-console-output --instance-id "$INSTANCE_ID" --latest --query Output --output text 2>/dev/null || true
    aws_cli ec2 terminate-instances --instance-ids "$INSTANCE_ID" >/dev/null 2>&1 || true
    aws_cli ec2 wait instance-terminated --instance-ids "$INSTANCE_ID" 2>/dev/null || true
  fi
  if [[ -n "$SECURITY_GROUP_ID" ]]; then
    for _ in $(seq 1 12); do
      aws_cli ec2 delete-security-group --group-id "$SECURITY_GROUP_ID" >/dev/null 2>&1 && break
      sleep 5
    done
  fi
  [[ -n "$TEMP_USER_DATA" ]] && rm -f "$TEMP_USER_DATA"
  rm -f "$STATE_FILE"
  exit "$status"
}
trap cleanup_on_error ERR INT TERM

echo "AWS認証を確認しています…"
IDENTITY_ARN="$(aws_cli sts get-caller-identity --query Arn --output text)"
aws_cli ec2 describe-regions --region-names "$REGION" --query 'Regions[0].RegionName' --output text >/dev/null
echo "認証: $IDENTITY_ARN"
echo "Region: $REGION"

if [[ "$AUTO_APPROVE" != true ]]; then
  echo
  echo "AWS resources may incur charges if your account is not eligible for Free Tier."
  read -r -p "Continue? [y/N] " response
  [[ "$response" =~ ^[Yy]$ ]] || { echo "Canceled."; exit 0; }
fi

ACTIVE_INSTANCE="$(aws_cli ec2 describe-instances \
  --filters \
    "Name=tag:Project,Values=$PROJECT_TAG" \
    "Name=tag:ManagedBy,Values=$MANAGED_BY_TAG" \
    'Name=instance-state-name,Values=pending,running,stopping,stopped' \
  --query 'Reservations[].Instances[].InstanceId | [0]' --output text)"
if [[ -n "$ACTIVE_INSTANCE" && "$ACTIVE_INSTANCE" != "None" ]]; then
  echo "既存のプロジェクト用インスタンス $ACTIVE_INSTANCE があります。先に ./deploy/destroy.sh を実行してください。" >&2
  exit 1
fi

if [[ -z "$GITHUB_REPO_URL" ]]; then
  GITHUB_REPO_URL="$(git -C "$PROJECT_DIR" config --get remote.origin.url 2>/dev/null || true)"
fi
if [[ "$GITHUB_REPO_URL" =~ ^git@github.com:(.+)$ ]]; then
  GITHUB_REPO_URL="https://github.com/${BASH_REMATCH[1]}"
fi
GITHUB_REPO_URL="${GITHUB_REPO_URL%.git}.git"
[[ -n "$GITHUB_REPO_URL" ]] || { echo "GITHUB_REPO_URLまたはgit remote originを指定してください。" >&2; exit 1; }

if [[ -z "$GITHUB_REF" ]]; then
  GITHUB_REF="$(git -C "$PROJECT_DIR" branch --show-current 2>/dev/null || true)"
fi
GITHUB_REF="${GITHUB_REF:-main}"
REMOTE_REF="$(GIT_TERMINAL_PROMPT=0 git ls-remote "$GITHUB_REPO_URL" "$GITHUB_REF" "refs/heads/$GITHUB_REF" "refs/tags/$GITHUB_REF" 2>/dev/null || true)"
[[ -n "$REMOTE_REF" ]] || { echo "公開リポジトリまたはrefを匿名cloneできません: $GITHUB_REPO_URL ($GITHUB_REF)" >&2; exit 1; }

VPC_ID="$(aws_cli ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)"
[[ -n "$VPC_ID" && "$VPC_ID" != "None" ]] || { echo "Region $REGION にDefault VPCがありません。" >&2; exit 1; }

SUBNET_ID=""
for candidate_subnet in $(aws_cli ec2 describe-subnets \
  --filters "Name=vpc-id,Values=$VPC_ID" Name=default-for-az,Values=true \
  --query 'Subnets[?MapPublicIpOnLaunch==`true`].SubnetId' --output text); do
  route_table="$(aws_cli ec2 describe-route-tables --filters "Name=association.subnet-id,Values=$candidate_subnet" --query 'RouteTables[0].RouteTableId' --output text)"
  if [[ -z "$route_table" || "$route_table" == "None" ]]; then
    route_table="$(aws_cli ec2 describe-route-tables --filters "Name=vpc-id,Values=$VPC_ID" Name=association.main,Values=true --query 'RouteTables[0].RouteTableId' --output text)"
  fi
  gateway="$(aws_cli ec2 describe-route-tables --route-table-ids "$route_table" --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'].GatewayId | [0]" --output text)"
  if [[ "$gateway" == igw-* ]]; then
    SUBNET_ID="$candidate_subnet"
    break
  fi
done
[[ -n "$SUBNET_ID" ]] || { echo "Public IPv4とInternet Gateway経路を持つDefault Subnetがありません。" >&2; exit 1; }

FREE_TYPES="$(aws_cli ec2 describe-instance-types \
  --filters Name=free-tier-eligible,Values=true \
  --query 'InstanceTypes[].InstanceType' --output text | tr '\t\n' '  ')"
if [[ -z "$INSTANCE_TYPE" ]]; then
  for preferred in t3.micro t2.micro t4g.micro t3.small t4g.small c7i-flex.large m7i-flex.large; do
    if [[ " $FREE_TYPES " == *" $preferred "* ]]; then
      INSTANCE_TYPE="$preferred"
      break
    fi
  done
  if [[ -z "$INSTANCE_TYPE" ]]; then
    INSTANCE_TYPE="$(printf '%s\n' $FREE_TYPES | head -n 1)"
  fi
fi
[[ -n "$INSTANCE_TYPE" ]] || { echo "Free Tier対象として返るインスタンスタイプがありません。INSTANCE_TYPEで明示してください。" >&2; exit 1; }

INSTANCE_ARCH="$(aws_cli ec2 describe-instance-types --instance-types "$INSTANCE_TYPE" --query 'InstanceTypes[0].ProcessorInfo.SupportedArchitectures[0]' --output text)"
[[ -n "$INSTANCE_ARCH" && "$INSTANCE_ARCH" != "None" ]] || { echo "利用できないinstance typeです: $INSTANCE_TYPE" >&2; exit 1; }
if [[ " $FREE_TYPES " != *" $INSTANCE_TYPE "* ]]; then
  echo "WARNING: $INSTANCE_TYPE はAWS API上でFree Tier対象と表示されていません。" >&2
fi

case "$INSTANCE_ARCH" in
  x86_64) AMI_PATTERN='al2023-ami-2023.*-x86_64' ;;
  arm64) AMI_PATTERN='al2023-ami-2023.*-arm64' ;;
  *) echo "未対応のCPU architectureです: $INSTANCE_ARCH" >&2; exit 1 ;;
esac

read -r AMI_ID ROOT_DEVICE_NAME <<<"$(aws_cli ec2 describe-images \
  --owners amazon \
  --filters \
    "Name=name,Values=$AMI_PATTERN" \
    "Name=architecture,Values=$INSTANCE_ARCH" \
    Name=state,Values=available \
    Name=root-device-type,Values=ebs \
    Name=virtualization-type,Values=hvm \
    Name=free-tier-eligible,Values=true \
  --query 'reverse(sort_by(Images,&CreationDate))[0].[ImageId,RootDeviceName]' --output text)"
[[ -n "$AMI_ID" && "$AMI_ID" != "None" ]] || { echo "Free Tier対象のAmazon Linux 2023 AMIを取得できません。" >&2; exit 1; }

DEPLOYMENT_ID="$(date -u +%Y%m%d%H%M%S)-$$"
SECURITY_GROUP_NAME="graph-matching-simulator-$DEPLOYMENT_ID"
SECURITY_GROUP_ID="$(aws_cli ec2 create-security-group \
  --group-name "$SECURITY_GROUP_NAME" \
  --description 'Streamlit access for GraphMatchingSimulator' \
  --vpc-id "$VPC_ID" \
  --query GroupId --output text)"
aws_cli ec2 create-tags --resources "$SECURITY_GROUP_ID" --tags \
  "Key=Project,Value=$PROJECT_TAG" \
  "Key=ManagedBy,Value=$MANAGED_BY_TAG" \
  "Key=Deployment,Value=$DEPLOYMENT_ID"
aws_cli ec2 authorize-security-group-ingress \
  --group-id "$SECURITY_GROUP_ID" \
  --ip-permissions "IpProtocol=tcp,FromPort=8501,ToPort=8501,IpRanges=[{CidrIp=$ALLOWED_CIDR,Description=Streamlit}]" >/dev/null

TEMP_USER_DATA="$(mktemp "${TMPDIR:-/tmp}/graph-matching-user-data.XXXXXX")"
repo_b64="$(printf '%s' "$GITHUB_REPO_URL" | base64 | tr -d '\n')"
ref_b64="$(printf '%s' "$GITHUB_REF" | base64 | tr -d '\n')"
sed \
  -e "s|__REPO_URL_B64__|$repo_b64|g" \
  -e "s|__REPO_REF_B64__|$ref_b64|g" \
  "$SCRIPT_DIR/user-data.sh" > "$TEMP_USER_DATA"
USER_DATA_SIZE="$(wc -c < "$TEMP_USER_DATA" | tr -d ' ')"
(( USER_DATA_SIZE <= 16384 )) || { echo "user-dataが16KB制限を超えています。" >&2; exit 1; }

INSTANCE_TAGS="ResourceType=instance,Tags=[{Key=Name,Value=graph-matching-simulator},{Key=Project,Value=$PROJECT_TAG},{Key=ManagedBy,Value=$MANAGED_BY_TAG},{Key=Deployment,Value=$DEPLOYMENT_ID}]"
VOLUME_TAGS="ResourceType=volume,Tags=[{Key=Project,Value=$PROJECT_TAG},{Key=ManagedBy,Value=$MANAGED_BY_TAG},{Key=Deployment,Value=$DEPLOYMENT_ID}]"
RUN_ARGS=(
  --image-id "$AMI_ID"
  --instance-type "$INSTANCE_TYPE"
  --count 1
  --network-interfaces "DeviceIndex=0,SubnetId=$SUBNET_ID,Groups=$SECURITY_GROUP_ID,AssociatePublicIpAddress=true"
  --metadata-options HttpTokens=required,HttpEndpoint=enabled,HttpPutResponseHopLimit=1
  --block-device-mappings "DeviceName=$ROOT_DEVICE_NAME,Ebs={VolumeSize=12,VolumeType=gp3,Encrypted=true,DeleteOnTermination=true}"
  --user-data "file://$TEMP_USER_DATA"
  --tag-specifications "$INSTANCE_TAGS" "$VOLUME_TAGS"
)
case "$INSTANCE_TYPE" in
  t2.*|t3.*|t3a.*|t4g.*) RUN_ARGS+=(--credit-specification CpuCredits=standard) ;;
esac

echo "EC2を起動します: type=$INSTANCE_TYPE, AMI=$AMI_ID, subnet=$SUBNET_ID"
INSTANCE_ID="$(aws_cli ec2 run-instances "${RUN_ARGS[@]}" --query 'Instances[0].InstanceId' --output text)"

umask 077
cat > "$STATE_FILE" <<STATE
REGION='$REGION'
INSTANCE_ID='$INSTANCE_ID'
SECURITY_GROUP_ID='$SECURITY_GROUP_ID'
DEPLOYMENT_ID='$DEPLOYMENT_ID'
STATE

echo "Instance $INSTANCE_ID の起動を待っています…"
aws_cli ec2 wait instance-running --instance-ids "$INSTANCE_ID"
aws_cli ec2 wait instance-status-ok --instance-ids "$INSTANCE_ID"

PUBLIC_DNS="$(aws_cli ec2 describe-instances --instance-ids "$INSTANCE_ID" --query 'Reservations[0].Instances[0].PublicDnsName' --output text)"
PUBLIC_IP="$(aws_cli ec2 describe-instances --instance-ids "$INSTANCE_ID" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"
HOST="$PUBLIC_DNS"
[[ -n "$HOST" && "$HOST" != "None" ]] || HOST="$PUBLIC_IP"
APP_URL="http://$HOST:8501"

echo "Docker buildとStreamlit起動を待っています…"
READY=false
for attempt in $(seq 1 90); do
  if curl --fail --silent --max-time 5 "$APP_URL/_stcore/health" >/dev/null 2>&1; then
    READY=true
    break
  fi
  if (( attempt % 6 == 0 )); then
    echo "  setup中です ($((attempt * 10))秒経過)"
  fi
  sleep 10
done
[[ "$READY" == true ]] || {
  echo "Streamlitの起動確認がタイムアウトしました。user-dataログを取得します。" >&2
  aws_cli ec2 get-console-output --instance-id "$INSTANCE_ID" --latest --query Output --output text >&2 || true
  false
}

DEPLOY_SUCCEEDED=true
trap - ERR INT TERM
rm -f "$TEMP_USER_DATA"

echo
echo "Deployment complete!"
echo
echo "App URL:"
echo "$APP_URL"
echo
echo "Instance ID: $INSTANCE_ID"
echo "Instance type: $INSTANCE_TYPE"
echo "Destroy: ./deploy/destroy.sh"
