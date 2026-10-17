FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV NOTICE_MAP_DATA=/data NOTICE_MAP_CONFIG=/config/config.yaml
VOLUME ["/data"]
EXPOSE 8765
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
