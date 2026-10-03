Overview: Development of an STM32-Based J1939 Signal Generation

System with User Interface" focused on creating a comprehensive toolset for

simulating automotive ECUs by generating authentic J1939 messages on a CAN bus.

The system was designed to serve as a reliable testing platform for engineers and

educators, enabling validation of communication systems and diagnostic functions

without requiring actual vehicle hardware.

Cốt lõi mục đích của J1939 Pattern Generator chính là đóng vai trò làm một nguồn phát/mô phỏng tín hiệu xe hạng nặng (Heavy-Duty Vehicle), sau đó đưa dữ liệu này qua bộ giải mã (J1939 Bridge/Decoder), thiết bị On-Board Unit (OBU) hoặc hệ thống FMS để xác thực (validate) khả năng xử lý của các thiết bị này  

1. Mô phỏng tín hiệu xe hạng nặng

Chuẩn SAE J1939 là tiêu chuẩn giao tiếp CAN Bus dành cho xe thương mại và xe hạng nặng (xe tải, xe buýt, máy công trình)

. Pattern Generator sẽ đóng vai trò thay thế chiếc xe thật để phát ra các khung tin nhắn CAN/J1939 chứa các thông số vận hành (như tốc độ xe, vòng tua máy, nhiệt độ, áp suất, lượng phát thải...)
2. Xây dựng dữ liệu & kịch bản kiểm tra
Hệ thống sử dụng các thông số kỹ thuật để tạo ra đa dạng các tập dữ liệu:
- Ngưỡng giới hạn (Max/Min & Data Range): Thiết lập phạm vi dữ liệu nhị phân (Bin_Data) và giá trị thập phân giải dịch (DEC_Data) dựa trên Operation Range và Data Range của tiêu chuẩn J1939
- 5 Mẫu biến đổi sóng (Data Patterns): Phát tín hiệu dạng cố định, tăng/giảm tuyến tính, ngẫu nhiên giá trị hoặc ngẫu nhiên chu kỳ truyền (Transmission Rate)
- Phát lại dữ liệu thực tế (Replay Data): Nạp các tập tin data log đã thu thập thực tế từ xe để tái tạo chính xác diễn biến thực địa (Stage 3)
- Thử nghiệm chịu tải/áp lực (Stress Testing): Tạo ra các tình huống dữ liệu vượt ngưỡng [Min, Max], tăng/giảm quá độ bất thường, hoặc sai lệch tốc độ truyền tín hiệu
3. Mục đích xác thực (Validate)
Toàn bộ dữ liệu mô phỏng trên được đưa vào thiết bị/bộ giải mã để validate 4 khía cạnh chính theo mục tiêu của dự án
Tính năng (Functionality): Kiểm tra bộ giải mã có đọc và giải dịch chính xác các mã PGN/SPN, mã lỗi chẩn đoán (DTC) theo đúng chuẩn SAE J1939 / OBDII hay không  
Độ tin cậy (Reliability): Thông qua các bài Stress Test để đánh giá bộ giải mã có bị treo, mất dữ liệu hay tràn bộ nhớ khi gặp tín hiệu dị thường hay không
  Hiệu năng (Performance): Đánh giá khả năng xử lý trong các kịch bản phức tạp như phân tích hành vi người lái, giám sát khí thải quy định chính phủ (GB17691-2018), tính toán ESG, và tốc độ đẩy dữ liệu lên đám mây của OBU