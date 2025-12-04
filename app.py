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

# =========================================================
# 1. CONFIGURATION: THE "DARK MODE" STUDIO AESTHETIC
# =========================================================
st.set_page_config(page_title="DeskBot // Studio", page_icon="⚡", layout="wide")

# Injecting Industrial-Grade CSS to force the Notion Look
st.markdown("""
<style>
    /* 1. The Canvas - Deep Dark Gray */
    .stApp {background-color: #191919; color: #e0e0e0; font-family: 'Inter', sans-serif;}
    
    /* 2. Sidebar - Slightly Lighter for Contrast */
    [data-testid="stSidebar"] {background-color: #202020; border-right: 1px solid #333;}
    
    /* 3. Input Fields - Minimalist */
    .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] {
        background-color: #2b2b2b !important; 
        color: white !important;
        border: 1px solid #404040 !important; 
        border-radius: 6px;
    }
    
    /* 4. Remove Streamlit Branding */
    header, #MainMenu, footer {visibility: hidden;}
    
    /* 5. Custom Tabs (Notion Style) */
    .stTabs [data-baseweb="tab-list"] {gap: 8px; border-bottom: 1px solid #333;}
    .stTabs [data-baseweb="tab"] {height: 35px; font-size: 13px; color: #888; background-color: transparent; border: none;}
    .stTabs [aria-selected="true"] {color: #fff !important; border-bottom: 1px solid #fff;}

    /* 6. Chat Bubbles - Invisible Containers */
    .stChatMessage {background-color: transparent; border: none; padding: 5px 0px;}
    [data-testid="stChatMessageAvatarUser"], [data-testid="stChatMessageAvatarAssistant"] {display: none;}
    
    /* 7. Studio Panel Container */
    div.css-1r6slb0 {border: 1px solid #333; border-radius: 8px; padding: 15px;}
    
    /* 8. Buttons - Subtle */
    .stButton button {border: 1px solid #444; color: #ccc; background-color: #2b2b2b;}
    .stButton button:hover {border-color: #666; color: #fff;}
</style>
""", unsafe_allow_html=True)

# State Initialization (Prevent Crashes)
if "user" not in st.session_state: st.session_state.user = None
if "active_notebook_id" not in st.session_state: st.session_state.active_notebook_id = None
if "show_create_modal" not in st.session_state: st.session_state.show_create_modal = False
if "chat_session" not in st.session_state: st.session_state.chat_session = None
if "settings_open" not in st.session_state: st.session_state.settings_open = False

# =========================================================
# 2. CORE SYSTEMS: DATABASE & I/O
# =========================================================
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try: supabase = init_supabase()
except: st.error("⚠️ SYSTEM FAILURE: Database connection refused. Check Secrets.")

# --- Image Handling Engines ---
def image_to_base64(image):
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

def base64_to_image(base64_str):
    try: return Image.open(BytesIO(base64.b64decode(base64_str)))
    except: return None

# =========================================================
# 3. AUTHENTICATION MODULE
# =========================================================
def login_page():
    c1, c2, c3 = st.columns([1,2,1])
    with c2:
        st.title("⚡ DeskBot // Access")
        st.markdown("### Initialize Workspace")
        
        tab_login, tab_signup = st.tabs(["Login", "Register"])
        
        with tab_login:
            with st.form("login"):
                e = st.text_input("Email"); p = st.text_input("Password", type="password")
                if st.form_submit_button("Enter System", use_container_width=True):
                    try:
                        res = supabase.auth.sign_in_with_password({"email":e,"password":p})
                        st.session_state.user = res.user
                        st.rerun()
                    except Exception as err: st.error(f"Access Denied: {err}")
                    
        with tab_signup:
            with st.form("signup"):
                e = st.text_input("Email"); p = st.text_input("Pass (min 6)", type="password")
                if st.form_submit_button("Create Identity", use_container_width=True):
                    try:
                        res = supabase.auth.sign_up({"email":e,"password":p})
                        st.session_state.user = res.user
                        st.success("Identity Created. Proceed to Login."); time.sleep(2); st.rerun()
                    except Exception as err: st.error(f"Creation Failed: {err}")

def logout():
    supabase.auth.sign_out()
    st.session_state.user = None
    st.session_state.active_notebook_id = None
    st.rerun()

