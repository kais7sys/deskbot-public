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
from typing import Optional, List, Dict, Any
import graphviz

# ==============================================================================
# 1. CONFIGURATION & STYLING
# ==============================================================================
st.set_page_config(page_title="DeskBot // Professional", page_icon="⚡", layout="wide")

# Professional, Dark Theme CSS inspired by the reference image
STYLING_CSS = """
<style>
    /* Main container and background */
    .stApp {
        background-color: #191919; /* Deep dark background */
        color: #e0e0e0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }

    /* Sidebar styling */
    section[data-testid="stSidebar"] {
        background-color: #202020; /* Slightly lighter sidebar */
        border-right: 1px solid #333;
    }
    
    /* Hide default Streamlit elements */
    header[data-testid="stHeader"], footer {display: none;}
    #MainMenu {visibility: hidden;}

    /* Custom Button Styling */
    .stButton button {
        background-color: #2b2b2b;
        color: #e0e0e0;
        border: 1px solid #444;
        border-radius: 6px;
        padding: 0.5rem 1rem;
        font-weight: 500;
        transition: all 0.2s;
    }
    .stButton button:hover {
        border-color: #666;
        background-color: #333;
    }
    /* Primary button action */
    div[data-testid="stVerticalBlock"] > div > div > div > div > .stButton > button {
         background-color: #0080ff; border: none; color: white;
    }
    div[data-testid="stVerticalBlock"] > div > div > div > div > .stButton > button:hover {
         background-color: #006bd6;
    }

    /* Input field styling */
    .stTextInput input, .stSelectbox div[data-baseweb="select"] > div, .stTextArea textarea {
        background-color: #2b2b2b !important;
        color: #e0e0e0 !important;
        border: 1px solid #444 !important;
        border-radius: 6px;
    }

    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        border-bottom: 1px solid #333;
        padding-bottom: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 32px;
        font-size: 14px;
        color: #888;
        background-color: transparent;
        border: none;
        padding: 0 12px;
    }
    .stTabs [aria-selected="true"] {
        color: #fff !important;
        background-color: #2b2b2b !important;
        border-radius: 4px;
    }

    /* Chat message styling - Minimalist */
    .stChatMessage {
        background-color: transparent;
        border: none;
        padding: 0.5rem 0;
    }
    [data-testid="stChatMessageAvatarUser"], [data-testid="stChatMessageAvatarAssistant"] {
        display: none;
    }
    .stChatMessageContent {
        background-color: #2b2b2b;
        border-radius: 8px;
        padding: 1rem;
        border: 1px solid #333;
        color: #e0e0e0;
    }

    /* Modal-like overlay for Settings */
    .settings-modal {
        position: fixed;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        background-color: #202020;
        border: 1px solid #444;
        border-radius: 12px;
        padding: 2rem;
        z-index: 1001;
        width: 500px;
        box-shadow: 0 10px 25px rgba(0,0,0,0.5);
    }
    .modal-backdrop {
        position: fixed; top: 0; left: 0; width: 100%; height: 100%;
        background-color: rgba(0,0,0,0.6); z-index: 1000;
    }
</style>
"""
st.markdown(STYLING_CSS, unsafe_allow_html=True)

# ==============================================================================
# 2. SESSION STATE MANAGEMENT
# ==============================================================================
default_states = {
    "user": None,
    "active_workspace_id": None,
    "show_create_modal": False,
    "show_settings_modal": False,
    "chat_session": None,
}
for key, value in default_states.items():
    if key not in st.session_state:
        st.session_state[key] = value

# ==============================================================================
# 3. DATABASE & API CLIENTS (Singleton Pattern)
# ==============================================================================
@st.cache_resource
def get_supabase_client() -> Client:
    """Initializes and caches the Supabase client."""
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except KeyError:
        st.error("🚨 Critical Error: Supabase credentials not found in secrets.toml")
        st.stop()

supabase = get_supabase_client()

if "GOOGLE_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
else:
    st.error("🚨 Critical Error: Google AI API key not found.")
    st.stop()

# ==============================================================================
# 4. HELPER FUNCTIONS (Utilities)
# ==============================================================================
def image_to_base64(image: Image.Image) -> str:
    """Converts a PIL Image to a base64 encoded string."""
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

def base64_to_image(base64_str: str) -> Optional[Image.Image]:
    """Converts a base64 string back to a PIL Image."""
    try:
        return Image.open(BytesIO(base64.b64decode(base64_str)))
    except Exception:
        return None

