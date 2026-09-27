"""Phiên bản Rust THUẦN (rust_pure).

Thư mục này chứa 2 thứ độc lập:
  - Cargo.toml + src/main.rs   : bản CLI Rust thuần (cargo build --release),
                                  vẫn dùng được độc lập ngoài Python.
  - pyo3_ext/                   : extension PyO3 (maturin develop --release),
                                  cho phép gọi IN-PROCESS từ Python -- đây là
                                  cách stage6_benchmark/bench.py đo tốc độ rust_pure.

Xem pipeline.py (wrapper Python gọi pyo3_ext) và README.md mục "Cách đo".
"""
