import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, time, timedelta
import io
from ics import Calendar

# --- 1. SETUP & DATABASE ---
st.set_page_config(page_title="DeskBot MVP", page_icon="🤖", layout="wide")

# This connects to a file named 'deskbot.db'. If it doesn't exist, it creates it.
def init_db():
    conn = sqlite3.connect("deskbot.db")
    c = conn.cursor()
    # Create table for Tasks
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    est_minutes INTEGER,
                    due_date TEXT,
                    status TEXT DEFAULT 'todo'
                )''')
    # Create table for Calendar Events
    c.execute('''CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    start_dt TEXT,
                    end_dt TEXT
                )''')
    conn.commit()
    conn.close()

# Run the setup immediately
init_db()

# --- 2. HELPER FUNCTIONS ---
def get_db_connection():
    return sqlite3.connect("deskbot.db", check_same_thread=False)

def add_task(title, est, due):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO tasks (title, est_minutes, due_date) VALUES (?, ?, ?)", 
              (title, est, str(due)))
    conn.commit()
    conn.close()

def get_tasks():
    conn = get_db_connection()
    # We use pandas to easily read the SQL data into a table format
    try:
        df = pd.read_sql("SELECT * FROM tasks", conn)
    except:
        df = pd.DataFrame()
    conn.close()
    return df

def delete_task(task_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()

# --- 3. THE APP INTERFACE ---
st.title("🤖 DeskBot: Smart Assistant")
st.write("I now have a **Database**, so I will remember what you tell me!")

col1, col2 = st.columns([1, 2])

# LEFT COLUMN: Add New Tasks
with col1:
    st.subheader("📝 New Task")
    with st.form("task_form"):
        task_title = st.text_input("Task Name")
        est_min = st.number_input("Minutes needed", min_value=15, value=60, step=15)
        due_date = st.date_input("Due Date")
        submitted = st.form_submit_button("Add to Memory")
        
        if submitted and task_title:
            add_task(task_title, est_min, due_date)
            st.success(f"Saved: {task_title}")
            st.rerun() # Refresh the page to show new data immediately

# RIGHT COLUMN: See Your List
with col2:
    st.subheader("📋 Your To-Do List")
    df = get_tasks()
    
    if not df.empty:
        # Show tasks as a nice table
        st.dataframe(
            df[['title', 'est_minutes', 'due_date', 'status']], 
            use_container_width=True,
            hide_index=True
        )
        
        # Simple Delete Feature
        task_list = df['id'].astype(str) + " - " + df['title']
        task_to_delete = st.selectbox("Select task to remove:", task_list)
        
        if st.button("🗑️ Delete Task"):
            # Extract the ID from the string "1 - Buy Milk" -> "1"
            task_id = task_to_delete.split(" - ")[0]
            delete_task(task_id)
            st.rerun()
    else:
        st.info("No tasks yet. Add one on the left!")

# --- 4. DEBUG INFO ---
st.divider()
st.caption("System Info: Database active at 'deskbot.db'")