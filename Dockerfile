FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY defeitos/ defeitos/
COPY mock_erp/ mock_erp/
COPY data/ data/

# Sem --once: fica rodando e faz uma rodada a cada INTERVALO_MIN minutos
CMD ["python", "-m", "defeitos.main"]
