from fastapi import FastAPI

app = FastAPI(title="Generated API", version=f"{version}")

from .users import router as users_router
app.include_router(users_router, prefix='/api/v1/users')

from .chat import router as chat_router
app.include_router(chat_router, prefix='/api/v1/chat')

from .test import router as test_router
app.include_router(test_router, prefix='/api/v1/test')


# To run: uvicorn main:app --reload (if this file is main.py in the version folder)