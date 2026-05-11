FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY jellyfin_skeleton_meal.jpg .
COPY . .
RUN mkdir -p /config /output
EXPOSE 5000
CMD ["python", "app.py"]
