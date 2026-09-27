//! Extension PyO3 cho phần HYBRID: chỉ các hàm HOTSPOT (vòng lặp per-pixel
//! nặng CPU) được viết bằng Rust; phần "khung" còn lại của pipeline vẫn ở
//! Python (xem versions/hybrid_pyo3/pipeline.py).
//!
//! SCAFFOLD: mỗi hàm dưới đây hiện là STUB (chưa có logic thật, chỉ chạm dữ
//! liệu tối thiểu để trả về buffer đúng kích thước) để build/bind PyO3 chạy
//! được ngay. Điền logic thật bằng cách port từ repo viraj7 -- thuật toán
//! giống hệt phần mô tả trong versions/rust_pure/src/main.rs và
//! versions/python_pure/pipeline.py, chỉ khác cách expose sang Python (qua
//! PyO3 #[pyfunction] thay vì CLI/stdout).
//!
//! Build: pip install maturin
//!        cd versions/hybrid_pyo3 && maturin develop --release
//! (chạy trong venv Python đang dùng để benchmark, xem README.md).
//!
//! I/O hiện dùng `Vec<u8>` thô (ảnh grayscale phẳng, row-major) truyền qua
//! Python `bytes`, để scaffold không phụ thuộc crate `numpy`. Nếu muốn tránh
//! copy dữ liệu, cân nhắc đổi sang crate `numpy` (PyReadonlyArray2 /
//! PyArray2) -- xem TODO trong Cargo.toml.

use pyo3::prelude::*;

/// TODO: port edge_det() thật (xem versions/rust_pure/src/main.rs::edge_det_stub
/// và versions/python_pure/pipeline.py::edge_det để biết thuật toán gốc).
#[pyfunction]
fn edge_det(image: Vec<u8>, _width: usize, _height: usize, _sig: f64) -> PyResult<Vec<u8>> {
    // --- STUB: trả nguyên input, chưa có logic thật ---
    Ok(image)
}

/// TODO: port harris() thật. Hàm thật trả về ảnh RGB (width*height*3 byte).
#[pyfunction]
fn harris(image: Vec<u8>, width: usize, height: usize) -> PyResult<Vec<u8>> {
    let mut out = vec![0u8; width * height * 3];
    for i in 0..image.len().min(width * height) {
        out[i * 3] = image[i];
    }
    Ok(out)
}

/// TODO: port hess_corner_det() thật. Trả về ảnh RGB (width*height*3 byte).
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
        out[i * 3] = image[i];
    }
    Ok(out)
}

/// TODO: port im_threshold() thật (entropy-based threshold, xem chi tiết
/// thuật toán trong versions/python_pure/pipeline.py::im_threshold).
#[pyfunction]
fn im_threshold(image: Vec<u8>, _width: usize, _height: usize) -> PyResult<Vec<u8>> {
    Ok(image.iter().map(|&v| if v >= 128 { 255 } else { 0 }).collect())
}

#[pymodule]
fn hybrid_pyo3_ext(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(edge_det, m)?)?;
    m.add_function(wrap_pyfunction!(harris, m)?)?;
    m.add_function(wrap_pyfunction!(hess_corner_det, m)?)?;
    m.add_function(wrap_pyfunction!(im_threshold, m)?)?;
    Ok(())
}
