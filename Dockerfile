FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


COPY app.py .
COPY ec2.html .
COPY index.html .
COPY icons/ icons/

ENV HOST=0.0.0.0
ENV PORT=5000

EXPOSE 5000

CMD ["python", "-u", "app.py"]