def extract_pdf_text(file: BytesIO) -> Optional[str]:
    """Extracts text content from a PDF file."""
    try:
        reader = PdfReader(file)
        return "".join([page.extract_text() for page in reader.pages])
    except Exception as e:
        st.error(f"Failed to process PDF: {e}")
        return None

# ==============================================================================
# 5. DATA ACCESS LAYER (Database Operations)
# ==============================================================================
class db:
    """Static class to group database interaction methods."""
    
    @staticmethod
    def get_workspaces(user_id: str) -> pd.DataFrame:
        """Fetches all workspaces for a given user."""
        try:
            res = supabase.table("workspaces").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
            return pd.DataFrame(res.data)
        except Exception as e:
            st.error(f"DB Error (get_workspaces): {e}")
            return pd.DataFrame()

    @staticmethod
    def create_workspace(user_id: str, title: str) -> Optional[int]:
        """Creates a new workspace and returns its ID."""
        try:
            res = supabase.table("workspaces").insert({"user_id": user_id, "title": title}).execute()
            return res.data[0]['id']
        except Exception as e:
            st.error(f"DB Error (create_workspace): {e}")
            return None

    @staticmethod
    def get_tasks(workspace_id: int) -> pd.DataFrame:
        """Fetches tasks for a specific workspace."""
        try:
            res = supabase.table("tasks").select("*").eq("workspace_id", workspace_id).order("due_date", nullsfirst=True).execute()
            df = pd.DataFrame(res.data)
            if not df.empty:
                df["due_date"] = pd.to_datetime(df["due_date"]).dt.date
            return df
        except Exception as e:
            # st.error(f"DB Error (get_tasks): {e}") # Fail silently for UI cleanliness
            return pd.DataFrame()

    @staticmethod
    def create_task(workspace_id: int, title: str, due_date: Optional[date] = None) -> bool:
        """Creates a new task in a workspace."""
        try:
            data = {"workspace_id": workspace_id, "title": title}
            if due_date: data["due_date"] = due_date.isoformat()
            supabase.table("tasks").insert(data).execute()
            return True
        except Exception as e:
            st.error(f"DB Error (create_task): {e}")
            return False
            
    @staticmethod
    def update_task(task_id: int, updates: Dict[str, Any]) -> None:
        """Updates a task's status or details."""
        try: supabase.table("tasks").update(updates).eq("id", task_id).execute()
        except Exception: pass

    @staticmethod
    def delete_task(task_id: int) -> None:
        """Deletes a task."""
        try: supabase.table("tasks").delete().eq("id", task_id).execute()
        except Exception: pass

    @staticmethod
    def save_document(workspace_id: int, filename: str, content: str) -> bool:
        """Saves a document's text content."""
        try:
            supabase.table("documents").insert({
                "workspace_id": workspace_id, "filename": filename, "content": content
            }).execute()
            return True
        except Exception as e:
            st.error(f"DB Error (save_document): {e}")
            return False

    @staticmethod
    def get_documents(workspace_id: int) -> List[Dict[str, Any]]:
        """Fetches metadata for documents in a workspace."""
        try:
            res = supabase.table("documents").select("id, filename").eq("workspace_id", workspace_id).execute()
            return res.data
        except Exception: return []
    
    @staticmethod
    def get_document_content(workspace_id: int) -> str:
        """Fetches concatenated content of all docs in a workspace for AI context."""
        try:
            res = supabase.table("documents").select("content").eq("workspace_id", workspace_id).execute()
            return "\n".join([d['content'] for d in res.data])
        except Exception: return ""

    @staticmethod
    def save_chat_message(workspace_id: int, role: str, content: str, image_data: Optional[str] = None) -> None:
        """Saves a chat message, optionally with base64 image data."""
        try:
            data = {"workspace_id": workspace_id, "role": role, "content": content}
            if image_data: data["image_data"] = image_data
            supabase.table("chat_messages").insert(data).execute()
        except Exception as e:
            st.error(f"DB Error (save_message): {e}")

    @staticmethod
    def get_chat_history(workspace_id: int) -> List[Dict[str, Any]]:
        """Fetches chat history for a workspace."""
        try:
            res = supabase.table("chat_messages").select("*").eq("workspace_id", workspace_id).order("created_at").execute()
            return res.data
        except Exception: return []

