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
from datetime import datetime, date
import graphviz

# ==============================================================================
# 1. UI ARCHITECTURE (CSS INJECTION)
# ==============================================================================
st.set_page_config(page_title="DeskBot // Workspace", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

# This CSS completely redesigns Streamlit to look like your reference image
st.markdown("""
<style>
    /* GLOBAL THEME */
    .stApp {background-color: #191919; color: #E0E0E0; font-family: 'Inter', sans-serif;}
    
    /* SIDEBAR */
    section[data-testid="stSidebar"] {
        background-color: #202020;
        border-right: 1px solid #2F2F2F;
    }
    
    /* FLATTENED INPUTS */
    .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] {
        background-color: #2B2B2B !important; 
        color: #FFF !important;
        border: 1px solid #3F3F3F !important; 
        border-radius: 8px;
    }
    
    /* HIDE CHROME */
    header[data-testid="stHeader"] {display: none;}
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .block-container {padding-top: 1.5rem; padding-bottom: 3rem;}

    /* MINIMAL TABS */
    .stTabs [data-baseweb="tab-list"] {gap: 20px; border-bottom: 1px solid #333;}
    .stTabs [data-baseweb="tab"] {height: 40px; background: transparent; color: #888; border: none;}
    .stTabs [aria-selected="true"] {color: #FFF !important; border-bottom: 2px solid #FFF;}

    /* BUTTONS */
    .stButton button {
        background-color: #2B2B2B; color: #CCC; border: 1px solid #444; width: 100%; border-radius: 6px;
    }
    .stButton button:hover {border-color: #FFF; color: #FFF; background-color: #333;}

    /* CHAT BUBBLES */
    .stChatMessage {background-color: transparent; border: none; padding: 5px 0;}
    [data-testid="stChatMessageAvatarUser"], [data-testid="stChatMessageAvatarAssistant"] {display: none !important;}
    
    /* MODAL STYLE */
    div[data-testid="stExpander"] {background-color: #252525; border-radius: 8px; border: 1px solid #333;}
</style>
""", unsafe_allow_html=True)

# Session State
if "user" not in st.session_state: st.session_state.user = None
if "active_ws_id" not in st.session_state: st.session_state.active_ws_id = None
if "chat_session" not in st.session_state: st.session_state.chat_session = None

# ==============================================================================
# 2. BACKEND
# ==============================================================================
@st.cache_resource
def init_supabase():
    try: return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    except: return None

supabase = init_supabase()
if not supabase: st.error("🚨 CRITICAL: Check Supabase Secrets."); st.stop()

# ==============================================================================
# 3. DATA LAYER
# ==============================================================================
class DB:
    @staticmethod
    def log_login(user_id):
        # FEATURE: Login Audit Trail
        try: supabase.table("login_logs").insert({"user_id": user_id}).execute()
        except: pass

    @staticmethod
    def get_workspaces(user_id):
        try:
            res = supabase.table("workspaces").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
            return pd.DataFrame(res.data)
        except: return pd.DataFrame()

    @staticmethod
    def create_workspace(user_id, title):
        try:
            res = supabase.table("workspaces").insert({"user_id": user_id, "title": title}).execute()
            return res.data[0]['id']
        except: return None

    @staticmethod
    def get_tasks(ws_id):
        try:
            res = supabase.table("tasks").select("*").eq("workspace_id", ws_id).order("id", desc=True).execute()
            df = pd.DataFrame(res.data)
            if not df.empty:
                df["id"] = pd.to_numeric(df["id"]).astype(int)
                df["due_date"] = pd.to_datetime(df["due_date"], errors='coerce').dt.date
            return df
        except: return pd.DataFrame()

    @staticmethod
    def create_task(user_id, ws_id, title, est, due):
        try:
            supabase.table("tasks").insert({"user_id": user_id, "workspace_id": ws_id, "title": title, "est_minutes": est, "due_date": due}).execute()
            return True
        except: return False

    @staticmethod
    def update_task(tid, updates):
        try: supabase.table("tasks").update(updates).eq("id", tid).execute()
        except: pass

    @staticmethod
    def delete_task(tid):
        try: supabase.table("tasks").delete().eq("id", tid).execute()
        except: pass

    @staticmethod
    def save_doc(user_id, ws_id, filename, content):
        try: supabase.table("documents").insert({"user_id": user_id, "workspace_id": ws_id, "filename": filename, "content": content}).execute()
        except: pass

    @staticmethod
    def get_docs(ws_id):
        try: return supabase.table("documents").select("*").eq("workspace_id", ws_id).execute().data
        except: return []

    @staticmethod
    def delete_doc(did):
        try: supabase.table("documents").delete().eq("id", did).execute()
        except: pass

    @staticmethod
    def save_chat(user_id, ws_id, role, content, img=None):
        try:
            data = {"user_id": user_id, "workspace_id": ws_id, "role": role, "content": content}
            if img: data["image_data"] = img
            supabase.table("chat_history").insert(data).execute()
        except: pass

    @staticmethod
    def get_chat(ws_id):
        try: return supabase.table("chat_history").select("*").eq("workspace_id", ws_id).order("created_at").execute().data
        except: return []

# ==============================================================================
# 4. INTELLIGENCE
# ==============================================================================
def image_to_base64(image):
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

def base64_to_image(base64_str):
    try: return Image.open(BytesIO(base64.b64decode(base64_str)))
    except: return None

def extract_pdf(file):
    try:
        reader = PdfReader(file)
        return "".join([p.extract_text() for p in reader.pages])
    except: return None

if "GOOGLE_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    
    def add_task_tool(task_title: str, duration_minutes: int, due_date: str):
        """Adds a task. due_date must be 'YYYY-MM-DD'."""
        ws_id = st.session_state.active_ws_id
        if not ws_id: return "Error: No active workspace."
        try:
            if not isinstance(duration_minutes, int): duration_minutes = 60
            DB.create_task(st.session_state.user.id, ws_id, task_title, duration_minutes, due_date)
            return f"✅ Scheduled: {task_title} ({due_date})"
        except Exception as e: return f"❌ DB Error: {e}"

    model = genai.GenerativeModel('gemini-2.0-flash', tools=[add_task_tool])
    
    if "chat_session" not in st.session_state or st.session_state.chat_session is None:
        st.session_state.chat_session = model.start_chat(enable_automatic_function_calling=True)

def ask_agent(msg, ctx, img=None):
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        prompt = [f"SYSTEM: Today is {today}. If user gives time, put it in Title. USE TOOLS if scheduling.", f"CONTEXT:\n{ctx}", f"USER: {msg}"]
        if img: prompt.append(img)
        return st.session_state.chat_session.send_message(prompt).text
    except Exception as e: return f"AI Error: {e}"

def generate_mindmap(topic, ctx):
    try:
        prompt = f"Context: {ctx[:4000]}\nGenerate Graphviz DOT code for a mindmap on '{topic}'. Output ONLY the DOT code inside ```dot blocks."
        resp = model.generate_content(prompt)
        match = re.search(r'```dot\n(.*?)\n```', resp.text, re.DOTALL)
        return match.group(1) if match else None
    except: return None

# ==============================================================================
# 5. UI VIEWS
# ==============================================================================
def auth_view():
    c1, c2, c3 = st.columns([1, 1.5, 1])
    with c2:
        st.markdown("<h1 style='text-align: center;'>⚡ DeskBot</h1>", unsafe_allow_html=True)
        tab1, tab2 = st.tabs(["Login", "Register"])
        with tab1:
            with st.form("login"):
                e = st.text_input("Email"); p = st.text_input("Password", type="password")
                if st.form_submit_button("Enter", use_container_width=True):
                    try:
                        res = supabase.auth.sign_in_with_password({"email":e,"password":p})
                        st.session_state.user = res.user
                        DB.log_login(res.user.id) # FEATURE: Logs login to DB
                        st.rerun()
                    except: st.error("Invalid Credentials")
        with tab2:
            with st.form("signup"):
                e = st.text_input("Email"); p = st.text_input("Password (min 6)")
                if st.form_submit_button("Create", use_container_width=True):
                    try:
                        res = supabase.auth.sign_up({"email":e,"password":p})
                        st.session_state.user = res.user
                        st.success("Created! Login now."); st.rerun()
                    except: st.error("Signup failed.")

def main_view():
    user = st.session_state.user
    
    # --- AUTO-INIT: Ensure a workspace always exists ---
    workspaces = DB.get_workspaces(user.id)
    if workspaces.empty:
        new_id = DB.create_workspace(user.id, "General")
        st.session_state.active_ws_id = new_id
        st.rerun()

    if st.session_state.active_ws_id is None and not workspaces.empty:
        st.session_state.active_ws_id = int(workspaces.iloc[0]['id'])

    active_ws_id = st.session_state.active_ws_id
    try:
        active_ws_title = workspaces[workspaces['id'] == active_ws_id].iloc[0]['title']
    except:
        active_ws_title = "Workspace"

    # --- SIDEBAR: NAVIGATION & I/O ---
    with st.sidebar:
        st.markdown(f"**{user.email}**")
        
        # 1. NEW WORKSPACE BUTTON
        if st.button("➕ New Workspace", use_container_width=True, type="primary"):
            st.session_state.show_create_modal = True

        if st.session_state.get("show_create_modal"):
            with st.form("new_ws"):
                title = st.text_input("Name")
                if st.form_submit_button("Create"):
                    if title:
                        new_id = DB.create_workspace(user.id, title)
                        st.session_state.active_ws_id = new_id
                        st.session_state.show_create_modal = False
                        st.rerun()

        st.markdown("### 📂 Workspaces")
        
        # Workspace Switcher
        for i, row in workspaces.iterrows():
            prefix = "🔹" if row['id'] == active_ws_id else "▫️"
            if st.button(f"{prefix} {row['title']}", key=f"ws_{row['id']}"):
                st.session_state.active_ws_id = row['id']
                st.rerun()

        st.markdown("---")
        st.markdown("### 📄 Sources")
        
        if active_ws_id:
            docs = DB.get_docs(active_ws_id)
            if docs:
                for d in docs:
                    c1, c2 = st.columns([4, 1])
                    c1.caption(f"{d['filename'][:15]}...")
                    if c2.button("×", key=f"del_{d['id']}"): DB.delete_doc(d['id']); st.rerun()
            else:
                st.caption("No sources.")
            
            with st.expander("Upload"):
                up_file = st.file_uploader("File", type=["pdf", "png", "jpg"], label_visibility="collapsed")
                if up_file:
                    if up_file.type == "application/pdf":
                        if st.button("Index PDF", use_container_width=True):
                            txt = extract_pdf(up_file)
                            if txt: DB.save_doc(user.id, active_ws_id, up_file.name, txt); st.toast("Indexed!"); st.rerun()
                    else:
                        st.image(Image.open(up_file), width=100)

        st.markdown("---")
        if st.button("Log Out", use_container_width=True):
            supabase.auth.sign_out()
            st.session_state.user = None
            st.rerun()

    # --- MAIN CANVAS (SPLIT VIEW) ---
    col_chat, col_studio = st.columns([1, 1.4], gap="medium")

    # === LEFT: CHAT ===
    with col_chat:
        st.markdown(f"### 💬 {active_ws_title}")
        chat_cont = st.container(height=600)
        
        with chat_cont:
            history = DB.get_chat(active_ws_id)
            if not history: st.info("Workspace ready.")
            for msg in history:
                with st.chat_message(msg["role"]):
                    if msg.get("image_data"):
                        try: st.image(base64_to_image(msg["image_data"]), width=300)
                        except: pass
                    st.write(msg["content"])

        if p := st.chat_input("Command..."):
            img_data = None; pil_img = None
            if 'up_file' in locals() and up_file and up_file.type != "application/pdf":
                pil_img = Image.open(up_file); img_data = image_to_base64(pil_img)

            DB.save_chat(user.id, active_ws_id, "user", p, img_data)
            
            tasks = DB.get_tasks(active_ws_id)
            ctx = "TASKS:\n" + tasks.to_string() if not tasks.empty else "No tasks."
            docs = DB.get_docs(active_ws_id)
            for d in docs: ctx += f"\nDOC: {d['filename']}\nCONTENT: {d['content'][:15000]}"

            with st.spinner("Processing..."):
                reply = ask_agent(p, ctx, pil_img)
            
            DB.save_chat(user.id, active_ws_id, "assistant", reply)
            st.rerun()

    # === RIGHT: STUDIO ===
    with col_studio:
        st.markdown("### 🛠️ Studio")
        t1, t2, t3 = st.tabs(["Plan", "Map", "Brief"])

        # 1. CALENDAR & GRID
        with t1:
            tasks = DB.get_tasks(active_ws_id)
            if not tasks.empty:
                cal_events = []
                for _, r in tasks.iterrows():
                    if r['due_date']:
                        color = "#2ecc71" if r['status']=='done' else "#3498db"
                        cal_events.append({"title": r['title'], "start": str(r['due_date']), "allDay": True, "backgroundColor": color})
                calendar(events=cal_events, options={"headerToolbar": {"left": "prev,next", "center": "title", "right": "dayGridMonth"}, "height": 350})
                
                st.divider()
                edited = st.data_editor(
                    tasks, key=f"ed_{active_ws_id}", hide_index=True, use_container_width=True,
                    column_config={
                        "id": None, "user_id": None, "workspace_id": None, "created_at": None, "est_minutes": None,
                        "title": st.column_config.TextColumn("Task"),
                        "due_date": st.column_config.DateColumn("Due Date"),
                        "status": st.column_config.SelectboxColumn("Status", options=["todo", "done"])
                    }
                )
                # Auto-Save Logic
                if st.session_state[f"ed_{active_ws_id}"]["edited_rows"]:
                    for i, u in st.session_state[f"ed_{active_ws_id}"]["edited_rows"].items():
                        if "due_date" in u and u["due_date"]: u["due_date"] = u["due_date"].strftime('%Y-%m-%d')
                        DB.update_task(tasks.iloc[i]["id"], u)
                    st.rerun()
                if st.session_state[f"ed_{active_ws_id}"]["deleted_rows"]:
                    for i in st.session_state[f"ed_{active_ws_id}"]["deleted_rows"]:
                        DB.delete_task(tasks.iloc[i]["id"])
                    st.rerun()
            else: st.info("No tasks in this workspace.")

        # 2. MIND MAP
        with t2:
            if st.button("Generate Graph", use_container_width=True):
                docs = DB.get_docs(active_ws_id)
                txt = "".join([d['content'][:10000] for d in docs])
                if txt:
                    with st.spinner("Visualizing..."):
                        dot = generate_mindmap(active_ws_title, txt)
                        if dot: st.graphviz_chart(dot)
                        else: st.error("Failed")
                else: st.warning("Upload PDF first")

        # 3. SUMMARY
        with t3:
            if st.button("Synthesize", use_container_width=True):
                docs = DB.get_docs(active_ws_id)
                txt = "".join([d['content'][:15000] for d in docs])
                if txt:
                    with st.spinner("Computing..."):
                        s = ask_agent("Executive Summary", txt)
                        st.markdown(s)
                else: st.warning("Upload PDF first")

# ==============================================================================
# 6. EXECUTION
# ==============================================================================
if st.session_state.user: main_view()
else: auth_view()
