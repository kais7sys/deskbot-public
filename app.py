import streamlit as st
import pandas as pd
import google.generativeai as genai
from PyPDF2 import PdfReader
from PIL import Image
import time
import base64
import re
from io import BytesIO
from supabase import create_client, Client
from streamlit_calendar import calendar
from datetime import datetime

# --- 1. CONFIG ---
st.set_page_config(page_title="DeskBot: Agent", page_icon="🤖", layout="wide")
if "user" not in st.session_state: st.session_state.user = None

# --- 2. DATABASE ---
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try: supabase = init_supabase()
except: st.error("⚠️ Supabase Keys missing!")

# --- 3. HELPER FUNCTIONS ---
def image_to_base64(image):
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

def base64_to_image(base64_str):
    try: return Image.open(BytesIO(base64.b64decode(base64_str)))
    except: return None

# --- 4. AUTH ---
def login_page():
    st.title("☁️ DeskBot: Login")
    tab1, tab2 = st.tabs(["Log In", "Sign Up"])
    with tab1:
        with st.form("login"):
            e = st.text_input("Email"); p = st.text_input("Pass", type="password")
            if st.form_submit_button("Log In"):
                try:
                    res = supabase.auth.sign_in_with_password({"email":e,"password":p})
                    st.session_state.user = res.user
                    st.success("Success!"); time.sleep(0.5); st.rerun()
                except Exception as err: st.error(f"Error: {err}")
    with tab2:
        with st.form("signup"):
            e = st.text_input("Email"); p = st.text_input("Pass (min 6)", type="password")
            if st.form_submit_button("Sign Up"):
                try:
                    res = supabase.auth.sign_up({"email":e,"password":p})
                    st.session_state.user = res.user
                    st.success("Created!"); time.sleep(0.5); st.rerun()
                except Exception as err: st.error(f"Error: {err}")

def logout():
    supabase.auth.sign_out()
    st.session_state.user = None
    st.rerun()

