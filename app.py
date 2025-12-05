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
# 1. SYSTEM CONFIGURATION & UI OVERRIDE
# ==============================================================================
st.set_page_config(page_title="DeskBot // Workspace", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

# Injecting Industrial-Grade CSS to force the Notion Look
# This overrides Streamlit's defaults to match your desired aesthetic.
st.markdown("""
<style>
    /* GLOBAL RESET */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    .stApp {
        background-color: #191919; /* Notion Dark Hex */
        color: #E3E3E3;
    }

    /* SIDEBAR ENGINEERING */
    [data-testid="stSidebar"] {
        background-color: #202020;
        border-right: 1px solid #2F2F2F;
        padding-top: 1rem;
    }
    [data-testid="stSidebar"] hr {
        border-color: #333;
    }
    
    /* INPUT FIELDS - FLATTENED & MINIMAL */
    .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] {
        background-color: #2B2B2B !important; 
        color: #E3E3E3 !important;
        border: 1px solid #3F3F3F !important; 
        border-radius: 6px;
        font-size: 14px;
    }
    .stTextInput input:focus, .stTextArea textarea:focus {
        border-color: #555 !important;
        box-shadow: none !important;
    }
    
    /* REMOVE STREAMLIT BLOAT */
    header[data-testid="stHeader"] {display: none;}
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .block-container {padding-top: 1.5rem; padding-bottom: 5rem; padding-left: 2rem; padding-right: 2rem;}

    /* TABS ARCHITECTURE - CLEAN & MODERN */
    .stTabs [data-baseweb="tab-list"] {
        gap: 24px; 
        border-bottom: 1px solid #2F2F2F;
        padding-bottom: 0px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 40px; 
        border: none; 
        background-color: transparent; 
        color: #888;
        font-weight: 500;
        font-size: 14px;
        padding: 0 4px;
    }
    .stTabs [aria-selected="true"] {
        color: #FFF !important; 
        border-bottom: 2px solid #FFF;
    }

    /* CHAT BUBBLES - GOOGLE STYLE */
    .stChatMessage {
        background-color: transparent;
        border: none;
        padding: 10px 0;
    }
    [data-testid="stChatMessageAvatarUser"], [data-testid="stChatMessageAvatarAssistant"] {
        display: none; /* Hide Icons for minimalism */
    }
    /* User Message */
    [data-testid="stChatMessage"]:nth-child(odd) {
        flex-direction: row-reverse;
        text-align: right;
    }
    [data-testid="stChatMessage"]:nth-child(odd) .stChatMessageContent {
        background-color: #333;
        border-radius: 12px;
        padding: 12px 16px;
        display: inline-block;
        max-width: 80%;
    }
    /* AI Message */
    [data-testid="stChatMessage"]:nth-child(even) .stChatMessageContent {
        background-color: transparent;
        padding: 0;
        color: #E3E3E3;
    }

    /* BUTTONS - SUBTLE & INTERACTIVE */
    .stButton button {
        background-color: #2B2B2B;
        color: #CCC;
        border: 1px solid #3F3F3F;
        border-radius: 6px;
        transition: all 0.1s ease;
        font-size: 14px;
        height: 38px;
    }
    .stButton button:hover {
        background-color: #383838;
        border-color: #555;
        color: #FFF;
    }
    /* Primary Action Buttons */
    div[data-testid="stVerticalBlock"] > div > .stButton > button {
       width: 100%;
       text-align: left;
       justify-content: flex-start;
       padding-left: 12px;
    }

    /* CUSTOM CARDS */
    div.css-1r6slb0 {
        border: 1px solid #333;
        border-radius: 8px;
        padding: 16px;
        background-color: #1E1E1E;
    }
</style>
""", unsafe_allow_html=True)

# State Management Initialization
if "user" not in st.session_state: st.session_state.user = None
if "active_notebook_id" not in st.session_state: st.session_state.active_notebook_id = None
if "chat_session" not in st.session_state: st.session_state.chat_session = None
if "settings_open" not in st.session_state: st.session_state.settings_open = False

# ==============================================================================
# 2. BACKEND INFRASTRUCTURE
# ==============================================================================
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try: supabase = init_supabase()
except: st.error("⚠️ SYSTEM ERROR: Database Credentials Missing.")

# ==============================================================================
# 3. UTILITIES & I/O
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

# ==============================================================================
# 4. DATABASE CONTROLLER
# ==============================================================================
class DB:
    """Encapsulated Database Logic."""
    
    @staticmethod
    def get_workspaces(user_id):
        try:
            res = supabase.table("tasks").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
            df = pd.DataFrame(res.data)
            if not df.empty:
                df["id"] = pd.to_numeric(df["id"]).astype(int)
                df["due_date"] = pd.to_datetime(df["due_date"], errors='coerce').dt.date
            return df
        except: return pd.DataFrame()

    @staticmethod
    def create_workspace(user_id, title):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            data = supabase.table("tasks").insert({
                "user_id": user_id, "title": title, "est_minutes": 0, "due_date": today
            }).execute()
            return data.data[0]['id']
        except: return None

    @staticmethod
    def log_login(user_id):
        try:
            supabase.table("login_logs").insert({"user_id": user_id}).execute()
        except: pass

    @staticmethod
    def save_chat(user_id, task_id, role, content, image_data=None):
        try:
            data = {"user_id": user_id, "task_id": int(task_id), "role": role, "content": content}
            if image_data: data["image_data"] = image_data
            supabase.table("chat_history").insert(data).execute()
        except: pass

    @staticmethod
    def get_chat(task_id):
        try:
            res = supabase.table("chat_history").select("*").eq("task_id", task_id).order("created_at").execute()
            return res.data
        except: return []

    @staticmethod
    def create_task(user_id, workspace_id, title, est, due):
        try:
            supabase.table("tasks").insert({
                "user_id": user_id, "workspace_id": workspace_id,
                "title": title, "est_minutes": est, "due_date": due
            }).execute()
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
    def save_doc(user_id, workspace_id, filename, content):
        try:
            supabase.table("documents").insert({
                "user_id": user_id, "workspace_id": workspace_id,
                "filename": filename, "content": content
            }).execute()
        except: pass

    @staticmethod
    def get_docs(workspace_id):
        try:
            res = supabase.table("documents").select("id, filename, content").eq("task_id", workspace_id).execute()
            return res.data
        except: return []

    @staticmethod
    def delete_doc(doc_id):
        try: supabase.table("documents").delete().eq("id", doc_id).execute()
        except: pass

# ==============================================================================
# 5. INTELLIGENCE LAYER
# ==============================================================================
if "GOOGLE_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

    def add_task_tool(task_title: str, duration_minutes: int, due_date: str):
        """Adds a task. Duration must be INT. Date YYYY-MM-DD."""
        if not st.session_state.active_notebook_id: return "Error: No workspace selected."
        try:
            if not isinstance(duration_minutes, int): duration_minutes = 60
            DB.create_task(st.session_state.user.id, st.session_state.active_notebook_id, task_title, duration_minutes, due_date)
            return f"✅ Scheduled: '{task_title}' on {due_date}"
        except Exception as e: return f"❌ Error: {e}"

    model = genai.GenerativeModel('gemini-2.0-flash', tools=[add_task_tool])
    
    if "chat_session" not in st.session_state or st.session_state.chat_session is None:
        st.session_state.chat_session = model.start_chat(enable_automatic_function_calling=True)

def generate_mindmap_code(topic, context):
    try:
        prompt = f"Context: {context[:5000]}\nGenerate Graphviz DOT code for a mindmap on '{topic}'. Output ONLY the DOT code inside ```dot``` blocks. Keep it simple and hierarchical."
        response = model.generate_content(prompt)
        match = re.search(r'```dot\n(.*?)\n```', response.text, re.DOTALL)
        return match.group(1) if match else None
    except: return None

def ask_agent(user_msg, context, image_data=None):
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        prompt_parts = [
            f"SYSTEM: You are DeskBot. Date: {today}. If user gives a time (e.g. 7pm), include it in the Title.",
            f"CONTEXT:\n{context}", f"USER: {user_msg}"
        ]
        if image_data: prompt_parts.append(image_data)
        response = st.session_state.chat_session.send_message(prompt_parts)
        return response.text
    except Exception as e: return f"AI Error: {e}"

# ==============================================================================
# 6. UI COMPONENTS
# ==============================================================================
def auth_view():
    c1, c2, c3 = st.columns([1,1.5,1])
    with c2:
        st.markdown("<h1 style='text-align: center; color: white;'>⚡ DeskBot</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; color: #888;'>Secure Neural Workspace</p>", unsafe_allow_html=True)
        st.markdown("---")
        
        tab1, tab2 = st.tabs(["Login", "Register"])
        
        with tab1:
            with st.form("login"):
                e = st.text_input("Email", placeholder="user@example.com")
                p = st.text_input("Password", type="password")
                if st.form_submit_button("Enter System", use_container_width=True):
                    try:
                        res = supabase.auth.sign_in_with_password({"email":e,"password":p})
                        st.session_state.user = res.user
                        DB.log_login(res.user.id)
                        st.rerun()
                    except Exception as err: st.error(f"Access Denied: {err}")
        
        with tab2:
            with st.form("signup"):
                e = st.text_input("Email", placeholder="user@example.com")
                p = st.text_input("Password (min 6)", type="password")
                if st.form_submit_button("Create Identity", use_container_width=True):
                    try:
                        res = supabase.auth.sign_up({"email":e,"password":p})
                        st.session_state.user = res.user
                        st.success("Identity Created. Proceed to Login."); time.sleep(1); st.rerun()
                    except Exception as err: st.error(f"Error: {err}")

def main_view():
    user = st.session_state.user
    workspaces = DB.get_workspaces(user.id)
    
    # --- SIDEBAR NAVIGATION ---
    with st.sidebar:
        st.markdown("#### ⚡ WORKSPACE")
        
        # New Workspace Button
        if st.button("➕ New Workspace", use_container_width=True):
            st.session_state.show_create_modal = True
            
        if st.session_state.get("show_create_modal"):
            with st.container():
                st.markdown("---")
                with st.form("new_ws"):
                    title = st.text_input("Name", placeholder="Project Alpha")
                    if st.form_submit_button("Create"):
                        if title:
                            new_id = DB.create_workspace(user.id, title)
                            st.session_state.active_notebook_id = new_id
                            st.session_state.show_create_modal = False
                            st.rerun()

        # Workspace Selector
        selected_ws_id = None
        selected_ws_title = "Select Workspace"
        
        if not workspaces.empty:
            options = ["Select..."] + workspaces['title'].tolist()
            ids = [None] + workspaces['id'].tolist()
            
            curr = st.session_state.active_notebook_id
            idx = ids.index(curr) if curr in ids else 0
            
            choice = st.selectbox("Active Notebook", options, index=idx, label_visibility="collapsed")
            
            if choice != "Select...":
                selected_ws_id = ids[options.index(choice)]
                selected_ws_title = choice
                st.session_state.active_notebook_id = selected_ws_id
            else:
                st.session_state.active_notebook_id = None

        st.markdown("---")
        st.markdown("#### 📂 SOURCES")
        
        if selected_ws_id:
            docs = DB.get_docs(selected_ws_id)
            if docs:
                for d in docs:
                    c1, c2 = st.columns([5,1])
                    c1.caption(f"📄 {d['filename'][:18]}...")
                    if c2.button("×", key=f"del_{d['id']}"): DB.delete_doc(d['id']); st.rerun()
            else: st.caption("No sources attached.")
            
            with st.expander("Upload Data", expanded=False):
                up_file = st.file_uploader("File", type=["pdf", "png", "jpg"], label_visibility="collapsed")
                if up_file:
                    if up_file.type == "application/pdf":
                        if st.button("Index PDF", use_container_width=True):
                            txt = extract_pdf(up_file)
                            if txt: DB.save_doc(user.id, selected_ws_id, up_file.name, txt); st.success("Indexed"); st.rerun()
                    else: st.image(Image.open(up_file), width=100)
        else:
            st.caption("Select workspace first.")

        st.markdown("---")
        # Settings & Logout
        if st.button("⚙️ Settings", use_container_width=True):
            st.toast("Settings Module Active (v1.0)")
            
        if st.button("Log Out", use_container_width=True): 
            supabase.auth.sign_out()
            st.session_state.user = None
            st.rerun()

    # --- MAIN CANVAS ---
    col_chat, col_studio = st.columns([1, 1.4], gap="large")

    # === LEFT: CHAT INTERFACE ===
    with col_chat:
        st.markdown(f"### 💬 {selected_ws_title}")
        chat_box = st.container(height=650)
        
        with chat_box:
            if selected_ws_id:
                history = DB.get_chat(selected_ws_id)
                if not history: st.markdown("*System initialized. Awaiting commands.*")
                for msg in history:
                    with st.chat_message(msg["role"]):
                        if msg.get("image_data"):
                            try: st.image(base64_to_image(msg["image_data"]), width=300)
                            except: pass
                        st.write(msg["content"])
            else:
                st.info("Please select a workspace to initialize the neural link.")

        if p := st.chat_input("Command the system..."):
            if not selected_ws_id: st.error("No workspace selected."); st.stop()
            
            img_to_send = None; img_base64 = None
            if 'up_file' in locals() and up_file and up_file.type != "application/pdf":
                img_to_send = Image.open(up_file); img_base64 = image_to_base64(img_to_send)

            DB.save_chat(user.id, selected_ws_id, "user", p, img_base64)
            
            ctx = workspaces.to_string()
            try:
                docs = DB.get_docs(selected_ws_id)
                for d in docs: ctx += f"\nDOC: {d['content'][:15000]}"
            except: pass

            with st.spinner("Processing..."):
                reply = ask_agent(p, ctx, img_to_send)
            
            DB.save_chat(user.id, selected_ws_id, "assistant", reply)
            st.rerun()

    # === RIGHT: STUDIO INTERFACE ===
    with col_studio:
        st.markdown("### 🛠️ Studio")
        
        tab_cal, tab_mind, tab_sum = st.tabs(["Plan & Calendar", "Mind Map", "Summary"])

        # 1. CALENDAR & GRID
        with tab_cal:
            if not workspaces.empty:
                cal_events = []
                for i, row in workspaces.iterrows():
                    if pd.notnull(row['due_date']):
                        cal_events.append({
                            "title": row['title'], "start": str(row['due_date']), 
                            "allDay": True, "backgroundColor": "#3788d8"
                        })
                calendar(events=cal_events, options={"headerToolbar": {"left": "prev,next", "center": "title", "right": "dayGridMonth"}, "height": 350})
                
                st.divider()
                st.caption("Database View")
                edited = st.data_editor(workspaces, key="editor", hide_index=True, use_container_width=True)
            else: st.info("No active tasks.")

        # 2. MIND MAP
        with tab_mind:
            if st.button("Generate Knowledge Graph", use_container_width=True):
                if selected_ws_id:
                    with st.spinner("Visualizing Architecture..."):
                        try:
                            docs = DB.get_docs(selected_ws_id)
                            doc_ctx = "".join([d['content'][:10000] for d in docs])
                            if doc_ctx:
                                dot = generate_mindmap_code(selected_ws_title, doc_ctx)
                                if dot: st.graphviz_chart(dot)
                                else: st.error("Visualization Failed.")
                            else: st.warning("Upload source data first.")
                        except: st.error("Error reading data.")
                else: st.warning("Select Workspace.")

        # 3. SUMMARY
        with tab_sum:
            if st.button("Synthesize Intelligence", use_container_width=True):
                if selected_ws_id:
                    with st.spinner("Computing..."):
                        try:
                            docs = DB.get_docs(selected_ws_id)
                            doc_ctx = "".join([d['content'][:15000] for d in docs])
                            if doc_ctx:
                                summary = ask_agent("Provide an executive summary.", doc_ctx)
                                st.markdown(summary)
                            else: st.warning("No data sources.")
                        except: st.error("Error reading data.")

# ==============================================================================
# 7. BOOT SEQUENCE
# ==============================================================================
if st.session_state.user: main_view()
else: auth_view()
    
