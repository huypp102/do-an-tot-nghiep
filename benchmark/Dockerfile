# Image cho service `benchmark`. Đã có sẵn Python + Rust toolchain + maturin
# để chạy được TOÀN BỘ pipeline, kể cả Stage 5 (cargo check) và build
# extension PyO3 -- những thứ mà máy dev cá nhân thường chưa cài.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Đường dẫn dự án chứa ký tự ngoài ASCII trên máy dev; bật UTF-8 mode để
    # Scalene và log tiếng Việt không vỡ (đã gặp thật trên Windows).
    PYTHONUTF8=1 \
    CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH=/usr/local/cargo/bin:$PATH

# git: cho input/intake.py clone repo GitHub.
# build-essential + curl: cần để cài Rust và biên dịch extension PyO3.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Rust toolchain -- BẮT BUỘC cho Stage 5 (`cargo check`) và cho việc build
# rust_pure/hybrid_pyo3 qua maturin.
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
      | sh -s -- -y --default-toolchain stable --profile minimal \
    && rustc --version && cargo --version

WORKDIR /app

# Cài dependency trước, copy code sau -> đổi code không phải cài lại từ đầu.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt maturin

COPY . /app

# Mặc định chạy pipeline đầy đủ. Ghi đè khi cần:
#   docker compose run --rm benchmark python stage6_benchmark/bench.py
CMD ["python", "run_pipeline.py"]
