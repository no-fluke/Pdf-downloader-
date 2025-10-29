FROM python:3.10.13-slim

# Update and install dependencies using apt
RUN apt-get update -y && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
        musl-dev \
        ffmpeg \
        aria2 \
        make \
        g++ \
        cmake \
        wget \
        unzip \
        chromium \
        chromium-driver \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy all files into the container
COPY . /app/
WORKDIR /app/

# Python dependencies install
RUN pip3 install --no-cache-dir --upgrade pip \
    && pip3 install --no-cache-dir --upgrade --requirement requirements.txt

# Set the command to run the application
CMD python3 main.py
