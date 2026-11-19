
name: Diagnostico Yahoo Finance

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  diagnostico:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Descargar repositorio
        uses: actions/checkout@v4

      - name: Configurar Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Instalar dependencias
        run: |
          python -m pip install --upgrade pip
          pip install yfinance==0.2.65 pandas==2.2.3 numpy==2.2.6 scipy

      - name: Ejecutar diagnóstico Yahoo Finance
        run: python -u diagnostico_yahoo.py
