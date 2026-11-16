import os, sqlite3, logging, zipfile, csv, io, json, time
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters, ConversationHandler

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9190435829:AAE55SZiVFGULWNx8fqzOHoVIYRIEYkmPjs")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0")) # حط الايدي مالك هنا - دز /myid للبوت القديم
DB = "institute.db"
FILES_DIR = "uploads"
os.makedirs(FILES_DIR, exist_ok=True)
logging.basicConfig(filename="bot.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SUBJECTS = ["علوم القرآن","سيرة النبي الأكرم (ص)","الأطوار الحسينية","نهج البلاغة – حكم أمير المؤمنين (ع)","العقيدة الإسلامية","الأدب العربي وفنونه","سيرة الإمام الحسين (ع)"]

# Conversation states
ADD_TITLE, ADD_DESC, ADD_FILE, ADD_SCHEDULE_IMG, EXAM_NAME, EXAM_DESC, EXAM_FILE, EXAM_DEADLINE, MCQ_Q, MCQ_OPTS, MCQ_ANS, MCQ_SCORE, BROADCAST_MSG, GRADE_SCORE, GRADE_NOTE = range(14)

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def init_db():
    con = db(); cur = con.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS students(id INTEGER PRIMARY KEY, name TEXT, username TEXT, joined TEXT, active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS teachers(id INTEGER PRIMARY KEY, name TEXT, subjects TEXT, can_admin INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS materials(id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, title TEXT, description TEXT, file_id TEXT, file_type TEXT, file_name TEXT, created TEXT);
    CREATE TABLE IF NOT EXISTS schedule(id INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT, created TEXT, active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS exams(id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, title TEXT, description TEXT, type TEXT, file_id TEXT, deadline TEXT, allow_resubmit INTEGER DEFAULT 0, time_limit INTEGER DEFAULT 0, max_attempts INTEGER DEFAULT 1, show_result INTEGER DEFAULT 1, creator INTEGER);
    CREATE TABLE IF NOT EXISTS questions(id INTEGER PRIMARY KEY AUTOINCREMENT, exam_id INTEGER, question TEXT, options TEXT, correct INTEGER, score REAL);
    CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY AUTOINCREMENT, exam_id INTEGER, student_id INTEGER, student_name TEXT, file_id TEXT, score REAL, notes TEXT, status TEXT DEFAULT 'pending', submitted_at TEXT, answers TEXT);
    CREATE TABLE IF NOT EXISTS announcements(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, created TEXT, sent_ok INTEGER, sent_fail INTEGER);
    """)
    con.commit(); con.close()
init_db()

def is_admin(uid): return uid == ADMIN_ID
def is_teacher(uid):
    con=db(); r=con.execute("SELECT * FROM teachers WHERE id=?", (uid,)).fetchone(); con.close(); return r is not None
def can_manage(uid): return is_admin(uid) or is_teacher(uid)

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 المكتبة التعليمية", callback_data="m:library")],
        [InlineKeyboardButton("📅 جدول المحاضرات", callback_data="m:schedule")],
        [InlineKeyboardButton("📝 الاختبارات", callback_data="m:exams")],
        [InlineKeyboardButton("🔎 البحث عن مادة", callback_data="m:search")],
        [InlineKeyboardButton("📢 الإعلانات", callback_data="m:ann")],
        [InlineKeyboardButton("👤 معلومات الطالب", callback_data="m:me")],
        [InlineKeyboardButton("📩 التواصل مع الإدارة", callback_data="m:contact")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    con=db(); con.execute("INSERT OR REPLACE INTO students VALUES(?,?,?,?,1)", (u.id, u.full_name, u.username, datetime.now().strftime("%Y-%m-%d %H:%M"))); con.commit(); con.close()
    await update.message.reply_text(f"أهلاً بيك {u.first_name} في بوت المعهد التعليمي 📚\nاختر من القائمة:", reply_markup=main_kb())

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"الايدي مالك:\n`{update.effective_user.id}`", parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear(); await update.message.reply_text("تم إلغاء العملية ✅", reply_markup=main_kb()); return ConversationHandler.END
async def skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # used in add material
    context.user_data['desc'] = ""
    await update.message.reply_text("دز الملف هسه (PDF / صوتي / فيديو / صورة / مستند)")
    return ADD_FILE

# --- Library ---
async def show_library(query_or_msg, is_query=False):
    kb = [[InlineKeyboardButton(s, callback_data=f"sub:{s}")] for s in SUBJECTS]
    kb.append([InlineKeyboardButton("⬅️ رجوع", callback_data="m:home")])
    txt="📚 المكتبة التعليمية - اختر المادة:"
    if is_query: await query_or_msg.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))
    else: await query_or_msg.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); d=q.data; uid=q.from_user.id
    if d=="m:home": await q.edit_message_text("القائمة الرئيسية 📚", reply_markup=main_kb())
    elif d=="m:library": await show_library(q, True)
    elif d=="m:schedule":
        con=db(); r=con.execute("SELECT * FROM schedule WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone(); con.close()
        if r:
            await q.message.reply_photo(r["file_id"], caption="📅 جدول المحاضرات المعتمد")
        else: await q.edit_message_text("لا يوجد جدول حالياً", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="m:home")]]))
    elif d=="m:exams":
        con=db(); rows=con.execute("SELECT * FROM exams ORDER BY id DESC").fetchall(); con.close()
        if not rows: await q.edit_message_text("لا توجد اختبارات حالياً", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="m:home")]])); return
        kb=[]
        for r in rows: kb.append([InlineKeyboardButton(f"{r['title']} - {r['subject']} ({r['type']})", callback_data=f"exam:{r['id']}")])
        kb.append([InlineKeyboardButton("⬅️ رجوع", callback_data="m:home")])
        await q.edit_message_text("📝 الاختبارات المتاحة:", reply_markup=InlineKeyboardMarkup(kb))
    elif d=="m:search": await q.edit_message_text("🔎 دز اسم المادة أو عنوان المحاضرة أو اسم الاختبار هنا")
    elif d=="m:ann":
        con=db(); rows=con.execute("SELECT * FROM announcements ORDER BY id DESC LIMIT 5").fetchall(); con.close()
        txt="📢 الإعلانات:\n\n" + ("\n\n".join([r["text"] for r in rows]) if rows else "لا توجد إعلانات")
        await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="m:home")]]))
    elif d=="m:me":
        con=db(); r=con.execute("SELECT * FROM students WHERE id=?", (uid,)).fetchone(); con.close()
        await q.edit_message_text(f"👤 معلومات الطالب:\nالاسم: {r['name'] if r else q.from_user.full_name}\nالايدي: {uid}\nتاريخ التسجيل: {r['joined'] if r else '-'}\nالحالة: {'مفعل ✅' if r and r['active'] else 'معطل'}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="m:home")]]))
    elif d=="m:contact": await q.edit_message_text("📩 للتواصل مع الإدارة دز رسالتك هنا وراح توصل للمدير.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="m:home")]]))
    elif d.startswith("sub:"):
        subj=d.split(":",1)[1]; con=db(); rows=con.execute("SELECT * FROM materials WHERE subject=? ORDER BY id DESC", (subj,)).fetchall(); con.close()
        kb=[]
        for r in rows: kb.append([InlineKeyboardButton(f"{r['title']} - {r['file_type']}", callback_data=f"mat:{r['id']}")])
        kb.append([InlineKeyboardButton("⬅️ رجوع للمكتبة", callback_data="m:library")])
        await q.edit_message_text(f"مادة: {subj}\nاختر المحاضرة:", reply_markup=InlineKeyboardMarkup(kb))
    elif d.startswith("mat:"):
        mid=int(d.split(":")[1]); con=db(); r=con.execute("SELECT * FROM materials WHERE id=?", (mid,)).fetchone(); con.close()
        if not r: return
        cap=f"📄 {r['title']}\nالمادة: {r['subject']}\n{r['description'] or ''}"
        try:
            if r["file_type"]=="photo": await q.message.reply_photo(r["file_id"], caption=cap)
            elif r["file_type"]=="audio": await q.message.reply_audio(r["file_id"], caption=cap)
            elif r["file_type"]=="video": await q.message.reply_video(r["file_id"], caption=cap)
            else: await q.message.reply_document(r["file_id"], caption=cap)
        except Exception as e: await q.message.reply_text(f"الملف: {cap}\nخطأ: {e}")
    elif d.startswith("exam:"):
        eid=int(d.split(":")[1]); con=db(); ex=con.execute("SELECT * FROM exams WHERE id=?", (eid,)).fetchone(); con.close()
        kb=[[InlineKeyboardButton("📥 تحميل ملف الاختبار", callback_data=f"examfile:{eid}")],[InlineKeyboardButton("📤 رفع الإجابة PDF", callback_data=f"submit:{eid}")]]
        if ex["type"]=="mcq": kb.append([InlineKeyboardButton("▶️ بدء اختبار الاختيارات", callback_data=f"startmcq:{eid}")])
        kb.append([InlineKeyboardButton("⬅️ رجوع", callback_data="m:exams")])
        await q.edit_message_text(f"📝 {ex['title']}\nالمادة: {ex['subject']}\n{ex['description'] or ''}\nالموعد النهائي: {ex['deadline'] or 'مفتوح'}", reply_markup=InlineKeyboardMarkup(kb))
    elif d.startswith("examfile:"):
        eid=int(d.split(":")[1]); con=db(); ex=con.execute("SELECT * FROM exams WHERE id=?", (eid,)).fetchone(); con.close()
        if ex and ex["file_id"]: await q.message.reply_document(ex["file_id"], caption=f"اختبار {ex['title']}")
        else: await q.message.reply_text("هذا الاختبار اختيارات، اضغط بدء الاختبار")
    elif d.startswith("submit:"):
        context.user_data["submit_exam"]=int(d.split(":")[1]); await q.message.reply_text("📤 دز ملف إجابتك PDF هسه")
    elif d.startswith("startmcq:"):
        eid=int(d.split(":")[1]); con=db(); qs=con.execute("SELECT * FROM questions WHERE exam_id=?", (eid,)).fetchall(); con.close()
        if not qs: await q.message.reply_text("لا توجد أسئلة بعد"); return
        context.user_data["mcq_exam"]=eid; context.user_data["mcq_idx"]=0; context.user_data["mcq_answers"]={}
        await send_mcq(q.message, context, qs[0], 0, len(qs))
    elif d.startswith("mcq:"):
        _, idx, opt = d.split(":"); idx=int(idx); opt=int(opt)
        eid=context.user_data.get("mcq_exam"); con=db(); qs=con.execute("SELECT * FROM questions WHERE exam_id=? ORDER BY id", (eid,)).fetchall(); con.close()
        context.user_data["mcq_answers"][qs[idx]["id"]]=opt
        if idx+1 < len(qs): await send_mcq(q.message, context, qs[idx+1], idx+1, len(qs))
        else:
            # calculate
            total=0; got=0
            for row in qs:
                total+=row["score"]
                if context.user_data["mcq_answers"].get(row["id"])==row["correct"]: got+=row["score"]
            con=db(); con.execute("INSERT INTO submissions(exam_id, student_id, student_name, score, status, submitted_at, answers) VALUES(?,?,?,?, 'graded', ?, ?)", (eid, uid, q.from_user.full_name, got, datetime.now().strftime("%Y-%m-%d %H:%M"), json.dumps(context.user_data["mcq_answers"]))); con.commit(); con.close()
            await q.edit_message_text(f"✅ تم إرسال إجاباتك\nنتيجتك: {got} / {total}")
    elif d.startswith("grade:"):
        sid=int(d.split(":")[1]); context.user_data["grading"]=sid; await q.message.reply_text("دز الدرجة (رقم) مثل: 85")
        return
    elif d.startswith("sub:") or d.startswith("m:"): pass

async def send_mcq(msg, context, row, idx, total):
    opts=json.loads(row["options"])
    kb=[[InlineKeyboardButton(o, callback_data=f"mcq:{idx}:{i}")] for i,o in enumerate(opts)]
    await msg.reply_text(f"سؤال {idx+1}/{total} - ({row['score']} درجة)\n\n{row['question']}", reply_markup=InlineKeyboardMarkup(kb))

# --- Admin ---
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) and not is_teacher(update.effective_user.id):
        await update.message.reply_text("❌ للأدمن والمدرسين فقط - دز /myid وارسله للمدير"); return
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة محتوى للمكتبة", callback_data="a:addmat")],
        [InlineKeyboardButton("🗑️ حذف محتوى", callback_data="a:delmat")],
        [InlineKeyboardButton("📅 رفع جدول المحاضرات", callback_data="a:schedule")],
        [InlineKeyboardButton("📝 إنشاء اختبار PDF", callback_data="a:exampdf")],
        [InlineKeyboardButton("✏️ إنشاء اختبار اختيارات", callback_data="a:exammcq")],
        [InlineKeyboardButton("📥 إجابات الطلاب + ZIP + CSV", callback_data="a:subs")],
        [InlineKeyboardButton("👥 إدارة الطلاب / المدرسين", callback_data="a:users")],
        [InlineKeyboardButton("📢 إرسال إعلان", callback_data="a:broadcast")],
        [InlineKeyboardButton("📊 إحصائيات", callback_data="a:stats")],
        [InlineKeyboardButton("💾 نسخ احتياطي", callback_data="a:backup")],
    ])
    await update.message.reply_text("⚙️ لوحة الإدارة - المعهد التعليمي", reply_markup=kb)

async def admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); d=q.data; uid=q.from_user.id
    if not can_manage(uid): await q.message.reply_text("❌ ليس لديك صلاحية"); return
    if d=="a:stats":
        con=db()
        s=con.execute("SELECT COUNT(*) c FROM students").fetchone()["c"]
        t=con.execute("SELECT COUNT(*) c FROM teachers").fetchone()["c"]
        m=con.execute("SELECT COUNT(*) c FROM materials").fetchone()["c"]
        e=con.execute("SELECT COUNT(*) c FROM exams").fetchone()["c"]
        sub=con.execute("SELECT COUNT(*) c FROM submissions").fetchone()["c"]
        pend=con.execute("SELECT COUNT(*) c FROM submissions WHERE status='pending'").fetchone()["c"]
        avg=con.execute("SELECT AVG(score) a FROM submissions WHERE score IS NOT NULL").fetchone()["a"] or 0
        con.close()
        await q.edit_message_text(f"📊 الإحصائيات:\nطلاب: {s}\nمدرسين: {t}\nملفات تعليمية: {m}\nاختبارات: {e}\nإجابات مستلمة: {sub}\nغير مصححة: {pend}\nمتوسط الدرجات: {avg:.1f}")
    elif d=="a:backup":
        # backup DB
        await q.message.reply_document(open(DB,"rb"), caption="💾 نسخة احتياطية من قاعدة البيانات")
    elif d=="a:subs":
        con=db(); rows=con.execute("SELECT s.*, e.title FROM submissions s JOIN exams e ON s.exam_id=e.id ORDER BY s.id DESC LIMIT 20").fetchall(); con.close()
        if not rows: await q.message.reply_text("لا توجد إجابات"); return
        txt="📥 إجابات الطلاب:\n\n"
        kb=[]
        for r in rows:
            txt+=f"{r['title']} | {r['student_name']} | {r['submitted_at']} | {r['status']} | درجة: {r['score']}\n"
            kb.append([InlineKeyboardButton(f"تصحيح {r['student_name']} - {r['title']}", callback_data=f"grade:{r['id']}")])
        kb.append([InlineKeyboardButton("⬇️ تنزيل الكل ZIP", callback_data="a:zip")])
        kb.append([InlineKeyboardButton("📄 تصدير CSV", callback_data="a:csv")])
        await q.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
    elif d=="a:zip":
        con=db(); rows=con.execute("SELECT * FROM submissions WHERE file_id IS NOT NULL").fetchall(); con.close()
        await q.message.reply_text("لتنزيل ZIP: الملفات محفوظة في Telegram file_id، استخدم /export_zip في السيرفر - أو حمل كل ملف من القائمة")
    elif d=="a:csv":
        con=db(); rows=con.execute("SELECT * FROM submissions").fetchall(); con.close()
        out=io.StringIO(); w=csv.writer(out); w.writerow(["exam_id","student","score","status","submitted"])
        for r in rows: w.writerow([r["exam_id"], r["student_name"], r["score"], r["status"], r["submitted_at"]])
        out.seek(0); await q.message.reply_document(io.BytesIO(out.getvalue().encode()), filename="results.csv", caption="📄 نتائج الاختبارات CSV")
    elif d=="a:users":
        con=db(); rows=con.execute("SELECT * FROM students LIMIT 20").fetchall(); con.close()
        txt="👥 الطلاب:\n" + "\n".join([f"{r['name']} - {r['id']} - {'مفعل' if r['active'] else 'معطل'}" for r in rows])
        await q.message.reply_text(txt + "\n\nلإضافة مدرس: /addteacher ID اسم المادة\nلتعطيل طالب: /toggle ID")
    elif d=="a:addmat":
        kb=[[InlineKeyboardButton(s, callback_data=f"addmat_sub:{s}")] for s in SUBJECTS]
        await q.edit_message_text("اختر المادة لإضافة محتوى:", reply_markup=InlineKeyboardMarkup(kb))
    elif d.startswith("addmat_sub:"):
        context.user_data["add_subject"]=d.split(":",1)[1]; await q.message.reply_text("اكتب عنوان المحتوى:")
        return ADD_TITLE
    elif d=="a:schedule": await q.message.reply_text("دز صورة الجدول هسه"); context.user_data["await_schedule"]=True
    elif d=="a:broadcast": await q.message.reply_text("اكتب نص الإعلان:"); context.user_data["await_broadcast"]=True
    elif d=="a:exampdf": await q.message.reply_text("اكتب اسم اختبار PDF:"); context.user_data["exam_type"]="pdf"; return EXAM_NAME
    elif d=="a:exammcq": await q.message.reply_text("اكتب اسم اختبار الاختيارات:"); context.user_data["exam_type"]="mcq"; return EXAM_NAME
    elif d=="a:delmat":
        con=db(); rows=con.execute("SELECT * FROM materials ORDER BY id DESC LIMIT 20").fetchall(); con.close()
        kb=[[InlineKeyboardButton(f"🗑️ {r['title']}", callback_data=f"delmat:{r['id']}")] for r in rows]
        await q.edit_message_text("اختر المحتوى للحذف:", reply_markup=InlineKeyboardMarkup(kb))
    elif d.startswith("delmat:"):
        con=db(); con.execute("DELETE FROM materials WHERE id=?", (int(d.split(":")[1]),)); con.commit(); con.close(); await q.message.reply_text("تم الحذف ✅")

# --- Add Material Conversation ---
async def add_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"]=update.message.text; await update.message.reply_text("اكتب وصف أو دز /skip للتخطي"); return ADD_DESC
async def add_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["desc"]=update.message.text; await update.message.reply_text("دز الملف هسه (PDF / صوتي / فيديو / صورة / مستند)"); return ADD_FILE
async def add_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg=update.message; fid=None; ftype="doc"
    if msg.document: fid=msg.document.file_id; ftype="document"
    elif msg.photo: fid=msg.photo[-1].file_id; ftype="photo"
    elif msg.audio: fid=msg.audio.file_id; ftype="audio"
    elif msg.voice: fid=msg.voice.file_id; ftype="audio"
    elif msg.video: fid=msg.video.file_id; ftype="video"
    if not fid: await update.message.reply_text("دز ملف صحيح"); return ADD_FILE
    con=db(); con.execute("INSERT INTO materials(subject,title,description,file_id,file_type,file_name,created) VALUES(?,?,?,?,?,?,?)", (context.user_data.get("add_subject"), context.user_data.get("title"), context.user_data.get("desc",""), fid, ftype, "", datetime.now().strftime("%Y-%m-%d %H:%M"))); con.commit(); con.close()
    await update.message.reply_text("✅ تمت إضافة المحتوى وربطه بالمادة بنجاح"); context.user_data.clear(); return ConversationHandler.END

# --- Exam Creation ---
async def exam_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["exam_title"]=update.message.text; await update.message.reply_text("اكتب وصف الاختبار أو /skip"); return EXAM_DESC
async def exam_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text != "/skip": context.user_data["exam_desc"]=update.message.text
    else: context.user_data["exam_desc"]=""
    kb=[[InlineKeyboardButton(s, callback_data=f"exam_sub:{s}")] for s in SUBJECTS]
    await update.message.reply_text("اختر المادة:", reply_markup=InlineKeyboardMarkup(kb)); return EXAM_FILE

async def general_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt=update.message.text
    # schedule broadcast etc handled via flags
    if context.user_data.get("await_broadcast"):
        context.user_data.pop("await_broadcast")
        con=db(); students=con.execute("SELECT id FROM students WHERE active=1").fetchall(); con.close()
        ok=fail=0
        for s in students:
            try: await context.bot.send_message(s["id"], f"📢 إعلان جديد:\n\n{txt}"); ok+=1
            except: fail+=1
        con=db(); con.execute("INSERT INTO announcements(text,created,sent_ok,sent_fail) VALUES(?,?,?,?)", (txt, datetime.now().strftime("%Y-%m-%d %H:%M"), ok, fail)); con.commit(); con.close()
        await update.message.reply_text(f"✅ تم إرسال الإعلان\nناجح: {ok} | فاشل: {fail}"); return
    if context.user_data.get("grading"):
        # grading score
        try: score=float(txt); context.user_data["grade_score"]=score; await update.message.reply_text("اكتب ملاحظات أو /skip"); context.user_data["await_note"]=True; return
        except: await update.message.reply_text("دز رقم صحيح"); return
    if context.user_data.get("await_note"):
        sid=context.user_data.pop("grading"); context.user_data.pop("await_note")
        note="" if txt=="/skip" else txt; score=context.user_data.pop("grade_score")
        con=db(); con.execute("UPDATE submissions SET score=?, notes=?, status='graded' WHERE id=?", (score, note, sid)); con.commit(); con.close()
        await update.message.reply_text("✅ تم التصحيح وحفظ الدرجة"); return
    if context.user_data.get("submit_exam"):
        pass
    # search
    if txt.startswith("/"): return
    con=db(); mats=con.execute("SELECT * FROM materials WHERE title LIKE ? OR subject LIKE ?", (f"%{txt}%", f"%{txt}%")).fetchall(); exs=con.execute("SELECT * FROM exams WHERE title LIKE ? OR subject LIKE ?", (f"%{txt}%", f"%{txt}%")).fetchall(); con.close()
    if not mats and not exs: await update.message.reply_text("ما لكيت نتيجة 🔎"); return
    kb=[]
    for r in mats: kb.append([InlineKeyboardButton(f"📄 {r['title']}", callback_data=f"mat:{r['id']}")])
    for r in exs: kb.append([InlineKeyboardButton(f"📝 {r['title']}", callback_data=f"exam:{r['id']}")])
    await update.message.reply_text("🔎 نتائج البحث:", reply_markup=InlineKeyboardMarkup(kb))

async def general_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg=update.message
    # schedule image
    if context.user_data.get("await_schedule") and msg.photo:
        fid=msg.photo[-1].file_id; con=db(); con.execute("UPDATE schedule SET active=0"); con.execute("INSERT INTO schedule(file_id,created) VALUES(?,?)", (fid, datetime.now().strftime("%Y-%m-%d %H:%M"))); con.commit(); con.close(); context.user_data.pop("await_schedule"); await update.message.reply_text("✅ تم رفع الجدول الجديد وأصبح المعتمد"); return
    # submit answer PDF
    if context.user_data.get("submit_exam"):
        eid=context.user_data.pop("submit_exam")
        fid=None
        if msg.document: fid=msg.document.file_id
        if not fid: await update.message.reply_text("دز ملف PDF"); context.user_data["submit_exam"]=eid; return
        con=db(); ex=con.execute("SELECT * FROM exams WHERE id=?", (eid,)).fetchone()
        exists=con.execute("SELECT * FROM submissions WHERE exam_id=? AND student_id=?", (eid, msg.from_user.id)).fetchone()
        if exists and not ex["allow_resubmit"]: await update.message.reply_text("❌ لقد سلمت من قبل ولا يسمح بإعادة التسليم"); con.close(); return
        con.execute("INSERT INTO submissions(exam_id,student_id,student_name,file_id,status,submitted_at) VALUES(?,?,?,?, 'pending', ?)", (eid, msg.from_user.id, msg.from_user.full_name, fid, datetime.now().strftime("%Y-%m-%d %H:%M"))); con.commit(); con.close()
        await update.message.reply_text("✅ تم استلام إجابتك بنجاح\nسيتم تصحيحها قريباً"); return
    # exam PDF file creation
    if context.user_data.get("exam_type")=="pdf" and msg.document:
        fid=msg.document.file_id; con=db(); con.execute("INSERT INTO exams(subject,title,description,type,file_id,creator) VALUES(?,?,?,?, 'pdf', ?, ?)", (context.user_data.get("exam_subject","عام"), context.user_data.get("exam_title"), context.user_data.get("exam_desc",""), fid, msg.from_user.id)); con.commit(); con.close(); context.user_data.clear(); await update.message.reply_text("✅ تم إنشاء اختبار PDF"); return

async def addteacher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try: tid=int(context.args[0]); subj=" ".join(context.args[1:]) or "عام"
    except: await update.message.reply_text("الاستخدام: /addteacher ID المادة"); return
    con=db(); con.execute("INSERT OR REPLACE INTO teachers VALUES(?,?,?,0)", (tid, "مدرس", subj)); con.commit(); con.close(); await update.message.reply_text(f"✅ تمت إضافة المدرس {tid} - {subj}")
async def toggle_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try: sid=int(context.args[0])
    except: return
    con=db(); con.execute("UPDATE students SET active = 1-active WHERE id=?", (sid,)); con.commit(); con.close(); await update.message.reply_text("تم تغيير حالة الطالب ✅")

def main():
    app=Application.builder().token(TOKEN).build()
    conv_add=ConversationHandler(entry_points=[CallbackQueryHandler(admin_cb, pattern="^a:addmat$")], states={ADD_TITLE:[MessageHandler(filters.TEXT & ~filters.COMMAND, add_title)], ADD_DESC:[MessageHandler(filters.TEXT & ~filters.COMMAND, add_desc), CommandHandler("skip", skip)], ADD_FILE:[MessageHandler(filters.ATTACHMENT, add_file)]}, fallbacks=[CommandHandler("cancel", cancel)], allow_reentry=True)
    # exam subject selection via callback
    async def exam_sub_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q=update.callback_query; await q.answer(); subj=q.data.split(":",1)[1]; context.user_data["exam_subject"]=subj
        if context.user_data.get("exam_type")=="pdf":
            await q.message.reply_text("دز ملف PDF مال الاختبار هسه")
        else:
            # create mcq exam then ask question
            con=db(); cur=con.cursor(); cur.execute("INSERT INTO exams(subject,title,description,type,creator) VALUES(?,?,?, 'mcq', ?)", (subj, context.user_data.get("exam_title"), context.user_data.get("exam_desc",""), q.from_user.id)); eid=cur.lastrowid; con.commit(); con.close(); context.user_data["mcq_exam_id"]=eid; await q.message.reply_text("اكتب السؤال الأول:")
        return MCQ_Q
    conv_exam=ConversationHandler(entry_points=[CallbackQueryHandler(admin_cb, pattern="^a:exampdf$|^a:exammcq$")], states={EXAM_NAME:[MessageHandler(filters.TEXT & ~filters.COMMAND, exam_name)], EXAM_DESC:[MessageHandler(filters.TEXT & ~filters.COMMAND, exam_desc), CommandHandler("skip", exam_desc)], EXAM_FILE:[CallbackQueryHandler(exam_sub_cb, pattern="^exam_sub:")], MCQ_Q:[MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: mcq_q(u,c))]}, fallbacks=[CommandHandler("cancel", cancel)], allow_reentry=True)
    async def mcq_q(update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["q_text"]=update.message.text; await update.message.reply_text("دز 4 اختيارات مفصولة بـ | مثال: أ | ب | ج | د"); return MCQ_OPTS
    # Simplify: handle MCQ via general handlers in main app - we add extra handlers below
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("skip", skip))
    app.add_handler(CommandHandler("addteacher", addteacher))
    app.add_handler(CommandHandler("toggle", toggle_student))
    app.add_handler(CallbackQueryHandler(admin_cb, pattern="^a:"))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^(m:|sub:|mat:|exam|submit:|startmcq:|mcq:|grade:)"))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | filters.AUDIO | filters.VIDEO | filters.VOICE, general_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, general_text))
    print("Bot running..."); app.run_polling()

if __name__=="__main__": main()
