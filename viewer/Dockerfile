# Demo viewer. From the repo root:  docker compose up --build viewer   -> http://localhost:8501
# data/ and out/ are mounted (data/ is in .dockerignore).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
COPY viewer/requirements.txt viewer/
RUN pip install --no-cache-dir -r viewer/requirements.txt
COPY plato40 plato40
COPY eval eval
COPY run.py validate.py ./
COPY viewer viewer
ENV PLATO40_DATA=data/quickstart \
    PLATO40_DECISIONS=out/qs.jsonl \
    PLATO40_APPROVALS=out/approvals.json
EXPOSE 8501
# Runs the (offline, deterministic) pipeline first if the decisions file is missing. No secrets needed.
CMD ["sh", "-c", "[ -f \"$PLATO40_DECISIONS\" ] || python run.py \"$PLATO40_DATA\" --out \"$PLATO40_DECISIONS\"; exec streamlit run viewer/app.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false"]
