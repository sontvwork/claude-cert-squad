# Hướng dẫn cho admin

Tổng quan và giao thức: [README](README.md).

## 1. Chuẩn bị Sheet và Apps Script

1. Tạo một Google Sheet. Vào **File → Settings** (Tệp → Cài đặt):
   - **Language / Ngôn ngữ**: chọn `United States` (Hoa Kỳ). Công thức của Dashboard dùng dấu phẩy. Locale `Việt Nam` đòi dấu chấm phẩy, nên mọi ô Dashboard sẽ báo `#ERROR!`. `rebuildDashboard` dừng và báo lỗi nếu locale khác `en_US`.
   - **Time zone / Múi giờ**: chọn `(GMT+07:00) Bangkok, Hanoi, Jakarta`. Tab Dashboard dùng `TODAY()` theo time zone này.
2. Mở **Extensions → Apps Script**. Nếu đã có project dùng để test, mở lại đúng project đó.
3. Vào **Project Settings**, bật **Show "appsscript.json" manifest file in editor**.
4. Dán nội dung 3 file vào editor:
   - `team/apps-script/Code.gs` vào `Code.gs`
   - `team/apps-script/Logic.gs` vào `Logic.gs`, tạo bằng nút **+ → Script** và đặt tên `Logic`
   - `team/apps-script/appsscript.json` vào `appsscript.json`. File này không tạo bằng nút **+**. Nó tự hiện trong danh sách file sau khi làm bước 3. Nếu không muốn dán manifest, chỉ cần chọn time zone `(GMT+07:00) Bangkok, Hanoi, Jakarta` trong **Project Settings**. Quyền truy cập web app sẽ chọn trong hộp thoại Deploy
5. Vào **Project Settings → Script Properties**, thêm:

| Property | Giá trị |
|---|---|
| `TEAM_TOKEN` | Chuỗi bí mật do admin tự đặt, khó đoán |
| `ALLOWED_DOMAIN` | `vti.com.vn` |
| `CHAT_WEBHOOK_URL` | Webhook của space Chat: **Apps & integrations → Webhooks → Add webhook** |
| `SHEET_ID` | Chỉ cần khi project Apps Script đứng riêng, không gắn với Sheet. Lấy từ URL của Sheet |

Không đưa giá trị nào ở trên vào repo. Repo là public.

## 2. Khởi tạo và deploy

1. Trong editor, chọn hàm `setupSheets` rồi bấm **Run**. Lần đầu Google sẽ hỏi quyền. Hàm tạo các tab `Events`, `Members`, `Progress`, `Awards`, `Templates`, `State`, `StateChunks`, `Dashboard`. Chạy lại cũng không mất dữ liệu.
2. Chạy `testChat`. Space Chat phải nhận được một tin nhắn thử.
3. Chạy `testMail`. Google hỏi thêm quyền **Send email as you**, bấm **Allow**. Hộp thư của admin phải nhận được một mail thử. Web app dùng quyền này để gửi cho member lệnh thêm máy (mục 10). Nếu Workspace chặn quyền này, member vẫn lấy được lệnh bằng `setup.py --export-device`.
4. Deploy:
   - Lần đầu: **Deploy → New deployment → Web app**, Execute as **Me**, Who has access **Anyone**.
   - Các lần sau, kể cả khi đã có deployment dùng để test:
     1. **Deploy → Manage deployments** (Triển khai → Quản lý các bản triển khai).
     2. Ở cột trái, chọn deployment đang dùng.
     3. Bấm ✏️ Edit.
     4. Ở ô **Version**, chọn **New version** (Phiên bản mới), rồi bấm **Deploy**.

     URL giữ nguyên nên member không phải tham gia lại.
5. Chạy `installWeeklyTrigger` một lần. Trigger gọi `weeklySummary` vào Chủ nhật, trong khung 22h–23h theo time zone của project.
6. Kiểm tra web app, kết quả phải là `{"ok":true,...}`:

```bash
curl -sL "https://script.google.com/macros/s/<deployment-id>/exec"
```

## 3. Mời member

```bash
python3 scripts/team/setup.py --make-code
```

Script hỏi URL Apps Script, `TEAM_TOKEN`, email domain, rồi in ra lệnh `setup.py --join <mã>`. Ghim lệnh đó trong space Chat.

## 4. Các tab trong Sheet

