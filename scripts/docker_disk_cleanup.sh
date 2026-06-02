#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "== Before =="
docker system df || true
du -sh browser_trace remotion/public/jobs jobs 2>/dev/null || true

echo
echo "== Removing local debug/public artifacts =="
rm -rf browser_trace/*
rm -rf remotion/public/jobs

echo
echo "== Pruning Docker build cache =="
docker builder prune -af

echo
echo "== Pruning stopped containers, dangling images, unused networks =="
docker system prune -af

echo
echo "== After =="
docker system df || true
du -sh browser_trace remotion/public/jobs jobs 2>/dev/null || true
