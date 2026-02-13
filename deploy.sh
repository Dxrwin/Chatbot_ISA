#!/bin/bash
set -e

# Verificar que se haya proporcionado un nombre de imagen
if [ -z "$1" ]; then
  echo "Uso: $0 <nombre_imagen>"
  exit 1
fi

IMAGE_NAME=$1
DOCKERHUB_USER="jeffercda"

# Tag por commit (si no hay git, cae a timestamp)
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  TAG=$(git rev-parse --short HEAD)
else
  TAG=$(date +%Y%m%d%H%M%S)
fi

FULL_IMAGE_SHA="$DOCKERHUB_USER/$IMAGE_NAME:$TAG"
FULL_IMAGE_LATEST="$DOCKERHUB_USER/$IMAGE_NAME:latest"

echo "🔧 Construyendo imagen: $FULL_IMAGE_SHA"
sudo docker build --pull --no-cache -t "$FULL_IMAGE_SHA" .

echo "📤 Subiendo imagen: $FULL_IMAGE_SHA"
sudo docker push "$FULL_IMAGE_SHA"

# Opcional: también actualiza latest (útil si lo quieres conservar)
echo "🏷️ Actualizando tag latest -> $FULL_IMAGE_LATEST"
sudo docker tag "$FULL_IMAGE_SHA" "$FULL_IMAGE_LATEST"
sudo docker push "$FULL_IMAGE_LATEST"

echo "✅ Listo."
echo "👉 En CapRover despliega ESTA imagen (recomendado): $FULL_IMAGE_SHA"