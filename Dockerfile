FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/logs /app/memory/life/projects /app/memory/life/areas \
    /app/memory/life/resources /app/memory/life/archives \
    /app/memory/daily /app/memory/tacit

CMD ["python", "-m", "src.orchestrator"]
