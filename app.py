import streamlit as st
import sqlite3
import pandas as pd
import google.generativeai as genai
from PyPDF2 import PdfReader
from PIL import Image
import time

# --- 1. CONFIG & STATE ---
st.set_page_config(page_title="DeskBot: Secure Workspace", page_icon="🔐", layout="wide")

# Initialize Session State for Login
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

# --- 2. AUTHENTICATION FUNCTIONS ---
def check_login(username, password):
    """Simple check. In real life, this checks a database."""
    # DEMO CREDENTIALS:
    if username == "kais" and password == "deskbot123":
        return True
    elif username == "admin" and password == "admin":
        return True
    else:
        return False

def login_page():
    """The Front Door"""
    st.title("🔐 Sign In to DeskBot")
    st.write("Welcome to your AI Productivity Workspace.")
    
    col1, col2 = st.columns([1, 2])
    with col1:
        with st.form("login_form"):
            user = st.text_input("Username")
            # type="password" hides the text with dots
            passw = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log In")
            
            if submitted:
                if check_login(user, passw):
                    st.session_state.authenticated = True
                    st.success("Access Granted!")
                    time.sleep(1)
                    st.rerun() # Reloads the app to show the Main App
                else:
                    st.error("Incorrect username or password")
    
    with col2:
        st.info("ℹ️ **Demo Access:**\n\nUsername: `kais`\nPassword: `deskbot123`")

def logout():
    st.session_state.authenticated = False
    st.rerun()

# --- 3. THE MAIN APP (Your Previous Code) ---
def main_app():
    # --- SETUP ---
    # Connect to Google Gemini
    if "GOOGLE_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        model = genai.GenerativeModel('gemini-2.0-flash')
    else:
        st.error("⚠️ Google API Key missing!")

    # Database Setup
    def init_db():
        conn = sqlite3.connect("deskbot.db")
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT,
                        est_minutes INTEGER,
                        due_date TEXT,
                        status TEXT DEFAULT 'todo'
                    )''')
        conn.commit()
        conn.close()
    init_db()

    # Helpers
    def get_tasks():
        conn = sqlite3.connect("deskbot.db")
        try:
            df = pd.read_sql("SELECT * FROM tasks", conn)
        except:
            df = pd.DataFrame()
        conn.close()
        return df

    def add_task(title, est, due):
        conn = sqlite3.connect("deskbot.db")
        c = conn.cursor()
        c.execute("INSERT INTO tasks (title, est_minutes, due_date) VALUES (?, ?, ?)", 
                (title, est, str(due)))
        conn.commit()
        conn.close()

    def delete_task(task_id):
        conn = sqlite3.connect("deskbot.db")
        c = conn.cursor()
        c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        conn.commit()
        conn.close()
    
    def extract_text_from_pdf(uploaded_file):
        try:
            pdf_reader = PdfReader(uploaded_file)
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() or ""
            return text
        except:
            return None

    def ask_gemini(user_message, task_list_text, file_data=None, file_type=None):
        try:
            system_instruction = f"""
            You are DeskBot.
            CONTEXT: User's Tasks: {task_list_text}
            USER MESSAGE: "{user_message}"
            INSTRUCTIONS: Analyze images or text provided. Help plan tasks.
            """
            content_package = [system_instruction]
            if file_type == "image":
                content_package.append(file_data)
            elif file_type == "pdf":
                content_package.append(f"DOCUMENT CONTENT:\n{file_data}")
            response = model.generate_content(content_package)
            return response.text
        except Exception as e:
            return f"⚠️ AI Error: {e}"

    # --- UI LAYOUT ---
    
    # Sidebar with LOGOUT
    with st.sidebar:
        st.header("👤 User: Kais")
        if st.button("Log Out"):
            logout()
            
        st.divider()
        st.header("📂 Upload")
        uploaded_file = st.file_uploader("Drop a File (PDF, PNG, JPG)", type=["pdf", "png", "jpg", "jpeg"])
        
        file_payload = None
        file_type = None

        if uploaded_file is not None:
            if uploaded_file.type == "application/pdf":
                file_type = "pdf"
                with st.spinner("Reading PDF..."):
                    file_payload = extract_text_from_pdf(uploaded_file)
                st.success("PDF Loaded!")
            else:
                file_type = "image"
                file_payload = Image.open(uploaded_file)
                st.image(file_payload, caption="Uploaded Image", use_container_width=True)
                st.success("Image Loaded!")

    st.title("🤖 DeskBot Workspace")
    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("📝 Quick Add")
        with st.form("task_form"):
            task_title = st.text_input("Task Name")
            est = st.number_input("Minutes", 15, 120, 60, step=15)
            due = st.date_input("Due")
            if st.form_submit_button("Add Task"):
                add_task(task_title, est, due)
                st.success("Added!")
                st.rerun()

        st.divider()
        tasks = get_tasks()
        if not tasks.empty:
            st.dataframe(tasks[['title', 'due_date']], hide_index=True, use_container_width=True)
            task_context = tasks[['title', 'est_minutes', 'due_date']].to_string(index=False)
            task_del = st.selectbox("Remove:", tasks['id'].astype(str) + " - " + tasks['title'])
            if st.button("Delete"):
                delete_task(task_del.split(" - ")[0])
                st.rerun()
        else:
            task_context = "No tasks currently."
            st.info("No tasks yet.")

    with col2:
        st.subheader("💬 Chat")
        if "messages" not in st.session_state:
            st.session_state.messages = []
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
        if prompt := st.chat_input("Ask about the uploaded file..."):
            with st.chat_message("user"):
                st.markdown(prompt)
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    ai_reply = ask_gemini(prompt, task_context, file_payload, file_type)
                    st.markdown(ai_reply)
            st.session_state.messages.append({"role": "assistant", "content": ai_reply})

# --- 4. FLOW CONTROL ---
# This is the "Traffic Cop" that decides which page to show
if st.session_state.authenticated:
    main_app()
else:
    login_page()

