import streamlit as st
import pandas as pd
import google.generativeai as genai
from PyPDF2 import PdfReader
from PIL import Image
import time
from supabase import create_client, Client

# --- 1. CONFIG & STATE ---
st.set_page_config(page_title="DeskBot: Cloud Platform", page_icon="☁️", layout="wide")

# Initialize Session State
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "username" not in st.session_state:
    st.session_state.username = None

# --- 2. DATABASE CONNECTION (SUPABASE) ---
# Initialize the connection once
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try:
    supabase = init_supabase()
except:
    st.error("⚠️ Supabase Keys missing in Streamlit Secrets!")

# --- 3. AUTHENTICATION ---
def check_login(username, password):
    # Mock Database of Users
    if username == "kais" and password == "deskbot123":
        return True
    elif username == "admin" and password == "admin":
        return True
    else:
        return False

def login_page():
    st.title("☁️ DeskBot Cloud Login")
    st.write("Sign in to access your persistent workspace.")
    
    col1, col2 = st.columns([1, 2])
    with col1:
        with st.form("login_form"):
            user = st.text_input("Username")
            passw = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log In")
            
            if submitted:
                if check_login(user, passw):
                    st.session_state.authenticated = True
                    st.session_state.username = user # Remember WHO is logged in
                    st.success(f"Welcome back, {user}!")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("Invalid credentials.")
    
    with col2:
        st.info("ℹ️ **Demo Accounts:**\n\nUser: `kais` | Pass: `deskbot123`")

def logout():
    st.session_state.authenticated = False
    st.session_state.username = None
    st.rerun()

# --- 4. MAIN APPLICATION ---
def main_app():
    # Helper: Get Current User's Name
    current_user = st.session_state.username

    # Connect to AI
    if "GOOGLE_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        model = genai.GenerativeModel('gemini-2.0-flash')

    # --- DATABASE FUNCTIONS (SUPABASE EDITION) ---
    def get_tasks():
        # Select * FROM tasks WHERE username = current_user
        response = supabase.table("tasks").select("*").eq("username", current_user).execute()
        df = pd.DataFrame(response.data)
        return df

    def add_task(title, est, due):
        # Insert new row with username
        data = {
            "username": current_user,
            "title": title,
            "est_minutes": est,
            "due_date": str(due),
            "status": "todo"
        }
        supabase.table("tasks").insert(data).execute()

    def delete_task(task_id):
        supabase.table("tasks").delete().eq("id", task_id).execute()

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
    with st.sidebar:
        st.header(f"👤 {current_user.upper()}")
        if st.button("Log Out"):
            logout()
        st.divider()
        st.header("📂 Vision Upload")
        uploaded_file = st.file_uploader("File (PDF/Image)", type=["pdf", "png", "jpg"])
        
        file_payload = None
        file_type = None
        if uploaded_file:
            if uploaded_file.type == "application/pdf":
                file_type = "pdf"
                file_payload = extract_text_from_pdf(uploaded_file)
                st.success("PDF Ready")
            else:
                file_type = "image"
                file_payload = Image.open(uploaded_file)
                st.image(file_payload, width=200)
                st.success("Image Ready")

    st.title("☁️ DeskBot Platform")
    
    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("📝 Cloud Tasks")
        with st.form("task_form"):
            t_title = st.text_input("Task")
            t_est = st.number_input("Mins", 15, 120, 60, step=15)
            t_due = st.date_input("Due")
            if st.form_submit_button("Add to Cloud"):
                add_task(t_title, t_est, t_due)
                st.success("Saved to Supabase!")
                st.rerun()

        st.divider()
        df = get_tasks()
        if not df.empty:
            st.dataframe(df[['title', 'due_date', 'status']], hide_index=True)
            task_context = df.to_string()
            
            # Delete Logic
            t_del = st.selectbox("Delete:", df['id'].astype(str) + " - " + df['title'])
            if st.button("Delete Task"):
                delete_task(t_del.split(" - ")[0])
                st.rerun()
        else:
            task_context = "No tasks."
            st.info("No tasks in cloud yet.")

    with col2:
        st.subheader("💬 AI Assistant")
        if "messages" not in st.session_state:
            st.session_state.messages = []
        
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
        
        if prompt := st.chat_input("Ask DeskBot..."):
            with st.chat_message("user"):
                st.markdown(prompt)
            st.session_state.messages.append({"role": "user", "content": prompt})
            
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    reply = ask_gemini(prompt, task_context, file_payload, file_type)
                    st.markdown(reply)
            st.session_state.messages.append({"role": "assistant", "content": reply})

# --- 5. TRAFFIC CONTROL ---
if st.session_state.authenticated:
    main_app()
else:
    login_page()
