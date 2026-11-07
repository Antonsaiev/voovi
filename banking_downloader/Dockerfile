FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Europe/Moscow
WORKDIR /app
COPY banking_downloader/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY banking_downloader/browser.py banking_downloader/runtime.py ./
# Reuse exactly the parser used by the existing Django importer.
COPY s.voovi.ru-api/basic/banking/parser.py ./statement_parser.py
CMD ["python", "runtime.py", "--serve"]
