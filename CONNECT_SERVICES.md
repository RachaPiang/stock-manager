# เชื่อมบริการจริงทีละขั้น

ตั้งตัววิเคราะห์หลักเป็น Codex ในเครื่องแล้ว ไม่ต้องใช้ OpenAI API key
ยังต้องเชื่อมแหล่งราคาหุ้นกับ LINE แยกกัน การทดสอบแบบจำลองไม่ยืนยันสิทธิ์ข้อมูล โควตา หรือการส่ง LINE จริง
คุณเป็นผู้สร้างบัญชีและยอมรับเงื่อนไขบริการด้วยตัวเอง ไม่ต้องส่ง key มาในแชต

## 1. เริ่มจากราคาหุ้น

1. เปิด [Twelve Data](https://twelvedata.com/) แล้วสมัครหรือเข้าบัญชีของคุณ
2. ตรวจว่าแผนที่เลือกเข้าถึง daily/5min time series ของหุ้นสหรัฐทั้ง 9 ตัวได้ ความหน่วงข้อมูลขึ้นอยู่กับสิทธิ์ของแผน ส่วน doctor จะตรวจ quote เพิ่มเพื่อวินิจฉัย
3. คัดลอก API key จากบัญชี แล้วดับเบิลคลิก `Setup Connections.cmd`
4. เลือก `1` วาง key ลงในช่องที่ซ่อนข้อความ แล้วตอบ `y` เมื่อต้องการให้การตรวจครั้งถัดไปใช้ข้อมูลจริง
5. เปิด PowerShell ในโฟลเดอร์ stock-manager แล้วตรวจการเชื่อมต่อหุ้นตัวแรก:

```powershell
.\.venv\Scripts\python.exe -m app.main doctor --online --service stock
```

คำสั่งนี้เรียก API จริงเพื่อดึง quote/history ของ META และใช้โควตาบัญชี แต่ยังไม่เรียก AI หรือส่ง LINE
หากผ่าน ให้ดับเบิลคลิก `Open Portfolio.cmd` เพื่อดูทั้ง 9 หุ้น ระบบจะสร้าง `data/live-portfolio.html`
ตรวจราคาเทียบกับบัญชีลงทุนของคุณ โดยดูเวลาของข้อมูลและสกุลเงินประกอบ

เริ่มแบบตรวจด้วยตัวเองก่อนเปิดงานทุก 5 นาทีเฉพาะตลาดเปิด: รอบแรก 18 credits รอบถัดไป 9
ประวัติรายวันถูกเก็บใช้ซ้ำทั้งวัน วันเต็มประมาณ 711 credits (78 รอบ) จากเพดานบัญชี 800
การตรวจปกติใช้ราคาปิดแท่ง 5 นาทีแทน quote เพื่อไม่เพิ่มอีก 9 requests ต่อรอบ
โหลดข้อมูล 5 นาทีจริงแล้ว แต่การตรวจต่อเนื่องระหว่างตลาดเปิดยังต้องทดสอบก่อนเปิดตารางจริง
โปรแกรมจำกัด 760 credits/วัน UTC พร้อมเว้นขั้นต่ำ 8 วินาทีข้ามรอบและข้ามการปิดโปรแกรม
ตัวนับไม่รวมการใช้ก่อนอัปเดตนี้และจากแอปอื่น จึงไม่รับรองโควตาบัญชีหากใช้ key ร่วมที่อื่น
check/view จะไม่ดึงข้อมูลจริงเมื่อตลาดปิด; doctor --online ยังตรวจได้ตามคำสั่งและนับเครดิต
ดู [วิธีคิดเครดิตของ Twelve Data](https://support.twelvedata.com/en/articles/5615854-credits)
ก่อนเลือกแผนหรือกำหนดตารางจริง ระบบยังไม่สมัครหรืออัปเกรดแผนให้

## 2. ใช้ Codex ที่ล็อกอินในเครื่อง

ส่วนนี้เลือก `ANALYST_MODE=codex` ไว้แล้ว ไม่ต้องสมัคร OpenAI API เพิ่ม
Codex CLI ใช้การล็อกอิน ChatGPT เดิมของบัญชี Windows นี้ การเรียกแต่ละครั้งต้องต่ออินเทอร์เน็ตและใช้โควตา Codex

ตรวจการล็อกอิน:

```powershell
.\.venv\Scripts\python.exe -m app.main doctor --service codex
```

ทดลองวิเคราะห์หนึ่งครั้งด้วยข้อมูลสมมุติ (แม้ยังไม่มี key ราคาหุ้น):

```powershell
.\.venv\Scripts\python.exe -m app.main test-codex
```

หรือดับเบิลคลิก `Test Codex.cmd` จะได้คำตอบในหน้าต่างและใน `data/codex-example.txt`
ขั้นทดสอบนี้ไม่ส่ง LINE หรือเปลี่ยน cooldown ของเหตุการณ์จริง
เมื่อใช้ราคาจริงแล้ว การตรวจหุ้นจะเรียก Codex เฉพาะเมื่อมีเหตุการณ์ใหม่ที่ไม่ติด cooldown
หากยังเป็น MOCK_MODE จะใช้แม่แบบตามเดิม ยกเว้นการสั่ง test-codex โดยตรง

ถ้าจำเป็นต้องล็อกอินใหม่ ให้รัน `codex login` ด้วยตัวเอง ไม่ต้องคัดลอก auth.json หรือส่ง token เข้าแชต
ตัวช่วย `Setup Connections.cmd` ตัวเลือก 2 ใช้ตรวจ login และเลือก Codex ได้ด้วย
อ้างอิง [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
และ [การล็อกอิน Codex](https://learn.chatgpt.com/docs/auth)

### ทางเลือกเพิ่มเติม: OpenAI API แยกบัญชี (ไม่จำเป็นสำหรับวิธี Codex)

1. เปิด [OpenAI API platform](https://platform.openai.com/) และตรวจ project/billing ที่คุณจะใช้
2. สร้างกุญแจผ่าน [API keys](https://platform.openai.com/api-keys) ด้วยตัวคุณเอง และเก็บชื่อ model ที่บัญชีคุณใช้ได้
3. ดับเบิลคลิก `Setup Connections.cmd` เลือก `4` ใส่ key และชื่อ model
4. ตอบ `y` เพื่อเปิดบทวิเคราะห์ AI ในการตรวจครั้งถัดไป การสร้างคำตอบอาจมีค่าใช้จ่ายตามบัญชี
5. ตรวจ key และการมองเห็น model ก่อน:

```powershell
.\.venv\Scripts\python.exe -m app.main doctor --online --service openai
```

คำสั่งนี้ใช้ retrieve model ไม่สร้างคำตอบ จึงยังไม่ยืนยันว่า billing และ Responses API ทำงานครบ
การทดสอบสร้างคำตอบจริงเกิดเมื่อรันตรวจหุ้นแล้วมีเหตุการณ์ใหม่ที่ไม่ติด cooldown
ถ้าไม่มีเหตุการณ์ใหม่ ระบบจะไม่เรียก AI ซึ่งเป็นพฤติกรรมที่ตั้งใจเพื่อลดค่าใช้จ่ายและข้อความซ้ำ
หาก API ขัดข้อง ข้อความจะระบุว่าใช้แม่แบบสำรอง ต้องแยกออกจากการวิเคราะห์ AI สำเร็จ
ดู [OpenAI quickstart](https://developers.openai.com/api/docs/quickstart) และ [retrieve model](https://developers.openai.com/api/reference/python/resources/models/methods/retrieve)

## 3. เพิ่ม LINE

1. สร้างหรือใช้ LINE Official Account ของคุณผ่าน [LINE Official Account Manager](https://manager.line.biz/)
2. เปิด Messaging API ในการตั้งค่าของ Official Account เพื่อสร้าง channel แล้วไปจัดการผ่าน [LINE Developers Console](https://developers.line.biz/console/)
3. ออก Channel access token สำหรับ channel นี้ แล้วเพิ่ม Official Account เป็นเพื่อนใน LINE ส่วนตัว
4. ใน channel ให้ดูแท็บ Basic settings ช่อง Your user ID เพื่อใช้เป็นผู้รับของตัวเอง โดย ID จะขึ้นต้นด้วย `U`
5. ดับเบิลคลิก `Setup Connections.cmd` เลือก `3` ใส่ token และ user ID แล้วตอบ `y` หากต้องการเปิดส่งแจ้งเตือน
6. ตรวจ token และการเข้าถึงโปรไฟล์ผู้รับก่อน:

```powershell
.\.venv\Scripts\python.exe -m app.main doctor --online --service line
```

คำสั่งนี้อ่านข้อมูล bot/ผู้รับ ไม่ส่งข้อความ การส่งจริงจะเกิดเมื่อ `Open Portfolio.cmd` หรือ `check`
พบเหตุการณ์ใหม่ที่ไม่ติด cooldown ตรวจข้อจำกัดข้อความของ OA ในบัญชีจริงด้วย
การอ่าน profile สำเร็จไม่ได้รับรองว่าผู้รับจะได้รับข้อความเสมอ
ขั้นตอนสำหรับผู้รับที่เป็นเจ้าของ channel นี้ไม่ต้องเปิด webhook หรือ public port บนเครื่อง
ดู [เริ่มต้น Messaging API](https://developers.line.biz/en/docs/messaging-api/getting-started/)
และ [การหา user ID ของตัวเอง](https://developers.line.biz/en/docs/messaging-api/getting-user-ids/)

## การตรวจสถานะและกลับไปโหมดจำลอง

เช็กว่าตั้งค่าไว้ครบหรือยังโดยไม่ต่ออินเทอร์เน็ต:

```powershell
.\.venv\Scripts\python.exe -m app.main doctor
```

`doctor` จะแสดงเพียงสถานะ ไม่มี key หรือ user ID หลุดออกมาในข้อความ
ค่าจาก environment ของระบบมีความสำคัญเหนือ `.env`; ตัวช่วยจะแจ้งหากพบค่าของระบบอยู่แล้ว

กลับไปทดสอบได้ด้วยการตั้ง `MOCK_MODE=true` ใน `.env` แล้วเปิดโปรแกรมใหม่
โหมดนี้บังคับใช้ข้อมูลสมมุติ/แม่แบบ/console แม้จะมี key จริงอยู่แล้ว
ข้อมูลจำลองและข้อมูลจริงอยู่คนละฐานข้อมูลและคนละไฟล์รายงาน

หลังเชื่อมครบ ค่อยยืนยันการแจ้งเตือนจริงหนึ่งเหตุการณ์ แล้วจึงตั้งตารางรันที่เหมาะกับโควตาและเวลาตลาด
ยังไม่มีการเปิดใช้งาน Task Scheduler หรือ VPS อัตโนมัติ
