import requests
import streamlit as st

API_URL = "http://127.0.0.1:8000"

st.set_page_config(page_title="MedGemma local", page_icon="🩺")
st.title("🩺 MedGemma local")
st.caption("Informational assistant running on your computer. It does not replace a healthcare professional.")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "sent_image_id" not in st.session_state:
    st.session_state.sent_image_id = None

# --- Barra lateral ---
with st.sidebar:
    st.header("Options")
    image = st.file_uploader("Attach image (optional)", type=["png", "jpg", "jpeg", "webp"])
    if image:
        st.image(image, use_container_width=True)
        st.caption(
            "It is sent with your next question and the model keeps it in mind for the "
            "next few turns. To use another image, remove it with the X and upload a new one."
        )

    if st.button("New conversation", use_container_width=True):
        if st.session_state.session_id:
            try:
                requests.delete(f"{API_URL}/sessions/{st.session_state.session_id}", timeout=5)
            except requests.RequestException:
                pass
        st.session_state.messages = []
        st.session_state.session_id = None
        st.session_state.sent_image_id = None
        st.rerun()

# --- Historial ---
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])


def stream_text(resp):
    resp.encoding = "utf-8"
    for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
        if chunk:
            yield chunk


# --- Entrada ---
if prompt := st.chat_input("Type your question..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            # The image is only sent if it is new (not re-sent every turn)
            new_image = image is not None and image.file_id != st.session_state.sent_image_id

            if new_image:
                r = requests.post(
                    f"{API_URL}/chat/image",
                    data={"message": prompt, "session_id": st.session_state.session_id or ""},
                    files={"image": (image.name, image.getvalue(), image.type)},
                    stream=True,
                    timeout=600,
                )
            else:
                r = requests.post(
                    f"{API_URL}/chat/stream",
                    json={"message": prompt, "session_id": st.session_state.session_id},
                    stream=True,
                    timeout=600,
                )

            if r.status_code >= 400:
                raise RuntimeError(f"{r.status_code}: {r.text}")

            st.session_state.session_id = r.headers.get("X-Session-Id")
            answer = st.write_stream(stream_text(r))
            st.session_state.messages.append({"role": "assistant", "content": answer})

            if new_image:
                st.session_state.sent_image_id = image.file_id

        except Exception as e:
            st.session_state.messages.pop()  # remove the failed question
            st.error(f"Error: {e}")