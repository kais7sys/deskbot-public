import streamlit as st
import pandas as pd
import google.generativeai as genai
from PyPDF2 import PdfReader
from PIL import Image
import time
from supabase import create_client, Client

# --- 1. CONFIG & STATE ---
st.set_page_config(page_title="DeskBot: Pro Platform", page_icon="🚀", layout="wide")

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "username" not in st.session_state:
    st.session_state.username = None

# --- 2. DATABASE CONNECTION ---
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try:
    supabase = init_supabase()
except:
    st.error("⚠️ Supabase Keys missing in Secrets!")

# --- 3. AUTHENTICATION ---
def check_login(username, password):
    if username == "kais" and password == "deskbot123":
        return True
    elif username == "admin" and password == "admin":
        return True
    else:
        return False

def login_page():
    st.title("☁️ DeskBot Cloud Login")
    col1, col2 = st.columns([1, 2])
    with col1:
        with st.form("login_form"):
            user = st.text_input("Username")
            passw = st.text_input("Password", type="password")
            if st.form_submit_button("Log In"):
                if check_login(user, passw):
                    st.session_state.authenticated = True
                    st.session_state.username = user
                    st.success(f"Welcome, {user}!")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("Invalid credentials.")
    with col2:
        st.info("ℹ️ **Demo:** User: `kais` | Pass: `deskbot123`")

def logout():
    st.session_state.authenticated = False
    st.session_state.username = None
    st.rerun()

# --- 4. MAIN APP ---
def main_app():
    current_user = st.session_state.username

    # AI Setup
    if "GOOGLE_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        model = genai.GenerativeModel('gemini-2.0-flash')

    # --- DATABASE ACTIONS ---
    def get_tasks():
        # Get tasks sorted by ID
        response = supabase.table("tasks").select("*").eq("username", current_user).order("id").execute()
        df = pd.DataFrame(response.data)
        
        if not df.empty:
            # 🔧 CRITICAL FIX: Force correct data types
            # Convert ID and Minutes to Numbers (integers)
            df["id"] = df["id"].astype(int)
            df["est_minutes"] = df["est_minutes"].astype(int)
            
            # Convert Date string to actual Date Object
            df["due_date"] = pd.to_datetime(df["due_date"]).dt.date
            
            # Ensure Status is a string (handle potential empty values)
            df["status"] = df["status"].astype(str)
            
        return df

    def add_task(title, est, due):
        data = {"username": current_user, "title": title, "est_minutes": est, "due_date": str(due), "status": "todo"}
        supabase.table("tasks").insert(data).execute()

    def update_task_in_db(task_id, updates):
        # Updates Supabase with the changes
        supabase.table("tasks").update(updates).eq("id", task_id).execute()

    def delete_task_in_db(task_id):
        supabase.table("tasks").delete().eq("id", task_id).execute()

    # --- AI FUNCTIONS ---
    def ask_gemini(user_msg, task_context, file_data=None, file_type=None):
        try:
            sys_prompt = f"You are DeskBot. User Tasks:\n{task_context}\nUser Query: {user_msg}"
            content = [sys_prompt]
            if file_type == "image": content.append(file_data)
            elif file_type == "pdf": content.append(f"PDF CONTENT:\n{file_data}")
            return model.generate_content(content).text
        except Exception as e: return f"AI Error: {e}"

    def extract_pdf(file):
        try:
            reader = PdfReader(file)
            return "".join([p.extract_text() for p in reader.pages])
        except: return None

    # --- UI LAYOUT ---
    with st.sidebar:
        st.header(f"👤 {current_user}")
        if st.button("Log Out"): logout()
        st.divider()
        st.header("📂 Vision Upload")
        up_file = st.file_uploader("File", type=["pdf", "png", "jpg"])
        file_load = None
        file_type = None
        if up_file:
            if up_file.type == "application/pdf":
                file_type="pdf"; file_load=extract_pdf(up_file); st.success("PDF Ready")
            else:
                file_type="image"; file_load=Image.open(up_file); st.image(file_load, width=200); st.success("Image Ready")

    st.title("🚀 DeskBot Pro Platform")
    
    # We use tabs now to organize the view better
    tab1, tab2 = st.tabs(["📝 Task Grid", "💬 AI Assistant"])

    with tab1:
        # 1. ADD TASK BAR
        with st.expander("➕ Add New Task", expanded=False):
            with st.form("quick_add"):
                c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
                t_title = c1.text_input("Task Title")
                t_est = c2.number_input("Mins", 15, 120, 60)
                t_due = c3.date_input("Due")
                if c4.form_submit_button("Add"):
                    add_task(t_title, t_est, t_due)
                    st.success("Added!")
                    st.rerun()

        # 2. THE EDITABLE GRID (Jotform Style)
        df = get_tasks()
        
        if not df.empty:
            st.caption("Double-click any cell to edit. Changes save automatically.")
            
            # This is the Magic Component
            edited_df = st.data_editor(
                df,
                key="task_editor", # Vital for tracking changes
                num_rows="dynamic", # Allows adding/deleting rows (optional)
                column_config={
                    "id": st.column_config.NumberColumn(disabled=True), # Don't edit ID
                    "username": st.column_config.TextColumn(disabled=True), # Don't edit user
                    "created_at": st.column_config.TextColumn(disabled=True),
                    "status": st.column_config.SelectboxColumn(
                        "Status",
                        options=["todo", "in_progress", "done"],
                        required=True,
                    ),
                    "title": st.column_config.TextColumn("Task Name"),
                    "est_minutes": st.column_config.NumberColumn("Est. Mins"),
                    "due_date": st.column_config.DateColumn("Due Date"),
                },
                use_container_width=True,
                hide_index=True
            )

            # 3. SYNC LOGIC (Detect Changes and Push to Supabase)
            if st.session_state["task_editor"]["edited_rows"]:
                # Iterate through edits
                for index, updates in st.session_state["task_editor"]["edited_rows"].items():
                    # Get the real ID of the task from the original dataframe
                    task_id = df.iloc[index]["id"]
                    # Push updates to Supabase
                    update_task_in_db(int(task_id), updates)
                    st.toast(f"✅ Task {task_id} updated!")
                
                # Clear the edit state so we don't loop
                # (Optional: usually rerun handles this)
            
            # 4. DELETE LOGIC
            if st.session_state["task_editor"]["deleted_rows"]:
                for index in st.session_state["task_editor"]["deleted_rows"]:
                    task_id = df.iloc[index]["id"]
                    delete_task_in_db(int(task_id))
                    st.toast(f"🗑️ Task {task_id} deleted!")
                st.rerun()

        else:
            st.info("No tasks found. Add one above!")

    with tab2:
        # AI Chat Section
        if "messages" not in st.session_state: st.session_state.messages = []
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
            
        if prompt := st.chat_input("Ask DeskBot..."):
            with st.chat_message("user"): st.markdown(prompt)
            st.session_state.messages.append({"role": "user", "content": prompt})
            
            # Prepare context
            task_txt = df.to_string() if not df.empty else "No tasks."
            with st.chat_message("assistant"):
                with st.spinner("Processing..."):
                    reply = ask_gemini(prompt, task_txt, file_load, file_type)
                    st.markdown(reply)
            st.session_state.messages.append({"role": "assistant", "content": reply})

if st.session_state.authenticated:
    main_app()
else:
    login_page()

