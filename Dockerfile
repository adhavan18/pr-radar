FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY prgraph prgraph
COPY web web
COPY data data
COPY docs/eval_results.json docs/eval_results.json
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn web.app:app --host 0.0.0.0 --port ${PORT}"]
