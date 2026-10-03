# J1939 Pattern Generator – Thiết kế hệ thống & phương pháp Validate TX↔RX

Mục tiêu (theo `.Agent/.skill/skill.md`): STM32 đóng vai một chiếc xe hạng nặng, phát ra frame
J1939 có dữ liệu đã biết trước để kiểm tra bộ giải mã / OBU / FMS về **Functionality, Reliability,
Performance**. Muốn kết luận được điều đó thì trước hết phải chứng minh được rằng *chính bộ phát*
gửi đúng thứ mình cấu hình. Tài liệu này trình bày cách hệ thống làm việc đó.

---

## 1. Kiến trúc tổng thể

```
 ┌──────────── PC ─────────────────────────────────────────────┐
 │  Browser (webui/)          host/run_server.py (FastAPI :8000)│
 │  ─ chọn SPN, pattern  ──►  /api/configure → "CONFIG ..."     │──UART 115200──┐
 │  ─ START/STOP         ──►  /api/start     → "TXLOG 1","START"│               │
 │  ─ tab Validation     ◄──  /api/validation/*  ◄─ Correlator  │◄─ $TX,$RUN ───┤
 │                                              ▲               │               │
 │  TSMaster ── Mini Program bridge ── TCP :29501 (RX JSON)     │          ┌────▼─────┐
 │      ▲                                                       │          │ STM32F103│
 └──────┼───────────────────────────────────────────────────────┘          │ bxCAN    │
        └──────────────── CAN bus 250/500 kbps (J1939) ◄────────────────────┤ MCP2551  │
                                                                           └──────────┘
```

Có hai đường dữ liệu độc lập cùng mô tả *một* frame:

| Đường | Nguồn | Nội dung | Ý nghĩa |
|---|---|---|---|
| **TX (ground truth)** | Firmware in qua UART | `$TX,seq,t_ms,ID,DATA,status` | Thứ STM32 *đã đưa* cho bộ điều khiển CAN |
| **RX (observed)** | TSMaster nhận trên bus | `{id, data, timestamp_us}` | Thứ thực sự *xuất hiện* trên bus |

Validate = ghép từng cặp TX↔RX rồi so sánh.

---

## 2. Lộ trình phát triển từng bước

| Bước | Nội dung | Trạng thái | Kiểm chứng |
|---|---|---|---|
| 0 | HAL/MID/APP firmware: encode SPN, scheduler PGN, CLI `CONFIG/START/STOP/BAUD` | Có sẵn | `CANTEST`, TSMaster thấy frame |
| 1 | Web UI cấu hình 52 SPN, 8 pattern, 3 mode timing, xuất DBC | Có sẵn (đã dọn logo/credits) | `host/tests/test_end_to_end.py` |
| 2 | **Log TX theo frame**, không chặn scheduler (`$TX`, lệnh `TXLOG`) | **Mới** | `STATUS` → `Lines dropped` |
| 3 | **Marker `$RUN/$END`** để biết mốc t0 của waveform | **Mới** | Thấy `$RUN` trong log |
| 4 | **Bridge TSMaster → TCP 29501** (timestamp phần cứng) | **Mới** | Console bridge in "connected" |
| 5 | **Correlator** 3 lớp: Transport / Timing / Semantics | **Mới** | `python host/tests/test_correlator.py` |
| 6 | **Tab "TX ↔ RX Validation"** + lưu bằng chứng mỗi run | **Mới** | Thư mục `host/runs/run_*` |
| 7 | Replay log thực tế (Stage 3 của skill.md) | Kế tiếp | Nạp `.asc` → firmware phát lại |
| 8 | Stress: giá trị ngoài [Min,Max], sai chu kỳ, bus-load cao | Kế tiếp | Correlator đo được độ lệch |
| 9 | Nối bộ giải mã/OBU: so giá trị OBU báo cáo với `expected` | Kế tiếp | Lớp Semantics dùng lại |

---

## 3. Tương thích với firmware STM32

Web/Python tuân theo đúng giao thức CLI của `APP/app_cli.c`:

| Lệnh | Cú pháp | Ghi chú |
|---|---|---|
| CONFIG | `CONFIG <spn> <type 0-7> <min> <max> [param1] [timeframe_ms] [t_start] [t_dur]` | Dòng ≤ 127 ký tự (`APP_CLI_BUF_LEN`) |
| START | `START <duration_s> <mode>` | mode **0=Smooth, 1=SAE, 2=Stress** (`J1939_Sched_Mode_t`) |
| BAUD | `BAUD <250\|500>` | Web gửi lại mỗi lần START để luôn đồng bộ |
| TXLOG | `TXLOG <0\|1>` | Mới: bật/tắt dòng `$TX` |
| STOP / CLEAR / STATUS / RESET / CANTEST | | |

