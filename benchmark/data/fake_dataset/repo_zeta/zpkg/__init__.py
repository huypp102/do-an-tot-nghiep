"""Repo GIẢ dựng CỐ Ý để kiểm chứng nhãn VACUOUS (PHẦN 1.3).

Tình huống mô phỏng: hotspot được bộ test gọi ở lượt GHI ĐỐI SỐ (nên nó phát
lại được và đi hết tới bước thay bằng Rust), nhưng ở lượt chạy HYBRID thì code
path gọi nó KHÔNG được đi qua. Khi đó bộ test vẫn xanh mà hàm Rust chưa từng
chạy -- "đúng một cách rỗng". Nếu pipeline tính lượt đó là regression-free thì
số liệu sẽ đẹp một cách giả tạo.
"""
