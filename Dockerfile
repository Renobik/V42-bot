
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY top100_choch_v4_2_FINAL_FULL.py .
# state files will be created at runtime
CMD ["python", "top100_choch_v4_2_FINAL_FULL.py"]