Những điểm đã sửa để hai bên khớp nhau:

- Web server: `STATIC_DIR`/`DB_PATH` từng trỏ sai (`python/web/`) → nay mọi đường dẫn nằm ở `host/j1939hub/paths.py`.
- Comment mode trong `StartRequest` sai (0=SAE, 1=Stress) → đúng theo firmware.
- UI mặc định 500 kbps trong khi firmware boot ở 250 kbps → UI mặc định 250, và START luôn gửi `BAUD`.
- Log `[CAN TX]` cũ (~75 ký tự/frame, gọi `hal_console_write` kiểu blocking): ở mode Smooth với 3
  PGN mặc định ≈ 217 frame/s ≈ 16 KB/s, vượt quá 11,5 KB/s của UART 115200. Khi ring buffer
  1 KB đầy thì `prv_put()` busy-wait, **làm scheduler CAN bị trễ** và chu kỳ PGN sai đúng
  lúc đang cần đo. Bản mới in dòng gọn (~45 ký tự), và **bỏ dòng thay vì chờ** khi buffer đầy
  (đếm vào `Lines dropped`).

Ngân sách UART để log được đầy đủ (115200 baud ≈ 250 dòng `$TX`/s):

| Mode | Ví dụ | Frame/s | Log đủ? |
|---|---|---|---|
| SAE | 10 PGN | ~60 | ✅ |
| Smooth | 3 PGN mặc định | ~217 | ✅ (gần ngưỡng) |
| Smooth | 23 PGN (52 SPN) | ~1000 | ❌ có gap → `UNLOGGED` |
| Stress | | > 1000 | ❌ → tăng baud console (921600: BRR=78, sai số 0,16%) |

Gap trong log không phải lỗi của bus: correlator nhận ra nhờ `seq` nhảy cóc.

Giới hạn của firmware hiện tại (UI cần biết):
- `t_start`/`t_dur` được lưu nhưng **pattern generator chưa áp dụng** cửa sổ active.
- 5 giây đầu sau START mọi SPN = 0 (`STARTUP_SEC`).
- SPN chưa cấu hình trong cùng PGN giữ `0xFF` (not available). Tab "Frame Preview" mô phỏng trong browser,
  điền 0 cho các SPN đó và dùng thuật toán random khác, nên **không dùng tab đó để đối chiếu**.

---

## 4. Validate TX↔RX: cách nhận biết tương quan giữa truyền và nhận

### 4.1 Khóa ghép cặp
Mỗi frame được nhận diện bằng **(CAN ID 29-bit, 8 byte payload, thời điểm)**. Chỉ ID+DATA thì
chưa đủ, vì giá trị Constant hoặc 5 giây đầu tạo ra nhiều frame giống hệt nhau. Thời điểm giúp
phân biệt chúng, nhưng hai đồng hồ (STM32 tính ms từ lúc boot, TSMaster tính µs) có gốc khác nhau.

### 4.2 Đồng bộ đồng hồ (tự động, không cần phần cứng thêm)
Với mỗi RX, tính `Δ = t_rx − t_tx` cho mọi TX có cùng ID+DATA. Offset thật tạo thành **một đỉnh nhọn**
trên histogram, còn các cặp trùng ngẫu nhiên thì trải đều theo bội số chu kỳ. Payload càng hiếm thì
phiếu bầu càng nặng (trọng số `1/số ứng viên`). Sau khi khóa, offset bám theo trôi tinh thể (EWMA),
và hệ thống báo `clock_drift_ppm`.

### 4.3 Luật ghép
Với RX có thời điểm dự kiến `t_rx − offset`, chỉ xét TX cùng ID trong cửa sổ **±½ chu kỳ của ID đó**
(tối đa 30 ms). Nếu không giới hạn như vậy, một cặp ghép sai sẽ làm lệch mọi cặp phía sau đúng một
chu kỳ (lỗi này đã bị `host/tests/test_correlator.py` bắt được và đã sửa).

| Verdict | Điều kiện | Ý nghĩa |
|---|---|---|
| **MATCH** | Cùng ID + 8 byte, trong cửa sổ | Truyền đúng |
| **LOST** | TX `Q` đã quá hạn, không có RX nào khớp | Mất trên bus / TSMaster |
| **MISMATCH** | Cùng ID, đúng thời điểm, khác byte (byte sai được tô đỏ) | Hỏng dữ liệu |
| **UNEXPECTED** | RX của ID do generator phát, không có TX tương ứng | Frame lạ / lặp |
| **UNLOGGED** | RX rơi vào gap `seq` của UART log | Log bị thiếu, **không phải lỗi bus** |
| foreign | ID mà generator không bao giờ phát | ECU khác, bỏ qua |
| TX busy/err | Firmware báo `B`/`E` | Chưa vào mailbox, không mong đợi xuất hiện trên bus |

