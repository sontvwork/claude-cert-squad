# Hướng dẫn cho member

## 1. Tham gia team (một lần)

Copy lệnh admin ghim trong space Chat rồi chạy ở thư mục gốc repo:

```bash
python3 scripts/team/setup.py --join <mã>
```

Trả lời các câu hỏi để thiết lập hồ sơ.
Muốn đổi tên hoặc email thì chạy lại lệnh join.

## 2. Học với tutor

- B1: Mở project này bằng Claude Code / Codex / Antigravity
- B2: Chat: "Bắt đầu học Claude"

## 3. Học trên nhiều máy

Tutor tự đồng bộ `CLAUDE-CERTIFICATION.md` và `learning-artifacts/` giữa các máy của bạn. Lần đầu tutor chạy trên máy đang học, đồng bộ tự bật:

- 🔐 Tutor in `Team sync: đã bật đồng bộ nhiều máy`.
- 📧 Hộp thư công ty nhận mail **"Mã thêm máy học chứng chỉ Claude"**, trong đó có lệnh `setup.py --join ...`.

Thêm một máy mới, ví dụ laptop ở nhà:

1. Clone repo.
2. Mở mail trên, chạy lệnh trong mail ở thư mục gốc repo. Không cần chạy lại lệnh join đang ghim trong Chat.
3. Mở tutor như bình thường. Tiến độ đã có sẵn.

Lệnh trong mail chứa khoá đồng bộ riêng của bạn. Đừng chuyển tiếp mail và đừng dán lệnh vào Chat chung.

Không thấy mail thì chạy một trong hai lệnh sau trên máy đang học:

```bash
python3 scripts/team/setup.py --email-device    # gửi lại mail
python3 scripts/team/setup.py --export-device   # in lệnh ra ngay tại đây
```

Dữ liệu được mã hoá bằng khoá này trước khi rời máy. Admin và người xem Sheet không đọc được.

### Khi học ở cả hai máy mà chưa đồng bộ

Nếu cả hai máy cùng sửa một file, tutor sẽ báo có file chờ gộp trong `.team/remote/`. Cứ để tutor gộp: bài nào đã xong ở máy nào cũng được giữ, mọi lần làm mock được giữ. Tutor sẽ hỏi bạn chọn bản artifact nào nếu hai bản khác nhau.

Trước khi tải bản từ máy khác về, script lưu bản cũ vào `.team/backup/`, giữ 5 bản gần nhất.

### Máy đã học trước khi có đồng bộ

1. Chạy `git pull` ở thư mục repo trên máy đang học.
2. Mở lại session tutor. Đồng bộ tự bật.
3. Nếu máy thứ hai cũng đã học dở, chạy lệnh trong mail trên máy thứ hai. Tutor sẽ gộp tiến độ của hai máy.

## 4. Khác

### Khi cần bật hoặc tắt chia sẻ điểm

```bash
python3 scripts/team/setup.py --share off
python3 scripts/team/setup.py --share on
```

Lựa chọn này chỉ ảnh hưởng **điểm số** (quiz, mock) trên Dashboard và Chat. **Tiến độ luôn công khai, có tên.** 
