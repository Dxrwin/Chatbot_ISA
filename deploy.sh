#!/bin/bash

# Verificar que se haya proporcionado un nombre de imagen
if [ -z "$1" ]; then
  echo "Uso: $0 <nombre_imagen>"
  exit 1
fi

# Variables
IMAGE_NAME=$1
DOCKERHUB_USER="jeffercda"
TAG="latest"
FULL_IMAGE_NAME="$DOCKERHUB_USER/$IMAGE_NAME:$TAG"

# Paso 1: Construir la imagen
echo "Construyendo la imagen Docker: $IMAGE_NAME"
sudo docker build -t $IMAGE_NAME .

# Paso 2: Etiquetar la imagen
echo "Etiquetando la imagen como $FULL_IMAGE_NAME"
sudo docker tag $IMAGE_NAME $FULL_IMAGE_NAME

# Paso 3: Subir la imagen a Docker Hub
echo "Subiendo la imagen a Docker Hub: $FULL_IMAGE_NAME"
sudo docker push $FULL_IMAGE_NAME

echo "✅ Imagen $FULL_IMAGE_NAME generada y subida correctamente."