# =========================================================
# 4. MAIN APPLICATION LOGIC
# =========================================================
def main_app():
    user_id = st.session_state.user.id
    email = st.session_state.user.email

    # --- Database Tools ---
    def get_notebooks():
        try:
            res = supabase.table("tasks").select("*").eq("user_id", user_id).order("id", desc=True).execute()
            df = pd.DataFrame(res.data)
            if not df.empty:
                df["id"] = pd.to_numeric(df["id"]).astype(int)
                df["due_date"] = pd.to_datetime(df["due_date"], errors='coerce').dt.date
                df = df.dropna(subset=['due_date'])
            return df
        except: return pd.DataFrame()

    def create_notebook(title):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            data = supabase.table("tasks").insert({
                "user_id": user_id, "title": title, "est_minutes": 0, "due_date": today
            }).execute()
            return data.data[0]['id']
        except: return None

    # --- AI Tools ---
    def add_task_to_scheduler(task_title: str, duration_minutes: int, due_date: str):
        """Adds a task. duration_minutes must be INT. due_date YYYY-MM-DD."""
        try:
            if not isinstance(duration_minutes, int): duration_minutes = 60
            supabase.table("tasks").insert({
                "user_id": user_id, "title": task_title, "est_minutes": duration_minutes, "due_date": due_date
            }).execute()
            return f"✅ Scheduled: '{task_title}' on {due_date}"
        except Exception as e: return f"❌ Error: {e}"

    def update_task(tid, updates):
        try: supabase.table("tasks").update(updates).eq("id", tid).execute()
        except: pass

    def delete_task(tid):
        try: supabase.table("tasks").delete().eq("id", tid).execute()
        except: pass

    # --- Document & Vision Handling ---
    def save_document(filename, content, task_id):
        try:
            supabase.table("documents").insert({"user_id": user_id, "filename": filename, "content": content, "task_id": int(task_id)}).execute()
        except: pass

    def get_task_documents(task_id):
        try:
            res = supabase.table("documents").select("*").eq("task_id", task_id).execute()
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

    def generate_mindmap(topic, context):
        try:
            prompt = f"Context: {context[:5000]}\nGenerate Graphviz DOT code for a mindmap on '{topic}'. Only output DOT code inside ```dot``` blocks."
            response = model.generate_content(prompt)
            match = re.search(r'```dot\n(.*?)\n```', response.text, re.DOTALL)
            return match.group(1) if match else None
        except: return None

    # --- Initialize Gemini 2.0 ---
    if "GOOGLE_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        model = genai.GenerativeModel('gemini-2.0-flash', tools=[add_task_to_scheduler])
        
        # Ensure Chat Session is fresh and persistent
        if "chat_session" not in st.session_state or st.session_state.chat_session is None:
            st.session_state.chat_session = model.start_chat(enable_automatic_function_calling=True)

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

    # --- Chat History (With Image Persistence) ---
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

    # =========================================================
    # 5. UI CONSTRUCTION
    # =========================================================
    notebooks_df = get_notebooks()

    # --- LEFT NAVIGATION BAR ---
    with st.sidebar:
        st.write(f"**{email}**")
        st.divider()
        
        # 1. Workspace Navigation
        st.caption("NOTEBOOKS")
        
        # Create New Button
        if st.button("➕ New Notebook", use_container_width=True):
            st.session_state.show_create_modal = True
        
        # Modal Logic
        if st.session_state.show_create_modal:
            with st.form("new_nb"):
                title = st.text_input("Notebook Name")
                if st.form_submit_button("Create"):
                    if title:
                        new_id = create_notebook(title)
                        st.session_state.active_notebook_id = new_id
                        st.session_state.show_create_modal = False
                        st.rerun()

        # Selection Dropdown
        selected_task_id = None
        selected_task_title = "General"
        
        if not notebooks_df.empty:
            options = ["General"] + notebooks_df['title'].tolist()
            ids = [None] + notebooks_df['id'].tolist()
            
            # Logic to maintain selection state
            curr = st.session_state.active_notebook_id
            idx = ids.index(curr) if curr in ids else 0
            
            choice = st.selectbox("Select", options, index=idx, label_visibility="collapsed")
            
            if choice != "General":
                selected_task_id = ids[options.index(choice)]
                selected_task_title = choice
                st.session_state.active_notebook_id = selected_task_id
            else:
                st.session_state.active_notebook_id = None

        st.divider()
        
        # 2. Data Sources
        st.caption("SOURCES")
        if selected_task_id:
            docs = get_task_documents(selected_task_id)
            if docs:
                for d in docs:
                    c1, c2 = st.columns([5,1])
                    c1.text(f"📄 {d['filename'][:15]}...")
                    if c2.button("x", key=f"d{d['id']}"): delete_document(d['id']); st.rerun()
            else: st.caption("No sources attached.")
            
            with st.expander("Add Source", expanded=False):
                up_file = st.file_uploader("Upload", type=["pdf", "png", "jpg"], label_visibility="collapsed")
                if up_file:
                    if up_file.type == "application/pdf":
                        if st.button("Save PDF"):
                            txt = extract_pdf(up_file)
                            if txt: save_document(up_file.name, txt, selected_task_id); st.success("Saved"); st.rerun()
                    else: st.image(Image.open(up_file), width=100)
        else:
            st.caption("Select notebook to add sources.")

        st.divider()
        
        # 3. Settings & Logout
        if st.button("⚙️ Settings"):
            st.session_state.settings_open = not st.session_state.settings_open
        
        if st.session_state.settings_open:
            st.info("Settings Module: User Profile, API Config, Export Data (Coming Soon)")
            
        if st.button("Log Out"): logout()

    # --- MAIN INTERFACE: SPLIT LAYOUT ---
    col_chat, col_studio = st.columns([1, 1.3], gap="medium")

    # === LEFT COLUMN: CHAT INTERFACE ===
    with col_chat:
        st.subheader(f"💬 {selected_task_title}")
        chat_area = st.container(height=650)
        
        with chat_area:
            history = get_chat_history(selected_task_id)
            if not history: st.caption("System ready.")
            for msg in history:
                with st.chat_message(msg["role"]):
                    if msg.get("image_data"):
                        try: st.image(base64_to_image(msg["image_data"]), width=300)
                        except: pass
                    st.write(msg["content"])

        if p := st.chat_input("Command..."):
            img_to_send = None; img_base64 = None
            if 'up_file' in locals() and up_file and up_file.type != "application/pdf":
                img_to_send = Image.open(up_file); img_base64 = image_to_base64(img_to_send)

            save_chat_message("user", p, selected_task_id, img_base64)
            
            ctx = notebooks_df.to_string() if not notebooks_df.empty else "No tasks."
            if selected_task_id:
                docs = get_task_documents(selected_task_id)
                for d in docs: ctx += f"\nFILE: {d['filename']}\nCONTENT: {d['content'][:15000]}"

            with st.spinner("Processing..."):
                reply = ask_agent(p, ctx, img_to_send)
            
            save_chat_message("assistant", reply, selected_task_id)
            st.rerun()

    # === RIGHT COLUMN: STUDIO INTELLIGENCE ===
    with col_studio:
        st.subheader("🛠️ Studio")
        
        tab_cal, tab_mind, tab_sum = st.tabs(["Plan & Calendar", "Mind Map", "Summary"])

        # 1. Calendar & Grid
        with tab_cal:
            if not notebooks_df.empty:
                cal_events = []
                for i, row in notebooks_df.iterrows():
                    if pd.notnull(row['due_date']):
                        cal_events.append({"title": row['title'], "start": str(row['due_date']), "allDay": True, "backgroundColor": "#3788d8"})
                
                calendar(events=cal_events, options={"headerToolbar": {"left": "prev,next", "center": "title", "right": "dayGridMonth"}, "initialView": "dayGridMonth", "height": 400})
                
                st.divider()
                st.caption("Database View")
                edited = st.data_editor(
                    notebooks_df, key="editor", hide_index=True, use_container_width=True,
                    column_config={
                        "id": None, "user_id": None, "est_minutes": None,
                        "title": st.column_config.TextColumn("Task"),
                        "due_date": st.column_config.DateColumn("Due"),
                        "status": st.column_config.SelectboxColumn("Status", options=["todo", "done"])
                    }
                )
                if st.session_state["editor"]["edited_rows"]:
                    for idx, updates in st.session_state["editor"]["edited_rows"].items():
                        update_task(notebooks_df.iloc[idx]["id"], updates)
                    st.rerun()
                if st.session_state["editor"]["deleted_rows"]:
                    for idx in st.session_state["editor"]["deleted_rows"]:
                        delete_task(notebooks_df.iloc[idx]["id"])
                    st.rerun()
            else:
                st.info("No tasks in the system.")

        # 2. Mind Map
        with tab_mind:
            if st.button("Generate Graph"):
                if selected_task_id:
                    with st.spinner("Analyzing..."):
                        doc_ctx = ""
                        docs = get_task_documents(selected_task_id)
                        for d in docs: doc_ctx += d['content'][:10000]
                        if doc_ctx:
                            dot = generate_mindmap(selected_task_title, doc_ctx)
                            if dot: st.graphviz_chart(dot)
                            else: st.error("Failed to visualize.")
                        else: st.warning("Upload PDF source first.")
                else: st.warning("Select Notebook.")

        # 3. Summary
        with tab_sum:
            if st.button("Synthesize Data"):
                if selected_task_id:
                    with st.spinner("Computing..."):
                        doc_ctx = ""
                        docs = get_task_documents(selected_task_id)
                        for d in docs: doc_ctx += d['content'][:15000]
                        if doc_ctx:
                            summary = ask_agent("Provide an executive summary of attached documents.", doc_ctx)
                            st.markdown(summary)
                        else: st.warning("No data sources.")

# Boot Sequence
if st.session_state.user: main_app()
else: login_page()
