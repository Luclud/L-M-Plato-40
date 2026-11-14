# Demo viewer. From the repo root:  docker compose up --build viewer   -> http://localhost:8501
# data/ and out/ are mounted (data/ is in .dockerignore). insights.md/.jsonl are baked in (rebuild after regenerating).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
COPY viewer/requirements.txt viewer/
RUN pip install --no-cache-dir -r viewer/requirements.txt
COPY plato40 plato40
COPY eval eval
COPY run.py validate.py ./
COPY analysis analysis
COPY insights.md insights.jsonl ./
# committed root decisions (dev = decisions.jsonl, later decisions_holdout.jsonl) so the dataset picker offers
# "Dev year" and "Holdout" too (data/ comes from the volume). Rebuild after committing the holdout run.
COPY decisions*.jsonl decisions*_dq.json ./
COPY viewer viewer
COPY .streamlit .streamlit
ENV PLATO40_DATA=data/quickstart \
    PLATO40_DECISIONS=out/qs.jsonl \
    PLATO40_APPROVALS=out/approvals.json
EXPOSE 8501
# Runs the (offline, deterministic) pipeline first if the decisions file is missing. No secrets needed.
CMD ["sh", "-c", "[ -f \"$PLATO40_DECISIONS\" ] || python run.py \"$PLATO40_DATA\" --out \"$PLATO40_DECISIONS\"; exec streamlit run viewer/app.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false"]
