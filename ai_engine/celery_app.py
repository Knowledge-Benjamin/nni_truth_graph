import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

# Since the user doesn't have Redis installed, we'll use SQLite as the broker & result backend
# Note: SQLite works well for prototyping but concurrent writes can cause Database Is Locked
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "sqla+sqlite:///celery_broker.sqlite")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "db+sqlite:///celery_results.sqlite")

app = Celery('celery_app',
             broker=CELERY_BROKER_URL,
             backend=CELERY_RESULT_BACKEND,
             include=['tasks'])

app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    # 10 threads: fast stages (scrape, mutate) always have free slots
    # even while 3-4 slots are held by slow LLM stages (resolution, extraction)
    worker_concurrency=10,
    task_acks_late=True,        # Don't ack until task completes — prevents silent task loss
    task_time_limit=1800,       # 30 min hard kill
    task_soft_time_limit=1500,  # 25 min: soft warning before kill
)

if __name__ == '__main__':
    app.start()
