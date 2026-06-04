import streamlit as st
import requests
from requests.exceptions import ConnectionError, Timeout
from typing import Optional
import os

# ==========================================================
# CONFIG
# ==========================================================


API_BASE_URL = "http://rag_backend:8000"
st.set_page_config(
    page_title="RAG Assistant",
    page_icon="🤖",
    layout="wide"
)

# ==========================================================
# SESSION STATE INITIALIZATION
# ==========================================================

if "token" not in st.session_state:
    st.session_state.token = None

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "messages" not in st.session_state:
    st.session_state.messages = []

# ==========================================================
# API HELPERS
# ==========================================================

def get_headers():
    return {
        "Authorization": f"Bearer {st.session_state.token}"
    }


def handle_api_error(response):
    if response.status_code == 401:
        st.error("Session expired. Please login again.")
        logout()
        return True

    if response.status_code >= 400:
        try:
            st.error(response.json())
        except Exception:
            st.error("Something went wrong.")
        return True

    return False


# ==========================================================
# AUTH
# ==========================================================

def login(username: str, password: str) -> bool:
    try:

        response = requests.post(
            f"{API_BASE_URL}/token",
            data={
                "username": username,
                "password": password
            },
            timeout=30
        )

        if response.status_code == 200:

            data = response.json()

            st.session_state.token = data["access_token"]
            st.session_state.logged_in = True

            return True

        st.error("Invalid username or password")
        return False

    except ConnectionError:
        st.error("Backend server unavailable.")
        return False

    except Timeout:
        st.error("Request timeout.")
        return False

    except Exception as e:
        st.error(str(e))
        return False


def logout():
    st.session_state.token = None
    st.session_state.logged_in = False
    st.session_state.messages = []
    st.rerun()


# ==========================================================
# DOCUMENT API
# ==========================================================
def render_documents_page():

    st.title("📄 Documents")

    try:

        response = requests.get(
            f"{API_BASE_URL}/documents",
            headers=get_headers()
        )

        if response.status_code != 200:
            st.error("Failed to fetch documents")
            return

        data = response.json()

        docs = data.get(
            "documents",
            []
        )

        if not docs:
            st.info("No documents found")
            return

        rows = []

        for doc in docs:

            rows.append({
                "Document ID": doc["id"],
                "Filename": doc["filename"]
            })

        st.dataframe(
            rows,
            use_container_width=True
        )

    except Exception as e:
        st.error(str(e))

def render_delete_page():

    st.title("🗑 Delete Document")

    document_id = st.number_input(
        "Enter Document ID",
        min_value=1,
        step=1
    )

    if st.button(
        "Delete Document",
        use_container_width=True
    ):

        try:

            response = requests.get(
                f"{API_BASE_URL}/delete/{document_id}",
                headers=get_headers()
            )

            if response.status_code == 200:

                st.success(
                    "Document deleted successfully"
                )

            else:

                st.error(
                    "Unable to delete document"
                )

        except Exception as e:
            st.error(str(e))


def upload_document(file):

    try:

        files = {
            "file": (
                file.name,
                file,
                "application/pdf"
            )
        }

        response = requests.post(
            f"{API_BASE_URL}/documents/upload",
            files=files,
            headers=get_headers(),
            timeout=300
        )

        if handle_api_error(response):
            return False

        return True

    except ConnectionError:
        st.error("Backend unavailable.")
        return False

    except Exception as e:
        st.error(str(e))
        return False


def get_documents():

    try:

        response = requests.get(
            f"{API_BASE_URL}/documents",
            headers=get_headers(),
            timeout=60
        )

        if handle_api_error(response):
            return []

        return response.json()

    except Exception:
        return []


def delete_document(doc_id):

    try:

        response = requests.get(
            f"{API_BASE_URL}/delete/{doc_id}",
            headers=get_headers(),
            timeout=60
        )

        if handle_api_error(response):
            return False

        return True

    except Exception:
        return False


# ==========================================================
# QUERY API
# ==========================================================

def ask_question(question: str) -> Optional[str]:

    try:

        response = requests.post(
            f"{API_BASE_URL}/query/{question}",
            headers=get_headers(),
            timeout=300
        )

        if handle_api_error(response):
            return None

        data = response.json()

        return data.get("result", "No response received.")

    except ConnectionError:
        st.error("Backend unavailable.")
        return None

    except Timeout:
        st.error("Request timeout.")
        return None

    except Exception as e:
        st.error(str(e))
        return None


# ==========================================================
# SIDEBAR
# ==========================================================

def render_sidebar():

    with st.sidebar:

        st.title("📚 RAG Assistant")

        if st.session_state.logged_in:

            st.success("Logged In")

            if st.button("Logout"):
                logout()

            st.divider()

            st.subheader("Upload PDF")

            uploaded_file = st.file_uploader(
                "Choose PDF",
                type=["pdf"]
            )

            if uploaded_file:

                if st.button("Upload Document"):

                    with st.spinner("Uploading..."):

                        success = upload_document(uploaded_file)

                        if success:
                            st.success(
                                "Document uploaded successfully"
                            )
                            st.rerun()

        else:
            st.warning("Please login")

# ==========================================================
# LOGIN PAGE
# ==========================================================

def render_login():

    st.title("🔐 Login")

    st.markdown("Login to access your RAG Assistant")

    username = st.text_input("Username")

    password = st.text_input(
        "Password",
        type="password"
    )

    if st.button("Login", use_container_width=True):

        if not username or not password:
            st.warning("Please enter username and password")
            return

        with st.spinner("Authenticating..."):

            success = login(
                username,
                password
            )

            if success:
                st.success("Login successful")
                st.rerun()



# ==========================================================
# CHAT PAGE
# ==========================================================

def render_chat():

    st.title("🤖 RAG Chat Assistant")

    st.caption(
        "Ask questions from your uploaded PDF documents"
    )

    for message in st.session_state.messages:

        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input(
        "Ask a question..."
    )

    if prompt:

        st.session_state.messages.append(
            {
                "role": "user",
                "content": prompt
            }
        )

        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):

            with st.spinner("Thinking..."):

                answer = ask_question(prompt)

                if answer:

                    st.markdown(answer)

                    st.session_state.messages.append(
                        {
                            "role": "assistant",
                            "content": answer
                        }
                    )


# ==========================================================
# MAIN APP
# ==========================================================

def main():

    render_sidebar()

    if not st.session_state.logged_in:
        render_login()
        return 
        

    page = st.sidebar.radio(
    "Navigation",
    [
        "Chat",
        "Documents",
        "Delete Document"
    ]
)

    if page == "Chat":
        render_chat()

    elif page == "Documents":
        render_documents_page()

    elif page == "Delete Document":
        render_delete_page()


if __name__ == "__main__":
    main()