# ==============================================================================
# 6. AI & AGENT LOGIC
# ==============================================================================
class Agent:
    """Handles interaction with the AI model and tool execution."""
    
    def __init__(self):
        # Define tools the AI can use
        self.tools = [self.add_task_tool]
        self.model = genai.GenerativeModel('gemini-2.0-flash-exp', tools=self.tools)

    def add_task_tool(self, task_title: str, due_date_str: str = None):
        """Adds a task to the user's list. due_date_str must be YYYY-MM-DD format."""
        ws_id = st.session_state.active_workspace_id
        if not ws_id: return "Error: No active workspace."
        
        d_date = None
        if due_date_str:
            try: d_date = date.fromisoformat(due_date_str)
            except ValueError: pass # Let it be None if format is wrong

        if db.create_task(ws_id, task_title, d_date):
            return f"✅ Added task: '{task_title}'" + (f" due on {d_date}" if d_date else "")
        return "❌ Failed to add task. Database error."

    def generate_response(self, user_msg: str, context: str, image: Optional[Image.Image] = None) -> str:
        """Generates a response from the AI, handling context and images."""
        if not st.session_state.chat_session:
            st.session_state.chat_session = self.model.start_chat(enable_automatic_function_calling=True)
            
        today = date.today().isoformat()
        system_prompt = f"""
        SYSTEM: You are a professional AI assistant embedded in a productivity workspace.
        Today's date is {today}.
        Your goal is to help the user manage their tasks and understand their documents.
        When asked to add a task, ALWAYS use the 'add_task_tool'. extract a due date if possible.
        
        WORKSPACE CONTEXT:
        {context}
        """
        
        prompt_parts = [system_prompt, f"USER: {user_msg}"]
        if image: prompt_parts.append(image)
        
        try:
            response = st.session_state.chat_session.send_message(prompt_parts)
            return response.text
        except Exception as e:
            return f"⚠️ AI Error: {str(e)}"

    def generate_mindmap_code(self, topic: str, context: str) -> Optional[str]:
        """Generates Graphviz DOT code for a mind map."""
        prompt = f"""
        Based on the following context, create a professional mind map about '{topic}'.
        Context: {context[:3000]}...
        Output ONLY valid Graphviz DOT code inside a ```dot code block.
        Use a clean, professional, left-to-right layout (rankdir=LR).
        """
        try:
            resp = self.model.generate_content(prompt)
            match = re.search(r'```dot\n(.*?)\n```', resp.text, re.DOTALL)
            return match.group(1) if match else None
        except Exception: return None

agent = Agent()

# ==============================================================================
# 7. AUTHENTICATION VIEWS
# ==============================================================================
def auth_view():
    """Renders the login/signup page."""
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.title("⚡ DeskBot // Professional")
        st.write("Sign in to access your persistent workspace.")
        
        tabs = st.tabs(["Login", "Create Account"])
        with tabs[0]:
            with st.form("login_form"):
                email = st.text_input("Email")
                password = st.text_input("Password", type="password")
                if st.form_submit_button("Sign In", use_container_width=True):
                    try:
                        res = supabase.auth.sign_in_with_password({"email": email, "password": password})
                        st.session_state.user = res.user
                        st.rerun()
                    except Exception as e:
                        st.error(f"Login failed: {e}")
        with tabs[1]:
            with st.form("signup_form"):
                email = st.text_input("Email")
                password = st.text_input("Password (min 6 chars)", type="password")
                if st.form_submit_button("Sign Up", use_container_width=True):
                    try:
                        res = supabase.auth.sign_up({"email": email, "password": password})
                        st.session_state.user = res.user
                        st.success("Account created! Logging in...", icon="✅")
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Signup failed: {e}")

def logout():
    """Logs the user out and clears session state."""
    supabase.auth.sign_out()
    for key in default_states:
        st.session_state[key] = default_states[key]
    st.rerun()