# --- 5. MAIN APP ---
def main_app():
    user_id = st.session_state.user.id
    email = st.session_state.user.email

    # --- DB TOOLS ---
    def get_tasks():
        try:
            res = supabase.table("tasks").select("*").eq("user_id", user_id).order("id").execute()
            df = pd.DataFrame(res.data)
            if not df.empty:
                df["id"] = df["id"].astype(int)
                df["est_minutes"] = df["est_minutes"].astype(int)
                df["due_date"] = pd.to_datetime(df["due_date"]).dt.date
                df["status"] = df["status"].astype(str)
            return df
        except: return pd.DataFrame()

    def create_task_tool(title: str, duration_minutes: int, due_date: str):
        """
        Creates a task.
        duration_minutes: Duration in minutes (e.g. 60). NOT the time of day.
        due_date: YYYY-MM-DD.
        """
        try:
            # Fallback if AI sends 0 or crazy number
            if duration_minutes < 1: duration_minutes = 30
            
            supabase.table("tasks").insert({
                "user_id": user_id, 
                "title": title, 
                "est_minutes": duration_minutes, 
                "due_date": due_date
            }).execute()
            return f"✅ Created task: '{title}' ({duration_minutes}m) due {due_date}"
        except Exception as e: return f"❌ Error: {e}"

    def update_task_in_db(tid, updates):
        try: supabase.table("tasks").update(updates).eq("id", tid).execute()
        except: pass

    def save_document(filename, content, task_id):
        try:
            data = {"user_id": user_id, "filename": filename, "content": content}
            if task_id: data["task_id"] = int(task_id)
            supabase.table("documents").insert(data).execute()
        except Exception as e: st.error(f"Save Error: {e}")

    def get_task_documents(task_id):
        try:
            res = supabase.table("documents").select("id, filename, content").eq("task_id", task_id).execute()
            return res.data 
        except: return []

    def delete_document(doc_id):
        try: supabase.table("documents").delete().eq("id", doc_id).execute()
        except: pass

    def extract_pdf(file):
        try:
            reader = PdfReader(file)
            return "".join([p.extract_text() for p in reader.pages])
        except: return None

    # --- AI SETUP (FRESH START) ---
    if "GOOGLE_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        my_tools = [create_task_tool]
        model = genai.GenerativeModel('gemini-2.0-flash', tools=my_tools)
        
        # 🟢 VITAL FIX: Force create a NEW chat session on every run to bind new tools
        # We restore history from the DB later, so it's fine to start "fresh" logic-wise.
        if "chat_session" not in st.session_state or st.session_state.get("needs_refresh", False):
            st.session_state.chat_session = model.start_chat(enable_automatic_function_calling=True)
            st.session_state.needs_refresh = False

    def ask_agent(user_msg, context, image_data=None):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            prompt_parts = [
                f"""SYSTEM: You are DeskBot. Today is {today}.
                
                STRICT RULES:
                1. If user says a TIME (e.g. '7pm'), put it in the TITLE.
                2. 'duration_minutes' must be an INTEGER (e.g. 30, 60). Default to 60 if unknown.
                3. Do NOT put '7pm' in duration_minutes.
                """,
                f"CONTEXT:\n{context}",
                f"USER: {user_msg}"
            ]
            if image_data: prompt_parts.append(image_data)
            
            response = st.session_state.chat_session.send_message(prompt_parts)
            return response.text
        except Exception as e: 
            # If tool fails, force refresh next time
            st.session_state.needs_refresh = True
            return f"AI Error: {e}"

    # --- CHAT HISTORY ---
    def save_chat_message(role, content, task_id, image_data=None):
        try:
            data = {"user_id": user_id, "role": role, "content": content}
            if task_id: data["task_id"] = int(task_id)
            if image_data: data["image_data"] = image_data
            supabase.table("chat_history").insert(data).execute()
        except: pass

    def get_chat_history(task_id):
        try:
            if task_id:
                res = supabase.table("chat_history").select("*").eq("task_id", task_id).eq("user_id", user_id).order("created_at").execute()
            else:
                res = supabase.table("chat_history").select("*").is_("task_id", "null").eq("user_id", user_id).order("created_at").execute()
            return res.data
        except: return []

    # --- UI LAYOUT ---
    tasks_df = get_tasks()

    with st.sidebar:
        st.caption(f"User: {email}")
        if st.button("Log Out"): logout()
        st.divider()

        st.header("🎯 Notebooks")
        selected_task_id = None
        selected_task_title = "General"
        
        if not tasks_df.empty:
            task_options = {f"{row['id']} - {row['title']}": row['id'] for i, row in tasks_df.iterrows()}
            options_list = ["No Focus (General)"] + list(task_options.keys())
            choice = st.selectbox("Select Notebook", options_list)
            if choice != "No Focus (General)":
                selected_task_id = task_options[choice]
                selected_task_title = choice.split(" - ")[1]
        
        st.divider()
        if selected_task_id:
            st.subheader(f"📂 Files: {selected_task_title}")
            task_docs = get_task_documents(selected_task_id)
            if task_docs:
                for d in task_docs:
                    c1,c2 = st.columns([4,1])
                    c1.text(f"📄 {d['filename']}")
                    if c2.button("X", key=f"d{d['id']}"): delete_document(d['id']); st.rerun()
            
            up_file = st.file_uploader("Upload File", type=["pdf", "png", "jpg", "jpeg"])
            if up_file:
                if up_file.type == "application/pdf":
                    if st.button("Save PDF"):
                        txt = extract_pdf(up_file)
                        if txt: save_document(up_file.name, txt, selected_task_id); st.success("Saved!"); time.sleep(1); st.rerun()
                else:
                    st.image(Image.open(up_file), caption="Ready", width=200)

    st.title(f"🤖 {selected_task_title}")
    tab1, tab2, tab3 = st.tabs(["📝 Grid", "📅 Calendar", "💬 Agent Chat"])

    with tab1:
        if not tasks_df.empty:
            edited = st.data_editor(tasks_df, key="editor", hide_index=True,
                column_config={"id":st.column_config.NumberColumn(disabled=True), "user_id":None})
            if st.session_state["editor"]["edited_rows"]:
                for idx, updates in st.session_state["editor"]["edited_rows"].items():
                    update_task_in_db(tasks_df.iloc[idx]["id"], updates)
                st.toast("Updated!")
        else: st.info("No tasks.")

    with tab2:
        if not tasks_df.empty:
            cal_events = []
            for i, row in tasks_df.iterrows():
                color = "#28a745" if row['status'] == 'done' else "#3788d8"
                cal_events.append({"title": f"{row['title']} ({row['est_minutes']}m)", "start": str(row['due_date']), "end": str(row['due_date']), "backgroundColor": color, "borderColor": color})
            calendar(events=cal_events, options={"headerToolbar": {"left": "today prev,next", "center": "title", "right": "dayGridMonth"}, "initialView": "dayGridMonth"})
        else: st.info("No schedule.")

    with tab3:
        history = get_chat_history(selected_task_id)
        for msg in history:
            with st.chat_message(msg["role"]):
                if msg.get("image_data"):
                    try: st.image(base64_to_image(msg["image_data"]), width=300)
                    except: pass
                st.markdown(msg["content"])
        
        if p := st.chat_input("Ex: 'Add a task for Physics Exam at 7pm'"):
            img_to_send = None
            img_base64 = None
            if 'up_file' in locals() and up_file and up_file.type != "application/pdf":
                img_to_send = Image.open(up_file)
                img_base64 = image_to_base64(img_to_send)

            with st.chat_message("user"):
                if img_to_send: st.image(img_to_send, width=300)
                st.markdown(p)
            save_chat_message("user", p, selected_task_id, img_base64)
            
            ctx = tasks_df.to_string() if not tasks_df.empty else "No tasks."
            if selected_task_id:
                docs = get_task_documents(selected_task_id)
                for d in docs: ctx += f"\nFILE: {d['filename']}\nCONTENT: {d['content'][:10000]}"

            with st.chat_message("assistant"):
                with st.spinner("Agent working..."):
                    reply = ask_agent(p, ctx, img_to_send)
                    st.markdown(reply)
            
            save_chat_message("assistant", reply, selected_task_id)
            if "Created task" in reply: time.sleep(1); st.rerun()

if st.session_state.user: main_app()
else: login_page()
