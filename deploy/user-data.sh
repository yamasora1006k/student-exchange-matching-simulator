#!/bin/bash
set -Eeuo pipefail

exec > >(tee /var/log/graph-matching-user-data.log | logger -t user-data -s 2>/dev/console) 2>&1

REPOSITORY_URL="$(printf '%s' '__REPO_URL_B64__' | base64 --decode)"
REPOSITORY_REF="$(printf '%s' '__REPO_REF_B64__' | base64 --decode)"
APP_DIRECTORY="/opt/graph-matching-simulator"
IMAGE_NAME="graph-matching-simulator:latest"
CONTAINER_NAME="graph-matching-simulator"

echo "[1/7] Amazon Linux 2023を更新しています"
dnf upgrade -y

echo "[2/7] gitとDockerをインストールしています"
# Amazon Linux 2023にはcurl-minimalが標準搭載されているため、競合するcurlを追加しない。
dnf install -y git docker

echo "[3/7] 小さいインスタンスでのDocker build用にswapを用意しています"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  printf '/swapfile none swap sw 0 0\n' >> /etc/fstab
fi

echo "[4/7] Dockerを有効化しています"
systemctl enable --now docker

echo "[5/7] 公開GitHubリポジトリをcloneしています"
rm -rf "$APP_DIRECTORY"
git clone --depth 1 --branch "$REPOSITORY_REF" "$REPOSITORY_URL" "$APP_DIRECTORY"

echo "[6/7] Docker imageをbuildしています"
docker build --pull --tag "$IMAGE_NAME" "$APP_DIRECTORY"

echo "[7/7] Streamlitコンテナを起動しています"
docker rm --force "$CONTAINER_NAME" >/dev/null 2>&1 || true
docker run \
  --detach \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --publish 8501:8501 \
  "$IMAGE_NAME"

for attempt in $(seq 1 30); do
  if curl --fail --silent --max-time 3 http://127.0.0.1:8501/_stcore/health >/dev/null; then
    touch /var/log/graph-matching-ready
    echo "Streamlit is ready."
    exit 0
  fi
  sleep 3
done

docker logs "$CONTAINER_NAME" || true
echo "Streamlit health check timed out." >&2
exit 1
