//! Extension PyO3 cho bản Rust THUẦN (rust_pure), gọi IN-PROCESS thay vì qua
//! subprocess CLI.
//!
//! LÝ DO có crate này: rust_pure trước đây được benchmark bằng cách
//! runner/bench.py spawn 1 tiến trình con chạy versions/rust_pure/src/main.rs
//! (CLI, nhận argv, lặp N lần BÊN TRONG tiến trình con đó). Cách đo đó công
//! bằng hơn kiểu spawn-mỗi-lần-gọi, nhưng vẫn KHÔNG cùng điều kiện với
//! python_pure/hybrid_pyo3 (đo bằng time.perf_counter() NGAY TRONG tiến
//! trình Python đang chạy, không spawn gì cả). Use-case thật của đồ án là
//! gọi hàm preprocessing LẶP LẠI nhiều lần trong CÙNG 1 tiến trình training
//! (theo batch/epoch), không phải chạy như 1 chương trình rời mỗi lần gọi.
//! Crate này expose cùng các hàm đó qua PyO3 để gọi trực tiếp trong tiến
//! trình Python, cho phép đo bằng đúng time.perf_counter(), cùng điều kiện
//! với 2 phiên bản còn lại.
//!
//! versions/rust_pure/src/main.rs (bản CLI, Cargo.toml ở thư mục cha) được
//! GIỮ NGUYÊN KHÔNG ĐỔI -- vẫn build/chạy độc lập bằng `cargo build --release`
//! nếu muốn test nhanh không qua Python, nhưng KHÔNG còn được
//! runner/bench.py dùng để đo tốc độ nữa.
//!
//! SCAFFOLD: các hàm dưới đây là STUB giống hệt logic trong
//! versions/rust_pure/src/main.rs (edge_det_stub, harris_stub,
//! hess_corner_det_stub, im_threshold_stub) -- xem file đó và
//! versions/python_pure/pipeline.py để biết chi tiết thuật toán gốc cần port.
//!
//! LƯU Ý QUAN TRỌNG: đây là crate Cargo ĐỘC LẬP với ../src/main.rs (không
//! dùng chung code, theo đúng yêu cầu giữ main.rs nguyên vẹn), nên code stub
//! bị LẶP LẠI CÓ CHỦ ĐÍCH giữa 2 nơi. TODO KHI ĐIỀN LOGIC THẬT: phải cập
//! nhật ĐỒNG BỘ ở CẢ HAI nơi (../src/main.rs và file này). Nếu muốn tránh
//! trùng lặp, có thể tự refactor: tách thuật toán ra 1 lib crate dùng chung,
//! ../Cargo.toml thêm [lib] (rlib, không phụ thuộc pyo3) cho main.rs dùng,
//! rồi crate này phụ thuộc path vào lib đó thay vì tự định nghĩa lại.
//!
//! Build: pip install maturin
//!        cd versions/rust_pure/pyo3_ext && maturin develop --release
//! (chạy trong venv Python đang dùng để benchmark, xem README.md).
//!
//! I/O dùng `Vec<u8>` thô (ảnh grayscale phẳng, row-major) truyền qua Python
//! `bytes`, giống hệt cách versions/hybrid_pyo3/src/lib.rs làm.

use pyo3::prelude::*;

/// Giống hệt edge_det_stub trong ../src/main.rs.
#[pyfunction]
fn edge_det(image: Vec<u8>, _width: usize, _height: usize, _sig: f64) -> PyResult<Vec<u8>> {
    let out: Vec<u8> = image.iter().map(|&v| v.wrapping_add(0)).collect(); // TODO: thay bằng phép tính thật
    Ok(out)
}

/// Giống hệt harris_stub trong ../src/main.rs -- trả về ảnh RGB (width*height*3 byte).
#[pyfunction]
fn harris(image: Vec<u8>, width: usize, height: usize) -> PyResult<Vec<u8>> {
    let mut out = vec![0u8; width * height * 3];
    for i in 0..image.len().min(width * height) {
        out[i * 3] = image[i]; // TODO: thay bằng phép tính thật
    }
    Ok(out)
}

/// Giống hệt hess_corner_det_stub trong ../src/main.rs -- trả về ảnh RGB.
#[pyfunction]
fn hess_corner_det(
    image: Vec<u8>,
    width: usize,
    height: usize,
    _sig: f64,
    _th: f64,
) -> PyResult<Vec<u8>> {
    let mut out = vec![0u8; width * height * 3];
    for i in 0..image.len().min(width * height) {
        out[i * 3] = image[i]; // TODO: thay bằng phép tính thật
    }
    Ok(out)
}

/// Giống hệt im_threshold_stub trong ../src/main.rs.
#[pyfunction]
fn im_threshold(image: Vec<u8>, _width: usize, _height: usize) -> PyResult<Vec<u8>> {
    Ok(image.iter().map(|&v| if v >= 128 { 255 } else { 0 }).collect())
}

#[pymodule]
fn rust_pure_ext(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(edge_det, m)?)?;
    m.add_function(wrap_pyfunction!(harris, m)?)?;
    m.add_function(wrap_pyfunction!(hess_corner_det, m)?)?;
    m.add_function(wrap_pyfunction!(im_threshold, m)?)?;
    Ok(())
}
