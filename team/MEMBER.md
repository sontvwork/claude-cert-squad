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

## 3. Khác

### Khi cần bật hoặc tắt chia sẻ điểm

```bash
python3 scripts/team/setup.py --share off
python3 scripts/team/setup.py --share on
```

Lựa chọn này chỉ ảnh hưởng **điểm số** (quiz, mock) trên Dashboard và Chat. **Tiến độ luôn công khai, có tên.** 