# ==============================================================================
# 8. MAIN APPLICATION VIEW
# ==============================================================================
def main_view():
    """Renders the main workspace UI."""
    user = st.session_state.user
    workspaces_df = db.get_workspaces(user.id)
    active_ws_id = st.session_state.active_workspace_id
    active_ws_title = "No Workspace Selected"
    
    # Determine active workspace title
    if active_ws_id and not workspaces_df.empty:
        title_row = workspaces_df[workspaces_df['id'] == active_ws_id]['title']
        if not title_row.empty:
            active_ws_title = title_row.iloc[0]

    # --- SIDEBAR NAVIGATION ---
    with st.sidebar:
        st.caption(f"👤 {user.email}")
        st.divider()

        st.subheader("Workspaces")
        # New Workspace Button & Modal Logic
        if st.button("➕ New Workspace", use_container_width=True):
            st.session_state.show_create_modal = True

        if st.session_state.show_create_modal:
            with st.expander("Create New Workspace", expanded=True):
                with st.form("new_ws_form"):
                    title = st.text_input("Workspace Title")
                    col_c1, col_c2 = st.columns(2)
                    if col_c1.form_submit_button("Create", type="primary"):
                        if title:
                            new_id = db.create_workspace(user.id, title)
                            if new_id:
                                st.session_state.active_workspace_id = new_id
                                st.session_state.show_create_modal = False
                                st.rerun()
                    if col_c2.form_submit_button("Cancel"):
                         st.session_state.show_create_modal = False
                         st.rerun()

        # Workspace List/Selector
        if not workspaces_df.empty:
            ws_options = {row['id']: row['title'] for _, row in workspaces_df.iterrows()}
            selected_ws_id = st.radio(
                "Select Workspace",
                options=list(ws_options.keys()),
                format_func=lambda x: ws_options[x],
                index=list(ws_options.keys()).index(active_ws_id) if active_ws_id in ws_options else 0,
                label_visibility="collapsed"
            )
            if selected_ws_id != active_ws_id:
                st.session_state.active_workspace_id = selected_ws_id
                st.rerun()
        else:
            st.info("Create a workspace to get started.")

        st.divider()
        
        # Bottom Actions
        if st.button("⚙️ Settings", use_container_width=True):
            st.session_state.show_settings_modal = True
        
        st.button("Log Out", on_click=logout, use_container_width=True)

    # --- MAIN CONTENT AREA ---
    # Settings Modal Overlay
    if st.session_state.show_settings_modal:
        st.markdown("""<div class="modal-backdrop"></div>""", unsafe_allow_html=True)
        with st.container():
            st.markdown("""<div class="settings-modal">""", unsafe_allow_html=True)
            st.subheader("Settings")
            st.tabs(["Account", "Appearance"])
            st.info("Settings functionality coming soon.")
            if st.button("Close"):
                st.session_state.show_settings_modal = False
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

    # Split Layout: Chat (Left) vs. Studio (Right)
    chat_col, studio_col = st.columns([2, 3])

    # --- CHAT COLUMN ---
    with chat_col:
        st.subheader(f"💬 {active_ws_title}")
        
        # File Uploader for Vision/Docs
        with st.expander("Upload File to Workspace", expanded=False):
            uploaded_file = st.file_uploader("Drop PDF or Image", type=["pdf", "png", "jpg", "jpeg"])
            if uploaded_file and active_ws_id:
                if uploaded_file.type == "application/pdf":
                    if st.button("Process PDF"):
                        with st.spinner("Extracting text..."):
                            text = extract_pdf_text(uploaded_file)
                            if text and db.save_document(active_ws_id, uploaded_file.name, text):
                                st.success("PDF saved to workspace!")
                                time.sleep(0.5); st.rerun()
                else:
                    st.image(uploaded_file, caption="Ready to send", width=200)

        # Chat History Container
        chat_container = st.container(height=500)
        with chat_container:
            if active_ws_id:
                history = db.get_chat_history(active_ws_id)
                if not history:
                    st.caption("No messages yet. Start the conversation!")
                for msg in history:
                    with st.chat_message(msg["role"]):
                        if msg.get("image_data"):
                            img = base64_to_image(msg["image_data"])
                            if img: st.image(img, width=300)
                        st.write(msg["content"])
            else:
                st.warning("Please select or create a workspace.")

        # Chat Input
        if prompt := st.chat_input("Ask anything...", disabled=not active_ws_id):
            if active_ws_id:
                img_data = None
                pil_img = None
                # Check for image in uploader
                if uploaded_file and uploaded_file.type != "application/pdf":
                    pil_img = Image.open(uploaded_file)
                    img_data = image_to_base64(pil_img)

                # Save User Message
                db.save_chat_message(active_ws_id, "user", prompt, img_data)
                
                # Prepare Context
                tasks_df = db.get_tasks(active_ws_id)
                task_ctx = tasks_df.to_string() if not tasks_df.empty else "No tasks."
                doc_ctx = db.get_document_content(active_ws_id)
                full_ctx = f"TASKS:\n{task_ctx}\n\nDOCUMENTS:\n{doc_ctx[:20000]}" # Limit context size

                # Generate and Save AI Response
                with st.spinner("Thinking..."):
                    response = agent.generate_response(prompt, full_ctx, pil_img)
                    db.save_chat_message(active_ws_id, "assistant", response)
                st.rerun()

    # --- STUDIO COLUMN ---
    with studio_col:
        st.subheader("🛠️ Studio")
        tabs = st.tabs(["Schedule", "Mind Map", "Summary"])

        # Tab 1: Schedule (Calendar + Tasks)
        with tabs[0]:
            if active_ws_id:
                tasks_df = db.get_tasks(active_ws_id)
                
                # 1. Calendar View
                calendar_events = []
                if not tasks_df.empty:
                    for _, row in tasks_df.iterrows():
                        if row["due_date"]:
                            calendar_events.append({
                                "title": row["title"],
                                "start": row["due_date"].isoformat(),
                                "backgroundColor": "#4CAF50" if row["status"] == "done" else "#2196F3",
                                "borderColor": "#4CAF50" if row["status"] == "done" else "#2196F3",
                            })
                
                calendar(
                    events=calendar_events,
                    options={
                        "headerToolbar": {"left": "prev,next today", "center": "title", "right": "dayGridMonth,timeGridWeek"},
                        "initialView": "dayGridMonth",
                        "height": 400,
                    },
                    key=f"cal_{active_ws_id}" # Unique key to force refresh
                )

                st.divider()
                st.write("**Upcoming Tasks**")
                
                # 2. Editable Task List
                if not tasks_df.empty:
                    edited_df = st.data_editor(
                        tasks_df,
                        key=f"editor_{active_ws_id}",
                        hide_index=True,
                        use_container_width=True,
                        column_config={
                            "id": None, "workspace_id": None, "created_at": None,
                            "title": st.column_config.TextColumn("Task Title", required=True),
                            "status": st.column_config.SelectboxColumn("Status", options=["todo", "done"], required=True),
                            "due_date": st.column_config.DateColumn("Due Date")
                        },
                        num_rows="dynamic" # Allow adding/deleting rows
                    )

                    # Handle Data Editor Changes
                    editor_state = st.session_state[f"editor_{active_ws_id}"]
                    
                    # Edits
                    for idx, updates in editor_state["edited_rows"].items():
                        task_id = tasks_df.iloc[idx]["id"]
                        # Convert date objects back to ISO string for DB
                        if "due_date" in updates and updates["due_date"]:
                            updates["due_date"] = updates["due_date"].isoformat()
                        db.update_task(task_id, updates)
                        
                    # Deletions
                    for idx in editor_state["deleted_rows"]:
                        db.delete_task(tasks_df.iloc[idx]["id"])
                        
                    # Additions
                    for new_row in editor_state["added_rows"]:
                        if new_row.get("title"):
                            d_date = new_row.get("due_date")
                            if d_date: d_date = date.fromisoformat(d_date)
                            db.create_task(active_ws_id, new_row["title"], d_date)

                    if any(editor_state.values()):
                        st.rerun()
                else:
                    st.info("No tasks yet. Add one via chat or the table below.")
                    # Empty editor to allow adding first task
                    st.data_editor(
                        pd.DataFrame(columns=["title", "status", "due_date"]),
                        key=f"new_editor_{active_ws_id}", hide_index=True, num_rows="dynamic"
                    )
            else:
                st.info("Select a workspace to view schedule.")

        # Tab 2: Mind Map
        with tabs[1]:
            if active_ws_id:
                if st.button("Generate Mind Map from Workspace Docs"):
                    doc_content = db.get_document_content(active_ws_id)
                    if doc_content:
                        with st.spinner("Analyzing & Visualizing..."):
                            dot_code = agent.generate_mindmap_code(active_ws_title, doc_content)
                            if dot_code:
                                st.graphviz_chart(dot_code)
                            else:
                                st.error("Could not generate mind map structure.")
                    else:
                        st.warning("No documents found in this workspace to analyze.")
            else:
                st.info("Select a workspace.")

        # Tab 3: Summary
        with tabs[2]:
             if active_ws_id:
                if st.button("Summarize Workspace Content"):
                    doc_content = db.get_document_content(active_ws_id)
                    if doc_content:
                        with st.spinner("Synthesizing..."):
                            summary = agent.model.generate_content(f"Please provide a concise, structured summary of the following information:\n{doc_content[:10000]}").text
                            st.markdown(summary)
                    else:
                        st.warning("No documents to summarize.")
             else:
                st.info("Select a workspace.")

# ==============================================================================
# 9. MAIN EXECUTION FLOW
# ==============================================================================
if __name__ == "__main__":
    if st.session_state.user:
        main_view()
    else:
        auth_view()