| Tab | Ai ghi | Nội dung |
|---|---|---|
| `Events` | `doPost`, chỉ thêm dòng | Mọi event, kể cả điểm số. Cột `payload` giữ nguyên event, trừ token |
| `Members` | `doPost` | email, tên, track hiện tại, share, ngày tham gia, hoạt động gần nhất |
| `Progress` | `doPost` tính lại | Mỗi cặp member × track: số bài **khác nhau** đã `Complete`, tổng, %, ngày bắt đầu, ngày xong |
| `Awards` | `doPost`, `weeklySummary` | Khoá của mỗi lời khen đã gửi, để không khen trùng |
| `Templates` | Admin sửa tay | Mẫu tin nhắn |
| `Dashboard` | Công thức | Mỗi dòng là một cặp member × track, xếp theo % giảm dần, kèm biểu đồ "% route theo từng người" |
| `State` | `doPost` | Mỗi member một dòng: hash của khoá đồng bộ, revision hiện tại, kích thước, lần gửi mail mã thêm máy gần nhất |
| `StateChunks` | `doPost` | Snapshot đã mã hoá của revision hiện tại, cắt thành các ô ≤45k ký tự |

Ai có quyền xem Sheet thì xem được mọi tab, kể cả điểm trong `Events`. Nếu chia sẻ Sheet cho cả team, hãy ẩn các tab dữ liệu và tắt quyền tải, in, sao chép của người xem. Lựa chọn share của member chỉ quyết định Dashboard và Chat có hiện điểm hay không.

Nếu Dashboard bị sửa hỏng, chạy `rebuildDashboard` để dựng lại.

Ẩn hai tab `State` và `StateChunks`, đừng sửa tay. Nội dung snapshot đã được mã hoá trên máy member, nên admin và người xem Sheet không đọc được.

### Xem thử Dashboard bằng dữ liệu giả

Dùng khi sửa bố cục, màu hay biểu đồ. Làm trên **bản sao** của Sheet, không làm trên Sheet thật.

1. Trong Sheet: **File → Make a copy**, bỏ chọn **Share it with the same people**.
2. Trong bản sao, mở **Extensions → Apps Script**, chạy `seedDemo`. Hàm thêm 6 member ảo (email bắt đầu bằng `demo.`) rồi dựng lại Progress và Dashboard.
3. Xem tab Dashboard. Chạy lại `seedDemo` thì dữ liệu giả được thay mới, không bị nhân đôi.
4. Xong thì chạy `clearDemo`. Hàm chỉ xoá dòng của member `demo.`.

`seedDemo` từ chối chạy nếu project có `TEAM_TOKEN` hoặc `CHAT_WEBHOOK_URL`, để không ghi dữ liệu giả vào Sheet thật. Bản sao không có Script Properties nên chạy bình thường.

## 5. Quy tắc khen

| Tình huống | Điều kiện | Khoá trong `Awards` | Hiện tên |
|---|---|---|---|
| `day3`: ngày năng suất | Chạm 3 bài mới `Complete` trong cùng một ngày (GMT+7). Event không trễ quá 24 giờ. Mỗi ngày khen tối đa 1 lần | `day3:<email>:<yyyy-mm-dd>` | Luôn hiện |
| `mock80`: mock ≥80% | Mock (không tính diagnostic) đạt ≥80%, lần đầu của mỗi track | `mock80:<email>:<track>` | Hiện khi bật share |
| `mock80_anon` | Như trên, khi member tắt share | `mock80:<email>:<track>` | Giấu tên |
| `first_finish`: về đích đầu tiên | Người đầu tiên xong toàn bộ route của một track | `first:<track>` | Luôn hiện |
| `finish`: về đích | Những người xong track sau người đầu tiên | `finish:<email>:<track>` | Luôn hiện |
| `weekly`: tổng kết tuần | Xem mục 6 | `weekly:<yyyy-mm-dd>:<email>` | Luôn hiện, không hiện điểm |

"Bài mới" là bài member xong lần đầu, tính trên mọi track. Khi đổi track, tutor ghi lại các bài chung đã xong. Các bài đó được cộng vào `Progress` của track mới nhưng không tính là bài mới.

## 6. Tổng kết tuần

