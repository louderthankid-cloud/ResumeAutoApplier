# тег версии должен совпадать с playwright в requirements.txt
FROM mcr.microsoft.com/playwright/python:v1.59.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# отдельно для гигачата, так как конфликт версий лангчейна 
RUN pip install --no-cache-dir gigachat==0.1.43 \
 && pip install --no-cache-dir --no-deps langchain-gigachat==0.3.12

COPY . .

RUN sed -i 's/\r$//' docker-entrypoint.sh && chmod +x docker-entrypoint.sh

ENTRYPOINT ["./docker-entrypoint.sh"]
