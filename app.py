import streamlit as st
import sqlite3
import pandas as pd
import google.generativeai as genai
from PyPDF2 import PdfReader
from PIL import Image

# --- 1. SETUP & CONFIG ---
st.set_page_config(page_title="DeskBot: Vision", page_icon="👁️", layout="wide")

# Connect to Google Gemini
if "GOOGLE_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash')
else:
    st.error("⚠️ Google API Key missing! Please add it to Streamlit Secrets.")

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

# --- 2. HELPER FUNCTIONS ---
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
    except Exception as e:
        return None

# --- 3. THE AI BRAIN (NOW WITH VISION) ---
def ask_gemini(user_message, task_list_text, file_data=None, file_type=None):
    try:
        # Prompt Logic
        system_instruction = f"""
        You are DeskBot.
        
        CONTEXT: User's Tasks:
        {task_list_text}
        
        USER MESSAGE: "{user_message}"
        
        INSTRUCTIONS:
        - If an image is provided, analyze it.
        - If a PDF text is provided, answer based on it.
        - If the user asks to add a task, suggest the details.
        """
        
        # Prepare the "Package" to send to Gemini
        content_package = [system_instruction]
        
        # Add Image or Text to the package if it exists
        if file_type == "image":
            content_package.append(file_data) # Send actual image object
        elif file_type == "pdf":
            content_package.append(f"DOCUMENT CONTENT:\n{file_data}") # Send text
            
        response = model.generate_content(content_package)
        return response.text
    except Exception as e:
        return f"⚠️ AI Error: {e}"

# --- 4. THE UI ---
st.title("👁️ DeskBot: Vision Agent")
st.caption("Chat with Tasks, PDFs, or Images")

# SIDEBAR: Universal File Uploader
with st.sidebar:
    st.header("📂 Upload")
    # We now accept Images AND PDFs
    uploaded_file = st.file_uploader("Drop a File (PDF, PNG, JPG)", type=["pdf", "png", "jpg", "jpeg"])
    
    file_payload = None
    file_type = None

    if uploaded_file is not None:
        # Check if it is an Image or PDF
        if uploaded_file.type == "application/pdf":
            file_type = "pdf"
            with st.spinner("Reading PDF..."):
                file_payload = extract_text_from_pdf(uploaded_file)
            st.success("PDF Loaded!")
            
        else:
            file_type = "image"
            # Load the image for Gemini
            file_payload = Image.open(uploaded_file)
            st.image(file_payload, caption="Uploaded Image", use_container_width=True)
            st.success("Image Loaded!")

col1, col2 = st.columns([1, 2])

# LEFT: Task Management
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

# RIGHT: Chat Interface
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
            with st.spinner("Looking & Thinking..."):
                # Send the prompt + the file (Image or Text)
                ai_reply = ask_gemini(prompt, task_context, file_payload, file_type)
                st.markdown(ai_reply)
        st.session_state.messages.append({"role": "assistant", "content": ai_reply})

