FROM nvidia/cuda:12.8.0-cudnn-runtime-ubuntu22.04

# Set non-interactive to prevent timezone prompts
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    libsndfile1 \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Map python to python3
RUN ln -s /usr/bin/python3 /usr/bin/python

# Create virtual env for the application
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install specific torch version required
RUN pip install --no-cache-dir torch==2.7.0 torchvision==0.22.0 torchaudio==2.7.0 --index-url https://download.pytorch.org/whl/test/cu128

# Copy requirements files
COPY requirements.txt requirements-nvidia.txt ./

# Install remaining requirements
RUN pip install --no-cache-dir -r requirements.txt

# Copy main application code
COPY . .

# Expose the API port
EXPOSE 8000
EXPOSE 5000

# Default command
CMD ["python", "app.py"]
