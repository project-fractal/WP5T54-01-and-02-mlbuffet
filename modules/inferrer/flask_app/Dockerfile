ARG PYTHON_VERSION=3.8

FROM python:${PYTHON_VERSION}

WORKDIR /usr/src/flask_app

RUN apt-get update
RUN apt-get install ffmpeg libsm6 libxext6 iputils-ping -y

COPY requirements.txt .

RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY modelhost_manager.py .
COPY trainer_executor.py .
COPY storage_talker.py .
COPY api_test.py .
COPY utils utils

RUN mkdir /home/logs
RUN mkdir /trainerfiles
RUN mv utils/find.py /trainerfiles/find.py

#run integration testing
RUN ["chmod", "+x", "./api_test.py"]
#RUN python ./api_test.py

#http
CMD ["gunicorn", "-w", "1", "-k", "gevent", "--worker-connections", "1000", "-b", "0.0.0.0:8000", "app:server"]