`Match rate = MATCH / (MATCH + LOST + MISMATCH)`.

### 4.4 Ba lớp kết luận

1. **Transport**: các verdict ở trên. Đạt khi match rate = 100%.
2. **Timing**: jitter (σ của `t_rx − t_tx − offset`), chu kỳ RX thực tế so với chu kỳ TX trên từng
   CAN ID. Đây là chỉ số Performance trong skill.md.
3. **Semantics**: giải mã từng SPN từ payload RX bằng `host/data/j1939_spn_database.json`, rồi so với
   **công thức pattern của firmware** tại thời điểm `t_tx − t0` (`t0` lấy từ `$RUN`). Công thức này
   được chép 1:1 trong `pattern_value()`. Dung sai là ±1 raw count, có xét thêm ±1 ms ở các cạnh
   Square/Step. Random walk chỉ kiểm tra range. Lớp này chứng minh chuỗi **UI → CONFIG → encode →
   CAN** đúng end-to-end: nếu UI tưởng chu kỳ là 9 s mà firmware chạy 8 s thì ô `Fail` sẽ báo
   ngay (đã có test).

Khi có bộ giải mã/OBU (bước 9), chỉ cần so giá trị nó báo cáo với cột `Expected`: đó chính là
mục tiêu validate Functionality trong skill.md.

### 4.5 Bằng chứng tái lập
Mỗi lần START, web server lưu vào `host/runs/run_YYYYmmdd_HHMMSS/`:
`config.json`, `stm32_uart.log`, `tsmaster_rx.jsonl`, và `correlation_report.csv` (khi bấm Export).
Có thể chạy lại offline, hoặc dùng file `.asc` export từ TSMaster:

```powershell
python host\tools\correlate_logs.py --tx host\runs\run_X\stm32_uart.log --rx host\runs\run_X\tsmaster_rx.jsonl `
       --config host\runs\run_X\config.json --csv report.csv
python host\tools\correlate_logs.py --tx stm32_uart.log --rx tsmaster_export.asc --config host\runs\run_X\config.json
```

---

## 5. Quy trình chạy thực tế

1. Nạp firmware (`make flash`), nối CAN tới TSMaster (đúng bitrate, có điện trở 120 Ω).
2. `pip install -r host/requirements.txt`, rồi `python host/run_server.py` → mở `http://127.0.0.1:8000`.
3. Trong TSMaster: tạo Python Mini Program từ `host/integrations/tsmaster/mini_program_bridge.py`, gắn callback CAN RX.
4. Trên web: Connect COM → chọn SPN/pattern → tích **Record per-frame `$TX` log** → START.
5. Mở tab **TX ↔ RX Validation**: chờ "Clock locked", sau đó đọc các ô KPI và bảng anomalies.
6. Muốn có kết luận chặt về giá trị, nên dùng mode **SAE** hoặc ít PGN để log không bị gap.

## 6. Cấu trúc thư mục

```
host/                       phần mềm chạy trên PC
  run_server.py             khởi chạy web UI + serial + correlator
  j1939hub/                 package lõi
    server.py               FastAPI: API, serial STM32, bridge RX :29501, lưu runs/
    correlator.py           ghép TX↔RX 3 lớp + pattern model của firmware
    dbc_generator.py        xuất DBC từ database SPN
    live_verifier.py        dashboard tk kiểm frame theo DBC (:29500)
    paths.py                mọi đường dẫn repo
  integrations/tsmaster/    mini_program_bridge.py (chạy trong TSMaster)
  tools/                    correlate_logs, live_verifier, generate_spn_database, verify_database_dbc
  reports/                  script báo cáo Excel (cần folder log gốc "new verification system - logs comp/")
  tests/                    test_correlator, test_end_to_end
  data/                     j1939_spn_database.json (sinh từ MID/j1939_signal_definitions.c)
  runs/                     bằng chứng mỗi run (gitignore)
webui/                      index.html, css/style.css, js/app.js  (phục vụ ở /static)
```

## 7. Kiểm thử đã có

| Lệnh | Kiểm tra |
|---|---|
| `python host/tests/test_correlator.py` | Cấy offset, jitter, 1 frame mất, 1 byte sai, 5 dòng log bị drop, 1 frame ECU lạ, cấu hình sai → mọi verdict đúng |
| `python host/tests/test_end_to_end.py` | API SPN + DBC |
| `make` | Firmware build sạch, không warning |
