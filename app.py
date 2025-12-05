
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
# 1. SYSTEM CONFIGURATION & CSS OVERRIDES (The "Notion" Look)
# ==============================================================================
st.set_page_config(page_title="DeskBot Workspace", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

# Aggressive CSS injection to override Streamlit defaults for a premium feel.
st.markdown("""
<style>
    /* Main Background */
    .stApp {background-color: #191919; color: #E0E0E0; font-family: sans-serif;}
    
    /* Sidebar Background & Border */
    section[data-testid="stSidebar"] {background-color: #202020; border-right: 1px solid #2F2F2F;}
    
    /* Inputs & Selectboxes - Dark and Flat */
    .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"], .stDataEditor div[data-baseweb="data-grid"] {
        background-color: #2B2B2B !important; color: #E0E0E0 !important;
        border: 1px solid #3F3F3F !important; border-radius: 6px;
    }
    
    /* Hide Streamlit Header/Footer Clutter */
    header[data-testid="stHeader"], footer, #MainMenu {display: none;}
    .block-container {padding-top: 1.5rem; padding-bottom: 3rem;}

    /* Minimalist Tabs */
    .stTabs [data-baseweb="tab-list"] {gap: 16px; border-bottom: 1px solid #333;}
    .stTabs [data-baseweb="tab"] {height: 40px; border: none; background: transparent; color: #888; font-weight: 500;}
    .stTabs [aria-selected="true"] {color: #FFF !important; border-bottom: 2px solid #FFF;}

    /* Clean Chat Bubbles (No Avatars) */
    .stChatMessage {background-color: transparent; border: none; padding: 5px 0;}
    [data-testid="stChatMessageAvatarUser"], [data-testid="stChatMessageAvatarAssistant"] {display: none !important;}
    
    /* Custom Buttons */
    .stButton button {
        background-color: #2B2B2B; color: #CCC; border: 1px solid #444; border-radius: 6px; transition: all 0.2s;
    }
    .stButton button:hover {border-color: #FFF; color: #FFF; background-color: #333;}
</style>
""", unsafe_allow_html=True)

# Session State Management
if "user" not in st.session_state: st.session_state.user = None
if "active_ws_id" not in st.session_state: st.session_state.active_ws_id = None
if "chat_session" not in st.session_state: st.session_state.chat_session = None

# ==============================================================================
# 2. BACKEND CONNECTION
# ==============================================================================
@st.cache_resource
def init_supabase():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except: st.error("🚨 Critical Error: Supabase Secrets Missing."); st.stop()

supabase = init_supabase()

# ==============================================================================
# 3. DATA LAYER (The Source of Truth)
# ==============================================================================
class DB:
    """Static class for organized database interactions."""
    @staticmethod
    def log_login(user_id):
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
            # Ensure IDs are integers for editor compatibility
            if not df.empty: df["id"] = pd.to_numeric(df["id"]).astype(int)
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
# 4. UTILITIES & AI AGENT
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
    
    # Tool for the AI
    def add_task_tool(task_title: str, due_date_iso: str = None):
        """Adds a task. due_date_iso must be 'YYYY-MM-DD' format or None."""
        ws_id = st.session_state.active_ws_id
        if not ws_id: return "Error: No active workspace."
        try:
            DB.create_task(st.session_state.user.id, ws_id, task_title, 60, due_date_iso)
            return f"✅ Added task: {task_title} (Due: {due_date_iso})"
        except Exception as e: return f"❌ Database Error: {e}"

    model = genai.GenerativeModel('gemini-2.0-flash', tools=[add_task_tool])
    
    # Ensure fresh session for tool use
    if "chat_session" not in st.session_state or st.session_state.chat_session is None:
        st.session_state.chat_session = model.start_chat(enable_automatic_function_calling=True)

def ask_agent(msg, ctx, img=None):
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        prompt = [f"SYSTEM: Today is {today}. Use 'add_task_tool' if user wants to schedule something.", f"CONTEXT:\n{ctx}", f"USER: {msg}"]
        if img: prompt.append(img)
        return st.session_state.chat_session.send_message(prompt).text
    except Exception as e: return f"AI Error: {e}"

def generate_mindmap(topic, ctx):
    try:
        prompt = f"Context: {ctx[:4000]}\nGenerate Graphviz DOT code for a mindmap on '{topic}'. Output ONLY the DOT code inside ```dot blocks. Keep it hierarchical and clean."
        resp = model.generate_content(prompt)
        match = re.search(r'```dot\n(.*?)\n```', resp.text, re.DOTALL)
        return match.group(1) if match else None
    except: return None

# ==============================================================================
# 5. APP VIEWS
# ==============================================================================
def auth_view():
    c1, c2, c3 = st.columns([1, 1.5, 1])
    with c2:
        st.markdown("<h1 style='text-align: center;'>⚡ DeskBot</h1>", unsafe_allow_html=True)
        tab1, tab2 = st.tabs(["Login", "Sign Up"])
        with tab1:
            with st.form("login"):
                e = st.text_input("Email"); p = st.text_input("Password", type="password")
                if st.form_submit_button("Login", use_container_width=True):
                    try:
                        res = supabase.auth.sign_in_with_password({"email":e,"password":p})
                        st.session_state.user = res.user
                        DB.log_login(res.user.id) # Audit trail
                        st.rerun()
                    except: st.error("Bad credentials.")
        with tab2:
            with st.form("signup"):
                e = st.text_input("Email"); p = st.text_input("Password (min 6)")
                if st.form_submit_button("Create Account", use_container_width=True):
                    try:
                        res = supabase.auth.sign_up({"email":e,"password":p})
                        st.session_state.user = res.user
                        st.success("Created! Log in now."); st.rerun()
                    except: st.error("Signup failed.")

def main_view():
    user = st.session_state.user
    workspaces_df = DB.get_workspaces(user.id)

    # --- CRITICAL: AUTO-INITIALIZE WORKSPACE ---
    # If no workspaces exist, create one immediately so the UI never breaks.
    if workspaces_df.empty:
        new_id = DB.create_workspace(user.id, "General")
        st.session_state.active_ws_id = new_id
        st.rerun()

    # Ensure active ID is valid
    if st.session_state.active_ws_id not in workspaces_df['id'].values:
         st.session_state.active_ws_id = workspaces_df.iloc[0]['id']

    active_ws_id = st.session_state.active_ws_id
    active_ws_title = workspaces_df[workspaces_df['id'] == active_ws_id].iloc[0]['title']

    # --- SIDEBAR ---
    with st.sidebar:
        st.caption(f"👤 {user.email}")
        st.markdown("---")
        
        # --- WORKSPACE SWITCHER (The Core Feature) ---
        st.markdown("### 🔄 Switch Workspace")
        
        # Create dictionary for dropdown: {title: id}
        ws_map = dict(zip(workspaces_df['title'], workspaces_df['id']))
        # Reverse map to find current title from ID
        id_map = dict(zip(workspaces_df['id'], workspaces_df['title']))
        current_title = id_map.get(active_ws_id)

        selected_title = st.selectbox(
            "Select Workspace",
            options=workspaces_df['title'].tolist(),
            index=workspaces_df['title'].tolist().index(current_title) if current_title else 0,
            label_visibility="collapsed"
        )

        # Detect switch and update state immediately
        if selected_title and ws_map[selected_title] != active_ws_id:
            st.session_state.active_ws_id = ws_map[selected_title]
            st.rerun()

        # New Workspace Popover
        with st.popover("➕ Create New Workspace", use_container_width=True):
            new_name = st.text_input("Workspace Name", placeholder="e.g., Physics Project")
            if st.button("Create"):
                if new_name:
                    DB.create_workspace(user.id, new_name)
                    st.rerun()
        
        st.markdown("---")
        
        # --- SOURCES SECTION ---
        st.markdown("### 📄 Sources (Active Workspace)")
        docs = DB.get_docs(active_ws_id)
        if docs:
            for d in docs:
                c1, c2 = st.columns([4, 1])
                c1.caption(f"{d['filename'][:20]}")
                if c2.button("🗑️", key=f"del_{d['id']}"): DB.delete_doc(d['id']); st.rerun()
        else:
            st.caption("No sources attached.")
            
        with st.expander("Upload PDF/Image"):
            up_file = st.file_uploader("File", type=["pdf", "png", "jpg"], label_visibility="collapsed")
            if up_file and up_file.type == "application/pdf":
                 if st.button("Index PDF"):
                     txt = extract_pdf(up_file)
                     if txt: DB.save_doc(user.id, active_ws_id, up_file.name, txt); st.toast("PDF Indexed!"); st.rerun()

        st.markdown("---")
        if st.button("Log Out", use_container_width=True):
            supabase.auth.sign_out()
            st.session_state.user = None
            st.rerun()

    # --- MAIN CANVAS ---
    col_chat, col_studio = st.columns([1, 1.4], gap="medium")

    # === LEFT COLUMN: CHAT ===
    with col_chat:
        st.markdown(f"### 💬 {active_ws_title}")
        chat_box = st.container(height=600)
        with chat_box:
            history = DB.get_chat(active_ws_id)
            if not history: st.markdown("Start the conversation...")
            for msg in history:
                with st.chat_message(msg["role"]):
                    if msg.get("image_data"):
                        try: st.image(base64_to_image(msg["image_data"]), width=300)
                        except: pass
                    st.write(msg["content"])

        # Input handling (Text + optional Image from sidebar)
        if prompt := st.chat_input("Ask about this workspace..."):
            img_data = None; pil_img = None
            if 'up_file' in locals() and up_file and up_file.type != "application/pdf":
                pil_img = Image.open(up_file); img_data = image_to_base64(pil_img)

            DB.save_chat(user.id, active_ws_id, "user", prompt, img_data)
            
            # Build context from Tasks and Docs belonging to THIS workspace
            tasks_df = DB.get_tasks(active_ws_id)
            ctx = "TASKS:\n" + tasks_df.to_string() if not tasks_df.empty else "TASKS: None."
            docs = DB.get_docs(active_ws_id)
            for d in docs: ctx += f"\nDOC: {d['filename']}\nCONTENT: {d['content'][:10000]}"

            with st.spinner("Thinking..."):
                reply = ask_agent(prompt, ctx, pil_img)
            
            DB.save_chat(user.id, active_ws_id, "assistant", reply)
            st.rerun()

    # === RIGHT COLUMN: STUDIO TOOLS ===
    with col_studio:
        st.markdown("### 🛠️ Studio")
        t1, t2, t3 = st.tabs(["📅 Schedule", "🧠 Mind Map", "📝 Summary"])

        # 1. Schedule Tab (Calendar + Grid)
        with t1:
            tasks_df = DB.get_tasks(active_ws_id)
            if not tasks_df.empty:
                cal_events = []
                for _, row in tasks_df.iterrows():
                    if row['due_date']:
                        color = "#2ecc71" if row['status'] == 'done' else "#3498db"
                        cal_events.append({"title": row['title'], "start": row['due_date'], "allDay": True, "backgroundColor": color})
                calendar(events=cal_events, options={"headerToolbar": {"left": "prev,next", "center": "title", "right": "dayGridMonth"}, "height": 350})
                
                st.divider()
                # Editable Grid
                edited = st.data_editor(
                    tasks_df, key=f"ed_{active_ws_id}", hide_index=True, use_container_width=True,
                    column_config={
                        "id": None, "user_id": None, "workspace_id": None, "created_at": None, "est_minutes": None,
                        "title": st.column_config.TextColumn("Task"),
                        "due_date": st.column_config.DateColumn("Due Date"),
                        "status": st.column_config.SelectboxColumn("Status", options=["todo", "done"])
                    }
                )
                # Handle Grid Edits/Deletes
                if st.session_state[f"ed_{active_ws_id}"]["edited_rows"]:
                    for i, updates in st.session_state[f"ed_{active_ws_id}"]["edited_rows"].items():
                        if "due_date" in updates and updates["due_date"]:
                             updates["due_date"] = updates["due_date"].isoformat() # Convert Date -> String for DB
                        DB.update_task(tasks_df.iloc[i]["id"], updates)
                    st.rerun()
                if st.session_state[f"ed_{active_ws_id}"]["deleted_rows"]:
                    for i in st.session_state[f"ed_{active_ws_id}"]["deleted_rows"]:
                        DB.delete_task(tasks_df.iloc[i]["id"])
                    st.rerun()
            else: st.info("No tasks in this workspace.")

        # 2. Mind Map Tab
        with t2:
            if st.button("Generate Graph", use_container_width=True):
                docs = DB.get_docs(active_ws_id)
                txt = "".join([d['content'][:8000] for d in docs])
                if txt:
                    with st.spinner("Visualizing..."):
                        dot = generate_mindmap(active_ws_title, txt)
                        if dot: st.graphviz_chart(dot)
                        else: st.error("Could not visualize data.")
                else: st.warning("Upload a PDF to this workspace first.")

        # 3. Summary Tab
        with t3:
            if st.button("Synthesize Summary", use_container_width=True):
                docs = DB.get_docs(active_ws_id)
                txt = "".join([d['content'][:12000] for d in docs])
                if txt:
                    with st.spinner("Summarizing..."):
                        s = ask_agent("Provide a structured executive summary of these documents.", txt)
                        st.markdown(s)
                else: st.warning("Upload a PDF to this workspace first.")

# ==============================================================================
# 6. EXECUTION ROOT
# ==============================================================================
if st.session_state.user: main_view()
else: auth_view()
