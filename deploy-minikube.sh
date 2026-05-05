#!/usr/bin/env bash
set -euo pipefail

# ── Build and deploy supermarket-compare to local Minikube ──────────────────

echo "▶ Checking Minikube status..."
minikube status --format='{{.Host}}' | grep -q Running || {
  echo "  Minikube is not running. Starting it..."
  minikube start --memory=4096 --cpus=2
}

echo "▶ Pointing Docker to Minikube's daemon..."
eval "$(minikube docker-env)"

echo "▶ Building Docker image (this takes ~3 min on first run due to Playwright)..."
docker build -t supermarket-compare:latest .

echo "▶ Applying Kubernetes manifests..."
kubectl apply -f k8s/deployment.yaml

echo "▶ Waiting for pod to be ready..."
kubectl rollout status deployment/supermarket-compare --timeout=120s

echo ""
echo "✅ Done! Open the app at:"
minikube service supermarket-compare --url
