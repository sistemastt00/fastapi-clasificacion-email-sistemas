#!/bin/bash
# deploy.sh — actualizar y reiniciar el servicio
set -e
cd "$(dirname "$0")"
git pull
pip install -r requirements.txt --quiet
sudo systemctl restart email-sistemas
echo "Deploy completado"
