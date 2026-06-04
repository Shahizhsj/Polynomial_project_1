from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from schemas import UserCreate
from passlib.context import CryptContext
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from fastapi import APIRouter, UploadFile, File, Depends
from sqlalchemy.orm import Session
import os
import uuid
import cohere
from sqlalchemy import desc
from models import Document
from database import session_local, engine
import models as models
from fastapi import FastAPI, Depends, HTTPException
from langchain_community.document_loaders.pdf import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores.faiss import FAISS
from qdrant_client import QdrantClient
from qdrant_client.http.models import VectorParams, Distance
from qdrant_client.models import PointStruct
from google import genai
from qdrant_client.models import Filter,FieldCondition,MatchValue





GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
google_client = genai.Client(
    api_key=GOOGLE_API_KEY
)
co = cohere.ClientV2(api_key="FwtnTPzms03OR88GnDdF3FOZvaKZLN20aVXlKbPy")


app = FastAPI()
models.Base.metadata.create_all(engine)
SECRET_KEY = "super-secret-key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(
    schemes=["pbkdf2_sha256"],
    deprecated="auto"
)

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="token"
)
def get_password_hash(password):
    return pwd_context.hash(password)

def verify_password(password, hashed):
    return pwd_context.verify(password, hashed)

def create_access_token(data: dict):

    to_encode = data.copy()

    expire = datetime.utcnow() + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )

    to_encode.update(
        {"exp": expire}
    )

    return jwt.encode(
        to_encode,
        SECRET_KEY,
        algorithm=ALGORITHM
    )



client = QdrantClient(path="./qdrant_data")

if not client.collection_exists("documents"):
    client.create_collection(
        collection_name="documents",
        vectors_config=VectorParams(
            size=1024,
            distance=Distance.COSINE
        )
    )


def ingest(pdf_path):
    loader=PyPDFLoader(pdf_path)
    documents=loader.load()
    text_splitter=RecursiveCharacterTextSplitter(chunk_size=1000,chunk_overlap=200)
    chunks=text_splitter.split_documents(documents)
    return chunks




def create_vector_store(chunks):
    model = "embed-v4.0"    
    input_type = "search_document"
    # Extract text content from LangChain document objects
    texts = [chunk.page_content for chunk in chunks]
    res = co.embed(
    texts=texts,
    model=model,
    input_type=input_type,
    output_dimension=1024,
    embedding_types=["float"],
    )
    return res.embeddings.float
    

def create_vector_db(chunks, embeddings,document_id):
    points=[]
    for chunk,embedding in zip(chunks,embeddings):
        point=PointStruct(
            id=str(uuid.uuid4()),
            vector=embedding,
            payload={
                "document_id":document_id,
                "text":chunk.page_content
            }
        )
        points.append(point)
    client.upsert(
        collection_name="documents",
        points=points
    )
    
def save_chat_history(user_id:str,role:str,message:str,db:Session):
    chat_history=models.ChatHistory(
        user_id=user_id,
        role=role,
        message=message
    )
    db.add(chat_history)
    db.commit()
    db.refresh(chat_history)

def get_last_10_messages(db,user_id: str):
    messages = (
        db.query(models.ChatHistory)
        .filter(models.ChatHistory.user_id == user_id)
        .order_by(desc(models.ChatHistory.created_at))
        .limit(10)
        .all()
    )

    return list(reversed(messages))

def get_db():
    db = session_local()
    try:
        yield db
    finally:
        db.close()


UPLOAD_DIR = "uploads"

os.makedirs(UPLOAD_DIR, exist_ok=True)

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):

    try:

        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )

        username = payload.get("sub")

    except JWTError:

        raise HTTPException(
            status_code=401
        )

    user = (
        db.query(models.User)
        .filter(
            models.User.username ==
            username
        )
        .first()
    )

    return user

@app.post("/register")
def register(
    user: UserCreate,
    db: Session = Depends(get_db)
):

    db_user = models.User(
        username=user.username,
        hashed_password=get_password_hash(
            user.password
        )
    )

    db.add(db_user)
    db.commit()

    return {
        "message": "User created"
    }


@app.post("/token")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):

    user = (
        db.query(models.User)
        .filter(
            models.User.username ==
            form_data.username
        )
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=401
        )

    if not verify_password(
        form_data.password,
        user.hashed_password
    ):
        raise HTTPException(
            status_code=401
        )

    token = create_access_token(
        {
            "sub": user.username
        }
    )

    return {
        "access_token": token,
        "token_type": "bearer"
    }


@app.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):

    file_path = os.path.join(UPLOAD_DIR, file.filename)

    with open(file_path, "wb") as buffer:
        buffer.write(await file.read())

    document = Document(
        filename=file.filename,
        file_path=file_path
    )

    db.add(document)
    db.commit()
    db.refresh(document)
    chunks=ingest(file_path)
    embeddings=create_vector_store(chunks)
    create_vector_db(chunks, embeddings, document.id)


    return {
        "message": "File uploaded successfully",
        "document_id": document.id,
        "chunks":chunks,
        "embeddings":embeddings
    }




@app.post('/query/{query}')
async def query_documents(query:str,db:Session=Depends(get_db), current_user = Depends(get_current_user)):
    query_embedding=co.embed(
        texts=[query],
        model="embed-v4.0",
        input_type="search_query",
        output_dimension=1024,
        embedding_types=["float"],
    ).embeddings.float[0]
    history=get_last_10_messages(db,current_user.username)
    results = client.query_points(
    collection_name="documents",
    query=query_embedding,
    limit=10
    )

    documents = [
    point.payload["text"]
    for point in results.points]

    rerank_results = co.rerank(
    model="rerank-v3.5",
    query=query,
    documents=documents,
    top_n=5
)
    top_chunks = []
    for result in rerank_results.results:
        top_chunks.append(documents[result.index])
    context = "\n\n".join(top_chunks)
  
    prompt = f"""
You are a helpful AI assistant.

Conversation History:
{history}

Retrieved Context:
{context}

User Question:
{query}

Instructions:
- Answer the question using the retrieved context whenever possible.
- Use the conversation history when it is relevant.
- If the answer is not present in the context, clearly say that the information is not available in the provided documents.
- Do not make up facts.
- Provide a clear and well-structured response.

Answer:
"""

    response = google_client.models.generate_content(
    model="gemini-2.5-flash",
    contents=prompt,
)
    #save the convo in db
    save_chat_history(user_id=current_user.username, role="user", message=query, db=db)
    save_chat_history(user_id=current_user.username, role="assistant", message=response.text, db=db)

    return {
        "result":response.text
    }
    
    

@app.get("/documents")
async def list_documents(db: Session=Depends(get_db), current_user = Depends(get_current_user)):
    documents=db.query(Document).all()
    return {
        'message':"Documents retrieved successfully",
        'documents':documents
    }



@app.get('/delete/{id}')
async def delete_document(id:int,db:Session=Depends(get_db), current_user = Depends(get_current_user)):
    client.delete(
    collection_name="documents",
    points_selector=Filter(must=[FieldCondition(key="document_id",match=MatchValue(value=id))]))


    document=db.query(Document).filter(Document.id==id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    try:
        os.remove(document.file_path)
    except Exception as e:
        print(f"Error deleting file: {e}")
    
    db.delete(document)
    db.commit()
    
    return {
        "message": "Document deleted successfully"
    }