# Use a lightweight official Python 3.11 runtime to optimize container size
FROM python:3.11-slim

# Create a non-root user for strict security and privilege dropping
RUN adduser --disabled-password --gecos '' botuser

# Set the active working directory inside the container
WORKDIR /app

# Set Python to run in unbuffered mode so print() statements appear in Docker logs instantly
ENV PYTHONUNBUFFERED=1

# Install system-level dependencies required for building C-extensions (pandas, ccxt)
# Immediately clean up the apt cache to keep the image footprint minimal
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only the requirements file first.
# This heavily optimizes the Docker build cache so we don't redownload packages if only code changes.
COPY requirements.txt .

# Install Python dependencies without storing pip cache
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application source code into the container
COPY . .

# Create the specific directories needed for state persistence and logs.
# Transfer ownership of the entire /app directory to our secure non-root user.
RUN mkdir -p /app/data /app/logs && \
    chown -R botuser:botuser /app

# Switch context to the non-root user before executing the application
USER botuser

# The default command to spin up the Main Event Loop
CMD ["python", "-m", "app.bot"]