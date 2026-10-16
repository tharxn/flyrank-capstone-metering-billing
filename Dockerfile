FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd --create-home billing && mkdir -p /app/data && chown -R billing:billing /app
USER billing
EXPOSE 8000
CMD ["python", "-m", "app.cli", "run"]
