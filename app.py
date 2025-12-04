import streamlit as st
import pandas as pd
import google.generativeai as genai
from PyPDF2 import PdfReader
from PIL import Image
import time
import base64
from io import BytesIO
from supabase import create_client, Client
from streamlit_calendar import calendar
from datetime import datetime, date

# --- 1. CONFIG & NOTION STYLE ---
st.set_page_config(page_title="DeskBot Workspace", page_icon="📓", layout="wide")

# Custom CSS for Notion-like clean aesthetics
st.markdown("""
<style>
    /* Clean white background and typography */
    .stApp {background-color: #ffffff;}
    
    /* Sidebar styling */
    [data-testid="stSidebar"] {background-color: #f7f9fb; border-right: 1px solid #e0e0e0;}
    
    /* Clean Tabs */
    .stTabs [data-baseweb="tab-list"] {gap: 20px; border-bottom: 1px solid #ddd;}
    .stTabs [data-baseweb="tab"] {height: 45px; font-weight: 500; font-size: 16px;}
    .stTabs [aria-selected="true"] {color: #000000; border-bottom: 2px solid #000000;}
    
    /* Remove default Streamlit clutter */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    /* Card styling for messages */
    .stChatMessage {background-color: transparent;}
</style>
""", unsafe_allow_html=True)

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
    c1, c2, c3 = st.columns([1,2,1])
    with c2:
        st.title("📓 DeskBot Workspace")
        st.caption("Sign in to access your AI Second Brain.")
        tab1, tab2 = st.tabs(["Log In", "Create Account"])
        with tab1:
            with st.form("login"):
                e = st.text_input("Email"); p = st.text_input("Password", type="password")
                if st.form_submit_button("Log In", use_container_width=True):
                    try:
                        res = supabase.auth.sign_in_with_password({"email":e,"password":p})
                        st.session_state.user = res.user
                        st.rerun()
                    except Exception as err: st.error(f"Error: {err}")
        with tab2:
            with st.form("signup"):
                e = st.text_input("Email"); p = st.text_input("Pass (min 6 chars)", type="password")
                if st.form_submit_button("Sign Up", use_container_width=True):
                    try:
                        res = supabase.auth.sign_up({"email":e,"password":p})
                        st.session_state.user = res.user
                        st.success("Account created!"); time.sleep(1); st.rerun()
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
                # 🔧 CRITICAL FIX: Robust Date Conversion
                # 1. Force ID/Mins to Int
                df["id"] = pd.to_numeric(df["id"], errors='coerce').fillna(0).astype(int)
                df["est_minutes"] = pd.to_numeric(df["est_minutes"], errors='coerce').fillna(60).astype(int)
                
                # 2. Force Date Object (Handles errors gracefully)
                df["due_date"] = pd.to_datetime(df["due_date"], errors='coerce').dt.date
                
                # 3. Drop rows where date is missing (Prevents Calendar Crash)
                df = df.dropna(subset=['due_date'])
                
                df["status"] = df["status"].astype(str)
            return df
        except Exception as e: 
            st.error(f"Data Load Error: {e}")
            return pd.DataFrame()

    def add_task_to_scheduler(task_title: str, duration_minutes: int, due_date: str):
        try:
            if not isinstance(duration_minutes, int): duration_minutes = 60
            supabase.table("tasks").insert({
                "user_id": user_id, "title": task_title, "est_minutes": duration_minutes, "due_date": due_date
            }).execute()
            return f"✅ Scheduled: '{task_title}' on {due_date}"
        except Exception as e: return f"❌ Error: {e}"

    def update_task_in_db(tid, updates):
        try: supabase.table("tasks").update(updates).eq("id", tid).execute()
        except: pass

    def delete_task_in_db(tid):
        try: supabase.table("tasks").delete().eq("id", tid).execute()
        except: pass

    def save_document(filename, content, task_id):
        try:
            data = {"user_id": user_id, "filename": filename, "content": content}
            if task_id: data["task_id"] = int(task_id)
            supabase.table("documents").insert(data).execute()
        except: pass

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

    # --- AI SETUP ---
    if "GOOGLE_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        my_tools = [add_task_to_scheduler]
        model = genai.GenerativeModel('gemini-2.0-flash', tools=my_tools)
        
        # Always fresh session to ensure tools work
        if "chat_session" not in st.session_state:
            st.session_state.chat_session = model.start_chat(enable_automatic_function_calling=True)

    def ask_agent(user_msg, context, image_data=None):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            prompt_parts = [
                f"SYSTEM: You are DeskBot. Today is {today}. If user gives time (e.g. 7pm), put it in Title.",
                f"CONTEXT:\n{context}", f"USER: {user_msg}"
            ]
            if image_data: prompt_parts.append(image_data)
            response = st.session_state.chat_session.send_message(prompt_parts)
            return response.text
        except Exception as e: return f"AI Error: {e}"

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

    # ==========================
    # UI LAYOUT
    # ==========================
    tasks_df = get_tasks()

    # --- SIDEBAR ---
    with st.sidebar:
        st.write(f"**{email}**")
        st.subheader("📚 Notebooks")
        selected_task_id = None
        selected_task_title = "General Chat"
        
        if not tasks_df.empty:
            task_options = {f"{row['title']}": row['id'] for i, row in tasks_df.iterrows()}
            options_list = ["General Chat"] + list(task_options.keys())
            choice = st.selectbox("Select Notebook", options_list, label_visibility="collapsed")
            if choice != "General Chat":
                selected_task_id = task_options[choice]
                selected_task_title = choice
        
        st.divider()
        st.subheader("📂 Sources")
        if selected_task_id:
            task_docs = get_task_documents(selected_task_id)
            if task_docs:
                for d in task_docs:
                    c1, c2 = st.columns([5,1])
                    c1.caption(f"📄 {d['filename']}")
                    if c2.button("x", key=f"d{d['id']}"): delete_document(d['id']); st.rerun()
            else: st.caption("No sources.")
            
            with st.expander("Add Source (+)", expanded=False):
                up_file = st.file_uploader("Upload", type=["pdf", "png", "jpg", "jpeg"], label_visibility="collapsed")
                if up_file:
                    if up_file.type == "application/pdf":
                        if st.button("Save PDF", use_container_width=True):
                            txt = extract_pdf(up_file)
                            if txt: save_document(up_file.name, txt, selected_task_id); st.success("Saved!"); time.sleep(1); st.rerun()
                    else: st.image(Image.open(up_file), caption="Preview", width=150)
        else: st.caption("Select a notebook to add files.")

        st.divider()
        if st.button("Log Out"): logout()

    # --- MAIN CONTENT ---
    st.title(f"{selected_task_title}")
    tab_chat, tab_plan = st.tabs(["💬 Chat", "🗓️ Plan & Calendar"])

    # === TAB 1: CHAT ===
    with tab_chat:
        history_container = st.container()
        if p := st.chat_input("Ask DeskBot..."):
            img_to_send = None; img_base64 = None
            if 'up_file' in locals() and up_file and up_file.type != "application/pdf":
                img_to_send = Image.open(up_file); img_base64 = image_to_base64(img_to_send)

            save_chat_message("user", p, selected_task_id, img_base64)
            
            # Smart Context: Show Task List + Documents
            ctx = tasks_df[['title', 'due_date', 'status']].to_string() if not tasks_df.empty else "No tasks."
            if selected_task_id:
                docs = get_task_documents(selected_task_id)
                for d in docs: ctx += f"\nFILE: {d['filename']}\nCONTENT: {d['content'][:10000]}"

            with st.spinner("Thinking..."):
                reply = ask_agent(p, ctx, img_to_send)
            save_chat_message("assistant", reply, selected_task_id)
            
            if "Scheduled:" in reply or "Created task" in reply: time.sleep(1); st.rerun()
            else: st.rerun()

        with history_container:
            history = get_chat_history(selected_task_id)
            if not history: st.info("👋 Hi! I'm DeskBot. I can read your PDFs, see images, and schedule tasks.")
            for msg in history:
                with st.chat_message(msg["role"]):
                    if msg.get("image_data"):
                        try: st.image(base64_to_image(msg["image_data"]), width=300)
                        except: pass
                    st.markdown(msg["content"])

    # === TAB 2: DASHBOARD ===
    with tab_plan:
        c_cal, c_list = st.columns([2, 1])
        
        with c_cal:
            st.subheader("Calendar")
            if not tasks_df.empty:
                cal_events = []
                for i, row in tasks_df.iterrows():
                    color = "#4CAF50" if row['status'] == 'done' else "#2196F3"
                    cal_events.append({
                        "title": row['title'],
                        "start": str(row['due_date']), # STR IS SAFE HERE
                        "allDay": True,
                        "backgroundColor": color, "borderColor": color
                    })
                calendar(events=cal_events, options={"headerToolbar": {"left": "prev,next today", "center": "title", "right": "dayGridMonth"}, "initialView": "dayGridMonth", "height": 550})
            else: st.info("No tasks scheduled.")

        with c_list:
            st.subheader("Task List")
            if not tasks_df.empty:
                edited = st.data_editor(
                    tasks_df, key="editor", hide_index=True, use_container_width=True,
                    column_config={
                        "id": None, "user_id": None, "est_minutes": None,
                        "title": st.column_config.TextColumn("Task"),
                        "due_date": st.column_config.DateColumn("Due"), # DATE OBJECT SAFE HERE
                        "status": st.column_config.SelectboxColumn("Status", options=["todo", "done"])
                    }
                )
                if st.session_state["editor"]["edited_rows"]:
                    for idx, updates in st.session_state["editor"]["edited_rows"].items():
                        update_task_in_db(tasks_df.iloc[idx]["id"], updates)
                    st.rerun()
                if st.session_state["editor"]["deleted_rows"]:
                    for idx in st.session_state["editor"]["deleted_rows"]:
                        delete_task_in_db(tasks_df.iloc[idx]["id"])
                    st.rerun()
            else: st.caption("List empty.")

if st.session_state.user: main_app()
else: login_page()
