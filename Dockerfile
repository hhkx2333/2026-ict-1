FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt && useradd --create-home --uid 10001 grading
COPY --chown=grading:grading c_app ./c_app
COPY --chown=grading:grading backend ./backend
COPY --chown=grading:grading examples ./examples
COPY --chown=grading:grading migrations ./migrations
COPY --chown=grading:grading alembic.ini ./
COPY --chown=grading:grading c-prototype/app.py c-prototype/index.html c-prototype/app.js c-prototype/style.css ./c-prototype/
RUN mkdir -p /app/c-prototype/work && chown grading:grading /app/c-prototype/work
USER grading
EXPOSE 18065
CMD ["python", "c-prototype/app.py", "--host", "0.0.0.0"]
