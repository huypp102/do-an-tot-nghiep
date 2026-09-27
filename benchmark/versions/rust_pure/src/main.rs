//! Bản Rust THUẦN (biên dịch sẵn) của pipeline tiền xử lý ảnh.
//!
//! SCAFFOLD: chưa có logic xử lý ảnh thật. Mỗi hàm `*_stub` chỉ chạm (touch)
//! từng byte của buffer ảnh giả để mô phỏng khung của một vòng lặp per-pixel,
//! rồi trả về buffer đúng kích thước -- đủ để đo "khung" chạy được và để
//! runner/bench.py có số liệu ngay. Số liệu này KHÔNG phản ánh tốc độ thuật
//! toán thật cho tới khi bạn điền TODO bên dưới.
//!
//! Khi điền logic thật, port trực tiếp từ các file gốc trong repo:
//!   https://github.com/viraj7/Computer-Vision-Image-processing
//!     edge_det          <- edge_detection.py::edge_det
//!     harris            <- "harris corner detection.py"::harris
//!     hess_corner_det   <- "hessian corner detection.py"::hess_corner_det
//!     im_threshold      <- image_entropy.py::im_threshold
//! (xem versions/python_pure/pipeline.py để biết chi tiết thuật toán gốc
//! từng hàm, đã ghi lại từ source thật).
//!
//! Contract CLI (bench.py gọi qua subprocess, xem runner/bench.py):
//!   rust_pure <function> <width> <height> <iterations> <warmup>
//! In ra stdout mỗi dòng: `ITER <index> <elapsed_ns>` cho mỗi lần đo (SAU
//! warmup, warmup không in). Chạy nhiều iteration TRONG CÙNG 1 process để
//! loại bỏ overhead khởi động process khỏi phép đo (chỉ đo phần tính toán).
//!
//! Build: cargo build --release   (yêu cầu Rust >= 1.66 vì dùng std::hint::black_box)

use std::env;
use std::time::Instant;

/// Sinh ảnh xám giả (u8, row-major), cùng kiểu dữ liệu mà data/loader.py sản
/// xuất (grayscale uint8), để hàm stub có dữ liệu "giống thật" để chạm vào.
fn dummy_image(width: usize, height: usize) -> Vec<u8> {
    let mut buf = vec![0u8; width * height];
    for y in 0..height {
        for x in 0..width {
            buf[y * width + x] =
                (((x * 255 / width.max(1)) + (y * 255 / height.max(1))) / 2) as u8;
        }
    }
    buf
}

/// TODO: thay thân hàm bằng edge_det thật (Gaussian smoothing X/Y ->
/// derivative of Gaussian -> magnitude/orientation -> non-max suppression ->
/// hysteresis thresholding). Hiện tại chỉ chạm từng byte.
fn edge_det_stub(image: &[u8], width: usize, height: usize) -> Vec<u8> {
    let mut out = vec![0u8; width * height];
    for i in 0..out.len() {
        out[i] = image[i].wrapping_add(0); // TODO: thay bằng phép tính thật
    }
    out
}

/// TODO: port harris() thật (Gaussian smooth -> Ix/Iy -> Laplace -> response
/// Harris R = det(H) - alpha*trace(H)^2 -> threshold). Hàm thật trả về ảnh
/// RGB (width*height*3 byte), stub hiện tại trả cùng kích thước đó.
fn harris_stub(image: &[u8], width: usize, height: usize) -> Vec<u8> {
    let mut out = vec![0u8; width * height * 3];
    for i in 0..image.len() {
        out[i * 3] = image[i]; // TODO: thay bằng phép tính thật (kênh R = corner marker)
    }
    out
}

/// TODO: port hess_corner_det() thật (Gaussian smooth -> đạo hàm bậc 1/2 ->
/// eigenvalues Hessian mỗi pixel -> threshold |l1|,|l2| > th).
fn hess_corner_det_stub(image: &[u8], width: usize, height: usize) -> Vec<u8> {
    let mut out = vec![0u8; width * height * 3];
    for i in 0..image.len() {
        out[i * 3] = image[i]; // TODO: thay bằng phép tính thật
    }
    out
}

/// TODO: port im_threshold() thật (histogram 256 bin -> tối đa hoá entropy
/// H(A)+H(B) theo ngưỡng T -> nhị phân hoá theo T).
fn im_threshold_stub(image: &[u8], width: usize, height: usize) -> Vec<u8> {
    let mut out = vec![0u8; width * height];
    for i in 0..out.len() {
        out[i] = if image[i] >= 128 { 255 } else { 0 }; // TODO: thay bằng entropy threshold thật
    }
    out
}

fn run_stub(function: &str, image: &[u8], width: usize, height: usize) -> Result<Vec<u8>, String> {
    match function {
        "edge_det" => Ok(edge_det_stub(image, width, height)),
        "harris" => Ok(harris_stub(image, width, height)),
        "hess_corner_det" => Ok(hess_corner_det_stub(image, width, height)),
        "im_threshold" => Ok(im_threshold_stub(image, width, height)),
        other => Err(format!(
            "Không hỗ trợ function '{other}'. Các lựa chọn: edge_det | harris | \
             hess_corner_det | im_threshold"
        )),
    }
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() != 6 {
        eprintln!(
            "Usage: {} <function> <width> <height> <iterations> <warmup>",
            args.first().map(String::as_str).unwrap_or("rust_pure")
        );
        std::process::exit(2);
    }

    let function = &args[1];
    let width: usize = args[2].parse().expect("width phải là số nguyên");
    let height: usize = args[3].parse().expect("height phải là số nguyên");
    let iterations: usize = args[4].parse().expect("iterations phải là số nguyên");
    let warmup: usize = args[5].parse().expect("warmup phải là số nguyên");

    let image = dummy_image(width, height);

    for _ in 0..warmup {
        if let Err(e) = run_stub(function, &image, width, height) {
            eprintln!("Error: {e}");
            std::process::exit(1);
        }
    }

    for i in 0..iterations {
        let start = Instant::now();
        match run_stub(function, &image, width, height) {
            Ok(out) => {
                // Ngăn compiler loại bỏ toàn bộ vòng lặp (dead code elimination)
                // khi thân hàm chỉ là stub đơn giản.
                std::hint::black_box(&out);
            }
            Err(e) => {
                eprintln!("Error: {e}");
                std::process::exit(1);
            }
        }
        let elapsed_ns = start.elapsed().as_nanos();
        println!("ITER {i} {elapsed_ns}");
    }
}