- Cửa sổ tính từ lần chạy trước đến lúc chạy. Mốc lưu trong Script Property `LAST_WEEKLY_AT`, nên không sót event.
- Mức tăng của mỗi member = tổng theo track của (số bài mới nhận được trong cửa sổ / số bài của route) × 100.
- Bỏ người có mức tăng bằng 0. Xếp hạng đồng hạng (dense rank), lấy hạng 1–3, nên có thể nhiều hơn 3 người.
- Không ai tiến bộ thì không gửi tin.
- Gửi Chat thất bại thì mốc không đổi và hàm báo lỗi. Google sẽ gửi email báo lỗi trigger cho admin. Sửa xong thì chạy lại `weeklySummary` bằng tay.
- Event đến muộn từ outbox sau khi tổng kết đã chạy vẫn vào `Progress`, và được tính vào tuần kế tiếp.

## 7. Sửa mẫu tin nhắn

Mỗi dòng trong tab `Templates` có 2 cột: `situation` (tên tình huống ở mục 5) và `text`. Web app chọn ngẫu nhiên một dòng của tình huống. Nếu tình huống không còn dòng nào, web app dùng mẫu mặc định trong `Logic.gs`. Sửa xong là có hiệu lực ngay, không cần deploy lại.

| Placeholder | Dùng trong | Giá trị |
|---|---|---|
| `{name}` | `day3`, `mock80`, `first_finish`, `finish` | Tên hiển thị |
| `{track}` | `mock80`, `mock80_anon`, `first_finish`, `finish` | Mã track viết hoa, ví dụ `CCDV-F` |
| `{count}` | `day3` | Số bài mới trong ngày |
| `{score}` | `mock80` | Điểm mock, ví dụ `82.5` |
| `{pct}` | `first_finish`, `finish` | `100` |
| `{ranking}` | `weekly` | Các dòng xếp hạng, ví dụ `🥇 Nguyễn Văn An: +20% route (CCDV-F đang ở 47%)` |

Đừng dùng `{name}` hay `{score}` trong `mock80_anon`.

## 8. Sửa dữ liệu ghi nhầm

Không có lệnh hoàn tác. Xoá dòng sai trong tab `Events`, rồi chạy `rebuildProgress` trong editor. Lời khen đã gửi không bị thu hồi.

## 9. Đổi token khi bị lộ

1. Đổi `TEAM_TOKEN` trong Script Properties. Có hiệu lực ngay, không cần deploy lại.
2. Chạy `setup.py --make-code` và ghim mã mới trong Chat.
3. Member chạy lại `setup.py --join <mã mới>`. Event đang chờ trong outbox sẽ được gửi bằng token mới.

Event lạ, nếu có, vẫn hiện trong tab `Events` để admin xoá.

## 10. Đồng bộ nhiều máy

Member học trên nhiều máy thì tutor tự đồng bộ `CLAUDE-CERTIFICATION.md` và `learning-artifacts/` qua chính web app này. Hướng dẫn cho member: [MEMBER.md](MEMBER.md#3-học-trên-nhiều-máy).

### Nâng cấp khi team đang học

1. Dán `Code.gs` và `Logic.gs` mới vào editor. Chạy `setupSheets` để tạo `State` và `StateChunks`. Chạy `testMail` để cấp quyền gửi mail. Rồi deploy **New version** trên deployment đang dùng (mục 2). Web app vẫn nhận event từ bản client cũ.
2. Ghim thông báo trong Chat:

   > 🔄 Tutor đã hỗ trợ học trên nhiều máy. Trên máy đang học, chạy `git pull` ở thư mục repo rồi mở lại session tutor. Đồng bộ sẽ tự bật và gửi vào email công ty một lệnh để thêm máy khác. Chi tiết: mục "Học trên nhiều máy" trong `team/MEMBER.md`.

Nếu member cập nhật repo trước khi admin deploy, script báo "web app chưa hỗ trợ đồng bộ" rồi bỏ qua. Việc học không bị ảnh hưởng.

### Member mất khoá hoặc bị chiếm email

Xoá dòng của email đó trong tab `State`. Lần đồng bộ sau, máy của member đăng ký lại và đẩy bản trên máy lên. Các chunk cũ tự xoá khi đăng ký lại.

Mail mã thêm máy chỉ gửi tới chính email đã đăng ký, tối đa 1 lần mỗi 10 phút. Web app không lưu và không ghi log mã này.

Ai có token chung đều có thể đăng ký trước một email chưa bật đồng bộ. Họ không đọc được dữ liệu, vì dữ liệu được mã hoá bằng khoá của chủ email. Chủ email chỉ bị chặn đồng bộ cho đến khi admin xoá dòng.
